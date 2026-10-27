# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: PSR-125 -- delete re-confirmation code, deleted-workspace list and restore.

# Python imports
import re

# Django imports
from django.db import IntegrityError, transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_workspace_super_admin
from plane.app.serializers import WorkSpaceSerializer
from plane.app.views.base import BaseAPIView
from plane.bgtasks.workspace_delete_code_task import send_workspace_delete_code
from plane.db.models import Workspace
from plane.utils import workspace_lifecycle
from plane.utils.constants import RESTRICTED_WORKSPACE_SLUGS

_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class WorkspaceDeleteCodeEndpoint(BaseAPIView):
    """Email a one-time code that confirms a workspace delete, for accounts without a password."""

    @allow_workspace_super_admin
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        if workspace_lifecycle.uses_password(request.user):
            return Response(
                {"error": "Confirm with your password instead.", "error_code": "USE_PASSWORD"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = workspace_lifecycle.issue_delete_code(workspace.id, request.user.id)
        if code is None:
            return Response(
                {"error": "Too many codes sent. Try again in 10 minutes.", "error_code": "TOO_MANY_CODES"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        send_workspace_delete_code.delay(request.user.email, workspace.name, code)
        return Response({"sent": True}, status=status.HTTP_200_OK)


def _deleted_workspace_row(workspace):
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "slug": workspace_lifecycle.original_slug(workspace),
        "logo_url": workspace.logo_url,
        "deleted_at": workspace.deleted_at,
        "purge_at": workspace_lifecycle.purge_at(workspace),
        "is_slug_available": not Workspace.all_objects.filter(slug=workspace_lifecycle.original_slug(workspace))
        .exclude(pk=workspace.pk)
        .exists(),
    }


class DeletedWorkspacesEndpoint(BaseAPIView):
    """Deleted workspaces the user was a Super Admin of, still inside the keep window."""

    def get(self, request):
        workspaces = (
            workspace_lifecycle.restorable_workspaces_for(request.user)
            .select_related("logo_asset")
            .order_by("-deleted_at")
        )
        return Response([_deleted_workspace_row(ws) for ws in workspaces[:100]], status=status.HTTP_200_OK)


class WorkspaceRestoreEndpoint(BaseAPIView):
    def post(self, request, workspace_id):
        workspace = workspace_lifecycle.restorable_workspaces_for(request.user).filter(id=workspace_id).first()
        if workspace is None:
            return Response({"error": "Workspace not found."}, status=status.HTTP_404_NOT_FOUND)
        if workspace_lifecycle.deletion_still_running(workspace.id):
            return Response(
                {
                    "error": "This workspace is still being deleted. Try again in a few minutes.",
                    "error_code": "DELETE_IN_PROGRESS",
                },
                status=status.HTTP_409_CONFLICT,
            )

        data = request.data if isinstance(request.data, dict) else {}
        requested = data.get("slug")
        slug = str(requested).strip() if requested else workspace_lifecycle.original_slug(workspace)
        if len(slug) > 48 or not _SLUG_RE.match(slug) or slug in RESTRICTED_WORKSPACE_SLUGS:
            return Response(
                {"error": "That web address is not valid.", "error_code": "SLUG_INVALID"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if Workspace.all_objects.filter(slug=slug).exclude(pk=workspace.pk).exists():
            return Response(
                {"error": "That web address is now taken. Choose a new one.", "error_code": "SLUG_TAKEN", "slug": slug},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                locked = Workspace.all_objects.select_for_update().get(pk=workspace.pk)
                if locked.deleted_at is None:
                    return Response({"error": "Workspace not found."}, status=status.HTTP_404_NOT_FOUND)
                since = locked.deleted_at
                Workspace.all_objects.filter(pk=locked.pk).update(deleted_at=None, slug=slug)
                workspace_lifecycle.restore_workspace_tree(locked, since)
        except IntegrityError:
            return Response(
                {
                    "error": "This workspace could not be restored because some of its data now conflicts.",
                    "error_code": "RESTORE_CONFLICT",
                },
                status=status.HTTP_409_CONFLICT,
            )

        restored = Workspace.objects.get(pk=workspace.pk)
        return Response(WorkSpaceSerializer(restored).data, status=status.HTTP_200_OK)
