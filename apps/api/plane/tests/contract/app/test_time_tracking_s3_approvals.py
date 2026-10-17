# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — approving, rejecting and withdrawing time-entry requests
(DESIGN §2 items 15-21, §6 S3): atomic apply, stale 409, split over local midnight."""

from datetime import date

import pytest
from freezegun import freeze_time

from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import (
    IssueWorklog,
    TimeEntryRequest,
    TimeEntryRequestEvent,
    TimeTrackingAuditLog,
    WorklogSource,
)
from plane.utils.time_tracking import entry_requests

from .time_tracking_s2_support import IDLE_OFF
from . import time_tracking_s3_support as support
from .time_tracking_s3_support import (  # noqa: F401 (_load_urlconf is an autouse fixture)
    NOW,
    _load_urlconf,
    act,
    client_for,
    entry,
    iso,
    missing,
    req,
    set_settings,
    submit,
    utc,
    ws_url,
)

# Fixtures, bound by name so pytest finds them in this module.
event_guard = support.event_guard
s3 = support.s3

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _actions(request_id):
    return list(
        TimeEntryRequestEvent.objects.filter(request_id=request_id)
        .order_by("created_at")
        .values_list("action", flat=True)
    )


def _change(s3, wl, start, end, reason="wrong times"):
    r = submit(
        client_for(s3["member"]),
        s3,
        type="time_change",
        worklog_id=str(wl.id),
        started_at=iso(start),
        ended_at=iso(end),
        reason=reason,
    )
    assert r.status_code == 201, r.json()
    return r.json()


def _total(s3):
    r = client_for(s3["wsadmin"]).get(
        ws_url(s3, "time-report/?group_by=resource&start_date=2026-06-01&end_date=2026-06-30")
    )
    assert r.status_code == 200, r.json()
    return r.data["totals"]["total_minutes"]


class TestApplyMissingTime:
    def test_approve_creates_the_entry_split_over_local_midnight(self, s3, event_guard):
        with freeze_time(NOW):
            # 23:00 → 01:30 Dhaka on the night of Mon 22 → Tue 23.
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 17, 0), utc(2026, 6, 22, 19, 30)).json()
            resp = act(client_for(s3["pa"]), s3, r["id"], "approve", note="fine")
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["status"] == "approved" and body["reviewer"]["id"] == str(s3["pa"].id)
        assert body["reviewer_role"] == "project_admin" and body["review_note"] == "fine"
        pieces = list(IssueWorklog.objects.filter(logged_by=s3["member"]).order_by("started_at"))
        assert [(p.logged_date, p.duration) for p in pieces] == [(date(2026, 6, 22), 60), (date(2026, 6, 23), 90)]
        assert pieces[0].ended_at == utc(2026, 6, 22, 18, 0) == pieces[1].started_at
        assert all(p.request_id == req(r["id"]).id and p.source == WorklogSource.MANUAL for p in pieces)
        assert pieces[0].session_id is not None and pieces[0].session_id == pieces[1].session_id
        assert req(r["id"]).worklog_id in {p.id for p in pieces}
        assert _actions(r["id"]) == ["submitted", "approved", "applied"]
        applied = TimeEntryRequestEvent.objects.get(request_id=r["id"], action="applied")
        assert sorted(applied.payload["worklog_ids"]) == sorted(str(p.id) for p in pieces)
        assert (
            TimeTrackingAuditLog.objects.filter(
                action="apply_request", target_id__in=[p.id for p in pieces], actor=s3["pa"]
            ).count()
            == 2
        )

    def test_overlap_at_approval_is_409_and_changes_nothing(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 5, 0)).json()
        entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 4, 30))  # logged meanwhile
        with freeze_time(NOW):
            resp = act(client_for(s3["pa"]), s3, r["id"], "approve")
        assert resp.status_code == 409 and resp.json()["code"] == "overlap"
        assert req(r["id"]).status == "pending" and IssueWorklog.objects.filter(logged_by=s3["member"]).count() == 1

    def test_approve_is_atomic(self, s3, monkeypatch):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 5, 0)).json()
        real = entry_requests._event

        def boom(request, action, *args, **kwargs):
            if action == "applied":
                raise RuntimeError("boom")
            return real(request, action, *args, **kwargs)

        monkeypatch.setattr(entry_requests, "_event", boom)
        with freeze_time(NOW), pytest.raises(RuntimeError):
            entry_requests.approve(s3["workspace"].id, s3["pa"], r["id"])
        assert req(r["id"]).status == "pending" and req(r["id"]).reviewer_id is None
        assert not IssueWorklog.all_objects.filter(logged_by=s3["member"]).exists()
        assert _actions(r["id"]) == ["submitted"]
        assert not TimeTrackingAuditLog.objects.filter(action="apply_request").exists()


class TestApplyChangeAndDeletion:
    def test_time_change_applies_and_splits(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            r = _change(s3, wl, utc(2026, 6, 22, 17, 0), utc(2026, 6, 22, 19, 0))
            assert act(client_for(s3["pa"]), s3, r["id"], "approve").status_code == 200
        pieces = list(IssueWorklog.objects.filter(logged_by=s3["member"]).order_by("started_at"))
        assert [(p.logged_date, p.duration) for p in pieces] == [(date(2026, 6, 22), 60), (date(2026, 6, 23), 60)]
        assert pieces[0].id == wl.id  # the original row is reused for the first piece
        assert all(p.request_id is not None for p in pieces)

    def test_deletion_applies_as_soft_delete(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            r = submit(client_for(s3["member"]), s3, type="deletion", worklog_id=str(wl.id), reason="wrong item").json()
            assert act(client_for(s3["wsadmin"]), s3, r["id"], "approve").json()["reviewer_role"] == "workspace_admin"
        assert not IssueWorklog.objects.filter(pk=wl.id).exists()
        gone = IssueWorklog.all_objects.get(pk=wl.id)
        assert gone.deleted_at is not None and gone.request_id == req(r["id"]).id
        log = TimeTrackingAuditLog.objects.get(action="apply_request", target_id=wl.id)
        assert log.after["deleted"] is True and log.actor_id == s3["wsadmin"].id

    def test_entry_changed_since_the_request_is_stale(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            r = _change(s3, wl, utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
            patched = client_for(s3["wsadmin"]).patch(
                ws_url(s3, f"time/entries/{wl.id}/"), {"ended_at": iso(utc(2026, 6, 22, 5, 30))}, format="json"
            )
            assert patched.status_code == 200, patched.json()
            resp = act(client_for(s3["pa"]), s3, r["id"], "approve")
        assert resp.status_code == 409 and resp.json()["code"] == "stale"
        wl.refresh_from_db()
        assert wl.duration == 90 and req(r["id"]).status == "pending"
        # The approver can still reject it.
        with freeze_time(NOW):
            assert act(client_for(s3["pa"]), s3, r["id"], "reject", note="changed meanwhile").status_code == 200

    def test_deleted_or_locked_entry_is_409(self, s3):
        a = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        b = entry(s3, s3["member"], utc(2026, 6, 23, 4, 0), utc(2026, 6, 23, 6, 0))
        with freeze_time(NOW):
            ra = submit(client_for(s3["member"]), s3, type="deletion", worklog_id=str(a.id), reason="x").json()
            rb = _change(s3, b, utc(2026, 6, 23, 4, 0), utc(2026, 6, 23, 5, 0))
        a.delete()
        IssueWorklog.objects.filter(pk=b.pk).update(is_locked=True)
        with freeze_time(NOW):
            resp_a = act(client_for(s3["pa"]), s3, ra["id"], "approve")
            resp_b = act(client_for(s3["pa"]), s3, rb["id"], "approve")
        assert resp_a.status_code == 409 and resp_a.json()["code"] == "stale"
        assert resp_b.status_code == 409 and resp_b.json()["code"] == "entry_locked"

    def test_change_overlapping_another_entry_at_approval_is_409(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            r = _change(s3, wl, utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 8, 0))
        entry(s3, s3["member"], utc(2026, 6, 22, 7, 0), utc(2026, 6, 22, 7, 30))
        with freeze_time(NOW):
            resp = act(client_for(s3["pa"]), s3, r["id"], "approve")
        assert resp.status_code == 409 and resp.json()["code"] == "overlap"
        wl.refresh_from_db()
        assert wl.duration == 120

    def test_pending_requests_do_not_move_totals(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            assert _total(s3) == 120
            change = _change(s3, wl, utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
            add = missing(client_for(s3["member"]), s3, utc(2026, 6, 23, 3, 0), utc(2026, 6, 23, 4, 0)).json()
            assert _total(s3) == 120
            act(client_for(s3["pa"]), s3, change["id"], "approve")
            assert _total(s3) == 60
            act(client_for(s3["pa"]), s3, add["id"], "approve")
            assert _total(s3) == 120


class TestRejectAndWithdraw:
    def test_reject_needs_a_note_and_changes_nothing(self, s3, event_guard):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        with freeze_time(NOW):
            r = _change(s3, wl, utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
            empty = act(client_for(s3["pa"]), s3, r["id"], "reject", note="  ")
            assert empty.status_code == 400 and empty.json()["code"] == "note_required"
            done = act(client_for(s3["pa"]), s3, r["id"], "reject", note="the timer was right")
        assert done.status_code == 200 and done.json()["status"] == "rejected"
        assert done.json()["review_note"] == "the timer was right"
        wl.refresh_from_db()
        assert wl.duration == 120 and wl.request_id is None
        assert _actions(r["id"]) == ["submitted", "rejected"]
        with freeze_time(NOW):
            again = act(client_for(s3["pa"]), s3, r["id"], "approve")
        assert again.status_code == 409 and again.json()["code"] == "not_pending"

    def test_reviewer_checks(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            own = act(client_for(s3["member"]), s3, r["id"], "approve")
            assert own.status_code == 403 and own.json()["code"] == "own_request"
            assert act(client_for(s3["pa_q"]), s3, r["id"], "approve").status_code == 404  # not its project
            assert act(client_for(s3["member2"]), s3, r["id"], "reject", note="x").status_code == 404

    def test_withdraw(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            by_admin = act(client_for(s3["pa"]), s3, r["id"], "withdraw")
            assert by_admin.status_code == 403 and by_admin.json()["code"] == "not_requester"
            ok = act(client_for(s3["member"]), s3, r["id"], "withdraw")
            assert ok.status_code == 200 and ok.json()["status"] == "withdrawn" and ok.json()["can_withdraw"] is False
            twice = act(client_for(s3["member"]), s3, r["id"], "withdraw")
            assert twice.status_code == 409 and twice.json()["code"] == "not_pending"
            assert act(client_for(s3["pa"]), s3, r["id"], "approve").json()["code"] == "not_pending"
        assert _actions(r["id"]) == ["submitted", "withdrawn"]
        assert not IssueWorklog.objects.filter(logged_by=s3["member"]).exists()


class TestAutoStopReviewApproval:
    def _auto_stopped(self, s3):
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s3["workspace"], **IDLE_OFF)
            r = client_for(s3["member"]).post(
                ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json"
            )
            assert r.status_code == 201, r.json()
        with freeze_time(utc(2026, 6, 22, 14, 5)):
            time_tracking_timer_sweep()
        wl = IssueWorklog.objects.get(logged_by=s3["member"])
        assert wl.duration == 840
        return wl, TimeEntryRequest.objects.get(type="auto_stop_review", requester=s3["member"])

    def test_confirm_as_is(self, s3):
        wl, review = self._auto_stopped(s3)
        with freeze_time(utc(2026, 6, 22, 15, 0)):
            resp = act(client_for(s3["pa"]), s3, review.id, "approve")
        assert resp.status_code == 200 and resp.json()["status"] == "approved"
        wl.refresh_from_db()
        assert wl.duration == 840 and wl.request_id == review.id
        assert TimeTrackingAuditLog.objects.filter(action="confirm", target_id=wl.id).exists()

    def test_trim_with_new_ended_at(self, s3):
        wl, review = self._auto_stopped(s3)
        with freeze_time(utc(2026, 6, 22, 15, 0)):
            late = act(client_for(s3["pa"]), s3, review.id, "approve", new_ended_at=iso(utc(2026, 6, 22, 15, 0)))
            assert late.status_code == 400 and late.json()["code"] == "invalid_interval"
            resp = act(client_for(s3["pa"]), s3, review.id, "approve", new_ended_at=iso(utc(2026, 6, 22, 9, 0)))
        assert resp.status_code == 200, resp.json()
        wl.refresh_from_db()
        assert wl.duration == 540 and wl.ended_at == utc(2026, 6, 22, 9, 0)
        applied = TimeEntryRequestEvent.objects.get(request=review, action="applied")
        assert applied.payload["trimmed"] is True

    def test_entry_edited_since_auto_stop_is_stale(self, s3):
        wl, review = self._auto_stopped(s3)
        IssueWorklog.objects.filter(pk=wl.pk).update(ended_at=utc(2026, 6, 22, 10, 0), duration=600)
        with freeze_time(utc(2026, 6, 22, 15, 0)):
            resp = act(client_for(s3["pa"]), s3, review.id, "approve")
        assert resp.status_code == 409 and resp.json()["code"] == "stale"

    def test_new_ended_at_only_for_auto_stop_reviews(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            resp = act(client_for(s3["pa"]), s3, r["id"], "approve", new_ended_at=iso(utc(2026, 6, 22, 3, 30)))
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_field"
