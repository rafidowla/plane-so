# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: guest-access PSR-119 — module / cycle aggregates in the web app API only count what a guest can see.

A "restricted" guest is a project Guest in a project with ``guest_view_all_features`` off; they may only see work
items they created. The module / cycle lists, the archived-module endpoints and the cycle progress / analytics
endpoints must not reveal how many hidden items exist (counts, state groups, estimate sums, assignee / label
distributions, burndown). Mirrors ``tests/contract/api/test_guest_access.py`` for the v1 API.

Fixture: module ``M`` and cycle ``C`` each hold three items --
  i_admin  (admin, completed state, 4 points, label "bug", assigned to admin)  -> hidden from guests
  i_guest  (guest, backlog, 1 point, assigned to guest)
  i_guest2 (guest2, backlog, 2 points)                                         -> hidden from ``guest``
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    CycleIssue,
    Estimate,
    EstimatePoint,
    Issue,
    IssueAssignee,
    IssueLabel,
    Label,
    Module,
    ModuleIssue,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)

PROJECT_URL = "/api/workspaces/{slug}/projects/{project_id}/"


def _user(name, workspace, ws_role):
    uid = uuid4().hex[:6]
    user = User.objects.create(email=f"{name}-{uid}@plane.so", username=f"{name}_{uid}")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role)
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def world(db, workspace, create_user):
    admin = create_user
    member = _user("member", workspace, 15)
    guest = _user("guest", workspace, 5)
    guest2 = _user("guest2", workspace, 5)

    project = Project.objects.create(name="GA", identifier="GA", workspace=workspace, created_by=admin)
    for user, role in ((admin, 20), (member, 15), (guest, 5), (guest2, 5)):
        ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role, is_active=True)

    backlog = State.objects.create(name="Backlog", group="backlog", default=True, project=project, workspace=workspace)
    done = State.objects.create(name="Done", group="completed", project=project, workspace=workspace)

    estimate = Estimate.objects.create(name="Points", type="points", project=project, workspace=workspace)
    points = {
        v: EstimatePoint.objects.create(estimate=estimate, key=int(v), value=v, project=project, workspace=workspace)
        for v in ("1", "2", "4")
    }
    Project.objects.filter(id=project.id).update(estimate=estimate)

    def issue(name, creator, state, point):
        item = Issue(name=name, project=project, workspace=workspace, state=state, estimate_point=point)
        item.save(created_by_id=creator.id)
        return item

    i_admin = issue("by admin", admin, done, points["4"])
    Issue.objects.filter(id=i_admin.id).update(completed_at=timezone.now())
    i_guest = issue("by guest", guest, backlog, points["1"])
    i_guest2 = issue("by guest2", guest2, backlog, points["2"])

    bug = Label.objects.create(name="bug", project=project, workspace=workspace)
    IssueLabel.objects.create(issue=i_admin, label=bug, project=project, workspace=workspace)
    IssueAssignee.objects.create(issue=i_admin, assignee=admin, project=project, workspace=workspace)
    IssueAssignee.objects.create(issue=i_guest, assignee=guest, project=project, workspace=workspace)

    now = timezone.now()
    module = Module.objects.create(
        name="M",
        project=project,
        workspace=workspace,
        start_date=(now - timedelta(days=1)).date(),
        target_date=(now + timedelta(days=1)).date(),
    )
    cycle = Cycle.objects.create(
        name="C",
        project=project,
        workspace=workspace,
        owned_by=admin,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )
    for item in (i_admin, i_guest, i_guest2):
        ModuleIssue.objects.create(module=module, issue=item, project=project, workspace=workspace)
        CycleIssue.objects.create(cycle=cycle, issue=item, project=project, workspace=workspace)

    class World:
        pass

    w = World()
    w.ws, w.project, w.module, w.cycle = workspace, project, module, cycle
    w.admin, w.member, w.guest, w.guest2 = admin, member, guest, guest2
    w.c = {name: _client(u) for name, u in (("admin", admin), ("member", member), ("guest", guest), ("guest2", guest2))}
    return w


def url(w, path=""):
    return PROJECT_URL.format(slug=w.ws.slug, project_id=w.project.id) + path


def row(response, obj):
    assert response.status_code == status.HTTP_200_OK, response.data
    rows = [r for r in response.data if str(r["id"]) == str(obj.id)]
    assert len(rows) == 1
    return rows[0]


# who -> (total, completed, backlog, total_points, completed_points)
EXPECTED = [
    ("guest", (1, 0, 1, 1.0, 0.0)),
    ("guest2", (1, 0, 1, 2.0, 0.0)),
    ("member", (3, 1, 2, 7.0, 4.0)),
    ("admin", (3, 1, 2, 7.0, 4.0)),
]


@pytest.mark.contract
class TestModuleListCounts:
    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_counts_state_groups_and_estimates(self, world, who, expected):
        total, completed, backlog, total_points, completed_points = expected
        data = row(world.c[who].get(url(world, "modules/")), world.module)
        assert data["total_issues"] == total
        assert data["completed_issues"] == completed
        assert data["backlog_issues"] == backlog
        assert data["total_estimate_points"] == total_points
        assert data["completed_estimate_points"] == completed_points

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        assert row(world.c["guest"].get(url(world, "modules/")), world.module)["total_issues"] == 3

    def test_removed_guest_gets_nothing(self, world):
        ProjectMember.objects.filter(project=world.project, member=world.guest).update(is_active=False)
        assert world.c["guest"].get(url(world, "modules/")).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
class TestArchivedModules:
    @pytest.fixture(autouse=True)
    def archive(self, world):
        Module.objects.filter(id=world.module.id).update(archived_at=timezone.now())

    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_archived_list_counts(self, world, who, expected):
        total, completed, backlog, _, _ = expected
        data = row(world.c[who].get(url(world, "archived-modules/")), world.module)
        assert (data["total_issues"], data["completed_issues"], data["backlog_issues"]) == (total, completed, backlog)

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        assert row(world.c["guest"].get(url(world, "archived-modules/")), world.module)["total_issues"] == 3

    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_archived_detail_counts_and_distributions(self, world, who, expected):
        r = world.c[who].get(url(world, f"archived-modules/{world.module.id}/"))
        assert r.status_code == status.HTTP_200_OK, r.data
        total, completed, _, total_points, _ = expected
        assert r.data["total_issues"] == total
        # assignee distribution: the admin's assignment sits on a hidden item
        assignees = {a["assignee_id"] for a in r.data["distribution"]["assignees"] if a["assignee_id"]}
        labels = {item["label_name"] for item in r.data["distribution"]["labels"] if item["label_name"]}
        points = sum(a["total_estimates"] or 0 for a in r.data["estimate_distribution"]["assignees"])
        if who in ("guest", "guest2"):
            assert world.admin.id not in assignees
            assert "bug" not in labels
        else:
            assert world.admin.id in assignees
            assert "bug" in labels
        assert points == total_points
        # burndown: pending work is total minus the completed items the viewer can see
        chart = r.data["distribution"]["completion_chart"]
        assert chart[str(timezone.now().date())] == total - completed


@pytest.mark.contract
class TestCycleListCounts:
    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_counts(self, world, who, expected):
        total, completed, _, _, _ = expected
        data = row(world.c[who].get(url(world, "cycles/")), world.cycle)
        assert data["total_issues"] == total
        assert data["completed_issues"] == completed

    def test_assignee_ids_hide_assignees_of_hidden_items(self, world):
        guest_row = row(world.c["guest"].get(url(world, "cycles/")), world.cycle)
        assert {str(a) for a in guest_row["assignee_ids"]} == {str(world.guest.id)}
        member_row = row(world.c["member"].get(url(world, "cycles/")), world.cycle)
        assert {str(a) for a in member_row["assignee_ids"]} == {str(world.guest.id), str(world.admin.id)}

    def test_current_cycle_view_is_scoped_too(self, world):
        r = world.c["guest"].get(url(world, "cycles/"), {"cycle_view": "current"})
        assert row(r, world.cycle)["total_issues"] == 1

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        assert row(world.c["guest"].get(url(world, "cycles/")), world.cycle)["total_issues"] == 3

    def test_progress_snapshot_is_withheld_from_restricted_guests(self, world):
        # the snapshot was frozen over every item (including hidden ones), so it must not reach a restricted guest
        Cycle.objects.filter(id=world.cycle.id).update(progress_snapshot={"total_issues": 3, "completed_issues": 1})
        assert row(world.c["guest"].get(url(world, "cycles/")), world.cycle)["progress_snapshot"] == {}
        member_row = row(world.c["member"].get(url(world, "cycles/")), world.cycle)
        assert member_row["progress_snapshot"] == {"total_issues": 3, "completed_issues": 1}


@pytest.mark.contract
class TestCycleProgress:
    def progress(self, world, who):
        r = world.c[who].get(url(world, f"cycles/{world.cycle.id}/progress/"))
        assert r.status_code == status.HTTP_200_OK, r.data
        return r.data

    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_counts_and_estimates(self, world, who, expected):
        total, completed, backlog, total_points, completed_points = expected
        data = self.progress(world, who)
        assert data["total_issues"] == total
        assert data["completed_issues"] == completed
        assert data["backlog_issues"] == backlog
        assert data["total_estimate_points"] == total_points
        assert data["completed_estimate_points"] == completed_points

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        assert self.progress(world, "guest")["total_issues"] == 3

    def test_snapshot_counts_are_not_served_to_restricted_guests(self, world):
        Cycle.objects.filter(id=world.cycle.id).update(
            progress_snapshot={"total_issues": 3, "completed_issues": 1, "backlog_issues": 2}
        )
        # members keep today's behaviour (the frozen snapshot); a restricted guest gets live, scoped counts
        assert self.progress(world, "member")["total_issues"] == 3
        guest_data = self.progress(world, "guest")
        assert (guest_data["total_issues"], guest_data["completed_issues"]) == (1, 0)


@pytest.mark.contract
class TestCycleAnalytics:
    def analytics(self, world, who, kind):
        r = world.c[who].get(url(world, f"cycles/{world.cycle.id}/analytics/"), {"type": kind})
        assert r.status_code == status.HTTP_200_OK, r.data
        return r.data

    @pytest.mark.parametrize("who", ["guest", "guest2", "member", "admin"])
    def test_issue_distributions_and_burndown(self, world, who):
        data = self.analytics(world, who, "issues")
        assignees = {a["assignee_id"] for a in data["assignees"] if a["assignee_id"]}
        labels = {item["label_name"] for item in data["labels"] if item["label_name"]}
        chart_today = data["completion_chart"][str(timezone.now().date())]
        if who in ("guest", "guest2"):
            assert world.admin.id not in assignees
            assert "bug" not in labels
            assert chart_today == 1  # one visible item, none of it completed
        else:
            assert world.admin.id in assignees
            assert "bug" in labels
            assert chart_today == 2  # three items, one completed

    @pytest.mark.parametrize("who, total_points, pending_today", [("guest", 1, 1), ("member", 7, 3)])
    def test_point_distributions_and_burndown(self, world, who, total_points, pending_today):
        data = self.analytics(world, who, "points")
        assert sum(a["total_estimates"] or 0 for a in data["assignees"]) == total_points
        assert data["completion_chart"][str(timezone.now().date())] == pending_today
        labels = {item["label_name"] for item in data["labels"] if item["label_name"]}
        assert ("bug" in labels) == (who == "member")

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        data = self.analytics(world, "guest", "issues")
        assert world.admin.id in {a["assignee_id"] for a in data["assignees"] if a["assignee_id"]}
        assert data["completion_chart"][str(timezone.now().date())] == 2

    def test_snapshot_distribution_is_not_served_to_restricted_guests(self, world):
        snapshot = {"distribution": {"labels": [{"label_name": "leak"}], "assignees": [], "completion_chart": {}}}
        Cycle.objects.filter(id=world.cycle.id).update(progress_snapshot=snapshot)
        assert self.analytics(world, "member", "issues")["labels"] == [{"label_name": "leak"}]
        labels = {item["label_name"] for item in self.analytics(world, "guest", "issues")["labels"]}
        assert "leak" not in labels
