# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- add people to a project by email (Part 1)."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Project,
    ProjectIdentifier,
    ProjectMember,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberInvite,
    WorkspaceMemberInviteProject,
)
from plane.tests.factories import UserFactory

SEND = "plane.app.views.project.invite_by_email.workspace_invitation.delay"
NOTIFY = "plane.app.views.project.invite_by_email.project_add_user_email.delay"


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _project(workspace, owner, name, ident):
    project = Project.objects.create(workspace=workspace, name=name, identifier=ident, created_by=owner)
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name=ident)
    return project


def _ws_member(workspace, role=15, is_active=True):
    user = UserFactory()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=is_active)
    return user


def _proj_member(project, user, role):
    return ProjectMember.objects.create(project=project, member=user, role=role, workspace=project.workspace)


def _base(workspace, project):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}"


@pytest.fixture(autouse=True)
def mocked_tasks():
    with patch(SEND) as send, patch(NOTIFY) as notify:
        yield {"send": send, "notify": notify}


@pytest.fixture
def project(db, workspace, create_user):
    p = _project(workspace, create_user, "Alpha", "ALP")
    _proj_member(p, create_user, 20)
    return p


@pytest.fixture
def project2(db, workspace, create_user):
    p = _project(workspace, create_user, "Beta", "BET")
    _proj_member(p, create_user, 20)
    return p


@pytest.fixture
def admin_client(create_user):
    """create_user is workspace admin (super admin) and project admin."""
    return _client(create_user)


@pytest.fixture
def project_admin(workspace, project):
    """Project Admin who is only a workspace Member."""
    user = _ws_member(workspace, 15)
    _proj_member(project, user, 20)
    return user


def _invite(client, workspace, project, emails, **extra):
    body = {"emails": emails, **extra}
    return client.post(f"{_base(workspace, project)}/members/invite-by-email/", body, format="json")


def _status_of(response, email):
    return next(r for r in response.data["results"] if r["email"] == email)


# --- endpoint 1 --------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_existing_workspace_member_is_added_directly(admin_client, workspace, project, mocked_tasks):
    user = _ws_member(workspace, 15)
    res = _invite(admin_client, workspace, project, [{"email": user.email.upper(), "role": 15}])
    assert res.status_code == 200
    assert res.data["results"] == [{"email": user.email, "status": "added"}]
    assert ProjectMember.objects.get(project=project, member=user).role == 15
    assert not WorkspaceMemberInvite.objects.filter(workspace=workspace).exists()
    mocked_tasks["send"].assert_not_called()


@pytest.mark.contract
@pytest.mark.django_db
def test_inactive_project_member_is_reactivated_not_duplicated(admin_client, workspace, project):
    user = _ws_member(workspace, 15)
    pm = _proj_member(project, user, 5)
    ProjectMember.objects.filter(pk=pm.pk).update(is_active=False)
    res = _invite(admin_client, workspace, project, [{"email": user.email, "role": 15}])
    assert _status_of(res, user.email)["status"] == "added"
    assert ProjectMember.objects.filter(project=project, member=user).count() == 1
    pm.refresh_from_db()
    assert pm.is_active and pm.role == 15


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_guest_role_is_capped(admin_client, workspace, project):
    guest = _ws_member(workspace, 5)
    _invite(admin_client, workspace, project, [{"email": guest.email, "role": 20}])
    assert ProjectMember.objects.get(project=project, member=guest).role == 5


@pytest.mark.contract
@pytest.mark.django_db
def test_already_member(admin_client, workspace, project):
    user = _ws_member(workspace, 15)
    _proj_member(project, user, 15)
    res = _invite(admin_client, workspace, project, [{"email": user.email, "role": 20}])
    assert _status_of(res, user.email)["status"] == "already_member"
    assert ProjectMember.objects.get(project=project, member=user).role == 15


@pytest.mark.contract
@pytest.mark.django_db
def test_non_admin_project_admin_gets_not_allowed_for_new_email(project_admin, workspace, project, mocked_tasks):
    existing = _ws_member(workspace, 15)
    res = _invite(
        _client(project_admin),
        workspace,
        project,
        [{"email": "new@example.com", "role": 15}, {"email": existing.email, "role": 15}],
    )
    assert res.status_code == 200
    new = _status_of(res, "new@example.com")
    assert new["status"] == "not_allowed" and new["detail"]
    assert _status_of(res, existing.email)["status"] == "added"
    assert not WorkspaceMemberInvite.objects.filter(email="new@example.com").exists()
    assert not WorkspaceMemberInviteProject.objects.exists()
    mocked_tasks["send"].assert_not_called()


@pytest.mark.contract
@pytest.mark.django_db
def test_new_email_creates_one_invite_and_spot_and_one_email(admin_client, workspace, project, mocked_tasks):
    res = _invite(
        admin_client,
        workspace,
        project,
        [{"email": "  New@Example.com ", "role": 15}],
        workspace_role=5,
        message="Welcome <b>aboard</b>",
    )
    assert _status_of(res, "new@example.com")["status"] == "invited"
    invite = WorkspaceMemberInvite.objects.get(workspace=workspace, email="new@example.com")
    assert invite.role == 5 and invite.created_from_project is True
    spot = WorkspaceMemberInviteProject.objects.get(invite=invite)
    assert spot.project_id == project.id and spot.role == 15 and spot.workspace_id == workspace.id
    mocked_tasks["send"].assert_called_once()
    args = mocked_tasks["send"].call_args.args
    assert args[0] == "new@example.com" and args[2] == invite.token
    assert args[-1] == "Welcome <b>aboard</b>"


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_role_defaults_to_member_and_admin_rejected(admin_client, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    assert WorkspaceMemberInvite.objects.get(email="a@example.com").role == 15
    res = _invite(admin_client, workspace, project, [{"email": "b@example.com", "role": 15}], workspace_role=20)
    assert res.status_code == 400


@pytest.mark.contract
@pytest.mark.django_db
def test_second_project_reuses_invite_without_second_email(admin_client, workspace, project, project2, mocked_tasks):
    _invite(admin_client, workspace, project, [{"email": "new@example.com", "role": 15}])
    res = _invite(admin_client, workspace, project2, [{"email": "new@example.com", "role": 20}], workspace_role=5)
    assert _status_of(res, "new@example.com")["status"] == "invite_updated"
    assert WorkspaceMemberInvite.objects.filter(email="new@example.com").count() == 1
    invite = WorkspaceMemberInvite.objects.get(email="new@example.com")
    assert invite.role == 15  # existing invite's workspace role is kept
    assert WorkspaceMemberInviteProject.objects.filter(invite=invite).count() == 2
    mocked_tasks["send"].assert_called_once()


@pytest.mark.contract
@pytest.mark.django_db
def test_validation_and_limits(admin_client, workspace, project):
    res = _invite(
        admin_client, workspace, project, [{"email": "not-an-email", "role": 15}, {"email": "x@e.com", "role": 7}]
    )
    assert [r["status"] for r in res.data["results"]] == ["invalid", "invalid"]
    assert _invite(admin_client, workspace, project, []).status_code == 400
    many = [{"email": f"u{i}@example.com", "role": 15} for i in range(51)]
    assert _invite(admin_client, workspace, project, many).status_code == 400


@pytest.mark.contract
@pytest.mark.django_db
def test_requires_project_admin(workspace, project):
    member = _ws_member(workspace, 15)
    _proj_member(project, member, 15)
    res = _invite(_client(member), workspace, project, [{"email": "n@example.com", "role": 15}])
    assert res.status_code == 403


@pytest.mark.contract
@pytest.mark.django_db
def test_email_template_escapes_personal_message():
    from django.template.loader import render_to_string

    html = render_to_string(
        "emails/invitations/workspace_invitation.html",
        {
            "email": "a@b.com",
            "first_name": "Ann",
            "workspace_name": "WS",
            "abs_url": "http://x/y",
            "personal_message": "<script>alert(1)</script>\nline2",
        },
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "line2" in html


# --- accepting ---------------------------------------------------------------------------------


def _pending(admin_client, workspace, project, project2, email="new@example.com", ws_role=15):
    _invite(admin_client, workspace, project, [{"email": email, "role": 20}], workspace_role=ws_role)
    _invite(admin_client, workspace, project2, [{"email": email, "role": 15}], workspace_role=ws_role)
    return WorkspaceMemberInvite.objects.get(email=email)


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_via_join_endpoint(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    user = UserFactory(email="new@example.com")
    res = _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": invite.token, "accepted": True},
        format="json",
    )
    assert res.status_code == 200
    assert res.data["project_id"] in (str(project.id), str(project2.id))
    assert ProjectMember.objects.get(project=project, member=user).role == 20
    assert ProjectMember.objects.get(project=project2, member=user).role == 15
    assert not WorkspaceMemberInviteProject.objects.filter(invite_id=invite.id).exists()
    assert not WorkspaceMemberInvite.objects.filter(pk=invite.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_join_endpoint_project_id_null_without_spots(workspace, create_user):
    invite = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="plain@example.com", token="tok", role=15, created_by=create_user
    )
    user = UserFactory(email="plain@example.com")
    res = _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": "tok", "accepted": True},
        format="json",
    )
    assert res.status_code == 200 and res.data["project_id"] is None


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_as_guest_caps_project_role(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2, ws_role=5)
    user = UserFactory(email="new@example.com")
    _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": invite.token, "accepted": True},
        format="json",
    )
    assert ProjectMember.objects.get(project=project, member=user).role == 5


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_from_invitations_list(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    user = UserFactory(email="new@example.com")
    res = _client(user).post("/api/users/me/workspaces/invitations/", {"invitations": [str(invite.id)]}, format="json")
    assert res.status_code == 204
    assert ProjectMember.objects.filter(member=user, project__in=[project, project2], is_active=True).count() == 2
    assert not WorkspaceMemberInviteProject.objects.filter(invite_id=invite.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_on_signup_path(admin_client, workspace, project, project2):
    from plane.authentication.utils.workspace_project_join import process_workspace_project_invitations

    invite = _pending(admin_client, workspace, project, project2)
    WorkspaceMemberInvite.objects.filter(pk=invite.pk).update(accepted=True)
    user = UserFactory(email="new@example.com")
    process_workspace_project_invitations(user)
    assert WorkspaceMember.objects.filter(workspace=workspace, member=user, is_active=True).exists()
    assert ProjectMember.objects.filter(member=user, project__in=[project, project2]).count() == 2
    assert not WorkspaceMemberInviteProject.objects.filter(invite_id=invite.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_reactivates_inactive_project_member(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    user = UserFactory(email="new@example.com")
    pm = _proj_member(project, user, 5)
    ProjectMember.objects.filter(pk=pm.pk).update(is_active=False)
    _client(user).post("/api/users/me/workspaces/invitations/", {"invitations": [str(invite.id)]}, format="json")
    assert ProjectMember.objects.filter(project=project, member=user).count() == 1
    pm.refresh_from_db()
    assert pm.is_active and pm.role == 20


# --- pending list / cancel / resend ------------------------------------------------------------


def _pending_url(workspace, project, spot_id=None, suffix=""):
    base = f"{_base(workspace, project)}/pending-members/"
    return base + (f"{spot_id}/" if spot_id else "") + suffix


@pytest.mark.contract
@pytest.mark.django_db
def test_pending_list_shape_and_invite_link_visibility(admin_client, project_admin, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "new@example.com", "role": 15}])
    invite = WorkspaceMemberInvite.objects.get(email="new@example.com")

    res = admin_client.get(_pending_url(workspace, project))
    assert res.status_code == 200 and len(res.data) == 1
    row = res.data[0]
    assert row["email"] == "new@example.com" and row["role"] == 15 and row["workspace_role"] == 15
    assert row["invite_id"] == invite.id and row["invited_by"]["id"]
    assert f"token={invite.token}" in row["invite_link"] and f"invitation_id={invite.id}" in row["invite_link"]

    res = _client(project_admin).get(_pending_url(workspace, project))
    assert res.status_code == 200 and len(res.data) == 1
    assert "invite_link" not in res.data[0]


@pytest.mark.contract
@pytest.mark.django_db
def test_pending_list_excludes_other_projects_and_accepted(admin_client, workspace, project, project2):
    _invite(admin_client, workspace, project2, [{"email": "other@example.com", "role": 15}])
    assert admin_client.get(_pending_url(workspace, project)).data == []


@pytest.mark.contract
@pytest.mark.django_db
def test_cancel_one_spot_of_several_keeps_invite(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    spot = WorkspaceMemberInviteProject.objects.get(invite=invite, project=project)
    assert admin_client.delete(_pending_url(workspace, project, spot.id)).status_code == 204
    assert WorkspaceMemberInvite.objects.filter(pk=invite.pk).exists()
    assert WorkspaceMemberInviteProject.objects.filter(invite=invite).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_cancel_last_spot_removes_invite_only_if_created_from_project(admin_client, workspace, project, project2):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    assert admin_client.delete(_pending_url(workspace, project, spot.id)).status_code == 204
    assert not WorkspaceMemberInvite.objects.filter(email="a@example.com").exists()

    # an invitation made from the workspace Members page survives its last spot
    ws_invite = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="b@example.com", token="t", role=15, created_from_project=False
    )
    spot = WorkspaceMemberInviteProject.objects.create(invite=ws_invite, project=project, workspace=workspace, role=15)
    assert admin_client.delete(_pending_url(workspace, project, spot.id)).status_code == 204
    assert WorkspaceMemberInvite.objects.filter(pk=ws_invite.pk).exists()
    assert not WorkspaceMemberInviteProject.objects.filter(invite=ws_invite).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_reinvite_after_cancel_works(admin_client, workspace, project, mocked_tasks):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    admin_client.delete(_pending_url(workspace, project, spot.id))
    res = _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 20}])
    assert _status_of(res, "a@example.com")["status"] == "invited"
    assert WorkspaceMemberInvite.objects.filter(email="a@example.com").count() == 1
    assert WorkspaceMemberInviteProject.objects.filter(project=project).count() == 1
    assert mocked_tasks["send"].call_count == 2


@pytest.mark.contract
@pytest.mark.django_db
def test_reinvite_after_workspace_invite_cancel_works(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    res = admin_client.delete(f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/")
    assert res.status_code == 204
    assert not WorkspaceMemberInviteProject.objects.filter(invite_id=invite.id).exists()
    assert admin_client.get(_pending_url(workspace, project)).data == []
    res = _invite(admin_client, workspace, project, [{"email": "new@example.com", "role": 15}])
    assert _status_of(res, "new@example.com")["status"] == "invited"


@pytest.mark.contract
@pytest.mark.django_db
def test_resend_requires_workspace_admin(admin_client, project_admin, workspace, project, mocked_tasks):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    mocked_tasks["send"].reset_mock()

    res = _client(project_admin).post(_pending_url(workspace, project, spot.id, "resend/"))
    assert res.status_code == 403
    mocked_tasks["send"].assert_not_called()

    res = admin_client.post(_pending_url(workspace, project, spot.id, "resend/"))
    assert res.status_code == 200
    mocked_tasks["send"].assert_called_once()
    assert mocked_tasks["send"].call_args.args[0] == "a@example.com"
    from django.core.cache import cache

    cache.delete(f"psr124:resend-invite:{spot.invite_id}")


@pytest.mark.contract
@pytest.mark.django_db
def test_project_admin_can_cancel_spot(admin_client, project_admin, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    assert _client(project_admin).delete(_pending_url(workspace, project, spot.id)).status_code == 204


# --- IDOR --------------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_cross_project_and_cross_workspace_ids_return_404(admin_client, workspace, project, project2):
    _invite(admin_client, workspace, project2, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()

    # spot of project2 addressed through project's URL
    assert admin_client.delete(_pending_url(workspace, project, spot.id)).status_code == 404
    assert admin_client.post(_pending_url(workspace, project, spot.id, "resend/")).status_code == 404

    # spot of another workspace addressed through our own project
    other_owner = UserFactory()
    other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=other_owner)
    WorkspaceMember.objects.create(workspace=other_ws, member=other_owner, role=20)
    other_project = _project(other_ws, other_owner, "Gamma", "GAM")
    _proj_member(other_project, other_owner, 20)
    other_client = _client(other_owner)
    other_client.post(
        f"{_base(other_ws, other_project)}/members/invite-by-email/",
        {"emails": [{"email": "z@example.com", "role": 15}]},
        format="json",
    )
    foreign = WorkspaceMemberInviteProject.objects.get(workspace=other_ws)
    assert admin_client.delete(_pending_url(workspace, project, foreign.id)).status_code == 404
    assert other_client.delete(_pending_url(other_ws, other_project, spot.id)).status_code == 404
    assert WorkspaceMemberInviteProject.objects.count() == 2

    # no access at all to another workspace's project
    res = admin_client.get(_pending_url(other_ws, other_project))
    assert res.status_code == 403


# --- review round 1 ----------------------------------------------------------------------------


def _join(user, workspace, invite, accepted=True):
    return _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": invite.token, "accepted": accepted},
        format="json",
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_join_rolls_back_invite_state_when_project_adds_fail(admin_client, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "new@example.com", "role": 15}])
    invite = WorkspaceMemberInvite.objects.get(email="new@example.com")
    user = UserFactory(email="new@example.com")
    with patch("plane.app.views.workspace.invite.accept_project_spots", side_effect=RuntimeError("boom")):
        assert _join(user, workspace, invite).status_code == 500
    invite.refresh_from_db()
    assert invite.responded_at is None and invite.accepted is False
    assert not WorkspaceMember.objects.filter(workspace=workspace, member=user).exists()
    # the invite can still be accepted afterwards
    assert _join(user, workspace, invite).status_code == 200
    assert ProjectMember.objects.filter(project=project, member=user).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_join_lookup_is_case_insensitive_on_user_email(admin_client, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "case@example.com", "role": 15}])
    invite = WorkspaceMemberInvite.objects.get(email="case@example.com")
    user = UserFactory(email="Case@Example.com")
    assert _join(user, workspace, invite).status_code == 200
    assert ProjectMember.objects.filter(project=project, member=user).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_signup_path_skips_spots_for_inactive_workspace_member(admin_client, workspace, project, project2):
    from plane.authentication.utils.workspace_project_join import process_workspace_project_invitations

    invite = _pending(admin_client, workspace, project, project2)
    WorkspaceMemberInvite.objects.filter(pk=invite.pk).update(accepted=True)
    user = UserFactory(email="new@example.com")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=False)
    process_workspace_project_invitations(user)
    assert not ProjectMember.objects.filter(member=user, project__in=[project, project2]).exists()
    assert not WorkspaceMember.objects.get(workspace=workspace, member=user).is_active


@pytest.mark.contract
@pytest.mark.django_db
def test_invitations_list_accept_skips_spots_without_active_workspace_role(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)
    user = UserFactory(email="new@example.com")
    with patch("plane.app.views.workspace.invite.accept_project_spots") as accept:
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=False)
        _client(user).post("/api/users/me/workspaces/invitations/", {"invitations": [str(invite.id)]}, format="json")
    # the invitation reactivates the member first, so the spots are handed over with an active role
    accept.assert_called_once()


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_admin_becomes_project_admin_on_accept(workspace, project, create_user):
    from plane.utils.project_invite_spots import cap_project_role

    assert cap_project_role(5, 20) == 20 and cap_project_role(15, 20) == 20
    assert cap_project_role(20, 5) == 5 and cap_project_role(5, 15) == 5
    invite = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="adm@example.com", token="tok", role=20, created_by=create_user
    )
    WorkspaceMemberInviteProject.objects.create(invite=invite, project=project, workspace=workspace, role=5)
    user = UserFactory(email="adm@example.com")
    assert _join(user, workspace, invite).status_code == 200
    assert ProjectMember.objects.get(project=project, member=user).role == 20


@pytest.mark.contract
@pytest.mark.django_db
def test_accept_does_not_change_role_of_active_project_member(admin_client, workspace, project, project2):
    invite = _pending(admin_client, workspace, project, project2)  # spots: project=20, project2=15
    user = UserFactory(email="new@example.com")
    _proj_member(project, user, 5)
    _proj_member(project2, user, 20)
    assert _join(user, workspace, invite).status_code == 200
    assert ProjectMember.objects.get(project=project, member=user).role == 5
    assert ProjectMember.objects.get(project=project2, member=user).role == 20


@pytest.mark.contract
@pytest.mark.django_db
def test_reactivated_member_gets_user_property_row(admin_client, workspace, project, project2):
    from plane.db.models import ProjectUserProperty

    invite = _pending(admin_client, workspace, project, project2)
    user = UserFactory(email="new@example.com")
    pm = _proj_member(project, user, 5)
    ProjectMember.objects.filter(pk=pm.pk).update(is_active=False)
    ProjectUserProperty.all_objects.filter(project=project, user=user).delete()
    assert not ProjectUserProperty.objects.filter(project=project, user=user).exists()
    _join(user, workspace, invite)
    assert ProjectUserProperty.objects.filter(project=project, user=user).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_concurrent_invite_collision_does_not_fail_the_batch(admin_client, workspace, project):
    from plane.app.views.project import invite_by_email as mod

    real = mod._invite_and_spot
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise mod.IntegrityError("duplicate key")
        return real(*args, **kwargs)

    # another request got there first for a@example.com
    WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="a@example.com", token="t", role=15, created_by=None, created_from_project=True
    )
    with patch.object(mod, "_invite_and_spot", side_effect=flaky):
        res = _invite(
            admin_client,
            workspace,
            project,
            [{"email": "a@example.com", "role": 15}, {"email": "b@example.com", "role": 15}],
        )
    assert res.status_code == 200
    assert _status_of(res, "a@example.com")["status"] == "invite_updated"
    assert _status_of(res, "b@example.com")["status"] == "invited"
    assert WorkspaceMemberInviteProject.objects.filter(project=project).count() == 2


@pytest.mark.contract
@pytest.mark.django_db
def test_existing_invite_lookup_is_case_insensitive(admin_client, workspace, project, create_user):
    WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="Mixed@Example.com", token="t", role=15, created_by=create_user
    )
    res = _invite(admin_client, workspace, project, [{"email": "mixed@example.com", "role": 15}])
    assert _status_of(res, "mixed@example.com")["status"] == "invite_updated"
    assert WorkspaceMemberInvite.objects.filter(workspace=workspace, email__iexact="mixed@example.com").count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_deleting_project_cancels_project_created_invites_without_spots(admin_client, workspace, project, project2):
    _invite(admin_client, workspace, project, [{"email": "solo@example.com", "role": 15}])
    _invite(admin_client, workspace, project, [{"email": "both@example.com", "role": 15}])
    _invite(admin_client, workspace, project2, [{"email": "both@example.com", "role": 15}])
    WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="ws@example.com", token="t", role=15, created_by=None, created_from_project=False
    )
    ws_invite = WorkspaceMemberInvite.objects.get(email="ws@example.com")
    WorkspaceMemberInviteProject.objects.create(invite=ws_invite, project=project, workspace=workspace, role=15)

    res = admin_client.delete(f"/api/workspaces/{workspace.slug}/projects/{project.id}/")
    assert res.status_code == 204
    assert not WorkspaceMemberInvite.objects.filter(email="solo@example.com").exists()
    assert WorkspaceMemberInvite.objects.filter(email="both@example.com").exists()
    assert WorkspaceMemberInvite.objects.filter(email="ws@example.com").exists()
    assert not WorkspaceMemberInviteProject.objects.filter(project_id=project.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_members_page_reinvite_makes_project_invite_workspace_level(admin_client, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    with patch("plane.app.views.workspace.invite.workspace_invitation.delay"):
        res = admin_client.post(
            f"/api/workspaces/{workspace.slug}/invitations/",
            {"emails": [{"email": "A@example.com", "role": 15}]},
            format="json",
        )
    assert res.status_code == 200
    assert WorkspaceMemberInvite.objects.get(email="a@example.com").created_from_project is False
    spot = WorkspaceMemberInviteProject.objects.get()
    assert admin_client.delete(_pending_url(workspace, project, spot.id)).status_code == 204
    assert WorkspaceMemberInvite.objects.filter(email="a@example.com").exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_pending_list_effective_role(admin_client, workspace, project, create_user):
    _invite(admin_client, workspace, project, [{"email": "g@example.com", "role": 20}], workspace_role=5)
    _invite(admin_client, workspace, project, [{"email": "m@example.com", "role": 15}])
    adm = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="adm@example.com", token="t", role=20, created_by=create_user
    )
    WorkspaceMemberInviteProject.objects.create(invite=adm, project=project, workspace=workspace, role=5)
    rows = {r["email"]: r for r in admin_client.get(_pending_url(workspace, project)).data}
    assert rows["g@example.com"]["role"] == 20 and rows["g@example.com"]["effective_role"] == 5
    assert rows["m@example.com"]["effective_role"] == 15
    assert rows["adm@example.com"]["role"] == 5 and rows["adm@example.com"]["effective_role"] == 20


@pytest.mark.contract
@pytest.mark.django_db
def test_resend_has_a_per_invite_cooldown(admin_client, workspace, project, mocked_tasks):
    from django.core.cache import cache

    _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    cache.delete(f"psr124:resend-invite:{spot.invite_id}")
    mocked_tasks["send"].reset_mock()
    try:
        assert admin_client.post(_pending_url(workspace, project, spot.id, "resend/")).status_code == 200
        res = admin_client.post(_pending_url(workspace, project, spot.id, "resend/"))
        assert res.status_code == 429
        assert res.data == {"error": "Please wait a minute before resending this invitation."}
        mocked_tasks["send"].assert_called_once()
    finally:
        cache.delete(f"psr124:resend-invite:{spot.invite_id}")


@pytest.mark.contract
@pytest.mark.django_db
def test_public_api_project_delete_cancels_project_created_invites(admin_client, api_key_client, workspace, project):
    _invite(admin_client, workspace, project, [{"email": "solo@example.com", "role": 15}])
    invite = WorkspaceMemberInvite.objects.get(email="solo@example.com")
    res = api_key_client.delete(f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/")
    assert res.status_code == 204
    assert not WorkspaceMemberInviteProject.objects.filter(project_id=project.id).exists()
    assert not WorkspaceMemberInvite.objects.filter(pk=invite.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_integrity_fallback_never_attaches_spot_to_a_declined_invite(admin_client, workspace, project):
    from django.utils import timezone

    from plane.app.views.project import invite_by_email as mod

    WorkspaceMemberInvite.objects.create(
        workspace=workspace,
        email="a@example.com",
        token="t",
        role=15,
        accepted=False,
        responded_at=timezone.now(),
        created_by=None,
    )
    with patch.object(mod, "_invite_and_spot", side_effect=mod.IntegrityError("duplicate key")):
        res = _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}])
    assert res.status_code == 200
    assert _status_of(res, "a@example.com")["status"] == "invalid"
    assert _status_of(res, "a@example.com")["detail"] == "Please try again."
    assert not WorkspaceMemberInviteProject.objects.filter(project=project).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_declined_invite_is_replaced_and_resent_with_the_note(admin_client, workspace, project, mocked_tasks):
    from django.utils import timezone

    old = WorkspaceMemberInvite.objects.create(
        workspace=workspace,
        email="a@example.com",
        token="old",
        role=15,
        accepted=False,
        responded_at=timezone.now(),
        created_by=None,
    )
    res = _invite(admin_client, workspace, project, [{"email": "a@example.com", "role": 15}], message="Hello")
    assert _status_of(res, "a@example.com")["status"] == "invited"
    assert not WorkspaceMemberInvite.objects.filter(pk=old.pk).exists()
    mocked_tasks["send"].assert_called_once()
    assert mocked_tasks["send"].call_args.args[-1] == "Hello"


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_join_processing_matches_invite_email_case_insensitively(workspace, project):
    from plane.authentication.utils.workspace_project_join import process_workspace_project_invitations

    user = UserFactory(email="mixed@example.com")
    invite = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email="Mixed@Example.com", token="t", role=15, accepted=True, created_by=None
    )
    WorkspaceMemberInviteProject.objects.create(invite=invite, project=project, workspace=workspace, role=15)
    process_workspace_project_invitations(user)
    assert WorkspaceMember.objects.filter(workspace=workspace, member=user).exists()
    assert ProjectMember.objects.filter(project=project, member=user).exists()


# FORK: PSR-124 -- invites are locked while a spot is attached (cancel race), in email order
# (no deadlock between overlapping batches); results still come back in request order.
@pytest.mark.contract
@pytest.mark.django_db
def test_batch_locks_invites_and_keeps_request_order(admin_client, workspace, project, mocked_tasks):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    existing = _ws_member(workspace, 15)
    emails = ["zed@example.com", existing.email, "amy@example.com", "not-an-email"]
    with CaptureQueriesContext(connection) as ctx:
        res = _invite(admin_client, workspace, project, [{"email": e, "role": 15} for e in emails])
    assert res.status_code == 200
    assert [r["email"] for r in res.data["results"]] == [e.lower() for e in emails]
    assert [r["status"] for r in res.data["results"]] == ["invited", "added", "invited", "invalid"]
    locked = [
        q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"] and "workspace_member_invites" in q["sql"]
    ]
    assert len(locked) >= 2
