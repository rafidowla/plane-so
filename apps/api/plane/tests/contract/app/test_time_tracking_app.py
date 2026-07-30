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
    IssueAssignee,
    IssueComment,
    IssueWorklog,
    Project,
    ProjectIdentifier,
    ProjectMember,
    ResourceCapacity,
    State,
    Timesheet,
    User,
    WorklogTimer,
    WorkspaceMember,
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


def _ws_member(workspace, role=15):
    """Create a user with a workspace-level membership at the given role.

    The report and capacity endpoints authorize at WORKSPACE level (against
    WorkspaceMember), so project-only membership is not enough to reach them.
    role: 20=admin, 15=member, 5=guest.
    """
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(email=f"ws-{uid}@plane.so", username=f"ws_{uid}", first_name="WsMem")
    u.set_password("x")
    u.save()
    WorkspaceMember.objects.create(workspace=workspace, member=u, role=role, is_active=True)
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

    def test_member_cannot_log_manually(self, session_client, tt):
        # Manual entry is a PM/admin function: a plain member is refused even for
        # their own time and must use the start/stop timer instead.
        slug, pid, iid = _ids(tt)
        member = _member(tt["workspace"], tt["project"])
        session_client.force_authenticate(user=member)
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 45, "logged_date": "2026-06-22"},
            format="json",
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert IssueWorklog.objects.filter(issue=tt["issue"], logged_by=member).count() == 0

    def test_manual_entry_is_forced_source_manual(self, session_client, tt):
        # The manual endpoint always records source=manual ("PM reported"), even
        # if the client tries to relabel the entry as self-tracked.
        slug, pid, iid = _ids(tt)
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 30, "logged_date": "2026-06-22", "source": "timer"},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED
        assert IssueWorklog.objects.get(id=r.data["id"]).source == "manual"

    def test_guest_sees_totals_only(self, session_client, tt):
        # A guest (client) listing worklogs gets the reported total (duration)
        # but none of the identity/source fields that reveal who logged it or how.
        slug, pid, iid = _ids(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 60, "logged_date": "2026-06-22"}, format="json")
        guest = _member(tt["workspace"], tt["project"], role=5)
        session_client.force_authenticate(user=guest)
        r = session_client.get(_wl_url(slug, pid, iid))
        assert r.status_code == status.HTTP_200_OK
        assert len(r.data) == 1
        entry = r.data[0]
        assert entry["duration"] == 60
        for hidden in ("logged_by", "logged_by_detail", "created_by", "created_by_detail", "source", "description"):
            assert hidden not in entry

    def test_member_sees_full_detail(self, session_client, tt):
        # An internal member still gets the full record (source, who logged it).
        slug, pid, iid = _ids(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 60, "logged_date": "2026-06-22"}, format="json")
        member = _member(tt["workspace"], tt["project"], role=15)
        session_client.force_authenticate(user=member)
        r = session_client.get(_wl_url(slug, pid, iid))
        assert r.status_code == status.HTTP_200_OK
        assert "source" in r.data[0] and "logged_by" in r.data[0]

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
    def _seed(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.post(_wl_url(slug, pid, iid), {"duration": 30, "logged_date": "2026-06-23"}, format="json")

    def _second_issue(self, tt, name="Issue 2"):
        issue = Issue(workspace=tt["workspace"], project=tt["project"], name=name, state=tt["state"])
        issue.save(created_by_id=tt["user"].id)
        return issue

    def test_report_group_by_resource(self, session_client, tt):
        # ADMIN (create_user) gets the full report — regression guard against
        # over-tightening the legitimate admin/PM use case.
        slug, pid, iid = _ids(tt)
        self._seed(session_client, tt)
        r = session_client.get(
            f"/api/workspaces/{slug}/time-report/?group_by=resource&start_date=2026-06-01&end_date=2026-06-30"
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["totals"]["total_minutes"] == 120
        assert r.data["group_by"] == "resource"
        # Billing fields are present for an admin.
        assert "billable_amount" in r.data["totals"]

    def test_report_member_allowed(self, session_client, tt):
        # A plain workspace MEMBER may still see the billing report: billable
        # amounts are revenue-side data members can already see via worklogs.
        slug = tt["workspace"].slug
        self._seed(session_client, tt)
        member = _ws_member(tt["workspace"], role=15)
        session_client.force_authenticate(user=member)
        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource")
        assert r.status_code == status.HTTP_200_OK
        assert "totals" in r.data

    def test_report_guest_forbidden(self, session_client, tt):
        # A GUEST (client) must NOT reach the billing report at all — no names,
        # emails, or dollar amounts leak, even in the error path.
        slug = tt["workspace"].slug
        self._seed(session_client, tt)
        guest = _ws_member(tt["workspace"], role=5)
        session_client.force_authenticate(user=guest)
        for group_by in ("resource", "project", "client", "issue"):
            r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by={group_by}")
            assert r.status_code == status.HTTP_403_FORBIDDEN
            body = str(r.data)
            for leaked in ("billable_amount", "billable_minutes", "email", "display_name", "totals", "groups"):
                assert leaked not in body

    def test_report_group_by_issue_splits_per_task(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        issue2 = self._second_issue(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.post(
            _wl_url(slug, pid, str(issue2.id)), {"duration": 45, "logged_date": "2026-06-23"}, format="json"
        )

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue")
        assert r.status_code == status.HTTP_200_OK
        assert r.data["group_by"] == "issue"
        groups = {g["issue_id"]: g for g in r.data["groups"]}
        assert len(groups) == 2

        tt["issue"].refresh_from_db()
        issue2.refresh_from_db()
        g1, g2 = groups[str(tt["issue"].id)], groups[str(issue2.id)]
        assert g1["total_minutes"] == 90
        assert g1["name"] == f"TTP-{tt['issue'].sequence_id} Issue 1"
        assert g1["project_identifier"] == "TTP"
        assert g2["total_minutes"] == 45
        assert g2["name"] == f"TTP-{issue2.sequence_id} Issue 2"

        assert r.data["totals"]["total_minutes"] == 135
        assert r.data["truncated"] is False
        assert r.data["limit"] == 200

    def test_report_user_ids_filter(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        other = _member(tt["workspace"], tt["project"], role=15)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.force_authenticate(user=other)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 60, "logged_date": "2026-06-22"}, format="json")
        session_client.force_authenticate(user=tt["user"])

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&user_ids={tt['user'].id}")
        assert r.status_code == status.HTTP_200_OK
        assert r.data["totals"]["total_minutes"] == 90
        assert len(r.data["groups"]) == 1
        assert r.data["groups"][0]["key"] == str(tt["user"].id)

    def test_report_group_by_issue_respects_user_filter(self, session_client, tt):
        slug, pid, iid = _ids(tt)
        issue2 = self._second_issue(tt)
        other = _member(tt["workspace"], tt["project"], role=15)

        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.force_authenticate(user=other)
        session_client.post(
            _wl_url(slug, pid, str(issue2.id)), {"duration": 45, "logged_date": "2026-06-23"}, format="json"
        )
        session_client.force_authenticate(user=tt["user"])

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue&user_ids={tt['user'].id}")
        assert r.status_code == status.HTTP_200_OK
        assert len(r.data["groups"]) == 1
        assert r.data["groups"][0]["issue_id"] == str(tt["issue"].id)
        assert r.data["totals"]["total_minutes"] == 90

    def test_report_invalid_group_by(self, session_client, tt):
        slug = tt["workspace"].slug
        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=bogus")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        for key in ("resource", "project", "client", "issue"):
            assert key in r.data["error"]

    def test_report_csv_export(self, session_client, tt):
        # Regression guard: "format" is DRF's reserved content-negotiation query
        # param — "?format=csv" 404s before this view's own get() ever runs, since
        # only JSONRenderer is registered. The CSV branch must be keyed off a
        # differently-named param ("export").
        slug, pid, iid = _ids(tt)
        self._seed(session_client, tt)
        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&export=csv")
        assert r.status_code == status.HTTP_200_OK
        assert r["Content-Type"] == "text/csv"
        body = r.content.decode()
        assert "Resource" in body
        assert tt["user"].display_name in body

    def test_report_issue_grouping_scoped_to_visible_projects(self, session_client, tt):
        # A workspace MEMBER who is not a member of the test project must not
        # see its issue titles via group_by=issue, but the workspace-level
        # resource grouping (which they already can see) must be unaffected.
        slug = tt["workspace"].slug
        self._seed(session_client, tt)
        outsider = _ws_member(tt["workspace"], role=15)
        session_client.force_authenticate(user=outsider)

        r_issue = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue")
        assert r_issue.status_code == status.HTTP_200_OK
        assert r_issue.data["groups"] == []

        r_resource = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource")
        assert r_resource.status_code == status.HTTP_200_OK
        assert r_resource.data["totals"]["total_minutes"] == 120


@pytest.mark.contract
@pytest.mark.django_db
class TestResourceCapacity:
    """Resource capacity carries internal cost_rate/billable_rate and is
    ADMIN-only. No non-admin UI consumes this endpoint."""

    URL = "/api/workspaces/{slug}/resource-capacities/"

    def _seed_capacity(self, tt):
        ResourceCapacity.objects.create(
            workspace=tt["workspace"],
            user=tt["user"],
            weekly_capacity=2400,
            cost_rate="50.00",
            billable_rate="150.00",
            currency="USD",
        )

    def test_admin_sees_capacity_with_rates(self, session_client, tt):
        self._seed_capacity(tt)
        r = session_client.get(self.URL.format(slug=tt["workspace"].slug))
        assert r.status_code == status.HTTP_200_OK
        assert len(r.data) == 1
        row = r.data[0]
        assert "cost_rate" in row and "billable_rate" in row

    def test_member_forbidden(self, session_client, tt):
        self._seed_capacity(tt)
        member = _ws_member(tt["workspace"], role=15)
        session_client.force_authenticate(user=member)
        r = session_client.get(self.URL.format(slug=tt["workspace"].slug))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert "cost_rate" not in str(r.data)
        assert "billable_rate" not in str(r.data)

    def test_guest_forbidden(self, session_client, tt):
        self._seed_capacity(tt)
        guest = _ws_member(tt["workspace"], role=5)
        session_client.force_authenticate(user=guest)
        r = session_client.get(self.URL.format(slug=tt["workspace"].slug))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert "cost_rate" not in str(r.data)
        assert "billable_rate" not in str(r.data)


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

    def _issue(self, key, assignee=None, comment_author=None, worklog_author=None):
        issue = {
            "key": key,
            "fields": {
                "summary": f"Issue {key}",
                "status": {"name": "Todo"},
                "priority": {"name": "Medium"},
                "assignee": assignee,
                "comment": {"comments": []},
                "worklog": {"worklogs": []},
            },
        }
        if comment_author is not None:
            issue["fields"]["comment"]["comments"] = [
                {"id": "9001", "author": comment_author, "body": {"type": "doc", "content": []}}
            ]
        if worklog_author is not None:
            issue["fields"]["worklog"]["worklogs"] = [
                {"author": worklog_author, "timeSpentSeconds": 3600, "started": "2026-06-20T09:00:00.000+0000"}
            ]
        return issue

    def test_assignee_matched_by_display_name_when_email_missing(self, tt):
        # Regression guard for the client-reported "many work items unassigned"
        # bug: Jira Cloud commonly omits emailAddress from the API response for
        # privacy reasons, so the old email-only matching left these unmapped
        # even though the assignee is a real, active project member.
        from plane.utils.jira_importer import run_import

        full_name = f"{tt['user'].first_name} {tt['user'].last_name}"
        issue = self._issue("NM-1", assignee={"displayName": full_name})
        res = run_import(project=tt["project"], initiator=tt["user"], issues=[issue], dry_run=False)
        assert res["unmapped_users"] == []
        created = Issue.objects.get(project=tt["project"], external_source="jira", external_id="NM-1")
        assert IssueAssignee.objects.filter(issue=created, assignee=tt["user"]).exists()

    def test_assignee_unmapped_when_neither_email_nor_name_matches(self, tt):
        from plane.utils.jira_importer import run_import

        issue = self._issue("NM-2", assignee={"displayName": "Nobody Here", "emailAddress": "nobody@example.com"})
        res = run_import(project=tt["project"], initiator=tt["user"], issues=[issue], dry_run=False)
        assert res["unmapped_users"] == ["Nobody Here"]
        created = Issue.objects.get(project=tt["project"], external_source="jira", external_id="NM-2")
        assert not IssueAssignee.objects.filter(issue=created).exists()

    def test_comment_and_worklog_author_fallback_is_now_surfaced_as_unmapped(self, tt):
        # Comments/worklogs from an author we can't map still have to be
        # attributed to *someone* (created_by_id can't be null), so they fall
        # back to the initiator — but that fallback used to be completely
        # silent. It's now counted in unmapped_users so an admin can tell how
        # many entries were silently reattributed to them.
        from plane.utils.jira_importer import run_import

        stranger = {"displayName": "Stranger Danger", "emailAddress": "stranger@example.com"}
        issue = self._issue("NM-3", comment_author=stranger, worklog_author=stranger)
        res = run_import(
            project=tt["project"], initiator=tt["user"], issues=[issue], dry_run=False, with_worklogs=True
        )
        assert res["unmapped_users"] == ["Stranger Danger"]
        created = Issue.objects.get(project=tt["project"], external_source="jira", external_id="NM-3")
        comment = IssueComment.objects.get(issue=created)
        assert comment.actor == tt["user"]
        worklog = IssueWorklog.objects.get(issue=created)
        assert worklog.logged_by == tt["user"]


@pytest.mark.contract
@pytest.mark.django_db
class TestAttachmentImport:
    """Server-side Jira attachment migration (API path). The Jira download and the
    S3/MinIO upload are mocked so the test verifies our record-creation logic and
    idempotency without external services."""

    def _issue(self, key="ENG-500", size=11):
        return {
            "key": key,
            "fields": {
                "summary": "Has an attachment",
                "status": {"name": "Todo"},
                "priority": {"name": "Medium"},
                "attachment": [
                    {
                        "id": "att-1",
                        "filename": "spec.pdf",
                        "mimeType": "application/pdf",
                        "size": size,
                        "content": "https://acme.atlassian.net/rest/api/3/attachment/content/att-1",
                        "author": {"emailAddress": "someone@acme.com"},
                    }
                ],
                "comment": {"comments": []},
                "worklog": {"worklogs": []},
            },
        }

    def _patch_io(self, monkeypatch, body=b"hello-bytes"):
        from plane.settings.storage import S3Storage
        from plane.utils import jira_importer

        # Attachment downloads are streamed, so the double has to behave like a
        # `with requests.get(..., stream=True)` response, not a buffered one.
        class FakeResp:
            content = body
            status_code = 200

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=1024 * 1024):
                for i in range(0, len(body), chunk_size):
                    yield body[i : i + chunk_size]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(jira_importer.requests, "get", lambda *a, **k: FakeResp())
        monkeypatch.setattr(S3Storage, "upload_file", lambda self, f, key, content_type=None, extra_args={}: True)

    def test_attachment_migrated_and_idempotent(self, tt, monkeypatch):
        from plane.db.models import FileAsset, Issue
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)
        res = run_import(
            project=tt["project"], initiator=tt["user"], issues=[self._issue()],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
        )
        assert res["attachments_created"] == 1
        issue = Issue.objects.get(project=tt["project"], external_source="jira", external_id="ENG-500")
        fa = FileAsset.objects.get(external_source="jira", external_id="att-1")
        assert fa.issue_id == issue.id
        assert fa.entity_type == FileAsset.EntityTypeContext.ISSUE_ATTACHMENT
        assert fa.is_uploaded is True
        assert fa.size == len(b"hello-bytes")

        # Re-running skips the already-imported issue (and its attachment).
        res2 = run_import(
            project=tt["project"], initiator=tt["user"], issues=[self._issue()],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
        )
        assert res2["attachments_created"] == 0
        assert FileAsset.objects.filter(external_source="jira", external_id="att-1").count() == 1

    def test_oversized_attachment_skipped(self, tt, monkeypatch):
        from django.conf import settings
        from plane.db.models import FileAsset
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)
        res = run_import(
            project=tt["project"], initiator=tt["user"],
            issues=[self._issue(key="ENG-501", size=settings.FILE_SIZE_LIMIT + 1)],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
        )
        assert res["attachments_created"] == 0
        assert res["attachments_skipped_size"] == 1
        assert not FileAsset.objects.filter(external_source="jira", external_id="att-1").exists()

    def test_attachments_off_by_default(self, tt, monkeypatch):
        from plane.db.models import FileAsset
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)
        res = run_import(
            project=tt["project"], initiator=tt["user"], issues=[self._issue(key="ENG-502")],
            dry_run=False,
        )
        assert res.get("attachments_created", 0) == 0
        assert not FileAsset.objects.filter(external_source="jira").exists()

    def _inline_att(self, aid="img-1", size=0):
        # Jira under-reports inline-image size as 0 here on purpose: the streamed
        # download, not att["size"], is what the cap must be enforced against.
        return {
            "id": aid,
            "filename": "shot.png",
            "mimeType": "image/png",
            "size": size,
            "content": f"https://acme.atlassian.net/rest/api/3/attachment/content/{aid}",
            "author": {"emailAddress": "someone@acme.com"},
        }

    def test_inline_body_image_streamed_cached_and_reused(self, tt, monkeypatch):
        """Inline body images go through the same streaming download as real
        attachments, the FileAsset size comes from the stream (not att["size"]),
        and the per-issue cache holds a file-like object that is re-seeked so a
        second entity reusing the same image reads the full bytes again."""
        from plane.db.models import FileAsset, IssueComment
        from plane.settings.storage import S3Storage
        from plane.utils import jira_importer
        from plane.utils.jira_importer import _upload_body_image

        body = b"inline-image-bytes"
        self._patch_io(monkeypatch, body=body)  # streaming requests.get double
        reads = []

        def capturing_upload(self, f, key, content_type=None, extra_args={}):
            reads.append(f.read())
            return True

        monkeypatch.setattr(S3Storage, "upload_file", capturing_upload)

        att = self._inline_att()
        cache = {}
        asset_id = _upload_body_image(
            att, FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
            {"issue_id": tt["issue"].id}, tt["project"], tt["user"],
            ("e@x.com", "tok"), {}, {}, cache,
        )
        assert asset_id is not None
        fa = FileAsset.objects.get(id=asset_id)
        assert fa.entity_type == FileAsset.EntityTypeContext.ISSUE_DESCRIPTION
        assert fa.size == len(body)  # size derived from the stream
        assert "img-1" in cache  # download cached (a file-like object) for reuse
        assert reads[0] == body

        # The same image reused in a comment must NOT re-download; the resolver
        # would break if requests.get were hit again, so make that fatal.
        def _no_more_downloads(*a, **k):
            raise AssertionError("cached inline image should not be re-downloaded")

        monkeypatch.setattr(jira_importer.requests, "get", _no_more_downloads)
        comment = IssueComment.objects.create(
            issue=tt["issue"], project=tt["project"], workspace=tt["workspace"],
            comment_html="<p></p>", actor=tt["user"], created_by_id=tt["user"].id,
        )
        reused_id = _upload_body_image(
            att, FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
            {"comment_id": comment.id, "issue_id": tt["issue"].id},
            tt["project"], tt["user"], ("e@x.com", "tok"), {}, {}, cache,
        )
        assert reused_id is not None and reused_id != asset_id
        # Re-seek worked: the second upload read the full body, not empty bytes.
        assert reads[1] == body

    def test_oversized_inline_body_image_skipped_via_stream_cap(self, tt, settings, monkeypatch):
        """An inline image whose Jira-reported size is understated but whose real
        body exceeds the cap is rejected DURING streaming (too_large), skipped
        cleanly (returns None, no FileAsset, no crash), and never cached."""
        from plane.db.models import FileAsset
        from plane.utils.jira_importer import _upload_body_image

        settings.FILE_SIZE_LIMIT = 4
        self._patch_io(monkeypatch, body=b"far-more-than-four-bytes")
        att = self._inline_att(aid="img-big", size=0)
        cache = {}
        result = _upload_body_image(
            att, FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
            {"issue_id": tt["issue"].id}, tt["project"], tt["user"],
            ("e@x.com", "tok"), {}, {}, cache,
        )
        assert result is None
        assert FileAsset.objects.filter(external_source="jira", issue_id=tt["issue"].id).count() == 0
        assert cache == {}  # a too_large download is not cached for reuse

    def test_svg_body_image_rejected_before_download(self, tt, monkeypatch):
        """Jira's reported mimeType is attacker-controlled (an imported source
        project can claim anything). An SVG must be rejected outright rather
        than stored as an ISSUE_DESCRIPTION/COMMENT_DESCRIPTION FileAsset — that
        asset is served by the AllowAny public deploy-board endpoint, which
        would otherwise render it inline as same-origin stored XSS."""
        from plane.db.models import FileAsset
        from plane.utils import jira_importer
        from plane.utils.jira_importer import _upload_body_image

        self._patch_io(monkeypatch, body=b"<svg onload=alert(1)>")

        def _no_downloads(*a, **k):
            raise AssertionError("denylisted mime type should be rejected before any download")

        monkeypatch.setattr(jira_importer.requests, "get", _no_downloads)

        att = self._inline_att(aid="img-svg")
        att["mimeType"] = "image/svg+xml"
        att["filename"] = "evil.svg"
        cache = {}
        result = _upload_body_image(
            att, FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
            {"issue_id": tt["issue"].id}, tt["project"], tt["user"],
            ("e@x.com", "tok"), {}, {}, cache,
        )
        assert result is None
        assert FileAsset.objects.filter(external_source="jira", issue_id=tt["issue"].id).count() == 0
        assert cache == {}
