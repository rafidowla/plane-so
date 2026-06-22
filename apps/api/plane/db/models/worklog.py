# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


class WorklogSource(models.TextChoices):
    TIMER = "timer", "Timer"
    MANUAL = "manual", "Manual"


class WorklogApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class TimesheetStatus(models.TextChoices):
    OPEN = "open", "Open"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class IssueWorklog(ProjectBaseModel):
    """A single time entry logged against an issue by a resource.

    `logged_by` is the resource the time belongs to; `created_by` (from the
    audit mixin) records who actually entered it, so a PM/admin can log time
    on behalf of a resource and we keep the audit trail.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="worklogs")
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worklogs"
    )
    # Duration of the entry in minutes.
    duration = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, default="")
    # The calendar date the work was performed (drives timesheet/period grouping).
    logged_date = models.DateField()
    # Populated for timer-sourced entries.
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=WorklogSource.choices, default=WorklogSource.MANUAL)
    # Free-form categorisation of the work (e.g. development, qa, meeting).
    work_type = models.CharField(max_length=50, null=True, blank=True)
    # Billing snapshot taken at log time so later rate changes don't rewrite history.
    is_billable = models.BooleanField(default=True)
    billable_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="USD")
    # Approval / locking lifecycle.
    approval_status = models.CharField(
        max_length=20, choices=WorklogApprovalStatus.choices, default=WorklogApprovalStatus.DRAFT
    )
    is_locked = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_worklogs",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    timesheet = models.ForeignKey(
        "db.Timesheet", on_delete=models.SET_NULL, null=True, blank=True, related_name="worklogs"
    )

    class Meta:
        verbose_name = "Issue Worklog"
        verbose_name_plural = "Issue Worklogs"
        db_table = "issue_worklogs"
        ordering = ("-logged_date", "-created_at")

    def __str__(self):
        return f"{self.issue_id} - {self.logged_by_id} - {self.duration}m"


class WorklogTimer(ProjectBaseModel):
    """A currently-running stopwatch. At most one active timer per user.

    Stopping the timer converts it into an IssueWorklog and removes the timer.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="worklog_timers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worklog_timers")
    started_at = models.DateTimeField()
    description = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Worklog Timer"
        verbose_name_plural = "Worklog Timers"
        db_table = "worklog_timers"
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(deleted_at__isnull=True),
                name="worklog_timer_one_active_per_user",
            )
        ]

    def __str__(self):
        return f"timer:{self.user_id} -> {self.issue_id}"


class Timesheet(BaseModel):
    """A per-resource period (week) that bundles worklogs for submission/approval."""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="timesheets")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timesheets")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=20, choices=TimesheetStatus.choices, default=TimesheetStatus.OPEN)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_timesheets",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Timesheet"
        verbose_name_plural = "Timesheets"
        db_table = "timesheets"
        ordering = ("-period_start",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user", "period_start"],
                condition=models.Q(deleted_at__isnull=True),
                name="timesheet_unique_workspace_user_period_when_deleted_at_null",
            )
        ]

    def __str__(self):
        return f"{self.user_id} {self.period_start}..{self.period_end} [{self.status}]"


class ResourceCapacity(BaseModel):
    """Per-resource workspace settings: weekly capacity and default rates.

    Used for utilization (logged vs capacity) and as the default billable rate
    source for that resource's worklogs.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="resource_capacities")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="resource_capacities")
    # Expected billable/available capacity per week, in minutes (default 40h).
    weekly_capacity = models.PositiveIntegerField(default=2400)
    # Internal cost rate and default client-facing billable rate.
    cost_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    billable_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="USD")

    class Meta:
        verbose_name = "Resource Capacity"
        verbose_name_plural = "Resource Capacities"
        db_table = "resource_capacities"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"],
                condition=models.Q(deleted_at__isnull=True),
                name="resource_capacity_unique_workspace_user_when_deleted_at_null",
            )
        ]

    def __str__(self):
        return f"capacity:{self.user_id} {self.weekly_capacity}m/wk"
