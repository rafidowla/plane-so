# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S4 — the My timesheet week payload (grid, expected, grouping, caps).

World: ``s3`` (Dhaka, UTC+6). The week of ``MON`` = Mon 2026-06-22 .. Sun 2026-06-28.
Dhaka local midnight is 18:00 UTC, so Mon 00:00 local = Sun 18:00 UTC.
"""

from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from plane.db.models import IssueWorklog, Timesheet

from .time_tracking_s2_support import IDLE_OFF, client_for, make_issue, set_settings, utc, ws_url
from . import time_tracking_s3_support as support
from .time_tracking_s3_support import MON, NOW, _load_urlconf, entry  # noqa: F401 (_load_urlconf is autouse)

s3 = support.s3  # fixture, bound by name so pytest finds it in this module

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def week(viewer, s, week_start=None, expect=200, **params):
    query = "&".join([f"{k}={v}" for k, v in ({"week_start": week_start, **params} if week_start else params).items()])
    r = client_for(viewer).get(ws_url(s, f"time/timesheet/?{query}"))
    assert r.status_code == expect, r.content
    return r.data if expect == 200 else r.json()


def at(day, hh, mm=0):
    """UTC instant for local (Dhaka) wall-clock ``hh:mm`` on ``day``."""
    return utc(day.year, day.month, day.day, hh, mm) - timedelta(hours=6)


def log(s, user, day, minutes, issue=None, hh=9, **extra):
    start = at(day, hh)
    return entry(s, user, start, start + timedelta(minutes=minutes), issue=issue, **extra)


class TestGrid:
    def test_totals_subtotals_and_weekend_flag(self, s3):
        m = s3["member"]
        log(s3, m, MON, 120)  # Mon, project P
        log(s3, m, MON + timedelta(days=2), 60)  # Wed, P
        log(s3, m, MON + timedelta(days=2), 30, issue=s3["issue_q"], hh=13)  # Wed, Q
        log(s3, m, MON + timedelta(days=5), 45)  # Sat (weekend)
        with freeze_time(NOW):
            data = week(m, s3)
        assert data["week_start"] == MON and data["week_end"] == MON + timedelta(days=6)
        assert data["is_self"] is True and data["scoped"] is False
        assert [d["logged_minutes"] for d in data["days"]] == [120, 0, 90, 0, 0, 45, 0]
        assert [d["is_weekend"] for d in data["days"]] == [False] * 5 + [True, True]
        assert [d["weekday"] for d in data["days"]] == [1, 2, 3, 4, 5, 6, 7]
        assert data["totals"]["logged"] == 255 and data["totals"]["weekend"] == 45
        by_project = {p["project"]["identifier"]: p for p in data["projects"]}
        assert by_project["PP"]["total"] == 225 and by_project["QQ"]["total"] == 30
        assert by_project["PP"]["minutes"] == [120, 0, 60, 0, 0, 45, 0]
        (row,) = by_project["QQ"]["issues"]
        assert row["id"] == str(s3["issue_q"].id) and row["minutes"][2] == 30 and row["total"] == 30
        assert row["ref"] == f"QQ-{s3['issue_q'].sequence_id}" and row["is_deleted"] is False
        # Each project subtotal equals the sum of its rows; day totals equal the sum of projects.
        for p in data["projects"]:
            assert p["total"] == sum(i["total"] for i in p["issues"])
        assert [d["logged_minutes"] for d in data["days"]] == [
            sum(p["minutes"][i] for p in data["projects"]) for i in range(7)
        ]

    def test_expected_is_8h_on_weekdays_only(self, s3):
        with freeze_time(NOW):
            data = week(s3["member"], s3)
        assert [d["expected_minutes"] for d in data["days"]] == [480] * 5 + [0, 0]
        assert data["totals"]["expected"] == 2400
        # Wed noon: Mon-Wed are due so far.
        assert data["totals"]["expected_to_date"] == 1440
        assert [d["is_today"] for d in data["days"]] == [False, False, True, False, False, False, False]
        assert [d["is_future"] for d in data["days"]] == [False] * 3 + [True] * 4
        assert data["settings"]["minutes_per_day"] == 480

    def test_expected_follows_work_days_capacity_and_tracking(self, s3):
        from plane.db.models import ResourceCapacity, TimeTrackingMemberStatus

        with freeze_time(NOW):
            set_settings(s3["workspace"], work_days=[1, 2, 3, 4], minutes_per_day=360)
        ResourceCapacity.objects.create(workspace=s3["workspace"], user=s3["member2"], weekly_capacity=1200)
        TimeTrackingMemberStatus.objects.create(
            workspace=s3["workspace"], user=s3["pa"], is_tracked=False, effective_from=date(2026, 6, 24)
        )
        with freeze_time(NOW):
            member = week(s3["member"], s3)
            member2 = week(s3["member2"], s3)
            pa = week(s3["pa"], s3)
        assert [d["expected_minutes"] for d in member["days"]] == [360] * 4 + [0] * 3
        assert [d["is_weekend"] for d in member["days"]] == [False] * 4 + [True] * 3  # Fri is off now
        assert [d["expected_minutes"] for d in member2["days"]] == [300] * 4 + [0] * 3
        # Not tracked from Wed: only Mon and Tue are expected.
        assert [d["expected_minutes"] for d in pa["days"]] == [360, 360, 0, 0, 0, 0, 0]
        assert [d["tracked"] for d in pa["days"]] == [True, True] + [False] * 5

    def test_week_boundary_is_monday_to_sunday_in_workspace_tz(self, s3):
        m = s3["member"]
        # Sun 23:30 local (previous week) and Mon 00:30 local: 17:30 UTC / 18:30 UTC Sunday.
        entry(s3, m, utc(2026, 6, 21, 17, 30), utc(2026, 6, 21, 17, 40))  # Sun 23:30 -> prev week
        entry(s3, m, utc(2026, 6, 21, 18, 30), utc(2026, 6, 21, 19, 0))  # Mon 00:30 -> this week
        entry(s3, m, utc(2026, 6, 28, 17, 30), utc(2026, 6, 28, 17, 50))  # Sun 23:30 -> this week
        entry(s3, m, utc(2026, 6, 28, 18, 30), utc(2026, 6, 28, 18, 45))  # Mon 00:30 -> next week
        with freeze_time(NOW):
            this = week(m, s3)
            prev = week(m, s3, "2026-06-15")
            nxt = week(m, s3, "2026-06-29")
        assert this["totals"]["logged"] == 50
        assert [d["logged_minutes"] for d in this["days"]] == [30, 0, 0, 0, 0, 0, 20]
        assert prev["totals"]["logged"] == 10 and nxt["totals"]["logged"] == 15
        assert this["timezone"] == "Asia/Dhaka"

    def test_any_date_snaps_to_its_monday_and_default_is_current_week(self, s3):
        with freeze_time(NOW):
            assert week(s3["member"], s3)["week_start"] == MON
            assert week(s3["member"], s3, "2026-06-28")["week_start"] == MON  # a Sunday
            assert week(s3["member"], s3, "2026-06-22")["week_start"] == MON
        # 20:00 UTC Sun is already Monday in Dhaka.
        with freeze_time(utc(2026, 6, 28, 20, 0)):
            assert week(s3["member"], s3)["week_start"] == date(2026, 6, 29)

    @pytest.mark.parametrize("bad", ["nope", "2026-13-01", "1999-12-27", "2101-01-04", "2026-6-1", "20260601"])
    def test_bad_week_start_is_400(self, s3, bad):
        with freeze_time(NOW):
            body = week(s3["member"], s3, bad, expect=400)
        assert body["code"] == "invalid_filter"

    def test_midnight_split_run_lands_on_both_days(self, s3):
        m = s3["member"]
        with freeze_time(utc(2026, 6, 24, 15, 0)):  # Wed 21:00 Dhaka
            set_settings(s3["workspace"], **IDLE_OFF)
            c = client_for(m)
            assert (
                c.post(ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json").status_code
                == 201
            )
        with freeze_time(utc(2026, 6, 24, 19, 0)):  # Thu 01:00 Dhaka: 3 h before + 1 h after midnight
            assert c.post(ws_url(s3, "time/timer/stop/"), {}, format="json").status_code == 201
            data = week(m, s3)
        assert [d["logged_minutes"] for d in data["days"]][2:4] == [180, 60]
        thu, wed = data["entries_by_date"]
        assert (thu["date"], wed["date"]) == (date(2026, 6, 25), date(2026, 6, 24))
        assert (thu["total_minutes"], wed["total_minutes"]) == (60, 180)
        assert thu["groups"][0]["entries"][0]["source"] == "timer"


class TestEntriesByDate:
    def test_two_entries_on_one_task_group_with_badge_span_and_total(self, s3):
        m = s3["member"]
        day = MON + timedelta(days=1)
        log(s3, m, day, 60, hh=13)  # 13:00-14:00
        log(s3, m, day, 30, hh=15)  # 15:00-15:30
        log(s3, m, day, 20, issue=s3["issue_q"], hh=10)
        with freeze_time(NOW):
            data = week(m, s3)
        (d,) = data["entries_by_date"]
        assert d["date"] == day and d["total_minutes"] == 110
        group = next(g for g in d["groups"] if g["issue"]["id"] == str(s3["issue"].id))
        assert group["count"] == 2 and group["total_minutes"] == 90
        assert group["span_started_at"] == at(day, 13) and group["span_ended_at"] == at(day, 15, 30)
        assert [e["duration"] for e in group["entries"]] == [60, 30]
        single = next(g for g in d["groups"] if g["issue"]["id"] == str(s3["issue_q"].id))
        assert single["count"] == 1 and single["total_minutes"] == 20

    def test_dates_are_newest_first_and_manual_entries_have_no_span(self, s3):
        m = s3["member"]
        log(s3, m, MON, 10)
        IssueWorklog.objects.create(
            workspace=s3["workspace"],
            project=s3["p"],
            issue=s3["issue"],
            logged_by=m,
            duration=25,
            logged_date=MON + timedelta(days=3),
            source="manual",
            description="called client",
        )
        with freeze_time(NOW):
            data = week(m, s3)
        assert [d["date"] for d in data["entries_by_date"]] == [MON + timedelta(days=3), MON]
        (g,) = data["entries_by_date"][0]["groups"]
        assert g["span_started_at"] is None and g["span_ended_at"] is None
        assert g["entries"][0]["description"] == "called client" and g["entries"][0]["source"] == "manual"

    def test_entry_flags_and_no_billing_fields(self, s3):
        m = s3["member"]
        log(s3, m, MON, 30, auto_stopped=True, is_locked=True, approval_status="approved", billable_rate=99)
        with freeze_time(NOW):
            data = week(m, s3)
        e = data["entries_by_date"][0]["groups"][0]["entries"][0]
        assert e["auto_stopped"] is True and e["is_locked"] is True and e["approval_status"] == "approved"
        assert "billable" not in str(data).lower() and "rate" not in " ".join(e)
        assert "currency" not in str(data).lower()

    def test_deleted_work_item_keeps_its_time_with_a_snapshot_label(self, s3):
        m = s3["member"]
        gone = make_issue(s3["p"], s3["issue"].state, "Doomed task", s3["wsadmin"])
        log(s3, m, MON, 40, issue=gone)
        snap_ref = IssueWorklog.objects.get(issue=gone).issue_ref
        from plane.db.models import Issue

        Issue.all_objects.filter(pk=gone.pk).delete()  # hard delete
        with freeze_time(NOW):
            data = week(m, s3)
        (row,) = data["projects"][0]["issues"]
        assert row["is_deleted"] is True and row["total"] == 40
        assert row["ref"] == snap_ref and row["name"] == "Doomed task"
        assert data["totals"]["logged"] == 40

    def test_pending_request_marks_the_entry_and_is_listed(self, s3):
        from .time_tracking_s3_support import submit

        m = s3["member"]
        wl = log(s3, m, MON, 60)
        with freeze_time(NOW):
            r = submit(client_for(m), s3, type="deletion", worklog_id=str(wl.id), reason="wrong task")
            assert r.status_code == 201, r.content
            data = week(m, s3)
        e = data["entries_by_date"][0]["groups"][0]["entries"][0]
        assert e["pending_request_id"] == r.data["id"]
        assert [p["id"] for p in data["pending_requests"]] == [r.data["id"]]
        assert data["pending_requests"][0]["can_withdraw"] is True

    def test_entries_are_capped(self, s3, monkeypatch):
        from plane.app.views.time_tracking import timesheet_week

        monkeypatch.setattr(timesheet_week, "MAX_ENTRIES", 3)
        for i in range(5):
            log(s3, s3["member"], MON, 5, hh=8 + i)
        with freeze_time(NOW):
            data = week(s3["member"], s3)
        assert data["entries_truncated"] is True
        assert sum(g["count"] for d in data["entries_by_date"] for g in d["groups"]) == 3
        assert data["totals"]["logged"] == 25  # the grid still counts everything


class TestSettingsAndApproval:
    def test_manual_entry_flag_and_can_add_time(self, s3):
        with freeze_time(NOW):
            off = week(s3["member"], s3)
            admin_off = week(s3["wsadmin"], s3)
            set_settings(s3["workspace"], allow_member_manual_entry=True)
            on = week(s3["member"], s3)
        assert off["settings"]["allow_member_manual_entry"] is False
        assert off["viewer"]["can_add_time"] is False
        assert admin_off["viewer"]["can_add_time"] is True and admin_off["viewer"]["is_workspace_admin"] is True
        assert on["settings"]["allow_member_manual_entry"] is True and on["viewer"]["can_add_time"] is True

    def test_settings_get_exposes_the_flag_to_members_but_not_billing(self, s3):
        with freeze_time(NOW):
            set_settings(s3["workspace"], allow_member_manual_entry=True, billing_enabled=True)
            r = client_for(s3["member"]).get(ws_url(s3, "time/settings/"))
        assert r.status_code == 200
        assert r.data["allow_member_manual_entry"] is True
        assert "billing_enabled" not in r.data

    def test_approval_block_and_submit_is_400_when_off(self, s3):
        m = s3["member"]
        log(s3, m, MON, 60)
        with freeze_time(NOW):
            data = week(m, s3)
            assert data["approval"]["enabled"] is False and data["approval"]["status"] == "open"
            r = client_for(m).post(
                ws_url(s3, "timesheets/submit/"),
                {"period_start": "2026-06-22", "period_end": "2026-06-28"},
                format="json",
            )
        assert r.status_code == 400 and r.json()["code"] == "weekly_approval_disabled"

    def test_approval_status_follows_the_submitted_timesheet(self, s3):
        m = s3["member"]
        log(s3, m, MON, 60)
        with freeze_time(NOW):
            set_settings(s3["workspace"], weekly_approval_enabled=True)
            r = client_for(m).post(
                ws_url(s3, "timesheets/submit/"),
                {"period_start": "2026-06-22", "period_end": "2026-06-28"},
                format="json",
            )
            assert r.status_code in (200, 201), r.content
            data = week(m, s3)
            other = week(m, s3, "2026-06-29")
        assert data["approval"]["enabled"] is True and data["approval"]["status"] == "submitted"
        assert data["approval"]["timesheet_id"] == str(Timesheet.objects.get(user=m).id)
        assert other["approval"]["status"] == "open"
