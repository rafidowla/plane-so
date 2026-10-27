# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 slice 2 -- workspace creation gate, delete re-confirmation, keep window, restore.

Also covers the slice-2 follow-up that makes the Time and Renewals writes (holidays, leave, member
tracking status, capacity) Super Admin only.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.deletion_task import hard_delete, soft_delete_related_objects
from plane.db.models import (
    Issue,
    Profile,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    Workspace,
    WorkspaceMember,
)
from plane.settings.redis import redis_instance
from plane.tests.factories import UserFactory
from plane.utils import workspace_lifecycle

PASSWORD = "test-password"  # conftest.user_data


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def plain_admin(db, workspace):
    return WorkspaceMember.objects.create(workspace=workspace, member=UserFactory(), role=20, is_active=True)


@pytest.fixture
def plain_admin_client(plain_admin):
    return _client(plain_admin.member)


def _delete(client, workspace, **body):
    return client.delete(f"/api/workspaces/{workspace.slug}/", body, format="json")


def _soft_delete_via_api(client, workspace):
    response = _delete(client, workspace, confirm_name=workspace.name, password=PASSWORD)
    assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
    # The cascade runs on a Celery worker in production; run it inline here.
    soft_delete_related_objects("db", "workspace", workspace.pk)
    return Workspace.all_objects.get(pk=workspace.pk)


# --- creation gate --------------------------------------------------------------------------


def _create(client, slug):
    return client.post("/api/workspaces/", {"name": f"WS {slug}", "slug": slug}, format="json")


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_user_without_super_admin_cannot_create_workspace(_seed, db):
    response = _create(_client(UserFactory()), f"ws-{uuid.uuid4().hex[:8]}")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["error"] == workspace_lifecycle.CREATION_FORBIDDEN_ERROR


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_plain_admin_cannot_create_workspace(_seed, plain_admin_client):
    response = _create(plain_admin_client, f"ws-{uuid.uuid4().hex[:8]}")
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_super_admin_can_create_workspace(_seed, session_client, workspace):
    slug = f"ws-{uuid.uuid4().hex[:8]}"
    response = _create(session_client, slug)
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert WorkspaceMember.objects.get(workspace__slug=slug, member=workspace.owner).is_super_admin


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_instance_admin_can_create_workspace(_seed, session_client, instance_admin):
    # No workspace at all: the god-mode admin role alone allows creating one.
    assert not WorkspaceMember.objects.filter(member=instance_admin).exists()
    response = _create(session_client, f"ws-{uuid.uuid4().hex[:8]}")
    assert response.status_code == status.HTTP_201_CREATED, response.data


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_super_admin_of_deleted_workspace_only_cannot_create(_seed, session_client, workspace):
    _soft_delete_via_api(session_client, workspace)
    response = _create(session_client, f"ws-{uuid.uuid4().hex[:8]}")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def _can_create(user):
    Profile.objects.get_or_create(user=user)
    response = _client(user).get("/api/users/me/settings/")
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data["can_create_workspace"]


@pytest.mark.contract
@pytest.mark.django_db
def test_user_settings_report_can_create_workspace(workspace, plain_admin):
    # Slice 4: the web app hides "Create workspace" from this flag, so it must match the gate.
    assert _can_create(workspace.owner) is True
    assert _can_create(plain_admin.member) is False


@pytest.mark.contract
@pytest.mark.django_db
def test_user_settings_report_can_create_for_instance_admin(instance_admin):
    assert _can_create(instance_admin) is True


@pytest.mark.contract
@pytest.mark.django_db
def test_user_settings_report_cannot_create_after_only_workspace_deleted(session_client, workspace):
    _soft_delete_via_api(session_client, workspace)
    assert _can_create(workspace.owner) is False


# --- delete re-confirmation -----------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("confirm_name", [None, "", "test workspace", "Other"])
def test_delete_requires_exact_name(session_client, workspace, confirm_name):
    body = {"password": PASSWORD}
    if confirm_name is not None:
        body["confirm_name"] = confirm_name
    response = _delete(session_client, workspace, **body)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error_code"] == "WORKSPACE_NAME_MISMATCH"
    assert Workspace.objects.filter(pk=workspace.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("password", [None, "", "wrong-password"])
def test_delete_requires_correct_password(session_client, workspace, password):
    body = {"confirm_name": workspace.name}
    if password is not None:
        body["password"] = password
    response = _delete(session_client, workspace, **body)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error_code"] == "PASSWORD_INCORRECT"
    assert Workspace.objects.filter(pk=workspace.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_with_name_and_password_soft_deletes(session_client, workspace):
    deleted = _soft_delete_via_api(session_client, workspace)
    assert deleted.deleted_at is not None
    assert deleted.slug.startswith("test-workspace__")
    assert not Workspace.objects.filter(pk=workspace.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_locks_after_too_many_failed_checks(session_client, workspace):
    for _ in range(workspace_lifecycle.DELETE_MAX_FAILED_CHECKS):
        assert _delete(session_client, workspace, confirm_name=workspace.name, password="nope").status_code == 400
    response = _delete(session_client, workspace, confirm_name=workspace.name, password=PASSWORD)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert Workspace.objects.filter(pk=workspace.pk).exists()


@pytest.fixture
def code_user(create_user):
    """The Super Admin, as an account that signs in with an email code (no password of their own)."""
    create_user.is_password_autoset = True
    create_user.set_unusable_password()
    create_user.save()
    return create_user


def _request_code(client, workspace):
    with patch("plane.app.views.workspace.lifecycle.send_workspace_delete_code") as task:
        response = client.post(f"/api/workspaces/{workspace.slug}/delete-code/", format="json")
    code = task.delay.call_args.args[2] if task.delay.called else None
    return response, code


@pytest.mark.contract
@pytest.mark.django_db
def test_code_account_deletes_with_emailed_code(session_client, workspace, code_user):
    response, code = _request_code(session_client, workspace)
    assert response.status_code == status.HTTP_200_OK, response.data
    assert code and len(code) == 6

    wrong = _delete(
        session_client, workspace, confirm_name=workspace.name, code="000000" if code != "000000" else "111111"
    )
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    assert wrong.data["error_code"] == "CODE_INVALID"

    ok = _delete(session_client, workspace, confirm_name=workspace.name, code=code)
    assert ok.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.contract
@pytest.mark.django_db
def test_code_account_cannot_delete_with_password_field(session_client, workspace, code_user):
    response = _delete(session_client, workspace, confirm_name=workspace.name, password=PASSWORD)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error_code"] == "CODE_INVALID"


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_code_works_once(workspace, code_user):
    code = workspace_lifecycle.issue_delete_code(workspace.id, code_user.id)
    assert workspace_lifecycle.verify_delete_identity(code_user, workspace.id, code=code)
    assert not workspace_lifecycle.verify_delete_identity(code_user, workspace.id, code=code)


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_code_issue_is_rate_limited(session_client, workspace, code_user):
    for _ in range(workspace_lifecycle.DELETE_CODE_MAX_ISSUED):
        assert _request_code(session_client, workspace)[0].status_code == status.HTTP_200_OK
    response, code = _request_code(session_client, workspace)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert code is None


@pytest.mark.contract
@pytest.mark.django_db
def test_password_account_is_told_to_use_password(session_client, workspace):
    response, code = _request_code(session_client, workspace)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["error_code"] == "USE_PASSWORD"
    assert code is None


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_request_delete_code(workspace, plain_admin_client):
    response, code = _request_code(plain_admin_client, workspace)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert code is None


# --- deleted list, restore, keep window ------------------------------------------------------


@pytest.fixture
def populated(workspace, create_user):
    """A project with one live issue and one issue that was deleted a day before the workspace."""
    project = Project.objects.create(workspace=workspace, name="Proj", identifier="PRJ", created_by=create_user)
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="PRJ")
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20, is_active=True)
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    live = Issue(workspace=workspace, project=project, name="Live", state=state)
    live.save(created_by_id=create_user.id)
    old = Issue(workspace=workspace, project=project, name="Old", state=state)
    old.save(created_by_id=create_user.id)
    earlier = timezone.now() - timedelta(days=1)
    Issue.all_objects.filter(pk=old.pk).update(deleted_at=earlier)
    return {"project": project, "live": live, "old": old, "earlier": earlier}


@pytest.mark.contract
@pytest.mark.django_db
def test_deleted_workspace_is_listed_for_its_super_admin(session_client, workspace):
    deleted = _soft_delete_via_api(session_client, workspace)
    response = session_client.get("/api/users/me/deleted-workspaces/")
    assert response.status_code == status.HTTP_200_OK
    [row] = response.data
    assert row["id"] == str(workspace.id)
    assert row["slug"] == "test-workspace"
    assert row["is_slug_available"] is True
    assert row["purge_at"] == deleted.deleted_at + timedelta(days=30)


@pytest.mark.contract
@pytest.mark.django_db
def test_deleted_workspace_not_listed_or_restorable_for_plain_admin(session_client, workspace, plain_admin_client):
    _soft_delete_via_api(session_client, workspace)
    assert plain_admin_client.get("/api/users/me/deleted-workspaces/").data == []
    response = plain_admin_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/", format="json")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
@pytest.mark.django_db
def test_restore_brings_back_only_what_the_delete_took(session_client, workspace, populated):
    _soft_delete_via_api(session_client, workspace)
    assert Project.all_objects.get(pk=populated["project"].pk).deleted_at is not None
    assert Issue.all_objects.get(pk=populated["live"].pk).deleted_at is not None

    response = session_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/", format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["slug"] == "test-workspace"

    restored = Workspace.objects.get(pk=workspace.pk)
    assert restored.slug == "test-workspace"
    assert Project.objects.filter(pk=populated["project"].pk).exists()
    assert Issue.objects.filter(pk=populated["live"].pk).exists()
    assert WorkspaceMember.objects.get(workspace=workspace, member=workspace.owner).is_super_admin
    # The issue deleted before the workspace keeps its own deletion.
    assert Issue.all_objects.get(pk=populated["old"].pk).deleted_at == populated["earlier"]
    # And the workspace shows up for the user again.
    slugs = [w["slug"] for w in session_client.get("/api/users/me/workspaces/").data]
    assert "test-workspace" in slugs


@pytest.mark.contract
@pytest.mark.django_db
def test_restore_waits_for_running_delete(session_client, workspace):
    response = _delete(session_client, workspace, confirm_name=workspace.name, password=PASSWORD)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    # Cascade not run yet: the "deletion running" marker is still set.
    response = session_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/", format="json")
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["error_code"] == "DELETE_IN_PROGRESS"
    redis_instance().delete(workspace_lifecycle.deletion_running_key(workspace.id))


@pytest.mark.contract
@pytest.mark.django_db
def test_queued_cascade_skips_a_restored_workspace(session_client, workspace, populated):
    _delete(session_client, workspace, confirm_name=workspace.name, password=PASSWORD)
    redis_instance().delete(workspace_lifecycle.deletion_running_key(workspace.id))  # e.g. worker down past TTL
    assert session_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/").status_code == 200
    soft_delete_related_objects("db", "workspace", workspace.pk)  # the late cascade arrives
    assert Project.objects.filter(pk=populated["project"].pk).exists()
    assert Issue.objects.filter(pk=populated["live"].pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_restore_asks_for_new_slug_when_taken(session_client, workspace, create_user):
    _soft_delete_via_api(session_client, workspace)
    Workspace.objects.create(name="Squatter", slug="test-workspace", owner=UserFactory())

    row = session_client.get("/api/users/me/deleted-workspaces/").data[0]
    assert row["is_slug_available"] is False

    url = f"/api/users/me/deleted-workspaces/{workspace.id}/restore/"
    taken = session_client.post(url, format="json")
    assert taken.status_code == status.HTTP_409_CONFLICT
    assert taken.data["error_code"] == "SLUG_TAKEN"

    bad = session_client.post(url, {"slug": "has spaces"}, format="json")
    assert bad.status_code == status.HTTP_400_BAD_REQUEST

    ok = session_client.post(url, {"slug": "test-workspace-2"}, format="json")
    assert ok.status_code == status.HTTP_200_OK, ok.data
    assert Workspace.objects.get(pk=workspace.pk).slug == "test-workspace-2"


@pytest.mark.contract
@pytest.mark.django_db
def test_restore_closed_after_keep_window(session_client, workspace):
    _soft_delete_via_api(session_client, workspace)
    Workspace.all_objects.filter(pk=workspace.pk).update(deleted_at=timezone.now() - timedelta(days=31))
    assert session_client.get("/api/users/me/deleted-workspaces/").data == []
    response = session_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/", format="json")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
@pytest.mark.django_db
def test_hard_delete_purges_workspaces_after_30_days(create_user):
    old = Workspace.objects.create(name="Old", slug=f"old-{uuid.uuid4().hex[:6]}", owner=create_user)
    recent = Workspace.objects.create(name="Recent", slug=f"recent-{uuid.uuid4().hex[:6]}", owner=create_user)
    Workspace.all_objects.filter(pk=old.pk).update(deleted_at=timezone.now() - timedelta(days=31))
    Workspace.all_objects.filter(pk=recent.pk).update(deleted_at=timezone.now() - timedelta(days=29))
    hard_delete()
    assert not Workspace.all_objects.filter(pk=old.pk).exists()
    assert Workspace.all_objects.filter(pk=recent.pk).exists()


# --- Time and Renewals writes are Super Admin only -------------------------------------------


def _tt_writes(slug):
    some = uuid.uuid4()
    base = f"/api/workspaces/{slug}/time"
    return [
        ("post", f"{base}/holiday-calendars/"),
        ("patch", f"{base}/holiday-calendars/{some}/"),
        ("delete", f"{base}/holiday-calendars/{some}/"),
        ("post", f"{base}/holiday-calendars/{some}/holidays/"),
        ("patch", f"{base}/holiday-calendars/{some}/holidays/{some}/"),
        ("delete", f"{base}/holiday-calendars/{some}/holidays/{some}/"),
        ("put", f"{base}/members/{some}/holiday-calendar/"),
        ("post", f"{base}/leave/"),
        ("patch", f"{base}/leave/{some}/"),
        ("delete", f"{base}/leave/{some}/"),
        ("post", f"{base}/members/{some}/tracking/"),
        ("put", f"{base}/members/{some}/capacity/"),
        ("post", f"/api/workspaces/{slug}/resource-capacities/"),
    ]


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("index", range(13))
def test_plain_admin_cannot_write_time_and_renewals(workspace, plain_admin_client, index):
    method, url = _tt_writes(workspace.slug)[index]
    response = getattr(plain_admin_client, method)(url, {}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN, (method, url, response.status_code)


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("index", range(13))
def test_super_admin_passes_time_and_renewals_gate(session_client, workspace, index):
    method, url = _tt_writes(workspace.slug)[index]
    response = getattr(session_client, method)(url, {}, format="json")
    # Past the gate the view runs: bad input / unknown ids, but never the Super Admin 403.
    assert response.status_code != status.HTTP_403_FORBIDDEN, (method, url)


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_code_email_renders_and_sends(settings):
    from django.core import mail

    from plane.bgtasks.workspace_delete_code_task import send_workspace_delete_code

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    config = ("localhost", "", "", "25", "0", "0", "noreply@example.com")
    with patch("plane.bgtasks.workspace_delete_code_task.get_email_configuration", return_value=config):
        send_workspace_delete_code("owner@example.com", "Acme <Ops>", "482913")
    [message] = mail.outbox
    assert message.to == ["owner@example.com"]
    assert "Acme <Ops>" in message.subject
    html = message.alternatives[0][0]
    assert "482913" in html
    assert "Acme &lt;Ops&gt;" in html  # workspace name is escaped in the HTML body
