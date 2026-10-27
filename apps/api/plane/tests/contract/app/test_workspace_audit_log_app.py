# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 slice 3 -- workspace audit log: what gets recorded, who can read it, how long it lives."""

import importlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.app.views.asset.v2 import WorkspaceFileAssetEndpoint
from plane.bgtasks.deletion_task import hard_delete, soft_delete_related_objects
from plane.db.models import (
    APIToken,
    FileAsset,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    User,
    Webhook,
    Workspace,
    WorkspaceAuditLog,
    WorkspaceMember,
    WorkspaceMemberInvite,
)
from plane.tests.factories import UserFactory
from plane.utils import workspace_audit
from plane.utils.time_tracking import audit as tt_audit

PASSWORD = "test-password"  # conftest.user_data


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _entries(workspace, action=None):
    qs = WorkspaceAuditLog.objects.filter(workspace_id=workspace.id).order_by("created_at")
    return list(qs.filter(action=action) if action else qs)


def _one(workspace, action):
    rows = _entries(workspace, action)
    assert len(rows) == 1, [r.action for r in _entries(workspace)]
    return rows[0]


@pytest.fixture
def plain_admin(db, workspace):
    return WorkspaceMember.objects.create(workspace=workspace, member=UserFactory(), role=20, is_active=True)


@pytest.fixture
def member(db, workspace):
    return WorkspaceMember.objects.create(workspace=workspace, member=UserFactory(), role=15, is_active=True)


@pytest.fixture
def audit_guard(db):
    """Install the 0137 trigger + FKs (pytest runs with --nomigrations)."""
    module = importlib.import_module("plane.db.migrations.0137_workspace_audit_log")
    with connection.cursor() as cursor:
        cursor.execute(module.WORKSPACE_AUDIT_GUARD_SQL)


# --- members, ownership ---------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_promotion_to_super_admin_is_recorded(session_client, workspace, member, create_user):
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/members/{member.pk}/", {"is_super_admin": True}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "member.role_changed")
    assert entry.actor_id == create_user.id
    assert entry.actor_email == create_user.email
    assert entry.target_type == "member"
    assert entry.target_id == member.member_id
    assert entry.before == {"role": 15, "role_name": "Member", "is_super_admin": False}
    assert entry.after == {"role": 20, "role_name": "Admin", "is_super_admin": True}


@pytest.mark.contract
@pytest.mark.django_db
def test_unchanged_role_records_nothing(session_client, workspace, member):
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/members/{member.pk}/", {"role": 15}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert _entries(workspace) == []


@pytest.mark.contract
@pytest.mark.django_db
def test_refused_role_change_records_nothing(workspace, member, plain_admin):
    response = _client(plain_admin.member).patch(
        f"/api/workspaces/{workspace.slug}/members/{member.pk}/", {"role": 20}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _entries(workspace) == []


@pytest.mark.contract
@pytest.mark.django_db
def test_member_removal_is_recorded(session_client, workspace, plain_admin):
    response = session_client.delete(f"/api/workspaces/{workspace.slug}/members/{plain_admin.pk}/")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    entry = _one(workspace, "member.removed")
    assert entry.target_id == plain_admin.member_id
    assert entry.before["role"] == 20
    assert entry.after is None


@pytest.mark.contract
@pytest.mark.django_db
def test_admin_leaving_is_recorded_member_leaving_is_not(workspace, plain_admin, member):
    assert _client(member.member).post(f"/api/workspaces/{workspace.slug}/members/leave/").status_code == 204
    assert _entries(workspace) == []
    assert _client(plain_admin.member).post(f"/api/workspaces/{workspace.slug}/members/leave/").status_code == 204
    entry = _one(workspace, "member.left")
    assert entry.actor_id == plain_admin.member_id
    assert entry.target_id == plain_admin.member_id


@pytest.mark.contract
@pytest.mark.django_db
def test_ownership_transfer_is_recorded(session_client, workspace, create_user):
    other = WorkspaceMember.objects.create(
        workspace=workspace, member=UserFactory(), role=20, is_super_admin=True, is_active=True
    )
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/transfer-ownership/", {"member_id": str(other.member_id)}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "workspace.ownership_transferred")
    assert entry.before["owner_id"] == str(create_user.id)
    assert entry.after["owner_id"] == str(other.member_id)


# --- workspace details, create, delete, restore ---------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_rename_records_only_changed_fields(session_client, workspace):
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/",
        {"name": "Renamed", "slug": workspace.slug, "organization_size": workspace.organization_size},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "workspace.updated")
    assert entry.before == {"name": "Test Workspace"}
    assert entry.after == {"name": "Renamed"}
    assert entry.target_label == "Renamed"


@pytest.mark.contract
@pytest.mark.django_db
def test_web_address_change_is_recorded(session_client, workspace):
    response = session_client.patch(f"/api/workspaces/{workspace.slug}/", {"slug": "new-address"}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "workspace.updated")
    assert entry.before == {"slug": "test-workspace"}
    assert entry.after == {"slug": "new-address"}


@pytest.mark.contract
@pytest.mark.django_db
def test_save_without_changes_records_nothing(session_client, workspace):
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/", {"name": workspace.name, "slug": workspace.slug}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    assert _entries(workspace) == []


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.app.views.asset.v2.invalidate_cache_directly")
def test_logo_change_through_asset_api_is_recorded(_cache, workspace, create_user):
    asset = FileAsset.objects.create(
        workspace=workspace, asset="logo.png", attributes={}, entity_type="WORKSPACE_LOGO", is_uploaded=True
    )
    request = SimpleNamespace(user=create_user)
    WorkspaceFileAssetEndpoint().entity_asset_save(asset.id, FileAsset.EntityTypeContext.WORKSPACE_LOGO, asset, request)
    entry = _one(workspace, "workspace.updated")
    assert entry.before == {"logo_asset_id": None}
    assert entry.after == {"logo_asset_id": str(asset.id)}

    WorkspaceFileAssetEndpoint().entity_asset_delete(FileAsset.EntityTypeContext.WORKSPACE_LOGO, asset, request)
    removed = _entries(workspace, "workspace.updated")[-1]
    assert removed.before == {"logo_asset_id": str(asset.id)}
    assert removed.after == {"logo_asset_id": None}


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.bgtasks.workspace_seed_task.workspace_seed.delay")
def test_workspace_creation_is_recorded(_seed, session_client, workspace, create_user):
    response = session_client.post("/api/workspaces/", {"name": "Fresh", "slug": "fresh-ws"}, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    fresh = Workspace.objects.get(slug="fresh-ws")
    entry = _one(fresh, "workspace.created")
    assert entry.actor_id == create_user.id
    assert entry.after["name"] == "Fresh"


@pytest.mark.contract
@pytest.mark.django_db
def test_log_survives_delete_and_records_delete_and_restore(session_client, workspace):
    session_client.patch(f"/api/workspaces/{workspace.slug}/", {"name": "Before delete"}, format="json")
    response = session_client.delete(
        f"/api/workspaces/{workspace.slug}/", {"confirm_name": "Before delete", "password": PASSWORD}, format="json"
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
    soft_delete_related_objects("db", "workspace", workspace.pk)

    deleted = _one(workspace, "workspace.deleted")
    assert deleted.before == {"name": "Before delete", "slug": "test-workspace"}
    assert len(_entries(workspace)) == 2  # the rename is still there

    response = session_client.post(f"/api/users/me/deleted-workspaces/{workspace.id}/restore/", {}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    restored = _one(workspace, "workspace.restored")
    assert restored.after == {"slug": "test-workspace"}
    assert [e.action for e in _entries(workspace)] == ["workspace.updated", "workspace.deleted", "workspace.restored"]


@pytest.mark.contract
@pytest.mark.django_db
def test_purge_erases_the_log_with_the_workspace(create_user, audit_guard):
    old = Workspace.objects.create(name="Old", slug=f"old-{uuid.uuid4().hex[:6]}", owner=create_user)
    keep = Workspace.objects.create(name="Keep", slug=f"keep-{uuid.uuid4().hex[:6]}", owner=create_user)
    for ws in (old, keep):
        workspace_audit.record(ws.id, create_user, "workspace.created", "workspace", target_id=ws.id)
    Workspace.all_objects.filter(pk=old.pk).update(deleted_at=timezone.now() - timedelta(days=31))
    hard_delete()
    assert not WorkspaceAuditLog.objects.filter(workspace_id=old.id).exists()
    assert WorkspaceAuditLog.objects.filter(workspace_id=keep.id).count() == 1


# --- invitations ----------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_admin_invitation_is_recorded_member_invitation_is_not(session_client, workspace):
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/invitations/",
        {"emails": [{"email": "a@example.com", "role": 20}, {"email": "m@example.com", "role": 15}]},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "member.admin_invited")
    assert entry.target_label == "a@example.com"
    assert len(_entries(workspace)) == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_invitation_role_change_to_admin_is_recorded(session_client, workspace):
    invite = WorkspaceMemberInvite.objects.create(workspace=workspace, email="x@example.com", role=15)
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.pk}/", {"role": 20}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    entry = _one(workspace, "member.invite_role_changed")
    assert entry.before["role"] == 15 and entry.after["role"] == 20
    assert entry.target_id == invite.id


@pytest.mark.contract
@pytest.mark.django_db
def test_invitation_change_between_member_and_guest_is_not_recorded(session_client, workspace):
    invite = WorkspaceMemberInvite.objects.create(workspace=workspace, email="g@example.com", role=15)
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.pk}/", {"role": 5}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    assert _entries(workspace) == []


@pytest.mark.contract
@pytest.mark.django_db
def test_v1_api_admin_invitation_is_recorded(workspace, create_user):
    token = APIToken.objects.create(user=create_user, label="t")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    response = client.post(
        f"/api/v1/workspaces/{workspace.slug}/invitations/", {"email": "v1@example.com", "role": 20}, format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    entry = _one(workspace, "member.admin_invited")
    assert entry.target_label == "v1@example.com"
    assert entry.target_id is not None


# --- webhooks -------------------------------------------------------------------------------


SLACK_STYLE_URL = "https://hooks.example.com/services/T000/B000/s3cr3tPathToken?token=q"


@pytest.mark.contract
@pytest.mark.django_db
@patch("plane.app.serializers.webhook.validate_url")
def test_webhook_lifecycle_is_recorded_without_secrets(_validate, session_client, workspace):
    base = f"/api/workspaces/{workspace.slug}/webhooks/"
    response = session_client.post(base, {"url": SLACK_STYLE_URL, "project": True}, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    hook = Webhook.objects.get(workspace=workspace)

    assert session_client.patch(f"{base}{hook.pk}/", {"is_active": False}, format="json").status_code == 200
    assert session_client.post(f"{base}{hook.pk}/regenerate/").status_code == 200
    hook.refresh_from_db()
    secret = hook.secret_key
    assert session_client.delete(f"{base}{hook.pk}/").status_code == 204

    actions = [e.action for e in _entries(workspace)]
    assert actions == ["webhook.created", "webhook.updated", "webhook.secret_regenerated", "webhook.deleted"]
    updated = _one(workspace, "webhook.updated")
    assert updated.before == {"is_active": True} and updated.after == {"is_active": False}

    for entry in _entries(workspace):
        dumped = json.dumps([entry.before, entry.after, entry.target_label])
        assert secret not in dumped
        assert "s3cr3tPathToken" not in dumped and "token=q" not in dumped
        assert "secret_key" not in dumped
    assert _one(workspace, "webhook.created").after["url"].startswith("https://hooks.example.com/… (#")


@pytest.mark.contract
@pytest.mark.django_db
def test_redact_url_keeps_host_and_shows_changes():
    a = workspace_audit.redact_url("https://hooks.example.com/a")
    b = workspace_audit.redact_url("https://hooks.example.com/b")
    assert a.startswith("https://hooks.example.com/") and a != b
    assert "user:pw" not in workspace_audit.redact_url("https://user:pw@example.com:8443/x")
    assert workspace_audit.redact_url("https://user:pw@example.com:8443/x").startswith("https://example.com:8443/")


# --- reading the log ------------------------------------------------------------------------


def _audit_url(workspace, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/workspaces/{workspace.slug}/audit-log/" + (f"?{query}" if query else "")


@pytest.mark.contract
@pytest.mark.django_db
def test_only_super_admins_can_read_the_log(session_client, workspace, plain_admin, member):
    assert session_client.get(_audit_url(workspace)).status_code == status.HTTP_200_OK
    assert _client(plain_admin.member).get(_audit_url(workspace)).status_code == status.HTTP_403_FORBIDDEN
    assert _client(member.member).get(_audit_url(workspace)).status_code == status.HTTP_403_FORBIDDEN
    assert _client(UserFactory()).get(_audit_url(workspace)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_of_another_workspace_cannot_read(workspace, create_user):
    other_owner = UserFactory()
    other = Workspace.objects.create(name="Other", slug="other-ws", owner=other_owner)
    WorkspaceMember.objects.create(workspace=other, member=other_owner, role=20, is_super_admin=True)
    workspace_audit.record(workspace.id, create_user, "workspace.created", "workspace", target_id=workspace.id)
    assert _client(other_owner).get(_audit_url(workspace)).status_code == status.HTTP_403_FORBIDDEN
    response = _client(other_owner).get(_audit_url(other))
    assert response.status_code == status.HTTP_200_OK
    assert response.data["results"] == []


@pytest.mark.contract
@pytest.mark.django_db
def test_log_merges_time_and_renewals_settings_history(session_client, workspace, create_user):
    response = session_client.patch(
        f"/api/workspaces/{workspace.slug}/time/settings/", {"minutes_per_day": 420}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    # A worklog change is day-to-day work, not a setting: it stays out of this log.
    tt_audit.record(
        workspace.id, create_user, TimeTrackingAuditTarget.WORKLOG, uuid.uuid4(), TimeTrackingAuditAction.CREATE
    )
    session_client.patch(f"/api/workspaces/{workspace.slug}/", {"name": "Renamed"}, format="json")

    response = session_client.get(_audit_url(workspace))
    assert response.status_code == status.HTTP_200_OK
    rows = response.data["results"]
    assert [r["source"] for r in rows] == ["workspace", "time_tracking"]  # newest first
    tt = rows[1]
    assert tt["action"] == "time_tracking.setting.update"
    assert tt["action_label"] == "Time and Renewals: Setting changed"
    assert tt["actor"]["email"] == create_user.email
    assert rows[0]["action_label"] == "Workspace details changed"
    assert rows[0]["after"] == {"name": "Renamed"}
    assert response.data["next_cursor"] is None

    only_tt = session_client.get(_audit_url(workspace, source="time_tracking")).data["results"]
    assert [r["source"] for r in only_tt] == ["time_tracking"]


@pytest.mark.contract
@pytest.mark.django_db
def test_paging_walks_both_sources_without_gaps_or_repeats(session_client, workspace, create_user):
    expected = set()
    for i in range(7):
        expected.add(
            str(workspace_audit.record(workspace.id, create_user, "workspace.updated", "workspace", after={"i": i}).id)
        )
        expected.add(
            str(
                tt_audit.record(
                    workspace.id,
                    create_user,
                    TimeTrackingAuditTarget.HOLIDAY,
                    uuid.uuid4(),
                    TimeTrackingAuditAction.CREATE,
                    after={"name": f"Day {i}"},
                ).id
            )
        )
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        response = session_client.get(f"/api/workspaces/{workspace.slug}/audit-log/", params)
        assert response.status_code == status.HTTP_200_OK
        rows = response.data["results"]
        seen.extend(r["id"] for r in rows)
        pages += 1
        cursor = response.data["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 14
    assert set(seen) == expected
    assert pages == 4
    page = session_client.get(_audit_url(workspace, limit=14)).data["results"]
    stamps = [(r["created_at"], r["id"]) for r in page]
    assert stamps == sorted(stamps, reverse=True)
    holiday = next(r for r in page if r["source"] == "time_tracking")
    assert holiday["target_label"].startswith("Day ")


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("params", [{"cursor": "nonsense"}, {"cursor": "2026-01-01T00:00:00|x"}, {"source": "x"}])
def test_bad_query_is_refused(session_client, workspace, params):
    response = session_client.get(f"/api/workspaces/{workspace.slug}/audit-log/", params)
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- append-only ----------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_orm_refuses_changing_or_deleting_an_entry(workspace, create_user):
    entry = workspace_audit.record(workspace.id, create_user, "workspace.created", "workspace")
    entry.target_label = "x"
    with pytest.raises(ValueError, match="append-only"):
        entry.save()
    with pytest.raises(ValueError, match="append-only"):
        entry.delete()


@pytest.mark.contract
@pytest.mark.django_db
def test_database_refuses_changing_or_deleting_an_entry(workspace, create_user, audit_guard):
    entry = workspace_audit.record(workspace.id, create_user, "workspace.created", "workspace")
    for sql in (
        "UPDATE workspace_audit_logs SET action = 'x' WHERE id = %s",
        "UPDATE workspace_audit_logs SET actor_id = NULL WHERE id = %s",
        "DELETE FROM workspace_audit_logs WHERE id = %s",
    ):
        with pytest.raises(DatabaseError, match="append-only"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql, [str(entry.pk)])
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            WorkspaceAuditLog.objects.filter(pk=entry.pk).delete()
    assert WorkspaceAuditLog.objects.get(pk=entry.pk).action == "workspace.created"


@pytest.mark.contract
@pytest.mark.django_db
def test_erasing_the_actor_keeps_the_entry_and_its_name(workspace, audit_guard):
    actor = UserFactory(first_name="Gone", last_name="Person")
    WorkspaceMember.objects.create(workspace=workspace, member=actor, role=20, is_active=True)
    entry = workspace_audit.record(workspace.id, actor, "workspace.updated", "workspace")
    User.objects.filter(pk=actor.pk).delete()  # the database FK sets actor_id to NULL
    kept = WorkspaceAuditLog.objects.get(pk=entry.pk)
    assert kept.actor_id is None
    assert kept.actor_name == "Gone Person"
