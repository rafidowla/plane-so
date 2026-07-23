# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the fork's custom-dashboards feature (plane.dashboards).

Pattern mirrors test_time_tracking_app.py / test_custom_properties_app.py. The
dashboard widget CRUD/data/issues endpoints are permissioned at the WORKSPACE
level (ROLE.ADMIN / ROLE.MEMBER via WorkspaceMember), not the PROJECT level,
so the extra-user helper here mints WorkspaceMember rows rather than
ProjectMember rows; project-level membership is added separately wherever a
test needs to exercise project-visibility scoping.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from plane.dashboards.models import WorkspaceDashboardWidget
from plane.db.models import (
    Issue,
    IssueView,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

WidgetType = WorkspaceDashboardWidget.WidgetType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def dash(db, create_user, workspace):
    """Workspace (test user is already WorkspaceMember ADMIN via the `workspace`
    fixture) with two projects, the test user as project admin on both."""
    project_a = Project.objects.create(
        workspace=workspace, name="Dash Project A", identifier="DPA", created_by=create_user
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project_a, name="DPA")
    ProjectMember.objects.create(
        project=project_a, workspace=workspace, member=create_user, role=20, is_active=True
    )

    project_b = Project.objects.create(
        workspace=workspace, name="Dash Project B", identifier="DPB", created_by=create_user
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project_b, name="DPB")
    ProjectMember.objects.create(
        project=project_b, workspace=workspace, member=create_user, role=20, is_active=True
    )

    return {
        "workspace": workspace,
        "project_a": project_a,
        "project_b": project_b,
        "user": create_user,
    }


def _workspace_member(workspace, role=15):
    """Mint a fresh user with a WORKSPACE-level role only (no project membership).
    Enough to exercise the widget CRUD/data endpoints' `level="WORKSPACE"`
    `allow_permission` check."""
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(email=f"dash-{uid}@plane.so", username=f"dash_{uid}", first_name="Mem")
    u.set_password("x")
    u.save()
    WorkspaceMember.objects.create(workspace=workspace, member=u, role=role, is_active=True)
    return u


def _add_project_member(workspace, project, user, role=15):
    ProjectMember.objects.create(project=project, workspace=workspace, member=user, role=role, is_active=True)


def _widgets_url(slug):
    return f"/api/workspaces/{slug}/fork-dashboard/widgets/"


def _widget_url(slug, widget_id):
    return f"{_widgets_url(slug)}{widget_id}/"


def _widget_data_url(slug, widget_id):
    return f"{_widget_url(slug, widget_id)}data/"


def _widget_issues_url(slug, widget_id):
    return f"{_widget_url(slug, widget_id)}issues/"


def _make_issue(workspace, project, user, name="I", state=None, priority="none"):
    issue = Issue(workspace=workspace, project=project, name=name, state=state, priority=priority)
    issue.save(created_by_id=user.id)
    return issue


# ---------------------------------------------------------------------------
# 1. CRUD + permissions
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestWidgetCRUDAndPermissions:
    def _valid_config(self, dash, widget_type, issue_view_id=None):
        if widget_type == WidgetType.DISTRIBUTION_PIE:
            return {"widget_type": widget_type, "title": "Dist", "config": {"group_by": "state"}}
        if widget_type == WidgetType.PROJECT_BREAKDOWN_PIE:
            return {"widget_type": widget_type, "title": "Breakdown", "config": {}}
        if widget_type == WidgetType.AGE_TREND:
            return {"widget_type": widget_type, "title": "Age", "config": {"lookback_days": 30}}
        if widget_type == WidgetType.VIEW_LIST:
            return {
                "widget_type": widget_type,
                "title": "Views",
                "config": {"page_size": 10},
                "issue_view": issue_view_id,
            }
        raise AssertionError(widget_type)

    def test_admin_creates_each_widget_type_and_they_appear_in_list(self, session_client, dash):
        slug = dash["workspace"].slug
        view = IssueView.objects.create(
            workspace=dash["workspace"], project=None, name="V1", owned_by=dash["user"]
        )
        created_ids = []
        for widget_type in (
            WidgetType.DISTRIBUTION_PIE,
            WidgetType.PROJECT_BREAKDOWN_PIE,
            WidgetType.AGE_TREND,
            WidgetType.VIEW_LIST,
        ):
            body = self._valid_config(dash, widget_type, issue_view_id=str(view.id))
            r = session_client.post(_widgets_url(slug), body, format="json")
            assert r.status_code == status.HTTP_201_CREATED, r.data
            assert r.data["widget_type"] == widget_type
            created_ids.append(r.data["id"])

        lst = session_client.get(_widgets_url(slug))
        assert lst.status_code == status.HTTP_200_OK
        listed_ids = {row["id"] for row in lst.data}
        assert set(created_ids) == listed_ids

    def test_member_can_list(self, session_client, dash):
        slug = dash["workspace"].slug
        session_client.post(
            _widgets_url(slug),
            self._valid_config(dash, WidgetType.PROJECT_BREAKDOWN_PIE),
            format="json",
        )
        member = _workspace_member(dash["workspace"], role=15)
        session_client.force_authenticate(user=member)
        r = session_client.get(_widgets_url(slug))
        assert r.status_code == status.HTTP_200_OK
        assert len(r.data) == 1

    def test_member_cannot_write(self, session_client, dash):
        slug = dash["workspace"].slug
        created = session_client.post(
            _widgets_url(slug),
            self._valid_config(dash, WidgetType.PROJECT_BREAKDOWN_PIE),
            format="json",
        ).data
        member = _workspace_member(dash["workspace"], role=15)
        session_client.force_authenticate(user=member)

        r_post = session_client.post(
            _widgets_url(slug), self._valid_config(dash, WidgetType.PROJECT_BREAKDOWN_PIE), format="json"
        )
        assert r_post.status_code == status.HTTP_403_FORBIDDEN

        r_patch = session_client.patch(
            _widget_url(slug, created["id"]), {"title": "New"}, format="json"
        )
        assert r_patch.status_code == status.HTTP_403_FORBIDDEN

        r_delete = session_client.delete(_widget_url(slug, created["id"]))
        assert r_delete.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_list(self, session_client, dash):
        slug = dash["workspace"].slug
        guest = _workspace_member(dash["workspace"], role=5)
        session_client.force_authenticate(user=guest)
        r = session_client.get(_widgets_url(slug))
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_workspace_isolation(self, session_client, dash):
        slug_a = dash["workspace"].slug
        created = session_client.post(
            _widgets_url(slug_a),
            self._valid_config(dash, WidgetType.PROJECT_BREAKDOWN_PIE),
            format="json",
        ).data
        widget_id = created["id"]

        workspace_b = Workspace.objects.create(
            name="Other Workspace", owner=dash["user"], slug="other-workspace"
        )
        WorkspaceMember.objects.create(workspace=workspace_b, member=dash["user"], role=20, is_active=True)
        slug_b = workspace_b.slug

        # Listing under workspace B never sees workspace A's widget.
        lst_b = session_client.get(_widgets_url(slug_b))
        assert lst_b.status_code == status.HTTP_200_OK
        assert lst_b.data == []

        # PATCH/DELETE of A's widget_id under B's slug -> 404 (not found in scope).
        patch_b = session_client.patch(
            _widget_url(slug_b, widget_id), {"title": "Hijack"}, format="json"
        )
        assert patch_b.status_code == status.HTTP_404_NOT_FOUND

        delete_b = session_client.delete(_widget_url(slug_b, widget_id))
        assert delete_b.status_code == status.HTTP_404_NOT_FOUND

        # The widget is untouched.
        assert WorkspaceDashboardWidget.objects.get(id=widget_id).title != "Hijack"

    def test_soft_delete(self, session_client, dash):
        slug = dash["workspace"].slug
        created = session_client.post(
            _widgets_url(slug),
            self._valid_config(dash, WidgetType.PROJECT_BREAKDOWN_PIE),
            format="json",
        ).data
        widget_id = created["id"]

        r = session_client.delete(_widget_url(slug, widget_id))
        assert r.status_code == status.HTTP_204_NO_CONTENT

        lst = session_client.get(_widgets_url(slug))
        assert all(row["id"] != widget_id for row in lst.data)

        # Row still exists (soft delete), with deleted_at set, via the
        # "including deleted" manager -- not hard-deleted.
        assert not WorkspaceDashboardWidget.objects.filter(id=widget_id).exists()
        widget = WorkspaceDashboardWidget.all_objects.get(id=widget_id)
        assert widget.deleted_at is not None


# ---------------------------------------------------------------------------
# 2. Config validation (400s)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestConfigValidation:
    def test_distribution_pie_bad_group_by(self, session_client, dash):
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.DISTRIBUTION_PIE,
                "config": {"group_by": "not_a_real_field"},
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_age_trend_bad_lookback_days(self, session_client, dash):
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {"widget_type": WidgetType.AGE_TREND, "config": {"lookback_days": 45}},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_view_list_requires_issue_view(self, session_client, dash):
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {"widget_type": WidgetType.VIEW_LIST, "config": {"page_size": 10}},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_view_list_rejects_cross_workspace_issue_view(self, session_client, dash):
        other_workspace = Workspace.objects.create(
            name="Foreign Workspace", owner=dash["user"], slug="foreign-workspace"
        )
        WorkspaceMember.objects.create(
            workspace=other_workspace, member=dash["user"], role=20, is_active=True
        )
        foreign_view = IssueView.objects.create(
            workspace=other_workspace, project=None, name="Foreign", owned_by=dash["user"]
        )
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.VIEW_LIST,
                "config": {"page_size": 10},
                "issue_view": str(foreign_view.id),
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_config_key_rejected(self, session_client, dash):
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.DISTRIBUTION_PIE,
                "config": {"group_by": "state", "not_a_real_key": True},
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_distribution_pie_forbids_issue_view(self, session_client, dash):
        view = IssueView.objects.create(
            workspace=dash["workspace"], project=None, name="V", owned_by=dash["user"]
        )
        slug = dash["workspace"].slug
        r = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.DISTRIBUTION_PIE,
                "config": {"group_by": "state"},
                "issue_view": str(view.id),
            },
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# 3. distribution_pie data correctness
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestDistributionPieData:
    def _seed(self, dash):
        """16 issues total, spread across 3 states / 2 priorities:
        - Todo (unstarted) x10, priority=medium
        - Done (completed) x4,  priority=urgent
        - In Progress (started) x2, priority=urgent
        => state counts: Todo=10, Done=4, In Progress=2
        => priority counts: medium=10, urgent=6
        => completed_percentage = 4 / 16 * 100 = 25
        """
        workspace, project, user = dash["workspace"], dash["project_a"], dash["user"]
        todo = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
        done = State.objects.create(workspace=workspace, project=project, name="Done", group="completed")
        prog = State.objects.create(workspace=workspace, project=project, name="In Progress", group="started")

        for i in range(10):
            _make_issue(workspace, project, user, name=f"Todo-{i}", state=todo, priority="medium")
        for i in range(4):
            _make_issue(workspace, project, user, name=f"Done-{i}", state=done, priority="urgent")
        for i in range(2):
            _make_issue(workspace, project, user, name=f"Prog-{i}", state=prog, priority="urgent")

        return {"todo": todo, "done": done, "prog": prog}

    def test_grouped_by_state(self, session_client, dash):
        states = self._seed(dash)
        slug = dash["workspace"].slug
        widget = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.DISTRIBUTION_PIE,
                "config": {"group_by": "state", "project_ids": [str(dash["project_a"].id)]},
            },
            format="json",
        ).data

        r = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        by_key = {str(item["key"]): item for item in r.data["data"]}
        assert by_key[str(states["todo"].id)]["count"] == 10
        assert by_key[str(states["done"].id)]["count"] == 4
        assert by_key[str(states["prog"].id)]["count"] == 2
        assert r.data["completed_percentage"] == 25

    def test_grouped_by_priority(self, session_client, dash):
        self._seed(dash)
        slug = dash["workspace"].slug
        widget = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.DISTRIBUTION_PIE,
                "config": {"group_by": "priority", "project_ids": [str(dash["project_a"].id)]},
            },
            format="json",
        ).data

        r = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        by_key = {item["key"]: item for item in r.data["data"]}
        assert by_key["medium"]["count"] == 10
        assert by_key["urgent"]["count"] == 6
        assert r.data["completed_percentage"] == 25


# ---------------------------------------------------------------------------
# 4. project_breakdown_pie data correctness
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestProjectBreakdownPieData:
    def test_counts_and_visibility_scoping(self, session_client, dash):
        workspace, project_a, project_b, user = (
            dash["workspace"],
            dash["project_a"],
            dash["project_b"],
            dash["user"],
        )
        for i in range(7):
            _make_issue(workspace, project_a, user, name=f"A-{i}")
        for i in range(3):
            _make_issue(workspace, project_b, user, name=f"B-{i}")

        slug = workspace.slug
        widget = session_client.post(
            _widgets_url(slug),
            {"widget_type": WidgetType.PROJECT_BREAKDOWN_PIE, "config": {}},
            format="json",
        ).data

        # Admin (member of both projects) sees both.
        r = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        rows = {row["key"]: row["count"] for row in r.data["data"]}
        assert rows[str(project_a.id)] == 7
        assert rows[str(project_b.id)] == 3

        # A second user who is only a member of project_a must only see
        # project_a's count -- this is the visibility-scoping regression case.
        limited_user = _workspace_member(workspace, role=15)
        _add_project_member(workspace, project_a, limited_user, role=15)
        session_client.force_authenticate(user=limited_user)

        r2 = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r2.status_code == status.HTTP_200_OK
        rows2 = {row["key"]: row["count"] for row in r2.data["data"]}
        assert rows2 == {str(project_a.id): 7}


# ---------------------------------------------------------------------------
# 5. age_trend data correctness
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestAgeTrendData:
    def test_avg_age_and_open_count(self, session_client, dash):
        """Seed 3 issues relative to "today" (T = timezone.now().date()):

        - A: created T-20, never completed (still open).
        - B: created T-25, completed_at = T-10 (inside the 30-day lookback).
        - C: created T-5, never completed (still open).

        "Open on day D" (per widget_data.py) == created_date <= D AND
        (completed_date is None OR completed_date >= D) -- i.e. the day an
        issue is completed still counts it as open, the day *after* does not.

        Hand-computed expectations for lookback_days=30 (window T-29..T):

        Day D1 = T-15:
          A: created T-20 <= D1, open.            age = D1 - (T-20) = 5
          B: created T-25 <= D1; completed T-10 >= D1 (T-10 >= T-15), open.
                                                    age = D1 - (T-25) = 10
          C: created T-5 <= D1? No (T-5 is after T-15) -> not yet created.
          => open_count = 2, avg_age_days = (5 + 10) / 2 = 7.5

        Day D2 = T-10 (B's completion day -- the inclusive boundary):
          A: open.                                 age = D2 - (T-20) = 10
          B: completed T-10 >= D2 (T-10 >= T-10), open (boundary inclusive).
                                                    age = D2 - (T-25) = 15
          C: not yet created.
          => open_count = 2, avg_age_days = (10 + 15) / 2 = 12.5

        Day D3 = T (today):
          A: open.                                 age = T - (T-20) = 20
          B: completed T-10 >= T? No -> not open (the day after completion).
          C: created T-5 <= T, open.                age = T - (T-5) = 5
          => open_count = 2, avg_age_days = (20 + 5) / 2 = 12.5
        """
        workspace, project, user = dash["workspace"], dash["project_a"], dash["user"]
        now = timezone.now()
        today = now.date()

        issue_a = _make_issue(workspace, project, user, name="A")
        Issue.objects.filter(id=issue_a.id).update(created_at=now - timedelta(days=20), completed_at=None)

        issue_b = _make_issue(workspace, project, user, name="B")
        Issue.objects.filter(id=issue_b.id).update(
            created_at=now - timedelta(days=25), completed_at=now - timedelta(days=10)
        )

        issue_c = _make_issue(workspace, project, user, name="C")
        Issue.objects.filter(id=issue_c.id).update(created_at=now - timedelta(days=5), completed_at=None)

        slug = workspace.slug
        widget = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.AGE_TREND,
                "config": {"project_ids": [str(project.id)], "lookback_days": 30},
            },
            format="json",
        ).data

        r = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        data = r.data["data"]

        assert len(data) == 30
        assert data[-1]["key"] == today.strftime("%Y-%m-%d")
        assert data[0]["key"] == (today - timedelta(days=29)).strftime("%Y-%m-%d")

        by_key = {row["key"]: row for row in data}

        d1 = (today - timedelta(days=15)).strftime("%Y-%m-%d")
        assert by_key[d1]["open_count"] == 2
        assert by_key[d1]["avg_age_days"] == 7.5

        d2 = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        assert by_key[d2]["open_count"] == 2
        assert by_key[d2]["avg_age_days"] == 12.5

        d3 = today.strftime("%Y-%m-%d")
        assert by_key[d3]["open_count"] == 2
        assert by_key[d3]["avg_age_days"] == 12.5


# ---------------------------------------------------------------------------
# 6. view_list data
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
class TestViewListData:
    def _seed_view_and_widget(self, session_client, dash, page_size=10):
        workspace, project, user = dash["workspace"], dash["project_a"], dash["user"]
        for i in range(15):
            _make_issue(workspace, project, user, name=f"Urgent-{i}", priority="urgent")
        for i in range(3):
            _make_issue(workspace, project, user, name=f"Medium-{i}", priority="medium")

        view = IssueView.objects.create(
            workspace=workspace,
            project=None,
            name="Urgent Only",
            owned_by=user,
            filters={"priority": ["urgent"]},
        )
        assert view.query == {"priority__in": ["urgent"]}

        slug = workspace.slug
        widget = session_client.post(
            _widgets_url(slug),
            {
                "widget_type": WidgetType.VIEW_LIST,
                "config": {"page_size": page_size},
                "issue_view": str(view.id),
            },
            format="json",
        ).data
        return widget

    def test_issues_filtered_and_paginated(self, session_client, dash):
        widget = self._seed_view_and_widget(session_client, dash, page_size=10)
        slug = dash["workspace"].slug

        r = session_client.get(_widget_issues_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        assert r.data["count"] == 10
        assert r.data["total_results"] == 15
        assert all(item["priority"] == "urgent" for item in r.data["results"])

    def test_data_endpoint_400_for_view_list(self, session_client, dash):
        widget = self._seed_view_and_widget(session_client, dash)
        slug = dash["workspace"].slug
        r = session_client.get(_widget_data_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_deleted_view_yields_clean_empty_state(self, session_client, dash):
        widget = self._seed_view_and_widget(session_client, dash)
        slug = dash["workspace"].slug

        # Simulate the SET_NULL that fires when the saved View itself is deleted.
        WorkspaceDashboardWidget.objects.filter(id=widget["id"]).update(issue_view_id=None)

        r = session_client.get(_widget_issues_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_200_OK
        assert r.data == {"data": [], "view_deleted": True}

    def test_issues_endpoint_400_for_non_view_list_widget(self, session_client, dash):
        slug = dash["workspace"].slug
        widget = session_client.post(
            _widgets_url(slug),
            {"widget_type": WidgetType.PROJECT_BREAKDOWN_PIE, "config": {}},
            format="json",
        ).data
        r = session_client.get(_widget_issues_url(slug, widget["id"]))
        assert r.status_code == status.HTTP_400_BAD_REQUEST
