# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — the one-off ``recover_cascaded_worklogs`` command.

Pre-v2, deleting a work item soft-deleted its worklogs a moment later (celery
cascade). The tests recreate that state directly (post-v2 the cascade skips
worklogs) and check what the command counts, restores and leaves alone.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    Issue,
    IssueWorklog,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    TimeTrackingAuditLog,
    User,
    WorklogTimer,
    Workspace,
    WorkspaceMember,
)

DELETED_AT = datetime(2026, 5, 4, 10, 0, 0, tzinfo=dt_timezone.utc)


def _project(workspace, user, identifier):
    project = Project.objects.create(
        workspace=workspace,
        name=f"P {identifier}",
        identifier=identifier,
        created_by=user,
        is_time_tracking_enabled=True,
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name=identifier)
    ProjectMember.objects.create(project=project, workspace=workspace, member=user, role=20, is_active=True)
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    return project, state


def _issue(project, state, user, name="Item"):
    issue = Issue(workspace=project.workspace, project=project, name=name, state=state)
    issue.save(created_by_id=user.id)
    return issue


def _worklog(issue, user, minutes, day=date(2026, 5, 1)):
    return IssueWorklog.objects.create(
        project=issue.project, issue=issue, logged_by=user, duration=minutes, logged_date=day
    )


def _cascade(issue, *worklogs, after=2, issue_deleted_at=DELETED_AT):
    """The pre-v2 state: issue soft-deleted, its worklogs soft-deleted ``after`` seconds later."""
    Issue.all_objects.filter(pk=issue.pk).update(deleted_at=issue_deleted_at)
    for wl in worklogs:
        IssueWorklog.all_objects.filter(pk=wl.pk).update(deleted_at=issue_deleted_at + timedelta(seconds=after))


def _run(*args):
    out = StringIO()
    call_command("recover_cascaded_worklogs", *args, stdout=out)
    return out.getvalue()


def _alive(wl):
    return IssueWorklog.objects.filter(pk=wl.pk).exists()


@pytest.fixture
def tt(db, create_user, workspace):
    project, state = _project(workspace, create_user, "REC")
    return {"workspace": workspace, "project": project, "state": state, "user": create_user}


@pytest.mark.contract
@pytest.mark.django_db
class TestRecoverCascadedWorklogs:
    def _scenario(self, tt):
        """Two cascade-deleted entries on one deleted item, plus one live entry."""
        gone = _issue(tt["project"], tt["state"], tt["user"], "Deleted item")
        live = _issue(tt["project"], tt["state"], tt["user"], "Live item")
        a = _worklog(gone, tt["user"], 60, date(2026, 5, 1))
        b = _worklog(gone, tt["user"], 30, date(2026, 5, 2))
        kept = _worklog(live, tt["user"], 15, date(2026, 5, 3))
        _cascade(gone, a, b)
        return gone, a, b, kept

    def test_dry_run_counts_and_changes_nothing(self, tt):
        gone, a, b, kept = self._scenario(tt)
        audit_before = TimeTrackingAuditLog.objects.count()

        out = _run()

        assert "test-workspace" in out and "REC" in out
        assert "2 entries, 1.50 h" in out
        assert "2026-05-01 .. 2026-05-02" in out
        assert "DRY RUN" in out
        assert not _alive(a) and not _alive(b) and _alive(kept)
        assert IssueWorklog.all_objects.get(pk=a.pk).deleted_at is not None
        assert TimeTrackingAuditLog.objects.count() == audit_before

    def test_apply_restores_exactly_the_cascaded_entries_and_reports_include_them(self, session_client, tt):
        gone, a, b, kept = self._scenario(tt)
        # Blank snapshot, as on a row that predates the v2 snapshot backfill.
        IssueWorklog.all_objects.filter(pk=a.pk).update(issue_ref="", issue_name="")
        ws = tt["workspace"].slug
        report = f"/api/workspaces/{ws}/time-report/?group_by=issue"
        assert session_client.get(report).data["totals"]["total_minutes"] == 15

        out = _run("--apply")

        assert "Restored 2 time entries" in out
        assert _alive(a) and _alive(b) and _alive(kept)
        a.refresh_from_db()
        assert a.deleted_at is None
        assert a.issue_ref == f"REC-{gone.sequence_id}" and a.issue_name == "Deleted item"
        # Entry data is untouched.
        assert (a.duration, a.logged_date, a.logged_by_id) == (60, date(2026, 5, 1), tt["user"].id)

        r = session_client.get(report)
        assert r.status_code == status.HTTP_200_OK
        assert r.data["totals"]["total_minutes"] == 105
        groups = {g["issue_id"]: g for g in r.data["groups"]}
        assert groups[str(gone.id)]["total_minutes"] == 90
        assert groups[str(gone.id)]["is_deleted"] is True

        rows = TimeTrackingAuditLog.objects.filter(target_id__in=[a.id, b.id])
        assert rows.count() == 2
        row = rows.get(target_id=a.id)
        assert row.actor_id is None and row.action == "update" and row.target_type == "worklog"
        assert row.after["deleted_at"] is None and row.before["deleted_at"] is not None
        assert row.after["reason"] == "recover_cascaded_worklogs"

    def test_apply_is_idempotent(self, tt):
        gone, a, b, kept = self._scenario(tt)
        _run("--apply")
        audit_count = TimeTrackingAuditLog.objects.count()

        out = _run("--apply")

        assert "Total recoverable: 0 entries" in out
        assert "Restored 0 time entries" in out
        assert TimeTrackingAuditLog.objects.count() == audit_count
        assert IssueWorklog.objects.filter(issue=gone).count() == 2

    def test_user_deleted_entries_are_left_alone(self, tt):
        user, project, state = tt["user"], tt["project"], tt["state"]
        # 1. work item still alive: the user deleted the entry.
        live = _issue(project, state, user, "Alive")
        alive_wl = _worklog(live, user, 10)
        IssueWorklog.all_objects.filter(pk=alive_wl.pk).update(deleted_at=DELETED_AT)
        # 2. entry deleted days before the work item was.
        later = _issue(project, state, user, "Deleted later")
        early_wl = _worklog(later, user, 20)
        _cascade(later)
        IssueWorklog.all_objects.filter(pk=early_wl.pk).update(deleted_at=DELETED_AT - timedelta(days=3))
        # 3. entry deleted long after the work item (outside the cascade window).
        slow = _issue(project, state, user, "Deleted, entry later")
        slow_wl = _worklog(slow, user, 30)
        _cascade(slow, slow_wl, after=3600)
        # 4. entry deleted a minute BEFORE its work item: the cascade never runs backwards.
        before = _issue(project, state, user, "Entry first")
        before_wl = _worklog(before, user, 40)
        _cascade(before, before_wl, after=-60)

        out = _run("--apply")

        assert "Restored 0 time entries" in out
        for wl in (alive_wl, early_wl, slow_wl, before_wl):
            assert not _alive(wl)

    def test_tolerance_option_widens_the_window(self, tt):
        slow = _issue(tt["project"], tt["state"], tt["user"], "Slow cascade")
        wl = _worklog(slow, tt["user"], 30)
        _cascade(slow, wl, after=3600)

        assert "Total recoverable: 0 entries" in _run()
        assert "1 entries, 0.50 h" in _run("--tolerance-seconds", "7200")
        assert not _alive(wl)  # dry run
        _run("--apply", "--tolerance-seconds", "7200")
        assert _alive(wl)

    def test_entries_of_deleted_projects_are_not_restored(self, create_user, workspace, tt):
        project, state = _project(workspace, create_user, "GON")
        issue = _issue(project, state, create_user, "In dead project")
        wl = _worklog(issue, create_user, 50)
        _cascade(issue, wl)
        Project.all_objects.filter(pk=project.pk).update(deleted_at=DELETED_AT)

        out = _run("--apply")

        assert "Restored 0 time entries" in out
        assert "1 entries deleted together with a deleted project" in out
        assert not _alive(wl)

    def test_missing_work_item_row_is_reported_not_restored(self, tt):
        issue = _issue(tt["project"], tt["state"], tt["user"], "Hard deleted")
        wl = _worklog(issue, tt["user"], 25)
        _cascade(issue, wl)
        Issue.all_objects.filter(pk=issue.pk).delete()  # row gone, as after the 60-day hard delete

        out = _run("--apply")

        assert "Restored 0 time entries" in out
        assert "1 soft-deleted entries whose work item row is already gone" in out
        assert not _alive(wl)

    def test_lost_running_timers_are_counted_but_not_restored(self, tt):
        issue = _issue(tt["project"], tt["state"], tt["user"], "Timer item")
        timer = WorklogTimer.objects.create(
            project=tt["project"], issue=issue, user=tt["user"], started_at=timezone.now() - timedelta(hours=2)
        )
        _cascade(issue)
        WorklogTimer.all_objects.filter(pk=timer.pk).update(deleted_at=DELETED_AT + timedelta(seconds=1))

        out = _run("--apply")

        assert "1 running timers lost with their work item" in out
        assert not WorklogTimer.objects.filter(pk=timer.pk).exists()
        assert not IssueWorklog.all_objects.filter(issue=issue).exists()

    def test_workspace_filter(self, tt):
        other_user = User.objects.create(email="owner2@plane.so", username="owner2")
        other_ws = Workspace.objects.create(name="Other", owner=other_user, slug="other-ws")
        WorkspaceMember.objects.create(workspace=other_ws, member=other_user, role=20)
        project2, state2 = _project(other_ws, other_user, "OTH")
        issue2 = _issue(project2, state2, other_user, "Other deleted")
        wl2 = _worklog(issue2, other_user, 45)
        _cascade(issue2, wl2)
        gone, a, b, kept = self._scenario(tt)

        out = _run("--workspace", "other-ws")
        assert "other-ws" in out and "test-workspace" not in out
        assert "Total recoverable: 1 entries" in out

        _run("--apply", "--workspace", "other-ws")
        assert _alive(wl2)
        assert not _alive(a) and not _alive(b)  # the other workspace was not touched

        _run("--apply", "--workspace", "test-workspace")
        assert _alive(a) and _alive(b)

    def test_unknown_workspace_is_an_error(self, tt):
        with pytest.raises(CommandError):
            _run("--workspace", "nope")
        with pytest.raises(CommandError):
            _run("--tolerance-seconds", "-1")
