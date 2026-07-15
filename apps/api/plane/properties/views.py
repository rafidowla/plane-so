# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties views.

Phase 0 ships only a capability/health probe. Property/option/value viewsets
are added in Phase 1/2 (see docs/custom-properties-design.md §4).
"""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Project, ProjectMember
from plane.properties.flags import is_instance_enabled
from plane.properties.models import ProjectPropertiesFeature


def is_project_admin(slug, project_id, user):
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


class CustomPropertiesHealthEndpoint(APIView):
    """Report whether the instance kill switch is on. No auth: reveals a single
    boolean capability flag and nothing project-specific."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"instance_enabled": is_instance_enabled()})


class ProjectPropertiesFeatureEndpoint(BaseAPIView):
    """Read / toggle the per-project custom-properties switch.

    GET is a capability probe: it always answers, reporting the stored toggle
    masked by the instance kill switch (so the client sees ``is_enabled: false``
    whenever the operator has the feature off). PATCH requires the instance
    switch on *and* a project admin.
    """

    def _payload(self, feature):
        instance = is_instance_enabled()
        return {
            "is_enabled": bool(feature and feature.is_enabled) and instance,
            "instance_enabled": instance,
        }

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id):
        feature = ProjectPropertiesFeature.objects.filter(
            workspace__slug=slug, project_id=project_id
        ).first()
        return Response(self._payload(feature), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def patch(self, request, slug, project_id):
        if not is_instance_enabled():
            return Response(
                {"error": "custom properties disabled"}, status=status.HTTP_403_FORBIDDEN
            )
        if not is_project_admin(slug, project_id, request.user):
            return Response(
                {"error": "Only project admins can toggle custom properties."},
                status=status.HTTP_403_FORBIDDEN,
            )
        project = Project.objects.get(pk=project_id)
        feature, _ = ProjectPropertiesFeature.objects.get_or_create(
            project_id=project_id,
            defaults={"workspace_id": project.workspace_id},
        )
        if "is_enabled" in request.data:
            feature.is_enabled = bool(request.data.get("is_enabled"))
            feature.save()
        return Response(self._payload(feature), status=status.HTTP_200_OK)
