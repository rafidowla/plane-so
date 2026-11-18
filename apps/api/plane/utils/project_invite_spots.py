# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- helpers for adding people to a project by email.

A pending project spot (``WorkspaceMemberInviteProject``) hangs off a workspace invitation and is
turned into a ``ProjectMember`` when the invitation is accepted. Soft delete note: queryset
``.delete()`` is a plain UPDATE (no cascade) and instance ``.delete()`` cascades asynchronously, so
spots are always removed explicitly here, in the same transaction as the invitation.
"""

from django.db import models

from plane.db.models import (
    ProjectMember,
    ProjectUserProperty,
    WorkspaceMemberInvite,
    WorkspaceMemberInviteProject,
)
from plane.utils.cache import invalidate_cache_directly

GUEST = 5
MEMBER = 15
ADMIN = 20


def cap_project_role(project_role, workspace_role):
    """A workspace Admin is always a project Admin; a workspace Guest can only be a project Guest."""
    if workspace_role == ADMIN:
        return ADMIN
    if workspace_role == GUEST:
        return GUEST
    return project_role


def _ensure_user_property(project, user):
    """Create the ``ProjectUserProperty`` row if missing (mirrors the project members endpoint)."""
    min_sort_order = ProjectUserProperty.objects.filter(workspace_id=project.workspace_id, user=user).aggregate(
        m=models.Min("sort_order")
    )["m"]
    ProjectUserProperty.objects.bulk_create(
        [
            ProjectUserProperty(
                user=user,
                project=project,
                workspace_id=project.workspace_id,
                sort_order=(min_sort_order - 10000 if min_sort_order is not None else 65535),
            )
        ],
        ignore_conflicts=True,
    )


def upsert_project_member(project, user, role, created_by_id=None):
    """Create or reactivate the ``ProjectMember`` row (no duplicate for an inactive member).

    An already ACTIVE member is returned untouched: their role is never overwritten here.
    """
    member = ProjectMember.objects.filter(project=project, member=user).first()
    if member is not None:
        if member.is_active:
            return member
        member.role = role
        member.is_active = True
        member.save()
        _ensure_user_property(project, user)
        return member

    ProjectMember.objects.bulk_create(
        [ProjectMember(member=user, role=role, project=project, workspace_id=project.workspace_id)],
        ignore_conflicts=True,
    )
    _ensure_user_property(project, user)
    return ProjectMember.objects.get(project=project, member=user)


def accept_project_spots(invite, user, workspace_role):
    """Turn the invitation's pending spots into project memberships, then remove the spots.

    Returns the ids of the projects joined (oldest spot first). Call after the user has joined the
    workspace, inside the same transaction.
    """
    spots = list(
        WorkspaceMemberInviteProject.objects.filter(invite=invite, project__deleted_at__isnull=True)
        .select_related("project")
        .order_by("created_at")
    )
    project_ids = []
    for spot in spots:
        upsert_project_member(spot.project, user, cap_project_role(spot.role, workspace_role))
        project_ids.append(spot.project_id)
        invalidate_cache_directly(
            path=f"/api/workspaces/{invite.workspace.slug}/projects/{spot.project_id}/members/",
            url_params=False,
            user=False,
            multiple=True,
        )
    remove_project_spots(invite)
    return project_ids


def remove_project_spots(invite):
    """Soft-delete every pending spot of an invitation (queryset delete is an immediate UPDATE)."""
    WorkspaceMemberInviteProject.objects.filter(invite=invite).delete()


def remove_project_spots_for_invites(invites):
    """Same as ``remove_project_spots`` for a queryset/list of invitations."""
    WorkspaceMemberInviteProject.objects.filter(invite__in=list(invites)).delete()


def remove_project_spots_for_project(project):
    """Soft-delete every pending spot of a project being deleted.

    A workspace invitation that was created from a project and is left with no live spot is cancelled
    too; invitations an admin made at workspace level are kept.
    """
    invite_ids = list(
        WorkspaceMemberInviteProject.objects.filter(project=project).values_list("invite_id", flat=True).distinct()
    )
    if not invite_ids:
        return
    WorkspaceMemberInviteProject.objects.filter(project=project).delete()
    still_has_spots = WorkspaceMemberInviteProject.objects.filter(invite_id__in=invite_ids).values("invite_id")
    WorkspaceMemberInvite.objects.filter(
        pk__in=invite_ids,
        created_from_project=True,
        accepted=False,
        responded_at__isnull=True,
    ).exclude(pk__in=still_has_spots).delete()
