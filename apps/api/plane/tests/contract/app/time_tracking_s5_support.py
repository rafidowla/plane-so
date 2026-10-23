# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S5 — helpers for the Reports v2 / capacity contract tests.

Uses the ``s2`` fixture (Dhaka workspace, project "TTP", workspace + project admin)."""

import csv
from datetime import date, timedelta

from plane.db.models import IssueWorklog, ResourceCapacity, TimeTrackingMemberStatus

from .time_tracking_s2_support import client_for, utc, ws_url

# Fri 16 Oct 2026, 12:00 in Dhaka — 1–7 Oct 2026 is entirely in the past.
AFTER_RANGE = utc(2026, 10, 16, 6, 0)
OCT = {d: date(2026, 10, d) for d in range(1, 32)}
H = 60  # minutes per hour


def log(s, user, day, minutes, issue=None, note="", source="manual", billable=True, rate=None, start=None):
    issue = issue or s["issue"]
    started_at = start
    return IssueWorklog.objects.create(
        workspace=s["workspace"],
        project=issue.project,
        issue=issue,
        logged_by=user,
        duration=minutes,
        logged_date=day,
        description=note,
        source=source,
        is_billable=billable,
        billable_rate=rate,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=minutes) if started_at else None,
    )


def set_tracking(s, user, tracked, effective_from):
    return TimeTrackingMemberStatus.objects.create(
        workspace=s["workspace"], user=user, is_tracked=tracked, effective_from=effective_from
    )


def set_capacity(s, user, weekly_minutes):
    ResourceCapacity.objects.update_or_create(
        workspace=s["workspace"], user=user, defaults={"weekly_capacity": weekly_minutes}
    )


def report_url(s, frm="2026-10-01", to="2026-10-07", **params):
    query = "&".join([f"from={frm}", f"to={to}"] + [f"{k}={v}" for k, v in params.items()])
    return ws_url(s, f"time/report/?{query}")


def report(viewer, s, frm="2026-10-01", to="2026-10-07", expect=200, **params):
    r = client_for(viewer).get(report_url(s, frm, to, **params))
    assert r.status_code == expect, r.content
    return r.data if expect == 200 else r.json()


def csv_rows(viewer, s, frm="2026-10-01", to="2026-10-07", **params):
    query = "&".join([f"from={frm}", f"to={to}"] + [f"{k}={v}" for k, v in params.items()])
    r = client_for(viewer).get(ws_url(s, f"time/report.csv?{query}"))
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "text/csv"
    return list(csv.reader(r.content.decode().splitlines()))


def member_row(data, user):
    return next(m for m in data["members"] if m["user_id"] == str(user.id))
