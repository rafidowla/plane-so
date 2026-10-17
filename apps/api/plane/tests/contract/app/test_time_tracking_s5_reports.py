# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S5 — Reports v2: tiles, Expected / Expected-to-date,
series, team and member tables, weekend, tracking history, capacity, billing."""

import pytest
from freezegun import freeze_time

from plane.db.models import TimeTrackingSetting

from .time_tracking_s2_support import _load_urlconf, make_user, set_settings, utc  # noqa: F401 (autouse fixture)
from .time_tracking_s5_support import AFTER_RANGE, H, OCT, log, member_row, report, set_capacity, set_tracking

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _team(s, n):
    """The admin plus ``n`` workspace members (all tracked, default work week)."""
    return [make_user(s["workspace"], 15, f"M{i}") for i in range(n)]


class TestExpected:
    def test_one_member_1_to_7_oct_is_40h(self, s2):
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        tiles = data["tiles"]
        assert tiles["expected_minutes"] == 40 * H
        assert tiles["expected_to_date_minutes"] == 40 * H
        assert tiles["logged_minutes"] == 0
        assert tiles["balance_minutes"] == -40 * H
        assert data["user"]["user_id"] == str(s2["user"].id)
        assert data["work_week"] == {"work_days": [1, 2, 3, 4, 5], "minutes_per_day": 480, "weekly_minutes": 2400}

    def test_team_of_seven_is_280h_and_logged_is_the_sum_of_rows(self, s2):
        members = _team(s2, 6)  # + the admin = 7 tracked people
        for i, m in enumerate([s2["user"], *members]):
            log(s2, m, OCT[1], 60 + i)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert data["tiles"]["member_count"] == 7
        assert data["tiles"]["expected_minutes"] == 280 * H
        assert len(data["members"]) == 7
        assert all(m["expected_minutes"] == 40 * H for m in data["members"])
        assert data["tiles"]["logged_minutes"] == sum(m["logged_minutes"] for m in data["members"]) == 7 * 60 + 21

    def test_weekend_counts_in_logged_and_weekend_not_expected(self, s2):
        log(s2, s2["user"], OCT[2], 8 * H)  # Fri
        log(s2, s2["user"], OCT[3], 2 * H)  # Sat
        log(s2, s2["user"], OCT[4], 30)  # Sun
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        tiles = data["tiles"]
        assert tiles["logged_minutes"] == 10 * H + 30
        assert tiles["weekend_minutes"] == 2 * H + 30
        assert tiles["expected_minutes"] == 40 * H
        by_day = {str(p["date"]): p for p in data["series"]}
        assert by_day["2026-10-03"]["is_working_day"] is False
        assert by_day["2026-10-03"]["expected_minutes"] == 0
        assert by_day["2026-10-03"]["logged_minutes"] == 2 * H
        assert by_day["2026-10-02"]["expected_minutes"] == 8 * H

    def test_series_running_totals(self, s2):
        log(s2, s2["user"], OCT[1], 5 * H)
        log(s2, s2["user"], OCT[5], 3 * H)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        series = data["series"]
        assert [str(p["date"]) for p in series] == [f"2026-10-0{d}" for d in range(1, 8)]
        assert [p["cumulative_logged_minutes"] for p in series] == [300, 300, 300, 300, 480, 480, 480]
        assert [p["cumulative_expected_minutes"] for p in series] == [480, 960, 960, 960, 1440, 1920, 2400]

    def test_expected_to_date_counts_only_days_up_to_local_today(self, s2):
        # 00:30 Tue 6 Oct in Dhaka is still Mon 5 Oct 18:30 UTC: today is the 6th locally.
        with freeze_time(utc(2026, 10, 5, 18, 30)):
            data = report(s2["user"], s2, user=s2["user"].id)
        assert data["today"].isoformat() == "2026-10-06"
        assert data["tiles"]["expected_minutes"] == 40 * H
        assert data["tiles"]["expected_to_date_minutes"] == 32 * H  # Thu, Fri, Mon, Tue
        assert data["tiles"]["balance_to_date_minutes"] == -32 * H
        future = [str(p["date"]) for p in data["series"] if p["is_future"]]
        assert future == ["2026-10-07"]

    def test_default_range_is_the_current_week(self, s2):
        with freeze_time(utc(2026, 10, 7, 6, 0)):  # Wed 7 Oct
            data = report(s2["user"], s2, frm="", to="")
        assert (str(data["from"]), str(data["to"])) == ("2026-10-05", "2026-10-11")

    def test_custom_work_week(self, s2):
        set_settings(s2["workspace"], work_days=[1, 2, 3, 4], minutes_per_day=600)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        # Thu 1 + Mon 5 + Tue 6 + Wed 7 = 4 working days x 10 h; Fri now a weekend day.
        assert data["tiles"]["expected_minutes"] == 40 * H
        assert {str(p["date"]): p["is_working_day"] for p in data["series"]}["2026-10-02"] is False


class TestTrackingHistory:
    def test_tracking_turned_off_mid_range(self, s2):
        member = make_user(s2["workspace"], 15, "Leaver")
        set_tracking(s2, member, False, OCT[6])
        log(s2, member, OCT[5], 4 * H)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        row = member_row(data, member)
        assert row["expected_minutes"] == 24 * H  # Thu, Fri, Mon
        assert row["logged_minutes"] == 4 * H
        assert row["is_tracked_now"] is False
        assert data["tiles"]["expected_minutes"] == (40 + 24) * H

    def test_untracked_for_the_whole_range_is_missing_from_the_team(self, s2):
        ceo = make_user(s2["workspace"], 15, "Ceo")
        set_tracking(s2, ceo, False, OCT[1].replace(day=1, month=9))
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert str(ceo.id) not in {m["user_id"] for m in data["members"]}
        assert data["tiles"]["member_count"] == 1
        # Still selectable in Reports, marked "not tracked now".
        person = next(p for p in data["people"] if p["user_id"] == str(ceo.id))
        assert person["is_tracked_now"] is False

    def test_turned_back_on(self, s2):
        member = make_user(s2["workspace"], 15, "Back")
        set_tracking(s2, member, False, OCT[1].replace(month=9))
        set_tracking(s2, member, True, OCT[5])
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=member.id)
        assert data["tiles"]["expected_minutes"] == 24 * H  # Mon, Tue, Wed

    def test_guest_time_never_counts_expected(self, s2):
        guest = make_user(s2["workspace"], 5, "Guest")
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert str(guest.id) not in {p["user_id"] for p in data["people"]}
        assert data["tiles"]["expected_minutes"] == 40 * H


class TestCapacity:
    def test_20h_per_week_is_4h_per_day(self, s2):
        member = make_user(s2["workspace"], 15, "Part")
        set_capacity(s2, member, 20 * H)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=member.id)
        assert data["user"]["weekly_capacity_minutes"] == 20 * H
        assert data["tiles"]["expected_minutes"] == 20 * H
        per_day = {str(p["date"]): p["expected_minutes"] for p in data["series"]}
        assert per_day["2026-10-01"] == 4 * H and per_day["2026-10-03"] == 0

    def test_null_override_follows_the_work_week(self, s2):
        member = make_user(s2["workspace"], 15, "Full")
        set_capacity(s2, member, None)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=member.id)
        assert data["tiles"]["expected_minutes"] == 40 * H

    def test_odd_override_rounds_only_at_output(self, s2):
        member = make_user(s2["workspace"], 15, "Odd")
        set_capacity(s2, member, 1001)  # 200.2 min/day
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=member.id, frm="2026-10-05", to="2026-10-11")
        assert data["tiles"]["expected_minutes"] == 1001


class TestMemberView:
    def test_weeks_table_inside_the_range(self, s2):
        log(s2, s2["user"], OCT[2], 2 * H)
        log(s2, s2["user"], OCT[6], 3 * H)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        weeks = [(str(w["from"]), str(w["to"]), w["logged_minutes"], w["expected_minutes"]) for w in data["weeks"]]
        assert weeks == [("2026-10-01", "2026-10-04", 2 * H, 16 * H), ("2026-10-05", "2026-10-07", 3 * H, 24 * H)]
        assert "members" not in data

    def test_projects_and_tasks_breakdown(self, s2):
        log(s2, s2["user"], OCT[1], 90)
        log(s2, s2["user"], OCT[2], 30)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id)
        assert data["projects"]["results"][0]["logged_minutes"] == 120
        assert data["projects"]["other_projects"]["logged_minutes"] == 0
        task = data["tasks"]["results"][0]
        assert task["issue_id"] == str(s2["issue"].id)
        assert task["logged_minutes"] == 120
        assert task["name"].endswith("Issue 1")

    def test_task_limit_truncates(self, s2):
        from .time_tracking_s2_support import make_issue

        for i in range(3):
            issue = make_issue(s2["project"], s2["states"]["todo"], f"T{i}", s2["user"])
            log(s2, s2["user"], OCT[1], 10 + i, issue=issue)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user=s2["user"].id, task_limit=2)
        assert len(data["tasks"]["results"]) == 2
        assert data["tasks"]["truncated"] is True
        assert data["tasks"]["results"][0]["logged_minutes"] == 12


class TestValidation:
    @pytest.mark.parametrize(
        "frm,to,code",
        [
            ("2026-10-07", "2026-10-01", "invalid_range"),
            ("2025-01-01", "2026-10-01", "invalid_range"),
            ("x", "", "invalid_range"),
        ],
    )
    def test_bad_range(self, s2, frm, to, code):
        body = report(s2["user"], s2, frm=frm, to=to, expect=400)
        assert body["code"] == code

    def test_bad_task_limit_and_user(self, s2):
        assert report(s2["user"], s2, task_limit=0, expect=400)["code"] == "invalid_limit"
        assert report(s2["user"], s2, task_limit=501, expect=400)["code"] == "invalid_limit"
        assert report(s2["user"], s2, user="nope", expect=400)["code"] == "invalid_user"

    def test_366_days_is_allowed(self, s2):
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, frm="2025-10-08", to="2026-10-08")
        assert len(data["series"]) == 366


class TestBilling:
    def test_billing_fields_absent_when_billing_is_off(self, s2):
        log(s2, s2["user"], OCT[1], 60, rate="100.00")
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert data["billing_enabled"] is False
        for blob in (data["tiles"], data["members"][0]):
            assert not {"billable_minutes", "non_billable_minutes", "billable_amount"} & set(blob)

    def test_billing_fields_present_when_on(self, s2):
        TimeTrackingSetting.objects.update_or_create(workspace=s2["workspace"], defaults={"billing_enabled": True})
        log(s2, s2["user"], OCT[1], 60, rate="100.00")
        log(s2, s2["user"], OCT[2], 30, billable=False)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert data["tiles"]["billable_minutes"] == 60
        assert data["tiles"]["non_billable_minutes"] == 30
        assert data["tiles"]["billable_amount"] == 100.0
        assert member_row(data, s2["user"])["billable_amount"] == 100.0

    def test_legacy_report_ignores_billable_filter_when_off(self, s2):
        from .time_tracking_s2_support import client_for, ws_url

        log(s2, s2["user"], OCT[1], 60)
        log(s2, s2["user"], OCT[2], 30, billable=False)
        r = client_for(s2["user"]).get(ws_url(s2, "time-report/?group_by=project&billable=true"))
        assert r.status_code == 200
        assert r.data["totals"]["total_minutes"] == 90
        assert "billable_minutes" not in r.data["groups"][0]
