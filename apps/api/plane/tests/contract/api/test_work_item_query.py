# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for PQL filtering on the public work item API (FORK: PSR-85).

Covers ``?pql=`` / ``?filters=`` on the project list, the workspace-wide list
``workspaces/{slug}/work-items/`` and the count endpoint
``workspaces/{slug}/work-items/count/``, including visibility rules and
cross-workspace isolation.
"""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from rest_framework import status

from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

TARGET = date(2026, 3, 15)


def _states(project, ws):
    backlog = State.objects.create(name="Backlog", group="backlog", default=True, project=project, workspace=ws)
    started = State.objects.create(name="Doing", group="started", project=project, workspace=ws)
    return backlog, started


def _issue(name, project, state, creator, **kwargs):
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state, **kwargs)
    # BaseModel.save() takes created_by from the request user; pin it explicitly.
    Issue.objects.filter(id=issue.id).update(created_by=creator)
    issue.refresh_from_db()
    return issue


@pytest.fixture
def world(db, workspace, create_user):
    """Caller = ``create_user`` (workspace admin).

    test-workspace:
      ALP (caller admin):  i1 high/backlog labels L1+L2 assignee caller target_date
                           i2 urgent/started label L1;  i3 none/backlog
      BET (caller admin):  i4 high/started assignee u2
      GAM (caller NOT a member): i5 high
      GST (caller guest, guest_view_all_features=False): i6 (by caller, low), i7 (by u2, low)
    other-ws (caller is a member there too): OTHER project, i8 high with its own state/label
    """
    me = create_user
    u2 = User.objects.create(email="u2@plane.so", username="u2-user")
    WorkspaceMember.objects.create(workspace=workspace, member=u2, role=15)

    def project(name, identifier, ws, role=None, **kwargs):
        p = Project.objects.create(name=name, identifier=identifier, workspace=ws, created_by=me, **kwargs)
        if role is not None:
            ProjectMember.objects.create(project=p, member=me, role=role, is_active=True)
        ProjectMember.objects.create(project=p, member=u2, role=15, is_active=True)
        return p

    alp = project("Alpha", "ALP", workspace, role=20)
    bet = project("Beta", "BET", workspace, role=20)
    gam = project("Gamma", "GAM", workspace)
    gst = project("Guest", "GST", workspace, role=5)

    alp_backlog, alp_started = _states(alp, workspace)
    _, bet_started = _states(bet, workspace)
    gam_backlog, _ = _states(gam, workspace)
    gst_backlog, _ = _states(gst, workspace)

    l1 = Label.objects.create(name="bug", project=alp, workspace=workspace)
    l2 = Label.objects.create(name="ux", project=alp, workspace=workspace)

    i1 = _issue("one capex", alp, alp_backlog, me, priority="high", target_date=TARGET)
    i2 = _issue("two", alp, alp_started, me, priority="urgent")
    i3 = _issue("three", alp, alp_backlog, me, priority="none")
    i4 = _issue("four", bet, bet_started, me, priority="high")
    i5 = _issue("five hidden", gam, gam_backlog, me, priority="high")
    i6 = _issue("six guest own", gst, gst_backlog, me, priority="low")
    i7 = _issue("seven guest other", gst, gst_backlog, u2, priority="low")

    for issue, label in ((i1, l1), (i1, l2), (i2, l1)):
        IssueLabel.objects.create(issue=issue, label=label, project=issue.project, workspace=workspace)
    IssueAssignee.objects.create(issue=i1, assignee=me, project=alp, workspace=workspace)
    IssueAssignee.objects.create(issue=i4, assignee=u2, project=bet, workspace=workspace)

    other_owner = User.objects.create(email="other@plane.so", username="other-owner")
    other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=other_owner)
    WorkspaceMember.objects.create(workspace=other_ws, member=other_owner, role=20)
    WorkspaceMember.objects.create(workspace=other_ws, member=me, role=20)
    other = project("Other", "OTHER", other_ws, role=20)
    other_backlog, _ = _states(other, other_ws)
    other_label = Label.objects.create(name="bug", project=other, workspace=other_ws)
    i8 = _issue("eight other ws capex", other, other_backlog, me, priority="high")
    IssueLabel.objects.create(issue=i8, label=other_label, project=other, workspace=other_ws)

    return SimpleNamespace(
        ws=workspace,
        me=me,
        u2=u2,
        alp=alp,
        bet=bet,
        gam=gam,
        gst=gst,
        l1=l1,
        l2=l2,
        other=other,
        other_ws=other_ws,
        other_state=other_backlog,
        other_label=other_label,
        **{f"i{n}": issue for n, issue in enumerate((i1, i2, i3, i4, i5, i6, i7, i8), start=1)},
    )


def project_url(w, project=None):
    return f"/api/v1/workspaces/{w.ws.slug}/projects/{(project or w.alp).id}/work-items/"


def ws_url(slug="test-workspace"):
    return f"/api/v1/workspaces/{slug}/work-items/"


def count_url(slug="test-workspace"):
    return f"/api/v1/workspaces/{slug}/work-items/count/"


def names(response):
    return {item["name"] for item in response.data["results"]}


VISIBLE = {"one capex", "two", "three", "four", "six guest own"}


@pytest.mark.contract
class TestProjectListPQL:
    def test_no_pql_unchanged(self, api_key_client, world):
        r = api_key_client.get(project_url(world))
        assert r.status_code == status.HTTP_200_OK
        assert names(r) == {"one capex", "two", "three"}
        assert r.data["total_count"] == 3

    def test_pql_filters_results_and_total(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": 'priority = "high"'})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert names(r) == {"one capex"}
        assert r.data["total_count"] == 1

    def test_pql_m2m_no_duplicates(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": f'label IN ("{world.l1.id}", "{world.l2.id}")'})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert sorted(item["name"] for item in r.data["results"]) == ["one capex", "two"]
        assert r.data["total_count"] == 2

    def test_filters_json(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"filters": json.dumps({"priority__in": ["high", "urgent"]})})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert names(r) == {"one capex", "two"}
        assert r.data["total_count"] == 2

    def test_pql_and_filters_combine(self, api_key_client, world):
        r = api_key_client.get(
            project_url(world),
            {"pql": 'priority IN ("high", "urgent")', "filters": json.dumps({"priority": "urgent"})},
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        assert names(r) == {"two"}
        assert r.data["total_count"] == 1

    def test_bad_pql_400(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": "priority =="})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert set(r.data) == {"pql"} and "PQL error" in r.data["pql"]

    def test_unknown_field_400(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": 'nonsense = "x"'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "pql" in r.data

    def test_bad_filters_json_400(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"filters": "{not json"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert set(r.data) == {"filters"}

    def test_bad_filters_key_400(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"filters": json.dumps({"project__workspace__owner__email": "x"})})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert set(r.data) == {"filters"}

    def test_filters_not_object_400(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"filters": "[1, 2]"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert set(r.data) == {"filters"}

    def test_order_by_with_pql(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": 'priority IN ("high", "urgent")', "order_by": "priority"})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert [item["name"] for item in r.data["results"]] == ["two", "one capex"]
        r = api_key_client.get(project_url(world), {"pql": 'priority IN ("high", "urgent")', "order_by": "-priority"})
        assert [item["name"] for item in r.data["results"]] == ["one capex", "two"]

    def test_pagination_total_with_pql(self, api_key_client, world):
        r = api_key_client.get(project_url(world), {"pql": 'priority != "urgent"', "per_page": 1})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert len(r.data["results"]) == 1
        assert r.data["total_count"] == 2


@pytest.mark.contract
class TestWorkspaceList:
    def test_spans_member_projects_with_visibility(self, api_key_client, world):
        r = api_key_client.get(ws_url())
        assert r.status_code == status.HTTP_200_OK, r.data
        # excludes GAM (not a member), i7 (guest rule) and the other workspace
        assert names(r) == VISIBLE
        assert r.data["total_count"] == len(VISIBLE)

    def test_guest_view_all_features(self, api_key_client, world):
        Project.objects.filter(id=world.gst.id).update(guest_view_all_features=True)
        r = api_key_client.get(ws_url())
        assert names(r) == VISIBLE | {"seven guest other"}
        assert r.data["total_count"] == len(VISIBLE) + 1

    def test_inactive_membership_excluded(self, api_key_client, world):
        ProjectMember.objects.filter(project=world.bet, member=world.me).update(is_active=False)
        r = api_key_client.get(ws_url())
        assert names(r) == VISIBLE - {"four"}

    def test_archived_project_excluded(self, api_key_client, world):
        from django.utils import timezone

        Project.objects.filter(id=world.bet.id).update(archived_at=timezone.now())
        r = api_key_client.get(ws_url())
        assert names(r) == VISIBLE - {"four"}

    def test_other_workspace_isolated(self, api_key_client, world):
        r = api_key_client.get(ws_url("other-ws"))
        assert r.status_code == status.HTTP_200_OK, r.data
        assert names(r) == {"eight other ws capex"}

    def test_non_member_workspace_forbidden(self, api_key_client, world):
        WorkspaceMember.objects.filter(workspace=world.other_ws, member=world.me).update(is_active=False)
        r = api_key_client.get(ws_url("other-ws"))
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_pql(self, api_key_client, world):
        r = api_key_client.get(ws_url(), {"pql": 'priority = "high"'})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert names(r) == {"one capex", "four"}
        assert r.data["total_count"] == 2

    def test_pql_text_search(self, api_key_client, world):
        r = api_key_client.get(ws_url(), {"pql": 'title ~ "capex"'})
        assert names(r) == {"one capex"}

    def test_bad_pql_400(self, api_key_client, world):
        r = api_key_client.get(ws_url(), {"pql": "("})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert set(r.data) == {"pql"}

    def test_order_by(self, api_key_client, world):
        r = api_key_client.get(ws_url(), {"pql": 'priority IN ("high", "urgent")', "order_by": "priority"})
        assert [item["name"] for item in r.data["results"]][0] == "two"

    def test_post_not_allowed(self, api_key_client, world):
        r = api_key_client.post(ws_url(), {"name": "x"}, format="json")
        assert r.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.contract
class TestWorkspaceCount:
    def test_total_no_group(self, api_key_client, world):
        r = api_key_client.get(count_url())
        assert r.status_code == status.HTTP_200_OK, r.data
        assert r.data == {
            "grouped_by": None,
            "sub_grouped_by": None,
            "total_count": len(VISIBLE),
            "grouped_counts": {},
        }

    def test_group_by_priority(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "priority"})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert r.data["grouped_by"] == "priority"
        assert r.data["grouped_counts"] == {
            "high": {"count": 2},
            "urgent": {"count": 1},
            "none": {"count": 1},
            "low": {"count": 1},
        }

    def test_group_by_state_group(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "state__group"})
        assert r.data["grouped_counts"] == {"backlog": {"count": 3}, "started": {"count": 2}}

    def test_group_by_labels_multi_label_and_none(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "labels__id"})
        assert r.data["total_count"] == 5
        assert r.data["grouped_counts"] == {
            str(world.l1.id): {"count": 2},
            str(world.l2.id): {"count": 1},
            "None": {"count": 3},
        }

    def test_soft_deleted_label_row_not_counted(self, api_key_client, world):
        from django.utils import timezone

        IssueLabel.all_objects.filter(issue=world.i1, label=world.l2).update(deleted_at=timezone.now())
        r = api_key_client.get(count_url(), {"group_by": "labels__id"})
        assert str(world.l2.id) not in r.data["grouped_counts"]

    def test_group_by_assignees(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "assignees__id"})
        assert r.data["grouped_counts"] == {
            str(world.me.id): {"count": 1},
            str(world.u2.id): {"count": 1},
            "None": {"count": 3},
        }

    def test_group_by_project_and_target_date(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "project_id"})
        assert r.data["grouped_counts"] == {
            str(world.alp.id): {"count": 3},
            str(world.bet.id): {"count": 1},
            str(world.gst.id): {"count": 1},
        }
        r = api_key_client.get(count_url(), {"group_by": "target_date"})
        assert r.data["grouped_counts"] == {"None": {"count": 4}, TARGET.isoformat(): {"count": 1}}

    def test_sub_group_by(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "state__group", "sub_group_by": "labels__id"})
        assert r.status_code == status.HTTP_200_OK, r.data
        assert r.data["sub_grouped_by"] == "labels__id"
        l1, l2 = str(world.l1.id), str(world.l2.id)
        assert r.data["grouped_counts"] == {
            "backlog": {"count": 3, "sub_grouped_counts": {"None": {"count": 2}, l1: {"count": 1}, l2: {"count": 1}}},
            "started": {"count": 2, "sub_grouped_counts": {l1: {"count": 1}, "None": {"count": 1}}},
        }

    @pytest.mark.parametrize("value", ["type_id", "release_work_items__release_id", "milestone_id", "name"])
    def test_unsupported_group_by(self, api_key_client, world, value):
        r = api_key_client.get(count_url(), {"group_by": value})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert r.data["error"].startswith(f"group_by '{value}' is not supported on this Plane edition; supported:")

    def test_unsupported_sub_group_by(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "priority", "sub_group_by": "type_id"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "sub_group_by 'type_id'" in r.data["error"]

    def test_sub_group_without_group_400(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"sub_group_by": "priority"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in r.data

    def test_sub_group_same_as_group_400(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"group_by": "priority", "sub_group_by": "priority"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in r.data

    def test_visibility_matches_list(self, api_key_client, world):
        Project.objects.filter(id=world.gst.id).update(guest_view_all_features=True)
        r = api_key_client.get(count_url())
        assert r.data["total_count"] == len(VISIBLE) + 1

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"pql": 'priority = "high"'},
            {"pql": 'stateGroup = "backlog" OR priority = "urgent"'},
            {"filters": json.dumps({"priority__in": ["high", "low"]})},
        ],
    )
    def test_totals_match_list(self, api_key_client, world, params):
        listed = api_key_client.get(ws_url(), params)
        counted = api_key_client.get(count_url(), params)
        assert listed.status_code == counted.status_code == status.HTTP_200_OK, (listed.data, counted.data)
        assert counted.data["total_count"] == listed.data["total_count"]

    def test_bad_pql_and_filters_400(self, api_key_client, world):
        r = api_key_client.get(count_url(), {"pql": 'priority = "nope"'})
        assert r.status_code == status.HTTP_400_BAD_REQUEST and set(r.data) == {"pql"}
        r = api_key_client.get(count_url(), {"filters": "nope"})
        assert r.status_code == status.HTTP_400_BAD_REQUEST and set(r.data) == {"filters"}


@pytest.mark.contract
class TestCrossWorkspaceSecurity:
    """PQL naming another workspace's objects must never return that workspace's issues."""

    def queries(self, w):
        return [
            f'state = "{w.other_state.id}"',
            f'label = "{w.other_label.id}"',
            f'project = "{w.other.id}"',
            'id = "OTHER-1"',
            f'id = "{w.i8.id}"',
            'id IN ("OTHER-1")',
            'childOf("OTHER-1")',
        ]

    def _assert_no_leak(self, response):
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST), response.data
        if response.status_code == status.HTTP_200_OK:
            if "results" in response.data:
                assert "eight other ws capex" not in names(response)
                assert response.data["total_count"] == 0
            else:
                assert response.data["total_count"] == 0

    def test_workspace_list(self, api_key_client, world):
        for pql in self.queries(world):
            self._assert_no_leak(api_key_client.get(ws_url(), {"pql": pql}))

    def test_project_list(self, api_key_client, world):
        for pql in self.queries(world):
            self._assert_no_leak(api_key_client.get(project_url(world), {"pql": pql}))

    def test_count(self, api_key_client, world):
        for pql in self.queries(world):
            self._assert_no_leak(api_key_client.get(count_url(), {"pql": pql}))

    def test_filters_with_foreign_ids(self, api_key_client, world):
        filters = json.dumps({"state_id": str(world.other_state.id)})
        self._assert_no_leak(api_key_client.get(ws_url(), {"filters": filters}))
        filters = json.dumps({"labels__id__in": [str(world.other_label.id)]})
        self._assert_no_leak(api_key_client.get(count_url(), {"filters": filters}))
