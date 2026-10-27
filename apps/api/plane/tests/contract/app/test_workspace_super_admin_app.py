# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- workspace Super Admin (slice 1).

A Super Admin is an active Admin (role 20) with ``WorkspaceMember.is_super_admin``. These tests
cover the flag's DB invariant, the migration backfill, and every server-side gate that slice 1
moves from "Admin" to "Super Admin": admin role changes and removals, Admin invitations, workspace
name/slug/logo, delete, webhooks, time-tracking settings, ownership transfer, and the guard that
keeps at least one Super Admin per workspace.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Profile,
    Webhook,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberInvite,
)
from plane.tests.factories import UserFactory

SUPER_ADMIN_ERROR = "Only a Super Admin of this workspace can do this."


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _add_member(workspace, *, role, super_admin=False, is_active=True):
    return WorkspaceMember.objects.create(
        workspace=workspace,
        member=UserFactory(),
        role=role,
        is_active=is_active,
        is_super_admin=super_admin,
    )


@pytest.fixture
def plain_admin(db, workspace):
    """An Admin of ``workspace`` without the Super Admin flag (``create_user`` is the Super Admin)."""
    return _add_member(workspace, role=20)


@pytest.fixture
def plain_admin_client(plain_admin):
    return _client(plain_admin.member)


def _member_url(workspace, member):
    return f"/api/workspaces/{workspace.slug}/members/{member.pk}/"


# --- model invariant ------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("role,is_active", [(15, True), (5, True), (20, False)])
def test_flag_only_allowed_on_active_admin(workspace, role, is_active):
    with pytest.raises(IntegrityError), transaction.atomic():
        _add_member(workspace, role=role, is_active=is_active, super_admin=True)


# --- migration backfill ---------------------------------------------------------------------


def _backfill():
    module = importlib.import_module("plane.db.migrations.0136_workspace_member_super_admin")
    module.backfill_super_admins(django_apps, None)


@pytest.mark.contract
@pytest.mark.django_db
def test_backfill_flags_owner_only(workspace, create_user):
    other_admin = _add_member(workspace, role=20)
    WorkspaceMember.objects.filter(workspace=workspace).update(is_super_admin=False)

    _backfill()

    assert WorkspaceMember.objects.get(workspace=workspace, member=create_user).is_super_admin
    other_admin.refresh_from_db()
    assert not other_admin.is_super_admin


@pytest.mark.contract
@pytest.mark.django_db
def test_backfill_falls_back_to_earliest_human_admin(workspace, create_user):
    owner_row = WorkspaceMember.objects.get(workspace=workspace, member=create_user)
    WorkspaceMember.objects.filter(pk=owner_row.pk).update(is_super_admin=False, role=15)
    bot = UserFactory(is_bot=True)
    WorkspaceMember.objects.create(workspace=workspace, member=bot, role=20)
    first = _add_member(workspace, role=20)
    second = _add_member(workspace, role=20)
    # The bot row is the oldest admin but must be skipped.
    WorkspaceMember.objects.filter(member=bot).update(created_at=first.created_at.replace(year=2000))

    _backfill()

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_super_admin
    assert not second.is_super_admin
    assert not WorkspaceMember.objects.get(member=bot).is_super_admin


# --- member role changes --------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_can_still_manage_members_and_guests(workspace, plain_admin_client):
    member = _add_member(workspace, role=15)
    response = plain_admin_client.patch(_member_url(workspace, member), {"role": 5}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    member.refresh_from_db()
    assert member.role == 5


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_promote_to_admin(workspace, plain_admin_client):
    member = _add_member(workspace, role=15)
    response = plain_admin_client.patch(_member_url(workspace, member), {"role": 20}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["error"] == SUPER_ADMIN_ERROR
    member.refresh_from_db()
    assert member.role == 15


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_demote_admin(workspace, plain_admin_client):
    other_admin = _add_member(workspace, role=20)
    response = plain_admin_client.patch(_member_url(workspace, other_admin), {"role": 15}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    other_admin.refresh_from_db()
    assert other_admin.role == 20


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_grant_super_admin(workspace, plain_admin_client):
    member = _add_member(workspace, role=15)
    response = plain_admin_client.patch(_member_url(workspace, member), {"is_super_admin": True}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    member.refresh_from_db()
    assert (member.role, member.is_super_admin) == (15, False)


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_promotes_member_to_admin(workspace, session_client):
    member = _add_member(workspace, role=15)
    response = session_client.patch(_member_url(workspace, member), {"role": 20}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    member.refresh_from_db()
    assert (member.role, member.is_super_admin) == (20, False)


@pytest.mark.contract
@pytest.mark.django_db
def test_granting_super_admin_implies_admin(workspace, session_client):
    member = _add_member(workspace, role=15)
    response = session_client.patch(_member_url(workspace, member), {"is_super_admin": True}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["is_super_admin"] is True
    member.refresh_from_db()
    assert (member.role, member.is_super_admin) == (20, True)


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_flag_with_non_admin_role_is_rejected(workspace, session_client):
    member = _add_member(workspace, role=15)
    response = session_client.patch(
        _member_url(workspace, member), {"role": 15, "is_super_admin": True}, format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
def test_demoting_super_admin_clears_flag(workspace, session_client):
    other = _add_member(workspace, role=20, super_admin=True)
    response = session_client.patch(_member_url(workspace, other), {"role": 15}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data
    other.refresh_from_db()
    assert (other.role, other.is_super_admin) == (15, False)


@pytest.mark.contract
@pytest.mark.django_db
def test_other_fields_still_rejected(workspace, session_client):
    member = _add_member(workspace, role=15)
    response = session_client.patch(_member_url(workspace, member), {"role": 5, "is_active": False}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- removal, leave, deactivate -------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_remove_admin(workspace, plain_admin_client):
    other_admin = _add_member(workspace, role=20)
    response = plain_admin_client.delete(_member_url(workspace, other_admin))
    assert response.status_code == status.HTTP_403_FORBIDDEN
    other_admin.refresh_from_db()
    assert other_admin.is_active


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_can_remove_member(workspace, plain_admin_client):
    member = _add_member(workspace, role=15)
    response = plain_admin_client.delete(_member_url(workspace, member))
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_removes_super_admin_and_flag_is_cleared(workspace, session_client):
    other = _add_member(workspace, role=20, super_admin=True)
    response = session_client.delete(_member_url(workspace, other))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    other.refresh_from_db()
    assert (other.is_active, other.is_super_admin) == (False, False)


@pytest.mark.contract
@pytest.mark.django_db
def test_last_super_admin_cannot_leave(workspace, session_client, plain_admin):
    response = session_client.post(f"/api/workspaces/{workspace.slug}/members/leave/")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "only Super Admin" in response.data["error"]


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_can_leave_when_another_remains(workspace, session_client, create_user):
    _add_member(workspace, role=20, super_admin=True)
    response = session_client.post(f"/api/workspaces/{workspace.slug}/members/leave/")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    row = WorkspaceMember.objects.get(workspace=workspace, member=create_user)
    assert (row.is_active, row.is_super_admin) == (False, False)


@pytest.mark.contract
@pytest.mark.django_db
def test_last_super_admin_cannot_deactivate_account(workspace, session_client, create_user, plain_admin):
    response = session_client.delete("/api/users/me/")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "only Super Admin" in response.data["error"]
    create_user.refresh_from_db()
    assert create_user.is_active


@pytest.mark.contract
@pytest.mark.django_db
def test_deactivate_clears_flag_when_another_super_admin_remains(workspace, session_client, create_user):
    _add_member(workspace, role=20, super_admin=True)
    Profile.objects.get_or_create(user=create_user)
    response = session_client.delete("/api/users/me/")
    assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
    row = WorkspaceMember.objects.get(workspace=workspace, member=create_user)
    assert (row.is_active, row.is_super_admin) == (False, False)


# --- invitations ----------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_invite_admin(workspace, plain_admin_client):
    response = plain_admin_client.post(
        f"/api/workspaces/{workspace.slug}/invitations/",
        {"emails": [{"email": "new-admin@example.com", "role": 20}]},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not WorkspaceMemberInvite.objects.filter(email="new-admin@example.com").exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_can_invite_member(workspace, plain_admin_client):
    response = plain_admin_client.post(
        f"/api/workspaces/{workspace.slug}/invitations/",
        {"emails": [{"email": "new-member@example.com", "role": 15}]},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_can_invite_admin(workspace, session_client):
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/invitations/",
        {"emails": [{"email": "new-admin@example.com", "role": 20}]},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("from_role,to_role", [(15, 20), (20, 15)])
def test_plain_admin_cannot_change_admin_invitation(workspace, plain_admin_client, from_role, to_role):
    invite = WorkspaceMemberInvite.objects.create(workspace=workspace, email="x@example.com", role=from_role)
    response = plain_admin_client.patch(
        f"/api/workspaces/{workspace.slug}/invitations/{invite.pk}/", {"role": to_role}, format="json"
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    invite.refresh_from_db()
    assert invite.role == from_role


@pytest.mark.contract
@pytest.mark.django_db
def test_v1_api_plain_admin_cannot_invite_admin(workspace, plain_admin):
    token = APIToken.objects.create(user=plain_admin.member, label="t")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    response = client.post(
        f"/api/v1/workspaces/{workspace.slug}/invitations/",
        {"email": "v1-admin@example.com", "role": 20},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert not WorkspaceMemberInvite.objects.filter(email="v1-admin@example.com").exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_stale_invite_does_not_demote_super_admin(workspace, create_user):
    other = _add_member(workspace, role=20, super_admin=True)
    invite = WorkspaceMemberInvite.objects.create(workspace=workspace, email=other.member.email, role=15)
    response = _client(other.member).post(
        "/api/users/me/workspaces/invitations/", {"invitations": [str(invite.pk)]}, format="json"
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    other.refresh_from_db()
    assert (other.role, other.is_super_admin) == (20, True)


# --- workspace settings ---------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{"name": "Renamed"}, {"slug": "renamed-slug"}, {"logo": "x"}])
def test_plain_admin_cannot_change_identity_fields(workspace, plain_admin_client, payload):
    response = plain_admin_client.patch(f"/api/workspaces/{workspace.slug}/", payload, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    workspace.refresh_from_db()
    assert workspace.name == "Test Workspace"


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_can_change_other_workspace_fields(workspace, plain_admin_client):
    response = plain_admin_client.patch(
        f"/api/workspaces/{workspace.slug}/", {"organization_size": "2-10"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
def test_super_admin_can_rename(workspace, session_client):
    response = session_client.patch(f"/api/workspaces/{workspace.slug}/", {"name": "Renamed"}, format="json")
    assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_delete_workspace(workspace, plain_admin_client):
    response = plain_admin_client.delete(f"/api/workspaces/{workspace.slug}/")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Workspace.objects.filter(pk=workspace.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_upload_workspace_logo(workspace, plain_admin_client):
    response = plain_admin_client.post(
        f"/api/assets/v2/workspaces/{workspace.slug}/",
        {"name": "logo.png", "type": "image/png", "size": 1024, "entity_type": "WORKSPACE_LOGO"},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_webhooks_read_only(workspace, plain_admin_client):
    hook = Webhook.objects.create(workspace=workspace, url="https://example.com/hook")
    base = f"/api/workspaces/{workspace.slug}/webhooks/"
    assert plain_admin_client.get(base).status_code == status.HTTP_200_OK
    assert (
        plain_admin_client.post(base, {"url": "https://example.com/other"}, format="json").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        plain_admin_client.patch(f"{base}{hook.pk}/", {"is_active": False}, format="json").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert plain_admin_client.delete(f"{base}{hook.pk}/").status_code == status.HTTP_403_FORBIDDEN
    assert plain_admin_client.post(f"{base}{hook.pk}/regenerate/").status_code == status.HTTP_403_FORBIDDEN
    assert Webhook.objects.filter(pk=hook.pk, is_active=True).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_time_settings_read_only(workspace, plain_admin_client):
    url = f"/api/workspaces/{workspace.slug}/time/settings/"
    assert plain_admin_client.get(url).status_code == status.HTTP_200_OK
    response = plain_admin_client.patch(url, {"minutes_per_day": 420}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN


# --- ownership transfer ---------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_transfer_ownership_to_super_admin(workspace, session_client):
    other = _add_member(workspace, role=20, super_admin=True)
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/transfer-ownership/", {"member_id": str(other.member_id)}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    workspace.refresh_from_db()
    assert workspace.owner_id == other.member_id


@pytest.mark.contract
@pytest.mark.django_db
def test_transfer_ownership_requires_super_admin_target(workspace, session_client, plain_admin, create_user):
    response = session_client.post(
        f"/api/workspaces/{workspace.slug}/transfer-ownership/",
        {"member_id": str(plain_admin.member_id)},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    workspace.refresh_from_db()
    assert workspace.owner_id == create_user.id


@pytest.mark.contract
@pytest.mark.django_db
def test_plain_admin_cannot_transfer_ownership(workspace, plain_admin_client, plain_admin, create_user):
    response = plain_admin_client.post(
        f"/api/workspaces/{workspace.slug}/transfer-ownership/",
        {"member_id": str(plain_admin.member_id)},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    workspace.refresh_from_db()
    assert workspace.owner_id == create_user.id


# --- creation -------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_workspace_creator_is_super_admin(session_client, create_user, instance_admin):
    response = session_client.post("/api/workspaces/", {"name": "Fresh", "slug": "fresh-ws"}, format="json")
    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["is_super_admin"] is True
    assert WorkspaceMember.objects.get(workspace__slug="fresh-ws", member=create_user).is_super_admin

    listing = session_client.get("/api/users/me/workspaces/")
    row = next(w for w in listing.data if w["slug"] == "fresh-ws")
    assert row["is_super_admin"] is True
