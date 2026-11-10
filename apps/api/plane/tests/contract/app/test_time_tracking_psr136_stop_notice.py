# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — PSR-136: the user is told when the system stops their timer.

Sweep limit stops write one ``timer_stopped`` notification; the running-timer GET carries a
``stop_notice`` while it is unread and no timer runs; the worklog list exposes the
``idle_stopped`` / ``auto_stopped`` flags."""

from unittest.mock import patch

import pytest
from freezegun import freeze_time

from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import Issue, IssueWorklog, Notification, ProjectMember, WorklogTimer
from plane.utils.time_tracking import timer as timer_service

from .time_tracking_s2_support import (  # noqa: F401 (fixtures)
    _load_urlconf,
    client_for,
    IDLE_OFF,
    idle_every,
    set_settings,
    utc,
    ws_url,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]

SENDER = "in_app:time_tracking:stopped"


def _start(c, s, issue=None):
    return c.post(ws_url(s, "time/timer/start/"), {"issue_id": str((issue or s["issue"]).id)}, format="json")


def _notes(s):
    return Notification.objects.filter(receiver=s["user"], sender=SENDER)


def _idle_stop(s, c):
    with freeze_time(utc(2026, 6, 22, 4, 0)):
        set_settings(s["workspace"], **idle_every(60))
        _start(c, s)
    with freeze_time(utc(2026, 6, 22, 5, 6)):
        assert time_tracking_timer_sweep()["limits"] == 1


class TestNotification:
    def test_idle_stop_creates_exactly_one(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 22, 5, 7)):
            assert time_tracking_timer_sweep()["limits"] == 0  # nothing left to stop
        n = _notes(s2).get()
        assert n.entity_name == "issue" and n.entity_identifier == s2["issue"].id
        assert n.title == "Your timer was stopped"
        assert n.read_at is None
        tt = n.data["time_tracking"]
        assert tt["kind"] == "timer_stopped" and tt["reason"] == "idle_check"
        assert tt["issue_id"] == str(s2["issue"].id)
        assert tt["started_at"].startswith("2026-06-22T04:00:00")
        assert tt["stopped_at"].startswith("2026-06-22T05:00:00")
        assert tt["minutes"] == 60
        assert n.data["issue"]["identifier"] == "TTP"
        assert "issue_activity" in n.data

    def test_auto_stop_creates_one(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 0, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2)
        with freeze_time(utc(2026, 6, 22, 14, 7)):
            assert time_tracking_timer_sweep()["limits"] == 1
        tt = _notes(s2).get().data["time_tracking"]
        assert tt["reason"] == "auto_stop" and tt["minutes"] == 840

    def test_day_split_stop_still_one_notification(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 17, 30)):  # 23:30 Dhaka
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2)
        with freeze_time(utc(2026, 6, 22, 18, 40)):
            assert time_tracking_timer_sweep()["limits"] == 1
        assert IssueWorklog.objects.filter(logged_by=s2["user"]).count() == 2
        assert _notes(s2).get().data["time_tracking"]["minutes"] == 60

    def test_issue_deleted_makes_none(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2)
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            Issue.objects.filter(pk=s2["issue"].pk).delete()
            assert time_tracking_timer_sweep()["issue_deleted"] == 1
        assert not _notes(s2).exists()

    def test_member_removed_makes_none(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2)
        ProjectMember.objects.filter(project=s2["project"], member=s2["user"]).update(is_active=False)
        with freeze_time(utc(2026, 6, 22, 4, 30)):
            assert time_tracking_timer_sweep()["member_removed"] == 1
        assert not _notes(s2).exists()

    def test_user_stop_and_late_confirm_make_none(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2)
        with freeze_time(utc(2026, 6, 22, 5, 6)):
            assert c.post(ws_url(s2, "time/timer/confirm/")).status_code == 409
        assert not _notes(s2).exists()

    def test_lost_race_makes_none(self, s2):
        c = client_for(s2["user"])
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_settings(s2["workspace"], **idle_every(60))
            _start(c, s2)
        timer = WorklogTimer.objects.get(user=s2["user"])
        with freeze_time(utc(2026, 6, 22, 5, 6)):
            real = timer_service.lock_user_timers

            def stop_first(user):
                # The user's own stop lands between the sweep's read and its lock.
                WorklogTimer.objects.filter(pk=timer.pk).delete()
                return real(user)

            with patch.object(timer_service, "lock_user_timers", stop_first):
                assert timer_service.system_stop_timer(timer.id, None, "limit") is None
        assert not _notes(s2).exists()


class TestStopNotice:
    def _get(self, c, s):
        return c.get(ws_url(s, "time/timer/")).json()

    def test_present_when_unread_and_no_timer(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            d = self._get(c, s2)
        notice = d["stop_notice"]
        assert notice["notification_id"] == str(_notes(s2).get().id)
        assert notice["reason"] == "idle_check" and notice["minutes"] == 60
        assert notice["issue"]["ref"] == "TTP-1" and notice["issue"]["project_id"] == str(s2["project"].id)
        assert notice["started_at"].startswith("2026-06-22T04:00:00")
        assert notice["stopped_at"].startswith("2026-06-22T05:00:00")
        # The per-issue endpoint is the same view.
        url = f"/api/workspaces/{s2['slug']}/worklog-timer/"
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            assert c.get(url).json()["stop_notice"]["reason"] == "idle_check"

    def test_null_once_read(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            _notes(s2).update(read_at=utc(2026, 6, 22, 5, 30))
            assert self._get(c, s2)["stop_notice"] is None

    def test_null_while_a_timer_runs(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            assert _start(c, s2).status_code == 201
            d = self._get(c, s2)
        assert d["id"] and "stop_notice" not in d

    def test_starting_a_timer_reads_the_notice_so_a_later_stop_does_not_revive_it(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            set_settings(s2["workspace"], **IDLE_OFF)
            _start(c, s2)
            assert c.post(ws_url(s2, "time/timer/stop/"), {}, format="json").status_code == 201
            assert self._get(c, s2)["stop_notice"] is None
        assert _notes(s2).get().read_at is not None

    def test_null_after_24_hours(self, s2):
        c = client_for(s2["user"])
        _idle_stop(s2, c)
        with freeze_time(utc(2026, 6, 23, 5, 0)):
            assert self._get(c, s2)["stop_notice"]
        with freeze_time(utc(2026, 6, 23, 5, 30)):
            assert self._get(c, s2)["stop_notice"] is None

    def test_other_users_do_not_see_it(self, s2):
        from .time_tracking_s2_support import join, make_user

        c = client_for(s2["user"])
        _idle_stop(s2, c)
        other = make_user(s2["workspace"])
        join(s2["project"], other)
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            assert self._get(client_for(other), s2)["stop_notice"] is None

    def test_no_timer_no_notification_is_null(self, s2):
        assert self._get(client_for(s2["user"]), s2)["stop_notice"] is None


def test_worklog_list_exposes_the_stop_flags(s2):
    c = client_for(s2["user"])
    _idle_stop(s2, c)
    url = f"/api/workspaces/{s2['slug']}/projects/{s2['project'].id}/issues/{s2['issue'].id}/worklogs/"
    rows = c.get(url).json()
    rows = rows.get("results", rows) if isinstance(rows, dict) else rows
    assert len(rows) == 1
    assert rows[0]["idle_stopped"] is True and rows[0]["auto_stopped"] is False
