# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Thread replies keep their `parent` (FORK: comment-threads #26).

Upstream's INFRA-1208 allowlist (COMMENT_WRITABLE_FIELDS) dropped `parent` on create, so every reply landed as a
top-level comment. The create view accepts `parent` again, but only when it names a comment on the same work item;
any other value is ignored and the comment is saved top-level (upstream drops system fields rather than rejecting).
"""

import uuid
from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, IssueComment, Project, ProjectMember, State


@pytest.fixture(autouse=True)
def _no_side_effects():
    patches = [
        mock.patch("plane.app.views.issue.comment.issue_activity"),
        mock.patch("plane.app.views.issue.comment.model_activity"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture
def world(db, workspace, create_user):
    admin = create_user
    project = Project.objects.create(name="RP", identifier="RP", workspace=workspace, created_by=admin, network=2)
    State.objects.create(name="Backlog", group="backlog", default=True, project=project, workspace=workspace)
    ProjectMember.objects.create(project=project, member=admin, role=20, is_active=True)
    issue_a = Issue.objects.create(name="A", project=project, workspace=workspace)
    issue_b = Issue.objects.create(name="B", project=project, workspace=workspace)
    parent = IssueComment.objects.create(
        issue=issue_a, project=project, workspace=workspace, actor=admin, comment_html="<p>parent</p>"
    )
    other = IssueComment.objects.create(
        issue=issue_b, project=project, workspace=workspace, actor=admin, comment_html="<p>other</p>"
    )
    client = APIClient()
    client.force_authenticate(user=admin)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue_a.id}/comments/"
    return client, url, issue_a, parent, other


@pytest.mark.contract
class TestCommentReplyParent:
    def test_reply_keeps_parent(self, world):
        client, url, issue_a, parent, _ = world
        r = client.post(url, {"comment_html": "<p>reply</p>", "parent": str(parent.id)}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        reply = IssueComment.objects.get(pk=r.data["id"])
        assert reply.parent_id == parent.id
        assert reply.issue_id == issue_a.id

    def test_top_level_comment_has_no_parent(self, world):
        client, url, _, _, _ = world
        r = client.post(url, {"comment_html": "<p>top</p>"}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert IssueComment.objects.get(pk=r.data["id"]).parent_id is None

    def test_parent_on_another_work_item_is_ignored(self, world):
        client, url, issue_a, _, other = world
        r = client.post(url, {"comment_html": "<p>x</p>", "parent": str(other.id)}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        reply = IssueComment.objects.get(pk=r.data["id"])
        assert reply.parent_id is None
        assert reply.issue_id == issue_a.id

    @pytest.mark.parametrize("bad", [str(uuid.uuid4()), "not-a-uuid"])
    def test_unknown_or_malformed_parent_is_ignored(self, world, bad):
        client, url, _, _, _ = world
        r = client.post(url, {"comment_html": "<p>y</p>", "parent": bad}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert IssueComment.objects.get(pk=r.data["id"]).parent_id is None
