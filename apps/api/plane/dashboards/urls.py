# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-dashboards URL routes.

Mounted under `/api/` from the root urlconf (one FORK-marked line, mirroring
`plane.properties.urls`). Phase 2 exposes widget CRUD only; Phase 3 adds the
`.../widgets/<uuid:widget_id>/data/` and `.../widgets/<uuid:widget_id>/issues/`
routes alongside these.
"""

from django.urls import path

from plane.dashboards.views import (
    WorkspaceDashboardWidgetDataEndpoint,
    WorkspaceDashboardWidgetDetailEndpoint,
    WorkspaceDashboardWidgetIssuesEndpoint,
    WorkspaceDashboardWidgetsEndpoint,
)

_WORKSPACE = "workspaces/<str:slug>/fork-dashboard"

urlpatterns = [
    path(
        f"{_WORKSPACE}/widgets/",
        WorkspaceDashboardWidgetsEndpoint.as_view(),
        name="workspace-dashboard-widgets",
    ),
    path(
        f"{_WORKSPACE}/widgets/<uuid:widget_id>/",
        WorkspaceDashboardWidgetDetailEndpoint.as_view(),
        name="workspace-dashboard-widget-detail",
    ),
    path(
        f"{_WORKSPACE}/widgets/<uuid:widget_id>/data/",
        WorkspaceDashboardWidgetDataEndpoint.as_view(),
        name="workspace-dashboard-widget-data",
    ),
    path(
        f"{_WORKSPACE}/widgets/<uuid:widget_id>/issues/",
        WorkspaceDashboardWidgetIssuesEndpoint.as_view(),
        name="workspace-dashboard-widget-issues",
    ),
]
