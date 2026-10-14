# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: security review (Oct 2026) of move-work-item (PSR-91) / draft project
change (PSR-92) — regression tests for the issues found and fixed.

* validate_move checks permissions before anything that reveals item or
  target-project state;
* a pending-intake sub-item cannot leave intake by riding along with its parent;
* a sub-item that moved away between analysis and lock aborts the move (409);
* the old-identifier redirect is only followed for members of the new project;
* the draft project-mapping endpoint only resolves ids of the two projects
  involved (no label/user name lookup by UUID).
"""

from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Intake, IntakeIssue, Issue, Project, Workspace
from plane.db.models.api import APIToken
from plane.tests.move_helpers import (
    GUEST,
    add_project_member,
    add_workspace_member,
    build_world,
    client_for,
    make_issue,
    make_label,
    make_project,
    make_user,
    move_url,
)
from plane.utils import work_item_move


@pytest.fixture(autouse=True)
def side_effects(db):
    with (
        mock.patch("plane.app.views.issue.move.base_host", return_value="http://testserver"),
        mock.patch("plane.app.views.issue.move.model_activity"),
        mock.patch("plane.api.views.work_item_move.base_host", return_value="http://testserver"),
        mock.patch("plane.api.views.work_item_move.model_activity"),
        mock.patch("plane.bgtasks.notification_task.notifications.delay"),
    ):
        yield


@pytest.fixture
def world(db, workspace, create_user):
    return build_world(workspace, create_user)


def _key_client(user):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=APIToken.objects.create(user=user).token)
    return client


def _post_move(client, world, issue, **body):
    body.setdefault("target_project_id", str(world.tgt.id))
    return client.post(move_url(world, issue.project_id, issue.id), body, format="json")


@pytest.mark.contract
@pytest.mark.django_db
class TestValidationOrder:
    def test_guest_api_preview_learns_nothing_about_target_or_intake(self, world):
        guest = make_user("srcguest")
        add_workspace_member(world.workspace, guest, GUEST)
        add_project_member(world.src, guest, GUEST)
        item = make_issue(world.src, "Item", state=world.s.todo, creator=guest)
        intake = Intake.objects.create(project=world.src, name="Intake")
        IntakeIssue.objects.create(intake=intake, issue=item, project=world.src, status=-2)
        Project.objects.filter(pk=world.tgt.pk).update(archived_at="2026-01-01T00:00:00Z")
        url = f"/api/v1/workspaces/{world.slug}/projects/{world.src.id}/work-items/{item.id}/move/"

        for target in (str(world.tgt.id), "00000000-0000-0000-0000-000000000000"):
            response = _key_client(guest).get(url, {"target_project_id": target})
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.data["code"] == "FORBIDDEN_SOURCE"

    def test_non_member_of_archived_target_gets_forbidden_not_archived(self, world):
        other = make_project(world.workspace, "OTH", world.admin)
        Project.objects.filter(pk=other.pk).update(archived_at="2026-01-01T00:00:00Z")
        item = make_issue(world.src, "Item", state=world.s.todo, creator=world.mover)

        response = _post_move(client_for(world.mover), world, item, target_project_id=str(other.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"


@pytest.mark.contract
@pytest.mark.django_db
class TestSubItems:
    def test_pending_intake_sub_item_blocks_move_together(self, world):
        root = make_issue(world.src, "Root", state=world.s.todo, creator=world.mover)
        child = make_issue(world.src, "Child", state=world.s.todo, parent=root, creator=world.mover)
        intake = Intake.objects.create(project=world.src, name="Intake")
        IntakeIssue.objects.create(intake=intake, issue=child, project=world.src, status=0)

        response = _post_move(client_for(world.mover), world, root)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "ITEM_IN_INTAKE"
        assert Issue.objects.get(pk=root.pk).project_id == world.src.id
        assert Issue.objects.get(pk=child.pk).project_id == world.src.id

        # detaching leaves the intake sub-item where it is, so that is allowed
        detach = _post_move(client_for(world.mover), world, root, sub_items="detach")
        assert detach.status_code == status.HTTP_200_OK, detach.data
        assert Issue.objects.get(pk=child.pk).project_id == world.src.id

    def test_sub_item_moved_away_mid_move_aborts_with_409(self, world, monkeypatch):
        third = make_project(world.workspace, "THR", world.admin)
        root = make_issue(world.src, "Root", state=world.s.todo, creator=world.mover)
        child = make_issue(world.src, "Child", state=world.s.todo, parent=root, creator=world.mover)
        real_init = work_item_move._Analysis.__init__

        def init_then_race(self, *args, **kwargs):
            real_init(self, *args, **kwargs)
            # a concurrent request moved the sub-item after it was analysed
            Issue.objects.filter(pk=child.pk).update(project_id=third.id)

        monkeypatch.setattr(work_item_move._Analysis, "__init__", init_then_race)

        response = _post_move(client_for(world.mover), world, root)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["code"] == "CONFLICT"
        assert Issue.objects.get(pk=root.pk).project_id == world.src.id  # rolled back


@pytest.mark.contract
@pytest.mark.django_db
class TestOldIdentifierRedirect:
    def test_non_member_of_new_project_gets_plain_404(self, world):
        item = make_issue(world.src, "Moved", state=world.s.todo, creator=world.mover)
        assert _post_move(client_for(world.mover), world, item).status_code == status.HTTP_200_OK
        old = f"/api/workspaces/{world.slug}/work-items/SRC-{item.sequence_id}/"
        never = f"/api/workspaces/{world.slug}/work-items/SRC-999/"

        moved_response = client_for(world.users.not_in_tgt).get(old)
        never_response = client_for(world.users.not_in_tgt).get(never)

        assert moved_response.status_code == never_response.status_code == status.HTTP_404_NOT_FOUND
        assert moved_response.data == never_response.data


@pytest.mark.contract
@pytest.mark.django_db
class TestDraftMappingScope:
    def test_ids_outside_the_two_projects_are_not_resolved(self, world):
        hidden = make_project(world.workspace, "HID", world.admin)  # mover is not a member
        hidden_label = make_label(hidden, "secret-roadmap")
        other_ws = Workspace.objects.create(name="Other", owner=world.admin, slug="other-ws-sec")
        stranger = make_user("stranger")  # not in this workspace
        add_workspace_member(other_ws, stranger)

        response = client_for(world.mover).post(
            f"/api/workspaces/{world.slug}/draft-issues/project-mapping/",
            {
                "source_project_id": str(world.src.id),
                "target_project_id": str(world.tgt.id),
                "label_ids": [str(hidden_label.id), str(world.src_labels.feature.id)],
                "assignee_ids": [str(stranger.id), str(world.users.not_in_tgt.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert [label["name"] for label in response.data["dropped"]["labels"]] == ["feature"]
        assert [a["id"] for a in response.data["dropped"]["assignees"]] == [str(world.users.not_in_tgt.id)]
