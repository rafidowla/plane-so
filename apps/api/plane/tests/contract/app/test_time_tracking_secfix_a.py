# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 security-review fixes A (secrev findings 1, 2, 3, 6).

1. The member an auto-stop review is *about* can't dismiss it (direct withdraw, or by filing a
   change request that supersedes it and withdrawing that).
2. A locked (approved-timesheet) entry of someone else is a workspace admin's audited override.
3. The legacy ``logged_date`` + ``duration`` shape can't exceed 24 h a day or dodge the
   tracking switch, and a date/duration edit moves the stored interval with it.
6. Approving ``missing_time`` re-checks membership, the tracking switch and the project setting.
"""

from datetime import date

import pytest
from freezegun import freeze_time

from plane.db.models import IssueWorklog, ProjectMember, TimeEntryRequest, TimeEntryRequestEvent, TimeTrackingAuditLog
from plane.utils.time_tracking.tracking import set_member_tracking

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
    submit,
    utc,
    ws_url,
)

s3 = support.s3

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _create(user, s3, **data):
    data = {"issue_id": str(s3["issue"].id), **data}
    with freeze_time(NOW):
        return client_for(user).post(ws_url(s3, "time/entries/"), data, format="json")


def _patch(user, s3, wl, **data):
    with freeze_time(NOW):
        return client_for(user).patch(ws_url(s3, f"time/entries/{wl.id}/"), data, format="json")


def _delete(user, s3, wl):
    with freeze_time(NOW):
        return client_for(user).delete(ws_url(s3, f"time/entries/{wl.id}/"))


def _issue_entry_url(s3, wl):
    return ws_url(s3, f"projects/{s3['p'].id}/issues/{s3['issue'].id}/worklogs/{wl.id}/")


# ---------------------------------------------------------------------------
# 1. Auto-stop review can't be dismissed by its subject
# ---------------------------------------------------------------------------


def _review_for(s3):
    """A member's 14 h auto-stopped entry with its pending review (member stored as requester)."""
    wl = entry(
        s3, s3["member"], utc(2026, 6, 22, 0, 0), utc(2026, 6, 22, 14, 0), auto_stopped=True
    )  # 06:00-20:00 Dhaka
    review = TimeEntryRequest.objects.create(
        workspace=s3["workspace"],
        project=s3["p"],
        requester=s3["member"],
        issue=s3["issue"],
        worklog=wl,
        type="auto_stop_review",
        work_date=wl.logged_date,
        old_started_at=wl.started_at,
        old_ended_at=wl.ended_at,
        old_duration=wl.duration,
    )
    return wl, review


def _change(s3, wl):
    with freeze_time(NOW):
        return submit(
            client_for(s3["member"]),
            s3,
            type="time_change",
            worklog_id=str(wl.id),
            started_at=iso(wl.started_at),
            ended_at=iso(utc(2026, 6, 22, 9, 0)),
            reason="left earlier",
        )


class TestAutoStopReviewIsNotWithdrawable:
    def test_subject_cannot_withdraw_the_review_directly(self, s3):
        _, review = _review_for(s3)
        with freeze_time(NOW):
            r = act(client_for(s3["member"]), s3, review.id, "withdraw")
        assert r.status_code == 403 and r.json()["code"] == "review_not_withdrawable"
        review.refresh_from_db()
        assert review.status == "pending"

    def test_serializer_says_can_withdraw_false_for_reviews(self, s3):
        from plane.utils.time_tracking import entry_requests

        wl, review = _review_for(s3)
        assert entry_requests.serialize(review, s3["member"])["can_withdraw"] is False
        own = TimeEntryRequest.objects.get(pk=_change(s3, wl).json()["id"])
        assert entry_requests.serialize(own, s3["member"])["can_withdraw"] is True

    def test_withdrawing_the_superseding_request_reopens_the_review(self, s3):
        wl, review = _review_for(s3)
        change = _change(s3, wl)
        assert change.status_code == 201, change.json()
        review.refresh_from_db()
        assert review.status == "withdrawn"  # superseded, as designed
        with freeze_time(NOW):
            w = act(client_for(s3["member"]), s3, change.json()["id"], "withdraw")
        assert w.status_code == 200, w.json()
        review.refresh_from_db()
        assert review.status == "pending"
        reopened = TimeEntryRequestEvent.objects.filter(request=review, action="submitted").order_by("created_at")
        assert reopened.last().payload == {"reopened_after": change.json()["id"]}
        # The project admin can still resolve it.
        with freeze_time(NOW):
            ok = act(client_for(s3["pa"]), s3, review.id, "approve")
        assert ok.status_code == 200, ok.json()

    def test_rejecting_the_superseding_request_reopens_the_review(self, s3):
        wl, review = _review_for(s3)
        change = _change(s3, wl)
        with freeze_time(NOW):
            rej = act(client_for(s3["pa"]), s3, change.json()["id"], "reject", note="no")
        assert rej.status_code == 200, rej.json()
        review.refresh_from_db()
        assert review.status == "pending"

    def test_an_approved_superseding_request_leaves_the_review_closed(self, s3):
        wl, review = _review_for(s3)
        change = _change(s3, wl)
        with freeze_time(NOW):
            ok = act(client_for(s3["pa"]), s3, change.json()["id"], "approve")
        assert ok.status_code == 200, ok.json()
        review.refresh_from_db()
        assert review.status == "withdrawn"
        wl.refresh_from_db()
        assert wl.duration == 540


# ---------------------------------------------------------------------------
# 2. Locked entries: workspace-admin override only, audited
# ---------------------------------------------------------------------------


def _locked(s3):
    return entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0), is_locked=True)


class TestLockedEntries:
    def test_project_admin_cannot_edit_or_delete_a_locked_entry(self, s3):
        wl = _locked(s3)
        for r in (
            _patch(s3["pa"], s3, wl, duration=30),
            _patch(s3["pa"], s3, wl, description="x"),
            _delete(s3["pa"], s3, wl),
        ):
            assert r.status_code == 403 and r.json()["code"] == "entry_locked"
        with freeze_time(NOW):
            c = client_for(s3["pa"])
            p = c.patch(_issue_entry_url(s3, wl), {"duration": 30}, format="json")
            d = c.delete(_issue_entry_url(s3, wl))
        assert p.status_code == 403 and p.json()["code"] == "entry_locked"
        assert d.status_code == 403 and d.json()["code"] == "entry_locked"
        wl.refresh_from_db()
        assert wl.duration == 120 and wl.deleted_at is None
        assert not TimeTrackingAuditLog.objects.filter(target_id=wl.id).exists()

    def test_member_cannot_touch_a_locked_entry_of_someone_else(self, s3):
        wl = _locked(s3)
        assert _patch(s3["member2"], s3, wl, description="x").status_code == 403
        assert _delete(s3["member2"], s3, wl).status_code == 403

    def test_workspace_admin_override_is_audited(self, s3):
        wl = _locked(s3)
        r = _patch(s3["wsadmin"], s3, wl, ended_at=iso(utc(2026, 6, 22, 5, 0)))
        assert r.status_code == 200, r.json()
        wl.refresh_from_db()
        assert wl.duration == 60 and wl.is_locked is True
        log = TimeTrackingAuditLog.objects.get(action="update", target_id=wl.id)
        assert log.actor_id == s3["wsadmin"].id
        assert log.before["override"] == "locked_entry" and log.after["override"] == "locked_entry"
        assert log.before["duration"] == 120 and log.after["duration"] == 60

    def test_workspace_admin_delete_override_is_audited(self, s3):
        wl = _locked(s3)
        assert _delete(s3["wsadmin"], s3, wl).status_code == 204
        log = TimeTrackingAuditLog.objects.get(action="delete", target_id=wl.id)
        assert log.actor_id == s3["wsadmin"].id and log.before["override"] == "locked_entry"
        assert log.after == {"override": "locked_entry"}

    def test_unlocked_edit_carries_no_override_marker(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        assert _patch(s3["wsadmin"], s3, wl, duration=30).status_code == 200
        log = TimeTrackingAuditLog.objects.get(action="update", target_id=wl.id)
        assert "override" not in log.after

    def test_own_locked_entry_is_still_refused_for_a_member(self, s3):
        wl = _locked(s3)
        r = _patch(s3["member"], s3, wl, description="x")
        assert r.status_code == 400 and r.json()["code"] == "entry_locked"

    def test_split_piece_of_a_locked_entry_stays_locked(self, s3):
        wl = _locked(s3)
        r = _patch(
            s3["wsadmin"], s3, wl, started_at=iso(utc(2026, 6, 22, 17, 0)), ended_at=iso(utc(2026, 6, 22, 19, 0))
        )  # crosses local midnight (Dhaka)
        assert r.status_code == 200, r.json()
        rows = IssueWorklog.objects.filter(logged_by=s3["member"])
        assert rows.count() == 2 and all(row.is_locked for row in rows)


# ---------------------------------------------------------------------------
# 3. Legacy date + duration: day cap, tracking switch, no stale interval
# ---------------------------------------------------------------------------


class TestDayCap:
    def test_legacy_shape_cannot_stack_past_24h(self, s3):
        ok = _create(s3["wsadmin"], s3, logged_date="2026-06-23", duration=1440, logged_by=str(s3["member"].id))
        assert ok.status_code == 201, ok.json()
        for _ in range(2):
            r = _create(s3["wsadmin"], s3, logged_date="2026-06-23", duration=1440, logged_by=str(s3["member"].id))
            assert r.status_code == 400 and r.json()["code"] == "day_limit"
        assert IssueWorklog.objects.filter(logged_by=s3["member"]).count() == 1

    def test_legacy_and_interval_entries_share_the_cap(self, s3):
        entry(s3, s3["member"], utc(2026, 6, 22, 0, 0), utc(2026, 6, 22, 12, 0))  # 720 min on the 22nd
        r = _create(s3["wsadmin"], s3, logged_date="2026-06-22", duration=800, logged_by=str(s3["member"].id))
        assert r.status_code == 400 and r.json()["code"] == "day_limit"
        fits = _create(s3["wsadmin"], s3, logged_date="2026-06-22", duration=720, logged_by=str(s3["member"].id))
        assert fits.status_code == 201, fits.json()
        # ... and the other way round: an interval on a day already full of legacy minutes.
        later = _create(
            s3["wsadmin"],
            s3,
            started_at=iso(utc(2026, 6, 22, 13, 0)),
            ended_at=iso(utc(2026, 6, 22, 14, 0)),
            logged_by=str(s3["member"].id),
        )
        assert later.status_code == 400 and later.json()["code"] == "day_limit"

    def test_update_cannot_push_a_day_past_24h(self, s3):
        base = IssueWorklog.objects.create(
            project=s3["p"], issue=s3["issue"], logged_by=s3["member"], duration=720, logged_date=date(2026, 6, 22)
        )
        IssueWorklog.objects.create(
            project=s3["p"],
            issue=s3["issue"],
            logged_by=s3["member"],
            duration=900,
            logged_date=date(2026, 6, 22),
        )
        r = _patch(s3["wsadmin"], s3, base, duration=600)
        assert r.status_code == 400 and r.json()["code"] == "day_limit"
        assert _patch(s3["wsadmin"], s3, base, duration=540).status_code == 200  # 540 + 900 = 1440

    def test_update_moving_to_a_full_day_is_refused(self, s3):
        entry(s3, s3["member"], utc(2026, 6, 23, 0, 0), utc(2026, 6, 23, 12, 0))
        IssueWorklog.objects.create(
            project=s3["p"], issue=s3["issue"], logged_by=s3["member"], duration=720, logged_date=date(2026, 6, 23)
        )
        wl = IssueWorklog.objects.create(
            project=s3["p"], issue=s3["issue"], logged_by=s3["member"], duration=30, logged_date=date(2026, 6, 22)
        )
        r = _patch(s3["wsadmin"], s3, wl, logged_date="2026-06-23")
        assert r.status_code == 400 and r.json()["code"] == "day_limit"


class TestLegacyUpdate:
    def test_update_to_a_day_with_tracking_off_is_refused(self, s3):
        set_member_tracking(s3["workspace"].id, s3["member"].id, False, date(2026, 6, 23), s3["wsadmin"])
        wl = IssueWorklog.objects.create(
            project=s3["p"], issue=s3["issue"], logged_by=s3["member"], duration=30, logged_date=date(2026, 6, 22)
        )
        r = _patch(s3["wsadmin"], s3, wl, logged_date="2026-06-23")
        assert r.status_code == 403 and r.json()["code"] == "tracking_disabled"
        wl.refresh_from_db()
        assert wl.logged_date == date(2026, 6, 22)

    def test_interval_entry_keeps_start_end_in_step_with_date_and_duration(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))  # 10:00 Dhaka, 60 min
        r = _patch(s3["wsadmin"], s3, wl, duration=90)
        assert r.status_code == 200, r.json()
        wl.refresh_from_db()
        assert wl.duration == 90 and wl.ended_at == utc(2026, 6, 22, 5, 30) and wl.started_at == utc(2026, 6, 22, 4, 0)
        r = _patch(s3["wsadmin"], s3, wl, logged_date="2026-06-23")
        assert r.status_code == 200, r.json()
        wl.refresh_from_db()
        assert wl.logged_date == date(2026, 6, 23)
        assert wl.started_at == utc(2026, 6, 23, 4, 0) and wl.ended_at == utc(2026, 6, 23, 5, 30)

    def test_duration_edit_that_now_overlaps_is_refused(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
        entry(s3, s3["member"], utc(2026, 6, 22, 5, 30), utc(2026, 6, 22, 6, 0))
        r = _patch(s3["wsadmin"], s3, wl, duration=120)
        assert r.status_code == 400 and r.json()["code"] == "overlap"
        wl.refresh_from_db()
        assert wl.duration == 60

    def test_date_move_that_now_overlaps_is_refused(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
        entry(s3, s3["member"], utc(2026, 6, 23, 4, 30), utc(2026, 6, 23, 5, 30))
        r = _patch(s3["wsadmin"], s3, wl, logged_date="2026-06-23")
        assert r.status_code == 400 and r.json()["code"] == "overlap"

    def test_future_date_is_still_refused(self, s3):
        wl = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 5, 0))
        r = _patch(s3["wsadmin"], s3, wl, logged_date="2026-06-30")
        assert r.status_code == 400 and r.json()["code"] == "invalid_interval"


# ---------------------------------------------------------------------------
# 6. missing_time approval re-checks the requester's standing
# ---------------------------------------------------------------------------


def _pending_missing(s3):
    with freeze_time(NOW):
        r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 5, 0))
    assert r.status_code == 201, r.json()
    return r.json()["id"]


def _approve(s3, rid):
    with freeze_time(NOW):
        return act(client_for(s3["pa"]), s3, rid, "approve")


class TestMissingTimeRecheck:
    def test_still_approvable_when_nothing_changed(self, s3):
        rid = _pending_missing(s3)
        r = _approve(s3, rid)
        assert r.status_code == 200, r.json()
        assert IssueWorklog.objects.filter(logged_by=s3["member"]).count() == 1

    def test_removed_member_is_refused_and_request_stays_pending(self, s3):
        rid = _pending_missing(s3)
        ProjectMember.objects.filter(project=s3["p"], member=s3["member"]).update(is_active=False)
        r = _approve(s3, rid)
        assert r.status_code == 409 and r.json()["code"] == "not_tracked"
        assert req(rid).status == "pending"
        assert not IssueWorklog.objects.filter(logged_by=s3["member"]).exists()

    def test_tracking_turned_off_is_refused(self, s3):
        rid = _pending_missing(s3)
        set_member_tracking(s3["workspace"].id, s3["member"].id, False, date(2026, 6, 22), s3["wsadmin"])
        r = _approve(s3, rid)
        assert r.status_code == 409 and r.json()["code"] == "tracking_disabled"
        assert req(rid).status == "pending"
        assert not IssueWorklog.objects.filter(logged_by=s3["member"]).exists()

    def test_project_tracking_switched_off_is_refused(self, s3):
        rid = _pending_missing(s3)
        s3["p"].is_time_tracking_enabled = False
        s3["p"].save()
        r = _approve(s3, rid)
        assert r.status_code == 409 and r.json()["code"] == "project_tracking_disabled"
        assert req(rid).status == "pending"
        # The approver can still close it out by rejecting.
        with freeze_time(NOW):
            rej = act(client_for(s3["pa"]), s3, rid, "reject", note="project no longer tracked")
        assert rej.status_code == 200 and req(rid).status == "rejected"
