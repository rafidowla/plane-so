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

S2 adds the reminder / auto-stop / still-working passes here.
"""

# Python imports
import logging

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import Issue, WorklogTimer
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
        if system_stop_timer(timer_id, ended_at, "issue_deleted"):
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
        if system_stop_timer(timer_id, day_start(row.effective_from, tz), "tracking_disabled"):
            stopped += 1
    return stopped


@shared_task
def time_tracking_timer_sweep():
    result = {"issue_deleted": 0, "tracking_disabled": 0}
    for key, sweep in (("issue_deleted", _sweep_deleted_issues), ("tracking_disabled", _sweep_tracking_disabled)):
        try:
            result[key] = sweep()
        except Exception:  # one failing pass must not starve the other
            logger.exception("time_tracking_timer_sweep: %s pass failed", key)
    return result
