# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the public v1 move-work-item endpoint (PSR-91).

``GET|POST /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{id}/move/``
is a thin wrapper over the same code as the app endpoint (covered in depth by
tests/contract/app/test_work_item_move_app.py); these tests pin the API-key
auth path and the permission rules.
"""

from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, IssueActivity, IssueLabel, IssueSequence
from plane.db.models.api import APIToken
from plane.tests.move_helpers import (
    GUEST,
    MEMBER,
    add_project_member,
    add_workspace_member,
    assign,
    build_world,
    label_issue,
    make_issue,
    make_user,
)


def move_url(world, issue, project_id=None):
    return f"/api/v1/workspaces/{world.slug}/projects/{project_id or issue.project_id}/work-items/{issue.id}/move/"


def key_client(user):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=APIToken.objects.create(user=user).token)
    return client


@pytest.fixture(autouse=True)
def side_effects(db):
    with (
        mock.patch("plane.api.views.work_item_move.base_host", return_value="http://testserver"),
        mock.patch("plane.api.views.work_item_move.model_activity") as model_activity,
        mock.patch("plane.bgtasks.notification_task.notifications.delay") as notify,
    ):
        yield mock.Mock(model_activity=model_activity, notify=notify)


@pytest.fixture
def world(db, workspace, create_user):
    return build_world(workspace, create_user)


@pytest.fixture
def item(world):
    return make_issue(world.src, "Item", state=world.s.todo, creator=world.admin)


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkItemMoveApi:
    def test_preview_with_api_key(self, api_key_client, world, item):
        label_issue(item, world.src_labels.bug)
        label_issue(item, world.src_labels.feature)
        assign(item, world.users.tgt_member)
        assign(item, world.users.not_in_tgt)

        response = api_key_client.get(move_url(world, item), {"target_project_id": str(world.tgt.id)})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["identifier"] == "SRC-1"
        assert response.data["next_identifier"] == "TGT-1"
        assert response.data["states"][0]["target"]["id"] == str(world.t.ready.id)
        assert [label["name"] for label in response.data["labels"]["dropped"]] == ["feature"]
        assert [a["id"] for a in response.data["assignees"]["dropped"]] == [str(world.users.not_in_tgt.id)]
        assert {"LABELS_DROPPED", "ASSIGNEES_DROPPED"} <= set(response.data["warnings"])
        # preview is read-only
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id
        assert not IssueSequence.objects.filter(project=world.tgt).exists()

    def test_move_with_api_key(self, api_key_client, world, item, side_effects, django_capture_on_commit_callbacks):
        label_issue(item, world.src_labels.bug)
        assign(item, world.users.tgt_member)
        assign(item, world.users.not_in_tgt)

        with django_capture_on_commit_callbacks(execute=True):
            response = api_key_client.post(
                move_url(world, item),
                {
                    "target_project_id": str(world.tgt.id),
                    "state_map": {str(world.s.todo.id): str(world.t.progress.id)},
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["identifier"] == "TGT-1"
        assert response.data["project_id"] == str(world.tgt.id)
        assert response.data["id"] == str(item.id)
        moved = Issue.objects.get(pk=item.pk)
        assert (moved.project_id, moved.sequence_id) == (world.tgt.id, 1)
        assert moved.state_id == world.t.progress.id
        assert set(IssueLabel.objects.filter(issue=item).values_list("label_id", flat=True)) == {
            world.tgt_labels.bug.id
        }
        assert IssueActivity.objects.filter(issue=item, field="project", old_value="SRC-1", new_value="TGT-1").exists()
        # old identifier row kept in the source
        assert IssueSequence.objects.filter(project=world.src, sequence=1, issue=item).exists()
        side_effects.model_activity.delay.assert_called_once()
        assert side_effects.model_activity.delay.call_args.kwargs["model_id"] == str(item.id)
        side_effects.notify.assert_called_once()

    def test_move_moves_sub_items_by_default_and_honours_detach(self, api_key_client, world, item):
        child = make_issue(world.src, "Child", state=world.s.done, parent=item, creator=world.admin)
        sibling = make_issue(world.src, "Kept", state=world.s.done, parent=item, creator=world.admin)

        together = api_key_client.post(move_url(world, item), {"target_project_id": str(world.tgt.id)}, format="json")
        assert together.status_code == status.HTTP_200_OK, together.data
        assert len(together.data["moved"]) == 3
        assert Issue.objects.get(pk=child.pk).project_id == world.tgt.id
        assert Issue.objects.get(pk=sibling.pk).parent_id == item.id

        # back again, detaching this time
        detached = api_key_client.post(
            move_url(world, item, project_id=world.tgt.id),
            {"target_project_id": str(world.src.id), "sub_items": "detach"},
            format="json",
        )
        assert detached.status_code == status.HTTP_200_OK, detached.data
        assert len(detached.data["moved"]) == 1
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id
        assert Issue.objects.get(pk=child.pk).project_id == world.tgt.id
        assert Issue.objects.get(pk=child.pk).parent_id is None

    @pytest.mark.parametrize("role_in_source", [GUEST, None])
    def test_post_is_forbidden_without_edit_rights_in_source(self, world, item, role_in_source):
        user = make_user("apiguest")
        add_workspace_member(world.workspace, user, MEMBER)
        if role_in_source:
            add_project_member(world.src, user, role_in_source)
        add_project_member(world.tgt, user, MEMBER)

        response = key_client(user).post(move_url(world, item), {"target_project_id": str(world.tgt.id)}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id
        assert not IssueSequence.objects.filter(project=world.tgt).exists()

    def test_guest_preview_is_forbidden(self, world, item):
        user = make_user("apiguest")
        add_workspace_member(world.workspace, user, MEMBER)
        add_project_member(world.src, user, GUEST)
        add_project_member(world.tgt, user, MEMBER)

        response = key_client(user).get(move_url(world, item), {"target_project_id": str(world.tgt.id)})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_SOURCE"

    def test_post_is_forbidden_when_not_allowed_to_create_in_target(self, world, item):
        client = key_client(world.users.tgt_guest)  # member of SRC, guest of TGT

        response = client.post(move_url(world, item), {"target_project_id": str(world.tgt.id)}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id

    def test_not_in_target_is_forbidden(self, world, item):
        client = key_client(world.users.not_in_tgt)

        get = client.get(move_url(world, item), {"target_project_id": str(world.tgt.id)})
        post = client.post(move_url(world, item), {"target_project_id": str(world.tgt.id)}, format="json")

        assert get.status_code == status.HTTP_403_FORBIDDEN
        assert post.status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id

    def test_same_project_is_400(self, api_key_client, world, item):
        response = api_key_client.post(move_url(world, item), {"target_project_id": str(world.src.id)}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "SAME_PROJECT"

    def test_invalid_state_map_is_400_and_changes_nothing(self, api_key_client, world, item):
        response = api_key_client.post(
            move_url(world, item),
            {"target_project_id": str(world.tgt.id), "state_map": {str(world.s.todo.id): str(world.s.done.id)}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_STATE_MAP"
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id

    def test_unknown_work_item_is_404(self, api_key_client, world, item):
        url = (
            f"/api/v1/workspaces/{world.slug}/projects/{world.src.id}"
            "/work-items/00000000-0000-0000-0000-000000000000/move/"
        )

        get = api_key_client.get(url, {"target_project_id": str(world.tgt.id)})
        post = api_key_client.post(url, {"target_project_id": str(world.tgt.id)}, format="json")

        assert get.status_code == status.HTTP_404_NOT_FOUND
        assert post.status_code == status.HTTP_404_NOT_FOUND

    def test_other_http_methods_are_not_allowed(self, api_key_client, world, item):
        for method in ("put", "patch", "delete"):
            response = getattr(api_key_client, method)(move_url(world, item), {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED, method

    def test_requires_authentication(self, api_client, world, item):
        response = api_client.post(move_url(world, item), {"target_project_id": str(world.tgt.id)}, format="json")

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert Issue.objects.get(pk=item.pk).project_id == world.src.id

    def test_extra_body_keys_are_ignored(self, api_key_client, world, item):
        response = api_key_client.post(
            move_url(world, item),
            {"target_project_id": str(world.tgt.id), "sequence_id": 999, "name": "Hacked", "workspace": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        moved = Issue.objects.get(pk=item.pk)
        assert (moved.sequence_id, moved.name) == (1, "Item")
