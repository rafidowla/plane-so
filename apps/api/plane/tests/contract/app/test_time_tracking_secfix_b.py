# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 secfix B — regression tests for review findings 4, 7, 8, 9 and 10.

(Finding 5, the estimate gate, is covered in ``test_time_tracking_s6_estimates.py``.)

* 4  — billing fields (``is_billable`` / ``billable_rate`` / ``currency``) are visible only to
       a workspace admin or an admin of the entry's project, and only while billing is on.
* 7  — entry create/update validate the billing fields and ignore them for everyone else.
* 8  — the audit log is append-only in the database; the 0128 reverse keeps orphaned time.
* 9  — the legacy ``time-report/`` endpoint has the 366-day cap and a capped issue CSV.
* 10 — the sweep stops the timer of a user who left the project / workspace.
"""

import importlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from freezegun import freeze_time

from plane.app.views.time_tracking import report as report_module
from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import (
    IssueWorklog,
    ProjectMember,
    TimeTrackingAuditLog,
    WorkspaceMember,
    WorklogTimer,
)
from plane.utils.time_tracking import billing

from . import time_tracking_s3_support as support
from .time_tracking_s2_support import IDLE_OFF, client_for, set_settings, ws_url
from .time_tracking_s3_support import NOW, _load_urlconf, entry, utc  # noqa: F401 (_load_urlconf is an autouse fixture)

s3 = support.s3

RATE_KEYS = ("is_billable", "billable_rate", "currency")
START, END = utc(2026, 6, 24, 2, 0), utc(2026, 6, 24, 3, 0)  # Wed 08:00-09:00 Dhaka


def _billing(s, on=True):
    set_settings(s["workspace"], billing_enabled=on, allow_member_manual_entry=True, **IDLE_OFF)


def _rated(s, user, rate="75.00", **extra):
    return entry(s, user, START, END, billable_rate=Decimal(rate), currency="EUR", **extra)


def _worklogs_url(s, issue=None):
    issue = issue or s["issue"]
    return ws_url(s, f"projects/{issue.project_id}/issues/{issue.id}/worklogs/")


def _has_rates(payload):
    return any(k in payload for k in RATE_KEYS)


def _create(user, s, **extra):
    data = {
        "issue_id": str(s["issue"].id),
        "started_at": START.isoformat(),
        "ended_at": END.isoformat(),
        **extra,
    }
    with freeze_time(NOW):
        return client_for(user).post(ws_url(s, "time/entries/"), data, format="json")


def _patch(user, s, wl, **data):
    with freeze_time(NOW):
        return client_for(user).patch(ws_url(s, f"time/entries/{wl.id}/"), data, format="json")


# ---------------------------------------------------------------------------
# Finding 4 — who sees the billing fields
# ---------------------------------------------------------------------------


class TestRateVisibility:
    @pytest.mark.parametrize("viewer", ["wsadmin", "pa"])
    def test_admins_see_rates_when_billing_is_on(self, s3, viewer):
        _billing(s3)
        _rated(s3, s3["member"])
        rows = client_for(s3[viewer]).get(_worklogs_url(s3)).json()
        assert rows and all(r["billable_rate"] == "75.00" and r["currency"] == "EUR" for r in rows)

    @pytest.mark.parametrize("viewer", ["member", "member2"])
    def test_members_never_see_rates(self, s3, viewer):
        _billing(s3)
        _rated(s3, s3["member"])
        rows = client_for(s3[viewer]).get(_worklogs_url(s3)).json()
        assert rows and not any(_has_rates(r) for r in rows)

    def test_nobody_sees_rates_while_billing_is_off(self, s3):
        _billing(s3, on=False)
        _rated(s3, s3["member"])
        for viewer in ("wsadmin", "pa", "member"):
            rows = client_for(s3[viewer]).get(_worklogs_url(s3)).json()
            assert rows and not any(_has_rates(r) for r in rows), viewer

    def test_admin_of_another_project_is_not_a_rate_viewer(self, s3):
        _billing(s3)
        assert billing.can_manage_rates(s3["workspace"].id, s3["wsadmin"], s3["p"].id)
        assert billing.can_manage_rates(s3["workspace"].id, s3["pa"], s3["p"].id)
        assert not billing.can_manage_rates(s3["workspace"].id, s3["pa_q"], s3["p"].id)
        assert not billing.can_manage_rates(s3["workspace"].id, s3["member"], s3["p"].id)
        assert not billing.can_manage_rates(s3["workspace"].id, s3["pa"], s3["q"].id)

    def test_timer_start_and_stop_responses(self, s3):
        _billing(s3)
        responses = {}
        for who in ("member", "pa"):
            c = client_for(s3[who])
            with freeze_time(utc(2026, 6, 24, 3, 0)):
                started = c.post(ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json")
            assert started.status_code == 201, started.content
            with freeze_time(utc(2026, 6, 24, 4, 0)):
                stopped = c.post(ws_url(s3, "time/timer/stop/"), {}, format="json")
            assert stopped.status_code == 201, stopped.content
            responses[who] = stopped.json()
        assert not _has_rates(responses["member"])
        assert not any(_has_rates(p) for p in responses["member"]["pieces"])
        assert "billable_rate" in responses["pa"] and responses["pa"]["pieces"][-1]["currency"]

    def test_start_that_stops_the_previous_timer_hides_rates_from_a_member(self, s3):
        _billing(s3)
        other = support.make_issue(s3["p"], s3["issue"].state, "Second", s3["wsadmin"], assignees=[s3["member"]])
        c = client_for(s3["member"])
        with freeze_time(utc(2026, 6, 24, 3, 0)):
            c.post(ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json")
        with freeze_time(utc(2026, 6, 24, 4, 0)):
            r = c.post(ws_url(s3, "time/timer/start/"), {"issue_id": str(other.id)}, format="json")
        assert r.status_code == 201 and r.json()["stopped"]
        assert not _has_rates(r.json()["stopped"]["worklog"])

    def test_entry_create_and_update_responses(self, s3):
        _billing(s3)
        made = _create(s3["member"], s3)
        assert made.status_code == 201, made.content
        assert not _has_rates(made.json())
        wl = IssueWorklog.objects.get(pk=made.json()["id"])
        patched = _patch(s3["member"], s3, wl, description="note")
        assert patched.status_code == 200 and not _has_rates(patched.json())
        admin_made = _create(s3["pa"], s3)
        assert admin_made.status_code == 201 and "billable_rate" in admin_made.json()


class TestRateVisibilityInReports:
    """v2 report + CSV and the legacy report: rates are for admins of the project only."""

    @staticmethod
    def _world(s):
        _billing(s)
        _rated(s, s["member"])
        _rated(s, s["member"], issue=s["issue_q"])

    def test_v2_csv_masks_rates_of_projects_the_viewer_does_not_administer(self, s3):
        self._world(s3)
        url = ws_url(s3, "time/report.csv?from=2026-06-22&to=2026-06-28")
        rows = {}
        for who in ("wsadmin", "pa", "pa_q"):
            r = client_for(s3[who]).get(url)
            assert r.status_code == 200, (who, r.content)
            lines = r.content.decode().splitlines()
            rows[who] = [line for line in lines[1:] if line]
        assert all("75" in line for line in rows["wsadmin"]) and len(rows["wsadmin"]) == 2
        # pa administers P only: the Q row is present (as "Other projects") without its rate.
        assert sum("75" in line for line in rows["pa"]) == 1
        assert sum("75" in line for line in rows["pa_q"]) == 1

    def test_v2_json_report_has_no_billing_for_a_member(self, s3):
        self._world(s3)
        url = ws_url(s3, "time/report/?from=2026-06-22&to=2026-06-28")
        member = client_for(s3["member"]).get(url).json()
        assert (
            "billable_amount" not in str(member)
            and "billing_enabled" not in member
            or not member.get("billing_enabled")
        )
        admin = client_for(s3["wsadmin"]).get(url).json()
        assert "billable_amount" in str(admin)

    def test_legacy_report_has_no_billing_for_a_member(self, s3):
        self._world(s3)
        url = ws_url(s3, "time-report/?group_by=project&start_date=2026-06-22&end_date=2026-06-28")
        member = client_for(s3["member"]).get(url).json()
        assert member["billing_enabled"] is False
        assert "billable_amount" not in str(member)
        admin = client_for(s3["wsadmin"]).get(url).json()
        assert admin["billing_enabled"] is True and "billable_amount" in str(admin)


# ---------------------------------------------------------------------------
# Finding 7 — validation of the billing fields on create / update
# ---------------------------------------------------------------------------


class TestRateWrites:
    @pytest.mark.parametrize("bad", ["abc", "-1", "NaN", "Infinity", "99999999999999", True, [1], {"a": 1}])
    def test_bad_rate_is_a_400_on_create_and_update(self, s3, bad):
        _billing(s3)
        r = _create(s3["wsadmin"], s3, billable_rate=bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_rate", r.content
        wl = _rated(s3, s3["wsadmin"])
        r = _patch(s3["wsadmin"], s3, wl, billable_rate=bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_rate", r.content
        wl.refresh_from_db()
        assert wl.billable_rate == Decimal("75.00")

    @pytest.mark.parametrize("bad", ["DOLLARS", "U$", "12", "us", 5, ["USD"]])
    def test_bad_currency_is_a_400_on_create_and_update(self, s3, bad):
        _billing(s3)
        r = _create(s3["wsadmin"], s3, billable_rate="10", currency=bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_currency", r.content
        wl = _rated(s3, s3["wsadmin"])
        r = _patch(s3["wsadmin"], s3, wl, currency=bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_currency", r.content
        wl.refresh_from_db()
        assert wl.currency == "EUR"

    def test_good_values_are_normalised(self, s3):
        _billing(s3)
        r = _create(s3["pa"], s3, billable_rate="12.345", currency="eur", logged_by=str(s3["member"].id))
        assert r.status_code == 201, r.content
        wl = IssueWorklog.objects.get(pk=r.json()["id"])
        assert wl.billable_rate == Decimal("12.35") and wl.currency == "EUR"
        r = _patch(s3["pa"], s3, wl, billable_rate=0, currency="cad", is_billable="false")
        assert r.status_code == 200, r.content
        wl.refresh_from_db()
        assert (wl.billable_rate, wl.currency, wl.is_billable) == (Decimal("0.00"), "CAD", False)

    def test_a_member_cannot_set_rates(self, s3):
        _billing(s3)
        r = _create(s3["member"], s3, billable_rate="9999", currency="JPY", is_billable=False)
        assert r.status_code == 201, r.content
        wl = IssueWorklog.objects.get(pk=r.json()["id"])
        assert wl.billable_rate != Decimal("9999") and wl.currency != "JPY" and wl.is_billable is True
        before = (wl.billable_rate, wl.currency, wl.is_billable)
        # Own entries: the direct-edit rules already refuse the billing fields...
        r = _patch(s3["member"], s3, wl, billable_rate="9999", currency="JPY", is_billable=False)
        assert r.status_code == 403, r.content
        wl.refresh_from_db()
        assert (wl.billable_rate, wl.currency, wl.is_billable) == before
        # ...and the permitted part of an edit still applies.
        assert _patch(s3["member"], s3, wl, description="ok").status_code == 200

    def test_a_project_admin_of_another_project_cannot_set_rates(self, s3):
        _billing(s3)
        # pa_q administers Q, not P: creating in P for a P member is refused outright.
        r = _create(s3["pa_q"], s3, billable_rate="9999", logged_by=str(s3["member"].id))
        assert r.status_code == 403, r.content

    def test_a_member_sending_garbage_is_ignored_not_rejected(self, s3):
        _billing(s3)
        r = _create(s3["member"], s3, billable_rate="abc", currency="DOLLARS")
        assert r.status_code == 201, r.content

    def test_nobody_sets_rates_while_billing_is_off(self, s3):
        _billing(s3, on=False)
        r = _create(s3["wsadmin"], s3, billable_rate="500", currency="JPY")
        assert r.status_code == 201, r.content
        wl = IssueWorklog.objects.get(pk=r.json()["id"])
        assert wl.billable_rate != Decimal("500") and wl.currency != "JPY"
        wl2 = _rated(s3, s3["wsadmin"])
        r = _patch(s3["wsadmin"], s3, wl2, billable_rate="500", currency="JPY")
        assert r.status_code == 200, r.content
        wl2.refresh_from_db()
        assert (wl2.billable_rate, wl2.currency) == (Decimal("75.00"), "EUR")

    def test_rate_changes_are_audited(self, s3):
        _billing(s3)
        wl = _rated(s3, s3["wsadmin"])
        assert _patch(s3["wsadmin"], s3, wl, billable_rate="80").status_code == 200
        row = TimeTrackingAuditLog.objects.filter(target_id=wl.id).order_by("-created_at").first()
        assert row is not None and "billable_rate" in str(row.after)


# ---------------------------------------------------------------------------
# Finding 8 — audit log append-only; the 0128 reverse is non-destructive
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_guard(db):
    """Install the 0132 trigger + FKs (pytest runs with --nomigrations)."""
    module = importlib.import_module("plane.db.migrations.0132_time_tracking_audit_append_only")
    with connection.cursor() as cursor:
        cursor.execute(module.AUDIT_GUARD_SQL)


def _audit_row(s):
    return TimeTrackingAuditLog.objects.create(
        workspace=s["workspace"],
        actor=s["member"],
        action="create",
        target_type="worklog",
        target_id=uuid.uuid4(),
        after={"duration": 5},
    )


class TestAuditAppendOnly:
    def test_update_and_delete_are_refused(self, s3, audit_guard):
        row = _audit_row(s3)
        for sql in (
            "UPDATE time_tracking_audit_logs SET action = 'x' WHERE id = %s",
            "UPDATE time_tracking_audit_logs SET actor_id = NULL WHERE id = %s",
            "DELETE FROM time_tracking_audit_logs WHERE id = %s",
        ):
            with pytest.raises(DatabaseError, match="append-only"):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql, [str(row.pk)])
        assert TimeTrackingAuditLog.objects.get(pk=row.pk).action == "create"

    def test_orm_update_and_delete_are_refused_too(self, s3, audit_guard):
        row = _audit_row(s3)
        with pytest.raises(DatabaseError, match="append-only"):
            with transaction.atomic():
                TimeTrackingAuditLog.objects.filter(pk=row.pk).update(action="x")
        with pytest.raises(DatabaseError, match="append-only"):
            with transaction.atomic():
                TimeTrackingAuditLog.objects.filter(pk=row.pk).delete()

    def test_appends_still_work(self, s3, audit_guard):
        _audit_row(s3)
        _audit_row(s3)
        assert TimeTrackingAuditLog.objects.count() == 2

    def test_hard_delete_of_the_actor_nulls_the_actor_and_keeps_the_row(self, s3, audit_guard):
        loner = support.make_user(s3["workspace"], None, "Loner")
        row = TimeTrackingAuditLog.objects.create(
            workspace=s3["workspace"], actor=loner, action="create", target_type="worklog", target_id=uuid.uuid4()
        )
        loner.delete()  # Django cascades its own rows; the audit FK is the SQL one (SET NULL)
        row.refresh_from_db()
        assert row.actor_id is None and row.action == "create"

    def test_hard_delete_of_the_workspace_cascades(self, s3, audit_guard):
        from plane.db.models import Workspace

        doomed = Workspace.objects.create(name="Doomed", slug="doomed-ws", owner=s3["wsadmin"])
        row = TimeTrackingAuditLog.objects.create(
            workspace=doomed, actor=None, action="create", target_type="worklog", target_id=uuid.uuid4()
        )
        keep = _audit_row(s3)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM workspaces WHERE id = %s", [str(doomed.id)])
        assert not TimeTrackingAuditLog.objects.filter(pk=row.pk).exists()
        assert TimeTrackingAuditLog.objects.filter(pk=keep.pk).exists()


class TestMigration0128Reverse:
    def test_reverse_moves_orphans_instead_of_deleting_them(self, s3):
        mig = importlib.import_module("plane.db.migrations.0128_time_tracking_v2_foundations")
        reverse = next(
            op.reverse_sql
            for op in mig.Migration.operations
            if getattr(op, "reverse_sql", None) and "orphaned_0128" in str(op.reverse_sql)
        )
        live = _rated(s3, s3["member"])
        ghost = uuid.uuid4()
        orphan = IssueWorklog.objects.create(
            workspace=s3["workspace"],
            project=s3["p"],
            issue_id=ghost,
            logged_by=s3["member"],
            duration=30,
            logged_date=date(2026, 6, 24),
            issue_ref="PP-9",
            issue_name="Gone",
        )
        WorklogTimer.objects.create(project=s3["p"], issue_id=ghost, user=s3["member2"], started_at=START)
        with connection.cursor() as cursor:
            cursor.execute(reverse)
            cursor.execute("SELECT id FROM issue_worklogs_orphaned_0128")
            assert {r[0] for r in cursor.fetchall()} == {orphan.id}
            cursor.execute("SELECT count(*) FROM worklog_timers_orphaned_0128")
            assert cursor.fetchone()[0] == 1
        assert IssueWorklog.objects.filter(pk=live.pk).exists()
        assert not IssueWorklog.objects.filter(pk=orphan.pk).exists()
        assert not WorklogTimer.objects.filter(issue_id=ghost).exists()


# ---------------------------------------------------------------------------
# Finding 9 — legacy time-report: range cap and capped issue CSV
# ---------------------------------------------------------------------------


def _legacy(user, s, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    with freeze_time(NOW):
        return client_for(user).get(ws_url(s, f"time-report/?{query}"))


def _log(s, day, minutes=30, issue=None, user=None):
    issue = issue or s["issue"]
    return IssueWorklog.objects.create(
        workspace=s["workspace"],
        project=issue.project,
        issue=issue,
        logged_by=user or s["member"],
        duration=minutes,
        logged_date=day,
        source="manual",
    )


class TestLegacyReportRange:
    def test_a_span_over_366_days_is_refused(self, s3):
        r = _legacy(s3["wsadmin"], s3, group_by="project", start_date="2025-06-23", end_date="2026-06-24")
        assert r.status_code == 400 and "366" in r.json()["error"], r.content

    def test_exactly_366_days_is_allowed(self, s3):
        r = _legacy(s3["wsadmin"], s3, group_by="project", start_date="2025-06-24", end_date="2026-06-24")
        assert r.status_code == 200, r.content

    def test_end_before_start_is_refused(self, s3):
        r = _legacy(s3["wsadmin"], s3, group_by="project", start_date="2026-06-24", end_date="2026-06-01")
        assert r.status_code == 400

    def test_no_dates_means_the_last_366_days_not_the_whole_history(self, s3):
        _log(s3, date(2023, 1, 2), 11)
        _log(s3, date(2026, 6, 20), 22)
        r = _legacy(s3["wsadmin"], s3, group_by="project")
        assert r.status_code == 200, r.content
        assert r.json()["totals"]["total_minutes"] == 22

    def test_a_single_bound_is_completed_to_a_capped_span(self, s3):
        _log(s3, date(2024, 1, 2), 11)
        _log(s3, date(2026, 6, 20), 22)
        only_end = _legacy(s3["wsadmin"], s3, group_by="project", end_date="2026-06-24").json()
        assert only_end["totals"]["total_minutes"] == 22
        only_start = _legacy(s3["wsadmin"], s3, group_by="project", start_date="2023-12-01").json()
        assert only_start["totals"]["total_minutes"] == 11  # 2023-12-01 + 365 days = 2024-11-30


class TestLegacyIssueCsvCap:
    def test_issue_csv_over_the_cap_is_refused_not_truncated(self, s3, monkeypatch):
        monkeypatch.setattr(report_module, "MAX_CSV_ROWS", 1)
        second = support.make_issue(s3["p"], s3["issue"].state, "Second", s3["wsadmin"])
        _log(s3, date(2026, 6, 20), 10)
        _log(s3, date(2026, 6, 21), 10, issue=second)
        r = _legacy(s3["wsadmin"], s3, group_by="issue", export="csv", start_date="2026-06-01", end_date="2026-06-24")
        assert r.status_code == 400 and r.json()["code"] == "too_many_rows", r.content

    def test_issue_csv_within_the_cap_is_a_csv(self, s3, monkeypatch):
        monkeypatch.setattr(report_module, "MAX_CSV_ROWS", 2)
        second = support.make_issue(s3["p"], s3["issue"].state, "Second", s3["wsadmin"])
        _log(s3, date(2026, 6, 20), 10)
        _log(s3, date(2026, 6, 21), 10, issue=second)
        r = _legacy(s3["wsadmin"], s3, group_by="issue", export="csv", start_date="2026-06-01", end_date="2026-06-24")
        assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
        assert len(r.content.decode().splitlines()) == 3  # header + 2 work items

    def test_resource_csv_task_breakdown_is_capped(self, s3, monkeypatch):
        monkeypatch.setattr(report_module, "MAX_CSV_ROWS", 1)
        second = support.make_issue(s3["p"], s3["issue"].state, "Second", s3["wsadmin"])
        _log(s3, date(2026, 6, 20), 10)
        _log(s3, date(2026, 6, 21), 10, issue=second)
        r = _legacy(
            s3["wsadmin"], s3, group_by="resource", export="csv", start_date="2026-06-01", end_date="2026-06-24"
        )
        assert r.status_code == 400 and r.json()["code"] == "too_many_rows", r.content


# ---------------------------------------------------------------------------
# Finding 10 — the sweep stops the timer of a user who is no longer a member
# ---------------------------------------------------------------------------


def _start(s, user, at=utc(2026, 6, 24, 3, 0)):
    set_settings(s["workspace"], **IDLE_OFF)
    with freeze_time(at):
        r = client_for(user).post(ws_url(s, "time/timer/start/"), {"issue_id": str(s["issue"].id)}, format="json")
    assert r.status_code == 201, r.content


def _sweep(at=utc(2026, 6, 24, 5, 0)):
    with freeze_time(at):
        return time_tracking_timer_sweep()


class TestSweepStopsRemovedMembers:
    def test_project_membership_deactivated(self, s3):
        _start(s3, s3["member2"])
        ProjectMember.objects.filter(project=s3["p"], member=s3["member2"]).update(is_active=False)
        result = _sweep()
        assert result["member_removed"] == 1
        assert not WorklogTimer.objects.filter(user=s3["member2"]).exists()
        wl = IssueWorklog.objects.get(logged_by=s3["member2"])
        assert wl.duration == 120 and wl.ended_at == utc(2026, 6, 24, 5, 0)
        audit = TimeTrackingAuditLog.objects.get(target_id=wl.id, action="auto_stop")
        assert audit.actor_id is None and audit.after["reason"] == "member_removed"

    def test_workspace_membership_deactivated(self, s3):
        _start(s3, s3["member2"])
        WorkspaceMember.objects.filter(workspace=s3["workspace"], member=s3["member2"]).update(is_active=False)
        assert _sweep()["member_removed"] == 1
        assert not WorklogTimer.objects.filter(user=s3["member2"]).exists()

    def test_project_membership_row_deleted(self, s3):
        _start(s3, s3["member2"])
        ProjectMember.objects.filter(project=s3["p"], member=s3["member2"]).delete()
        assert _sweep()["member_removed"] == 1

    def test_demoted_to_guest_in_the_project(self, s3):
        _start(s3, s3["member2"])
        ProjectMember.objects.filter(project=s3["p"], member=s3["member2"]).update(role=5)
        assert _sweep()["member_removed"] == 1

    def test_demoted_to_guest_in_the_workspace(self, s3):
        _start(s3, s3["member2"])
        WorkspaceMember.objects.filter(workspace=s3["workspace"], member=s3["member2"]).update(role=5)
        assert _sweep()["member_removed"] == 1

    def test_active_members_are_left_alone(self, s3):
        _start(s3, s3["member2"])
        _start(s3, s3["pa"])
        result = _sweep()
        assert result["member_removed"] == 0
        assert WorklogTimer.objects.filter(user__in=[s3["member2"], s3["pa"]]).count() == 2

    def test_only_the_removed_users_timer_is_stopped(self, s3):
        _start(s3, s3["member2"])
        _start(s3, s3["pa"])
        ProjectMember.objects.filter(project=s3["p"], member=s3["member2"]).update(is_active=False)
        assert _sweep()["member_removed"] == 1
        assert WorklogTimer.objects.filter(user=s3["pa"]).exists()
