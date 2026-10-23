# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — submitting requests, the three lists, append-only history,
approver routing, notifications and the auto-stop review (DESIGN §2 items 9, 15-21, §6 S3)."""

import pytest
from django.db import DatabaseError, connection, transaction
from freezegun import freeze_time

from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import (
    IssueWorklog,
    Notification,
    TimeEntryRequest,
    TimeEntryRequestEvent,
    TimeTrackingSetting,
)
from plane.utils.time_tracking import approvers
from plane.utils.work_item_move import REPOINT_BY_ISSUE

from .time_tracking_s2_support import IDLE_OFF, idle_every
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


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_missing_time_request_is_pending_and_moves_nothing(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 5, 30))
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["type"] == "missing_time" and body["status"] == "pending"
        assert body["new_duration"] == 150 and body["work_date"] == "2026-06-22"
        assert body["route"] == "project_admins" and body["can_withdraw"] is True and body["can_approve"] is False
        assert not IssueWorklog.objects.filter(logged_by=s3["member"]).exists()
        events = list(TimeEntryRequestEvent.objects.filter(request_id=body["id"]))
        assert [e.action for e in events] == ["submitted"]
        assert events[0].actor_id == s3["member"].id and events[0].actor_role == "member"

    @pytest.mark.parametrize(
        "payload, code",
        [
            ({"reason": ""}, "reason_required"),
            ({"started_at": "2026-06-22T05:00:00+00:00", "ended_at": "2026-06-22T04:00:00+00:00"}, "invalid_interval"),
            ({"started_at": "2026-06-24T05:00:00+00:00", "ended_at": "2026-06-24T07:00:00+00:00"}, "invalid_interval"),
            ({"started_at": "2026-06-20T00:00:00+00:00", "ended_at": "2026-06-21T01:00:00+00:00"}, "invalid_interval"),
            ({"started_at": "2026-06-22T05:00:00", "ended_at": "2026-06-22T06:00:00"}, "invalid_interval"),
        ],
    )
    def test_invalid_missing_time_is_400(self, s3, payload, code):
        data = {
            "type": "missing_time",
            "issue_id": str(s3["issue"].id),
            "started_at": "2026-06-22T03:00:00+00:00",
            "ended_at": "2026-06-22T04:00:00+00:00",
            "reason": "forgot",
            **payload,
        }
        with freeze_time(NOW):
            r = submit(client_for(s3["member"]), s3, **data)
        assert r.status_code == 400 and r.json()["code"] == code
        assert not TimeEntryRequest.objects.exists()

    def test_overlap_with_own_entry_or_running_timer_is_refused(self, s3):
        entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        c = client_for(s3["member"])
        with freeze_time(NOW):
            r = missing(c, s3, utc(2026, 6, 22, 5, 0), utc(2026, 6, 22, 7, 0))
            assert r.status_code == 400 and r.json()["code"] == "overlap"
            # FORK: S3b — the clash is described so the UI can list it.
            clash = r.json()["overlapping"]
            assert [c_["id"] for c_ in clash] == r.json()["overlapping_ids"]
            assert clash[0]["issue_ref"] and clash[0]["started_at"] and clash[0]["ended_at"]
            # Touching ends do not overlap.
            assert missing(c, s3, utc(2026, 6, 22, 6, 0), utc(2026, 6, 22, 7, 0)).status_code == 201
            # Someone else's entry at the same time is no overlap.
            entry(s3, s3["member2"], utc(2026, 6, 23, 4, 0), utc(2026, 6, 23, 6, 0))
            assert missing(c, s3, utc(2026, 6, 23, 4, 0), utc(2026, 6, 23, 5, 0)).status_code == 201
            assert (
                c.post(ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json").status_code
                == 201
            )
        with freeze_time(utc(2026, 6, 24, 7, 0)):
            r = missing(c, s3, utc(2026, 6, 24, 5, 0), utc(2026, 6, 24, 6, 30))
        assert r.status_code == 400 and r.json()["code"] == "overlap" and r.json()["overlaps_timer"] is True

    def test_change_and_deletion_requests_on_own_entries_only(self, s3):
        mine = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        theirs = entry(s3, s3["member2"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        c = client_for(s3["member"])
        with freeze_time(NOW):
            r = submit(
                c,
                s3,
                type="time_change",
                worklog_id=str(mine.id),
                started_at=iso(utc(2026, 6, 22, 4, 0)),
                ended_at=iso(utc(2026, 6, 22, 5, 0)),
                reason="stopped late",
            )
            assert r.status_code == 201, r.json()
            assert r.json()["old_duration"] == 120 and r.json()["new_duration"] == 60
            # One pending request per entry.
            r2 = submit(c, s3, type="deletion", worklog_id=str(mine.id), reason="wrong task")
            assert r2.status_code == 409 and r2.json()["code"] == "pending_exists"
            r3 = submit(c, s3, type="deletion", worklog_id=str(theirs.id), reason="x")
            assert r3.status_code == 404
        # The entry keeps its old values until approved.
        mine.refresh_from_db()
        assert mine.duration == 120 and mine.ended_at == utc(2026, 6, 22, 6, 0)

    def test_locked_entry_is_refused(self, s3):
        locked = entry(s3, s3["member"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0), is_locked=True)
        with freeze_time(NOW):
            r = submit(client_for(s3["member"]), s3, type="deletion", worklog_id=str(locked.id), reason="x")
        assert r.status_code == 400 and r.json()["code"] == "entry_locked"

    def test_workspace_admin_edits_directly(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["wsadmin"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0))
        assert r.status_code == 400 and r.json()["code"] == "edit_directly"

    def test_not_a_member_of_the_items_project_is_403(self, s3):
        with freeze_time(NOW):
            r = missing(
                client_for(s3["member2"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0), issue=s3["issue_q"]
            )
        assert r.status_code == 403 and r.json()["code"] == "not_tracked"

    def test_guest_is_403_everywhere(self, s3):
        g = client_for(s3["guest"])
        with freeze_time(NOW):
            assert missing(g, s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).status_code == 403
            pending = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            for path in ("time/requests/", "time/requests/events/", f"time/requests/{pending['id']}/history/"):
                assert g.get(ws_url(s3, path)).status_code == 403, path
            for verb in ("approve", "reject", "withdraw"):
                assert act(g, s3, pending["id"], verb, note="n").status_code == 403, verb
        assert req(pending["id"]).status == "pending"


# ---------------------------------------------------------------------------
# Approver routing
# ---------------------------------------------------------------------------


class TestApproverMatrix:
    def test_member_request_goes_to_the_items_project_admins(self, s3):
        with freeze_time(NOW):
            on_p = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            on_q = missing(
                client_for(s3["member"]), s3, utc(2026, 6, 22, 5, 0), utc(2026, 6, 22, 6, 0), issue=s3["issue_q"]
            ).json()
        p, q = req(on_p["id"]), req(on_q["id"])
        assert approvers.can_approve(s3["pa"].id, p) and not approvers.can_approve(s3["pa_q"].id, p)
        assert approvers.can_approve(s3["pa_q"].id, q) and not approvers.can_approve(s3["pa"].id, q)
        assert approvers.can_approve(s3["wsadmin"].id, p) and approvers.can_approve(s3["wsadmin"].id, q)
        assert not approvers.can_approve(s3["member2"].id, p) and not approvers.can_approve(s3["guest"].id, p)
        assert not approvers.can_approve(s3["member"].id, p)  # own

    def test_project_admin_request_goes_to_workspace_admins(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["pa"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
        assert r["route"] == "workspace_admins"
        own = req(r["id"])
        assert not approvers.can_approve(s3["pa"].id, own)
        assert approvers.can_approve(s3["wsadmin"].id, own)
        # Another project admin of the same project can't approve a fellow admin's time.
        other_pa = s3["member2"]
        from plane.db.models import ProjectMember

        ProjectMember.objects.filter(project=s3["p"], member=other_pa).update(role=20)
        assert not approvers.can_approve(other_pa.id, own)
        with freeze_time(NOW):
            assert act(client_for(s3["pa"]), s3, r["id"], "approve").json()["code"] == "own_request"
            assert act(client_for(other_pa), s3, r["id"], "approve").status_code == 404

    def test_workspace_admin_cannot_approve_own(self, s3):
        mine = entry(s3, s3["wsadmin"], utc(2026, 6, 22, 4, 0), utc(2026, 6, 22, 6, 0))
        r = TimeEntryRequest.objects.create(
            workspace=s3["workspace"],
            project=s3["p"],
            requester=s3["wsadmin"],
            issue=s3["issue"],
            worklog=mine,
            type="auto_stop_review",
            work_date=mine.logged_date,
            old_started_at=mine.started_at,
            old_ended_at=mine.ended_at,
            old_duration=mine.duration,
        )
        assert not approvers.can_approve(s3["wsadmin"].id, r)
        with freeze_time(NOW):
            resp = act(client_for(s3["wsadmin"]), s3, r.id, "approve")
        assert resp.status_code == 403 and resp.json()["code"] == "own_request"

    def test_moved_item_reroutes_to_the_new_projects_admins(self, s3):
        assert (TimeEntryRequest, "issue") in REPOINT_BY_ISSUE
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
        TimeEntryRequest.objects.filter(pk=r["id"]).update(project=s3["q"])  # what work_item_move does
        moved = req(r["id"])
        assert approvers.can_approve(s3["pa_q"].id, moved) and not approvers.can_approve(s3["pa"].id, moved)


# ---------------------------------------------------------------------------
# Lists, history, events
# ---------------------------------------------------------------------------


class TestLists:
    def _seed(self, s3):
        with freeze_time(NOW):
            a = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            b = missing(
                client_for(s3["member"]), s3, utc(2026, 6, 22, 5, 0), utc(2026, 6, 22, 6, 0), issue=s3["issue_q"]
            ).json()
            c = missing(client_for(s3["member2"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
            d = missing(client_for(s3["pa"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
        return a, b, c, d

    @staticmethod
    def _ids(resp):
        assert resp.status_code == 200, resp.json()
        return {r["id"] for r in resp.json()["results"]}

    def test_mine_and_review_scopes(self, s3):
        a, b, c, d = self._seed(s3)
        get = lambda user, q="": client_for(user).get(ws_url(s3, f"time/requests/?{q}"))  # noqa: E731
        assert self._ids(get(s3["member"])) == {a["id"], b["id"]}
        assert self._ids(get(s3["member"], "scope=review")) == set()
        assert self._ids(get(s3["pa"], "scope=review")) == {a["id"], c["id"]}
        assert self._ids(get(s3["pa_q"], "scope=review")) == {b["id"]}
        assert self._ids(get(s3["wsadmin"], "scope=review")) == {a["id"], b["id"], c["id"], d["id"]}
        # Filters by user and project.
        assert self._ids(get(s3["wsadmin"], f"scope=review&user_id={s3['member'].id}")) == {a["id"], b["id"]}
        assert self._ids(get(s3["wsadmin"], f"scope=review&project_id={s3['q'].id}")) == {b["id"]}
        assert self._ids(get(s3["pa"], f"scope=review&user_id={s3['member2'].id}")) == {c["id"]}
        assert self._ids(get(s3["member"], f"project_id={s3['p'].id}")) == {a["id"]}
        assert self._ids(get(s3["wsadmin"], "scope=review&status=approved")) == set()
        assert get(s3["wsadmin"], "status=nope").status_code == 400
        assert get(s3["wsadmin"], "user_id=nope").status_code == 400
        assert get(s3["wsadmin"], "scope=all").status_code == 400

    def test_history_visibility(self, s3, event_guard):
        a, *_ = self._seed(s3)
        url = ws_url(s3, f"time/requests/{a['id']}/history/")
        for user in ("member", "pa", "wsadmin"):
            r = client_for(s3[user]).get(url)
            assert r.status_code == 200, user
            assert [e["action"] for e in r.json()["events"]] == ["submitted"]
        for user in ("member2", "pa_q"):
            assert client_for(s3[user]).get(url).status_code == 404, user

    def test_events_list_filters(self, s3):
        a, b, c, d = self._seed(s3)
        with freeze_time(utc(2026, 6, 25, 6, 0)):
            assert act(client_for(s3["pa"]), s3, a["id"], "approve", note="ok").status_code == 200
        get = lambda user, q="": client_for(user).get(ws_url(s3, f"time/requests/events/?{q}"))  # noqa: E731

        def pairs(resp):
            assert resp.status_code == 200, resp.json()
            return sorted((e["request_id"], e["action"]) for e in resp.json()["results"])

        assert pairs(get(s3["pa"])) == sorted(
            [(a["id"], "submitted"), (a["id"], "approved"), (a["id"], "applied"), (c["id"], "submitted")]
        )
        assert pairs(get(s3["pa"], f"user_id={s3['member2'].id}")) == [(c["id"], "submitted")]
        assert pairs(get(s3["wsadmin"], f"project_id={s3['q'].id}")) == [(b["id"], "submitted")]
        assert pairs(get(s3["pa"], "start_date=2026-06-25&end_date=2026-06-25")) == sorted(
            [(a["id"], "approved"), (a["id"], "applied")]
        )
        assert pairs(get(s3["pa"], "end_date=2026-06-24")) == sorted([(a["id"], "submitted"), (c["id"], "submitted")])
        assert pairs(get(s3["member"], "scope=mine&action=approved")) == [(a["id"], "approved")]
        assert pairs(get(s3["member"])) == []  # review scope: a member approves nothing
        first = get(s3["pa"]).json()["results"][0]
        assert first["request"]["requester_id"] == str(s3["member"].id) and first["actor"]["id"] == str(s3["pa"].id)


# ---------------------------------------------------------------------------
# Append-only history
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def _event(self, s3):
        with freeze_time(NOW):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
        return TimeEntryRequestEvent.objects.get(request_id=r["id"])

    def test_orm_update_and_delete_raise(self, s3):
        event = self._event(s3)
        event.note = "rewritten"
        with pytest.raises(ValueError):
            event.save()
        with pytest.raises(ValueError):
            event.delete()
        with pytest.raises(ValueError):
            TimeEntryRequestEvent.objects.filter(pk=event.pk).update(note="x")
        with pytest.raises(ValueError):
            TimeEntryRequestEvent.objects.filter(pk=event.pk).delete()
        with pytest.raises(ValueError):
            req(event.request_id).delete()
        assert TimeEntryRequestEvent.objects.get(pk=event.pk).note == "forgot the timer"

    def test_raw_sql_update_and_direct_delete_are_rejected(self, s3, event_guard):
        event = self._event(s3)
        for sql in (
            "UPDATE time_entry_request_events SET note = 'x' WHERE id = %s",
            "DELETE FROM time_entry_request_events WHERE id = %s",
        ):
            with pytest.raises(DatabaseError, match="append-only"):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql, [str(event.pk)])
        assert TimeEntryRequestEvent.objects.get(pk=event.pk).note == "forgot the timer"

    def test_hard_delete_of_the_request_still_cascades(self, s3, event_guard):
        event = self._event(s3)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM time_entry_requests WHERE id = %s", [str(event.request_id)])
        assert not TimeEntryRequestEvent.objects.filter(pk=event.pk).exists()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class TestNotifications:
    def test_submit_notifies_approvers_and_review_notifies_requester(self, s3, django_capture_on_commit_callbacks):
        with freeze_time(NOW), django_capture_on_commit_callbacks(execute=True):
            r = missing(client_for(s3["member"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)).json()
        sent = Notification.objects.filter(sender="in_app:time_tracking:request_submitted")
        assert {n.receiver_id for n in sent} == {s3["pa"].id, s3["wsadmin"].id}
        n = sent.first()
        assert n.entity_name == "issue" and n.data["time_tracking"]["request_id"] == r["id"]
        with freeze_time(NOW), django_capture_on_commit_callbacks(execute=True):
            act(client_for(s3["pa"]), s3, r["id"], "reject", note="no")
        rejected = Notification.objects.get(sender="in_app:time_tracking:request_rejected")
        assert rejected.receiver_id == s3["member"].id and rejected.triggered_by_id == s3["pa"].id

    def test_project_admin_request_notifies_workspace_admins(self, s3, django_capture_on_commit_callbacks):
        with freeze_time(NOW), django_capture_on_commit_callbacks(execute=True):
            missing(client_for(s3["pa"]), s3, utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0))
        sent = Notification.objects.filter(sender="in_app:time_tracking:request_submitted")
        assert {n.receiver_id for n in sent} == {s3["wsadmin"].id}


# ---------------------------------------------------------------------------
# Auto-stop review
# ---------------------------------------------------------------------------


class TestAutoStopReview:
    def _start(self, s3, at):
        with freeze_time(at):
            r = client_for(s3["member"]).post(
                ws_url(s3, "time/timer/start/"), {"issue_id": str(s3["issue"].id)}, format="json"
            )
        assert r.status_code == 201, r.json()

    def test_auto_stop_opens_one_review_for_the_session(self, s3, django_capture_on_commit_callbacks):
        with freeze_time(utc(2026, 6, 22, 10, 0)):  # 16:00 Dhaka: the 14 h run crosses midnight
            set_settings(s3["workspace"], **IDLE_OFF)
        self._start(s3, utc(2026, 6, 22, 10, 0))
        with freeze_time(utc(2026, 6, 23, 0, 5)), django_capture_on_commit_callbacks(execute=True):
            assert time_tracking_timer_sweep()["limits"] == 1
        pieces = list(IssueWorklog.objects.filter(logged_by=s3["member"]).order_by("started_at"))
        assert len(pieces) == 2 and all(p.auto_stopped for p in pieces)
        review = TimeEntryRequest.objects.get(requester=s3["member"])
        assert review.type == "auto_stop_review" and review.status == "pending"
        assert review.worklog_id == pieces[-1].id and review.old_duration == 840
        assert review.old_started_at == utc(2026, 6, 22, 10, 0) and review.old_ended_at == utc(2026, 6, 23, 0, 0)
        event = TimeEntryRequestEvent.objects.get(request=review)
        assert event.action == "submitted" and event.actor_id is None and event.actor_role == "system"
        assert Notification.objects.filter(sender="in_app:time_tracking:request_submitted", receiver=s3["pa"]).exists()
        # The entry counts already; nothing about the settings row was written by the stop.
        assert sum(p.duration for p in pieces) == 840
        assert TimeTrackingSetting.objects.get(workspace=s3["workspace"]).updated_at == utc(2026, 6, 22, 10, 0)

    def test_idle_stop_opens_no_review(self, s3):
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s3["workspace"], **idle_every(60))
        self._start(s3, utc(2026, 6, 22, 4, 0))
        with freeze_time(utc(2026, 6, 22, 5, 6)):
            assert time_tracking_timer_sweep()["limits"] == 1
        assert IssueWorklog.objects.get(logged_by=s3["member"]).idle_stopped is True
        assert not TimeEntryRequest.objects.exists()

    def test_member_change_request_supersedes_the_review(self, s3, event_guard):
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s3["workspace"], **IDLE_OFF)
        self._start(s3, utc(2026, 6, 22, 0, 0))
        with freeze_time(utc(2026, 6, 22, 14, 5)):
            time_tracking_timer_sweep()
        wl = IssueWorklog.objects.get(logged_by=s3["member"])
        review = TimeEntryRequest.objects.get(type="auto_stop_review")
        with freeze_time(utc(2026, 6, 22, 15, 0)):
            r = submit(
                client_for(s3["member"]),
                s3,
                type="time_change",
                worklog_id=str(wl.id),
                started_at=iso(wl.started_at),
                ended_at=iso(utc(2026, 6, 22, 9, 0)),
                reason="left at 15:00",
            )
        assert r.status_code == 201, r.json()
        review.refresh_from_db()
        assert review.status == "withdrawn"
        superseded = TimeEntryRequestEvent.objects.get(request=review, action="superseded")
        assert superseded.payload == {"by": r.json()["id"]}
