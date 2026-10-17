# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project Guests cannot comment on archived work items (FORK: PSR-119).

Covers both the app API (``/api/workspaces/...``) and the public v1 API (``/api/v1/workspaces/...``, what the MCP
connector uses): create and edit of a comment is a 403 for a Guest when the work item is archived, a Guest can still
comment on a non-archived item they can see, and Members / Admins are unaffected.
"""

from unittest import mock

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Issue,
    IssueComment,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)

ARCHIVED_ERROR = "Guests cannot comment on archived work items"


@pytest.fixture(autouse=True)
def _no_side_effects():
    patches = [
        mock.patch("plane.app.views.issue.comment.issue_activity"),
        mock.patch("plane.app.views.issue.comment.model_activity"),
        mock.patch("plane.api.views.issue.issue_activity"),
        mock.patch("plane.api.views.issue.model_activity"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def _user(name, workspace, ws_role):
    user = User.objects.create(email=f"{name}@plane.so", username=f"{name}-user")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role)
    return user


def _session_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _token_client(user):
    token = f"token-{user.username}"
    APIToken.objects.create(user=user, label=token, token=token)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token)
    return client


def _issue(name, project, creator, archived=False):
    state = State.objects.filter(project=project, default=True).first()
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=creator, archived_at=timezone.now() if archived else None)
    issue.refresh_from_db()
    return issue


def _comment(issue, author):
    comment = IssueComment.objects.create(
        issue=issue, project=issue.project, workspace=issue.workspace, actor=author, comment_html="<p>hi</p>"
    )
    IssueComment.objects.filter(id=comment.id).update(created_by=author)
    return comment


@pytest.fixture
def world(db, workspace, create_user):
    admin = create_user
    member = _user("member", workspace, 15)
    guest = _user("guest", workspace, 5)
    guest2 = _user("guest2", workspace, 5)

    # guest_view_all_features=False: the guests see only the work items they created
    project = Project.objects.create(name="GA", identifier="GA", workspace=workspace, created_by=admin, network=0)
    State.objects.create(name="Backlog", group="backlog", default=True, project=project, workspace=workspace)
    for user, role in ((admin, 20), (member, 15), (guest, 5), (guest2, 5)):
        ProjectMember.objects.create(project=project, member=user, role=role, is_active=True)

    class World:
        pass

    w = World()
    w.ws, w.project = workspace, project
    w.admin, w.member, w.guest, w.guest2 = admin, member, guest, guest2
    w.archived_own = _issue("archived, by guest", project, guest, archived=True)
    w.live_own = _issue("live, by guest", project, guest)
    w.archived_admin = _issue("archived, by admin", project, admin, archived=True)
    w.live_admin = _issue("live, by admin", project, admin)
    return w


def _app_url(w, issue, comment=None):
    url = f"/api/workspaces/{w.ws.slug}/projects/{w.project.id}/issues/{issue.id}/comments/"
    return url + f"{comment.id}/" if comment else url


def _v1_url(w, issue, comment=None):
    url = f"/api/v1/workspaces/{w.ws.slug}/projects/{w.project.id}/issues/{issue.id}/comments/"
    return url + f"{comment.id}/" if comment else url


BODY = {"comment_html": "<p>hello</p>"}
EDIT = {"comment_html": "<p>edited</p>"}


@pytest.fixture(params=["app", "v1"])
def api(request):
    """The same assertions run against the app API and the v1 API."""
    if request.param == "app":
        return _session_client, _app_url
    return _token_client, _v1_url


@pytest.mark.contract
class TestGuestArchivedComments:
    def test_guest_cannot_comment_on_an_archived_item_of_their_own(self, world, api):
        client_for, url = api
        r = client_for(world.guest).post(url(world, world.archived_own), BODY, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, r.data
        assert r.data["error"] == ARCHIVED_ERROR
        assert not IssueComment.objects.filter(issue=world.archived_own).exists()

    def test_guest_can_comment_on_a_live_item_of_their_own(self, world, api):
        client_for, url = api
        r = client_for(world.guest).post(url(world, world.live_own), BODY, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert IssueComment.objects.filter(issue=world.live_own).count() == 1

    def test_guest_can_comment_on_a_live_item_they_can_see(self, world, api):
        client_for, url = api
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        r = client_for(world.guest2).post(url(world, world.live_admin), BODY, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data

    def test_guest_cannot_comment_on_an_archived_item_they_can_see(self, world, api):
        client_for, url = api
        Project.objects.filter(id=world.project.id).update(guest_view_all_features=True)
        r = client_for(world.guest2).post(url(world, world.archived_admin), BODY, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, r.data
        assert not IssueComment.objects.filter(issue=world.archived_admin).exists()

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_team_roles_can_comment_on_an_archived_item(self, world, api, who):
        client_for, url = api
        r = client_for(getattr(world, who)).post(url(world, world.archived_own), BODY, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert IssueComment.objects.filter(issue=world.archived_own).count() == 1

    def test_guest_cannot_edit_their_own_comment_once_the_item_is_archived(self, world, api):
        client_for, url = api
        comment = _comment(world.archived_own, world.guest)
        r = client_for(world.guest).patch(url(world, world.archived_own, comment), EDIT, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, r.data
        comment.refresh_from_db()
        assert comment.comment_html == "<p>hi</p>"

    def test_guest_can_edit_their_own_comment_on_a_live_item(self, world, api):
        client_for, url = api
        comment = _comment(world.live_own, world.guest)
        r = client_for(world.guest).patch(url(world, world.live_own, comment), EDIT, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        comment.refresh_from_db()
        assert comment.comment_html == "<p>edited</p>"

    def test_admin_can_edit_a_comment_on_an_archived_item(self, world, api):
        client_for, url = api
        comment = _comment(world.archived_own, world.guest)
        r = client_for(world.admin).patch(url(world, world.archived_own, comment), EDIT, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data

    def test_member_can_edit_their_own_comment_on_an_archived_item(self, world, api):
        client_for, url = api
        comment = _comment(world.archived_own, world.member)
        r = client_for(world.member).patch(url(world, world.archived_own, comment), EDIT, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data


@pytest.mark.contract
class TestAppPutRoute:
    def test_put_is_blocked_the_same_way(self, world):
        comment = _comment(world.archived_own, world.guest)
        r = _session_client(world.guest).put(_app_url(world, world.archived_own, comment), EDIT, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN, r.data
