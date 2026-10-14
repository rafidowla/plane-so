# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for changing a draft's project (PSR-92).

The draft endpoints reuse the move-work-item mapping rules
(docs/move-work-item-design.md section 2 and 3):

* ``PATCH /draft-issues/{id}/`` with a different ``project_id`` re-targets the draft.
* ``POST  /draft-to-issue/{id}/`` may post into a project other than the draft's.
* ``POST  /draft-issues/project-mapping/`` previews the mapping for the modal.
* the draft serializer's ``project`` field is read-only (no mass assignment).
"""

from unittest import mock
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    Cycle,
    CycleIssue,
    DraftIssue,
    DraftIssueAssignee,
    DraftIssueCycle,
    DraftIssueLabel,
    DraftIssueModule,
    Issue,
    IssueAssignee,
    IssueLabel,
    Module,
    ModuleIssue,
    Project,
    Workspace,
)
from plane.tests.move_helpers import (
    GUEST,
    MEMBER,
    add_project_member,
    add_workspace_member,
    build_world,
    client_for,
    make_estimate,
    make_issue,
    make_project,
    make_state,
    make_user,
)


def draft_url(slug, draft_id=None):
    base = f"/api/workspaces/{slug}/draft-issues/"
    return f"{base}{draft_id}/" if draft_id else base


def post_url(slug, draft_id):
    return f"/api/workspaces/{slug}/draft-to-issue/{draft_id}/"


def mapping_url(slug):
    return f"/api/workspaces/{slug}/draft-issues/project-mapping/"


@pytest.fixture(autouse=True)
def side_effects(db):
    """Stub the broker-backed activity task, base_host (needs APP_BASE_URL) and the soft-delete cascade task."""
    with (
        mock.patch("plane.app.views.workspace.draft.issue_activity") as issue_activity,
        mock.patch("plane.app.views.workspace.draft.base_host", return_value="http://testserver"),
        mock.patch("plane.db.mixins.soft_delete_related_objects"),
    ):
        yield mock.Mock(issue_activity=issue_activity)


@pytest.fixture
def world(db, workspace, create_user):
    return build_world(workspace, create_user)


@pytest.fixture
def mover_client(world):
    return client_for(world.mover)


def make_draft(world, owner=None, project=None, state=None, **fields):
    owner = owner or world.mover
    draft = DraftIssue(
        name=fields.pop("name", "Draft"),
        workspace=world.workspace,
        project=project or world.src,
        state=state if state is not None else world.s.done,
        **fields,
    )
    draft.save(created_by_id=owner.id)
    return draft


def attach_to_draft(world, draft, labels=(), assignees=(), cycle=None, modules=()):
    project = draft.project
    for label in labels:
        DraftIssueLabel.objects.create(draft_issue=draft, label=label, project=project, workspace=world.workspace)
    for user in assignees:
        DraftIssueAssignee.objects.create(draft_issue=draft, assignee=user, project=project, workspace=world.workspace)
    if cycle:
        DraftIssueCycle.objects.create(draft_issue=draft, cycle=cycle, project=project, workspace=world.workspace)
    for module in modules:
        DraftIssueModule.objects.create(draft_issue=draft, module=module, project=project, workspace=world.workspace)


@pytest.fixture
def full_draft(world):
    """A draft in SRC with state Done, labels bug+feature, two assignees, a cycle and a module."""
    cycle = Cycle.objects.create(project=world.src, name="Sprint", owned_by=world.admin)
    module = Module.objects.create(project=world.src, name="Mod")
    draft = make_draft(world)
    attach_to_draft(
        world,
        draft,
        labels=[world.src_labels.bug, world.src_labels.feature],
        assignees=[world.users.tgt_member, world.users.not_in_tgt],
        cycle=cycle,
        modules=[module],
    )
    return draft


def label_ids(draft):
    return set(DraftIssueLabel.objects.filter(draft_issue=draft).values_list("label_id", flat=True))


def assignee_ids(draft):
    return set(DraftIssueAssignee.objects.filter(draft_issue=draft).values_list("assignee_id", flat=True))


def fresh(draft):
    return DraftIssue.objects.get(pk=draft.pk)


# -------------------------------------------------------------------- PATCH
@pytest.mark.contract
@pytest.mark.django_db
class TestPatchDraftProject:
    def test_retargets_and_maps_stored_values(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
        draft = fresh(full_draft)
        assert draft.project_id == world.tgt.id
        assert draft.state_id == world.t.complete.id  # "Done" -> same group
        assert label_ids(draft) == {world.tgt_labels.bug.id}  # "feature" has no match
        assert assignee_ids(draft) == {world.users.tgt_member.id}  # not_in_tgt dropped
        assert not DraftIssueCycle.objects.filter(draft_issue=draft).exists()
        assert not DraftIssueModule.objects.filter(draft_issue=draft).exists()

    def test_related_rows_carry_the_new_project(self, mover_client, world, full_draft):
        mover_client.patch(draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json")

        assert set(DraftIssueLabel.objects.filter(draft_issue=full_draft).values_list("project_id", flat=True)) == {
            world.tgt.id
        }
        assert set(DraftIssueAssignee.objects.filter(draft_issue=full_draft).values_list("project_id", flat=True)) == {
            world.tgt.id
        }

    @pytest.mark.parametrize(
        "src_key, tgt_key",
        [("backlog", "backlog"), ("todo", "ready"), ("doing", "progress"), ("cancelled", "ready")],
        ids=["name", "group", "group-2", "default"],
    )
    def test_state_mapping_rules(self, mover_client, world, src_key, tgt_key):
        draft = make_draft(world, state=getattr(world.s, src_key))

        response = mover_client.patch(draft_url(world.slug, draft.id), {"project_id": str(world.tgt.id)}, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert fresh(draft).state_id == getattr(world.t, tgt_key).id

    def test_submitted_values_win_over_stored_ones(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {
                "project_id": str(world.tgt.id),
                "state_id": str(world.s.todo.id),
                "label_ids": [str(world.src_labels.docs.id)],
                "assignee_ids": [str(world.users.tgt_admin.id), str(world.users.tgt_guest.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
        draft = fresh(full_draft)
        assert draft.state_id == world.t.ready.id
        assert label_ids(draft) == {world.tgt_labels.docs.id}
        assert assignee_ids(draft) == {world.users.tgt_admin.id}  # a guest cannot be assigned

    def test_submitted_target_values_pass_through_idempotently(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {
                "project_id": str(world.tgt.id),
                "state_id": str(world.t.progress.id),
                "label_ids": [str(world.tgt_labels.docs.id)],
                "assignee_ids": [str(world.users.tgt_member.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        draft = fresh(full_draft)
        assert draft.state_id == world.t.progress.id
        assert label_ids(draft) == {world.tgt_labels.docs.id}
        assert assignee_ids(draft) == {world.users.tgt_member.id}

    def test_parent_and_estimate_are_remapped(self, mover_client, world):
        parent = make_issue(world.src, "Parent", state=world.s.todo, creator=world.mover)
        _, src_points = make_estimate(world.src, "points", ["2", "3"])
        _, tgt_points = make_estimate(world.tgt, "points", ["2"])
        draft = make_draft(world, parent=parent, estimate_point=src_points["2"])
        other = make_draft(world, parent=parent, estimate_point=src_points["3"])

        for d in (draft, other):
            r = mover_client.patch(draft_url(world.slug, d.id), {"project_id": str(world.tgt.id)}, format="json")
            assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)

        assert fresh(draft).parent_id is None  # parent lives in the source project
        assert fresh(draft).estimate_point_id == tgt_points["2"].id
        assert fresh(other).estimate_point_id is None  # no "3" in the target

    def test_name_can_change_in_the_same_request(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {"project_id": str(world.tgt.id), "name": "Renamed"},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        draft = fresh(full_draft)
        assert (draft.name, draft.project_id) == ("Renamed", world.tgt.id)

    def test_member_of_source_and_target_as_guest_may_retarget(self, world, full_draft):
        """Re-targeting a draft only needs active membership of the target (any role)."""
        guest_user = world.users.tgt_guest
        draft = make_draft(world, owner=guest_user)

        response = client_for(guest_user).patch(
            draft_url(world.slug, draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
        assert fresh(draft).project_id == world.tgt.id

    def test_forbidden_when_not_a_member_of_the_target(self, world, full_draft):
        outsider = world.users.not_in_tgt
        draft = make_draft(world, owner=outsider)
        attach_to_draft(world, draft, labels=[world.src_labels.bug])

        response = client_for(outsider).patch(
            draft_url(world.slug, draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"
        unchanged = fresh(draft)
        assert unchanged.project_id == world.src.id
        assert unchanged.state_id == world.s.done.id
        assert label_ids(draft) == {world.src_labels.bug.id}

    def test_inactive_membership_of_the_target_is_forbidden(self, world):
        user = world.users.tgt_inactive
        draft = make_draft(world, owner=user)

        response = client_for(user).patch(
            draft_url(world.slug, draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert fresh(draft).project_id == world.src.id

    def test_archived_target_is_400(self, mover_client, world, full_draft):
        Project.objects.filter(pk=world.tgt.pk).update(archived_at=timezone.now())

        response = mover_client.patch(
            draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_TARGET"
        assert fresh(full_draft).project_id == world.src.id

    @pytest.mark.parametrize("project_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_unknown_target_is_400(self, mover_client, world, full_draft, project_id):
        response = mover_client.patch(draft_url(world.slug, full_draft.id), {"project_id": project_id}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fresh(full_draft).project_id == world.src.id

    def test_project_of_another_workspace_is_400(self, mover_client, world, full_draft):
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=world.admin)
        foreign = make_project(other_ws, "FOR", world.admin)
        add_workspace_member(other_ws, world.mover, MEMBER)
        add_project_member(foreign, world.mover, MEMBER)

        response = mover_client.patch(
            draft_url(world.slug, full_draft.id), {"project_id": str(foreign.id)}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fresh(full_draft).project_id == world.src.id

    def test_project_key_in_body_is_ignored(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {"project": str(world.tgt.id), "name": "Renamed"},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        draft = fresh(full_draft)
        assert draft.project_id == world.src.id  # not re-targeted through the serializer
        assert draft.name == "Renamed"
        assert draft.state_id == world.s.done.id  # nothing was remapped
        assert label_ids(draft) == {world.src_labels.bug.id, world.src_labels.feature.id}

    def test_project_key_cannot_override_project_id(self, mover_client, world, full_draft):
        other = make_project(world.workspace, "OTH", world.admin)
        add_project_member(other, world.mover, MEMBER)
        make_state(other, "Todo", "unstarted", default=True)

        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {"project_id": str(world.tgt.id), "project": str(other.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert fresh(full_draft).project_id == world.tgt.id

    def test_workspace_key_in_body_is_ignored(self, mover_client, world, full_draft):
        other_ws = Workspace.objects.create(name="Evil", slug="evil-ws", owner=world.admin)

        response = mover_client.patch(
            draft_url(world.slug, full_draft.id), {"workspace": str(other_ws.id), "name": "x"}, format="json"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert fresh(full_draft).workspace_id == world.workspace.id

    def test_same_project_id_does_not_remap(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {"project_id": str(world.src.id), "name": "Same project"},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        draft = fresh(full_draft)
        assert (draft.project_id, draft.state_id, draft.name) == (world.src.id, world.s.done.id, "Same project")
        assert label_ids(draft) == {world.src_labels.bug.id, world.src_labels.feature.id}
        assert DraftIssueCycle.objects.filter(draft_issue=draft).count() == 1
        assert DraftIssueModule.objects.filter(draft_issue=draft).count() == 1

    def test_patch_without_project_id_keeps_the_project(self, mover_client, world, full_draft):
        response = mover_client.patch(draft_url(world.slug, full_draft.id), {"name": "Only a rename"}, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        draft = fresh(full_draft)
        assert (draft.project_id, draft.name) == (world.src.id, "Only a rename")
        assert DraftIssueCycle.objects.filter(draft_issue=draft).count() == 1

    def test_invalid_payload_rolls_the_retarget_back(self, mover_client, world, full_draft):
        response = mover_client.patch(
            draft_url(world.slug, full_draft.id),
            {"project_id": str(world.tgt.id), "start_date": "2026-07-10", "target_date": "2026-07-01"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        draft = fresh(full_draft)
        assert draft.project_id == world.src.id
        assert draft.state_id == world.s.done.id
        assert label_ids(draft) == {world.src_labels.bug.id, world.src_labels.feature.id}
        assert DraftIssueCycle.objects.filter(draft_issue=draft).count() == 1

    def test_cannot_retarget_someone_elses_draft(self, world, full_draft):
        stranger = world.users.tgt_member

        response = client_for(stranger).patch(
            draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert fresh(full_draft).project_id == world.src.id

    def test_workspace_guest_cannot_patch_drafts(self, world):
        guest = make_user("wsguest")
        add_workspace_member(world.workspace, guest, GUEST)
        add_project_member(world.src, guest, MEMBER)
        add_project_member(world.tgt, guest, MEMBER)
        draft = make_draft(world, owner=guest)

        response = client_for(guest).patch(
            draft_url(world.slug, draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert fresh(draft).project_id == world.src.id


# ----------------------------------------------------------------------- POST create
@pytest.mark.contract
@pytest.mark.django_db
class TestCreateDraft:
    def test_member_can_create_a_draft_in_their_project(self, mover_client, world):
        response = mover_client.post(
            draft_url(world.slug),
            {
                "name": "New draft",
                "project_id": str(world.src.id),
                "state_id": str(world.s.todo.id),
                "label_ids": [str(world.src_labels.bug.id)],
                "assignee_ids": [str(world.users.tgt_member.id), str(world.users.not_in_tgt.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        draft = DraftIssue.objects.get(pk=response.data["id"])
        assert draft.project_id == world.src.id
        assert label_ids(draft) == {world.src_labels.bug.id}
        assert assignee_ids(draft) == {world.users.tgt_member.id, world.users.not_in_tgt.id}

    @pytest.mark.parametrize("who", ["not_in_tgt", "tgt_inactive"])
    def test_cannot_create_a_draft_in_a_project_you_do_not_belong_to(self, world, who):
        user = getattr(world.users, who)
        before = DraftIssue.objects.count()

        response = client_for(user).post(
            draft_url(world.slug), {"name": "Sneaky", "project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"
        assert DraftIssue.objects.count() == before

    def test_project_in_another_workspace_is_forbidden(self, mover_client, world):
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=world.admin)
        foreign = make_project(other_ws, "FOR", world.admin)

        response = mover_client.post(draft_url(world.slug), {"name": "x", "project_id": str(foreign.id)}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not DraftIssue.objects.filter(name="x").exists()

    def test_project_key_in_body_is_not_mass_assigned(self, mover_client, world):
        response = mover_client.post(
            draft_url(world.slug),
            {"name": "Mass", "project_id": str(world.src.id), "project": str(world.tgt.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert DraftIssue.objects.get(pk=response.data["id"]).project_id == world.src.id


# ------------------------------------------------------------- POST draft-to-issue
@pytest.mark.contract
@pytest.mark.django_db
class TestPostDraftIntoAnotherProject:
    def _body(self, world, **extra):
        body = {
            "name": "Posted",
            "project_id": str(world.tgt.id),
            "state_id": str(world.s.done.id),
            "label_ids": [str(world.src_labels.bug.id), str(world.src_labels.feature.id)],
            "assignee_ids": [str(world.users.tgt_member.id), str(world.users.not_in_tgt.id)],
        }
        body.update(extra)
        return body

    def test_creates_the_issue_in_the_target_with_mapped_values_and_deletes_the_draft(
        self, mover_client, world, full_draft
    ):
        response = mover_client.post(post_url(world.slug, full_draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert issue.project_id == world.tgt.id
        assert issue.state_id == world.t.complete.id
        assert set(IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True)) == {
            world.tgt_labels.bug.id
        }
        assert set(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True)) == {
            world.users.tgt_member.id
        }
        assert issue.sequence_id == 1
        assert not DraftIssue.objects.filter(pk=full_draft.id).exists()

    def test_cycle_modules_and_parent_from_the_source_are_not_carried(self, mover_client, world, full_draft):
        cycle = Cycle.objects.get(project=world.src)
        module = Module.objects.get(project=world.src)
        parent = make_issue(world.src, "Parent", state=world.s.todo, creator=world.mover)

        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            self._body(world, cycle_id=str(cycle.id), module_ids=[str(module.id)], parent_id=str(parent.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert issue.parent_id is None
        assert not CycleIssue.objects.filter(issue=issue).exists()
        assert not ModuleIssue.objects.filter(issue=issue).exists()

    def test_cycle_and_module_of_the_target_are_applied(self, mover_client, world, full_draft):
        cycle = Cycle.objects.create(project=world.tgt, name="T cycle", owned_by=world.admin)
        module = Module.objects.create(project=world.tgt, name="T module")

        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            self._body(world, cycle_id=str(cycle.id), module_ids=[str(module.id)]),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert CycleIssue.objects.filter(issue=issue, cycle=cycle, project=world.tgt).exists()
        assert ModuleIssue.objects.filter(issue=issue, module=module, project=world.tgt).exists()

    def test_posting_without_project_id_uses_the_drafts_own_project(self, mover_client, world, full_draft):
        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            {"name": "Same project", "state_id": str(world.s.done.id), "label_ids": [str(world.src_labels.bug.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert issue.project_id == world.src.id
        assert issue.state_id == world.s.done.id  # no remapping within the same project
        assert set(IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True)) == {
            world.src_labels.bug.id
        }
        assert not DraftIssue.objects.filter(pk=full_draft.id).exists()

    def test_posting_with_the_drafts_own_project_id_does_not_remap(self, mover_client, world, full_draft):
        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            self._body(world, project_id=str(world.src.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert (issue.project_id, issue.state_id) == (world.src.id, world.s.done.id)

    def test_project_key_in_body_is_ignored(self, mover_client, world, full_draft):
        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            {"name": "x", "state_id": str(world.s.done.id), "project": str(world.tgt.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert Issue.objects.get(pk=response.data["id"]).project_id == world.src.id

    def test_guest_only_in_the_target_gets_403_and_keeps_the_draft(self, world):
        guest_user = world.users.tgt_guest  # member of SRC, guest in TGT
        draft = make_draft(world, owner=guest_user)

        response = client_for(guest_user).post(post_url(world.slug, draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert DraftIssue.objects.filter(pk=draft.id).exists()
        assert not Issue.objects.filter(project=world.tgt).exists()

    def test_guest_in_the_drafts_own_project_cannot_post_it(self, world):
        """The old code only checked the workspace role; posting needs project admin/member now."""
        user = world.users.tgt_guest
        draft = make_draft(world, owner=user, project=world.tgt, state=world.t.ready)

        response = client_for(user).post(post_url(world.slug, draft.id), {"name": "x"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert DraftIssue.objects.filter(pk=draft.id).exists()
        assert not Issue.objects.filter(project=world.tgt).exists()

    def test_non_member_of_the_target_gets_403(self, world):
        outsider = world.users.not_in_tgt
        draft = make_draft(world, owner=outsider)

        response = client_for(outsider).post(post_url(world.slug, draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert DraftIssue.objects.filter(pk=draft.id).exists()

    def test_inactive_member_of_the_target_gets_403(self, world):
        user = world.users.tgt_inactive
        draft = make_draft(world, owner=user)

        response = client_for(user).post(post_url(world.slug, draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_archived_target_is_400(self, mover_client, world, full_draft):
        Project.objects.filter(pk=world.tgt.pk).update(archived_at=timezone.now())

        response = mover_client.post(post_url(world.slug, full_draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "TARGET_ARCHIVED"
        assert DraftIssue.objects.filter(pk=full_draft.id).exists()

    @pytest.mark.parametrize("project_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_unknown_target_is_400(self, mover_client, world, full_draft, project_id):
        response = mover_client.post(
            post_url(world.slug, full_draft.id), self._body(world, project_id=project_id), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert DraftIssue.objects.filter(pk=full_draft.id).exists()

    def test_target_in_another_workspace_is_400(self, mover_client, world, full_draft):
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=world.admin)
        foreign = make_project(other_ws, "FOR", world.admin)

        response = mover_client.post(
            post_url(world.slug, full_draft.id), self._body(world, project_id=str(foreign.id)), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Issue.objects.filter(project=foreign).exists()

    def test_invalid_payload_keeps_the_draft(self, mover_client, world, full_draft):
        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            self._body(world, start_date="2026-07-10", target_date="2026-07-01"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert DraftIssue.objects.filter(pk=full_draft.id).exists()
        assert not Issue.objects.filter(project=world.tgt).exists()

    def test_cannot_post_someone_elses_draft(self, world, full_draft):
        response = client_for(world.users.tgt_member).post(
            post_url(world.slug, full_draft.id), self._body(world), format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert DraftIssue.objects.filter(pk=full_draft.id).exists()

    def test_activity_is_recorded_against_the_target_project(self, mover_client, world, full_draft, side_effects):
        response = mover_client.post(post_url(world.slug, full_draft.id), self._body(world), format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        created = [
            c for c in side_effects.issue_activity.delay.call_args_list if c.kwargs["type"] == "issue.activity.created"
        ]
        assert len(created) == 1
        assert created[0].kwargs["project_id"] == str(world.tgt.id)
        assert created[0].kwargs["issue_id"] == str(response.data["id"])

    def test_drafts_estimate_is_mapped_by_value(self, mover_client, world, full_draft):
        _, src_points = make_estimate(world.src, "points", ["2"])
        _, tgt_points = make_estimate(world.tgt, "points", ["2"])

        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            self._body(world, estimate_point=str(src_points["2"].id)),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert Issue.objects.get(pk=response.data["id"]).estimate_point_id == tgt_points["2"].id


# --------------------------------------------------------------- project-mapping
@pytest.mark.contract
@pytest.mark.django_db
class TestProjectMappingEndpoint:
    def _body(self, world, **extra):
        body = {
            "source_project_id": str(world.src.id),
            "target_project_id": str(world.tgt.id),
            "state_id": str(world.s.done.id),
            "label_ids": [str(world.src_labels.bug.id), str(world.src_labels.feature.id)],
            "assignee_ids": [str(world.users.tgt_member.id), str(world.users.not_in_tgt.id)],
        }
        body.update(extra)
        return body

    def test_returns_mapped_values_and_dropped_lists(self, mover_client, world):
        response = mover_client.post(mapping_url(world.slug), self._body(world), format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        data = response.data
        assert set(data) == {
            "state_id",
            "state_match",
            "label_ids",
            "assignee_ids",
            "estimate_point",
            "type_id",
            "parent_id",
            "cycle_id",
            "module_ids",
            "dropped",
        }
        assert set(data["dropped"]) == {"labels", "assignees", "cycle", "modules", "estimate", "parent", "type"}
        assert data["state_id"] == str(world.t.complete.id)
        assert data["state_match"] == "group"
        assert data["label_ids"] == [str(world.tgt_labels.bug.id)]
        assert data["assignee_ids"] == [str(world.users.tgt_member.id)]
        assert [label["name"] for label in data["dropped"]["labels"]] == ["feature"]
        assert [a["id"] for a in data["dropped"]["assignees"]] == [str(world.users.not_in_tgt.id)]
        assert data["dropped"]["cycle"] is False and data["dropped"]["modules"] is False

    def test_flags_cycle_modules_parent_and_estimate_as_dropped(self, mover_client, world):
        cycle = Cycle.objects.create(project=world.src, name="S", owned_by=world.admin)
        module = Module.objects.create(project=world.src, name="M")
        parent = make_issue(world.src, "P", state=world.s.todo, creator=world.mover)
        _, src_points = make_estimate(world.src, "points", ["3"])

        response = mover_client.post(
            mapping_url(world.slug),
            self._body(
                world,
                cycle_id=str(cycle.id),
                module_ids=[str(module.id)],
                parent_id=str(parent.id),
                estimate_point=str(src_points["3"].id),
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["cycle_id"] is None and response.data["module_ids"] == []
        assert response.data["parent_id"] is None and response.data["estimate_point"] is None
        dropped = response.data["dropped"]
        assert (dropped["cycle"], dropped["modules"], dropped["parent"], dropped["estimate"]) == (True,) * 4

    def test_no_state_maps_to_the_target_default(self, mover_client, world):
        response = mover_client.post(
            mapping_url(world.slug),
            {"source_project_id": str(world.src.id), "target_project_id": str(world.tgt.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["state_id"] == str(world.t.ready.id)
        assert response.data["state_match"] == "default"
        assert response.data["label_ids"] == [] and response.data["assignee_ids"] == []

    def test_is_read_only(self, mover_client, world):
        before = (DraftIssue.objects.count(), Issue.objects.count())

        mover_client.post(mapping_url(world.slug), self._body(world), format="json")

        assert (DraftIssue.objects.count(), Issue.objects.count()) == before

    def test_forbidden_when_not_a_member_of_the_target(self, world):
        response = client_for(world.users.not_in_tgt).post(mapping_url(world.slug), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"

    def test_forbidden_when_not_a_member_of_the_source(self, world):
        response = client_for(world.users.tgt_only).post(mapping_url(world.slug), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_target_guest_may_preview_the_mapping(self, world):
        """Membership (any role) of both projects is enough; assigning guests is dropped by the rules."""
        response = client_for(world.users.tgt_guest).post(
            mapping_url(world.slug),
            self._body(world, assignee_ids=[str(world.users.tgt_guest.id)]),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["assignee_ids"] == []
        assert [a["id"] for a in response.data["dropped"]["assignees"]] == [str(world.users.tgt_guest.id)]

    @pytest.mark.parametrize("field", ["source_project_id", "target_project_id"])
    def test_unknown_project_is_400(self, mover_client, world, field):
        response = mover_client.post(mapping_url(world.slug), self._body(world, **{field: str(uuid4())}), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_TARGET"

    def test_project_of_another_workspace_is_400(self, mover_client, world):
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=world.admin)
        foreign = make_project(other_ws, "FOR", world.admin)

        response = mover_client.post(
            mapping_url(world.slug), self._body(world, target_project_id=str(foreign.id)), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_ids_are_400(self, mover_client, world):
        response = mover_client.post(mapping_url(world.slug), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_requires_authentication(self, api_client, world):
        response = api_client.post(mapping_url(world.slug), self._body(world), format="json")

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_non_member_of_the_workspace_is_forbidden(self, world):
        stranger = make_user("stranger")

        response = client_for(stranger).post(mapping_url(world.slug), self._body(world), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unknown_body_keys_are_ignored(self, mover_client, world):
        response = mover_client.post(
            mapping_url(world.slug), self._body(world, workspace="x", project="y", sequence_id=5), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data

    def test_mapping_twice_is_stable(self, mover_client, world):
        first = mover_client.post(mapping_url(world.slug), self._body(world), format="json").data
        second = mover_client.post(
            mapping_url(world.slug),
            self._body(
                world,
                state_id=first["state_id"],
                label_ids=first["label_ids"],
                assignee_ids=first["assignee_ids"],
            ),
            format="json",
        ).data

        assert second["state_id"] == first["state_id"]
        assert second["state_match"] == "same"
        assert second["label_ids"] == first["label_ids"]
        assert second["assignee_ids"] == first["assignee_ids"]
        assert second["dropped"]["labels"] == [] and second["dropped"]["assignees"] == []


# --------------------------------------------------------------- draft list/read
@pytest.mark.contract
@pytest.mark.django_db
class TestDraftReadsAfterRetarget:
    def test_list_and_retrieve_report_the_new_project(self, mover_client, world, full_draft):
        mover_client.patch(draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json")

        listed = mover_client.get(draft_url(world.slug))
        assert listed.status_code == status.HTTP_200_OK
        (row,) = [r for r in listed.data["results"] if str(r["id"]) == str(full_draft.id)]
        assert str(row["project_id"]) == str(world.tgt.id)
        assert [str(i) for i in row["label_ids"]] == [str(world.tgt_labels.bug.id)]
        assert row["cycle_id"] is None and row["module_ids"] == []

    def test_end_to_end_retarget_then_post(self, mover_client, world, full_draft):
        patched = mover_client.patch(
            draft_url(world.slug, full_draft.id), {"project_id": str(world.tgt.id)}, format="json"
        )
        assert patched.status_code == status.HTTP_204_NO_CONTENT

        response = mover_client.post(
            post_url(world.slug, full_draft.id),
            {
                "name": "Posted after retarget",
                "project_id": str(world.tgt.id),
                "state_id": str(world.t.complete.id),
                "label_ids": [str(world.tgt_labels.bug.id)],
                "assignee_ids": [str(world.users.tgt_member.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(pk=response.data["id"])
        assert (issue.project_id, issue.state_id) == (world.tgt.id, world.t.complete.id)
        assert not DraftIssue.objects.filter(pk=full_draft.id).exists()
