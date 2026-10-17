# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — append-only audit rows for time-tracking changes."""

WORKLOG_FIELDS = (
    "issue_id",
    "project_id",
    "logged_by_id",
    "duration",
    "logged_date",
    "started_at",
    "ended_at",
    "description",
    "source",
    "is_billable",
    "billable_rate",
    "currency",
    "session_id",
    "auto_stopped",
    "idle_stopped",
    "issue_ref",
    # S3: the approved request that created/changed the entry.
    "request_id",
)

SETTING_FIELDS = (
    "work_days",
    "minutes_per_day",
    "remind_after_minutes",
    "auto_stop_after_minutes",
    "idle_check_min_minutes",
    "idle_check_max_minutes",
    "allow_member_manual_entry",
    "weekly_approval_enabled",
    "billing_enabled",
)


def state(instance, fields):
    return {f: getattr(instance, f) for f in fields}


def worklog_state(worklog):
    return state(worklog, WORKLOG_FIELDS)


def setting_state(setting):
    return state(setting, SETTING_FIELDS)


def record(workspace_id, actor, target_type, target_id, action, before=None, after=None):
    """Write one audit row. ``actor`` None (or anonymous) = the system."""
    from plane.db.models import TimeTrackingAuditLog

    actor_id = None
    if actor is not None and not getattr(actor, "is_anonymous", False):
        actor_id = getattr(actor, "pk", actor)
    return TimeTrackingAuditLog.objects.create(
        workspace_id=workspace_id,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        before=before,
        after=after,
    )
