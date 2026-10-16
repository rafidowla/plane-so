# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: put-permission — routed HTTP verbs must not fall through to unguarded ModelViewSet defaults.

Several app urls map ``put``/``patch``/``delete``/``get`` onto viewset actions the class never overrides, so
DRF's ``ModelViewSet`` implementation ran with only ``IsAuthenticated`` and a queryset scoped by URL slug /
project alone. Each test below drives the verb an attacker would use (and the verb the web client uses for the
legitimate case) and checks the row is untouched on refusal.

Also covers the ``creator=True`` shortcut in ``allow_permission``: a creator removed from the project must lose
their edit/delete rights there.
"""

import uuid
from unittest import mock

import pytest
from celery.app.task import Task
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    DeployBoard,
    Intake,
    Issue,
    IssueComment,
    IssueView,
    Module,
    Notification,
    Project,
    ProjectMember,
    ProjectMemberInvite,
    State,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberInvite,
)


@pytest.fixture(autouse=True)
def _no_celery():
    with mock.patch.object(Task, "delay"), mock.patch.object(Task, "apply_async"):
        yield


def _user(name, workspace=None, ws_role=None, ws_active=True):
    user = User.objects.create(email=f"{name}@plane.so", username=f"{name}-user")
    if workspace is not None:
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role, is_active=ws_active)
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _set_creator(obj, user):
    type(obj).objects.filter(id=obj.id).update(created_by=user)
    obj.refresh_from_db()


class World:
    pass


@pytest.fixture
def world(db, workspace, create_user):
    w = World()
    w.ws = workspace
    w.admin = create_user
    w.member = _user("member", workspace, 15)
    w.guest = _user("guest", workspace, 5)
    w.ws_only = _user("wsonly", workspace, 15)  # workspace member, not in the project
    w.ex_member = _user("exmember", workspace, 15)  # removed from the project, still in the workspace
    w.stranger = _user("stranger")  # admin of a different workspace
    w.other_ws = Workspace.objects.create(name="Other", owner=w.stranger, slug="other-ws")
    WorkspaceMember.objects.create(workspace=w.other_ws, member=w.stranger, role=20)

    w.project = Project.objects.create(name="P", identifier="P", workspace=workspace, network=0)
    for user, role, active in (
        (w.admin, 20, True),
        (w.member, 15, True),
        (w.guest, 5, True),
        (w.ex_member, 15, False),
    ):
        ProjectMember.objects.create(project=w.project, member=user, role=role, is_active=active)
    w.state = State.objects.create(
        name="Backlog", group="backlog", default=True, project=w.project, workspace=workspace
    )
    w.issue = Issue.objects.create(name="original", project=w.project, workspace=workspace, state=w.state)
    _set_creator(w.issue, w.admin)

    w.base = f"/api/workspaces/{workspace.slug}"
    w.pbase = f"{w.base}/projects/{w.project.id}"
    w.c = {n: _client(getattr(w, n)) for n in ("admin", "member", "guest", "ws_only", "ex_member", "stranger")}
    return w


def _assert_refused(r):
    assert r.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND), (
        r.status_code,
        getattr(r, "data", None),
    )


# --------------------------------------------------------------------------------------------------- project
@pytest.mark.contract
class TestProjectPut:
    def _url(self, w):
        return f"{w.pbase}/"

    @pytest.mark.parametrize("who", ["guest", "ws_only", "stranger"])
    def test_put_refused_and_project_unchanged(self, world, who):
        payload = {
            "name": "Pwned",
            "identifier": "PWN",
            "network": 2,
            "guest_view_all_features": True,
        }
        if who == "stranger":
            payload["workspace"] = str(world.other_ws.id)
        r = world.c[who].put(self._url(world), payload, format="json")
        _assert_refused(r)
        world.project.refresh_from_db()
        assert world.project.name == "P"
        assert world.project.network == 0
        assert world.project.guest_view_all_features is False
        assert world.project.workspace_id == world.ws.id

    def test_admin_put_works_but_cannot_move_workspace(self, world):
        r = world.c["admin"].put(
            self._url(world), {"name": "Renamed", "workspace": str(world.other_ws.id)}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        world.project.refresh_from_db()
        assert world.project.name == "Renamed"
        assert world.project.workspace_id == world.ws.id

    def test_admin_patch_cannot_move_workspace(self, world):
        r = world.c["admin"].patch(self._url(world), {"workspace": str(world.other_ws.id)}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        world.project.refresh_from_db()
        assert world.project.workspace_id == world.ws.id


# --------------------------------------------------------------------------------------------------- comment
@pytest.mark.contract
class TestIssueCommentPut:
    @pytest.fixture
    def comment(self, world):
        c = IssueComment.objects.create(
            issue=world.issue, project=world.project, workspace=world.ws, comment_html="<p>admin said</p>"
        )
        _set_creator(c, world.admin)
        return c

    def _url(self, w, c):
        return f"{w.pbase}/issues/{w.issue.id}/comments/{c.id}/"

    @pytest.mark.parametrize("who", ["guest", "member", "ws_only", "stranger"])
    def test_put_cannot_rewrite_someone_elses_comment(self, world, comment, who):
        r = world.c[who].put(self._url(world, comment), {"comment_html": "<p>rewritten</p>"}, format="json")
        _assert_refused(r)
        comment.refresh_from_db()
        assert comment.comment_html == "<p>admin said</p>"

    def test_author_put_still_works(self, world, comment):
        r = world.c["admin"].put(self._url(world, comment), {"comment_html": "<p>edited</p>"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        comment.refresh_from_db()
        assert comment.comment_html == "<p>edited</p>"


# --------------------------------------------------------------------------------------------- module / cycle
@pytest.mark.contract
class TestModuleAndCyclePut:
    @pytest.fixture
    def module(self, world):
        return Module.objects.create(name="M", project=world.project, workspace=world.ws)

    @pytest.fixture
    def cycle(self, world):
        return Cycle.objects.create(name="C", project=world.project, workspace=world.ws, owned_by=world.admin)

    @pytest.mark.parametrize("who", ["guest", "ws_only", "stranger"])
    def test_module_put_refused(self, world, module, who):
        r = world.c[who].put(f"{world.pbase}/modules/{module.id}/", {"name": "Pwned"}, format="json")
        _assert_refused(r)
        module.refresh_from_db()
        assert module.name == "M"

    def test_module_put_member_ok(self, world, module):
        r = world.c["member"].put(f"{world.pbase}/modules/{module.id}/", {"name": "M2"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        module.refresh_from_db()
        assert module.name == "M2"

    @pytest.mark.parametrize("who", ["guest", "ws_only", "stranger"])
    def test_cycle_put_refused(self, world, cycle, who):
        r = world.c[who].put(f"{world.pbase}/cycles/{cycle.id}/", {"name": "Pwned", "description": "x"}, format="json")
        _assert_refused(r)
        cycle.refresh_from_db()
        assert cycle.name == "C"
        assert cycle.description != "x"

    def test_cycle_put_member_ok(self, world, cycle):
        r = world.c["member"].put(f"{world.pbase}/cycles/{cycle.id}/", {"name": "C2"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        cycle.refresh_from_db()
        assert cycle.name == "C2"

    @pytest.mark.parametrize("method", ["get", "put", "patch"])
    def test_cycle_and_module_issue_detail_only_route_delete(self, world, module, cycle, method):
        for url in (
            f"{world.pbase}/cycles/{cycle.id}/cycle-issues/{world.issue.id}/",
            f"{world.pbase}/modules/{module.id}/issues/{world.issue.id}/",
        ):
            r = getattr(world.c["admin"], method)(url, {}, format="json")
            assert r.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, (url, r.status_code)


# ---------------------------------------------------------------------------------------------------- intake
@pytest.mark.contract
class TestIntakePatch:
    @pytest.fixture
    def intake(self, world):
        return Intake.objects.create(name="Intake", project=world.project, workspace=world.ws, is_default=True)

    @pytest.mark.parametrize("who", ["guest", "ws_only", "stranger"])
    def test_patch_refused(self, world, intake, who):
        r = world.c[who].patch(f"{world.pbase}/intakes/{intake.id}/", {"name": "Pwned"}, format="json")
        _assert_refused(r)
        intake.refresh_from_db()
        assert intake.name == "Intake"

    def test_stranger_cannot_read(self, world, intake):
        _assert_refused(world.c["stranger"].get(f"{world.pbase}/intakes/{intake.id}/"))

    def test_member_patch_ok(self, world, intake):
        r = world.c["member"].patch(f"{world.pbase}/intakes/{intake.id}/", {"name": "Triage"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        intake.refresh_from_db()
        assert intake.name == "Triage"


# ----------------------------------------------------------------------------------------------------- views
@pytest.mark.contract
class TestViewsPut:
    @pytest.fixture
    def project_view(self, world):
        v = IssueView.objects.create(
            name="PV", project=world.project, workspace=world.ws, owned_by=world.admin, access=1
        )
        _set_creator(v, world.admin)
        return v

    @pytest.fixture
    def workspace_view(self, world):
        v = IssueView.objects.create(name="WV", workspace=world.ws, owned_by=world.admin, access=1)
        _set_creator(v, world.admin)
        return v

    @pytest.mark.parametrize("who", ["member", "guest", "stranger"])
    def test_project_view_put_refused_for_non_owner(self, world, project_view, who):
        r = world.c[who].put(f"{world.pbase}/views/{project_view.id}/", {"name": "Pwned"}, format="json")
        _assert_refused(r)
        project_view.refresh_from_db()
        assert project_view.name == "PV"

    def test_project_view_put_owner_ok(self, world, project_view):
        r = world.c["admin"].put(f"{world.pbase}/views/{project_view.id}/", {"name": "PV2"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        project_view.refresh_from_db()
        assert project_view.name == "PV2"

    @pytest.mark.parametrize("who", ["member", "stranger"])
    def test_workspace_view_put_refused_for_non_owner(self, world, workspace_view, who):
        r = world.c[who].put(f"{world.base}/views/{workspace_view.id}/", {"name": "Pwned"}, format="json")
        _assert_refused(r)
        workspace_view.refresh_from_db()
        assert workspace_view.name == "WV"

    def test_workspace_view_put_owner_ok(self, world, workspace_view):
        r = world.c["admin"].put(f"{world.base}/views/{workspace_view.id}/", {"name": "WV2"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        workspace_view.refresh_from_db()
        assert workspace_view.name == "WV2"

    def test_stranger_cannot_read_workspace_view(self, world, workspace_view):
        _assert_refused(world.c["stranger"].get(f"{world.base}/views/{workspace_view.id}/"))


# ----------------------------------------------------------------------------------------- project invitations
@pytest.mark.contract
class TestProjectInvitations:
    @pytest.fixture
    def invite(self, world):
        return ProjectMemberInvite.objects.create(
            project=world.project, workspace=world.ws, email="invitee@plane.so", token=uuid.uuid4().hex, role=15
        )

    @pytest.mark.parametrize("who", ["member", "guest", "ws_only", "stranger"])
    def test_delete_refused(self, world, invite, who):
        r = world.c[who].delete(f"{world.pbase}/invitations/{invite.id}/")
        _assert_refused(r)
        assert ProjectMemberInvite.objects.filter(id=invite.id).exists()

    @pytest.mark.parametrize("who", ["member", "stranger"])
    def test_list_and_retrieve_refused(self, world, invite, who):
        _assert_refused(world.c[who].get(f"{world.pbase}/invitations/"))
        _assert_refused(world.c[who].get(f"{world.pbase}/invitations/{invite.id}/"))

    def test_admin_delete_ok(self, world, invite):
        r = world.c["admin"].delete(f"{world.pbase}/invitations/{invite.id}/")
        assert r.status_code == status.HTTP_204_NO_CONTENT, r.status_code
        assert not ProjectMemberInvite.objects.filter(id=invite.id).exists()


# ------------------------------------------------------------------------------------------------- notifications
@pytest.mark.contract
class TestNotificationDestroy:
    def _notify(self, world, receiver):
        return Notification.objects.create(
            workspace=world.ws, project=world.project, entity_name="issue", title="t", sender="x", receiver=receiver
        )

    def test_user_who_left_workspace_cannot_delete(self, world):
        n = self._notify(world, world.member)
        WorkspaceMember.objects.filter(workspace=world.ws, member=world.member).update(is_active=False)
        _assert_refused(world.c["member"].delete(f"{world.base}/users/notifications/{n.id}/"))
        assert Notification.objects.filter(id=n.id).exists()

    def test_stranger_cannot_delete_others(self, world):
        n = self._notify(world, world.member)
        _assert_refused(world.c["stranger"].delete(f"{world.base}/users/notifications/{n.id}/"))
        assert Notification.objects.filter(id=n.id).exists()

    def test_receiver_delete_ok(self, world):
        n = self._notify(world, world.member)
        r = world.c["member"].delete(f"{world.base}/users/notifications/{n.id}/")
        assert r.status_code == status.HTTP_204_NO_CONTENT, r.status_code


# ---------------------------------------------------------------------------------------------------- workspace
@pytest.mark.contract
class TestWorkspacePut:
    def test_member_put_refused(self, world):
        r = world.c["member"].put(f"{world.base}/", {"name": "Hijacked", "slug": world.ws.slug}, format="json")
        _assert_refused(r)
        world.ws.refresh_from_db()
        assert world.ws.name == "Test Workspace"

    def test_admin_put_ok(self, world):
        r = world.c["admin"].put(f"{world.base}/", {"name": "Renamed WS"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        world.ws.refresh_from_db()
        assert world.ws.name == "Renamed WS"


# ------------------------------------------------------------------------------------------------- deploy board
@pytest.mark.contract
class TestDeployBoardScope:
    @pytest.fixture
    def boards(self, world):
        own = DeployBoard.objects.create(
            entity_name="project", entity_identifier=world.project.id, project=world.project, workspace=world.ws
        )
        other_project = Project.objects.create(name="Q", identifier="Q", workspace=world.other_ws, network=0)
        foreign = DeployBoard.objects.create(
            entity_name="project",
            entity_identifier=other_project.id,
            project=other_project,
            workspace=world.other_ws,
            is_comments_enabled=False,
        )
        return own, foreign

    def test_cannot_patch_or_delete_another_projects_board(self, world, boards):
        _, foreign = boards
        url = f"{world.pbase}/project-deploy-boards/{foreign.id}/"
        _assert_refused(world.c["member"].patch(url, {"is_comments_enabled": True}, format="json"))
        _assert_refused(world.c["member"].delete(url))
        foreign.refresh_from_db()
        assert foreign.is_comments_enabled is False
        assert foreign.deleted_at is None

    def test_member_patch_own_board_ok(self, world, boards):
        own, _ = boards
        r = world.c["member"].patch(
            f"{world.pbase}/project-deploy-boards/{own.id}/", {"is_comments_enabled": True}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))
        own.refresh_from_db()
        assert own.is_comments_enabled is True


# ------------------------------------------------------------------------------------------ creator shortcut
@pytest.mark.contract
class TestCreatorShortcutRequiresProjectMembership:
    @pytest.fixture
    def own_issue(self, world):
        i = Issue.objects.create(name="mine", project=world.project, workspace=world.ws, state=world.state)
        _set_creator(i, world.ex_member)
        return i

    def test_ex_member_cannot_patch_own_issue(self, world, own_issue):
        r = world.c["ex_member"].patch(f"{world.pbase}/issues/{own_issue.id}/", {"name": "still mine"}, format="json")
        _assert_refused(r)
        own_issue.refresh_from_db()
        assert own_issue.name == "mine"

    def test_ex_member_cannot_delete_own_issue(self, world, own_issue):
        _assert_refused(world.c["ex_member"].delete(f"{world.pbase}/issues/{own_issue.id}/"))
        assert Issue.objects.filter(id=own_issue.id).exists()

    def test_ex_member_cannot_edit_or_delete_own_comment(self, world):
        c = IssueComment.objects.create(
            issue=world.issue, project=world.project, workspace=world.ws, comment_html="<p>mine</p>"
        )
        _set_creator(c, world.ex_member)
        url = f"{world.pbase}/issues/{world.issue.id}/comments/{c.id}/"
        _assert_refused(world.c["ex_member"].patch(url, {"comment_html": "<p>x</p>"}, format="json"))
        _assert_refused(world.c["ex_member"].delete(url))
        c.refresh_from_db()
        assert c.comment_html == "<p>mine</p>"

    def test_active_guest_creator_can_still_edit_own_comment(self, world):
        c = IssueComment.objects.create(
            issue=world.issue, project=world.project, workspace=world.ws, comment_html="<p>guest</p>"
        )
        _set_creator(c, world.guest)
        url = f"{world.pbase}/issues/{world.issue.id}/comments/{c.id}/"
        r = world.c["guest"].patch(url, {"comment_html": "<p>guest edit</p>"}, format="json")
        assert r.status_code == status.HTTP_200_OK, (r.status_code, getattr(r, "data", None))


# ----------------------------------------------------------------------------------------------- v1 API invite
@pytest.mark.contract
class TestV1WorkspaceInvitePut:
    def test_put_cannot_change_invite_email(self, world, api_key_client):
        invite = WorkspaceMemberInvite.objects.create(
            workspace=world.ws, email="invitee@plane.so", token=uuid.uuid4().hex, role=15
        )
        r = api_key_client.put(
            f"/api/v1/workspaces/{world.ws.slug}/invitations/{invite.id}/",
            {"email": "attacker@evil.test", "role": 15},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, (r.status_code, getattr(r, "data", None))
        invite.refresh_from_db()
        assert invite.email == "invitee@plane.so"
