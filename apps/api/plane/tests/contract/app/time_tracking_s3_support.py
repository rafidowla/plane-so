# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — shared world + helpers for the request/approval/entry tests.

Dhaka workspace (UTC+6, local midnight = 18:00 UTC). Project P (tracked) and Q (tracked).

* ``wsadmin`` — the test user: workspace admin (20) and admin of P.
* ``pa`` — workspace member (15), admin of P.   ``pa_q`` — workspace member, admin of Q.
* ``member`` / ``member2`` — workspace members, members of P (``member`` also of Q).
* ``guest`` — workspace guest (5), guest of P.
"""

import importlib
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from plane.db.models import IssueWorklog, TimeEntryRequest, WorklogSource

from .time_tracking_s2_support import (  # noqa: F401 (fixtures)
    DHAKA,
    _load_urlconf,
    client_for,
    join,
    make_issue,
    make_project,
    make_user,
    set_settings,
    utc,
    ws_url,
)

# "Now" for most tests: Wed 2026-06-24 12:00 Dhaka.
NOW = utc(2026, 6, 24, 6, 0)
MON = date(2026, 6, 22)


@pytest.fixture
def s3(db, create_user, workspace):
    workspace.timezone = DHAKA
    workspace.save()
    p, p_states = make_project(workspace, create_user, "P Project", "PP")
    q, q_states = make_project(workspace, create_user, "Q Project", "QQ")
    join(p, create_user, role=20)
    pa = make_user(workspace, 15, "Pa")
    pa_q = make_user(workspace, 15, "PaQ")
    member = make_user(workspace, 15, "Mem")
    member2 = make_user(workspace, 15, "Mem2")
    guest = make_user(workspace, 5, "Guest")
    join(p, pa, 20)
    join(q, pa_q, 20)
    join(p, member, 15)
    join(q, member, 15)
    join(p, member2, 15)
    join(p, guest, 5)
    issue = make_issue(p, p_states["in_progress"], "P issue", create_user, assignees=[member])
    issue_q = make_issue(q, q_states["in_progress"], "Q issue", create_user, assignees=[member])
    return {
        "workspace": workspace,
        "slug": workspace.slug,
        "p": p,
        "q": q,
        "issue": issue,
        "issue_q": issue_q,
        "wsadmin": create_user,
        "pa": pa,
        "pa_q": pa_q,
        "member": member,
        "member2": member2,
        "guest": guest,
    }


@pytest.fixture
def event_guard(db):
    """Install the 0129 trigger + cascading FK (pytest runs with --nomigrations)."""
    module = importlib.import_module("plane.db.migrations.0129_time_tracking_v2_requests")
    with connection.cursor() as cursor:
        cursor.execute(module.EVENT_GUARD_SQL)


def entry(s, user, start, end, issue=None, **extra):
    """A finished timer entry for ``user`` (no split; tests keep it inside one local day)."""
    issue = issue or s["issue"]
    minutes = int((end - start).total_seconds() // 60)
    return IssueWorklog.objects.create(
        project=issue.project,
        issue=issue,
        logged_by=user,
        duration=minutes,
        logged_date=extra.pop("logged_date", None) or start.astimezone(ZoneInfo(DHAKA)).date(),
        started_at=start,
        ended_at=end,
        source=extra.pop("source", WorklogSource.TIMER),
        **extra,
    )


def iso(dt):
    return dt.isoformat()


def submit(client, s, **data):
    return client.post(ws_url(s, "time/requests/"), data, format="json")


def missing(client, s, start, end, issue=None, reason="forgot the timer", **extra):
    return submit(
        client,
        s,
        type="missing_time",
        issue_id=str((issue or s["issue"]).id),
        started_at=iso(start),
        ended_at=iso(end),
        reason=reason,
        **extra,
    )


def act(client, s, request_id, verb, **data):
    return client.post(ws_url(s, f"time/requests/{request_id}/{verb}/"), data, format="json")


def req(request_id):
    return TimeEntryRequest.objects.get(pk=request_id)
