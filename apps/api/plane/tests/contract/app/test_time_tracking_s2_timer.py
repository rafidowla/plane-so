# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — timer v2: guests never track time, start rules
(Cancelled / untracked / project off, Todo -> In Progress), the two start
endpoints agree, the 10 h reminder, the 14 h auto-stop and the "still working?"
idle check."""

import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from rest_framework import status

from plane.bgtasks import issue_activities_task
from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import (
    IssueActivity,
    IssueWorklog,
    Notification,
    TimeTrackingAuditLog,
    TimeTrackingSetting,
    WorklogTimer,
)
from plane.utils.time_tracking import limits
from plane.utils.time_tracking.tracking import set_member_tracking

from .time_tracking_s2_support import (  # noqa: F401 (fixtures)
    _load_urlconf,
    client_for,
    IDLE_OFF,
    idle_every,
    issue_timer_url,
    join,
    make_issue,
    make_project,
    make_user,
    set_settings,
    utc,
    ws_url,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _start(client, s, issue):
    return client.post(ws_url(s, "time/timer/start/"), {"issue_id": str(issue.id)}, format="json")


def _worklog_url(s, issue=None):
    issue = issue or s["issue"]
    return f"/api/workspaces/{s['slug']}/projects/{issue.project_id}/issues/{issue.id}/worklogs/"


# ---------------------------------------------------------------------------
# Guests never track time
# ---------------------------------------------------------------------------


class TestGuests:
    def test_project_guest_cannot_start_a_timer_on_either_endpoint(self, s2):
        guest = make_user(s2["workspace"], ws_role=5)
        join(s2["project"], guest, role=5)
        c = client_for(guest)
        assert c.post(issue_timer_url(s2), {}, format="json").status_code == status.HTTP_403_FORBIDDEN
        assert _start(c, s2, s2["issue"]).status_code == status.HTTP_403_FORBIDDEN
        assert not WorklogTimer.objects.filter(user=guest).exists()

    def test_workspace_member_who_is_a_project_guest_cannot_start(self, s2):
        member = make_user(s2["workspace"], ws_role=15)
        join(s2["project"], member, role=5)
        c = client_for(member)
        assert c.post(issue_timer_url(s2), {}, format="json").status_code == status.HTTP_403_FORBIDDEN
        r = _start(c, s2, s2["issue"])
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert r.json()["code"] == "not_tracked"
        assert not WorklogTimer.objects.filter(user=member).exists()

    def test_guest_cannot_create_a_manual_entry(self, s2):
        guest = make_user(s2["workspace"], ws_role=5)
        join(s2["project"], guest, role=5)
        r = client_for(guest).post(_worklog_url(s2), {"duration": 30, "logged_date": "2026-06-22"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert not IssueWorklog.objects.exists()

    def test_guest_gets_only_the_total_on_an_item_they_can_see(self, s2):
        admin = client_for(s2["user"])
        for minutes in (30, 45):
            assert (
                admin.post(
                    _worklog_url(s2), {"duration": minutes, "logged_date": "2026-06-22"}, format="json"
                ).status_code
                == 201
            )
        s2["project"].guest_view_all_features = True
        s2["project"].save()
        guest = make_user(s2["workspace"], ws_role=5)
        join(s2["project"], guest, role=5)
        r = client_for(guest).get(_worklog_url(s2))
        assert r.status_code == status.HTTP_200_OK
        assert r.json() == {"total_minutes": 75}

    def test_guest_gets_nothing_on_an_item_they_cannot_see(self, s2):
        client_for(s2["user"]).post(_worklog_url(s2), {"duration": 30, "logged_date": "2026-06-22"}, format="json")
        guest = make_user(s2["workspace"], ws_role=5)
        join(s2["project"], guest, role=5)
        # guest_view_all_features is off and the guest did not create the item.
        assert client_for(guest).get(_worklog_url(s2)).status_code == status.HTTP_403_FORBIDDEN

    def test_guest_running_timer_get_is_empty(self, s2):
        guest = make_user(s2["workspace"], ws_role=5)
        join(s2["project"], guest, role=5)
        r = client_for(guest).get(ws_url(s2, "time/timer/"))
        # Readable (the indicator polls it) but a guest never has a timer to show.
        assert r.status_code == status.HTTP_200_OK
        assert set(r.json()) == {"server_now"}


# ---------------------------------------------------------------------------
# Start rules
# ---------------------------------------------------------------------------


class TestStartRules:
    def test_cancelled_item_is_refused_identically_by_both_endpoints(self, s2):
        issue = make_issue(s2["project"], s2["states"]["cancelled"], "Gone", s2["user"])
        c = client_for(s2["user"])
        a = c.post(issue_timer_url(s2, issue), {}, format="json")
        b = _start(c, s2, issue)
        assert a.status_code == b.status_code == status.HTTP_400_BAD_REQUEST
        assert a.json() == b.json() == {"error": "This work item is cancelled.", "code": "issue_cancelled"}
        assert not WorklogTimer.objects.exists()

    def test_untracked_member_is_refused(self, s2):
        member = make_user(s2["workspace"])
        join(s2["project"], member)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_member_tracking(s2["workspace"].id, member.id, False, date(2026, 6, 22), s2["user"])
            r = _start(client_for(member), s2, s2["issue"])
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert r.json()["code"] == "tracking_disabled"

    def test_project_with_tracking_off_is_refused(self, s2):
        project, states = make_project(s2["workspace"], s2["user"], "Untracked", "UNT", tracked=False)
        join(project, s2["user"], role=20)
        issue = make_issue(project, states["todo"], "Nope", s2["user"])
        r = _start(client_for(s2["user"]), s2, issue)
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert r.json()["code"] == "project_tracking_disabled"

    def test_item_outside_the_callers_projects_is_404(self, s2):
        other, states = make_project(s2["workspace"], s2["user"], "Other", "OTH")
        issue = make_issue(other, states["todo"], "Secret", s2["user"])
        member = make_user(s2["workspace"])
        join(s2["project"], member)
        r = _start(client_for(member), s2, issue)
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert "Secret" not in r.content.decode()

    def test_bad_issue_id_is_400(self, s2):
        r = client_for(s2["user"]).post(ws_url(s2, "time/timer/start/"), {"issue_id": "nope"}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST


class TestStartMovesToInProgress:
    def _run(self, django_capture_on_commit_callbacks, fn):
        task = issue_activities_task.issue_activity
        with (
            patch.object(task, "delay", side_effect=lambda **kw: task(**kw)) as delay,
            patch.object(issue_activities_task, "notifications"),
            django_capture_on_commit_callbacks(execute=True),
        ):
            r = fn()
        return r, delay

    def test_todo_moves_to_the_first_started_state_and_writes_activity(self, s2, django_capture_on_commit_callbacks):
        c = client_for(s2["user"])
        r, delay = self._run(django_capture_on_commit_callbacks, lambda: _start(c, s2, s2["issue"]))
        assert r.status_code == status.HTTP_201_CREATED, r.json()
        s2["issue"].refresh_from_db()
        # "In Progress" (sequence 10) wins over "In Review" (sequence 20).
        assert s2["issue"].state_id == s2["states"]["in_progress"].id
        assert r.json()["state_change"] == {
            "from": {"id": str(s2["states"]["todo"].id), "name": "Todo", "group": "unstarted"},
            "to": {"id": str(s2["states"]["in_progress"].id), "name": "In Progress", "group": "started"},
        }
        assert delay.call_count == 1
        assert delay.call_args.kwargs["type"] == "issue.activity.updated"
        activity = IssueActivity.objects.get(issue=s2["issue"], field="state")
        assert (activity.old_value, activity.new_value) == ("Todo", "In Progress")
        assert activity.actor_id == s2["user"].id
        # created_by/updated_by survive the system-side save.
        assert s2["issue"].created_by_id == s2["user"].id

    def test_backlog_also_moves(self, s2, django_capture_on_commit_callbacks):
        issue = make_issue(s2["project"], s2["states"]["backlog"], "B", s2["user"])
        c = client_for(s2["user"])
        r, _ = self._run(
            django_capture_on_commit_callbacks, lambda: c.post(issue_timer_url(s2, issue), {}, format="json")
        )
        assert r.status_code == status.HTTP_201_CREATED
        issue.refresh_from_db()
        assert issue.state_id == s2["states"]["in_progress"].id

    def test_started_and_done_items_keep_their_state(self, s2, django_capture_on_commit_callbacks):
        c = client_for(s2["user"])
        for key in ("review", "done"):
            issue = make_issue(s2["project"], s2["states"][key], key, s2["user"])
            r, delay = self._run(django_capture_on_commit_callbacks, lambda: _start(c, s2, issue))
            assert r.status_code == status.HTTP_201_CREATED
            assert r.json()["state_change"] is None
            assert delay.call_count == 0
            issue.refresh_from_db()
            assert issue.state_id == s2["states"][key].id

    def test_both_start_endpoints_return_the_same_shape(self, s2, django_capture_on_commit_callbacks):
        other = make_user(s2["workspace"])
        join(s2["project"], other)
        second = make_issue(s2["project"], s2["states"]["todo"], "Issue 2", s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            a, _ = self._run(
                django_capture_on_commit_callbacks,
                lambda: client_for(s2["user"]).post(issue_timer_url(s2), {}, format="json"),
            )
            b, _ = self._run(django_capture_on_commit_callbacks, lambda: _start(client_for(other), s2, second))
        assert a.status_code == b.status_code == status.HTTP_201_CREATED
        a, b = a.json(), b.json()
        assert set(a) == set(b)
        for key in ("started_at", "check", "remind_at", "auto_stop_at", "server_now"):
            assert a[key] == b[key]
        assert a["state_change"]["to"] == b["state_change"]["to"]
        assert a["issue_detail"]["ref"] == "TTP-1" and b["issue_detail"]["ref"] == "TTP-2"

    def test_starting_stops_the_previous_timer(self, s2):
        second = make_issue(s2["project"], s2["states"]["todo"], "Issue 2", s2["user"])
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            assert _start(c, s2, s2["issue"]).status_code == 201
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            r = _start(c, s2, second)
        assert r.status_code == 201
        assert r.json()["stopped"]["duration"] == 30
        assert WorklogTimer.objects.get(user=s2["user"]).issue_id == second.id


# ---------------------------------------------------------------------------
# Workspace timer endpoints: GET / stop
# ---------------------------------------------------------------------------


class TestTimerEndpoints:
    def test_get_shows_the_schedule(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            assert _start(c, s2, s2["issue"]).status_code == 201
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            r = c.get(ws_url(s2, "time/timer/"))
        assert r.status_code == 200
        d = r.json()
        assert d["issue"] == str(s2["issue"].id)
        assert d["issue_detail"]["ref"] == "TTP-1"
        assert d["idle_check_enabled"] is True
        assert d["idle_grace_seconds"] == 300
        # The check time is never revealed before it is due.
        assert d["check"] is None
        for gone in ("next_check_at", "check_deadline_at", "idle_check_minutes"):
            assert gone not in d
        assert d["remind_at"].startswith("2026-06-22T14:00:00")
        assert d["auto_stop_at"].startswith("2026-06-22T18:00:00")
        assert d["reminded"] is False
        assert d["server_now"].startswith("2026-06-22T04:30:00")

    def test_get_with_nothing_running(self, s2):
        r = client_for(s2["user"]).get(ws_url(s2, "time/timer/"))
        assert r.status_code == 200
        assert set(r.json()) == {"server_now"}

    def test_stop_endpoint(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 4, 40)):
            r = c.post(ws_url(s2, "time/timer/stop/"), {"description": "done"}, format="json")
        assert r.status_code == status.HTTP_201_CREATED
        d = r.json()
        assert d["total_duration"] == 40
        assert d["description"] == "done"
        assert d["issue_detail"]["ref"] == "TTP-1"
        assert len(d["pieces"]) == 1
        assert c.post(ws_url(s2, "time/timer/stop/"), {}, format="json").status_code == 404


# ---------------------------------------------------------------------------
# 10 h reminder and 14 h auto-stop
# ---------------------------------------------------------------------------


class TestReminderAndAutoStop:
    def test_reminder_once_at_ten_hours(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 9, 59)):
            assert time_tracking_timer_sweep()["reminded"] == 0
        with freeze_time(utc(2026, 6, 22, 10, 1)):
            assert time_tracking_timer_sweep()["reminded"] == 1
        with freeze_time(utc(2026, 6, 22, 10, 5)):
            assert time_tracking_timer_sweep()["reminded"] == 0
            assert c.get(ws_url(s2, "time/timer/")).json()["reminded"] is True
        n = Notification.objects.get(receiver=s2["user"])
        assert n.sender == "in_app:time_tracking:reminder"
        assert n.entity_name == "issue"
        assert n.entity_identifier == s2["issue"].id
        assert n.data["issue"]["identifier"] == "TTP"
        assert n.data["time_tracking"]["kind"] == "timer_reminder"
        assert WorklogTimer.objects.get(user=s2["user"]).reminded_at == utc(2026, 6, 22, 10, 1)

    def test_auto_stop_at_fourteen_hours_with_idle_check_off(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 0, 0)):  # 06:00 Dhaka
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 13, 59)):
            assert time_tracking_timer_sweep()["limits"] == 0
        with freeze_time(utc(2026, 6, 22, 14, 7)):
            assert time_tracking_timer_sweep()["limits"] == 1
        assert not WorklogTimer.objects.exists()
        wl = IssueWorklog.objects.get(logged_by=s2["user"])
        # Stopped AT the limit (not when the sweep ran) and still counted.
        assert wl.ended_at == utc(2026, 6, 22, 14, 0)
        assert wl.duration == 840
        assert wl.auto_stopped is True and wl.idle_stopped is False
        audit_row = TimeTrackingAuditLog.objects.get(target_id=wl.id)
        assert audit_row.action == "auto_stop" and audit_row.actor_id is None
        assert audit_row.after["reason"] == "auto_stop"

    def test_late_user_stop_is_clamped_to_the_auto_stop(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 16, 0)):  # sweep never ran
            r = c.delete(issue_timer_url(s2))
        assert r.status_code == 201
        assert r.json()["total_duration"] == 840
        assert IssueWorklog.objects.get(logged_by=s2["user"]).auto_stopped is True


# ---------------------------------------------------------------------------
# "Still working?" idle check
# ---------------------------------------------------------------------------


class TestIdleCheck:
    def test_unanswered_check_stops_at_the_prompt_time(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):  # default settings: check every 60 min
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 5, 4)):  # inside the 5-minute grace
            assert time_tracking_timer_sweep()["limits"] == 0
        with freeze_time(utc(2026, 6, 22, 5, 6)):
            assert time_tracking_timer_sweep()["limits"] == 1
        wl = IssueWorklog.objects.get(logged_by=s2["user"])
        assert wl.ended_at == utc(2026, 6, 22, 5, 0)
        assert wl.duration == 60
        assert wl.idle_stopped is True and wl.auto_stopped is False
        audit_row = TimeTrackingAuditLog.objects.get(target_id=wl.id)
        assert audit_row.action == "auto_stop" and audit_row.after["reason"] == "idle_check"

    def test_confirm_pushes_the_next_check(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 5, 2)):  # prompt showing, answered in time
            r = c.post(ws_url(s2, "time/timer/confirm/"))
        assert r.status_code == 200
        assert r.json()["check"] is None  # the new interval is not due yet
        with freeze_time(utc(2026, 6, 22, 5, 30)):
            assert time_tracking_timer_sweep()["limits"] == 0
        with freeze_time(utc(2026, 6, 22, 6, 8)):
            assert time_tracking_timer_sweep()["limits"] == 1
        wl = IssueWorklog.objects.get(logged_by=s2["user"])
        assert wl.ended_at == utc(2026, 6, 22, 6, 2)
        assert wl.duration == 122

    def test_confirm_after_the_deadline_stops_at_the_prompt_time(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 5, 6)):  # sweep has not run yet
            r = c.post(ws_url(s2, "time/timer/confirm/"))
        assert r.status_code == status.HTTP_409_CONFLICT
        assert r.json()["code"] == "idle_stopped"
        assert r.json()["stopped"]["total_duration"] == 60
        assert not WorklogTimer.objects.exists()
        # Resume is an ordinary start.
        with freeze_time(utc(2026, 6, 22, 5, 7)):
            assert _start(c, s2, s2["issue"]).status_code == 201

    def test_confirm_without_a_timer_is_404(self, s2):
        assert client_for(s2["user"]).post(ws_url(s2, "time/timer/confirm/")).status_code == 404

    def test_late_user_stop_is_clamped_to_the_prompt_time(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 7, 0)):
            r = c.post(ws_url(s2, "time/timer/stop/"), {}, format="json")
        assert r.json()["total_duration"] == 60
        assert IssueWorklog.objects.get(logged_by=s2["user"]).idle_stopped is True

    def test_interval_zero_turns_the_check_off(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 9, 0)):
            assert time_tracking_timer_sweep()["limits"] == 0
            d = c.get(ws_url(s2, "time/timer/")).json()
        assert d["idle_check_enabled"] is False
        assert d["check"] is None
        assert WorklogTimer.objects.filter(user=s2["user"]).exists()

    def test_idle_stop_across_midnight_splits(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 17, 30)):  # 23:30 Dhaka
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 18, 40)):  # 00:40 Dhaka, past the 00:35 deadline
            assert time_tracking_timer_sweep()["limits"] == 1
        wls = list(IssueWorklog.objects.filter(logged_by=s2["user"]).order_by("started_at"))
        assert [w.duration for w in wls] == [30, 30]
        assert [w.logged_date for w in wls] == [date(2026, 6, 22), date(2026, 6, 23)]
        assert wls[-1].ended_at == utc(2026, 6, 22, 18, 30)
        assert all(w.idle_stopped for w in wls)
        assert wls[0].session_id == wls[1].session_id

    def test_turning_the_check_on_is_not_retroactive(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 3, 0)):
            set_settings(s2["workspace"], **idle_every(60))
        with freeze_time(utc(2026, 6, 22, 3, 30)):
            assert time_tracking_timer_sweep()["limits"] == 0
            assert c.get(ws_url(s2, "time/timer/")).json()["check"] is None
        with freeze_time(utc(2026, 6, 22, 4, 1)):
            assert c.get(ws_url(s2, "time/timer/")).json()["check"]["prompted_at"].startswith("2026-06-22T04:00:00")
        with freeze_time(utc(2026, 6, 22, 4, 6)):
            assert time_tracking_timer_sweep()["limits"] == 1
        assert IssueWorklog.objects.get(logged_by=s2["user"]).ended_at == utc(2026, 6, 22, 4, 0)


# ---------------------------------------------------------------------------
# Random check time (PO decision: 20-80 minutes, never announced in advance)
# ---------------------------------------------------------------------------


def _interval(timer_id, started_at, lo, hi):
    timer = WorklogTimer(id=timer_id, started_at=started_at)
    setting = TimeTrackingSetting(idle_check_min_minutes=lo, idle_check_max_minutes=hi)
    s = limits.schedule(timer, setting)
    return s["next_check_at"] - started_at


class TestRandomCheckTime:
    def test_defaults_are_20_to_80(self):
        s = TimeTrackingSetting()
        assert (s.idle_check_min_minutes, s.idle_check_max_minutes) == (20, 80)

    def test_interval_is_always_inside_the_window(self):
        base = utc(2026, 6, 22, 4, 0)
        for i in range(300):
            d = _interval(uuid.uuid4(), base + timedelta(minutes=i), 20, 80)
            assert timedelta(minutes=20) <= d <= timedelta(minutes=80)

    def test_min_equals_max_is_exact(self):
        assert _interval(uuid.uuid4(), utc(2026, 6, 22, 4, 0), 45, 45) == timedelta(minutes=45)

    def test_same_base_gives_the_same_time(self):
        tid, base = uuid.uuid4(), utc(2026, 6, 22, 4, 0)
        assert len({_interval(tid, base, 20, 80) for _ in range(5)}) == 1

    def test_a_new_base_gives_a_new_draw(self):
        tid = uuid.uuid4()
        draws = {_interval(tid, utc(2026, 6, 22, 4, 0) + timedelta(minutes=i), 20, 80) for i in range(60)}
        assert len(draws) > 15  # spread over the window, not a constant
        timers = {_interval(uuid.uuid4(), utc(2026, 6, 22, 4, 0), 20, 80) for _ in range(60)}
        assert len(timers) > 15

    def test_check_is_null_until_due_then_populated(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(30))
            r = _start(c, s2, s2["issue"])
        assert r.json()["check"] is None and r.json()["idle_check_enabled"] is True
        with freeze_time(utc(2026, 6, 22, 4, 29)):
            assert c.get(ws_url(s2, "time/timer/")).json()["check"] is None
        with freeze_time(utc(2026, 6, 22, 4, 31)):
            d = c.get(ws_url(s2, "time/timer/")).json()
        assert d["check"]["prompted_at"].startswith("2026-06-22T04:30:00")
        assert d["check"]["deadline_at"].startswith("2026-06-22T04:35:00")

    def test_confirm_after_the_deadline_is_still_409(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(30))
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 4, 36)):
            r = c.post(ws_url(s2, "time/timer/confirm/"))
        assert r.status_code == status.HTTP_409_CONFLICT and r.json()["code"] == "idle_stopped"

    def test_the_window_is_used_by_the_sweep(self, s2):
        # A random draw within 20-80 minutes: nothing is stopped before 20 min,
        # and the check is always over by 80 + 5 minutes.
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], idle_check_min_minutes=20, idle_check_max_minutes=80)
            _start(c, s2, s2["issue"])
        with freeze_time(utc(2026, 6, 22, 4, 19)):
            assert time_tracking_timer_sweep()["limits"] == 0
        with freeze_time(utc(2026, 6, 22, 5, 26)):
            assert time_tracking_timer_sweep()["limits"] == 1
        wl = IssueWorklog.objects.get(logged_by=s2["user"])
        assert 20 <= wl.duration <= 80 and wl.idle_stopped is True

    def test_already_running_has_a_code(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            assert _start(c, s2, s2["issue"]).status_code == 201
            r = _start(c, s2, s2["issue"])
        assert r.status_code == 400
        assert r.json()["code"] == "already_running" and r.json()["timer"]
