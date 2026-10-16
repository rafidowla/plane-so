# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S1 — workspace-local day bucketing, midnight split,
worklogs surviving work-item deletion, the deleted-issue timer sweep, workspace
settings, member tracking status history and the audit log."""

import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.deletion_task import hard_delete, soft_delete_related_objects
from plane.bgtasks.time_tracking_sweep import time_tracking_timer_sweep
from plane.db.models import (
    Issue,
    IssueWorklog,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    TimeTrackingAuditLog,
    TimeTrackingMemberStatus,
    TimeTrackingSetting,
    User,
    WorklogSource,
    WorklogTimer,
    WorkspaceMember,
)
from plane.utils.time_tracking.tracking import is_tracked, set_member_tracking, tracked_days
from plane.utils.time_tracking.tz import split_by_local_day, zone

DHAKA = "Asia/Dhaka"  # UTC+6, no DST


@pytest.fixture(autouse=True)
def _load_urlconf():
    """Import the URLconf (and its heavy third-party imports) before any
    freeze_time block: freezegun swaps ``datetime.date`` for a fake class, and
    pydantic (pulled in lazily via openai) cannot subclass it at import time."""
    from django.urls import get_resolver

    get_resolver().url_patterns


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


@pytest.fixture
def tt(db, create_user, workspace):
    """A Dhaka workspace with a time-tracked project, the test user as admin, one issue."""
    workspace.timezone = DHAKA
    workspace.save()
    project = Project.objects.create(
        workspace=workspace,
        name="TT Project",
        identifier="TTP",
        created_by=create_user,
        is_time_tracking_enabled=True,
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="TTP")
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20, is_active=True)
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    issue = Issue(workspace=workspace, project=project, name="Issue 1", state=state)
    issue.save(created_by_id=create_user.id)
    return {"project": project, "issue": issue, "state": state, "user": create_user, "workspace": workspace}


def _user(workspace, project=None, ws_role=15, project_role=15):
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(email=f"s1-{uid}@plane.so", username=f"s1_{uid}", first_name="Mem")
    WorkspaceMember.objects.create(workspace=workspace, member=u, role=ws_role, is_active=True)
    if project is not None:
        ProjectMember.objects.create(project=project, workspace=workspace, member=u, role=project_role, is_active=True)
    return u


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _timer_url(tt, issue=None):
    issue = issue or tt["issue"]
    return f"/api/workspaces/{tt['workspace'].slug}/projects/{tt['project'].id}/issues/{issue.id}/worklog-timer/"


def _settings_url(tt):
    return f"/api/workspaces/{tt['workspace'].slug}/time/settings/"


def _tracking_url(tt, user):
    return f"/api/workspaces/{tt['workspace'].slug}/time/members/{user.id}/tracking/"


def _second_issue(tt, name="Issue 2"):
    issue = Issue(workspace=tt["workspace"], project=tt["project"], name=name, state=tt["state"])
    issue.save(created_by_id=tt["user"].id)
    return issue


def _report(client, tt, group_by="issue"):
    r = client.get(f"/api/workspaces/{tt['workspace'].slug}/time-report/?group_by={group_by}")
    assert r.status_code == status.HTTP_200_OK, r.data
    return r.data


# ---------------------------------------------------------------------------
# Day bucketing in the workspace timezone + midnight split
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestLocalDayBucketing:
    def test_dhaka_0030_timer_lands_on_the_local_date(self, session_client, tt):
        # 00:30 on 23 June in Dhaka is 18:30 on 22 June in UTC. The old code
        # filed this under the UTC date (22 June).
        with freeze_time(utc(2026, 6, 22, 18, 30)):
            r = session_client.post(_timer_url(tt), {}, format="json")
            assert r.status_code == status.HTTP_201_CREATED
        with freeze_time(utc(2026, 6, 22, 19, 15)):
            r = session_client.delete(_timer_url(tt))
        assert r.status_code == status.HTTP_201_CREATED
        assert r.data["logged_date"] == "2026-06-23"
        assert r.data["duration"] == 45
        assert r.data["total_duration"] == 45
        assert len(r.data["pieces"]) == 1
        wl = IssueWorklog.objects.get(issue=tt["issue"])
        assert wl.logged_date == date(2026, 6, 23)
        assert wl.source == WorklogSource.TIMER
        assert wl.session_id is not None

    def test_2330_to_0110_splits_into_two_entries_summing_to_the_total(self, session_client, tt):
        # 23:30 Dhaka (22 June) = 17:30 UTC; 01:10 Dhaka (23 June) = 19:10 UTC.
        with freeze_time(utc(2026, 6, 22, 17, 30)):
            assert session_client.post(_timer_url(tt), {"description": "late"}, format="json").status_code == 201
        with freeze_time(utc(2026, 6, 22, 19, 10)):
            r = session_client.delete(_timer_url(tt))
        assert r.status_code == status.HTTP_201_CREATED
        assert r.data["total_duration"] == 100
        assert [p["duration"] for p in r.data["pieces"]] == [30, 70]
        assert [p["logged_date"] for p in r.data["pieces"]] == ["2026-06-22", "2026-06-23"]
        # The top-level fields are the last piece (single-day stops look as before).
        assert r.data["id"] == r.data["pieces"][-1]["id"]

        wls = list(IssueWorklog.objects.filter(issue=tt["issue"]).order_by("started_at"))
        assert len(wls) == 2
        assert sum(w.duration for w in wls) == 100
        assert wls[0].session_id == wls[1].session_id is not None
        # Pieces meet exactly at local midnight (18:00 UTC).
        assert wls[0].ended_at == wls[1].started_at == utc(2026, 6, 22, 18, 0)
        assert wls[0].started_at == utc(2026, 6, 22, 17, 30)
        assert wls[1].ended_at == utc(2026, 6, 22, 19, 10)
        assert all(w.description == "late" for w in wls)
        assert not WorklogTimer.objects.filter(user=tt["user"]).exists()

    def test_switching_issue_across_midnight_splits_the_stopped_timer(self, session_client, tt):
        issue2 = _second_issue(tt)
        with freeze_time(utc(2026, 6, 22, 17, 50)):
            session_client.post(_timer_url(tt), {}, format="json")
        with freeze_time(utc(2026, 6, 22, 18, 20)):
            r = session_client.post(_timer_url(tt, issue2), {}, format="json")
        assert r.status_code == status.HTTP_201_CREATED
        stopped = r.data["stopped"]
        assert stopped["duration"] == 30
        assert [p["duration"] for p in stopped["pieces"]] == [10, 20]
        assert stopped["worklog"]["id"] == stopped["pieces"][-1]["id"]
        assert stopped["issue"]["ref"].startswith("TTP-")

    def test_split_helper_never_loses_minutes(self):
        tz = zone(DHAKA)
        # 23:30 on 20 June to 00:30 on 23 June, local: four local days.
        start, end = utc(2026, 6, 20, 17, 30), utc(2026, 6, 22, 18, 30)
        pieces = split_by_local_day(start, end, tz)
        assert [p["logged_date"] for p in pieces] == [
            date(2026, 6, 20),
            date(2026, 6, 21),
            date(2026, 6, 22),
            date(2026, 6, 23),
        ]
        assert [p["duration"] for p in pieces] == [30, 1440, 1440, 30]
        # Seconds either side of midnight: sub-minute slivers are folded away but
        # the whole-minute total is preserved exactly.
        start, end = utc(2026, 6, 20, 17, 59, 59), utc(2026, 6, 22, 18, 0, 31)
        pieces = split_by_local_day(start, end, tz)
        assert sum(p["duration"] for p in pieces) == int((end - start).total_seconds() // 60)
        assert all(p["duration"] > 0 for p in pieces)
        # A sub-minute run still logs the one-minute minimum.
        short = split_by_local_day(start, start + timedelta(seconds=20), tz)
        assert [p["duration"] for p in short] == [1]


# ---------------------------------------------------------------------------
# Worklogs survive work-item deletion
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestWorklogsOutliveWorkItems:
    def _log(self, session_client, tt, issue, minutes):
        url = f"/api/workspaces/{tt['workspace'].slug}/projects/{tt['project'].id}/issues/{issue.id}/worklogs/"
        r = session_client.post(url, {"duration": minutes, "logged_date": "2026-06-22"}, format="json")
        assert r.status_code == status.HTTP_201_CREATED
        return IssueWorklog.objects.get(pk=r.data["id"])

    def test_snapshot_is_taken_on_create(self, session_client, tt):
        wl = self._log(session_client, tt, tt["issue"], 30)
        tt["issue"].refresh_from_db()
        assert wl.issue_ref == f"TTP-{tt['issue'].sequence_id}"
        assert wl.issue_name == "Issue 1"

    def test_deleted_issue_keeps_its_time_in_the_report_then_falls_back_to_the_snapshot(self, session_client, tt):
        doomed = _second_issue(tt, "Doomed item")
        self._log(session_client, tt, tt["issue"], 60)
        self._log(session_client, tt, doomed, 45)
        doomed.refresh_from_db()
        ref = f"TTP-{doomed.sequence_id}"

        # Soft delete, including the related-object cascade that normally runs in celery.
        doomed.delete()
        soft_delete_related_objects("db", "issue", doomed.pk)
        assert IssueWorklog.objects.filter(issue_id=doomed.id).count() == 1

        data = _report(session_client, tt)
        assert data["totals"]["total_minutes"] == 105
        groups = {g["issue_id"]: g for g in data["groups"]}
        assert groups[str(doomed.id)]["total_minutes"] == 45
        assert groups[str(doomed.id)]["name"] == f"(deleted) {ref} Doomed item"
        assert groups[str(doomed.id)]["is_deleted"] is True
        assert groups[str(tt["issue"].id)]["is_deleted"] is False

        # Hard delete via the real periodic task, once the retention window has passed.
        Issue.all_objects.filter(pk=doomed.pk).update(deleted_at=timezone.now() - timedelta(days=400))
        hard_delete()
        assert not Issue.all_objects.filter(pk=doomed.pk).exists()
        assert IssueWorklog.objects.filter(issue_id=doomed.id).count() == 1

        data = _report(session_client, tt)
        assert data["totals"]["total_minutes"] == 105
        groups = {g["issue_id"]: g for g in data["groups"]}
        assert groups[str(doomed.id)]["total_minutes"] == 45
        assert groups[str(doomed.id)]["name"] == f"(deleted) {ref} Doomed item"
        assert groups[str(doomed.id)]["is_deleted"] is True

        # The resource view (and its CSV task breakdown) still counts the time too.
        assert _report(session_client, tt, "resource")["totals"]["total_minutes"] == 105
        r = session_client.get(f"/api/workspaces/{tt['workspace'].slug}/time-report/?group_by=resource&export=csv")
        assert r.status_code == status.HTTP_200_OK
        assert f"(deleted) {ref} Doomed item" in r.content.decode()

    def test_project_deletion_still_cascades(self, session_client, tt):
        self._log(session_client, tt, tt["issue"], 30)
        Project.all_objects.filter(pk=tt["project"].pk).delete()
        assert not IssueWorklog.all_objects.filter(issue_id=tt["issue"].id).exists()


# ---------------------------------------------------------------------------
# Sweep: timers on deleted work items / untracked members
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestTimerSweep:
    def test_timer_on_soft_deleted_issue_is_converted_at_deletion_time(self, session_client, tt):
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            assert session_client.post(_timer_url(tt), {}, format="json").status_code == 201
        with freeze_time(utc(2026, 6, 22, 5, 30)):
            tt["issue"].delete()
            soft_delete_related_objects("db", "issue", tt["issue"].pk)
        # The timer survives the cascade (it is not soft-deleted with the issue)...
        assert WorklogTimer.objects.filter(user=tt["user"]).exists()
        with freeze_time(utc(2026, 6, 22, 9, 0)):
            result = time_tracking_timer_sweep()
        assert result["issue_deleted"] == 1
        assert not WorklogTimer.objects.filter(user=tt["user"]).exists()
        wl = IssueWorklog.objects.get(issue_id=tt["issue"].id)
        # ...and is stopped at the moment the work item was deleted, not when the sweep ran.
        assert wl.duration == 90
        assert wl.ended_at == utc(2026, 6, 22, 5, 30)
        assert wl.issue_name == "Issue 1"
        audit_row = TimeTrackingAuditLog.objects.get(target_id=wl.id)
        assert audit_row.action == "auto_stop"
        assert audit_row.actor_id is None
        assert audit_row.after["reason"] == "issue_deleted"

    def test_timer_on_missing_issue_is_converted_now(self, tt):
        ghost = uuid.uuid4()
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            WorklogTimer.objects.create(
                project=tt["project"], issue_id=ghost, user=tt["user"], started_at=timezone.now()
            )
        with freeze_time(utc(2026, 6, 22, 4, 20)):
            assert time_tracking_timer_sweep()["issue_deleted"] == 1
        wl = IssueWorklog.objects.get(issue_id=ghost)
        assert wl.duration == 20
        assert wl.issue_ref == "" and wl.issue_name == ""

    def test_live_timers_are_left_alone(self, session_client, tt):
        session_client.post(_timer_url(tt), {}, format="json")
        assert time_tracking_timer_sweep() == {"issue_deleted": 0, "tracking_disabled": 0}
        assert WorklogTimer.objects.filter(user=tt["user"]).exists()

    def test_future_dated_tracking_off_is_enforced_by_the_sweep(self, tt):
        member = _user(tt["workspace"], tt["project"])
        with freeze_time(utc(2026, 6, 22, 15, 0)):  # 21:00 Dhaka on 22 June
            set_member_tracking(tt["workspace"].id, member.id, False, date(2026, 6, 23), tt["user"])
            assert _client(member).post(_timer_url(tt), {}, format="json").status_code == 201
        with freeze_time(utc(2026, 6, 22, 17, 0)):  # still 22 June locally
            assert time_tracking_timer_sweep()["tracking_disabled"] == 0
        with freeze_time(utc(2026, 6, 22, 19, 0)):  # 01:00 on 23 June locally
            assert time_tracking_timer_sweep()["tracking_disabled"] == 1
        wl = IssueWorklog.objects.get(logged_by=member)
        # Stopped at the local start of the day tracking turned off.
        assert wl.ended_at == utc(2026, 6, 22, 18, 0)
        assert wl.duration == 180


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestSettings:
    def test_get_creates_defaults_and_shows_the_workspace_timezone(self, session_client, tt):
        r = session_client.get(_settings_url(tt))
        assert r.status_code == status.HTTP_200_OK
        assert r.data["timezone"] == DHAKA
        assert r.data["work_days"] == [1, 2, 3, 4, 5]
        assert r.data["minutes_per_day"] == 480
        assert r.data["idle_check_minutes"] == 60
        assert TimeTrackingSetting.objects.filter(workspace=tt["workspace"]).count() == 1

    def test_patch_is_workspace_admin_only(self, session_client, tt):
        member = _user(tt["workspace"])
        guest = _user(tt["workspace"], ws_role=5)
        assert _client(member).patch(_settings_url(tt), {"minutes_per_day": 420}, format="json").status_code == 403
        assert _client(guest).patch(_settings_url(tt), {"minutes_per_day": 420}, format="json").status_code == 403
        # Members can read (minus admin-only fields); guests cannot.
        r = _client(member).get(_settings_url(tt))
        assert r.status_code == 200 and "billing_enabled" not in r.data
        assert _client(guest).get(_settings_url(tt)).status_code == 403

        r = session_client.patch(
            _settings_url(tt), {"minutes_per_day": 420, "idle_check_minutes": 0, "work_days": [6, 1]}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        assert r.data["minutes_per_day"] == 420
        assert r.data["idle_check_minutes"] == 0
        assert r.data["work_days"] == [1, 6]

    def test_patch_validation(self, session_client, tt):
        for body in (
            {"auto_stop_after_minutes": 500, "remind_after_minutes": 600},
            {"work_days": []},
            {"work_days": [0, 8]},
            {"minutes_per_day": 0},
            {"idle_check_minutes": 2000},
        ):
            assert session_client.patch(_settings_url(tt), body, format="json").status_code == 400, body

    def test_patch_is_audited(self, session_client, tt):
        session_client.patch(_settings_url(tt), {"remind_after_minutes": 300}, format="json")
        row = TimeTrackingAuditLog.objects.get(target_type="setting")
        assert row.action == "update"
        assert row.actor_id == tt["user"].id
        assert row.before["remind_after_minutes"] == 600
        assert row.after["remind_after_minutes"] == 300


# ---------------------------------------------------------------------------
# Member tracking status
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestMemberTracking:
    def test_tracking_off_stops_the_running_timer_and_blocks_start(self, session_client, tt):
        member = _user(tt["workspace"], tt["project"])
        mc = _client(member)
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            assert mc.post(_timer_url(tt), {}, format="json").status_code == 201
        with freeze_time(utc(2026, 6, 22, 5, 0)):
            r = session_client.post(_tracking_url(tt, member), {"is_tracked": False}, format="json")
            assert r.status_code == status.HTTP_201_CREATED, r.data
            assert r.data["is_tracked_today"] is False
            assert len(r.data["stopped"]) == 1
            assert r.data["stopped"][0]["duration"] == 60
            assert not WorklogTimer.objects.filter(user=member).exists()

            r = mc.post(_timer_url(tt), {}, format="json")
            assert r.status_code == status.HTTP_403_FORBIDDEN
            assert r.data["code"] == "tracking_disabled"
            assert not WorklogTimer.objects.filter(user=member).exists()

        stop_audit = TimeTrackingAuditLog.objects.get(action="auto_stop")
        assert stop_audit.actor_id == tt["user"].id
        assert stop_audit.after["reason"] == "tracking_disabled"

        # Back on: the member can start again.
        with freeze_time(utc(2026, 6, 22, 6, 0)):
            session_client.post(_tracking_url(tt, member), {"is_tracked": True}, format="json")
            assert mc.post(_timer_url(tt), {}, format="json").status_code == 201

    def test_status_endpoints_are_admin_only_and_validate(self, session_client, tt):
        member = _user(tt["workspace"], tt["project"])
        guest = _user(tt["workspace"], ws_role=5)
        assert _client(member).post(_tracking_url(tt, member), {"is_tracked": False}, format="json").status_code == 403
        assert _client(member).get(f"/api/workspaces/{tt['workspace'].slug}/time/members/").status_code == 403
        # Guests are never tracked, so there is nothing to switch.
        assert session_client.post(_tracking_url(tt, guest), {"is_tracked": False}, format="json").status_code == 400
        assert session_client.post(_tracking_url(tt, member), {"is_tracked": "no"}, format="json").status_code == 400
        assert (
            session_client.post(
                _tracking_url(tt, member), {"is_tracked": False, "effective_from": "soon"}, format="json"
            ).status_code
            == 400
        )

    def test_history_drives_tracked_on_date(self, session_client, tt):
        member = _user(tt["workspace"], tt["project"])
        guest = _user(tt["workspace"], ws_role=5)
        ws = tt["workspace"].id
        with freeze_time(utc(2026, 6, 1, 6, 0)):
            session_client.post(
                _tracking_url(tt, member), {"is_tracked": False, "effective_from": "2026-06-10"}, format="json"
            )
            session_client.post(
                _tracking_url(tt, member), {"is_tracked": True, "effective_from": "2026-06-20"}, format="json"
            )
        assert TimeTrackingMemberStatus.objects.filter(user=member).count() == 2
        assert is_tracked(ws, member.id, date(2026, 6, 9)) is True
        assert is_tracked(ws, member.id, date(2026, 6, 10)) is False
        assert is_tracked(ws, member.id, date(2026, 6, 19)) is False
        assert is_tracked(ws, member.id, date(2026, 6, 20)) is True
        assert is_tracked(ws, guest.id, date(2026, 6, 20)) is False
        days = tracked_days(ws, member.id, date(2026, 6, 8), date(2026, 6, 21))
        assert days == {date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 20), date(2026, 6, 21)}

        # The members list shows today's status and the full history.
        with freeze_time(utc(2026, 6, 15, 6, 0)):
            r = session_client.get(f"/api/workspaces/{tt['workspace'].slug}/time/members/")
        assert r.status_code == 200
        rows = {m["user_id"]: m for m in r.data["members"]}
        assert rows[str(member.id)]["is_tracked"] is False
        assert len(rows[str(member.id)]["history"]) == 2
        assert str(guest.id) not in rows
        assert rows[str(tt["user"].id)]["is_tracked"] is True

    def test_status_change_is_audited(self, session_client, tt):
        member = _user(tt["workspace"], tt["project"])
        session_client.post(_tracking_url(tt, member), {"is_tracked": False}, format="json")
        row = TimeTrackingAuditLog.objects.get(target_type="member_status")
        assert row.action == "create"
        assert row.actor_id == tt["user"].id
        assert row.after["is_tracked"] is False
        assert row.before["is_tracked_today"] is True

    def test_history_rows_are_append_only(self, session_client, tt):
        member = _user(tt["workspace"], tt["project"])
        session_client.post(_tracking_url(tt, member), {"is_tracked": False}, format="json")
        row = TimeTrackingMemberStatus.objects.get(user=member)
        row.is_tracked = True
        with pytest.raises(ValueError):
            row.save()
        with pytest.raises(ValueError):
            row.delete()


# ---------------------------------------------------------------------------
# Audit of manual worklog changes
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestWorklogAudit:
    def test_create_update_delete_are_audited(self, session_client, tt):
        base = f"/api/workspaces/{tt['workspace'].slug}/projects/{tt['project'].id}/issues/{tt['issue'].id}/worklogs/"
        r = session_client.post(base, {"duration": 30, "logged_date": "2026-06-22"}, format="json")
        wid = r.data["id"]
        assert session_client.patch(f"{base}{wid}/", {"duration": 45}, format="json").status_code == 200
        assert session_client.delete(f"{base}{wid}/").status_code == 204

        rows = list(TimeTrackingAuditLog.objects.filter(target_id=wid).order_by("created_at"))
        assert [r.action for r in rows] == ["create", "update", "delete"]
        assert all(r.actor_id == tt["user"].id for r in rows)
        assert rows[0].after["duration"] == 30
        assert rows[1].before["duration"] == 30 and rows[1].after["duration"] == 45
        assert rows[2].before["duration"] == 45 and rows[2].after is None

    def test_own_timer_stop_is_not_audited(self, session_client, tt):
        session_client.post(_timer_url(tt), {}, format="json")
        session_client.delete(_timer_url(tt))
        assert not TimeTrackingAuditLog.objects.exists()

    def test_server_fields_are_read_only(self, session_client, tt):
        base = f"/api/workspaces/{tt['workspace'].slug}/projects/{tt['project'].id}/issues/{tt['issue'].id}/worklogs/"
        r = session_client.post(
            base,
            {
                "duration": 30,
                "logged_date": "2026-06-22",
                "issue_ref": "FAKE-1",
                "issue_name": "spoof",
                "auto_stopped": True,
                "idle_stopped": True,
                "session_id": str(uuid.uuid4()),
            },
            format="json",
        )
        wl = IssueWorklog.objects.get(pk=r.data["id"])
        assert wl.issue_ref.startswith("TTP-") and wl.issue_name == "Issue 1"
        assert wl.auto_stopped is False and wl.idle_stopped is False and wl.session_id is None
