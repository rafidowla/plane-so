# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- Part 2: invitation expiry, allowed domains, project-admin invites, audit log."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from plane.authentication.adapter.base import Adapter
from plane.authentication.adapter.error import AuthenticationException
from plane.bgtasks.workspace_invite_expiry_task import record_expired_invitations
from plane.db.models import (
    Project,
    ProjectIdentifier,
    ProjectMember,
    WorkspaceAuditLog,
    WorkspaceInviteSettings,
    WorkspaceMember,
    WorkspaceMemberInvite,
    WorkspaceMemberInviteProject,
)
from plane.tests.factories import UserFactory
from plane.utils.invite_settings import (
    email_domain_allowed,
    get_invite_settings,
    invite_expires_at,
    is_invite_expired,
    normalize_domains,
)

SEND_PROJECT = "plane.app.views.project.invite_by_email.workspace_invitation.delay"
NOTIFY = "plane.app.views.project.invite_by_email.project_add_user_email.delay"
SEND_WS = "plane.app.views.workspace.invite.workspace_invitation.delay"
SEND_RESEND = "plane.app.views.workspace.invite_settings.workspace_invitation.delay"
EXPIRED_MSG = "This invitation has expired. Ask a workspace admin to resend it."


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _ws_member(workspace, role=15):
    user = UserFactory()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=True)
    return user


def _project(workspace, owner):
    p = Project.objects.create(workspace=workspace, name="Alpha", identifier="ALP", created_by=owner)
    ProjectIdentifier.objects.create(workspace=workspace, project=p, name="ALP")
    ProjectMember.objects.create(project=p, member=owner, role=20, workspace=workspace)
    return p


@pytest.fixture(autouse=True)
def mocked_tasks():
    cache.clear()
    # all three paths share one task object, so one patch (one mock) covers them
    with patch(SEND_PROJECT) as send, patch(NOTIFY):
        yield {"project": send, "workspace": send, "resend": send}


@pytest.fixture
def project(db, workspace, create_user):
    return _project(workspace, create_user)


@pytest.fixture
def admin_client(create_user):
    return _client(create_user)


@pytest.fixture
def project_admin(workspace, project):
    user = _ws_member(workspace, 15)
    ProjectMember.objects.create(project=project, member=user, role=20, workspace=workspace)
    return user


def _settings_url(workspace):
    return f"/api/workspaces/{workspace.slug}/invite-settings/"


def _set(workspace, **fields):
    WorkspaceInviteSettings.objects.update_or_create(workspace=workspace, defaults=fields)


def _invite_row(workspace, email="new@example.com", role=15, age_days=0, **extra):
    invite = WorkspaceMemberInvite.objects.create(
        workspace=workspace, email=email, token=f"tok-{email}", role=role, **extra
    )
    WorkspaceMemberInvite.objects.filter(pk=invite.pk).update(last_sent_at=timezone.now() - timedelta(days=age_days))
    invite.refresh_from_db()
    return invite


def _post_project_invite(client, workspace, project, emails, **extra):
    return client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/members/invite-by-email/",
        {"emails": emails, **extra},
        format="json",
    )


def _audit(workspace, action):
    return list(WorkspaceAuditLog.objects.filter(workspace=workspace, action=action).order_by("created_at"))


# --- pure helpers ------------------------------------------------------------------------------


def test_normalize_domains():
    assert normalize_domains([" @Groundfloor.CO ", "groundfloor.co", "a.b.com"]) == ["groundfloor.co", "a.b.com"]
    for bad in (["no spaces.com"], ["nodot"], ["-a.com"], ["a..com"], [""], [5], "a.com"):
        with pytest.raises(ValueError):
            normalize_domains(bad)
    with pytest.raises(ValueError):
        normalize_domains([f"d{i}.com" for i in range(51)])
    with pytest.raises(ValueError):
        normalize_domains(["a" * 250 + ".com"])


def test_email_domain_allowed():
    assert email_domain_allowed("x@any.com", [])
    assert email_domain_allowed("x@Groundfloor.co", ["groundfloor.co"])
    assert not email_domain_allowed("x@sub.groundfloor.co", ["groundfloor.co"])  # no subdomain match
    assert not email_domain_allowed("x@groundfloor.co.evil.com", ["groundfloor.co"])
    assert not email_domain_allowed("x@evil.com", ["groundfloor.co"])
    assert email_domain_allowed("a@b@groundfloor.co", ["groundfloor.co"])  # part after the LAST @
    assert not email_domain_allowed("nodomain", ["groundfloor.co"])


@pytest.mark.django_db
def test_expiry_helpers_and_get_does_not_create_row(workspace):
    assert get_invite_settings(workspace.id).invite_expiry_days == 7
    assert not WorkspaceInviteSettings.objects.exists()
    invite = _invite_row(workspace, age_days=6)
    assert not is_invite_expired(invite, 7)
    assert invite_expires_at(invite, 7) == invite.last_sent_at + timedelta(days=7)
    assert is_invite_expired(_invite_row(workspace, "old@example.com", age_days=8), 7)


# --- settings endpoint -------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_settings_get_defaults_for_any_member_without_creating_row(workspace, admin_client):
    guest = _ws_member(workspace, 5)
    for client in (admin_client, _client(guest)):
        res = client.get(_settings_url(workspace))
        assert res.status_code == 200
        assert res.data == {"invite_expiry_days": 7, "allowed_email_domains": [], "project_admins_can_invite": False}
    assert not WorkspaceInviteSettings.objects.exists()
    assert _client(UserFactory()).get(_settings_url(workspace)).status_code == 403


@pytest.mark.contract
@pytest.mark.django_db
def test_settings_patch_admin_only(workspace, admin_client):
    member = _ws_member(workspace, 15)
    res = _client(member).patch(_settings_url(workspace), {"invite_expiry_days": 3}, format="json")
    assert res.status_code == 403
    assert not WorkspaceInviteSettings.objects.exists()

    res = admin_client.patch(
        _settings_url(workspace),
        {"invite_expiry_days": 3, "allowed_email_domains": ["@GroundFloor.co", "groundfloor.co"], "project_admins_can_invite": True},
        format="json",
    )
    assert res.status_code == 200
    assert res.data == {
        "invite_expiry_days": 3,
        "allowed_email_domains": ["groundfloor.co"],
        "project_admins_can_invite": True,
    }
    # a subset leaves the rest alone
    res = admin_client.patch(_settings_url(workspace), {"invite_expiry_days": 10}, format="json")
    assert res.data["allowed_email_domains"] == ["groundfloor.co"] and res.data["invite_expiry_days"] == 10
    assert WorkspaceInviteSettings.objects.filter(workspace=workspace).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("days", [0, 91, -1, "7", 7.5, True, None])
def test_settings_patch_rejects_bad_days(workspace, admin_client, days):
    res = admin_client.patch(_settings_url(workspace), {"invite_expiry_days": days}, format="json")
    assert res.status_code == 400 and "invite_expiry_days" in res.data
    assert not WorkspaceInviteSettings.objects.exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_settings_patch_rejects_bad_domains_and_flag(workspace, admin_client):
    res = admin_client.patch(_settings_url(workspace), {"allowed_email_domains": ["not a domain"]}, format="json")
    assert res.status_code == 400 and res.data["allowed_email_domains"]
    res = admin_client.patch(_settings_url(workspace), {"allowed_email_domains": "a.com"}, format="json")
    assert res.status_code == 400 and res.data["allowed_email_domains"]
    res = admin_client.patch(_settings_url(workspace), {"project_admins_can_invite": "yes"}, format="json")
    assert res.status_code == 400 and res.data["project_admins_can_invite"]
    assert not WorkspaceInviteSettings.objects.exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_settings_change_is_audited_with_diff_only(workspace, admin_client):
    admin_client.patch(_settings_url(workspace), {"invite_expiry_days": 3, "project_admins_can_invite": True}, format="json")
    entries = _audit(workspace, "workspace.invite_settings_changed")
    assert len(entries) == 1
    assert entries[0].target_type == "workspace"
    assert entries[0].before == {"invite_expiry_days": 7, "project_admins_can_invite": False}
    assert entries[0].after == {"invite_expiry_days": 3, "project_admins_can_invite": True}
    # saving the same values again changes nothing -> no entry
    admin_client.patch(_settings_url(workspace), {"invite_expiry_days": 3}, format="json")
    assert len(_audit(workspace, "workspace.invite_settings_changed")) == 1


# --- expiry on the accept paths ----------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_join_endpoint_refuses_expired_but_allows_decline(workspace):
    user = UserFactory(email="new@example.com")
    invite = _invite_row(workspace, age_days=8)
    url = f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/"

    res = _client(user).post(url, {"token": invite.token, "accepted": True}, format="json")
    assert res.status_code == 400
    assert res.data == {"error": EXPIRED_MSG, "error_code": "INVITATION_EXPIRED"}
    invite.refresh_from_db()
    assert invite.responded_at is None and not invite.accepted
    assert not WorkspaceMember.objects.filter(workspace=workspace, member=user).exists()

    res = _client(user).post(url, {"token": invite.token, "accepted": False}, format="json")
    assert res.status_code == 200
    invite.refresh_from_db()
    assert invite.responded_at is not None and not invite.accepted


@pytest.mark.contract
@pytest.mark.django_db
def test_join_endpoint_accepts_inside_window_and_honours_custom_days(workspace):
    _set(workspace, invite_expiry_days=14)
    user = UserFactory(email="new@example.com")
    invite = _invite_row(workspace, age_days=10)  # expired under the 7-day default, fine under 14
    res = _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": invite.token, "accepted": True},
        format="json",
    )
    assert res.status_code == 200
    assert WorkspaceMember.objects.filter(workspace=workspace, member=user, is_active=True).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_user_invitations_list_skips_expired(workspace):
    user = UserFactory(email="new@example.com")
    expired = _invite_row(workspace, age_days=8)
    # all requested are expired -> 400, nothing joined, invite kept
    res = _client(user).post("/api/users/me/workspaces/invitations/", {"invitations": [str(expired.id)]}, format="json")
    assert res.status_code == 400 and res.data["error_code"] == "INVITATION_EXPIRED"
    assert not WorkspaceMember.objects.filter(workspace=workspace, member=user).exists()
    assert WorkspaceMemberInvite.objects.filter(pk=expired.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_user_invitations_mixed_joins_fresh_and_keeps_expired(workspace, create_user):
    from plane.db.models import Workspace

    other = Workspace.objects.create(name="Other", owner=create_user, slug="other-ws")
    user = UserFactory(email="new@example.com")
    expired = _invite_row(workspace, age_days=8)
    fresh = _invite_row(other, age_days=1)
    res = _client(user).post(
        "/api/users/me/workspaces/invitations/", {"invitations": [str(expired.id), str(fresh.id)]}, format="json"
    )
    assert res.status_code == 204
    assert WorkspaceMember.objects.filter(workspace=other, member=user).exists()
    assert not WorkspaceMember.objects.filter(workspace=workspace, member=user).exists()
    assert WorkspaceMemberInvite.objects.filter(pk=expired.pk).exists()
    assert not WorkspaceMemberInvite.objects.filter(pk=fresh.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_signup_disabled_expired_invite_does_not_unlock_signup(workspace):
    adapter = Adapter.__new__(Adapter)
    import logging

    adapter.logger = logging.getLogger("test")
    check = adapter._Adapter__check_signup
    with patch("plane.authentication.adapter.base.get_configuration_value", return_value=("0",)):
        _invite_row(workspace, "exp@example.com", age_days=8)
        with pytest.raises(AuthenticationException):
            check("exp@example.com")
        _invite_row(workspace, "ok@example.com", age_days=1)
        assert check("ok@example.com") is True
        with pytest.raises(AuthenticationException):
            check("nobody@example.com")


# --- serializers -------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_invitations_list_has_expiry_fields(workspace, admin_client):
    _invite_row(workspace, "fresh@example.com", age_days=1)
    _invite_row(workspace, "old@example.com", age_days=9)
    with_queries = admin_client.get(f"/api/workspaces/{workspace.slug}/invitations/")
    assert with_queries.status_code == 200
    by_email = {i["email"]: i for i in with_queries.data}
    assert by_email["fresh@example.com"]["is_expired"] is False
    assert by_email["old@example.com"]["is_expired"] is True
    assert by_email["old@example.com"]["expires_at"]
    assert "token" in by_email["old@example.com"]  # admin serializer unchanged


@pytest.mark.contract
@pytest.mark.django_db
def test_public_invite_get_has_expiry_fields_and_no_token(workspace):
    invite = _invite_row(workspace, age_days=9)
    res = APIClient().get(f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/")
    assert res.status_code == 200
    assert res.data["is_expired"] is True and res.data["expires_at"]
    assert "token" not in res.data


# --- resend ------------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_resend_renews_window_and_audits(workspace, admin_client, mocked_tasks):
    invite = _invite_row(workspace, age_days=9, expiry_recorded_at=timezone.now())
    token = invite.token
    res = admin_client.post(f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/resend/")
    assert res.status_code == 200
    invite.refresh_from_db()
    assert invite.token == token
    assert invite.expiry_recorded_at is None
    assert not is_invite_expired(invite, 7)
    mocked_tasks["resend"].assert_called_once()
    entries = _audit(workspace, "member.invite_resent")
    assert len(entries) == 1 and entries[0].target_id == invite.id
    assert entries[0].after == {"email": invite.email, "role": 15, "role_name": "Member"}


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_resend_permissions_cooldown_and_state(workspace, admin_client):
    invite = _invite_row(workspace)
    url = f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/resend/"
    assert _client(_ws_member(workspace, 15)).post(url).status_code == 403
    assert admin_client.post(url).status_code == 200
    res = admin_client.post(url)
    assert res.status_code == 429
    assert res.data == {"error": "Please wait a minute before resending this invitation."}
    declined = _invite_row(workspace, "d@example.com", responded_at=timezone.now())
    assert admin_client.post(f"/api/workspaces/{workspace.slug}/invitations/{declined.id}/resend/").status_code == 400
    assert admin_client.post(f"/api/workspaces/{workspace.slug}/invitations/{invite.id.__class__(int=1)}/resend/").status_code == 404


@pytest.mark.contract
@pytest.mark.django_db
def test_project_resend_renews_window_and_audits_with_project(workspace, project, admin_client):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    WorkspaceMemberInvite.objects.update(last_sent_at=timezone.now() - timedelta(days=9), expiry_recorded_at=timezone.now())
    cache.clear()
    res = admin_client.post(
        f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/{spot.id}/resend/"
    )
    assert res.status_code == 200
    invite = WorkspaceMemberInvite.objects.get()
    assert invite.expiry_recorded_at is None and not is_invite_expired(invite, 7)
    entry = _audit(workspace, "member.invite_resent")[0]
    assert entry.after["project_id"] == str(project.id) and entry.after["project_name"] == "Alpha"
    assert entry.after["project_role"] == 15


@pytest.mark.contract
@pytest.mark.django_db
def test_project_resend_po4_project_admin_needs_setting_and_domain(workspace, project, admin_client, project_admin):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@groundfloor.co", "role": 15}])
    spot = WorkspaceMemberInviteProject.objects.get()
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/{spot.id}/resend/"

    res = _client(project_admin).post(url)  # setting off
    assert res.status_code == 403 and res.data["detail"] == "workspace_admin_only"

    _set(workspace, project_admins_can_invite=True, allowed_email_domains=["other.com"])
    res = _client(project_admin).post(url)  # domain no longer allowed
    assert res.status_code == 403 and res.data["detail"] == "domain_not_allowed"

    _set(workspace, allowed_email_domains=["groundfloor.co"])
    assert _client(project_admin).post(url).status_code == 200
    # invite link stays workspace-Admin only
    listing = _client(project_admin).get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/")
    assert "invite_link" not in listing.data[0]


# --- project flow: domains, setting, PO-1 ------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_project_admin_setting_off_is_workspace_admin_only(workspace, project, project_admin):
    res = _post_project_invite(_client(project_admin), workspace, project, [{"email": "n@example.com", "role": 15}])
    assert res.data["results"] == [
        {"email": "n@example.com", "status": "not_allowed", "detail": "workspace_admin_only"}
    ]
    assert not WorkspaceMemberInvite.objects.exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_project_admin_setting_on_domain_rules_and_member_role(workspace, project, project_admin, mocked_tasks):
    _set(workspace, project_admins_can_invite=True, allowed_email_domains=["groundfloor.co"])
    res = _post_project_invite(
        _client(project_admin),
        workspace,
        project,
        [{"email": "ok@groundfloor.co", "role": 15}, {"email": "no@evil.com", "role": 15}, {"email": "sub@x.groundfloor.co", "role": 15}],
        workspace_role=5,  # ignored for a non-Admin (PO-1)
    )
    statuses = {r["email"]: r for r in res.data["results"]}
    assert statuses["ok@groundfloor.co"]["status"] == "invited"
    assert statuses["no@evil.com"] == {"email": "no@evil.com", "status": "not_allowed", "detail": "domain_not_allowed"}
    assert statuses["sub@x.groundfloor.co"]["detail"] == "domain_not_allowed"
    assert WorkspaceMemberInvite.objects.get().role == 15
    assert mocked_tasks["project"].call_count == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_project_admin_setting_on_no_domain_list_allows_any(workspace, project, project_admin):
    _set(workspace, project_admins_can_invite=True)
    res = _post_project_invite(_client(project_admin), workspace, project, [{"email": "any@anywhere.org", "role": 15}])
    assert res.data["results"][0]["status"] == "invited"


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_admin_bypasses_domain_list_and_setting(workspace, project, admin_client):
    _set(workspace, project_admins_can_invite=False, allowed_email_domains=["groundfloor.co"])
    res = _post_project_invite(admin_client, workspace, project, [{"email": "x@evil.com", "role": 15}], workspace_role=5)
    assert res.data["results"][0]["status"] == "invited"
    assert WorkspaceMemberInvite.objects.get().role == 5  # an Admin still chooses the workspace role


@pytest.mark.contract
@pytest.mark.django_db
def test_existing_workspace_member_is_added_regardless_of_domain(workspace, project, project_admin):
    _set(workspace, allowed_email_domains=["groundfloor.co"])
    member = _ws_member(workspace, 15)
    res = _post_project_invite(_client(project_admin), workspace, project, [{"email": member.email, "role": 15}])
    assert res.data["results"][0]["status"] == "added"


@pytest.mark.contract
@pytest.mark.django_db
def test_reinviting_expired_invite_renews_and_resends(workspace, project, admin_client, mocked_tasks):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 15}], message="hello")
    invite = WorkspaceMemberInvite.objects.get()
    WorkspaceMemberInvite.objects.update(last_sent_at=timezone.now() - timedelta(days=9), expiry_recorded_at=timezone.now())
    mocked_tasks["project"].reset_mock()

    res = _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 20}], message="again")
    assert res.data["results"][0]["status"] == "invited"  # not invite_updated
    invite.refresh_from_db()
    assert invite.expiry_recorded_at is None and not is_invite_expired(invite, 7)
    mocked_tasks["project"].assert_called_once()
    assert mocked_tasks["project"].call_args.args[-1] is None  # the note only goes in the first email
    assert len(_audit(workspace, "member.invite_resent")) == 1
    assert len(_audit(workspace, "member.invited")) == 1  # only the original invite


@pytest.mark.contract
@pytest.mark.django_db
def test_reinviting_fresh_pending_invite_is_invite_updated_without_email(workspace, project, admin_client, mocked_tasks):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 15}])
    mocked_tasks["project"].reset_mock()
    # a second project spot on the same invitation
    p2 = Project.objects.create(workspace=workspace, name="Beta", identifier="BET")
    ProjectIdentifier.objects.create(workspace=workspace, project=p2, name="BET")
    ProjectMember.objects.create(project=p2, member=workspace.owner, role=20, workspace=workspace)
    res = _post_project_invite(admin_client, workspace, p2, [{"email": "n@example.com", "role": 15}])
    assert res.data["results"][0]["status"] == "invite_updated"
    mocked_tasks["project"].assert_not_called()
    entries = _audit(workspace, "member.invited")
    assert len(entries) == 2
    assert entries[1].after["project_id"] == str(p2.id) and entries[1].after["project_name"] == "Beta"
    # re-adding the same project adds no spot and no entry
    _post_project_invite(admin_client, workspace, p2, [{"email": "n@example.com", "role": 15}])
    assert len(_audit(workspace, "member.invited")) == 2


@pytest.mark.contract
@pytest.mark.django_db
def test_pending_members_payload_has_expiry(workspace, project, admin_client):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 15}])
    res = admin_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/")
    assert res.data[0]["is_expired"] is False and res.data[0]["expires_at"]
    WorkspaceMemberInvite.objects.update(last_sent_at=timezone.now() - timedelta(days=9))
    res = admin_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/")
    assert res.data[0]["is_expired"] is True


# --- audit log: every action, once, no secrets -------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_project_invite_and_cancel(workspace, project, admin_client):
    _post_project_invite(admin_client, workspace, project, [{"email": "n@example.com", "role": 15}], message="secret note")
    spot = WorkspaceMemberInviteProject.objects.get()
    invite = WorkspaceMemberInvite.objects.get()
    invited = _audit(workspace, "member.invited")
    assert len(invited) == 1
    e = invited[0]
    assert e.target_type == "invite" and e.target_label == "n@example.com" and e.target_id == invite.id
    assert e.after == {
        "email": "n@example.com", "role": 15, "role_name": "Member",
        "project_id": str(project.id), "project_name": "Alpha", "project_role": 15,
    }
    assert e.actor_email == workspace.owner.email

    res = admin_client.delete(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/{spot.id}/")
    assert res.status_code == 204
    cancelled = _audit(workspace, "member.invite_cancelled")
    assert len(cancelled) == 1 and cancelled[0].after["project_id"] == str(project.id)

    # nothing in any entry carries the token or the note
    blob = " ".join(str(x.after) + str(x.before) for x in WorkspaceAuditLog.objects.filter(workspace=workspace))
    assert invite.token not in blob and "secret note" not in blob


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_project_spot_cancel_keeps_invitation_with_project_fields(workspace, project, admin_client):
    invite = _invite_row(workspace, "n@example.com")
    WorkspaceMemberInviteProject.objects.create(invite=invite, project=project, workspace=workspace, role=15)
    spot = WorkspaceMemberInviteProject.objects.get()
    admin_client.delete(f"/api/workspaces/{workspace.slug}/projects/{project.id}/pending-members/{spot.id}/")
    assert WorkspaceMemberInvite.objects.filter(pk=invite.pk).exists()  # workspace-level invite survives
    entries = _audit(workspace, "member.invite_cancelled")
    assert len(entries) == 1 and entries[0].after["project_name"] == "Alpha"


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_workspace_page_invite_and_cancel(workspace, admin_client, mocked_tasks):
    res = admin_client.post(
        f"/api/workspaces/{workspace.slug}/invitations/",
        {"emails": [{"email": "m@example.com", "role": 15}, {"email": "a@example.com", "role": 20}]},
        format="json",
    )
    assert res.status_code == 200
    assert [e.target_label for e in _audit(workspace, "member.invited")] == ["m@example.com"]
    assert [e.target_label for e in _audit(workspace, "member.admin_invited")] == ["a@example.com"]  # unchanged

    invite = WorkspaceMemberInvite.objects.get(email="m@example.com")
    assert admin_client.delete(f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/").status_code == 204
    cancelled = _audit(workspace, "member.invite_cancelled")
    assert len(cancelled) == 1 and cancelled[0].after == {"email": "m@example.com", "role": 15, "role_name": "Member"}


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_accept_and_decline(workspace):
    user = UserFactory(email="acc@example.com")
    invite = _invite_row(workspace, "acc@example.com")
    _client(user).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.id}/join/",
        {"token": invite.token, "accepted": True},
        format="json",
    )
    accepted = _audit(workspace, "member.invite_accepted")
    assert len(accepted) == 1 and accepted[0].actor_email == "acc@example.com"

    user2 = UserFactory(email="dec@example.com")
    invite2 = _invite_row(workspace, "dec@example.com")
    _client(user2).post(
        f"/api/workspaces/{workspace.slug}/invitations/{invite2.id}/join/",
        {"token": invite2.token, "accepted": False},
        format="json",
    )
    declined = _audit(workspace, "member.invite_declined")
    assert len(declined) == 1 and declined[0].actor_email == "dec@example.com"
    assert len(_audit(workspace, "member.invite_accepted")) == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_accept_from_invitations_list(workspace):
    user = UserFactory(email="acc@example.com")
    invite = _invite_row(workspace, "acc@example.com")
    res = _client(user).post("/api/users/me/workspaces/invitations/", {"invitations": [str(invite.id)]}, format="json")
    assert res.status_code == 204
    assert len(_audit(workspace, "member.invite_accepted")) == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_rolls_back_with_the_change(workspace, admin_client):
    """The entry is written in the same transaction: if it fails, no invite is left behind."""
    with patch("plane.utils.workspace_audit.record", side_effect=RuntimeError("boom")):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=workspace.owner)
        res = client.post(
            f"/api/workspaces/{workspace.slug}/invitations/",
            {"emails": [{"email": "m@example.com", "role": 15}]},
            format="json",
        )
    assert res.status_code == 500
    assert not WorkspaceMemberInvite.objects.exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_audit_public_api_v1_invite_and_cancel(workspace, create_user):
    from plane.db.models import APIToken

    token = APIToken.objects.create(user=create_user, workspace=workspace)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    res = client.post(f"/api/v1/workspaces/{workspace.slug}/invitations/", {"email": "v1@example.com", "role": 15}, format="json")
    assert res.status_code == 201, res.data
    assert len(_audit(workspace, "member.invited")) == 1
    invite = WorkspaceMemberInvite.objects.get(email="v1@example.com")
    assert client.delete(f"/api/v1/workspaces/{workspace.slug}/invitations/{invite.id}/").status_code == 204
    assert len(_audit(workspace, "member.invite_cancelled")) == 1


# --- expiry sweep ------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_sweep_logs_each_expiry_once_per_send(workspace):
    expired = _invite_row(workspace, "old@example.com", age_days=9)
    fresh = _invite_row(workspace, "new@example.com", age_days=1)
    declined = _invite_row(workspace, "dec@example.com", age_days=9, responded_at=timezone.now())

    assert record_expired_invitations() == 1
    assert record_expired_invitations() == 0  # idempotent
    entries = _audit(workspace, "member.invite_expired")
    assert len(entries) == 1
    e = entries[0]
    assert e.target_id == expired.id and e.target_label == "old@example.com"
    assert e.actor_id is None and e.actor_email == ""
    assert e.after["email"] == "old@example.com" and e.after["expired_at"]
    for inv in (expired, fresh, declined):
        assert WorkspaceMemberInvite.objects.filter(pk=inv.pk).exists()  # never deletes

    # resend, then expire again -> a second entry
    WorkspaceMemberInvite.objects.filter(pk=expired.pk).update(last_sent_at=timezone.now(), expiry_recorded_at=None)
    assert record_expired_invitations() == 0
    WorkspaceMemberInvite.objects.filter(pk=expired.pk).update(last_sent_at=timezone.now() - timedelta(days=8))
    assert record_expired_invitations() == 1
    assert len(_audit(workspace, "member.invite_expired")) == 2


@pytest.mark.contract
@pytest.mark.django_db
def test_sweep_uses_the_workspace_window_and_skips_soft_deleted(workspace):
    _set(workspace, invite_expiry_days=30)
    _invite_row(workspace, "a@example.com", age_days=20)  # inside the 30-day window
    gone = _invite_row(workspace, "b@example.com", age_days=40)
    gone.delete()
    assert record_expired_invitations() == 0
    _set(workspace, invite_expiry_days=10)
    assert record_expired_invitations() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_sweep_is_scheduled_hourly():
    from plane.celery import app

    entry = app.conf.beat_schedule["check-every-hour-to-record-expired-invitations"]
    assert entry["task"] == "plane.bgtasks.workspace_invite_expiry_task.record_expired_invitations"


# --- migration data step / email ---------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_email_template_shows_expiry_line():
    from django.template.loader import render_to_string

    html = render_to_string(
        "emails/invitations/workspace_invitation.html",
        {"email": "a@b.com", "first_name": "Ann", "workspace_name": "WS", "abs_url": "http://x/y",
         "expiry_days": 7, "expires_on": "November 26, 2026"},
    )
    assert "This invitation expires on November 26, 2026 (7 days)." in html


@pytest.mark.contract
@pytest.mark.django_db
def test_invitation_task_passes_expiry_context(workspace, create_user):
    from plane.bgtasks.workspace_invitation_task import workspace_invitation

    _set(workspace, invite_expiry_days=10)
    invite = _invite_row(workspace, "n@example.com")
    with patch("plane.bgtasks.workspace_invitation_task.render_to_string", return_value="<p>x</p>") as render, patch(
        "plane.bgtasks.workspace_invitation_task.get_email_configuration",
        return_value=("h", "u", "p", "25", "0", "0", "f@example.com"),
    ), patch("plane.bgtasks.workspace_invitation_task.get_connection"), patch(
        "plane.bgtasks.workspace_invitation_task.EmailMultiAlternatives"
    ):
        workspace_invitation("n@example.com", workspace.id, invite.token, "http://h", create_user.email)
    ctx = render.call_args.args[1]
    assert ctx["expiry_days"] == 10
    assert ctx["expires_on"] == invite_expires_at(invite, 10).strftime("%B %d, %Y")
