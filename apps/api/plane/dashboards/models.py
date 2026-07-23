# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-dashboards data model (docs/custom-dashboards-design.md).

All models live in the fork-owned ``plane.dashboards`` app so their migration
chain never collides with upstream's ``plane.db`` chain. Upstream models are
referenced by string label (``"db.Workspace"`` etc.), the same pattern
``plane.properties.models`` uses. Table names are ``fork_``-prefixed and must
never reuse the upstream ``dashboards`` / ``widgets`` / ``dashboard_widgets``
tables (pre-rename) or ``deprecated_dashboards`` / ``deprecated_widgets`` /
``deprecated_dashboard_widgets`` (post-rename, see
``plane.db.migrations.0090_rename_dashboard_deprecateddashboard_and_more``).
"""

from django.db import models

from plane.db.models.base import BaseModel


class WorkspaceDashboardWidget(BaseModel):
    class WidgetType(models.TextChoices):
        DISTRIBUTION_PIE = "distribution_pie", "Distribution Pie"
        PROJECT_BREAKDOWN_PIE = "project_breakdown_pie", "Project Breakdown Pie"
        AGE_TREND = "age_trend", "Age Trend"
        VIEW_LIST = "view_list", "View List"

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="fork_dashboard_widgets",
    )
    widget_type = models.CharField(max_length=32, choices=WidgetType.choices)
    title = models.CharField(max_length=255, blank=True)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.FloatField(default=65535)
    issue_view = models.ForeignKey(
        "db.IssueView",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fork_dashboard_widgets",
    )
    config = models.JSONField(default=dict)

    class Meta:
        db_table = "fork_workspace_dashboard_widgets"
        verbose_name = "Workspace Dashboard Widget"
        verbose_name_plural = "Workspace Dashboard Widgets"
        ordering = ("sort_order",)

    def __str__(self):
        return f"{self.workspace_id} - {self.widget_type}"
