# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Count, Q
from django.contrib.postgres.aggregates import ArrayAgg

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseViewSet
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ClientSerializer
from plane.db.models import Client


class ClientViewSet(BaseViewSet):
    """Workspace-scoped CRUD for clients (the customers projects are done for)."""

    serializer_class = ClientSerializer
    model = Client
    search_fields = ["name", "identifier"]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .annotate(project_count=Count("projects", filter=Q(projects__deleted_at__isnull=True)))
            .annotate(
                project_ids=ArrayAgg(
                    "projects__id",
                    filter=Q(projects__deleted_at__isnull=True),
                    distinct=True,
                )
            )
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        clients = self.get_queryset()
        serializer = ClientSerializer(clients, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        client = self.get_queryset().get(pk=pk)
        serializer = ClientSerializer(client)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        from plane.db.models import Workspace

        workspace = Workspace.objects.get(slug=slug)
        serializer = ClientSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(workspace_id=workspace.id)
            # re-fetch with annotation
            client = self.get_queryset().get(pk=serializer.data["id"])
            return Response(ClientSerializer(client).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        client = self.get_queryset().get(pk=pk)
        serializer = ClientSerializer(client, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        client = self.get_queryset().get(pk=pk)
        client.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
