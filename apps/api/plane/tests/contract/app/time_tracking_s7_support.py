# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S7 — helpers for the estimate-accuracy contract tests.

Uses the S3 world (Dhaka workspace, UTC+6, so local midnight is 18:00 UTC): ``wsadmin``
(workspace admin and admin of P), ``pa`` (admin of P), ``pa_q`` (admin of Q), ``member`` (P and
Q), ``member2`` (P), ``guest`` (P). Every task is created directly (no timers, so the random
"still working?" check never comes into play).
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from freezegun import freeze_time

from plane.db.models import Issue, IssueTimeEstimate, IssueWorklog, State

from .time_tracking_s2_support import client_for, utc, ws_url

# Wed 2026-06-24 12:00 Dhaka.
NOW = utc(2026, 6, 24, 6, 0)
FROM, TO = "2026-06-01", "2026-06-30"


def state(project, group):
    return State.objects.filter(project=project, group=group).first()


def task(
    s,
    project,
    original,
    actual,
    estimator,
    *,
    worker=None,
    estimator_minutes=None,
    current=None,
    group="completed",
    done_at=NOW,
    grandfathered=False,
    name=None,
):
    """A work item in a ``group`` state, with an estimate set by ``estimator`` and ``actual`` minutes logged.

    ``actual`` is split between the estimator (``estimator_minutes``, default none) and
    ``worker`` (the rest; default ``estimator``). ``done_at`` is when it was completed.
    """
    with freeze_time(done_at):
        issue = Issue(
            workspace=project.workspace,
            project=project,
            name=name or f"T{Issue.all_objects.count()}",
            state=state(project, group),
        )
        issue.save(created_by_id=s["wsadmin"].id)
        IssueTimeEstimate.objects.create(
            workspace=project.workspace,
            project=project,
            issue=issue,
            original_minutes=original,
            original_by=estimator,
            original_set_at=done_at - timedelta(days=2),
            current_minutes=current or original,
            current_by=estimator,
            current_set_at=done_at - timedelta(days=2),
            grandfathered=grandfathered,
        )
        own = estimator_minutes if estimator_minutes is not None else (actual if worker is None else 0)
        for user, minutes in ((estimator, own), (worker or estimator, actual - own)):
            if minutes > 0 and user is not None:
                log(s, project, issue, user, minutes, done_at)
    return issue


def log(s, project, issue, user, minutes, at):
    return IssueWorklog.objects.create(
        workspace=project.workspace,
        project=project,
        issue=issue,
        logged_by=user,
        duration=minutes,
        logged_date=at.astimezone(ZoneInfo("Asia/Dhaka")).date(),
        source="manual",
    )


def accuracy_url(s, frm=FROM, to=TO, **params):
    query = "&".join([f"from={frm}", f"to={to}"] + [f"{k}={v}" for k, v in params.items()])
    return ws_url(s, f"time/estimate-accuracy/?{query}")


def get(viewer, s, frm=FROM, to=TO, expect=200, **params):
    r = client_for(viewer).get(accuracy_url(s, frm, to, **params))
    assert r.status_code == expect, r.content
    return r.data if expect == 200 else r.json()


def names(data):
    return {t["name"] for t in data["tasks"]["results"]}
