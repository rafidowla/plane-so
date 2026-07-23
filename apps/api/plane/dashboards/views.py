# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-dashboards views.

Phase 2 ships CRUD for ``WorkspaceDashboardWidget`` (list/create/update/delete).
Per-widget-type data fetching (chart data, issue lists) is Phase 3 and is not
implemented here. See docs/custom-dashboards-design.md.
"""

from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace
from plane.utils.filters import IssueFilterSet

from plane.dashboards.models import WorkspaceDashboardWidget
from plane.dashboards.serializers import WorkspaceDashboardWidgetSerializer
from plane.dashboards.widget_data import (
    WIDGET_DATA_DISPATCH,
    view_list_data,
)

WidgetType = WorkspaceDashboardWidget.WidgetType


class WorkspaceDashboardWidgetsEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        widgets = WorkspaceDashboardWidget.objects.filter(workspace__slug=slug).order_by(
            "sort_order"
        )
        serializer = WorkspaceDashboardWidgetSerializer(widgets, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        serializer = WorkspaceDashboardWidgetSerializer(
            data=request.data, context={"workspace": workspace}
        )
        if serializer.is_valid():
            serializer.save(workspace_id=workspace.id)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WorkspaceDashboardWidgetDetailEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, widget_id):
        widget = WorkspaceDashboardWidget.objects.filter(
            workspace__slug=slug, pk=widget_id
        ).first()
        if not widget:
            return Response({"error": "Widget not found."}, status=status.HTTP_404_NOT_FOUND)
        workspace = Workspace.objects.get(slug=slug)
        serializer = WorkspaceDashboardWidgetSerializer(
            widget, data=request.data, partial=True, context={"workspace": workspace}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, widget_id):
        widget = WorkspaceDashboardWidget.objects.filter(
            workspace__slug=slug, pk=widget_id
        ).first()
        if not widget:
            return Response({"error": "Widget not found."}, status=status.HTTP_404_NOT_FOUND)
        widget.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceDashboardWidgetDataEndpoint(BaseAPIView):
    """Chart data for a single widget (distribution_pie / project_breakdown_pie /
    age_trend). ``view_list`` widgets are served by the issues endpoint below."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, widget_id):
        widget = WorkspaceDashboardWidget.objects.filter(
            workspace__slug=slug, pk=widget_id
        ).first()
        if not widget:
            return Response({"error": "Widget not found."}, status=status.HTTP_404_NOT_FOUND)

        if widget.widget_type == WidgetType.VIEW_LIST:
            return Response(
                {"error": "Use the issues endpoint for view_list widgets."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data_fn = WIDGET_DATA_DISPATCH.get(widget.widget_type)
        if data_fn is None:
            return Response(
                {"error": f"Unsupported widget_type: {widget.widget_type!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data_fn(widget, slug, request.user), status=status.HTTP_200_OK
        )


class WorkspaceDashboardWidgetIssuesEndpoint(BaseAPIView):
    """Paginated issues for a ``view_list`` widget's saved View."""

    filterset_class = IssueFilterSet

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, widget_id):
        widget = WorkspaceDashboardWidget.objects.filter(
            workspace__slug=slug, pk=widget_id
        ).first()
        if not widget:
            return Response({"error": "Widget not found."}, status=status.HTTP_404_NOT_FOUND)

        if widget.widget_type != WidgetType.VIEW_LIST:
            return Response(
                {"error": "This endpoint only serves view_list widgets."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return view_list_data(widget=widget, slug=slug, request=request, view=self)
