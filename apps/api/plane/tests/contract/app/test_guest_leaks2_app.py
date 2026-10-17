# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: guest-access PSR-119 — second round of guest data leaks.

A "restricted" guest is a project Guest in a project with ``guest_view_all_features`` off; they may only see work
items they created. Covers, per product decision:

1. analytics are hidden from guests entirely (workspace and project endpoints answer 403); ``project-stats`` (the
   project list's counters) is not an analytics page and keeps working
2. the home dashboard counts only what the guest can open, and nothing in projects they no longer belong to
3. ``user-stats`` cycle names come only from work items the guest can open, in projects they still belong to
4. ``sub_issues_count`` counts only the sub-items the guest created (restricted projects only)
5. ``next_work_item_sequence`` on the project list is null for a restricted guest
(6, the intake detail panel, is web-only.)
7. the profile ``user-issues`` list and ``user-activity`` feed drop work items the guest cannot open

Fixture: ``P1`` (restricted) and ``P2`` (``guest_view_all_features`` on) have ``guest`` as Guest; ``P3`` (view-all) has
``guest`` as an *inactive* member. Work items:
  i_admin  P1, admin, completed, assigned to admin and guest         -> hidden from ``guest``
  i_late   P1, admin, backlog, overdue, due this week, assigned to guest, member and admin -> hidden from ``guest``
  i_guest  P1, guest, backlog, assigned to guest and admin           -> visible
  i_p2     P2, admin, backlog, assigned to guest and admin           -> visible (view-all project)
  i_p3     P3, admin, backlog, assigned to guest, member and admin   -> guest no longer belongs to P3
Cycles: present ``C`` (P1, i_admin), ``C2`` (P2, i_p2), ``C3`` (P3, i_p3); future ``F`` (P1, i_late), ``F2`` (P2, i_p2).
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
    Issue,
    IssueActivity,
    IssueAssignee,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)


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
    wsguest = _user("wsguest", workspace, 5)  # workspace Guest who is a Guest of no project at all

    p1 = Project.objects.create(name="One", identifier="ONE", workspace=workspace, created_by=admin)
    p2 = Project.objects.create(
        name="Two", identifier="TWO", workspace=workspace, created_by=admin, guest_view_all_features=True
    )
    p3 = Project.objects.create(
        name="Three", identifier="THR", workspace=workspace, created_by=admin, guest_view_all_features=True
    )
    roster = {
        p1: ((admin, 20, True), (member, 15, True), (guest, 5, True)),
        p2: ((admin, 20, True), (guest, 5, True)),
        p3: ((admin, 20, True), (guest, 5, False)),  # the guest was removed from P3
    }
    for project, members in roster.items():
        for user, role, active in members:
            ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role, is_active=active)

    def state(project, name, group, default=False):
        return State.objects.create(name=name, group=group, default=default, project=project, workspace=workspace)

    backlogs = {p: state(p, "Backlog", "backlog", default=True) for p in (p1, p2, p3)}
    done = state(p1, "Done", "completed")

    def issue(project, name, creator, st=None, **extra):
        item = Issue(name=name, project=project, workspace=workspace, state=st or backlogs[project], **extra)
        item.save(created_by_id=creator.id)
        return item

    def assign(item, *users):
        for user in users:
            IssueAssignee.objects.create(issue=item, assignee=user, project=item.project, workspace=workspace)

    now = timezone.now()
    i_admin = issue(p1, "by admin", admin, done)
    Issue.objects.filter(id=i_admin.id).update(completed_at=now)
    i_late = issue(p1, "overdue by admin", admin, target_date=(now - timedelta(days=1)).date())
    i_guest = issue(p1, "by guest", guest)
    i_p2 = issue(p2, "p2 by admin", admin)
    i_p3 = issue(p3, "p3 by admin", admin)
    assign(i_admin, admin, guest)
    assign(i_late, guest, member, admin)
    assign(i_guest, guest, admin)
    assign(i_p2, guest, admin)
    assign(i_p3, guest, member, admin)

    def cycle(project, name, item, start, end):
        c = Cycle.objects.create(
            name=name, project=project, workspace=workspace, owned_by=admin, start_date=start, end_date=end
        )
        CycleIssue.objects.create(cycle=c, issue=item, project=project, workspace=workspace)
        return c

    day = timedelta(days=1)
    present = (now - day, now + day)
    future = (now + 2 * day, now + 3 * day)
    cycle(p1, "C", i_admin, *present)
    cycle(p2, "C2", i_p2, *present)
    cycle(p3, "C3", i_p3, *present)
    cycle(p1, "F", i_late, *future)
    cycle(p2, "F2", i_p2, *future)

    # sub-items: i_guest (guest's own parent in P1) and a guest-created parent in view-all P2
    sub_by_guest = issue(p1, "sub by guest", guest, parent=i_guest)
    sub_by_admin = issue(p1, "sub by admin", admin, parent=i_guest)
    p2_parent = issue(p2, "p2 parent by guest", guest)
    issue(p2, "p2 sub by admin", admin, parent=p2_parent)
    issue(p2, "p2 sub by guest", guest, parent=p2_parent)

    class World:
        pass

    w = World()
    w.ws, w.p1, w.p2, w.p3 = workspace, p1, p2, p3
    w.admin, w.member, w.guest, w.wsguest = admin, member, guest, wsguest
    w.i_admin, w.i_late, w.i_guest, w.i_p2, w.i_p3 = i_admin, i_late, i_guest, i_p2, i_p3
    w.sub_by_guest, w.sub_by_admin, w.p2_parent = sub_by_guest, sub_by_admin, p2_parent
    w.c = {
        name: _client(u) for name, u in (("admin", admin), ("member", member), ("guest", guest), ("wsguest", wsguest))
    }
    return w


def ws_url(w, path):
    return f"/api/workspaces/{w.ws.slug}/{path}"


# --- 1. analytics -----------------------------------------------------------------------------------------------


@pytest.mark.contract
class TestAnalyticsHiddenFromGuests:
    def test_workspace_default_analytics_403_for_a_workspace_guest(self, world):
        assert world.c["guest"].get(ws_url(world, "default-analytics/")).status_code == status.HTTP_403_FORBIDDEN
        assert world.c["wsguest"].get(ws_url(world, "default-analytics/")).status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_workspace_default_analytics_still_works_for_members_and_admins(self, world, who):
        assert world.c[who].get(ws_url(world, "default-analytics/")).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("project", ["p1", "p2"])
    def test_project_analytics_charts_403_for_a_project_guest(self, world, project):
        # both a restricted and a view-all project: analytics are hidden from guests either way
        url = ws_url(world, f"projects/{getattr(world, project).id}/advance-analytics-charts/")
        assert world.c["guest"].get(url, {"type": "work-items"}).status_code == status.HTTP_403_FORBIDDEN

    def test_project_analytics_charts_still_work_for_members_and_admins(self, world):
        url = ws_url(world, f"projects/{world.p1.id}/advance-analytics-charts/")
        assert world.c["member"].get(url, {"type": "work-items"}).status_code == status.HTTP_200_OK
        assert world.c["admin"].get(url, {"type": "work-items"}).status_code == status.HTTP_200_OK

    def test_project_stats_keeps_working_for_guests(self, world):
        # the project list's counters are not an analytics page
        response = world.c["guest"].get(ws_url(world, "project-stats/"), {"project_ids": str(world.p1.id)})
        assert response.status_code == status.HTTP_200_OK


# --- 2. home dashboard ------------------------------------------------------------------------------------------


@pytest.mark.contract
class TestHomeDashboard:
    def dashboard(self, world, who):
        response = world.c[who].get(f"/api/users/me/workspaces/{world.ws.slug}/dashboard/")
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data

    def test_restricted_guest_counts_only_items_they_can_open(self, world):
        data = self.dashboard(world, "guest")
        # assigned: i_guest (own, P1) + i_p2 (view-all P2). Not i_admin / i_late (hidden in P1), nor i_p3 (left P3)
        assert data["assigned_issues_count"] == 2
        assert data["pending_issues_count"] == 2
        assert data["completed_issues_count"] == 0
        assert data["issues_due_week_count"] == 0
        assert sum(row["state_count"] for row in data["state_distribution"]) == 2
        assert list(data["overdue_issues"]) == []
        assert list(data["upcoming_issues"]) == []

    def test_restricted_guest_does_not_see_hidden_items_in_the_overdue_list(self, world):
        ids = {str(row["id"]) for row in self.dashboard(world, "guest")["overdue_issues"]}
        assert str(world.i_late.id) not in ids

    def test_view_all_project_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        data = self.dashboard(world, "guest")
        # i_admin, i_late, i_guest, i_p2 -- still not i_p3 (the guest was removed from P3)
        assert data["assigned_issues_count"] == 4
        assert data["completed_issues_count"] == 1
        assert [str(row["id"]) for row in data["overdue_issues"]] == [str(world.i_late.id)]

    def test_member_counts_are_unchanged(self, world):
        data = self.dashboard(world, "member")
        assert data["assigned_issues_count"] == 2  # i_late, i_p3 (the member is not in P3; members keep today's rule)
        assert [str(row["id"]) for row in data["overdue_issues"]] == [str(world.i_late.id)]

    def test_admin_counts_are_unchanged(self, world):
        data = self.dashboard(world, "admin")
        assert data["assigned_issues_count"] == 5
        assert data["completed_issues_count"] == 1


# --- 3. cycle names in user-stats ------------------------------------------------------------------------------


@pytest.mark.contract
class TestUserStatsCycleNames:
    def names(self, world, who):
        response = world.c[who].get(ws_url(world, f"user-stats/{world.admin.id}/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        present = sorted(row["cycle__name"] for row in response.data["present_cycles"])
        upcoming = sorted(row["cycle__name"] for row in response.data["upcoming_cycles"])
        return present, upcoming

    def test_restricted_guest_only_gets_cycles_of_items_they_can_open(self, world):
        # C holds only the hidden i_admin and F only the hidden i_late; C3 is in a project the guest left
        assert self.names(world, "guest") == (["C2"], ["F2"])

    def test_view_all_project_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        assert self.names(world, "guest") == (["C", "C2"], ["F", "F2"])

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_members_and_admins_are_unchanged(self, world, who):
        assert self.names(world, who) == (["C", "C2", "C3"], ["F", "F2"])


# --- 4. sub-item counts -----------------------------------------------------------------------------------------


@pytest.mark.contract
class TestSubItemCounts:
    def detail(self, world, who, item):
        response = world.c[who].get(ws_url(world, f"projects/{item.project_id}/issues/{item.id}/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data

    def test_restricted_guest_counts_only_their_own_sub_items(self, world):
        assert self.detail(world, "guest", world.i_guest)["sub_issues_count"] == 1

    def test_restricted_guest_list_counts_only_their_own_sub_items(self, world):
        response = world.c["guest"].get(ws_url(world, f"projects/{world.p1.id}/issues/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        rows = {str(row["id"]): row for row in response.data["results"]}
        assert rows[str(world.i_guest.id)]["sub_issues_count"] == 1

    def test_member_counts_all_sub_items(self, world):
        assert self.detail(world, "member", world.i_guest)["sub_issues_count"] == 2

    def test_view_all_project_counts_all_sub_items_for_a_guest(self, world):
        assert self.detail(world, "guest", world.p2_parent)["sub_issues_count"] == 2

    def test_view_all_toggle_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        assert self.detail(world, "guest", world.i_guest)["sub_issues_count"] == 2


# --- 5. next work item sequence ---------------------------------------------------------------------------------


@pytest.mark.contract
class TestNextWorkItemSequence:
    def sequence(self, world, who, project):
        response = world.c[who].get(ws_url(world, f"projects/{project.id}/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data["next_work_item_sequence"]

    def test_restricted_guest_gets_none(self, world):
        assert self.sequence(world, "guest", world.p1) is None

    def test_guest_of_a_view_all_project_still_gets_the_number(self, world):
        assert self.sequence(world, "guest", world.p2) is not None

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_members_and_admins_still_get_the_number(self, world, who):
        assert self.sequence(world, who, world.p1) == 6  # i_admin, i_late, i_guest and two sub-items -> next is 6


# --- 7. profile work items and activity -------------------------------------------------------------------------


def _ids(data):
    rows = data["results"] if isinstance(data, dict) else data
    if isinstance(rows, dict):  # grouped
        rows = [row for group in rows.values() for row in group["results"]]
    return {str(row["id"]) for row in rows}


@pytest.mark.contract
class TestProfileIssuesAndActivity:
    def issues(self, world, who, of):
        response = world.c[who].get(ws_url(world, f"user-issues/{of.id}/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        return _ids(response.data)

    def activity(self, world, who, of):
        for item in (world.i_admin, world.i_late, world.i_guest, world.i_p2):
            IssueActivity.objects.create(
                issue=item, project=item.project, workspace=world.ws, actor=of, verb="updated", field="name"
            )
        response = world.c[who].get(ws_url(world, f"user-activity/{of.id}/"))
        assert response.status_code == status.HTTP_200_OK, response.data
        return {str(row["issue"]) for row in response.data["results"]}

    @pytest.mark.parametrize("of", ["admin", "guest"])
    def test_restricted_guest_lists_only_items_they_can_open(self, world, of):
        found = self.issues(world, "guest", getattr(world, of))
        assert str(world.i_admin.id) not in found
        assert str(world.i_late.id) not in found
        assert str(world.i_guest.id) in found
        assert str(world.i_p2.id) in found  # view-all project

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_members_and_admins_still_list_everything(self, world, who):
        found = self.issues(world, who, world.admin)
        assert {str(world.i_admin.id), str(world.i_late.id), str(world.i_guest.id)} <= found

    def test_restricted_guest_sees_no_activity_on_hidden_items(self, world):
        found = self.activity(world, "guest", world.admin)
        assert found == {str(world.i_guest.id), str(world.i_p2.id)}

    def test_member_sees_all_activity(self, world):
        found = self.activity(world, "member", world.admin)
        assert {str(world.i_admin.id), str(world.i_late.id), str(world.i_guest.id)} <= found

    def test_view_all_toggle_lifts_the_activity_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        found = self.activity(world, "guest", world.admin)
        assert {str(world.i_admin.id), str(world.i_late.id), str(world.i_guest.id), str(world.i_p2.id)} <= found
