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
from plane.utils.cache import cache_response
from plane.utils.filters import IssueFilterSet

from plane.dashboards.models import WorkspaceDashboardWidget
from plane.dashboards.serializers import WorkspaceDashboardWidgetSerializer
from plane.dashboards.widget_data import (
    WIDGET_DATA_DISPATCH,
    age_trend_data,
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

        # age_trend is the only cached widget type. Its response is a pure
        # function of (widget config, the *requesting user's* visible issues,
        # today's date), and the query is scoped by the caller's project
        # membership via get_analytics_filters -> _base_issue_queryset (the
        # base_filters always pin project__project_projectmember__member=user).
        # Two users with different project memberships legitimately get
        # different data from the same widget_id, so the cache MUST be keyed
        # per user (user=True) -- a global key would leak one user's
        # project-scoped counts to another. distribution_pie /
        # project_breakdown_pie share this same membership sensitivity and are
        # deliberately left uncached for now (see docs/custom-dashboards-design.md).
        if widget.widget_type == WidgetType.AGE_TREND:
            return self._age_trend_response(request, slug, widget)

        return Response(
            data_fn(widget, slug, request.user), status=status.HTTP_200_OK
        )

    @cache_response(timeout=300, user=True)
    def _age_trend_response(self, request, slug, widget):
        """Per-user-cached age_trend data (5 min TTL).

        Only reached from ``get`` after the ``@allow_permission`` workspace-role
        check and the widget lookup, so it is never an unguarded entry point.
        ``cache_response(user=True)`` folds ``request.user.id`` into the cache
        key alongside the full request path (which carries the widget_id), so
        each (user, widget) pair gets its own entry and no cross-user leak is
        possible.
        """
        return Response(
            age_trend_data(widget, slug, request.user), status=status.HTTP_200_OK
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
