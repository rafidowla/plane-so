# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — workspace time settings and per-member tracking status.

Tracking status is an effective-dated, append-only history
(``TimeTrackingMemberStatus``): the status on day ``d`` is the latest row with
``effective_from <= d`` (ties broken by ``created_at``). With no row, workspace
members/admins (role >= 15) are tracked. Guests are never tracked.
"""

# Python imports
from datetime import timedelta

# Django imports
from django.db import transaction

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    TimeTrackingMemberStatus,
    TimeTrackingSetting,
    WorkspaceMember,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.tz import local_today, workspace_tz


def get_settings(workspace_id):
    """The workspace's settings row, created with defaults on first use."""
    setting, _ = TimeTrackingSetting.all_objects.get_or_create(workspace_id=workspace_id)
    return setting


def status_row_on(workspace_id, user_id, on_date):
    """The status row in force on ``on_date``, or None if none applies."""
    return (
        TimeTrackingMemberStatus.objects.filter(workspace_id=workspace_id, user_id=user_id, effective_from__lte=on_date)
        .order_by("-effective_from", "-created_at")
        .first()
    )


def tracking_turned_off(workspace_id, user_id, on_date):
    """True only when an admin explicitly turned tracking off for this day."""
    row = status_row_on(workspace_id, user_id, on_date)
    return row is not None and not row.is_tracked


def is_internal_member(workspace_id, user_id):
    """Active workspace admin/member (guests are clients and never tracked)."""
    return WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
    ).exists()


def is_tracked(workspace_id, user_id, on_date):
    """Whether the user's time is tracked on ``on_date`` (guests never are)."""
    if not is_internal_member(workspace_id, user_id):
        return False
    return not tracking_turned_off(workspace_id, user_id, on_date)


def tracked_days(workspace_id, user_id, start, end):
    """The set of dates in [start, end] on which the user is tracked."""
    if end < start or not is_internal_member(workspace_id, user_id):
        return set()
    rows = list(
        TimeTrackingMemberStatus.objects.filter(workspace_id=workspace_id, user_id=user_id, effective_from__lte=end)
        .order_by("effective_from", "created_at")
        .values_list("effective_from", "is_tracked")
    )
    days = set()
    current = True
    i = 0
    day = start
    while day <= end:
        while i < len(rows) and rows[i][0] <= day:
            current = rows[i][1]
            i += 1
        if current:
            days.add(day)
        day += timedelta(days=1)
    return days


def set_member_tracking(workspace_id, user_id, tracked, effective_from, actor):
    """Append a status row (audited). Turning tracking off with an effective date
    of today or earlier also stops the member's running timer in this workspace,
    now. Returns ``(row, stopped_worklogs)``.
    """
    from plane.db.models import WorklogTimer
    from plane.utils.time_tracking.timer import system_stop_timer

    today = local_today(workspace_tz(workspace_id))
    with transaction.atomic():
        before = is_tracked(workspace_id, user_id, today)
        row = TimeTrackingMemberStatus.objects.create(
            workspace_id=workspace_id,
            user_id=user_id,
            is_tracked=tracked,
            effective_from=effective_from,
            changed_by=actor,
        )
        audit.record(
            workspace_id,
            actor,
            TimeTrackingAuditTarget.MEMBER_STATUS,
            row.id,
            TimeTrackingAuditAction.CREATE,
            before={"user_id": user_id, "is_tracked_today": before},
            after={"user_id": user_id, "is_tracked": tracked, "effective_from": effective_from},
        )
    stopped = []
    if not tracked and effective_from <= today:
        timer = WorklogTimer.objects.filter(workspace_id=workspace_id, user_id=user_id).first()
        if timer:
            stopped = system_stop_timer(timer.id, None, "tracking_disabled", actor=actor) or []
    return row, stopped
