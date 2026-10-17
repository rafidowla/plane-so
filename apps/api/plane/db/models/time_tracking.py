# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — workspace time settings, per-member tracking status
history, the time-tracking audit log (S1) and time-entry requests with their
append-only event history (S3), and per-work-item time estimates with their
append-only revision history (S6).

The two history tables are append-only: no soft delete (so the generic
``hard_delete`` sweep, which only touches models with ``deleted_at``, never
reaches them), ``save()`` refuses updates and ``delete()`` on an instance
refuses. Workspace / user hard deletes still cascade at the SQL level.
"""

# Python imports
import uuid

# Django imports
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


def default_work_days():
    # ISO weekdays: Monday=1 .. Sunday=7.
    return [1, 2, 3, 4, 5]


class AppendOnlyModel(models.Model):
    """Rows are written once and never changed or deleted through the ORM."""

    id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True, primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{self.__class__.__name__} rows are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{self.__class__.__name__} rows are append-only")


class TimeTrackingSetting(BaseModel):
    """Workspace-wide time-tracking settings. One row per workspace, created lazily.

    The timezone used to bucket time into days is NOT stored here: it is
    ``Workspace.timezone`` (one source of truth, edited in workspace settings).
    """

    workspace = models.OneToOneField("db.Workspace", on_delete=models.CASCADE, related_name="time_tracking_setting")
    work_days = ArrayField(models.PositiveSmallIntegerField(), default=default_work_days)
    minutes_per_day = models.PositiveIntegerField(default=480)
    remind_after_minutes = models.PositiveIntegerField(default=600)
    auto_stop_after_minutes = models.PositiveIntegerField(default=840)
    # "Still working?" check: each check lands at a random time between min and
    # max minutes of continuous running (see utils/time_tracking/limits.py). 0/0 = off.
    idle_check_min_minutes = models.PositiveSmallIntegerField(default=20)
    idle_check_max_minutes = models.PositiveSmallIntegerField(default=80)
    allow_member_manual_entry = models.BooleanField(default=False)
    weekly_approval_enabled = models.BooleanField(default=False)
    billing_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Time Tracking Setting"
        verbose_name_plural = "Time Tracking Settings"
        db_table = "time_tracking_settings"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(auto_stop_after_minutes__gt=models.F("remind_after_minutes")),
                name="time_tracking_setting_auto_stop_after_remind",
            )
        ]

    def __str__(self):
        return f"time-settings:{self.workspace_id}"


class TimeTrackingMemberStatus(AppendOnlyModel):
    """One row per change of a member's tracking on/off status.

    The status on a date ``d`` is the latest row (by ``effective_from``, then
    ``created_at``) with ``effective_from <= d``. No row means tracked for
    workspace members/admins (role >= 15).
    """

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="time_tracking_member_statuses"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_tracking_statuses")
    is_tracked = models.BooleanField()
    effective_from = models.DateField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_tracking_status_changes",
    )

    class Meta:
        verbose_name = "Time Tracking Member Status"
        verbose_name_plural = "Time Tracking Member Statuses"
        db_table = "time_tracking_member_status"
        ordering = ("-effective_from", "-created_at")
        indexes = [
            models.Index(fields=["workspace", "user", "effective_from"], name="tt_member_status_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} tracked={self.is_tracked} from {self.effective_from}"


class TimeTrackingAuditTarget(models.TextChoices):
    WORKLOG = "worklog", "Worklog"
    MEMBER_STATUS = "member_status", "Member status"
    SETTING = "setting", "Setting"
    ESTIMATE = "estimate", "Estimate"


class TimeTrackingAuditAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"
    AUTO_STOP = "auto_stop", "Auto stop"
    APPLY_REQUEST = "apply_request", "Apply request"
    CONFIRM = "confirm", "Confirm"


class TimeTrackingAuditLog(AppendOnlyModel):
    """Who changed which time-tracking record, with before/after snapshots.

    ``actor`` null means the system (e.g. the timer sweep).

    FORK: time-tracking-v2 secfix — append-only in the database too (migration 0132): a
    trigger refuses any UPDATE / DELETE except the cascade of a workspace hard delete and
    the ``actor`` -> NULL of a user hard delete. Both FKs are therefore DO_NOTHING here
    with the real ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` constraints created in SQL
    (Django's own cascade would issue a plain DELETE / UPDATE, which the trigger refuses).
    """

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.DO_NOTHING, db_constraint=False, related_name="time_tracking_audit_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="time_tracking_audit_logs",
    )
    target_type = models.CharField(max_length=20, choices=TimeTrackingAuditTarget.choices)
    target_id = models.UUIDField()
    action = models.CharField(max_length=20, choices=TimeTrackingAuditAction.choices)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "Time Tracking Audit Log"
        verbose_name_plural = "Time Tracking Audit Logs"
        db_table = "time_tracking_audit_logs"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["workspace", "target_type", "target_id"], name="tt_audit_target_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.target_type}:{self.target_id}"


# ---------------------------------------------------------------------------
# S3 — time-entry requests, approvals and their append-only history (mig 0129)
# ---------------------------------------------------------------------------


class TimeEntryRequestType(models.TextChoices):
    MISSING_TIME = "missing_time", "Missing time"
    TIME_CHANGE = "time_change", "Time change"
    DELETION = "deletion", "Deletion"
    AUTO_STOP_REVIEW = "auto_stop_review", "Auto-stop review"


class TimeEntryRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class TimeEntryActorRole(models.TextChoices):
    MEMBER = "member", "Member"
    PROJECT_ADMIN = "project_admin", "Project admin"
    WORKSPACE_ADMIN = "workspace_admin", "Workspace admin"
    SYSTEM = "system", "System"


class TimeEntryRequestAction(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    APPLIED = "applied", "Applied"
    WITHDRAWN = "withdrawn", "Withdrawn"
    SUPERSEDED = "superseded", "Superseded"


class TimeEntryRequest(ProjectBaseModel):
    """A member's request to add, change or delete time, or a system review item.

    ``missing_time`` creates entries on approval; ``time_change`` / ``deletion``
    target ``worklog`` (the member's own finished entry, which keeps its old
    values until approved); ``auto_stop_review`` is opened by the 14 h auto-stop
    for the entry's approver (``worklog`` = the last piece of the stopped run).
    ``old_*`` is the entry as it was when the request was made (approval is
    refused as stale when the entry no longer matches it). The approver is
    resolved at read time (``plane.utils.time_tracking.approvers``), so a request
    follows its work item to another project. Rows are never deleted through the
    ORM; the history lives in ``TimeEntryRequestEvent``.
    """

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_entry_requests"
    )
    # Same survival rule as IssueWorklog.issue: a request outlives its work item.
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="time_entry_requests"
    )
    worklog = models.ForeignKey(
        "db.IssueWorklog",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="time_entry_requests",
    )
    type = models.CharField(max_length=20, choices=TimeEntryRequestType.choices)
    work_date = models.DateField()
    old_started_at = models.DateTimeField(null=True, blank=True)
    old_ended_at = models.DateTimeField(null=True, blank=True)
    old_duration = models.PositiveIntegerField(null=True, blank=True)
    new_started_at = models.DateTimeField(null=True, blank=True)
    new_ended_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=TimeEntryRequestStatus.choices, default=TimeEntryRequestStatus.PENDING
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_time_entry_requests",
    )
    reviewer_role = models.CharField(max_length=20, choices=TimeEntryActorRole.choices, blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Time Entry Request"
        verbose_name_plural = "Time Entry Requests"
        db_table = "time_entry_requests"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["worklog"],
                condition=models.Q(status="pending", deleted_at__isnull=True, worklog__isnull=False),
                name="time_entry_request_one_pending_per_worklog",
            ),
            models.CheckConstraint(
                condition=~models.Q(type__in=["missing_time", "time_change"])
                | models.Q(new_ended_at__gt=models.F("new_started_at")),
                name="time_entry_request_new_interval_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "status", "created_at"], name="tt_request_ws_status_idx"),
            models.Index(fields=["workspace", "requester", "created_at"], name="tt_request_ws_user_idx"),
        ]

    def __str__(self):
        return f"{self.type}:{self.status} by {self.requester_id}"

    def delete(self, *args, **kwargs):
        raise ValueError("TimeEntryRequest rows are never deleted; withdraw or reject them instead")


class TimeEntryRequestEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("TimeEntryRequestEvent rows are append-only")

    def delete(self):
        raise ValueError("TimeEntryRequestEvent rows are append-only")


class TimeEntryRequestEvent(AppendOnlyModel):
    """Append-only history of a request: submitted, approved/rejected, applied, withdrawn, superseded.

    Enforced three ways: ``save()`` refuses updates, instance and queryset
    ``delete()`` / ``update()`` refuse, and a Postgres trigger (migration 0129)
    rejects any UPDATE and any direct DELETE. ``request`` is DO_NOTHING here with
    the real ``ON DELETE CASCADE`` constraint created in SQL by the migration, so
    a workspace / project / user hard delete still removes the history (the
    cascade runs inside Postgres, which the trigger allows) while a plain
    ``DELETE FROM time_entry_request_events`` is refused. ``actor`` has no DB
    constraint for the same reason (SET_NULL would be an UPDATE).
    """

    request = models.ForeignKey(
        "db.TimeEntryRequest", on_delete=models.DO_NOTHING, db_constraint=False, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="time_entry_request_events",
    )
    actor_role = models.CharField(max_length=20, choices=TimeEntryActorRole.choices)
    action = models.CharField(max_length=20, choices=TimeEntryRequestAction.choices)
    note = models.TextField(blank=True, default="")
    payload = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    objects = TimeEntryRequestEventQuerySet.as_manager()

    class Meta:
        verbose_name = "Time Entry Request Event"
        verbose_name_plural = "Time Entry Request Events"
        db_table = "time_entry_request_events"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["request", "created_at"], name="tt_request_event_idx"),
        ]

    def __str__(self):
        return f"{self.action} on {self.request_id}"


# ---------------------------------------------------------------------------
# S6 — per-work-item time estimates and their append-only revisions (mig 0130)
# ---------------------------------------------------------------------------


class TimeEstimateMode(models.TextChoices):
    """``Project.time_estimate_mode`` (DESIGN §2 item 33)."""

    OFF = "off", "Off"
    OPTIONAL = "optional", "Optional"
    REQUIRED = "required", "Required"


class TimeEstimateRevisionKind(models.TextChoices):
    ORIGINAL_SET = "original_set", "Original set"
    ORIGINAL_REPLACED = "original_replaced", "Original replaced"
    RE_ESTIMATE = "re_estimate", "Re-estimate"
    CLEARED = "cleared", "Cleared"


class IssueTimeEstimate(ProjectBaseModel):
    """One time estimate per work item, in minutes, independent of Plane's points estimates.

    ``original_*`` is the estimate as it stood when work started; it is replaced freely
    until ``locked_at`` is set (first of: timer start, first time entry, move into a
    Started/Completed state — see ``plane.utils.time_tracking.estimate``) and never
    changes afterwards. ``current_*`` is the latest value (re-estimates after the lock).
    ``grandfathered``: the estimate was first set on an item already past the "required"
    gate (it was started without one). Same survival rule as ``IssueWorklog.issue``: the
    row outlives its work item (estimate-accuracy reports keep deleted items).
    """

    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="time_estimates"
    )
    original_minutes = models.PositiveIntegerField()
    original_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="original_time_estimates",
    )
    original_set_at = models.DateTimeField()
    current_minutes = models.PositiveIntegerField()
    current_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_time_estimates",
    )
    current_set_at = models.DateTimeField()
    locked_at = models.DateTimeField(null=True, blank=True)
    grandfathered = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Issue Time Estimate"
        verbose_name_plural = "Issue Time Estimates"
        db_table = "issue_time_estimates"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=models.Q(deleted_at__isnull=True),
                name="issue_time_estimate_one_per_issue",
            ),
        ]

    def __str__(self):
        return f"{self.issue_id}: {self.current_minutes} min (original {self.original_minutes})"


class IssueEstimateGrandfather(ProjectBaseModel):
    """Marks a work item as already in progress when its project's estimate mode became
    "required" (items in a Started/Completed state at that moment, see
    ``plane.utils.time_tracking.estimate.grandfather_project``). A marked item is never blocked
    by the estimate gate. The flag is stored, not derived from the item's current state: the
    state is user-controlled, so deriving "grandfathered" from it let anyone skip the gate by
    creating or moving an item straight into a Started state. Cleared when the item moves to
    another project (the flag belongs to the project whose mode flip it records).
    """

    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="estimate_grandfathers"
    )

    class Meta:
        verbose_name = "Issue Estimate Grandfather"
        verbose_name_plural = "Issue Estimate Grandfathers"
        db_table = "issue_estimate_grandfathers"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=models.Q(deleted_at__isnull=True),
                name="issue_estimate_grandfather_one_per_issue",
            ),
        ]

    def __str__(self):
        return f"grandfathered {self.issue_id}"


class IssueTimeEstimateRevision(AppendOnlyModel):
    """Append-only history of an estimate: every set, replace, re-estimate or clear.

    ``minutes`` is the value after the change (null for ``cleared``). No project column,
    so a moved work item's revisions need no repointing (they follow ``issue``/``estimate``).
    """

    estimate = models.ForeignKey("db.IssueTimeEstimate", on_delete=models.CASCADE, related_name="revisions")
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.DO_NOTHING, db_constraint=False, related_name="time_estimate_revisions"
    )
    minutes = models.PositiveIntegerField(null=True, blank=True)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_estimate_revisions",
    )
    kind = models.CharField(max_length=20, choices=TimeEstimateRevisionKind.choices)
    reason = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Issue Time Estimate Revision"
        verbose_name_plural = "Issue Time Estimate Revisions"
        db_table = "issue_time_estimate_revisions"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["issue", "created_at"], name="tt_estimate_rev_issue_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.minutes} on {self.issue_id}"


# Fork-owned receivers: lock the original estimate when work starts. The lock rules live in
# ``plane.utils.time_tracking.estimate`` (imported lazily: utils import these models).


@receiver(post_save, sender="db.Issue", dispatch_uid="time_tracking_v2_estimate_lock_on_state")
def _lock_estimate_on_state(sender, instance, created, update_fields=None, **kwargs):
    if created or (update_fields is not None and not ({"state", "state_id"} & set(update_fields))):
        return  # a new item has no estimate yet; other saves didn't touch the state
    from plane.utils.time_tracking.estimate import lock_if_work_started_state

    lock_if_work_started_state(instance)


@receiver(post_save, sender="db.WorklogTimer", dispatch_uid="time_tracking_v2_estimate_lock_on_timer")
def _lock_estimate_on_timer(sender, instance, created, **kwargs):
    if created:
        from plane.utils.time_tracking.estimate import lock_estimate

        lock_estimate(instance.issue_id, instance.started_at)


@receiver(post_save, sender="db.IssueWorklog", dispatch_uid="time_tracking_v2_estimate_lock_on_entry")
def _lock_estimate_on_entry(sender, instance, created, **kwargs):
    if created:
        from plane.utils.time_tracking.estimate import lock_estimate

        lock_estimate(instance.issue_id)


@receiver(pre_save, sender="db.Project", dispatch_uid="time_tracking_v2_estimate_mode_before")
def _remember_estimate_gate(sender, instance, update_fields=None, **kwargs):
    """Remember whether the estimate gate was in force before this save (see the post_save below)."""
    instance._tt_gate_before = None
    if instance.pk is None or (
        update_fields is not None and not ({"time_estimate_mode", "is_time_tracking_enabled"} & set(update_fields))
    ):
        return
    row = sender.objects.filter(pk=instance.pk).values_list("is_time_tracking_enabled", "time_estimate_mode").first()
    if row is not None:
        instance._tt_gate_before = bool(row[0]) and row[1] == TimeEstimateMode.REQUIRED.value


@receiver(post_save, sender="db.Project", dispatch_uid="time_tracking_v2_estimate_mode_after")
def _grandfather_on_gate_start(sender, instance, created, **kwargs):
    """The gate just came into force (mode switched to required, or tracking switched on while the
    mode is required): mark the items already in progress so they are not blocked."""
    before = getattr(instance, "_tt_gate_before", None)
    instance._tt_gate_before = None
    if created or before is None or before:
        return
    if instance.is_time_tracking_enabled and instance.time_estimate_mode == TimeEstimateMode.REQUIRED.value:
        from plane.utils.time_tracking.estimate import grandfather_project

        grandfather_project(instance.pk)
