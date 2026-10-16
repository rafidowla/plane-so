# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: put-permission — PUT on a work item must enforce the same permissions as PATCH.

The urls route ``"put": "update"`` on ``IssueViewSet``; before the fix that resolved to the default
``ModelViewSet.update`` (``IsAuthenticated`` only), so any signed-in user could overwrite any work item.
"""

from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, Workspace, WorkspaceMember

VIEWS = "plane.app.views.issue.base"


@pytest.fixture(autouse=True)
def _no_side_effects():
    patches = [
        mock.patch(f"{VIEWS}.issue_activity"),
        mock.patch(f"{VIEWS}.model_activity"),
        mock.patch(f"{VIEWS}.issue_description_version_task"),
        mock.patch(f"{VIEWS}.recent_visited_task"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def _user(name, workspace=None, ws_role=None):
    user = User.objects.create(email=f"{name}@plane.so", username=f"{name}-user")
    if workspace is not None:
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
    ws_only = _user("wsonly", workspace, 15)  # workspace member, not in the project
    stranger = _user("stranger")  # no workspace at all
    other_ws = Workspace.objects.create(name="Other", owner=stranger, slug="other-ws")
    WorkspaceMember.objects.create(workspace=other_ws, member=stranger, role=20)

    project = Project.objects.create(name="P", identifier="P", workspace=workspace, created_by=admin, network=2)
    for user, role in ((admin, 20), (member, 15), (guest, 5)):
        ProjectMember.objects.create(project=project, member=user, role=role, is_active=True)
    state = State.objects.create(name="Backlog", group="backlog", default=True, project=project, workspace=workspace)
    issue = Issue.objects.create(name="original", project=project, workspace=workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=admin)
    issue.refresh_from_db()

    class World:
        pass

    w = World()
    w.issue = issue
    w.url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    w.c = {
        n: _client(u)
        for n, u in (
            ("admin", admin),
            ("member", member),
            ("guest", guest),
            ("ws_only", ws_only),
            ("stranger", stranger),
        )
    }
    return w


@pytest.mark.contract
class TestIssuePutPermission:
    @pytest.mark.parametrize("who", ["guest", "ws_only", "stranger"])
    def test_put_is_refused_for_users_who_cannot_patch(self, world, who):
        r = world.c[who].put(world.url, {"name": "overwritten"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, (r.status_code, getattr(r, "data", None))
        world.issue.refresh_from_db()
        assert world.issue.name == "original"

    @pytest.mark.parametrize("who", ["admin", "member"])
    def test_put_still_works_for_project_members(self, world, who):
        r = world.c[who].put(world.url, {"name": f"renamed by {who}"}, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT, (r.status_code, getattr(r, "data", None))
        world.issue.refresh_from_db()
        assert world.issue.name == f"renamed by {who}"
