# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — periodic sweep over running timers.

S1 converts two kinds of orphaned timers into worklogs (audited as ``auto_stop``,
actor = system):

* ``issue_deleted`` — the timer's work item was soft-deleted (stopped at its
  ``deleted_at``) or no longer exists at all (stopped now).
* ``tracking_disabled`` — an admin turned the member's tracking off from a date
  that has now arrived (stopped at the start of that local day, or at the
  timer's start if later).

S2 adds two passes driven by the workspace settings (``utils.time_tracking.limits``):

* ``limits`` — the 14 h auto-stop (``auto_stopped=True``) and the unanswered
  "still working?" check (stopped at the prompt time, ``idle_stopped=True``).
  The stop time comes from ``limits.cutoff``, so a late sweep run never counts
  extra time.
* ``reminded`` — the 10 h in-app reminder, once per timer (``reminded_at``).

Security fix adds ``member_removed`` — a timer whose owner is no longer an active
admin/member of the timer's project or an active non-guest member of the workspace
(removed, left or demoted to guest) is stopped; nothing else reacts to a membership
change, so the timer would otherwise run to the 14 h auto-stop and turn into time for
someone who has lost access. Same stop as every other system stop; the time up to the
sweep run counts (the sweep runs every minute).

The task runs every minute (celery beat) so the 5-minute "still working?" grace
is honoured to within a minute. Each pass is a fixed number of queries plus one
write per timer that actually changes.
"""

# Python imports
import logging

# Django imports
from django.db.models import Exists, OuterRef
from django.utils import timezone

# Third party imports
from celery import shared_task

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import Issue, Notification, ProjectMember, WorklogTimer, WorkspaceMember
from plane.utils.time_tracking import limits
from plane.utils.time_tracking.timer import system_stop_timer
from plane.utils.time_tracking.tracking import status_row_on
from plane.utils.time_tracking.tz import day_start, local_today, workspace_tz

logger = logging.getLogger("plane.worker")


def _sweep_deleted_issues():
    stopped = 0
    timers = list(WorklogTimer.objects.values_list("id", "issue_id"))
    if not timers:
        return 0
    issues = {
        row[0]: row[1]
        for row in Issue.all_objects.filter(id__in={t[1] for t in timers}).values_list("id", "deleted_at")
    }
    for timer_id, issue_id in timers:
        if issue_id in issues and issues[issue_id] is None:
            continue  # live issue
        ended_at = issues.get(issue_id)  # deleted_at, or None (row gone) => now
        if system_stop_timer(timer_id, ended_at, "issue_deleted") is not None:
            stopped += 1
    return stopped


def _sweep_tracking_disabled():
    stopped = 0
    tz_cache = {}
    for timer_id, workspace_id, user_id in WorklogTimer.objects.values_list("id", "workspace_id", "user_id"):
        tz = tz_cache.setdefault(workspace_id, workspace_tz(workspace_id))
        row = status_row_on(workspace_id, user_id, local_today(tz))
        if row is None or row.is_tracked:
            continue
        if system_stop_timer(timer_id, day_start(row.effective_from, tz), "tracking_disabled") is not None:
            stopped += 1
    return stopped


def _sweep_removed_members():
    """Stop timers whose owner can no longer track on the timer's project (see module docstring)."""
    stopped = 0
    active_project_member = ProjectMember.objects.filter(
        project_id=OuterRef("project_id"), member_id=OuterRef("user_id"), is_active=True, role__gte=ROLE.MEMBER.value
    )
    active_workspace_member = WorkspaceMember.objects.filter(
        workspace_id=OuterRef("workspace_id"),
        member_id=OuterRef("user_id"),
        is_active=True,
        role__gte=ROLE.MEMBER.value,
    )
    timer_ids = list(
        WorklogTimer.objects.filter(~Exists(active_project_member) | ~Exists(active_workspace_member)).values_list(
            "id", flat=True
        )
    )
    for timer_id in timer_ids:
        if system_stop_timer(timer_id, None, "member_removed") is not None:
            stopped += 1
    return stopped


def _sweep_limits():
    """Stop timers past the auto-stop time or an unanswered "still working?" deadline."""
    stopped = 0
    now = timezone.now()
    timers = list(WorklogTimer.objects.only("id", "workspace_id", "started_at", "last_confirmed_at"))
    if not timers:
        return 0
    settings = limits.settings_for({t.workspace_id for t in timers})
    for timer in timers:
        if now < limits.due_at(timer, settings[timer.workspace_id]):
            continue
        # ``stop_timer`` re-applies ``limits.cutoff`` and sets the stop time,
        # the flag and the audit reason; ``ended_at`` here is only "now".
        if system_stop_timer(timer.id, None, "limit") is not None:
            stopped += 1
    return stopped


def _sweep_reminders():
    """One in-app "your timer is still running" notification at ``remind_after_minutes``."""
    reminded = 0
    now = timezone.now()
    timers = list(
        WorklogTimer.objects.filter(reminded_at__isnull=True).only(
            "id", "workspace_id", "project_id", "issue_id", "user_id", "started_at"
        )
    )
    if not timers:
        return 0
    settings = limits.settings_for({t.workspace_id for t in timers})
    due = [t for t in timers if now >= limits.schedule(t, settings[t.workspace_id])["remind_at"]]
    if not due:
        return 0
    issues = {
        row["id"]: row
        for row in Issue.all_objects.filter(id__in={t.issue_id for t in due}).values(
            "id", "name", "sequence_id", "project__identifier", "state__name", "state__group"
        )
    }
    for timer in due:
        # Claim the reminder first: a concurrent run (or a stop) makes this a no-op.
        if not WorklogTimer.objects.filter(pk=timer.pk, reminded_at__isnull=True).update(reminded_at=now):
            continue
        issue = issues.get(timer.issue_id) or {}
        schedule = limits.schedule(timer, settings[timer.workspace_id])
        Notification.objects.create(
            workspace_id=timer.workspace_id,
            project_id=timer.project_id,
            receiver_id=timer.user_id,
            triggered_by_id=None,
            entity_identifier=timer.issue_id,
            # "issue" keeps it in the web inbox list, which filters on entity_name.
            entity_name="issue",
            sender="in_app:time_tracking:reminder",
            title="Your timer is still running",
            data={
                "issue": {
                    "id": str(timer.issue_id),
                    "name": issue.get("name"),
                    "identifier": issue.get("project__identifier"),
                    "sequence_id": issue.get("sequence_id"),
                    "state_name": issue.get("state__name"),
                    "state_group": issue.get("state__group"),
                },
                "issue_activity": {
                    "id": None,
                    "verb": "reminder",
                    "field": "worklog_timer",
                    "actor": None,
                    "new_value": None,
                    "old_value": None,
                    "old_identifier": None,
                    "new_identifier": None,
                },
                "time_tracking": {
                    "kind": "timer_reminder",
                    "timer_id": str(timer.id),
                    "started_at": timer.started_at.isoformat(),
                    "auto_stop_at": schedule["auto_stop_at"].isoformat(),
                },
            },
        )
        reminded += 1
    return reminded


@shared_task
def time_tracking_timer_sweep():
    result = {"issue_deleted": 0, "tracking_disabled": 0, "member_removed": 0, "limits": 0, "reminded": 0}
    passes = (
        ("issue_deleted", _sweep_deleted_issues),
        ("tracking_disabled", _sweep_tracking_disabled),
        ("member_removed", _sweep_removed_members),
        ("limits", _sweep_limits),
        ("reminded", _sweep_reminders),
    )
    for key, sweep in passes:
        try:
            result[key] = sweep()
        except Exception:  # one failing pass must not starve the other
            logger.exception("time_tracking_timer_sweep: %s pass failed", key)
    return result
