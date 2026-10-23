# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — who approves a time-entry request (DESIGN §2 item 17).

* A member's request goes to the **project admins of the request's project** (the
  work item's project — a member of several projects is routed by the task).
* A project admin's own request goes to the **workspace admins**.
* A workspace admin approves anything except their own requests.
* A project admin of *another* project cannot approve, and the requester never
  approves their own request.

Routing is resolved at read time from the request's current ``project_id``, so a
request whose work item moves to another project re-routes to the new project's
admins. Only active memberships count, and every approver must be an active
workspace admin/member (guests never approve).
"""

# Django imports
from django.db.models import Exists, OuterRef, Q

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import ProjectMember, TimeEntryActorRole, WorkspaceMember

ROUTE_PROJECT_ADMINS = "project_admins"
ROUTE_WORKSPACE_ADMINS = "workspace_admins"


def workspace_role(workspace_id, user_id):
    """The user's active workspace role, or None."""
    return (
        WorkspaceMember.objects.filter(workspace_id=workspace_id, member_id=user_id, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


def is_workspace_admin(workspace_id, user_id):
    return workspace_role(workspace_id, user_id) == ROLE.ADMIN.value


def is_project_admin(project_id, user_id):
    return ProjectMember.objects.filter(
        project_id=project_id, member_id=user_id, role=ROLE.ADMIN.value, is_active=True
    ).exists()


def actor_role(workspace_id, project_id, user_id):
    """The role recorded on history rows: workspace admin > project admin > member."""
    if is_workspace_admin(workspace_id, user_id):
        return TimeEntryActorRole.WORKSPACE_ADMIN
    if project_id and is_project_admin(project_id, user_id):
        return TimeEntryActorRole.PROJECT_ADMIN
    return TimeEntryActorRole.MEMBER


def route(request):
    """``project_admins`` for a member's request, ``workspace_admins`` when the requester
    is a project admin of the request's project (or a workspace admin)."""
    if is_project_admin(request.project_id, request.requester_id) or is_workspace_admin(
        request.workspace_id, request.requester_id
    ):
        return ROUTE_WORKSPACE_ADMINS
    return ROUTE_PROJECT_ADMINS


def can_approve(user_id, request):
    """Whether ``user_id`` may approve/reject ``request`` (status is not checked here)."""
    if str(user_id) == str(request.requester_id):
        return False
    ws_role = workspace_role(request.workspace_id, user_id)
    if ws_role is None or ws_role < ROLE.MEMBER.value:
        return False
    if ws_role == ROLE.ADMIN.value:
        return True
    return route(request) == ROUTE_PROJECT_ADMINS and is_project_admin(request.project_id, user_id)


def review_scope_q(workspace_id, user_id):
    """Q over ``TimeEntryRequest`` for every request ``user_id`` may approve (any status)."""
    ws_role = workspace_role(workspace_id, user_id)
    if ws_role is None or ws_role < ROLE.MEMBER.value:
        return Q(pk__in=[])
    not_own = ~Q(requester_id=user_id)
    if ws_role == ROLE.ADMIN.value:
        return Q(workspace_id=workspace_id) & not_own
    admin_projects = ProjectMember.objects.filter(
        workspace_id=workspace_id, member_id=user_id, role=ROLE.ADMIN.value, is_active=True
    ).values("project_id")
    requester_is_project_admin = Exists(
        ProjectMember.objects.filter(
            project_id=OuterRef("project_id"),
            member_id=OuterRef("requester_id"),
            role=ROLE.ADMIN.value,
            is_active=True,
        )
    )
    requester_is_workspace_admin = Exists(
        WorkspaceMember.objects.filter(
            workspace_id=OuterRef("workspace_id"),
            member_id=OuterRef("requester_id"),
            role=ROLE.ADMIN.value,
            is_active=True,
        )
    )
    return (
        Q(workspace_id=workspace_id, project_id__in=admin_projects)
        & not_own
        & ~Q(requester_is_project_admin)
        & ~Q(requester_is_workspace_admin)
    )


def approver_ids(request):
    """User ids to notify about a new request. A project with no (other) active admin
    falls back to the workspace admins, who can approve anything."""
    workspace_admins = set(
        WorkspaceMember.objects.filter(
            workspace_id=request.workspace_id, role=ROLE.ADMIN.value, is_active=True
        ).values_list("member_id", flat=True)
    )
    ids = set()
    if route(request) == ROUTE_PROJECT_ADMINS:
        internal = set(
            WorkspaceMember.objects.filter(
                workspace_id=request.workspace_id, role__gte=ROLE.MEMBER.value, is_active=True
            ).values_list("member_id", flat=True)
        )
        ids = {
            uid
            for uid in ProjectMember.objects.filter(
                project_id=request.project_id, role=ROLE.ADMIN.value, is_active=True
            ).values_list("member_id", flat=True)
            if uid in internal
        }
    ids.discard(request.requester_id)
    if not ids:
        ids = workspace_admins
    ids.discard(request.requester_id)
    return ids
