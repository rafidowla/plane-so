# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: guest-access PSR-119 — counts that span projects (or sit outside a project's own views) respect guest scope.

A "restricted" guest is a project Guest in a project with ``guest_view_all_features`` off; they may only see work
items they created, so no count they receive may include anyone else's. Companion to
``test_guest_module_cycle_counts_app.py`` (project-level module / cycle aggregates). Covers:

* the project list's ``intake_count`` (the sidebar Intake badge) -- must match what the Intake page lists
* the workspace-wide module and cycle lists
* the workspace ``project-stats`` endpoint and the ``user-stats`` profile endpoint

Fixture: project ``P1`` (restricted by default) holds three work items
  i_admin  (admin, completed state, assigned to admin)  -> hidden from guests
  i_guest  (guest, backlog)
  i_guest2 (guest2, backlog)                            -> hidden from ``guest``
and four intake requests (all pending): two by ``guest``, one by ``guest2``, one by ``admin``; plus one already
accepted request by ``guest`` (never counted). Module ``M`` / cycle ``C`` hold the three work items.
Project ``P2`` (``guest_view_all_features`` on, guest is a Guest of it) holds one admin-created item in module ``M2``
and cycle ``C2``, so the per-project scoping of the workspace-wide lists is exercised in one request.
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
    Intake,
    IntakeIssue,
    Issue,
    IssueAssignee,
    Module,
    ModuleIssue,
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
    guest2 = _user("guest2", workspace, 5)
    outsider = _user("outsider", workspace, 20)  # workspace admin who is not a member of any project

    p1 = Project.objects.create(name="One", identifier="ONE", workspace=workspace, created_by=admin)
    p2 = Project.objects.create(
        name="Two", identifier="TWO", workspace=workspace, created_by=admin, guest_view_all_features=True
    )
    for project, roster in (
        (p1, ((admin, 20), (member, 15), (guest, 5), (guest2, 5))),
        (p2, ((admin, 20), (guest, 5))),
    ):
        for user, role in roster:
            ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role, is_active=True)

    def state(project, name, group, default=False):
        return State.objects.create(name=name, group=group, default=default, project=project, workspace=workspace)

    backlog = state(p1, "Backlog", "backlog", default=True)
    done = state(p1, "Done", "completed")
    backlog2 = state(p2, "Backlog", "backlog", default=True)

    def issue(project, name, creator, st):
        item = Issue(name=name, project=project, workspace=workspace, state=st)
        item.save(created_by_id=creator.id)
        return item

    i_admin = issue(p1, "by admin", admin, done)
    Issue.objects.filter(id=i_admin.id).update(completed_at=timezone.now())
    i_guest = issue(p1, "by guest", guest, backlog)
    i_guest2 = issue(p1, "by guest2", guest2, backlog)
    i_p2 = issue(p2, "p2 by admin", admin, backlog2)
    IssueAssignee.objects.create(issue=i_admin, assignee=admin, project=p1, workspace=workspace)

    now = timezone.now()
    module = Module.objects.create(name="M", project=p1, workspace=workspace)
    module2 = Module.objects.create(name="M2", project=p2, workspace=workspace)
    cycle = Cycle.objects.create(
        name="C",
        project=p1,
        workspace=workspace,
        owned_by=admin,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )
    cycle2 = Cycle.objects.create(
        name="C2",
        project=p2,
        workspace=workspace,
        owned_by=admin,
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=1),
    )
    for item in (i_admin, i_guest, i_guest2):
        ModuleIssue.objects.create(module=module, issue=item, project=p1, workspace=workspace)
        CycleIssue.objects.create(cycle=cycle, issue=item, project=p1, workspace=workspace)
    ModuleIssue.objects.create(module=module2, issue=i_p2, project=p2, workspace=workspace)
    CycleIssue.objects.create(cycle=cycle2, issue=i_p2, project=p2, workspace=workspace)

    def intake_for(project, requests):
        intake = Intake.objects.create(name="Intake", project=project, workspace=workspace, is_default=True)
        for creator, status_code in requests:
            item = issue(project, f"request by {creator.username}", creator, backlog if project == p1 else backlog2)
            IntakeIssue.objects.create(
                intake=intake, issue=item, project=project, workspace=workspace, status=status_code
            )
            IntakeIssue.objects.filter(issue=item).update(created_by=creator)

    intake_for(p1, [(guest, -2), (guest, -2), (guest2, -2), (admin, -2), (guest, 1)])
    intake_for(p2, [(admin, -2), (guest, -2)])

    class World:
        pass

    w = World()
    w.ws, w.p1, w.p2 = workspace, p1, p2
    w.module, w.module2, w.cycle, w.cycle2 = module, module2, cycle, cycle2
    w.admin, w.member, w.guest, w.guest2, w.outsider = admin, member, guest, guest2, outsider
    w.c = {
        name: _client(u)
        for name, u in (
            ("admin", admin),
            ("member", member),
            ("guest", guest),
            ("guest2", guest2),
            ("outsider", outsider),
        )
    }
    return w


def ws_url(w, path):
    return f"/api/workspaces/{w.ws.slug}/{path}"


def by_id(response, obj):
    assert response.status_code == status.HTTP_200_OK, response.data
    rows = [r for r in response.data if str(r["id"]) == str(obj.id)]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.contract
class TestIntakeBadgeCount:
    """``intake_count`` on the project list drives the sidebar badge; the Intake page lists a guest's own only."""

    # who -> pending intake requests counted in P1 (the accepted one never counts)
    EXPECTED = [("guest", 2), ("guest2", 1), ("member", 4), ("admin", 4)]

    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_project_list(self, world, who, expected):
        assert by_id(world.c[who].get(ws_url(world, "projects/")), world.p1)["intake_count"] == expected

    @pytest.mark.parametrize("who, expected", EXPECTED)
    def test_badge_matches_what_the_intake_page_lists(self, world, who, expected):
        listed = world.c[who].get(ws_url(world, f"projects/{world.p1.id}/intake-issues/"))
        assert listed.status_code == status.HTTP_200_OK, listed.data
        assert len(listed.data["results"]) == expected

    def test_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        assert by_id(world.c["guest"].get(ws_url(world, "projects/")), world.p1)["intake_count"] == 4

    def test_scope_is_per_project(self, world):
        # P2 allows guests to see everything, so the same guest's badge there counts both pending requests
        data = world.c["guest"].get(ws_url(world, "projects/"))
        assert by_id(data, world.p1)["intake_count"] == 2
        assert by_id(data, world.p2)["intake_count"] == 2

    def test_a_removed_guest_membership_does_not_scope_anyone_else(self, world):
        ProjectMember.objects.filter(project=world.p1, member=world.guest2).update(is_active=False)
        assert by_id(world.c["member"].get(ws_url(world, "projects/")), world.p1)["intake_count"] == 4


@pytest.mark.contract
class TestWorkspaceModuleCycleLists:
    @pytest.mark.parametrize("who, p1_total", [("guest", 1), ("guest2", 1)])
    def test_modules_restricted_guest_counts_own_items_only(self, world, who, p1_total):
        r = world.c[who].get(ws_url(world, "modules/"))
        row = by_id(r, world.module)
        assert row["total_issues"] == p1_total
        assert row["completed_issues"] == 0
        assert row["backlog_issues"] == 1

    def test_modules_full_counts_for_member_and_admin(self, world):
        for who in ("member", "admin"):
            row = by_id(world.c[who].get(ws_url(world, "modules/")), world.module)
            assert (row["total_issues"], row["completed_issues"], row["backlog_issues"]) == (3, 1, 2)

    def test_modules_scope_is_per_project(self, world):
        # in the same response a guest sees the view-all project's module unscoped
        r = world.c["guest"].get(ws_url(world, "modules/"))
        assert by_id(r, world.module)["total_issues"] == 1
        assert by_id(r, world.module2)["total_issues"] == 1

    def test_modules_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        row = by_id(world.c["guest"].get(ws_url(world, "modules/")), world.module)
        assert (row["total_issues"], row["completed_issues"], row["backlog_issues"]) == (3, 1, 2)

    @pytest.mark.parametrize("who, p1_total", [("guest", 1), ("guest2", 1)])
    def test_cycles_restricted_guest_counts_own_items_only(self, world, who, p1_total):
        row = by_id(world.c[who].get(ws_url(world, "cycles/")), world.cycle)
        assert row["total_issues"] == p1_total
        assert row["completed_issues"] == 0
        assert row["backlog_issues"] == 1

    def test_cycles_full_counts_for_member_and_admin(self, world):
        for who in ("member", "admin"):
            row = by_id(world.c[who].get(ws_url(world, "cycles/")), world.cycle)
            assert (row["total_issues"], row["completed_issues"], row["backlog_issues"]) == (3, 1, 2)

    def test_cycles_scope_is_per_project(self, world):
        r = world.c["guest"].get(ws_url(world, "cycles/"))
        assert by_id(r, world.cycle)["total_issues"] == 1
        assert by_id(r, world.cycle2)["total_issues"] == 1

    def test_cycles_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        row = by_id(world.c["guest"].get(ws_url(world, "cycles/")), world.cycle)
        assert (row["total_issues"], row["completed_issues"]) == (3, 1)


@pytest.mark.contract
class TestWorkspaceStatCounts:
    def project_stats(self, world, who):
        r = world.c[who].get(ws_url(world, "project-stats/"), {"project_ids": str(world.p1.id)})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert len(r.data) == 1
        return r.data[0]

    # who -> (total_issues, completed_issues). Intake requests are work items too: P1 holds 3 work items + 5 requests;
    # ``guest`` created i_guest + 3 requests, ``guest2`` created i_guest2 + 1 request
    @pytest.mark.parametrize(
        "who, expected", [("guest", (4, 0)), ("guest2", (2, 0)), ("member", (8, 1)), ("admin", (8, 1))]
    )
    def test_project_stats(self, world, who, expected):
        row = self.project_stats(world, who)
        assert (row["total_issues"], row["completed_issues"]) == expected

    def test_project_stats_keep_full_counts_for_a_workspace_admin_outside_the_project(self, world):
        row = self.project_stats(world, "outsider")
        assert (row["total_issues"], row["completed_issues"]) == (8, 1)

    def test_project_stats_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        assert self.project_stats(world, "guest")["total_issues"] == 8

    def user_stats(self, world, who, target):
        r = world.c[who].get(ws_url(world, f"user-stats/{target.id}/"))
        assert r.status_code == status.HTTP_200_OK, r.data
        return r.data

    def test_user_stats_hide_other_peoples_items_from_a_restricted_guest(self, world):
        # The admin created two items in each project (a work item + an intake request) and is assigned only the
        # completed i_admin in P1. P1 hides all of that from the guest; P2 allows it, so only P2's two show.
        stats = self.user_stats(world, "guest", world.admin)
        assert stats["assigned_issues"] == 0
        assert stats["completed_issues"] == 0
        assert stats["created_issues"] == 2

    def test_user_stats_full_for_member(self, world):
        stats = self.user_stats(world, "member", world.admin)  # the member belongs to P1 only
        assert stats["assigned_issues"] == 1
        assert stats["completed_issues"] == 1
        assert stats["created_issues"] == 2

    def test_user_stats_guest_view_all_features_lifts_the_scope(self, world):
        Project.objects.filter(id=world.p1.id).update(guest_view_all_features=True)
        stats = self.user_stats(world, "guest", world.admin)
        assert (stats["assigned_issues"], stats["completed_issues"], stats["created_issues"]) == (1, 1, 4)
