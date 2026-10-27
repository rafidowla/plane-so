# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import transaction
from django.db.models import Count, Q, OuterRef, Subquery, IntegerField
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models.functions import Coalesce

# Third party modules
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import WorkspaceEntityPermission, allow_permission, ROLE
from plane.app.permissions import is_workspace_super_admin, super_admin_forbidden  # FORK: PSR-125

# Module imports
from plane.app.serializers import (
    ProjectMemberRoleSerializer,
    WorkspaceMemberAdminSerializer,
    WorkspaceMemberMeSerializer,
    WorkSpaceMemberSerializer,
)
from plane.app.views.base import BaseAPIView
from plane.db.models import Project, ProjectMember, Workspace, WorkspaceMember, DraftIssue
from plane.utils.cache import invalidate_cache

from .. import BaseViewSet

# FORK: PSR-125
LAST_SUPER_ADMIN_ERROR = (
    "This is the only Super Admin of the workspace. Make another member a Super Admin first."
)


def has_other_super_admin(workspace_id, member_id):
    """Whether the workspace keeps an active Super Admin other than ``member_id``.

    Locks the Super Admin rows so two concurrent removals cannot both see the other one.
    Call inside ``transaction.atomic()``.
    """
    others = list(
        WorkspaceMember.objects.select_for_update()
        .filter(workspace_id=workspace_id, is_super_admin=True, is_active=True)
        .exclude(member_id=member_id)
        .values_list("id", flat=True)
    )
    return bool(others)


def is_sole_admin_of_any_project(slug, user_id):
    """Whether the user is the only member, and an admin, of a project in the workspace."""
    return (
        Project.objects.annotate(
            total_members=Count("project_projectmember"),
            member_with_role=Count(
                "project_projectmember",
                filter=Q(project_projectmember__member_id=user_id, project_projectmember__role=20),
            ),
        )
        .filter(total_members=1, member_with_role=1, workspace__slug=slug)
        .exists()
    )


class WorkSpaceMemberViewSet(BaseViewSet):
    serializer_class = WorkspaceMemberAdminSerializer
    model = WorkspaceMember

    search_fields = ["member__display_name", "member__first_name"]
    use_read_replica = True

    def get_queryset(self):
        return self.filter_queryset(
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("member", "member__avatar_asset")
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        workspace_member = WorkspaceMember.objects.get(member=request.user, workspace__slug=slug, is_active=True)

        # Get all active workspace members
        workspace_members = self.get_queryset()
        if workspace_member.role > 5:
            serializer = WorkspaceMemberAdminSerializer(workspace_members, fields=("id", "member", "role"), many=True)
        else:
            serializer = WorkSpaceMemberSerializer(workspace_members, fields=("id", "member", "role"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        workspace_member = WorkspaceMember.objects.get(member=request.user, workspace__slug=slug, is_active=True)

        try:
            # Get the specific workspace member by pk
            member = self.get_queryset().get(pk=pk)
        except WorkspaceMember.DoesNotExist:
            return Response(
                {"error": "Workspace member not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if workspace_member.role > ROLE.GUEST.value:
            serializer = WorkspaceMemberAdminSerializer(member, fields=("id", "member", "role"))
        else:
            serializer = WorkSpaceMemberSerializer(member, fields=("id", "member", "role"))
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        workspace_member = WorkspaceMember.objects.select_related("member", "member__avatar_asset").get(
            pk=pk, workspace__slug=slug, member__is_bot=False, is_active=True
        )
        if request.user.id == workspace_member.member_id:
            return Response(
                {"error": "You cannot update your own role"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SECURITY: only ``role`` (and, FORK: PSR-125, ``is_super_admin``) is writable here. Passing
        # other keys through let an admin move a member row into another workspace via ``workspace``.
        if (
            not isinstance(request.data, dict)
            or not request.data
            or not set(request.data) <= {"role", "is_super_admin"}
        ):
            return Response(
                {"error": "Only the role of a workspace member can be updated"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if "role" in request.data:
            serializer = WorkSpaceMemberSerializer(
                workspace_member, data={"role": request.data["role"]}, partial=True
            )
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            role = serializer.validated_data["role"]
        else:
            role = workspace_member.role

        # FORK: PSR-125 -- Super Admin is an Admin with a flag. Granting it implies Admin; any other
        # role drops it. Touching an Admin or Super Admin, or granting either, needs a Super Admin.
        if "is_super_admin" in request.data:
            if not isinstance(request.data["is_super_admin"], bool):
                return Response(
                    {"error": "is_super_admin must be true or false"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            is_super_admin = request.data["is_super_admin"]
            if is_super_admin and "role" not in request.data:
                role = ROLE.ADMIN.value
            if is_super_admin and role != ROLE.ADMIN.value:
                return Response(
                    {"error": "A Super Admin must have the Admin role"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            is_super_admin = workspace_member.is_super_admin and role == ROLE.ADMIN.value

        touches_admins = (
            workspace_member.role == ROLE.ADMIN.value
            or role == ROLE.ADMIN.value
            or is_super_admin != workspace_member.is_super_admin
        )
        if touches_admins and not is_workspace_super_admin(request.user, slug=slug):
            return super_admin_forbidden()

        # A guest can't hold any other project role, so demoting downgrades every project membership.
        is_guest = role == ROLE.GUEST.value
        if is_guest and is_sole_admin_of_any_project(slug, workspace_member.member_id):
            return Response(
                {
                    "error": "User is a part of some projects where they are the only admin, they should either leave that project or promote another user to admin."  # noqa: E501
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # FORK: PSR-125 -- never leave the workspace without a Super Admin.
            if (
                workspace_member.is_super_admin
                and not is_super_admin
                and not has_other_super_admin(workspace_member.workspace_id, workspace_member.member_id)
            ):
                return Response({"error": LAST_SUPER_ADMIN_ERROR}, status=status.HTTP_400_BAD_REQUEST)
            if is_guest:
                ProjectMember.objects.filter(
                    workspace_id=workspace_member.workspace_id, member_id=workspace_member.member_id
                ).update(role=ROLE.GUEST.value, updated_at=timezone.now())
            workspace_member.role = role
            workspace_member.is_super_admin = is_super_admin
            # Write only these columns so a concurrent removal or view_props save isn't reverted.
            workspace_member.save(update_fields=["role", "is_super_admin", "updated_at", "updated_by"])

        return Response(WorkSpaceMemberSerializer(workspace_member).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        # Check the user role who is deleting the user
        workspace_member = WorkspaceMember.objects.get(
            workspace__slug=slug, pk=pk, member__is_bot=False, is_active=True
        )

        # check requesting user role
        requesting_workspace_member = WorkspaceMember.objects.get(
            workspace__slug=slug, member=request.user, is_active=True
        )

        if str(workspace_member.id) == str(requesting_workspace_member.id):
            return Response(
                {"error": "You cannot remove yourself from the workspace. Please use leave workspace"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if requesting_workspace_member.role < workspace_member.role:
            return Response(
                {"error": "You cannot remove a user having role higher than you"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FORK: PSR-125 -- removing an Admin or Super Admin needs a Super Admin.
        if workspace_member.role == ROLE.ADMIN.value and not requesting_workspace_member.is_super_admin:
            return super_admin_forbidden()

        if is_sole_admin_of_any_project(slug, workspace_member.member_id):
            return Response(
                {
                    "error": "User is a part of some projects where they are the only admin, they should either leave that project or promote another user to admin."  # noqa: E501
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # FORK: PSR-125 -- never leave the workspace without a Super Admin.
            if workspace_member.is_super_admin and not has_other_super_admin(
                workspace_member.workspace_id, workspace_member.member_id
            ):
                return Response({"error": LAST_SUPER_ADMIN_ERROR}, status=status.HTTP_400_BAD_REQUEST)

            # Deactivate the users from the projects where the user is part of
            _ = ProjectMember.objects.filter(
                workspace__slug=slug, member_id=workspace_member.member_id, is_active=True
            ).update(is_active=False, updated_at=timezone.now())

            workspace_member.is_active = False
            workspace_member.is_super_admin = False  # FORK: PSR-125 -- rejoining must not restore it
            workspace_member.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @invalidate_cache(
        path="/api/workspaces/:slug/members/",
        url_params=True,
        user=False,
        multiple=True,
    )
    @invalidate_cache(path="/api/users/me/settings/")
    @invalidate_cache(path="api/users/me/workspaces/", user=False, multiple=True)
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def leave(self, request, slug):
        workspace_member = WorkspaceMember.objects.get(workspace__slug=slug, member=request.user, is_active=True)

        # Check if the leaving user is the only admin of the workspace
        if (
            workspace_member.role == 20
            and not WorkspaceMember.objects.filter(workspace__slug=slug, role=20, is_active=True).count() > 1
        ):
            return Response(
                {
                    "error": "You cannot leave the workspace as you are the only admin of the workspace you will have to either delete the workspace or promote another user to admin."  # noqa: E501
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if is_sole_admin_of_any_project(slug, request.user.id):
            return Response(
                {
                    "error": "You are a part of some projects where you are the only admin, you should either leave the project or promote another user to admin."  # noqa: E501
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # FORK: PSR-125 -- the last Super Admin cannot leave.
            if workspace_member.is_super_admin and not has_other_super_admin(
                workspace_member.workspace_id, workspace_member.member_id
            ):
                return Response(
                    {
                        "error": "You are the only Super Admin of this workspace. Make another member a Super Admin before you leave."  # noqa: E501
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # # Deactivate the users from the projects where the user is part of
            _ = ProjectMember.objects.filter(
                workspace__slug=slug, member_id=workspace_member.member_id, is_active=True
            ).update(is_active=False, updated_at=timezone.now())

            # # Deactivate the user
            workspace_member.is_active = False
            workspace_member.is_super_admin = False  # FORK: PSR-125 -- rejoining must not restore it
            workspace_member.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceMemberUserViewsEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace_member = WorkspaceMember.objects.get(workspace__slug=slug, member=request.user, is_active=True)
        workspace_member.view_props = request.data.get("view_props", {})
        workspace_member.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceMemberUserEndpoint(BaseAPIView):
    use_read_replica = True

    def get(self, request, slug):
        draft_issue_count = (
            DraftIssue.objects.filter(created_by=request.user, workspace_id=OuterRef("workspace_id"))
            .values("workspace_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        workspace_member = (
            WorkspaceMember.objects.filter(member=request.user, workspace__slug=slug, is_active=True)
            .annotate(draft_issue_count=Coalesce(Subquery(draft_issue_count, output_field=IntegerField()), 0))
            .first()
        )
        serializer = WorkspaceMemberMeSerializer(workspace_member)
        return Response(serializer.data, status=status.HTTP_200_OK)


class WorkspaceProjectMemberEndpoint(BaseAPIView):
    serializer_class = ProjectMemberRoleSerializer
    model = ProjectMember

    permission_classes = [WorkspaceEntityPermission]

    def get(self, request, slug):
        # Fetch all project IDs where the user is involved
        project_ids = (
            ProjectMember.objects.filter(member=request.user, is_active=True)
            .values_list("project_id", flat=True)
            .distinct()
        )

        # Get all the project members in which the user is involved
        project_members = ProjectMember.objects.filter(
            workspace__slug=slug, project_id__in=project_ids, is_active=True
        ).select_related("project", "member", "workspace")
        project_members = ProjectMemberRoleSerializer(project_members, many=True).data

        project_members_dict = dict()

        # Construct a dictionary with project_id as key and project_members as value
        for project_member in project_members:
            project_id = project_member.pop("project")
            if str(project_id) not in project_members_dict:
                project_members_dict[str(project_id)] = []
            project_members_dict[str(project_id)].append(project_member)

        return Response(project_members_dict, status=status.HTTP_200_OK)


class WorkspaceOwnershipTransferEndpoint(BaseAPIView):
    """FORK: PSR-125 -- hand workspace ownership to another Super Admin.

    Body: ``{"member_id": <user id>}``. The new owner must already be an active Super Admin;
    the previous owner keeps their Super Admin flag.
    """

    @invalidate_cache(path="/api/workspaces/", user=False)
    @invalidate_cache(path="/api/users/me/workspaces/", multiple=True, user=False)
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        if not is_workspace_super_admin(request.user, slug=slug):
            return super_admin_forbidden()

        member_id = request.data.get("member_id") if isinstance(request.data, dict) else None
        if not member_id:
            return Response({"error": "member_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().get(slug=slug)
            try:
                target = WorkspaceMember.objects.get(
                    workspace=workspace,
                    member_id=member_id,
                    member__is_bot=False,
                    is_active=True,
                    is_super_admin=True,
                )
            except (WorkspaceMember.DoesNotExist, ValueError, ValidationError):
                return Response(
                    {"error": "Ownership can only be transferred to another Super Admin of this workspace."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if workspace.owner_id == target.member_id:
                return Response({"error": "This member already owns the workspace."}, status=status.HTTP_400_BAD_REQUEST)
            workspace.owner_id = target.member_id
            workspace.save(update_fields=["owner", "updated_at"])

        return Response({"owner": str(target.member_id)}, status=status.HTTP_200_OK)

