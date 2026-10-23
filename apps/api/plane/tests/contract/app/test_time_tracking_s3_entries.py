# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — who may write time entries directly (DESIGN §2 items 15-17, §6 S3).

Workspace admins: anything. Project admins: other members' entries in their project.
Members: add their own only when ``allow_member_manual_entry`` is on, edit the description,
and request every time change / deletion (403 ``request_required``). Guests: never.
"""

from datetime import date

import pytest
from freezegun import freeze_time

from plane.db.models import IssueWorklog, TimeTrackingAuditLog

from . import time_tracking_s3_support as support
from .time_tracking_s3_support import (  # noqa: F401 (_load_urlconf is an autouse fixture)
    MON,
    NOW,
    _load_urlconf,
    client_for,
    entry,
    iso,
    set_settings,
    utc,
    ws_url,
)

# Fixtures, bound by name so pytest finds them in this module.
s3 = support.s3

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _create(user, s3, start=None, end=None, issue=None, **extra):
    data = {"issue_id": str((issue or s3["issue"]).id), **extra}
    if start is not None:
        data.update(started_at=iso(start), ended_at=iso(end))
    with freeze_time(NOW):
        return client_for(user).post(ws_url(s3, "time/entries/"), data, format="json")


def _patch(user, s3, wl, **data):
    with freeze_time(NOW):
        return client_for(user).patch(ws_url(s3, f"time/entries/{wl.id}/"), data, format="json")


def _delete(user, s3, wl):
    with freeze_time(NOW):
        return client_for(user).delete(ws_url(s3, f"time/entries/{wl.id}/"))


def _issue_worklogs_url(s3, issue=None):
    issue = issue or s3["issue"]
    return ws_url(s3, f"projects/{issue.project_id}/issues/{issue.id}/worklogs/")


START, END = utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 30)


class TestCreate:
    def test_member_own_entry_follows_the_workspace_setting(self, s3):
        off = _create(s3["member"], s3, START, END)
        assert off.status_code == 403 and off.json()["code"] == "request_required"
        set_settings(s3["workspace"], allow_member_manual_entry=True)
        on = _create(s3["member"], s3, START, END, billable_rate="999", currency="EUR")
        assert on.status_code == 201, on.json()
        wl = IssueWorklog.objects.get(logged_by=s3["member"])
        assert wl.duration == 90 and wl.source == "manual" and wl.logged_date == MON
        assert wl.currency != "EUR" and wl.billable_rate != 999  # billing is an admin's call
        assert TimeTrackingAuditLog.objects.filter(action="create", target_id=wl.id, actor=s3["member"]).exists()

    def test_project_admin_for_others_yes_for_self_no(self, s3):
        others = _create(s3["pa"], s3, START, END, logged_by=str(s3["member"].id))
        assert others.status_code == 201, others.json()
        assert IssueWorklog.objects.get(logged_by=s3["member"]).created_by_id == s3["pa"].id
        mine = _create(s3["pa"], s3, START, END)
        assert mine.status_code == 403 and mine.json()["code"] == "request_required"
        # Not the admin of the work item's project.
        other_project = _create(s3["pa"], s3, START, END, issue=s3["issue_q"], logged_by=str(s3["member"].id))
        assert other_project.status_code == 403

    def test_member_cannot_create_for_someone_else(self, s3):
        set_settings(s3["workspace"], allow_member_manual_entry=True)
        r = _create(s3["member"], s3, START, END, logged_by=str(s3["member2"].id))
        assert r.status_code == 403 and r.json()["code"] == "not_allowed"

    def test_workspace_admin_any_and_split_over_midnight(self, s3):
        r = _create(s3["wsadmin"], s3, utc(2026, 6, 22, 17, 0), utc(2026, 6, 22, 19, 0), logged_by=str(s3["member"].id))
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["total_duration"] == 120 and len(body["pieces"]) == 2
        assert sorted(IssueWorklog.objects.filter(logged_by=s3["member"]).values_list("logged_date", flat=True)) == [
            date(2026, 6, 22),
            date(2026, 6, 23),
        ]
        own = _create(s3["wsadmin"], s3, START, END)
        assert own.status_code == 201

    def test_target_must_be_a_project_member(self, s3):
        r = _create(s3["wsadmin"], s3, START, END, issue=s3["issue_q"], logged_by=str(s3["member2"].id))
        assert r.status_code == 400 and r.json()["code"] == "invalid_user"

    def test_overlap_and_future_are_refused(self, s3):
        entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
        r = _create(s3["wsadmin"], s3, START, END, logged_by=str(s3["member"].id))
        assert r.status_code == 400 and r.json()["code"] == "overlap"
        future = _create(s3["wsadmin"], s3, utc(2026, 6, 24, 7, 0), utc(2026, 6, 24, 8, 0))
        assert future.status_code == 400 and future.json()["code"] == "invalid_interval"

    def test_duration_shape_still_works(self, s3):
        r = _create(s3["wsadmin"], s3, logged_date="2026-06-23", duration=45)
        assert r.status_code == 201 and r.json()["total_duration"] == 45

    def test_issue_endpoint_applies_the_same_rules(self, s3):
        with freeze_time(NOW):
            off = client_for(s3["member"]).post(
                _issue_worklogs_url(s3), {"logged_date": "2026-06-22", "duration": 30}, format="json"
            )
            assert off.status_code == 403 and off.json()["code"] == "request_required"
            set_settings(s3["workspace"], allow_member_manual_entry=True)
            on = client_for(s3["member"]).post(
                _issue_worklogs_url(s3), {"started_at": iso(START), "ended_at": iso(END)}, format="json"
            )
            assert on.status_code == 201, on.json()
            assert on.json()["total_duration"] == 90 and on.json()["logged_by"] == str(s3["member"].id)
            pa_self = client_for(s3["pa"]).post(
                _issue_worklogs_url(s3), {"logged_date": "2026-06-23", "duration": 30}, format="json"
            )
            assert pa_self.status_code == 201  # setting is on: an admin's own time is a member's own time
            pa_other = client_for(s3["pa"]).post(
                _issue_worklogs_url(s3),
                {"logged_date": "2026-06-23", "duration": 30, "logged_by": str(s3["member2"].id)},
                format="json",
            )
            assert pa_other.status_code == 201

    def test_guest_is_403(self, s3):
        set_settings(s3["workspace"], allow_member_manual_entry=True)
        assert _create(s3["guest"], s3, START, END).status_code == 403
        with freeze_time(NOW):
            r = client_for(s3["guest"]).post(
                _issue_worklogs_url(s3), {"logged_date": "2026-06-22", "duration": 30}, format="json"
            )
        assert r.status_code == 403
        assert not IssueWorklog.objects.filter(logged_by=s3["guest"]).exists()


class TestEditAndDelete:
    def test_member_time_edits_need_a_request(self, s3):
        set_settings(s3["workspace"], allow_member_manual_entry=True)  # does not unlock edits
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        for data in ({"ended_at": iso(utc(2026, 6, 22, 5, 0))}, {"duration": 30}, {"logged_date": "2026-06-23"}):
            r = _patch(s3["member"], s3, wl, **data)
            assert r.status_code == 403 and r.json()["code"] == "request_required", data
        d = _delete(s3["member"], s3, wl)
        assert d.status_code == 403 and d.json()["code"] == "request_required"
        billing = _patch(s3["member"], s3, wl, is_billable=False)
        assert billing.status_code == 403 and billing.json()["code"] == "not_allowed"
        ok = _patch(s3["member"], s3, wl, description="pairing with QA")
        assert ok.status_code == 200, ok.json()
        wl.refresh_from_db()
        assert wl.description == "pairing with QA" and wl.duration == 120 and wl.deleted_at is None
        log = TimeTrackingAuditLog.objects.get(action="update", target_id=wl.id)
        assert log.actor_id == s3["member"].id and log.after["description"] == "pairing with QA"

    def test_member_cannot_touch_someone_elses_entry(self, s3):
        wl = entry(s3, s3["member2"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        assert _patch(s3["member"], s3, wl, description="x").json()["code"] == "not_allowed"
        assert _delete(s3["member"], s3, wl).status_code == 403

    def test_locked_own_entry_description_is_refused(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0), is_locked=True)
        r = _patch(s3["member"], s3, wl, description="x")
        assert r.status_code == 400 and r.json()["code"] == "entry_locked"

    def test_project_admin_edits_and_deletes_members_entries_in_their_project(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        r = _patch(s3["pa"], s3, wl, started_at=iso(utc(2026, 6, 22, 4, 30)), ended_at=iso(utc(2026, 6, 22, 6, 0)))
        assert r.status_code == 200, r.json()
        wl.refresh_from_db()
        assert wl.duration == 90
        on_q = entry(s3, s3["member"], utc(2026, 6, 23, 4, 0), utc(2026, 6, 23, 6, 0), issue=s3["issue_q"])
        assert _patch(s3["pa"], s3, on_q, duration=30).json()["code"] == "not_allowed"
        own = entry(s3, s3["pa"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        assert _patch(s3["pa"], s3, own, duration=30).json()["code"] == "request_required"
        assert _delete(s3["pa"], s3, wl).status_code == 204
        assert not IssueWorklog.objects.filter(pk=wl.pk).exists()
        assert TimeTrackingAuditLog.objects.filter(action="delete", target_id=wl.id, actor=s3["pa"]).exists()

    def test_edit_overlap_is_refused(self, s3):
        a = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
        entry(s3, s3["member"], utc(2026, 6, 22, 6, 0), utc(2026, 6, 22, 7, 0))
        r = _patch(s3["wsadmin"], s3, a, ended_at=iso(utc(2026, 6, 22, 6, 30)))
        assert r.status_code == 400 and r.json()["code"] == "overlap"

    def test_issue_endpoint_patch_and_delete_follow_the_rules(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        url = _issue_worklogs_url(s3) + f"{wl.id}/"
        with freeze_time(NOW):
            assert client_for(s3["member"]).patch(url, {"duration": 30}, format="json").json()["code"] == (
                "request_required"
            )
            assert client_for(s3["member"]).delete(url).status_code == 403
            assert client_for(s3["member"]).patch(url, {"description": "ok"}, format="json").status_code == 200
            assert client_for(s3["pa"]).patch(url, {"duration": 30}, format="json").status_code == 200
            assert client_for(s3["guest"]).patch(url, {"description": "x"}, format="json").status_code == 403
            assert client_for(s3["pa"]).delete(url).status_code == 204

    def test_guest_is_403(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        assert _patch(s3["guest"], s3, wl, description="x").status_code == 403
        assert _delete(s3["guest"], s3, wl).status_code == 403

    def test_unknown_entry_is_404(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        wl.delete()
        assert _patch(s3["wsadmin"], s3, wl, description="x").status_code == 404


class TestWeeklyApprovalGate:
    def test_submit_and_review_are_400_when_weekly_approval_is_off(self, s3):
        with freeze_time(NOW):
            c = client_for(s3["member"])
            r = c.post(
                ws_url(s3, "timesheets/submit/"),
                {"period_start": "2026-06-22", "period_end": "2026-06-28"},
                format="json",
            )
            assert r.status_code == 400 and r.json()["code"] == "weekly_approval_disabled"
            set_settings(s3["workspace"], weekly_approval_enabled=True)
            entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
            ok = c.post(
                ws_url(s3, "timesheets/submit/"),
                {"period_start": "2026-06-22", "period_end": "2026-06-28"},
                format="json",
            )
            assert ok.status_code == 200, ok.json()
            set_settings(s3["workspace"], weekly_approval_enabled=False)
            review = client_for(s3["wsadmin"]).post(
                ws_url(s3, f"timesheets/{ok.json()['id']}/review/"), {"action": "approve"}, format="json"
            )
        assert review.status_code == 400 and review.json()["code"] == "weekly_approval_disabled"
