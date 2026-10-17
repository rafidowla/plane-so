# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ClientViewSet,
    IssueWorklogViewSet,
    WorklogTimerEndpoint,
    WorklogRunningTimerEndpoint,
    TimesheetViewSet,
    ResourceCapacityViewSet,
    TimeReportEndpoint,
    TimeTrackingSettingsEndpoint,
    TimeTrackingMembersEndpoint,
    TimeTrackingMemberStatusEndpoint,
    # FORK: time-tracking-v2 S2 — workspace timer bar, task picker, live dashboard
    WorkspaceTimerStartEndpoint,
    WorkspaceTimerStopEndpoint,
    WorkspaceTimerConfirmEndpoint,
    TimeMyTasksEndpoint,
    TimeLiveDashboardEndpoint,
    JiraImportStatusesEndpoint,
    JiraImportPreviewEndpoint,
    JiraImportEndpoint,
    JiraImportJobDetailEndpoint,
    # FORK: time-tracking-v2 S3 — requests/approvals and workspace-level manual entries
    TimeEntryRequestEndpoint,
    TimeEntryRequestApproveEndpoint,
    TimeEntryRequestRejectEndpoint,
    TimeEntryRequestWithdrawEndpoint,
    TimeEntryRequestHistoryEndpoint,
    TimeEntryRequestEventsEndpoint,
    TimeEntryEndpoint,
    TimeEntryDetailEndpoint,
    # FORK: time-tracking-v2 S6 — per-work-item time estimates
    IssueTimeEstimateEndpoint,
    # FORK: time-tracking-v2 S5 — Reports v2 + per-member capacity override
    TimeReportV2Endpoint,
    # FORK: time-tracking-v2 S4 — My timesheet
    TimesheetWeekEndpoint,
    TimesheetPeopleEndpoint,
    TimeTrackingMemberCapacityEndpoint,
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
    # FORK: PSR-105 — the user's running timer in the workspace (header/sidebar indicator)
    path(
        "workspaces/<str:slug>/worklog-timer/",
        WorklogRunningTimerEndpoint.as_view(),
        name="workspace-running-timer",
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
    # FORK: time-tracking-v2 — workspace settings and member tracking status
    path(
        "workspaces/<str:slug>/time/settings/",
        TimeTrackingSettingsEndpoint.as_view(),
        name="time-tracking-settings",
    ),
    path(
        "workspaces/<str:slug>/time/members/",
        TimeTrackingMembersEndpoint.as_view(),
        name="time-tracking-members",
    ),
    path(
        "workspaces/<str:slug>/time/members/<uuid:user_id>/tracking/",
        TimeTrackingMemberStatusEndpoint.as_view(),
        name="time-tracking-member-status",
    ),
    # FORK: time-tracking-v2 S2 — timer bar: running timer, start by issue_id, stop,
    # "still working?" confirm; my-tasks picker; live team dashboard
    path(
        "workspaces/<str:slug>/time/timer/",
        WorklogRunningTimerEndpoint.as_view(),
        name="time-timer",
    ),
    path(
        "workspaces/<str:slug>/time/timer/start/",
        WorkspaceTimerStartEndpoint.as_view(),
        name="time-timer-start",
    ),
    path(
        "workspaces/<str:slug>/time/timer/stop/",
        WorkspaceTimerStopEndpoint.as_view(),
        name="time-timer-stop",
    ),
    path(
        "workspaces/<str:slug>/time/timer/confirm/",
        WorkspaceTimerConfirmEndpoint.as_view(),
        name="time-timer-confirm",
    ),
    path(
        "workspaces/<str:slug>/time/my-tasks/",
        TimeMyTasksEndpoint.as_view(),
        name="time-my-tasks",
    ),
    path(
        "workspaces/<str:slug>/time/live/",
        TimeLiveDashboardEndpoint.as_view(),
        name="time-live",
    ),
    # FORK: time-tracking-v2 S3 — time-entry requests, approvals, history; manual entries
    path(
        "workspaces/<str:slug>/time/requests/",
        TimeEntryRequestEndpoint.as_view(),
        name="time-requests",
    ),
    path(
        "workspaces/<str:slug>/time/requests/events/",
        TimeEntryRequestEventsEndpoint.as_view(),
        name="time-request-events",
    ),
    path(
        "workspaces/<str:slug>/time/requests/<uuid:pk>/approve/",
        TimeEntryRequestApproveEndpoint.as_view(),
        name="time-request-approve",
    ),
    path(
        "workspaces/<str:slug>/time/requests/<uuid:pk>/reject/",
        TimeEntryRequestRejectEndpoint.as_view(),
        name="time-request-reject",
    ),
    path(
        "workspaces/<str:slug>/time/requests/<uuid:pk>/withdraw/",
        TimeEntryRequestWithdrawEndpoint.as_view(),
        name="time-request-withdraw",
    ),
    path(
        "workspaces/<str:slug>/time/requests/<uuid:pk>/history/",
        TimeEntryRequestHistoryEndpoint.as_view(),
        name="time-request-history",
    ),
    path(
        "workspaces/<str:slug>/time/entries/",
        TimeEntryEndpoint.as_view(),
        name="time-entries",
    ),
    path(
        "workspaces/<str:slug>/time/entries/<uuid:pk>/",
        TimeEntryDetailEndpoint.as_view(),
        name="time-entry-detail",
    ),
    # FORK: time-tracking-v2 S5 — Reports v2 (JSON + entry CSV), capacity override
    path(
        "workspaces/<str:slug>/time/report/",
        TimeReportV2Endpoint.as_view(),
        name="time-report-v2",
    ),
    path(
        "workspaces/<str:slug>/time/report.csv",
        TimeReportV2Endpoint.as_view(),
        {"csv_export": True},
        name="time-report-v2-csv",
    ),
    path(
        "workspaces/<str:slug>/time/members/<uuid:user_id>/capacity/",
        TimeTrackingMemberCapacityEndpoint.as_view(),
        name="time-tracking-member-capacity",
    ),
    # FORK: time-tracking-v2 S4 — My timesheet (week grid + entries by date)
    path(
        "workspaces/<str:slug>/time/timesheet/",
        TimesheetWeekEndpoint.as_view(),
        name="time-timesheet-week",
    ),
    path(
        "workspaces/<str:slug>/time/timesheet/people/",
        TimesheetPeopleEndpoint.as_view(),
        name="time-timesheet-people",
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
    # FORK: time-tracking-v2 S6 — a work item's time estimate (+ revision history)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/time-estimate/",
        IssueTimeEstimateEndpoint.as_view(),
        name="issue-time-estimate",
    ),
]
