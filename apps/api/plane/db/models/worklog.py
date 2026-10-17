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

    # FORK: time-tracking-v2 — time survives work-item deletion. DO_NOTHING keeps the
    # soft-delete cascade (soft_delete_related_objects skips DO_NOTHING) and the 60-day
    # hard delete from removing worklogs; db_constraint=False lets a row outlive its
    # issue. Project deletion still cascades through the project FK. `issue_ref` /
    # `issue_name` are the label to fall back on once the issue row is gone.
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="worklogs"
    )
    issue_ref = models.CharField(max_length=64, blank=True, default="")
    issue_name = models.CharField(max_length=255, blank=True, default="")
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
    # FORK: time-tracking-v2 — the pieces of one timer run share a session id (a run
    # that crosses local midnight is split into one entry per workspace-local day).
    session_id = models.UUIDField(null=True, blank=True)
    # Stopped by the 14 h backstop (S2) / by an unanswered "still working?" check (S2).
    auto_stopped = models.BooleanField(default=False)
    idle_stopped = models.BooleanField(default=False)
    # FORK: time-tracking-v2 S3 — the approved request this entry was created/changed by.
    request = models.ForeignKey(
        "db.TimeEntryRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="applied_worklogs"
    )

    class Meta:
        verbose_name = "Issue Worklog"
        verbose_name_plural = "Issue Worklogs"
        db_table = "issue_worklogs"
        ordering = ("-logged_date", "-created_at")
        indexes = [
            models.Index(fields=["workspace", "logged_by", "logged_date"], name="worklog_ws_user_date_idx"),
            models.Index(fields=["workspace", "logged_date"], name="worklog_ws_date_idx"),
            models.Index(
                fields=["issue"], condition=models.Q(deleted_at__isnull=True), name="worklog_issue_active_idx"
            ),
        ]

    def __str__(self):
        return f"{self.issue_id} - {self.logged_by_id} - {self.duration}m"

    def save(self, *args, **kwargs):
        # FORK: time-tracking-v2 — snapshot the work item label on first write, so the
        # entry can still be labelled after the issue row is hard-deleted.
        if self.issue_id and not self.issue_ref:
            from plane.utils.time_tracking.snapshot import issue_snapshot

            self.issue_ref, self.issue_name = issue_snapshot(self.issue_id)
        super().save(*args, **kwargs)


class WorklogTimer(ProjectBaseModel):
    """A currently-running stopwatch. At most one active timer per user.

    Stopping the timer converts it into an IssueWorklog and removes the timer.
    """

    # FORK: time-tracking-v2 — DO_NOTHING/no constraint: a timer whose work item is
    # deleted is not silently dropped; the sweep (bgtasks.time_tracking_sweep) stops it
    # at the deletion time and logs the time.
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="worklog_timers"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worklog_timers")
    started_at = models.DateTimeField()
    description = models.TextField(blank=True, default="")
    # FORK: time-tracking-v2 — set by the 10 h reminder (S2) and by answering the
    # "still working?" check (S2). Fields only in S1.
    reminded_at = models.DateTimeField(null=True, blank=True)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)

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
    # FORK: time-tracking-v2 S5 — now the per-person weekly capacity OVERRIDE: null
    # follows the workspace work week (TimeTrackingSetting work_days x minutes_per_day);
    # a value is spread evenly over the working days. Nullable so an override can be
    # cleared without deleting the row's rates.
    weekly_capacity = models.PositiveIntegerField(null=True, blank=True, default=None)
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
