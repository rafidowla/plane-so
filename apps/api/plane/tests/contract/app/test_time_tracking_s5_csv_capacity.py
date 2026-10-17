# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S5 — entry-row CSV (formula safety, sum check, columns,
billing) and the per-member capacity override API."""

from decimal import Decimal

import pytest
from freezegun import freeze_time

from plane.db.models import ResourceCapacity, TimeTrackingAuditLog, TimeTrackingSetting

from .time_tracking_s2_support import _load_urlconf, client_for, make_issue, make_user, utc, ws_url  # noqa: F401 (autouse fixture)
from .time_tracking_s5_support import AFTER_RANGE, H, OCT, csv_rows, log, report

pytestmark = [pytest.mark.contract, pytest.mark.django_db]

HEADER = ["Date", "Member", "Project", "Task ID", "Task title", "Start", "End", "Hours", "Minutes", "Source", "Note"]


class TestEntryCsv:
    def test_columns_local_times_and_sources(self, s2):
        log(s2, s2["user"], OCT[1], 90, note="Pairing", source="timer", start=utc(2026, 10, 1, 3, 0))
        log(s2, s2["user"], OCT[2], 30)
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(s2["user"], s2)
        assert rows[0] == HEADER
        s2["issue"].refresh_from_db()
        first = dict(zip(HEADER, rows[1]))
        assert first["Date"] == "2026-10-01"
        assert first["Task ID"] == f"TTP-{s2['issue'].sequence_id}"
        assert first["Task title"] == "Issue 1"
        assert (first["Start"], first["End"]) == ("2026-10-01 09:00", "2026-10-01 10:30")  # Dhaka
        assert (first["Hours"], first["Minutes"], first["Source"], first["Note"]) == ("1.5", "90", "timer", "Pairing")
        assert dict(zip(HEADER, rows[2]))["Source"] == "manual"

    def test_formula_injection_is_neutralized(self, s2):
        evil_issue = make_issue(s2["project"], s2["states"]["todo"], '=HYPERLINK("http://x","y")', s2["user"])
        evil_user = make_user(s2["workspace"], 15, "Evil")
        evil_user.display_name = "@SUM(1+1)"
        evil_user.save()
        log(s2, evil_user, OCT[1], 10, issue=evil_issue, note="+cmd|' /C calc'!A0")
        log(s2, s2["user"], OCT[1], 10, note="-2+3")
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(s2["user"], s2)
        cells = [c for r in rows for c in r]
        for payload in ('=HYPERLINK("http://x","y")', "@SUM(1+1)", "+cmd|' /C calc'!A0", "-2+3"):
            assert payload not in cells
            assert f"'{payload}" in cells
        assert not any(c[:1] in ("=", "+", "-", "@", "\t", "\r") for c in cells)

    def test_hours_column_sums_to_the_logged_tile(self, s2):
        members = [s2["user"], make_user(s2["workspace"], 15, "A"), make_user(s2["workspace"], 15, "B")]
        odd = [1, 7, 13, 61, 89, 100, 241, 37, 59, 2]
        n = 0
        for d in range(1, 8):
            for m in members:
                for minutes in odd:
                    log(s2, m, OCT[d], minutes + d)
                    n += 1
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(s2["user"], s2)
            tiles = report(s2["user"], s2, user="team")["tiles"]
        body = [dict(zip(HEADER, r)) for r in rows[1:]]
        assert len(body) == n == tiles["entry_count"]
        assert sum(int(r["Minutes"]) for r in body) == tiles["logged_minutes"]
        hours = sum(Decimal(r["Hours"]) for r in body)
        assert abs(hours - Decimal(tiles["logged_minutes"]) / 60) < Decimal("0.1")
        assert round(hours, 1) == round(Decimal(tiles["logged_minutes"]) / 60, 1)

    def test_export_param_on_the_json_path_and_deleted_items(self, s2):
        gone = make_issue(s2["project"], s2["states"]["todo"], "Gone item", s2["user"])
        log(s2, s2["user"], OCT[1], 15, issue=gone)
        gone.delete()  # soft delete
        with freeze_time(AFTER_RANGE):
            r = client_for(s2["user"]).get(ws_url(s2, "time/report/?from=2026-10-01&to=2026-10-07&export=csv"))
        assert r.status_code == 200 and r["Content-Type"] == "text/csv"
        assert "(deleted) Gone item" in r.content.decode()

    def test_billing_columns_only_when_billing_on(self, s2):
        log(s2, s2["user"], OCT[1], 30, rate="120.00")
        with freeze_time(AFTER_RANGE):
            assert csv_rows(s2["user"], s2)[0] == HEADER
        TimeTrackingSetting.objects.update_or_create(workspace=s2["workspace"], defaults={"billing_enabled": True})
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(s2["user"], s2)
        assert rows[0] == HEADER + ["Billable", "Rate", "Currency", "Amount"]
        assert rows[1][-4:] == ["yes", "120.00", "USD", "60.00"]

    def test_row_cap(self, s2, monkeypatch):
        from plane.app.views.time_tracking import report as report_module

        monkeypatch.setattr(report_module, "MAX_CSV_ROWS", 1)
        log(s2, s2["user"], OCT[1], 10)
        log(s2, s2["user"], OCT[2], 10)
        with freeze_time(AFTER_RANGE):
            r = client_for(s2["user"]).get(ws_url(s2, "time/report.csv?from=2026-10-01&to=2026-10-07"))
        assert r.status_code == 400 and r.json()["code"] == "too_many_rows"


def _capacity_url(s, user):
    return ws_url(s, f"time/members/{user.id}/capacity/")


class TestCapacityApi:
    def test_set_and_clear_the_override(self, s2):
        member = make_user(s2["workspace"], 15, "Part")
        admin = client_for(s2["user"])
        r = admin.put(_capacity_url(s2, member), {"weekly_minutes": 20 * H}, format="json")
        assert r.status_code == 200, r.content
        assert r.data == {"user_id": str(member.id), "weekly_capacity_minutes": 1200, "default_weekly_minutes": 2400}
        with freeze_time(AFTER_RANGE):
            assert report(s2["user"], s2, user=member.id)["tiles"]["expected_minutes"] == 20 * H
        listed = admin.get(ws_url(s2, "time/members/")).data
        assert listed["default_weekly_minutes"] == 2400
        assert next(m for m in listed["members"] if m["user_id"] == str(member.id))["weekly_capacity_minutes"] == 1200
        audits = TimeTrackingAuditLog.objects.filter(workspace=s2["workspace"], target_type="setting")
        audit = audits.latest("created_at")
        assert audit.after == {"user_id": str(member.id), "weekly_capacity": 1200}

        r = admin.put(_capacity_url(s2, member), {"weekly_minutes": None}, format="json")
        assert r.status_code == 200
        assert ResourceCapacity.objects.get(user=member).weekly_capacity is None
        with freeze_time(AFTER_RANGE):
            assert report(s2["user"], s2, user=member.id)["tiles"]["expected_minutes"] == 40 * H

    def test_validation_and_idor(self, s2):
        member = make_user(s2["workspace"], 15, "Part")
        admin = client_for(s2["user"])
        for bad in (-1, 10081, "20", 1.5, True):
            r = admin.put(_capacity_url(s2, member), {"weekly_minutes": bad}, format="json")
            assert r.status_code == 400 and r.json()["code"] == "invalid_capacity", bad
        assert admin.put(_capacity_url(s2, member), {}, format="json").json()["code"] == "invalid_capacity"
        outsider = make_user(s2["workspace"], None, "Outsider")  # not in this workspace
        guest = make_user(s2["workspace"], 5, "Guest")
        for who in (outsider, guest):
            r = admin.put(_capacity_url(s2, who), {"weekly_minutes": 600}, format="json")
            assert r.status_code == 400 and r.json()["code"] == "invalid_user"
        assert not ResourceCapacity.objects.filter(user__in=[outsider, guest]).exists()

    def test_members_cannot_set_capacity(self, s2):
        member = make_user(s2["workspace"], 15, "Part")
        r = client_for(member).put(_capacity_url(s2, member), {"weekly_minutes": 600}, format="json")
        assert r.status_code == 403
        assert not ResourceCapacity.objects.filter(user=member).exists()

    def test_resource_capacity_upsert_checks_membership_and_drops_rates_while_billing_off(self, s2):
        admin = client_for(s2["user"])
        url = ws_url(s2, "resource-capacities/")
        outsider = make_user(s2["workspace"], None, "Outsider")
        r = admin.post(url, {"user": str(outsider.id), "weekly_capacity": 600}, format="json")
        assert r.status_code == 400 and r.json()["code"] == "invalid_user"
        r = admin.post(url, {"user": "not-a-uuid", "weekly_capacity": 600}, format="json")
        assert r.status_code == 400

        member = make_user(s2["workspace"], 15, "Part")
        r = admin.post(url, {"user": str(member.id), "weekly_capacity": 600, "cost_rate": "50.00"}, format="json")
        assert r.status_code == 200, r.content
        assert "cost_rate" not in r.data
        row = ResourceCapacity.objects.get(user=member)
        assert row.weekly_capacity == 600 and row.cost_rate in (None, Decimal("0"))
        r = admin.post(url, {"user": str(member.id), "weekly_capacity": 99999}, format="json")
        assert r.status_code == 400 and r.json()["code"] == "invalid_capacity"
