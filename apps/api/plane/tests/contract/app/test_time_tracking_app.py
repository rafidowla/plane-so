# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the time-tracking, clients, timesheets, reporting and
self-service Jira import endpoints added by this fork."""

import csv
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

    def test_report_group_by_issue_tied_totals_ordered_deterministically(self, session_client, tt):
        # Three issues logging the exact same duration tie at the top of the
        # sort. Without a secondary sort key, Postgres doesn't guarantee which
        # order tied rows come back in - which rows a truncated response keeps
        # could vary between two otherwise-identical requests. Pin the
        # documented tiebreaker (ascending issue id) instead of just checking
        # "some order every time", so a regression that removes the secondary
        # sort key fails even if it happens to still look stable locally.
        slug, pid, iid = _ids(tt)
        issue2 = self._second_issue(tt, name="Issue 2")
        issue3 = self._second_issue(tt, name="Issue 3")
        for issue_id in (iid, str(issue2.id), str(issue3.id)):
            session_client.post(
                _wl_url(slug, pid, issue_id), {"duration": 60, "logged_date": "2026-06-22"}, format="json"
            )

        # The tiebreaker orders by the DB's own notion of ascending "issue"
        # (the same field _aggregate sorts by) - not Python's str() ordering,
        # which doesn't necessarily agree with how Postgres compares uuid
        # columns.
        expected_order = [
            str(i)
            for i in Issue.objects.filter(
                id__in=[tt["issue"].id, issue2.id, issue3.id]
            ).order_by("id").values_list("id", flat=True)
        ]

        r1 = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue")
        r2 = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue")
        assert r1.status_code == status.HTTP_200_OK
        assert [g["issue_id"] for g in r1.data["groups"]] == expected_order
        assert [g["issue_id"] for g in r2.data["groups"]] == expected_order

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

    def test_report_malformed_query_params_return_400_not_500(self, session_client, tt):
        # Regression guard: these used to reach the ORM's .filter() directly and
        # raise a bare django.core.exceptions.ValidationError, which DRF's
        # exception_handler doesn't translate — an unhandled 500 instead of a
        # clean 400.
        slug = tt["workspace"].slug
        cases = [
            "start_date=not-a-date",
            "end_date=2026-13-45",  # matches YYYY-MM-DD shape but not a real date
            "user_ids=not-a-uuid",
            "project_ids=not-a-uuid,also-bad",
            "client_id=not-a-uuid",
        ]
        for qs in cases:
            r = session_client.get(f"/api/workspaces/{slug}/time-report/?{qs}")
            assert r.status_code == status.HTTP_400_BAD_REQUEST, f"{qs} -> {r.status_code}"
            assert "error" in r.data

    def test_report_whitespace_only_id_token_ignored(self, session_client, tt):
        # A bare space between commas used to survive the old `if u` filter
        # (only empty strings were dropped, not whitespace) and hit the same
        # invalid-UUID crash. Stripped tokens must be dropped like empty ones.
        slug, pid, iid = _ids(tt)
        self._seed(session_client, tt)
        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&user_ids=%20,{tt['user'].id}")
        assert r.status_code == status.HTTP_200_OK
        assert r.data["totals"]["total_minutes"] == 120

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

    def test_report_csv_export_neutralizes_formula_injection(self, session_client, tt):
        # Security guard ("CSV injection"): a person's display name is
        # self-editable and ends up as a CSV cell verbatim in the "By resource"
        # export. Excel/Sheets evaluate any cell starting with =, +, -, or @ as
        # a formula on open, so a display name of "=HYPERLINK(...)" must not
        # get evaluated when an admin opens the exported report.
        slug, pid, iid = _ids(tt)
        payload = '=HYPERLINK("http://evil.example/","x")'
        attacker = User.objects.create(
            email="attacker@plane.so", username="attacker", first_name="A", last_name="B", display_name=payload
        )
        attacker.set_password("x")
        attacker.save()
        ProjectMember.objects.create(
            project=tt["project"], workspace=tt["workspace"], member=attacker, role=15, is_active=True
        )
        # On-behalf logging (admin/PM function) rather than the attacker logging
        # their own time — manual entry is PM/admin-only; members self-track via
        # the timer. Attributing the entry to the attacker via logged_by is all
        # that's needed to get their display name into the export.
        r = session_client.post(
            _wl_url(slug, pid, iid),
            {"duration": 30, "logged_date": "2026-06-22", "logged_by": str(attacker.id)},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&export=csv")
        assert r.status_code == status.HTTP_200_OK
        rows = list(csv.reader(r.content.decode().splitlines()))
        cells = [cell for row in rows for cell in row]
        assert payload not in cells  # raw formula must never appear as a cell verbatim
        assert f"'{payload}" in cells  # neutralized with a leading apostrophe

    def test_report_csv_export_by_resource_includes_task_breakdown(self, session_client, tt):
        # Client-reported gap: the "By resource" export only ever had the
        # summary row per person, never the per-task detail the UI shows when
        # a resource row is expanded. The CSV now appends a second table.
        slug, pid, iid = _ids(tt)
        issue2 = self._second_issue(tt)
        session_client.post(_wl_url(slug, pid, iid), {"duration": 90, "logged_date": "2026-06-22"}, format="json")
        session_client.post(
            _wl_url(slug, pid, str(issue2.id)), {"duration": 45, "logged_date": "2026-06-23"}, format="json"
        )

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&export=csv")
        assert r.status_code == status.HTTP_200_OK
        rows = list(csv.reader(r.content.decode().splitlines()))

        tt["issue"].refresh_from_db()
        issue2.refresh_from_db()
        expected_names = {
            f"TTP-{tt['issue'].sequence_id} Issue 1",
            f"TTP-{issue2.sequence_id} Issue 2",
        }
        # A blank separator row, then a second header, then the task rows.
        blank_idx = rows.index([])
        assert rows[blank_idx + 1] == ["Resource", "Task", "Project", "Total (h)", "Billable (h)", "Entries"]
        task_rows = rows[blank_idx + 2 :]
        assert {r[1] for r in task_rows} == expected_names
        assert all(r[0] == tt["user"].display_name for r in task_rows)

    def test_report_csv_export_by_resource_task_breakdown_scoped_to_visible_projects(self, session_client, tt):
        # Regression guard: the task-breakdown table carries the same work-item
        # title exposure as group_by=issue, so a workspace MEMBER who isn't a
        # project member must not see task titles from it in the CSV, even
        # though the resource summary table (workspace-level metadata) above
        # it is correctly unaffected.
        slug = tt["workspace"].slug
        self._seed(session_client, tt)
        outsider = _ws_member(tt["workspace"], role=15)
        session_client.force_authenticate(user=outsider)

        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=resource&export=csv")
        assert r.status_code == status.HTTP_200_OK
        rows = list(csv.reader(r.content.decode().splitlines()))

        # Resource summary is still workspace-wide (unaffected).
        assert any(tt["user"].display_name in row for row in rows[:2])
        # But the appended task-breakdown table is empty: header present, no rows.
        blank_idx = rows.index([])
        assert rows[blank_idx + 1] == ["Resource", "Task", "Project", "Total (h)", "Billable (h)", "Entries"]
        assert rows[blank_idx + 2 :] == []

    def test_report_csv_export_by_task_has_no_second_table(self, session_client, tt):
        # The extra breakdown table is specific to group_by=resource — the
        # By-task export is already task-level, so it must not gain a
        # redundant second section.
        slug, pid, iid = _ids(tt)
        self._seed(session_client, tt)
        r = session_client.get(f"/api/workspaces/{slug}/time-report/?group_by=issue&export=csv")
        assert r.status_code == status.HTTP_200_OK
        rows = list(csv.reader(r.content.decode().splitlines()))
        assert [] not in rows

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

    def test_assignee_name_match_scoped_to_project_not_whole_workspace(self, tt):
        # Security guard: the display-name fallback only requires project-
        # admin trust to trigger (that's the whole jira-import permission),
        # not workspace-owner trust. If it matched against ANY workspace
        # member, a project admin could attribute fabricated Jira content to
        # a real person who has nothing to do with this project just by
        # knowing their display name. It must only match this project's own
        # members — a workspace member who isn't on the project must stay
        # unmapped even with an exact display-name hit.
        from plane.utils.jira_importer import run_import

        outsider = _ws_member(tt["workspace"], role=15)
        full_name = f"{outsider.first_name} {outsider.last_name}"
        issue = self._issue("NM-4", assignee={"displayName": full_name})
        res = run_import(project=tt["project"], initiator=tt["user"], issues=[issue], dry_run=False)
        assert res["unmapped_users"] == [full_name]
        created = Issue.objects.get(project=tt["project"], external_source="jira", external_id="NM-4")
        assert not IssueAssignee.objects.filter(issue=created, assignee=outsider).exists()

    def test_assignee_name_match_ambiguous_when_two_members_share_a_name(self, tt):
        # Two project members can share a display name (two "John Smith"s
        # isn't exotic) — silently picking whichever one the map-building
        # loop saw last would misattribute that person's Jira history (and
        # worklogs, which feed the billing report) to a real but wrong
        # person, with nothing surfaced. A colliding name must resolve to
        # neither member and show up as unmapped instead of guessing.
        from plane.utils.jira_importer import run_import

        shared_name = "John Smith"
        first, last = shared_name.split(" ")
        john1 = User.objects.create(email="john1@plane.so", username="john1", first_name=first, last_name=last)
        john2 = User.objects.create(email="john2@plane.so", username="john2", first_name=first, last_name=last)
        for u in (john1, john2):
            u.set_password("x")
            u.save()
            ProjectMember.objects.create(
                project=tt["project"], workspace=tt["workspace"], member=u, role=15, is_active=True
            )

        issue = self._issue("NM-5", assignee={"displayName": shared_name})
        res = run_import(project=tt["project"], initiator=tt["user"], issues=[issue], dry_run=False)
        assert res["unmapped_users"] == [shared_name]
        created = Issue.objects.get(project=tt["project"], external_source="jira", external_id="NM-5")
        assert not IssueAssignee.objects.filter(issue=created, assignee__in=[john1, john2]).exists()

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
        # `with pinned_fetch_following_redirects(..., stream=True)` response,
        # not a buffered one.
        class FakeResp:
            content = body
            status_code = 200

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=1024 * 1024):
                for i in range(0, len(body), chunk_size):
                    yield body[i : i + chunk_size]

            def close(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            jira_importer, "pinned_fetch_following_redirects", lambda method, url, **kwargs: (FakeResp(), url)
        )
        monkeypatch.setattr(S3Storage, "upload_file", lambda self, f, key, content_type=None, extra_args={}: True)

    def test_attachment_migrated_and_idempotent(self, tt, monkeypatch):
        from plane.db.models import FileAsset, Issue
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)
        res = run_import(
            project=tt["project"], initiator=tt["user"], issues=[self._issue()],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
            jira_url="https://acme.atlassian.net",
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
            jira_url="https://acme.atlassian.net",
        )
        assert res2["attachments_created"] == 0
        assert FileAsset.objects.filter(external_source="jira", external_id="att-1").count() == 1

    def test_attachment_on_other_host_rejected(self, tt, monkeypatch):
        """A Jira response is attacker-influenceable data, not a trusted URL - a
        `content` URL pointing anywhere other than the configured Jira instance
        (e.g. an internal address) must be refused, not fetched (SSRF)."""
        from plane.db.models import FileAsset
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)

        def _fail_if_called(*a, **k):
            raise AssertionError("a content URL on an unexpected host must never be fetched")

        from plane.utils import jira_importer

        monkeypatch.setattr(jira_importer, "pinned_fetch_following_redirects", _fail_if_called)

        issue = self._issue(key="ENG-503")
        issue["fields"]["attachment"][0]["content"] = "http://169.254.169.254/latest/meta-data/"
        res = run_import(
            project=tt["project"], initiator=tt["user"], issues=[issue],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
            jira_url="https://acme.atlassian.net",
        )
        assert res["attachments_created"] == 0
        assert res["attachments_failed"] == 1
        assert not FileAsset.objects.filter(external_source="jira", external_id="att-1").exists()

    def test_oversized_attachment_skipped(self, tt, monkeypatch):
        from django.conf import settings
        from plane.db.models import FileAsset
        from plane.utils.jira_importer import run_import

        self._patch_io(monkeypatch)
        res = run_import(
            project=tt["project"], initiator=tt["user"],
            issues=[self._issue(key="ENG-501", size=settings.FILE_SIZE_LIMIT + 1)],
            dry_run=False, with_attachments=True, jira_auth=("e@x.com", "tok"),
            jira_url="https://acme.atlassian.net",
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
            ("e@x.com", "tok"), {}, {}, cache, ("https", "acme.atlassian.net"),
        )
        assert asset_id is not None
        fa = FileAsset.objects.get(id=asset_id)
        assert fa.entity_type == FileAsset.EntityTypeContext.ISSUE_DESCRIPTION
        assert fa.size == len(body)  # size derived from the stream
        assert "img-1" in cache  # download cached (a file-like object) for reuse
        assert reads[0] == body

        # The same image reused in a comment must NOT re-download; the resolver
        # would break if the fetch were hit again, so make that fatal.
        def _no_more_downloads(*a, **k):
            raise AssertionError("cached inline image should not be re-downloaded")

        monkeypatch.setattr(jira_importer, "pinned_fetch_following_redirects", _no_more_downloads)
        comment = IssueComment.objects.create(
            issue=tt["issue"], project=tt["project"], workspace=tt["workspace"],
            comment_html="<p></p>", actor=tt["user"], created_by_id=tt["user"].id,
        )
        reused_id = _upload_body_image(
            att, FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
            {"comment_id": comment.id, "issue_id": tt["issue"].id},
            tt["project"], tt["user"], ("e@x.com", "tok"), {}, {}, cache, ("https", "acme.atlassian.net"),
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
            ("e@x.com", "tok"), {}, {}, cache, ("https", "acme.atlassian.net"),
        )
        assert result is None
        assert FileAsset.objects.filter(external_source="jira", issue_id=tt["issue"].id).count() == 0
        assert cache == {}  # a too_large download is not cached for reuse

    def test_management_command_threads_jira_url_for_attachments(self, tt, monkeypatch):
        """The `import_jira` management command is a second, independent caller
        of run_import(..., with_attachments=True) - it must also pass jira_url
        through so the host-allowlist check has something to allow, not fail
        every attachment closed by omission."""
        import io

        from django.core.management import call_command

        from plane.db.management.commands import import_jira as import_jira_cmd
        from plane.db.models import FileAsset

        self._patch_io(monkeypatch)
        monkeypatch.setattr(import_jira_cmd, "fetch_jira_issues", lambda **kwargs: [self._issue(key="ENG-504")])

        out = io.StringIO()
        call_command(
            "import_jira",
            "--slug", tt["workspace"].slug,
            "--project", str(tt["project"].id),
            "--initiator", tt["user"].email,
            "--jira-url", "https://acme.atlassian.net",
            "--jira-email", "e@x.com",
            "--jira-token", "tok",
            "--with-attachments",
            "--execute",
            stdout=out,
        )
        assert "1 migrated" in out.getvalue()
        assert "0 failed" in out.getvalue()
        assert FileAsset.objects.filter(external_source="jira", external_id="att-1").exists()

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

        monkeypatch.setattr(jira_importer, "pinned_fetch_following_redirects", _no_downloads)

        att = self._inline_att(aid="img-svg")
        att["mimeType"] = "image/svg+xml"
        att["filename"] = "evil.svg"
        cache = {}
        result = _upload_body_image(
            att, FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
            {"issue_id": tt["issue"].id}, tt["project"], tt["user"],
            ("e@x.com", "tok"), {}, {}, cache, ("https", "acme.atlassian.net"),
        )
        assert result is None
        assert FileAsset.objects.filter(external_source="jira", issue_id=tt["issue"].id).count() == 0
        assert cache == {}

    def test_download_attachment_rejects_mismatched_or_missing_allowed_host(self, monkeypatch):
        """_download_attachment is the last line of defense against a Jira
        response pointing its `content` URL somewhere other than the
        configured instance (SSRF) - it must refuse before ever calling the
        SSRF-safe fetch, both when the host doesn't match and when no
        allowed_origin was configured at all (e.g. jira_url missing)."""
        from plane.utils import jira_importer
        from plane.utils.jira_importer import _download_attachment

        def _fail_if_called(*a, **k):
            raise AssertionError("host-mismatched/unconfigured content URL must never be fetched")

        monkeypatch.setattr(jira_importer, "pinned_fetch_following_redirects", _fail_if_called)

        fileobj, size = _download_attachment(
            "http://internal.corp/secret", ("e@x.com", "tok"), 1024, ("https", "acme.atlassian.net")
        )
        assert (fileobj, size) == (None, 0)

        fileobj, size = _download_attachment(
            "https://acme.atlassian.net/rest/api/3/attachment/content/att-1", ("e@x.com", "tok"), 1024, None
        )
        assert (fileobj, size) == (None, 0)

    def test_download_attachment_rejects_scheme_downgrade_on_matching_host(self, monkeypatch):
        """A `content` URL on the RIGHT host but the WRONG scheme (http instead
        of the configured https) must still be refused - the Basic-Auth header
        would otherwise go out in cleartext to a host that merely looks right."""
        from plane.utils import jira_importer
        from plane.utils.jira_importer import _download_attachment

        def _fail_if_called(*a, **k):
            raise AssertionError("scheme-downgraded content URL must never be fetched")

        monkeypatch.setattr(jira_importer, "pinned_fetch_following_redirects", _fail_if_called)

        fileobj, size = _download_attachment(
            "http://acme.atlassian.net/rest/api/3/attachment/content/att-1",
            ("e@x.com", "tok"),
            1024,
            ("https", "acme.atlassian.net"),
        )
        assert (fileobj, size) == (None, 0)

    def test_download_attachment_allows_configured_host_on_private_ip(self, monkeypatch):
        """A self-hosted Jira instance living on the same internal network as
        Plane (a private IP) is a legitimate, common deployment - the download
        must succeed for the one exact, admin-configured host even when it
        resolves privately. This host is admin-trusted config, not
        attacker-controlled data, unlike everything else in _download_attachment
        that IS attacker-influenceable (the content_url path/query, mimeType,
        reported size, etc)."""
        from unittest.mock import patch

        from plane.settings.storage import S3Storage
        from plane.utils.jira_importer import _download_attachment

        body = b"on-prem-bytes"

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=1024 * 1024):
                yield body

            def close(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            "plane.utils.url_security.requests.Session.request", lambda self, *a, **k: FakeResp()
        )
        monkeypatch.setattr(S3Storage, "upload_file", lambda self, f, key, content_type=None, extra_args={}: True)

        with patch("plane.utils.ip_address.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("10.0.0.5", 0))]
            fileobj, size = _download_attachment(
                "https://jira.internal.corp/rest/api/3/attachment/content/att-1",
                ("e@x.com", "tok"),
                1024,
                ("https", "jira.internal.corp"),
            )
        assert size == len(body)
        assert fileobj is not None

    def test_download_attachment_blocks_redirect_off_the_allowlisted_host(self, monkeypatch):
        """The private-IP bypass only covers the one exact configured host - a
        redirect from it to a DIFFERENT private-IP host must still be blocked,
        so the on-prem allowance can't be used as a springboard to anywhere
        else on the internal network."""
        from unittest.mock import MagicMock, patch

        from plane.utils.jira_importer import _download_attachment

        # `url` here is already rewritten to the pinned IP literal (the whole
        # point of pinning), so the two hops can't be told apart by hostname -
        # distinguish by path instead, which pinning leaves untouched.
        def _fake_request(self, method, url, **kwargs):
            resp = MagicMock()
            if url.endswith("/secret"):
                resp.status_code = 200
                resp.headers = {}
            else:
                resp.status_code = 302
                resp.headers = {"Location": "https://other-internal-host/secret"}
            return resp

        monkeypatch.setattr("plane.utils.url_security.requests.Session.request", _fake_request)

        with patch("plane.utils.ip_address.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("10.0.0.5", 0))]
            fileobj, size = _download_attachment(
                "https://jira.internal.corp/rest/api/3/attachment/content/att-1",
                ("e@x.com", "tok"),
                1024,
                ("https", "jira.internal.corp"),
            )
        assert (fileobj, size) == (None, 0)
