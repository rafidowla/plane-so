# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ClientViewSet,
    IssueWorklogViewSet,
    WorklogTimerEndpoint,
    TimesheetViewSet,
    ResourceCapacityViewSet,
    TimeReportEndpoint,
    JiraImportStatusesEndpoint,
    JiraImportPreviewEndpoint,
    JiraImportEndpoint,
    JiraImportJobDetailEndpoint,
)

urlpatterns = [
    # Clients (workspace-scoped)
    path(
        "workspaces/<str:slug>/clients/",
        ClientViewSet.as_view({"get": "list", "post": "create"}),
        name="clients",
    ),
    path(
        "workspaces/<str:slug>/clients/<uuid:pk>/",
        ClientViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="client-detail",
    ),
    # Issue worklogs
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/worklogs/",
        IssueWorklogViewSet.as_view({"get": "list", "post": "create"}),
        name="issue-worklogs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/worklogs/<uuid:pk>/",
        IssueWorklogViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="issue-worklog-detail",
    ),
    # Timer (start = post, stop = delete, current = get)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/worklog-timer/",
        WorklogTimerEndpoint.as_view(),
        name="worklog-timer",
    ),
    # Timesheets + approval
    path(
        "workspaces/<str:slug>/timesheets/",
        TimesheetViewSet.as_view({"get": "list"}),
        name="timesheets",
    ),
    path(
        "workspaces/<str:slug>/timesheets/submit/",
        TimesheetViewSet.as_view({"post": "submit"}),
        name="timesheet-submit",
    ),
    path(
        "workspaces/<str:slug>/timesheets/<uuid:pk>/review/",
        TimesheetViewSet.as_view({"post": "review"}),
        name="timesheet-review",
    ),
    # Resource capacity / rates
    path(
        "workspaces/<str:slug>/resource-capacities/",
        ResourceCapacityViewSet.as_view({"get": "list", "post": "upsert"}),
        name="resource-capacities",
    ),
    # Reporting
    path(
        "workspaces/<str:slug>/time-report/",
        TimeReportEndpoint.as_view(),
        name="time-report",
    ),
    # Jira import (self-service)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/jira-import/statuses/",
        JiraImportStatusesEndpoint.as_view(),
        name="jira-import-statuses",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/jira-import/preview/",
        JiraImportPreviewEndpoint.as_view(),
        name="jira-import-preview",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/jira-import/",
        JiraImportEndpoint.as_view(),
        name="jira-import",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/jira-import/<uuid:pk>/",
        JiraImportJobDetailEndpoint.as_view(),
        name="jira-import-detail",
    ),
]
