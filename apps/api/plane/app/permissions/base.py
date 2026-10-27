# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from plane.db.models import WorkspaceMember, ProjectMember
from functools import wraps
from rest_framework.response import Response
from rest_framework import status

from enum import Enum


class ROLE(Enum):
    ADMIN = 20
    MEMBER = 15
    GUEST = 5


def allow_permission(allowed_roles, level="PROJECT", creator=False, model=None):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            # Check for creator if required
            if creator and model:
                # check if the user is part of the workspace or not
                if not WorkspaceMember.objects.filter(
                    member=request.user,
                    workspace__slug=kwargs["slug"],
                    is_active=True,
                ).exists():
                    return Response(
                        {"error": "You don't have the required permissions."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

                # project-level creator access also requires active membership of the URL project
                if (
                    level != "WORKSPACE"
                    and not ProjectMember.objects.filter(
                        member=request.user,
                        workspace__slug=kwargs["slug"],
                        project_id=kwargs["project_id"],
                        is_active=True,
                    ).exists()
                ):
                    return Response(
                        {"error": "You don't have the required permissions."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

                # FORK: put-permission — also match the object inside the URL project, so a creator
                # cannot reach an object of theirs in another project through this project's route.
                creator_filter = {"id": kwargs["pk"], "created_by": request.user}
                if kwargs.get("project_id") is not None:
                    creator_filter["project_id"] = kwargs["project_id"]
                obj = model.objects.filter(**creator_filter).exists()
                if obj:
                    return view_func(instance, request, *args, **kwargs)

            # Convert allowed_roles to their values if they are enum members
            allowed_role_values = [role.value if isinstance(role, ROLE) else role for role in allowed_roles]

            # Check role permissions
            if level == "WORKSPACE":
                if WorkspaceMember.objects.filter(
                    member=request.user,
                    workspace__slug=kwargs["slug"],
                    role__in=allowed_role_values,
                    is_active=True,
                ).exists():
                    return view_func(instance, request, *args, **kwargs)
            else:
                is_user_has_allowed_role = ProjectMember.objects.filter(
                    member=request.user,
                    workspace__slug=kwargs["slug"],
                    project_id=kwargs["project_id"],
                    role__in=allowed_role_values,
                    is_active=True,
                ).exists()

                # Return if the user has the allowed role else if they are workspace admin and part of the project regardless of the role # noqa: E501
                if is_user_has_allowed_role:
                    return view_func(instance, request, *args, **kwargs)
                elif (
                    ProjectMember.objects.filter(
                        member=request.user,
                        workspace__slug=kwargs["slug"],
                        project_id=kwargs["project_id"],
                        is_active=True,
                    ).exists()
                    and WorkspaceMember.objects.filter(
                        member=request.user,
                        workspace__slug=kwargs["slug"],
                        role=ROLE.ADMIN.value,
                        is_active=True,
                    ).exists()
                ):
                    return view_func(instance, request, *args, **kwargs)

            # Return permission denied if no conditions are met
            return Response(
                {"error": "You don't have the required permissions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return _wrapped_view

    return decorator


# FORK: PSR-125 -- workspace Super Admin. A Super Admin is an active Admin with
# ``is_super_admin``; these gates sit on top of the normal Admin checks.
SUPER_ADMIN_REQUIRED_ERROR = "Only a Super Admin of this workspace can do this."


def is_workspace_super_admin(user, slug=None, workspace_id=None):
    """Whether ``user`` is an active Super Admin of the workspace (by slug or id)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    lookup = {"workspace__slug": slug} if workspace_id is None else {"workspace_id": workspace_id}
    return WorkspaceMember.objects.filter(
        member=user, role=ROLE.ADMIN.value, is_active=True, is_super_admin=True, **lookup
    ).exists()


def super_admin_forbidden():
    return Response({"error": SUPER_ADMIN_REQUIRED_ERROR}, status=status.HTTP_403_FORBIDDEN)


def allow_workspace_super_admin(view_func):
    """Allow the view only for a Super Admin of the workspace named by the ``slug`` URL kwarg."""

    @wraps(view_func)
    def _wrapped_view(instance, request, *args, **kwargs):
        if is_workspace_super_admin(request.user, slug=kwargs["slug"]):
            return view_func(instance, request, *args, **kwargs)
        return super_admin_forbidden()

    return _wrapped_view


def issue_hidden_from_guest(request, slug, project_id, issue_id):
    """Return True when the caller is a *restricted* project guest who must not
    see the given issue's data or sub-resources.

    A project member with role GUEST on a project whose
    ``guest_view_all_features`` is disabled may only see issues they created,
    the same rule issue detail applies. Issue sub-resource endpoints
    (attachments, activity, comments, reactions, versions, relations) apply it
    through this helper.

    Ownership is checked with ``Issue.objects`` (like issue detail), so the
    guest's own archived, draft and triage issues stay visible.

    Returns False for admins, members, unrestricted guests, and for the guest's
    own issues. Callers should translate a True result into a 403.
    """
    from plane.db.models import Issue

    is_restricted_guest = ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=request.user,
        role=ROLE.GUEST.value,
        is_active=True,
        project__guest_view_all_features=False,
    ).exists()
    if not is_restricted_guest:
        return False

    owns_issue = Issue.objects.filter(
        pk=issue_id,
        workspace__slug=slug,
        project_id=project_id,
        created_by=request.user,
    ).exists()
    return not owns_issue
