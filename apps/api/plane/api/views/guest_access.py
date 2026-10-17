# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: guest-access — guest visibility / ownership rules shared by the public ``/api/v1/`` views.

The web app's internal API (``plane.app.views``) hides work items from project Guests unless the
project allows ``guest_view_all_features``, and lets only a project Admin or the author edit or
delete comments and attachments. These helpers apply the same rules to the v1 API so API-key
clients (e.g. the MCP connector) are not more permissive than the app.
"""

# Django imports
from django.db.models import Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import Issue, ProjectMember, WorkspaceMember

# FORK: guest-access PSR-119 — these two moved to plane.utils.guest_work_item (shared with the app); re-exported
from plane.utils.guest_work_item import guest_count_q, is_restricted_guest  # noqa: F401
from plane.app.permissions import ProjectEntityPermission, ProjectLitePermission

VIEW_DENIED_MESSAGE = "You are not allowed to view this issue"
MODIFY_DENIED_MESSAGE = "You don't have the required permissions."


def visible_issues_q(user, slug) -> Q:
    """Issues ``user`` may see in workspace ``slug``.

    Project admins/members see everything in their projects; a Guest sees only what they created,
    unless the project has ``guest_view_all_features`` on.
    """
    memberships = ProjectMember.objects.filter(
        member=user,
        is_active=True,
        workspace__slug=slug,
        project__archived_at__isnull=True,
        project__deleted_at__isnull=True,
    )
    full_access = memberships.filter(
        Q(role__gt=ROLE.GUEST.value) | Q(role=ROLE.GUEST.value, project__guest_view_all_features=True)
    ).values("project_id")
    own_only = memberships.filter(role=ROLE.GUEST.value, project__guest_view_all_features=False).values("project_id")
    return Q(project_id__in=full_access) | Q(project_id__in=own_only, created_by=user)


def can_view_issue(user, project_id, created_by_id) -> bool:
    """Same rule as the app: a restricted guest only sees work items they created."""
    return created_by_id == user.id or not is_restricted_guest(user, project_id)


def issue_view_denied(request, project_id, issue_id=None, issue=None, message=VIEW_DENIED_MESSAGE, http_status=None):
    """Return a denial ``Response`` when ``request.user`` may not see the work item, else ``None``.

    Pass the loaded ``issue`` to avoid a query; otherwise it is looked up by ``issue_id`` within
    ``project_id`` (the guest check runs first, so non-guests cost one query).
    """
    user = request.user
    if not is_restricted_guest(user, project_id):
        return None
    if issue is not None:
        created_by_id = issue.created_by_id
    else:
        created_by_id = (
            Issue.objects.filter(pk=issue_id, project_id=project_id).values_list("created_by_id", flat=True).first()
        )
    if created_by_id == user.id:
        return None
    return Response({"error": message}, status=http_status or status.HTTP_403_FORBIDDEN)


def can_modify_owned(user, slug, project_id, created_by_id) -> bool:
    """Admin-or-author rule used by the app for comments and attachments.

    Mirrors ``allow_permission(allowed_roles=[ROLE.ADMIN], creator=True)``: the author (while still a
    workspace member), a project Admin, or a workspace Admin who is also a project member.
    """
    workspace_member = WorkspaceMember.objects.filter(member=user, workspace__slug=slug, is_active=True)
    if created_by_id == user.id and workspace_member.exists():
        return True
    project_member = ProjectMember.objects.filter(
        member=user, workspace__slug=slug, project_id=project_id, is_active=True
    )
    if project_member.filter(role=ROLE.ADMIN.value).exists():
        return True
    return project_member.exists() and workspace_member.filter(role=ROLE.ADMIN.value).exists()


def project_visibility_q(user, slug) -> Q:
    """Projects ``user`` can list/retrieve: their own, plus public ones unless they are a workspace Guest."""
    member_q = Q(project_projectmember__member=user, project_projectmember__is_active=True)
    if WorkspaceMember.objects.filter(
        member=user, workspace__slug=slug, is_active=True, role=ROLE.GUEST.value
    ).exists():
        return member_q
    return member_q | Q(network=2)


# FORK: guest-access PSR-119 — the field/ownership helpers live in plane.utils.guest_work_item (shared with the app)
class ProjectGuestWritePermission(ProjectEntityPermission):
    """``ProjectEntityPermission`` (writes: project Admin/Member), except any active project member may
    use the HTTP methods the view lists in ``guest_write_methods``. The view then applies the guest rules
    (field allow-list, own items only)."""

    def has_permission(self, request, view):
        if request.method in getattr(view, "guest_write_methods", ()):
            return ProjectLitePermission().has_permission(request, view)
        return super().has_permission(request, view)
