# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the time-tracking, clients, timesheets, reporting and
self-service Jira import endpoints added by this fork."""

import uuid

import pytest
from rest_framework import status

from plane.db.models import (
    Client,
    Issue,
    IssueWorklog,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    Timesheet,
    User,
    WorklogTimer,
)


@pytest.fixture
def tt(db, create_user, workspace):
    """A project (time tracking enabled) with the test user as admin + one issue."""
    project = Project.objects.create(
        workspace=workspace,
        name="TT Project",
        identifier="TTP",
        created_by=create_user,
        is_time_tracking_enabled=True,
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="TTP")
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    state = State.objects.create(
        workspace=workspace, project=project, name="Todo", group="unstarted", default=True
    )
    issue = Issue(workspace=workspace, project=project, name="Issue 1", state=state)
    issue.save(created_by_id=create_user.id)
    return {"project": project, "issue": issue, "state": state, "user": create_user, "workspace": workspace}


def _member(workspace, project, role=15):
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(email=f"m-{uid}@plane.so", username=f"u_{uid}", first_name="Mem")
    u.set_password("x")
    u.save()
    ProjectMember.objects.create(project=project, workspace=workspace, member=u, role=role, is_active=True)
    return u


def _wl_url(slug, pid, iid, pk=None):
    base = f"/api/workspaces/{slug}/projects/{pid}/issues/{iid}/worklogs/"
    return f"{base}{pk}/" if pk else base


def _ids(tt):
    return tt["workspace"].slug, str(tt["project"].id), str(tt["issue"].id)


@pytest.mark.contract
@pytest.mark.django_db
class TestWorklog:
    def test_create_own_worklog(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 90, "logged_date": "2026-06-22", "description": "investigation"},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        assert IssueWorklog.objects.filter(issue=tt["issue"], logged_by=tt["user"]).count() == 1

    def test_disabled_project_blocks_logging(self, session_client, tt):
        Project.objects.filter(id=tt["project"].id).update(is_time_tracking_enabled=False)
        slug, pid, iid = _ids(tt)
        r = session_client.post(_wl_url(slug, pid, iid), {"duration": 30, "logged_date": "2026-06-22"}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_admin_logs_on_behalf(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        member = _member(tt["workspace"], tt["project"])
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 60, "logged_date": "2026-06-22", "logged_by": str(member.id)},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        wl = IssueWorklog.objects.get(id=r.data["id"])
        assert wl.logged_by_id == member.id  # the resource
        assert wl.created_by_id == tt["user"].id  # who entered it (audit)

    def test_member_cannot_log_on_behalf(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        member = _member(tt["workspace"], tt["project"])
        other = _member(tt["workspace"], tt["project"])
        session_client.force_authenticate(user=member)
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 60, "logged_date": "2026-06-22", "logged_by": str(other.id)},
            format="json",
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_timer_start_conflict_and_stop(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        timer_url = f"/api/workspaces/{slug}/projects/{pid}/issues/{iid}/worklog-timer/"
        assert session_client.post(timer_url, {}, format="json").status_code == status.HTTP_201_CREATED
        # second start while one is running -> blocked
        assert session_client.post(timer_url, {}, format="json").status_code == status.HTTP_400_BAD_REQUEST
        # stop -> creates a worklog, clears the timer
        stop = session_client.delete(timer_url)
        assert stop.status_code == status.HTTP_201_CREATED
        assert WorklogTimer.objects.filter(user=tt["user"]).count() == 0
        assert IssueWorklog.objects.filter(issue=tt["issue"], source="timer").count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestTimesheetApproval:
    def test_submit_then_approve_locks(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        session_client.post(
            _wl_url(slug, pid, iid), {"duration": 120, "logged_date": "2026-06-22"}, format="json"
        )
        submit = session_client.post(
            f"/api/workspaces/{slug}/timesheets/submit/",
            {"period_start": "2026-06-22", "period_end": "2026-06-28"},
            format="json",
        )
        assert submit.status_code == status.HTTP_200_OK
        assert submit.data["status"] == "submitted"
        ts_id = submit.data["id"]
        review = session_client.post(
            f"/api/workspaces/{slug}/timesheets/{ts_id}/review/", {"action": "approve"}, format="json"
        )
        assert review.status_code == status.HTTP_200_OK
        assert review.data["status"] == "approved"
        assert IssueWorklog.objects.filter(timesheet_id=ts_id, is_locked=True).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestReport:
    def test_report_group_by_resource(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.post(_wl_url(slug, pid, iid), {"duration": 30, "logged_date": "2026-06-23"}, format="json")
        r = session_client.get(
            f"/api/workspaces/{slug}/time-report/?group_by=resource&start_date=2026-06-01&end_date=2026-06-30"
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["totals"]["total_minutes"] == 120
        assert r.data["group_by"] == "resource"


@pytest.mark.contract
@pytest.mark.django_db
class TestClient:
    def test_create_and_list_with_project_ids(self, session_client, tt):
        slug = tt["workspace"].slug
        c = session_client.post(
            f"/api/workspaces/{slug}/clients/",
            {"name": "Acme Corp", "default_billable_rate": "150.00"},
            format="json",
        )
        assert c.status_code == status.HTTP_201_CREATED
        client_id = c.data["id"]
        Project.objects.filter(id=tt["project"].id).update(client_id=client_id)
        lst = session_client.get(f"/api/workspaces/{slug}/clients/")
        assert lst.status_code == status.HTTP_200_OK
        row = next(x for x in lst.data if x["id"] == client_id)
        assert row["project_count"] == 1
        assert str(tt["project"].id) in [str(p) for p in row["project_ids"]]


@pytest.mark.contract
@pytest.mark.django_db
class TestJiraImport:
    def test_preview_is_dry_run(self, session_client, tt):
        slug, pid, _ = _ids(tt)
        r = session_client.post(
            f"/api/workspaces/{slug}/projects/{pid}/jira-import/preview/",
            {"sample": True, "with_worklogs": True},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["dry_run"] is True
        assert r.data["created"] == 2
        # nothing written on a preview
        assert Issue.objects.filter(project=tt["project"], external_source="jira").count() == 0

    def test_start_sample_import_writes(self, session_client, tt):
        slug, pid, _ = _ids(tt)
        r = session_client.post(
            f"/api/workspaces/{slug}/projects/{pid}/jira-import/",
            {"sample": True, "with_worklogs": True},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        assert r.data["status"] == "completed"
        assert r.data["result"]["created"] == 2
        assert Issue.objects.filter(project=tt["project"], external_source="jira").count() == 2
        # idempotent re-run skips both
        r2 = session_client.post(
            f"/api/workspaces/{slug}/projects/{pid}/jira-import/",
            {"sample": True, "with_worklogs": True},
            format="json",
        )
        assert r2.data["result"]["created"] == 0
        assert r2.data["result"]["skipped"] == 2
