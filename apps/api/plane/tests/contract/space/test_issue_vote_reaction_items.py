# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: public board work items with no votes / reactions return empty lists.

``vote_items`` and ``reaction_items`` are ``ArrayAgg`` annotations. Since Django
5.0 an ``ArrayAgg`` over zero rows yields ``None``, which the Space app then
calls ``.filter`` on ("Cannot read properties of null (reading 'filter')") when
someone casts the first vote on an item.
"""

import pytest
from django.db.models import QuerySet
from rest_framework.test import APIClient

from plane.db.models import (
    DeployBoard,
    Issue,
    IssueReaction,
    IssueVote,
    Project,
    ProjectMember,
    State,
)


@pytest.fixture
def board(db, workspace, create_user):
    project = Project.objects.create(name="Vote Project", identifier="VOT", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20, is_active=True)
    state = State.objects.create(name="Todo", project=project, workspace=workspace, group="backlog", default=True)
    issue = Issue.objects.create(
        name="Vote Issue", workspace=workspace, project=project, state=state, created_by=create_user
    )
    deploy_board = DeployBoard.objects.create(
        entity_name="project",
        entity_identifier=project.id,
        project=project,
        workspace=workspace,
        is_reactions_enabled=True,
        is_votes_enabled=True,
    )
    return {"project": project, "issue": issue, "anchor": deploy_board.anchor, "user": create_user}


def _detail(board):
    return APIClient().get(f"/api/public/anchor/{board['anchor']}/issues/{board['issue'].id}/")


def _issue_dicts(payload):
    """Yield every dict in a (possibly grouped / paginated) payload that carries vote_items."""
    if isinstance(payload, dict):
        if "vote_items" in payload:
            yield payload
        for value in payload.values():
            yield from _issue_dicts(value)
    elif isinstance(payload, (list, tuple, QuerySet)):
        for value in payload:
            yield from _issue_dicts(value)


@pytest.mark.contract
class TestPublicIssueVoteReactionItems:
    @pytest.mark.django_db
    def test_detail_without_votes_or_reactions_returns_empty_lists(self, board):
        response = _detail(board)
        assert response.status_code == 200
        assert response.data["vote_items"] == []
        assert response.data["reaction_items"] == []

    @pytest.mark.django_db
    def test_detail_with_vote_and_reaction_still_lists_them(self, board):
        common = {"issue": board["issue"], "project": board["project"], "workspace": board["project"].workspace}
        IssueVote.objects.create(actor=board["user"], vote=1, **common)
        IssueReaction.objects.create(actor=board["user"], reaction="128077", **common)

        response = _detail(board)
        assert response.status_code == 200
        assert [item["vote"] for item in response.data["vote_items"]] == [1]
        assert [item["reaction"] for item in response.data["reaction_items"]] == ["128077"]

    @pytest.mark.django_db
    def test_list_without_votes_or_reactions_returns_empty_lists(self, board):
        response = APIClient().get(f"/api/public/anchor/{board['anchor']}/issues/")
        assert response.status_code == 200
        issues = list(_issue_dicts(response.data))
        assert issues, "expected the board's work item in the list payload"
        for issue in issues:
            assert issue["vote_items"] == []
            assert issue["reaction_items"] == []
