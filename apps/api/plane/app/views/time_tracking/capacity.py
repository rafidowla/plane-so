# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseViewSet
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ResourceCapacitySerializer
from plane.db.models import Workspace, ResourceCapacity


class ResourceCapacityViewSet(BaseViewSet):
    """Per-resource weekly capacity and default rates (workspace-scoped)."""

    serializer_class = ResourceCapacitySerializer
    model = ResourceCapacity

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("user")
        )

    # Resource capacity rows carry internal cost_rate (what the company pays a
    # resource) and billable_rate for every user in the workspace. That is
    # ADMIN-only planning data; list access is restricted accordingly (upsert is
    # already ADMIN-only below).
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def list(self, request, slug):
        return Response(
            ResourceCapacitySerializer(self.get_queryset(), many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def upsert(self, request, slug):
        """Create or update the capacity row for a given user."""
        user_id = request.data.get("user")
        if not user_id:
            return Response({"error": "user is required."}, status=status.HTTP_400_BAD_REQUEST)
        workspace = Workspace.objects.get(slug=slug)
        capacity, _ = ResourceCapacity.objects.get_or_create(
            workspace_id=workspace.id, user_id=user_id
        )
        serializer = ResourceCapacitySerializer(capacity, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
