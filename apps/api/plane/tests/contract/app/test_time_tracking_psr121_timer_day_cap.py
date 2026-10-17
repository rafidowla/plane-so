# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-121 — the 24 h per person per local day cap on timer stops.

A stop is never refused (that would leave the timer running): the saved run is cut where the
day reaches 24 h and the stop response carries ``day_limit_clamped``. Reachable through legacy
``logged_date`` + ``duration`` rows, which take day capacity without clock time (the overlap
checks only see intervals). Every stop path shares ``stop_timer``: manual stop (both
endpoints), starting another timer, the 14 h auto-stop, the idle-check stop / late confirm and
the system stops of the sweep (removed from project, tracking off, item deleted).
"""

from datetime import date

import pytest
from freezegun import freeze_time
from rest_framework import status

from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import IssueWorklog, TimeTrackingAuditLog, WorklogTimer
from plane.utils.time_tracking.timer import system_stop_timer

from .time_tracking_s2_support import (  # noqa: F401 (fixtures)
    _load_urlconf,
    client_for,
    IDLE_OFF,
    issue_timer_url,
    make_issue,
    set_settings,
    utc,
    ws_url,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]

DAY = date(2026, 6, 22)  # Dhaka (UTC+6) local day of the runs below


def legacy(s, minutes, day=DAY):
    """A legacy date+duration row (no interval): fills the day without blocking clock time."""
    return IssueWorklog.objects.create(
        project=s["project"], issue=s["issue"], logged_by=s["user"], duration=minutes, logged_date=day
    )


def start(c, s, issue=None):
    issue = issue or s["issue"]
    return c.post(ws_url(s, "time/timer/start/"), {"issue_id": str(issue.id)}, format="json")


def timer_rows(s):
    return IssueWorklog.objects.filter(logged_by=s["user"], source="timer").order_by("started_at")


@pytest.fixture
def c(s2):
    set_settings(s2["workspace"], **IDLE_OFF)
    return client_for(s2["user"])


class TestManualStop:
    def test_stop_ends_the_entry_where_the_day_reaches_24h(self, s2, c):
        legacy(s2, 1400)  # 23 h 20 min already on the day: 40 min left
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            assert start(c, s2).status_code == 201
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            r = c.post(ws_url(s2, "time/timer/stop/"), {}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.content
        d = r.json()
        assert d["day_limit_clamped"] is True and d["total_duration"] == 40
        (wl,) = timer_rows(s2)
        assert wl.duration == 40 and wl.started_at == utc(2026, 6, 22, 4, 0) and wl.ended_at == utc(2026, 6, 22, 4, 40)
        assert not WorklogTimer.objects.filter(user=s2["user"]).exists()
        audit_row = TimeTrackingAuditLog.objects.get(target_id=wl.id)
        assert audit_row.action == "auto_stop" and audit_row.actor_id is None
        assert audit_row.after["reason"] == "day_limit" and audit_row.after["day_limit_clamped"] is True

    def test_full_day_saves_nothing_but_still_stops(self, s2, c):
        legacy(s2, 1440)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            assert c.post(issue_timer_url(s2), {}, format="json").status_code == 201
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            r = c.delete(issue_timer_url(s2), {}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.content
        d = r.json()
        assert d["day_limit_clamped"] is True and d["pieces"] == [] and d["total_duration"] == 0
        assert not timer_rows(s2).exists()
        assert not WorklogTimer.objects.filter(user=s2["user"]).exists()
        # a second stop finds nothing running
        assert c.delete(issue_timer_url(s2), {}, format="json").status_code == 404

    def test_under_the_cap_nothing_changes(self, s2, c):
        legacy(s2, 600)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            d = c.post(ws_url(s2, "time/timer/stop/"), {}, format="json").json()
        assert d["day_limit_clamped"] is False and d["total_duration"] == 60
        assert not TimeTrackingAuditLog.objects.filter(action="auto_stop").exists()

    def test_cross_midnight_run_ends_on_the_full_day(self, s2, c):
        legacy(s2, 1410)  # 30 min left on 22 June (Dhaka)
        with freeze_time(utc(2026, 6, 22, 17, 0)):  # 23:00 Dhaka
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 19, 0)):  # 01:00 Dhaka, 23 June
            d = c.post(ws_url(s2, "time/timer/stop/"), {}, format="json").json()
        assert d["day_limit_clamped"] is True and d["total_duration"] == 30
        (wl,) = timer_rows(s2)  # the run ended on the 22nd: nothing on the 23rd
        assert wl.logged_date == DAY and wl.ended_at == utc(2026, 6, 22, 17, 30)


class TestOtherStopPaths:
    def test_starting_another_timer_clamps_the_stopped_one(self, s2, c):
        second = make_issue(s2["project"], s2["states"]["todo"], "Issue 2", s2["user"])
        legacy(s2, 1420)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            r = start(c, s2, second)
        assert r.status_code == 201, r.content
        stopped = r.json()["stopped"]
        assert stopped["day_limit_clamped"] is True and stopped["duration"] == 20
        assert WorklogTimer.objects.get(user=s2["user"]).issue_id == second.id

    def test_starting_another_timer_on_a_full_day(self, s2, c):
        second = make_issue(s2["project"], s2["states"]["todo"], "Issue 2", s2["user"])
        legacy(s2, 1440)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            r = start(c, s2, second)
        assert r.status_code == 201, r.content
        stopped = r.json()["stopped"]
        assert stopped["day_limit_clamped"] is True and stopped["pieces"] == [] and stopped["worklog"] is None

    def test_14h_auto_stop_sweep_clamps(self, s2, c):
        legacy(s2, 1000)  # 440 min left
        with freeze_time(utc(2026, 6, 22, 0, 0)):  # 06:00 Dhaka
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 14, 7)):
            assert time_tracking_timer_sweep()["limits"] == 1
        (wl,) = timer_rows(s2)
        assert wl.duration == 440 and wl.ended_at == utc(2026, 6, 22, 7, 20) and wl.auto_stopped is True
        audit_row = TimeTrackingAuditLog.objects.get(target_id=wl.id)
        assert audit_row.after["reason"] == "auto_stop" and audit_row.after["day_limit_clamped"] is True

    def test_14h_auto_stop_on_a_full_day_still_stops_and_counts(self, s2, c):
        legacy(s2, 1440)
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 14, 7)):
            assert time_tracking_timer_sweep()["limits"] == 1
        assert not WorklogTimer.objects.exists() and not timer_rows(s2).exists()

    def test_late_confirm_on_a_full_day_reports_the_auto_stop(self, s2, c):
        legacy(s2, 1440)
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            start(c, s2)
        with freeze_time(utc(2026, 6, 22, 14, 5)):
            r = c.post(ws_url(s2, "time/timer/confirm/"), {}, format="json")
        assert r.status_code == status.HTTP_409_CONFLICT, r.content
        body = r.json()
        assert body["code"] == "auto_stopped"
        assert body["stopped"]["day_limit_clamped"] is True and body["stopped"]["pieces"] == []

    def test_system_stop_clamps_and_keeps_its_reason(self, s2, c):
        legacy(s2, 1430)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            start(c, s2)
        timer = WorklogTimer.objects.get(user=s2["user"])
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            result = system_stop_timer(timer.id, None, "member_removed")
        assert result.day_limit_clamped is True and [w.duration for w in result] == [10]
        audit_row = TimeTrackingAuditLog.objects.get(target_id=result[0].id)
        assert audit_row.after["reason"] == "member_removed" and audit_row.after["day_limit_clamped"] is True

    def test_system_stop_on_a_full_day_is_a_stop_not_a_miss(self, s2, c):
        legacy(s2, 1440)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            start(c, s2)
        timer = WorklogTimer.objects.get(user=s2["user"])
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            result = system_stop_timer(timer.id, None, "member_removed")
        assert result is not None and list(result) == [] and result.day_limit_clamped is True
        assert not WorklogTimer.objects.filter(pk=timer.id).exists()
