# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for moving a work item to another project (PSR-91).

Rules under test are the ones in docs/move-work-item-design.md. The world each
test starts from is built by ``plane.tests.move_helpers.build_world``:

    states   Backlog->BACKLOG (name), Todo->Ready / Doing,Review->In Progress /
             Done->Complete (group), Cancelled->Ready (target default)
    actors   ``mover`` is a plain workspace member + project MEMBER in SRC/TGT,
             so none of the workspace-admin bypass applies to it.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

import pytest
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    Client,
    CommentReaction,
    Cycle,
    CycleIssue,
    Description,
    FileAsset,
    Intake,
    IntakeIssue,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueBlocker,
    IssueComment,
    IssueDescriptionVersion,
    IssueLabel,
    IssueLink,
    IssueMention,
    IssueReaction,
    IssueRelation,
    IssueSequence,
    IssueSubscriber,
    IssueType,
    IssueVersion,
    IssueVote,
    IssueWorklog,
    Module,
    ModuleIssue,
    Notification,
    Project,
    ProjectMember,
    UserRecentVisit,
    Workspace,
    WorklogTimer,
)
from plane.db.models.issue import IssueAttachment
from plane.db.models.issue_type import ProjectIssueType
from plane.db.models.worklog import Timesheet
from plane.properties.models import IssueProperty, IssuePropertyOption, IssuePropertyValue
from plane.tests.move_helpers import (
    ADMIN,
    GUEST,
    MEMBER,
    add_project_member,
    add_workspace_member,
    assign,
    build_world,
    client_for,
    label_issue,
    make_estimate,
    make_issue,
    make_project,
    make_state,
    make_user,
    move_url,
    preview_url,
    subscribe,
)


# --------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def side_effects(db):
    """Stub everything that would hit a broker or need APP_BASE_URL."""
    with (
        mock.patch("plane.app.views.issue.move.base_host", return_value="http://testserver"),
        mock.patch("plane.app.views.issue.move.model_activity") as model_activity,
        mock.patch("plane.bgtasks.notification_task.notifications.delay") as notify,
        mock.patch("plane.app.views.issue.base.recent_visited_task"),
    ):
        yield mock.Mock(model_activity=model_activity, notify=notify)


@pytest.fixture
def world(db, workspace, create_user):
    return build_world(workspace, create_user)


@pytest.fixture
def mover_client(world):
    return client_for(world.mover)


@pytest.fixture
def item(world):
    """A plain work item in SRC (state "Todo"), created by ``mover``."""
    return make_issue(world.src, "Item", state=world.s.todo, creator=world.mover)


# ---------------------------------------------------------------------- helpers
def post_move(client, world, issue, **body):
    body.setdefault("target_project_id", str(world.tgt.id))
    return client.post(move_url(world, issue.project_id, issue.id), body, format="json")


def get_preview(client, world, issue, **params):
    params.setdefault("target_project_id", str(world.tgt.id))
    return client.get(preview_url(world, issue.project_id, issue.id), params)


def fresh(issue):
    return Issue.objects.get(pk=issue.pk)


def label_ids(issue):
    return set(IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True))


def assignee_ids(issue):
    return set(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))


def snapshot(world, issue):
    """Everything a move touches, for 'nothing changed' assertions."""
    row = Issue.objects.filter(pk=issue.pk).values("project_id", "sequence_id", "state_id", "parent_id").get()
    return {
        "issue": row,
        "labels": sorted(IssueLabel.objects.filter(issue=issue).values_list("label_id", "project_id")),
        "assignees": sorted(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", "project_id")),
        "subscribers": sorted(IssueSubscriber.objects.filter(issue=issue).values_list("subscriber_id", "project_id")),
        "cycles": CycleIssue.objects.filter(issue=issue).count(),
        "modules": ModuleIssue.objects.filter(issue=issue).count(),
        "sequences": (
            IssueSequence.objects.filter(project=world.src).count(),
            IssueSequence.objects.filter(project=world.tgt).count(),
        ),
        "activities": IssueActivity.objects.filter(issue=issue).count(),
        "comments": sorted(IssueComment.objects.filter(issue=issue).values_list("id", "project_id")),
        "worklogs": sorted(IssueWorklog.objects.filter(issue=issue).values_list("id", "project_id")),
        "property_values": sorted(
            IssuePropertyValue.objects.filter(issue=issue).values_list("id", "project_id", "property_id")
        ),
    }


def seed_busy_item(world, item):
    """Give ``item`` something in every bucket the move rewrites."""
    label_issue(item, world.src_labels.bug)
    label_issue(item, world.src_labels.feature)
    assign(item, world.users.tgt_member)
    assign(item, world.users.not_in_tgt)
    subscribe(item, world.users.tgt_guest)
    subscribe(item, world.users.not_in_tgt)
    cycle = Cycle.objects.create(project=world.src, name="Sprint", owned_by=world.admin)
    CycleIssue.objects.create(cycle=cycle, issue=item, project=world.src)
    module = Module.objects.create(project=world.src, name="Mod")
    ModuleIssue.objects.create(module=module, issue=item, project=world.src)
    comment = IssueComment.objects.create(issue=item, project=world.src, comment_html="<p>c</p>", actor=world.admin)
    IssueWorklog.objects.create(
        issue=item, project=world.src, logged_by=world.mover, duration=30, logged_date=date(2026, 6, 1)
    )
    return comment


# ----------------------------------------------------------------------- mapping
@pytest.mark.contract
@pytest.mark.django_db
class TestStateMapping:
    @pytest.mark.parametrize(
        "src_key, tgt_key, match",
        [
            ("backlog", "backlog", "name"),  # "Backlog" -> "BACKLOG": case-insensitive
            ("todo", "ready", "group"),
            ("doing", "progress", "group"),  # the target's triage "Doing" is never a target
            ("review", "progress", "group"),
            ("done", "complete", "group"),
            ("cancelled", "ready", "default"),  # no cancelled group in target -> target default
        ],
    )
    def test_default_rules(self, mover_client, world, src_key, tgt_key, match):
        issue = make_issue(world.src, "Item", state=getattr(world.s, src_key), creator=world.mover)

        preview = get_preview(mover_client, world, issue)
        assert preview.status_code == status.HTTP_200_OK, preview.data
        (row,) = preview.data["states"]
        assert row["match"] == match
        assert row["target"]["id"] == str(getattr(world.t, tgt_key).id)
        assert row["work_item_ids"] == [str(issue.id)]

        response = post_move(mover_client, world, issue)
        assert response.status_code == status.HTTP_200_OK, response.data
        assert fresh(issue).state_id == getattr(world.t, tgt_key).id

    def test_state_map_override_is_honoured(self, mover_client, world, item):
        response = post_move(mover_client, world, item, state_map={str(world.s.todo.id): str(world.t.complete.id)})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert fresh(item).state_id == world.t.complete.id

    def test_state_map_for_one_state_does_not_affect_other_states(self, mover_client, world, item):
        child = make_issue(world.src, "Child", state=world.s.done, parent=item, creator=world.mover)

        response = post_move(mover_client, world, item, state_map={str(world.s.todo.id): str(world.t.progress.id)})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert fresh(item).state_id == world.t.progress.id
        assert fresh(child).state_id == world.t.complete.id  # still the default group rule

    @pytest.mark.parametrize(
        "make_bad_target",
        [
            lambda w: str(w.s.done.id),  # a state of the SOURCE project
            lambda w: str(uuid4()),  # does not exist
            lambda w: str(w.t.triage.id),  # triage is never a target
            lambda w: "not-a-uuid",
        ],
        ids=["source-state", "unknown", "triage", "garbage"],
    )
    def test_invalid_state_map_target_is_400_and_changes_nothing(self, mover_client, world, item, make_bad_target):
        seed_busy_item(world, item)
        before = snapshot(world, item)

        response = post_move(mover_client, world, item, state_map={str(world.s.todo.id): make_bad_target(world)})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_STATE_MAP"
        assert snapshot(world, item) == before

    def test_state_map_must_be_an_object(self, mover_client, world, item):
        before = snapshot(world, item)

        response = post_move(mover_client, world, item, state_map=["x"])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_STATE_MAP"
        assert snapshot(world, item) == before

    def test_target_without_states_reports_no_target_state(self, mover_client, world, item):
        empty = make_project(world.workspace, "EMP", world.admin)
        add_project_member(empty, world.mover, MEMBER)

        response = mover_client.get(preview_url(world, world.src.id, item.id), {"target_project_id": str(empty.id)})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["states"][0]["match"] == "none"
        assert response.data["states"][0]["target"] is None
        assert "NO_TARGET_STATE" in response.data["warnings"]


@pytest.mark.contract
@pytest.mark.django_db
class TestLabelMapping:
    def test_labels_matched_by_name_unmatched_dropped_no_duplicates(self, mover_client, world, item):
        # "bug" and "Bug" both map to the target's "BUG"; "feature" has no match.
        for label in (
            world.src_labels.bug,
            world.src_labels.bug_upper,
            world.src_labels.feature,
            world.src_labels.docs,
        ):
            label_issue(item, label)

        preview = get_preview(mover_client, world, item)
        kept_targets = {k["target"]["id"] for k in preview.data["labels"]["kept"]}
        assert kept_targets == {str(world.tgt_labels.bug.id), str(world.tgt_labels.docs.id)}
        assert [d["name"] for d in preview.data["labels"]["dropped"]] == ["feature"]
        assert "LABELS_DROPPED" in preview.data["warnings"]

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        rows = list(IssueLabel.objects.filter(issue=item))
        assert {r.label_id for r in rows} == {world.tgt_labels.bug.id, world.tgt_labels.docs.id}
        assert len(rows) == 2  # no duplicate for the two source labels that map to one target label
        assert {r.project_id for r in rows} == {world.tgt.id}
        assert not IssueLabel.objects.filter(issue=item, label=world.src_labels.feature).exists()

    def test_no_labels_are_auto_created_in_target(self, mover_client, world, item):
        from plane.db.models import Label

        count = Label.objects.filter(project=world.tgt).count()
        label_issue(item, world.src_labels.feature)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert Label.objects.filter(project=world.tgt).count() == count

    def test_labels_of_sibling_items_that_collapse_onto_one_target_label_all_survive(self, mover_client, world, item):
        """Root has "bug", child has "Bug": both map to target "BUG" and BOTH items keep it."""
        child = make_issue(world.src, "Child", state=world.s.todo, parent=item, creator=world.mover)
        label_issue(item, world.src_labels.bug)
        label_issue(child, world.src_labels.bug_upper)

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_ids(item) == {world.tgt_labels.bug.id}
        assert label_ids(child) == {world.tgt_labels.bug.id}

    def test_workspace_wide_label_is_kept_as_is(self, mover_client, world, item):
        from plane.db.models import Label

        wide = Label.objects.create(workspace=world.workspace, project=None, name="shared", color="#000")
        label_issue(item, wide)

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_ids(item) == {wide.id}


@pytest.mark.contract
@pytest.mark.django_db
class TestAssigneesSubscribersAndClearedLinks:
    def test_assignees_kept_only_for_target_members_and_admins(self, mover_client, world, item):
        u = world.users
        for user in (u.tgt_member, u.tgt_admin, u.tgt_guest, u.not_in_tgt, u.tgt_inactive):
            assign(item, user)

        preview = get_preview(mover_client, world, item)
        assert {a["id"] for a in preview.data["assignees"]["kept"]} == {str(u.tgt_member.id), str(u.tgt_admin.id)}
        assert {a["id"] for a in preview.data["assignees"]["dropped"]} == {
            str(u.tgt_guest.id),
            str(u.not_in_tgt.id),
            str(u.tgt_inactive.id),
        }
        assert "ASSIGNEES_DROPPED" in preview.data["warnings"]

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert assignee_ids(item) == {u.tgt_member.id, u.tgt_admin.id}
        assert set(IssueAssignee.objects.filter(issue=item).values_list("project_id", flat=True)) == {world.tgt.id}

    def test_subscribers_kept_for_any_active_target_member_dropped_otherwise(self, mover_client, world, item):
        u = world.users
        subscribe(item, u.tgt_guest)  # guests may still open the item -> kept
        subscribe(item, u.not_in_tgt)
        subscribe(item, u.tgt_inactive)

        preview = get_preview(mover_client, world, item)
        assert {s["id"] for s in preview.data["subscribers_dropped"]} == {str(u.not_in_tgt.id), str(u.tgt_inactive.id)}

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        subs = IssueSubscriber.objects.filter(issue=item)
        assert {s.subscriber_id for s in subs} == {u.tgt_guest.id}
        assert {s.project_id for s in subs} == {world.tgt.id}

    def test_cycle_and_modules_are_cleared(self, mover_client, world, item):
        cycle = Cycle.objects.create(project=world.src, name="Sprint 1", owned_by=world.admin)
        CycleIssue.objects.create(cycle=cycle, issue=item, project=world.src)
        modules = [Module.objects.create(project=world.src, name=f"Mod {i}") for i in range(2)]
        for module in modules:
            ModuleIssue.objects.create(module=module, issue=item, project=world.src)

        preview = get_preview(mover_client, world, item)
        assert preview.data["cycle"] == {"id": str(cycle.id), "name": "Sprint 1"}
        assert {m["name"] for m in preview.data["modules"]} == {"Mod 0", "Mod 1"}
        assert {"CYCLE_CLEARED", "MODULES_CLEARED"} <= set(preview.data["warnings"])

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not CycleIssue.objects.filter(issue=item).exists()
        assert not ModuleIssue.objects.filter(issue=item).exists()
        # the cycle/module themselves stay in the source project
        cycle.refresh_from_db()
        assert cycle.project_id == world.src.id


@pytest.mark.contract
@pytest.mark.django_db
class TestEstimateAndType:
    @pytest.mark.parametrize(
        "src_type, src_values, tgt_type, tgt_values, point, expected",
        [
            ("points", ["1", "2", "3"], "points", ["2", "5"], "2", "2"),  # same type + value
            ("points", ["1", "2", "3"], "points", ["2", "5"], "3", None),  # value missing in target
            ("points", ["1", "2"], "categories", ["2"], "2", None),  # same value, different type
            ("points", ["1", "2"], None, None, "2", None),  # target has no active estimate
        ],
        ids=["kept", "value-missing", "type-differs", "no-target-estimate"],
    )
    def test_estimate(self, mover_client, world, src_type, src_values, tgt_type, tgt_values, point, expected):
        _, src_points = make_estimate(world.src, src_type, src_values)
        if tgt_type:
            make_estimate(world.tgt, tgt_type, tgt_values)
        issue = make_issue(
            world.src, "Estimated", state=world.s.todo, creator=world.mover, estimate_point=src_points[point]
        )

        preview = get_preview(mover_client, world, issue)
        assert preview.data["estimate"]["source"]["value"] == point
        assert preview.data["estimate"]["action"] == ("kept" if expected else "cleared")
        assert ("ESTIMATE_CLEARED" in preview.data["warnings"]) == (expected is None)

        assert post_move(mover_client, world, issue).status_code == status.HTTP_200_OK
        moved = fresh(issue)
        if expected is None:
            assert moved.estimate_point_id is None
        else:
            assert moved.estimate_point.value == expected
            assert moved.estimate_point.project_id == world.tgt.id

    def test_item_without_estimate_reports_none(self, mover_client, world, item):
        assert get_preview(mover_client, world, item).data["estimate"]["action"] == "none"

    def test_issue_type_kept_when_enabled_in_target_else_cleared(self, mover_client, world):
        kept_type = IssueType.objects.create(workspace=world.workspace, name="Task")
        lost_type = IssueType.objects.create(workspace=world.workspace, name="Incident")
        ProjectIssueType.objects.create(project=world.src, issue_type=kept_type)
        ProjectIssueType.objects.create(project=world.src, issue_type=lost_type)
        ProjectIssueType.objects.create(project=world.tgt, issue_type=kept_type)
        a = make_issue(world.src, "A", state=world.s.todo, creator=world.mover, type=kept_type)
        b = make_issue(world.src, "B", state=world.s.todo, creator=world.mover, type=lost_type)

        assert get_preview(mover_client, world, a).data["issue_type"]["action"] == "kept"
        assert get_preview(mover_client, world, b).data["issue_type"]["action"] == "cleared"
        assert post_move(mover_client, world, a).status_code == status.HTTP_200_OK
        assert post_move(mover_client, world, b).status_code == status.HTTP_200_OK
        assert fresh(a).type_id == kept_type.id
        assert fresh(b).type_id is None


# ---------------------------------------------------------------------- identity
@pytest.mark.contract
@pytest.mark.django_db
class TestIdentity:
    def _seed(self, world, src_count=5, tgt_count=3):
        src_items = [make_issue(world.src, f"S{i}", state=world.s.todo, creator=world.mover) for i in range(src_count)]
        for i in range(tgt_count):
            make_issue(world.tgt, f"T{i}", state=world.t.ready, creator=world.mover)
        return src_items

    def test_new_sequence_is_target_max_plus_one_and_uuid_is_kept(self, mover_client, world):
        src_items = self._seed(world)
        moving = src_items[4]  # SRC-5, the highest source sequence
        assert moving.sequence_id == 5

        preview = get_preview(mover_client, world, moving)
        assert preview.data["work_item"]["identifier"] == "SRC-5"
        assert preview.data["next_identifier"] == "TGT-4"

        response = post_move(mover_client, world, moving)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data == {
            "id": str(moving.id),
            "project_id": str(world.tgt.id),
            "sequence_id": 4,
            "identifier": "TGT-4",
            "moved": [{"id": str(moving.id), "old_identifier": "SRC-5", "new_identifier": "TGT-4"}],
            "detached_sub_item_ids": [],
        }
        moved = fresh(moving)
        assert moved.id == moving.id
        assert (moved.project_id, moved.workspace_id, moved.sequence_id) == (world.tgt.id, world.workspace.id, 4)

    def test_first_item_in_empty_target_gets_sequence_one(self, mover_client, world, item):
        response = post_move(mover_client, world, item)
        assert response.data["identifier"] == "TGT-1"

    def test_issue_sequence_rows_new_in_target_old_kept_in_source(self, mover_client, world):
        src_items = self._seed(world)
        moving = src_items[4]
        src_rows = IssueSequence.objects.filter(project=world.src).count()
        tgt_rows = IssueSequence.objects.filter(project=world.tgt).count()

        assert post_move(mover_client, world, moving).status_code == status.HTTP_200_OK

        new_row = IssueSequence.objects.get(project=world.tgt, issue=moving)
        assert new_row.sequence == 4
        old_row = IssueSequence.objects.get(project=world.src, issue=moving)
        assert old_row.sequence == 5  # redirect table entry
        assert IssueSequence.objects.filter(project=world.src).count() == src_rows
        assert IssueSequence.objects.filter(project=world.tgt).count() == tgt_rows + 1

    def test_source_sequence_is_never_reused(self, mover_client, world):
        src_items = self._seed(world)
        assert post_move(mover_client, world, src_items[4]).status_code == status.HTTP_200_OK

        # SRC-5 was the highest source sequence; the next new item must be SRC-6, not SRC-5 again.
        new_item = make_issue(world.src, "After the move", state=world.s.todo, creator=world.mover)

        assert new_item.sequence_id == 6

    def test_target_numbering_continues_after_a_move(self, mover_client, world):
        src_items = self._seed(world)
        assert post_move(mover_client, world, src_items[0]).data["identifier"] == "TGT-4"
        assert post_move(mover_client, world, src_items[1]).data["identifier"] == "TGT-5"
        assert make_issue(world.tgt, "Native", state=world.t.ready).sequence_id == 6

    def test_activity_row_records_old_and_new_identifier(self, mover_client, world):
        src_items = self._seed(world)
        moving = src_items[4]

        assert post_move(mover_client, world, moving).status_code == status.HTTP_200_OK

        activity = IssueActivity.objects.get(issue=moving, field="project")
        assert activity.verb == "updated"
        assert activity.old_value == "SRC-5"
        assert activity.new_value == "TGT-4"
        assert activity.old_identifier == world.src.id
        assert activity.new_identifier == world.tgt.id
        assert activity.actor_id == world.mover.id
        assert activity.project_id == world.tgt.id
        assert "SRC-5" in activity.comment and "TGT-4" in activity.comment

    def test_round_trip_allocates_a_fresh_sequence_each_way(self, mover_client, world):
        src_items = self._seed(world)
        moving = src_items[2]
        assert post_move(mover_client, world, moving).data["identifier"] == "TGT-4"

        back = mover_client.post(
            move_url(world, world.tgt.id, moving.id), {"target_project_id": str(world.src.id)}, format="json"
        )

        assert back.status_code == status.HTTP_200_OK, back.data
        assert back.data["identifier"] == "SRC-6"
        assert fresh(moving).project_id == world.src.id
        # the intermediate identifier still points at the item
        assert IssueSequence.objects.filter(project=world.tgt, sequence=4, issue=moving).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestOldIdentifierLookup:
    @staticmethod
    def _url(world, identifier):
        return f"/api/workspaces/{world.slug}/work-items/{identifier}/"

    @pytest.fixture
    def moved(self, mover_client, world):
        for i in range(2):
            make_issue(world.tgt, f"T{i}", state=world.t.ready)
        issue = make_issue(world.src, "Moved", state=world.s.todo, creator=world.mover)
        assert issue.sequence_id == 1
        assert post_move(mover_client, world, issue).status_code == status.HTTP_200_OK
        return issue

    def test_old_identifier_returns_the_moved_item_with_new_ids(self, mover_client, world, moved):
        response = mover_client.get(self._url(world, "SRC-1"))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(response.data["id"]) == str(moved.id)
        assert str(response.data["project_id"]) == str(world.tgt.id)
        assert response.data["sequence_id"] == 3

    def test_new_identifier_resolves_too(self, mover_client, world, moved):
        response = mover_client.get(self._url(world, "TGT-3"))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(response.data["id"]) == str(moved.id)

    def test_old_identifiers_survive_a_round_trip(self, mover_client, world, moved):
        back = mover_client.post(
            move_url(world, world.tgt.id, moved.id), {"target_project_id": str(world.src.id)}, format="json"
        )
        assert back.status_code == status.HTTP_200_OK, back.data
        assert back.data["identifier"] == "SRC-2"

        for identifier in ("SRC-1", "TGT-3", "SRC-2"):
            response = mover_client.get(self._url(world, identifier))
            assert response.status_code == status.HTTP_200_OK, (identifier, response.data)
            assert str(response.data["id"]) == str(moved.id)
            assert str(response.data["project_id"]) == str(world.src.id)
            assert response.data["sequence_id"] == 2

    def test_old_identifiers_survive_two_hops(self, mover_client, world, moved):
        third = make_project(world.workspace, "THR", world.admin)
        make_state(third, "Todo", "unstarted", default=True)
        add_project_member(third, world.mover, MEMBER)
        hop = mover_client.post(
            move_url(world, world.tgt.id, moved.id), {"target_project_id": str(third.id)}, format="json"
        )
        assert hop.status_code == status.HTTP_200_OK, hop.data

        for identifier in ("SRC-1", "TGT-3", "THR-1"):
            response = mover_client.get(self._url(world, identifier))
            assert response.status_code == status.HTTP_200_OK, (identifier, response.data)
            assert str(response.data["id"]) == str(moved.id)
            assert str(response.data["project_id"]) == str(third.id)

    def test_old_identifier_works_for_a_user_who_is_only_in_the_target(self, world, moved):
        response = client_for(world.users.tgt_only).get(self._url(world, "SRC-1"))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(response.data["id"]) == str(moved.id)

    def test_old_identifier_is_not_found_for_a_user_not_in_the_target(self, world, moved):
        # member of the source but not of the project the item lives in now: same answer
        # as an identifier that never existed (FORK: security review, Oct 2026)
        response = client_for(world.users.not_in_tgt).get(self._url(world, "SRC-1"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unknown_old_sequence_is_404(self, mover_client, world, moved):
        assert mover_client.get(self._url(world, "SRC-99")).status_code == status.HTTP_404_NOT_FOUND

    def test_deleted_moved_item_no_longer_resolves(self, mover_client, world, moved):
        Issue.objects.get(pk=moved.pk).delete()

        assert mover_client.get(self._url(world, "SRC-1")).status_code == status.HTTP_404_NOT_FOUND

    def test_live_item_with_same_sequence_is_not_shadowed_by_the_redirect(self, mover_client, world, moved):
        """SRC-1 now only exists as a redirect; a live SRC item keeps its own number."""
        live = make_issue(world.src, "Live", state=world.s.todo, creator=world.mover)
        assert live.sequence_id == 2

        response = mover_client.get(self._url(world, "SRC-2"))

        assert response.status_code == status.HTTP_200_OK
        assert str(response.data["id"]) == str(live.id)


# --------------------------------------------------------------------- sub-items
@pytest.mark.contract
@pytest.mark.django_db
class TestSubItems:
    @pytest.fixture
    def tree(self, world):
        mover = world.mover
        parent = make_issue(world.src, "Parent", state=world.s.todo, creator=mover)
        root = make_issue(world.src, "Root", state=world.s.doing, parent=parent, creator=mover)
        c1 = make_issue(world.src, "Child 1", state=world.s.done, parent=root, creator=mover)
        c2 = make_issue(world.src, "Child 2", state=world.s.todo, parent=root, creator=mover)
        grand = make_issue(world.src, "Grandchild", state=world.s.review, parent=c1, creator=mover)
        return {"parent": parent, "root": root, "c1": c1, "c2": c2, "grand": grand}

    def test_move_mode_moves_the_whole_subtree_with_fresh_sequences(self, mover_client, world, tree):
        for i in range(2):
            make_issue(world.tgt, f"T{i}", state=world.t.ready)

        preview = get_preview(mover_client, world, tree["root"])
        assert preview.data["sub_items_mode"] == "move"
        assert {s["identifier"] for s in preview.data["sub_items"]} == {"SRC-3", "SRC-4", "SRC-5"}
        assert preview.data["parent"]["identifier"] == "SRC-1"
        assert "PARENT_DETACHED" in preview.data["warnings"]

        response = post_move(mover_client, world, tree["root"])  # default is "move"

        assert response.status_code == status.HTTP_200_OK, response.data
        assert [m["id"] for m in response.data["moved"]] == [str(tree[k].id) for k in ("root", "c1", "c2", "grand")]
        assert [m["new_identifier"] for m in response.data["moved"]] == ["TGT-3", "TGT-4", "TGT-5", "TGT-6"]
        assert response.data["detached_sub_item_ids"] == []
        moved = {k: fresh(v) for k, v in tree.items()}
        for key in ("root", "c1", "c2", "grand"):
            assert moved[key].project_id == world.tgt.id
        assert len({moved[k].sequence_id for k in ("root", "c1", "c2", "grand")}) == 4
        # parent links inside the subtree are intact; the root lost its (left-behind) parent
        assert moved["root"].parent_id is None
        assert moved["c1"].parent_id == tree["root"].id
        assert moved["c2"].parent_id == tree["root"].id
        assert moved["grand"].parent_id == tree["c1"].id
        # the parent stays where it was
        assert moved["parent"].project_id == world.src.id
        # every moved item has its own project activity and its own source IssueSequence kept
        for key in ("root", "c1", "c2", "grand"):
            assert IssueActivity.objects.filter(issue=tree[key], field="project").count() == 1
            assert IssueSequence.objects.filter(project=world.src, issue=tree[key]).count() == 1
            assert IssueSequence.objects.filter(project=world.tgt, issue=tree[key]).count() == 1
        # each item's state was mapped on its own
        assert moved["c1"].state_id == world.t.complete.id
        assert moved["grand"].state_id == world.t.progress.id
        assert moved["c2"].state_id == world.t.ready.id

    def test_root_losing_its_parent_is_recorded_as_activity(self, mover_client, world, tree):
        assert post_move(mover_client, world, tree["root"]).status_code == status.HTTP_200_OK

        activity = IssueActivity.objects.get(issue=tree["root"], field="parent")
        assert activity.old_value == "SRC-1"
        assert activity.new_value == ""
        assert activity.old_identifier == tree["parent"].id

    def test_detach_mode_leaves_children_in_source_with_parent_cleared(self, mover_client, world, tree):
        preview = get_preview(mover_client, world, tree["root"], sub_items="detach")
        assert preview.data["sub_items_mode"] == "detach"
        assert len(preview.data["sub_items"]) == 3  # whole subtree is listed
        assert "SUB_ITEMS_DETACHED" in preview.data["warnings"]

        response = post_move(mover_client, world, tree["root"], sub_items="detach")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert [m["id"] for m in response.data["moved"]] == [str(tree["root"].id)]
        assert set(response.data["detached_sub_item_ids"]) == {str(tree["c1"].id), str(tree["c2"].id)}
        assert fresh(tree["root"]).project_id == world.tgt.id
        for key in ("c1", "c2", "grand"):
            assert fresh(tree[key]).project_id == world.src.id
        assert fresh(tree["c1"]).parent_id is None
        assert fresh(tree["c2"]).parent_id is None
        assert fresh(tree["grand"]).parent_id == tree["c1"].id  # deeper links are untouched
        for key in ("c1", "c2"):
            activity = IssueActivity.objects.get(issue=tree[key], field="parent")
            assert activity.project_id == world.src.id
            assert activity.old_value == response.data["identifier"]
        assert not IssueActivity.objects.filter(issue=tree["grand"], field="parent").exists()

    def test_item_whose_parent_stays_has_its_parent_cleared(self, mover_client, world, tree):
        response = post_move(mover_client, world, tree["root"])

        assert response.status_code == status.HTTP_200_OK
        assert fresh(tree["root"]).parent_id is None
        assert fresh(tree["parent"]).project_id == world.src.id

    def test_leaf_item_move_does_not_touch_siblings(self, mover_client, world, tree):
        response = post_move(mover_client, world, tree["c2"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert fresh(tree["c2"]).parent_id is None
        assert fresh(tree["c1"]).project_id == world.src.id
        assert fresh(tree["c1"]).parent_id == tree["root"].id

    def test_invalid_sub_items_mode_is_400(self, mover_client, world, tree):
        before = snapshot(world, tree["root"])

        response = post_move(mover_client, world, tree["root"], sub_items="everything")
        preview = get_preview(mover_client, world, tree["root"], sub_items="everything")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_SUB_ITEMS"
        assert preview.status_code == status.HTTP_400_BAD_REQUEST
        assert snapshot(world, tree["root"]) == before

    def test_soft_deleted_child_is_not_moved(self, mover_client, world, tree):
        tree["c2"].delete()

        response = post_move(mover_client, world, tree["root"])

        assert response.status_code == status.HTTP_200_OK
        assert {m["id"] for m in response.data["moved"]} == {str(tree[k].id) for k in ("root", "c1", "grand")}


# ------------------------------------------------------------------ carried data
@pytest.mark.contract
@pytest.mark.django_db
class TestCarriedData:
    @pytest.fixture
    def loaded(self, world, item):
        admin, other = world.admin, make_issue(world.src, "Other", state=world.s.todo, creator=world.mover)
        comment = IssueComment.objects.create(issue=item, project=world.src, comment_html="<p>c</p>", actor=admin)
        reply = IssueComment.objects.create(
            issue=item, project=world.src, comment_html="<p>r</p>", actor=admin, parent=comment
        )
        comment.refresh_from_db()
        reply.refresh_from_db()

        def asset(**kw):
            return FileAsset.objects.create(
                attributes={"name": "f.pdf", "type": "application/pdf", "size": 1},
                asset=f"{world.workspace.id}/{uuid4().hex}-f.pdf",
                size=1,
                workspace=world.workspace,
                project=world.src,
                is_uploaded=True,
                **kw,
            )

        objs = {
            "comment": comment,
            "reply": reply,
            "comment_reaction": CommentReaction.objects.create(
                comment=comment, actor=admin, reaction="x", project=world.src
            ),
            "comment_asset": asset(comment=comment, entity_type=FileAsset.EntityTypeContext.COMMENT_DESCRIPTION),
            "link": IssueLink.objects.create(issue=item, project=world.src, url="https://example.com", title="Ex"),
            "attachment_asset": asset(issue=item, entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT),
            "description_asset": asset(issue=item, entity_type=FileAsset.EntityTypeContext.ISSUE_DESCRIPTION),
            "legacy_attachment": IssueAttachment.objects.create(
                issue=item, project=world.src, asset=f"{world.workspace.id}/legacy.pdf", attributes={}
            ),
            "desc_version": IssueDescriptionVersion.objects.create(issue=item, project=world.src, owned_by=admin),
            "version": IssueVersion.objects.create(issue=item, project=world.src, name="Item", owned_by=admin),
            "history": IssueActivity.objects.create(
                issue=item, project=world.src, verb="created", field="state", actor=admin
            ),
            "relation_out": IssueRelation.objects.create(
                issue=item, related_issue=other, relation_type="blocked_by", project=world.src
            ),
            "blocker": IssueBlocker.objects.create(block=item, blocked_by=other, project=world.src),
            "mention": IssueMention.objects.create(issue=item, mention=admin, project=world.src),
            "vote": IssueVote.objects.create(issue=item, actor=admin, vote=1, project=world.src),
            "reaction": IssueReaction.objects.create(issue=item, actor=admin, reaction="x", project=world.src),
            "notification": Notification.objects.create(
                workspace=world.workspace,
                project=world.src,
                entity_name="issue",
                entity_identifier=item.id,
                title="t",
                sender="issue",
                receiver=admin,
                triggered_by=world.mover,
            ),
            "recent": UserRecentVisit.objects.create(
                workspace=world.workspace,
                project=world.src,
                entity_name="issue",
                entity_identifier=item.id,
                user=admin,
            ),
        }
        objs["relation_in"] = IssueRelation.objects.create(
            issue=other, related_issue=item, relation_type="relates_to", project=world.src
        )
        objs["other"] = other
        return objs

    def test_everything_follows_the_item_to_the_target_project(self, mover_client, world, item, loaded):
        preview = get_preview(mover_client, world, item)
        assert preview.data["carried"] == {"comments": 2, "attachments": 1, "links": 1, "relations": 2}

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        follow_the_item = [
            "comment",
            "reply",
            "comment_reaction",
            "comment_asset",
            "link",
            "attachment_asset",
            "description_asset",
            "legacy_attachment",
            "desc_version",
            "version",
            "history",
            "relation_out",
            "blocker",
            "mention",
            "vote",
            "reaction",
            "notification",
            "recent",
        ]
        wrong = {
            name: str(type(loaded[name]).objects.get(pk=loaded[name].pk).project_id)
            for name in follow_the_item
            if type(loaded[name]).objects.get(pk=loaded[name].pk).project_id != world.tgt.id
        }
        assert wrong == {}

    def test_activity_history_is_preserved_and_extended(self, mover_client, world, item, loaded):
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        fields = sorted(IssueActivity.objects.filter(issue=item).values_list("field", flat=True))
        assert fields == ["project", "state"]
        assert {a.project_id for a in IssueActivity.objects.filter(issue=item)} == {world.tgt.id}

    def test_comment_description_rows_follow_the_comment(self, mover_client, world, item, loaded):
        """The comment's Description row carries a project FK too; if it were left
        behind, deleting the source project would cascade to the moved comment."""
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        for key in ("comment", "reply"):
            comment = IssueComment.objects.get(pk=loaded[key].pk)
            assert comment.description_id is not None
            assert Description.objects.get(pk=comment.description_id).project_id == world.tgt.id

    def test_replies_keep_their_parent_comment(self, mover_client, world, item, loaded):
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        assert IssueComment.objects.get(pk=loaded["reply"].pk).parent_id == loaded["comment"].id

    def test_relations_keep_working_in_both_directions(self, mover_client, world, item, loaded):
        other = loaded["other"]
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        out_row = IssueRelation.objects.get(pk=loaded["relation_out"].pk)
        in_row = IssueRelation.objects.get(pk=loaded["relation_in"].pk)
        assert (out_row.issue_id, out_row.related_issue_id) == (item.id, other.id)
        assert (in_row.issue_id, in_row.related_issue_id) == (other.id, item.id)
        # the item's own side moved; the row owned by the item that stayed behind did not
        assert out_row.project_id == world.tgt.id
        assert in_row.project_id == world.src.id
        assert IssueRelation.objects.filter(Q(issue=item) | Q(related_issue=item)).count() == 2

    def test_uuid_based_inbound_references_still_resolve(self, mover_client, world, item, loaded):
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        # the other item's relation still points at the (same-UUID) moved item
        assert IssueRelation.objects.get(pk=loaded["relation_in"].pk).related_issue.project_id == world.tgt.id

    def test_files_are_not_copied_or_rekeyed(self, mover_client, world, item, loaded):
        before = {
            "attachment": loaded["attachment_asset"].asset.name,
            "legacy": loaded["legacy_attachment"].asset.name,
        }
        count = FileAsset.objects.count()

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        assert FileAsset.objects.count() == count
        assert FileAsset.objects.get(pk=loaded["attachment_asset"].pk).asset.name == before["attachment"]
        assert IssueAttachment.objects.get(pk=loaded["legacy_attachment"].pk).asset.name == before["legacy"]


# --------------------------------------------------------------------- worklogs
@pytest.mark.contract
@pytest.mark.django_db
class TestWorklogs:
    @pytest.fixture
    def logged(self, world, item):
        timesheet = Timesheet.objects.create(
            workspace=world.workspace,
            user=world.mover,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 7),
            status="approved",
        )
        approved = IssueWorklog.objects.create(
            issue=item,
            project=world.src,
            logged_by=world.mover,
            duration=90,
            logged_date=date(2026, 6, 2),
            billable_rate=Decimal("125.50"),
            currency="CAD",
            approval_status="approved",
            is_locked=True,
            approved_by=world.admin,
            approved_at=timezone.now(),
            timesheet=timesheet,
        )
        draft = IssueWorklog.objects.create(
            issue=item,
            project=world.src,
            logged_by=world.mover,
            duration=45,
            logged_date=date(2026, 6, 3),
            is_billable=False,
        )
        timer = WorklogTimer.objects.create(
            issue=item, project=world.src, user=world.mover, started_at=timezone.now() - timedelta(minutes=5)
        )
        return {"approved": approved, "draft": draft, "timer": timer, "timesheet": timesheet}

    def test_worklogs_and_timer_move_with_entries_untouched(self, mover_client, world, item, logged):
        before = {
            key: IssueWorklog.objects.filter(pk=logged[key].pk)
            .values(
                "duration",
                "billable_rate",
                "currency",
                "approval_status",
                "is_locked",
                "is_billable",
                "logged_by_id",
                "logged_date",
                "approved_by_id",
                "approved_at",
                "timesheet_id",
            )
            .get()
            for key in ("approved", "draft")
        }

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        for key in ("approved", "draft"):
            row = IssueWorklog.objects.get(pk=logged[key].pk)
            assert row.project_id == world.tgt.id
            assert row.issue_id == item.id
            after = IssueWorklog.objects.filter(pk=row.pk).values(*before[key].keys()).get()
            assert after == before[key]
        assert IssueWorklog.objects.get(pk=logged["approved"].pk).approval_status == "approved"
        assert IssueWorklog.objects.get(pk=logged["approved"].pk).timesheet_id == logged["timesheet"].id
        timer = WorklogTimer.objects.get(pk=logged["timer"].pk)
        assert timer.project_id == world.tgt.id
        assert timer.started_at == logged["timer"].started_at

    def test_preview_counts_and_locked_count(self, mover_client, world, item, logged):
        Project.objects.filter(pk=world.tgt.pk).update(is_time_tracking_enabled=True)

        worklogs = get_preview(mover_client, world, item).data["worklogs"]

        assert worklogs == {
            "count": 2,
            "total_minutes": 135,
            "locked_count": 1,
            "running_timers": 1,
            "target_time_tracking_enabled": True,
            "client_changes": False,
        }

    def test_approved_but_unlocked_entry_counts_as_locked(self, mover_client, world, item):
        IssueWorklog.objects.create(
            issue=item,
            project=world.src,
            logged_by=world.mover,
            duration=10,
            logged_date=date(2026, 6, 2),
            approval_status="approved",
            is_locked=False,
        )

        data = get_preview(mover_client, world, item).data

        assert data["worklogs"]["locked_count"] == 1
        assert "LOCKED_WORKLOGS" in data["warnings"]

    def test_warning_when_target_has_time_tracking_off(self, mover_client, world, item, logged):
        Project.objects.filter(pk=world.src.pk).update(is_time_tracking_enabled=True)
        Project.objects.filter(pk=world.tgt.pk).update(is_time_tracking_enabled=False)

        data = get_preview(mover_client, world, item).data

        assert data["worklogs"]["target_time_tracking_enabled"] is False
        assert "TIME_TRACKING_DISABLED_IN_TARGET" in data["warnings"]
        assert "LOCKED_WORKLOGS" in data["warnings"]  # the approved entry
        assert "CLIENT_CHANGES" not in data["warnings"]

    def test_no_time_warnings_without_worklogs(self, mover_client, world, item):
        Project.objects.filter(pk=world.tgt.pk).update(is_time_tracking_enabled=False)

        data = get_preview(mover_client, world, item).data

        assert data["worklogs"]["count"] == 0
        assert {"TIME_TRACKING_DISABLED_IN_TARGET", "LOCKED_WORKLOGS", "CLIENT_CHANGES"}.isdisjoint(data["warnings"])

    def test_warning_when_target_bills_a_different_client(self, mover_client, world, item, logged):
        acme = Client.objects.create(workspace=world.workspace, name="Acme")
        globex = Client.objects.create(workspace=world.workspace, name="Globex")
        Project.objects.filter(pk=world.src.pk).update(client=acme)
        Project.objects.filter(pk=world.tgt.pk).update(client=globex)

        data = get_preview(mover_client, world, item).data

        assert data["worklogs"]["client_changes"] is True
        assert "CLIENT_CHANGES" in data["warnings"]

    def test_no_client_warning_when_both_projects_share_the_client(self, mover_client, world, item, logged):
        acme = Client.objects.create(workspace=world.workspace, name="Acme")
        Project.objects.filter(pk__in=[world.src.pk, world.tgt.pk]).update(client=acme)

        data = get_preview(mover_client, world, item).data

        assert data["worklogs"]["client_changes"] is False
        assert "CLIENT_CHANGES" not in data["warnings"]

    def test_client_warning_when_only_the_target_has_a_client(self, mover_client, world, item, logged):
        acme = Client.objects.create(workspace=world.workspace, name="Acme")
        Project.objects.filter(pk=world.tgt.pk).update(client=acme)

        assert "CLIENT_CHANGES" in get_preview(mover_client, world, item).data["warnings"]

    def test_running_timer_alone_triggers_time_tracking_warning(self, mover_client, world, item):
        WorklogTimer.objects.create(issue=item, project=world.src, user=world.mover, started_at=timezone.now())
        Project.objects.filter(pk=world.tgt.pk).update(is_time_tracking_enabled=False)

        assert "TIME_TRACKING_DISABLED_IN_TARGET" in get_preview(mover_client, world, item).data["warnings"]

    def test_subtree_worklogs_are_counted_and_moved(self, mover_client, world, item):
        child = make_issue(world.src, "Child", state=world.s.todo, parent=item, creator=world.mover)
        log = IssueWorklog.objects.create(
            issue=child, project=world.src, logged_by=world.mover, duration=20, logged_date=date(2026, 6, 2)
        )

        assert get_preview(mover_client, world, item).data["worklogs"]["count"] == 1
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert IssueWorklog.objects.get(pk=log.pk).project_id == world.tgt.id


# ------------------------------------------------------------- custom properties
def _prop(project, name, ptype, **kw):
    return IssueProperty.objects.create(
        workspace=project.workspace,
        project=project,
        name=name,
        display_name=name.replace("_", " ").title(),
        property_type=ptype,
        **kw,
    )


def _option(prop, name):
    return IssuePropertyOption.objects.create(workspace=prop.workspace, project=prop.project, property=prop, name=name)


def _value(item, prop, **kw):
    return IssuePropertyValue.objects.create(
        workspace=item.workspace, project=item.project, issue=item, property=prop, **kw
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestCustomProperties:
    @pytest.fixture
    def props(self, world, item):
        src_stage = _prop(world.src, "stage", "OPTION")
        src_low, src_high = _option(src_stage, "Low"), _option(src_stage, "High")
        tgt_stage = _prop(world.tgt, "stage", "OPTION")
        tgt_low = _option(tgt_stage, "low")  # case-insensitive match; no "High" in target

        src_days = _prop(world.src, "days", "DECIMAL")
        tgt_days = _prop(world.tgt, "days", "DECIMAL")

        src_notes = _prop(world.src, "notes", "TEXT")  # missing in target
        src_team = _prop(world.src, "team", "TEXT")  # same name, different type in target
        _prop(world.tgt, "team", "DECIMAL")
        return locals()

    def test_values_mapped_by_name_and_type(self, mover_client, world, item, props):
        days = _value(item, props["src_days"], value_decimal=3.5)

        preview = get_preview(mover_client, world, item)
        mapped = {m["source"]["id"]: m for m in preview.data["custom_properties"]["mapped"]}
        assert mapped[str(props["src_days"].id)]["target"]["id"] == str(props["tgt_days"].id)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        days.refresh_from_db()
        assert days.property_id == props["tgt_days"].id
        assert days.project_id == world.tgt.id
        assert days.value_decimal == 3.5
        assert days.issue_id == item.id

    def test_option_value_mapped_by_option_name(self, mover_client, world, item, props):
        value = _value(item, props["src_stage"], value_option=props["src_low"])

        preview = get_preview(mover_client, world, item)
        (entry,) = [
            m for m in preview.data["custom_properties"]["mapped"] if m["source"]["id"] == str(props["src_stage"].id)
        ]
        assert entry["dropped_options"] == []
        assert "PROPERTIES_DROPPED" not in preview.data["warnings"]

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        value.refresh_from_db()
        assert value.property_id == props["tgt_stage"].id
        assert value.value_option_id == props["tgt_low"].id
        assert value.project_id == world.tgt.id

    def test_unmatched_option_is_dropped_and_listed(self, mover_client, world, item, props):
        value = _value(item, props["src_stage"], value_option=props["src_high"])

        preview = get_preview(mover_client, world, item)
        (entry,) = [
            m for m in preview.data["custom_properties"]["mapped"] if m["source"]["id"] == str(props["src_stage"].id)
        ]
        assert entry["dropped_options"] == ["High"]
        assert "PROPERTIES_DROPPED" in preview.data["warnings"]

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not IssuePropertyValue.objects.filter(pk=value.pk).exists()

    def test_property_missing_in_target_is_dropped_and_listed(self, mover_client, world, item, props):
        value = _value(item, props["src_notes"], value_text="remember")

        preview = get_preview(mover_client, world, item)
        assert [p["id"] for p in preview.data["custom_properties"]["dropped"]] == [str(props["src_notes"].id)]
        assert "PROPERTIES_DROPPED" in preview.data["warnings"]

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not IssuePropertyValue.objects.filter(pk=value.pk).exists()

    def test_same_name_different_type_is_not_a_match(self, mover_client, world, item, props):
        value = _value(item, props["src_team"], value_text="blue")

        preview = get_preview(mover_client, world, item)
        assert [p["id"] for p in preview.data["custom_properties"]["dropped"]] == [str(props["src_team"].id)]

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not IssuePropertyValue.objects.filter(pk=value.pk).exists()

    def test_inactive_target_property_is_not_a_match(self, mover_client, world, item, props):
        IssueProperty.objects.filter(pk=props["tgt_days"].pk).update(is_active=False)
        value = _value(item, props["src_days"], value_decimal=1)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not IssuePropertyValue.objects.filter(pk=value.pk).exists()

    def test_target_property_restricted_to_another_issue_type_is_not_a_match(self, mover_client, world, item, props):
        issue_type = IssueType.objects.create(workspace=world.workspace, name="Epic")
        IssueProperty.objects.filter(pk=props["tgt_days"].pk).update(issue_type=issue_type)
        value = _value(item, props["src_days"], value_decimal=1)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        assert not IssuePropertyValue.objects.filter(pk=value.pk).exists()

    def test_user_relation_values_keep_only_target_members(self, mover_client, world, item):
        src_owner = _prop(world.src, "owners", "RELATION", relation_type="USER", is_multi=True)
        tgt_owner = _prop(world.tgt, "owners", "RELATION", relation_type="USER", is_multi=True)
        keep = _value(item, src_owner, value_uuid=world.users.tgt_guest.id)  # any active member may stay
        drop = _value(item, src_owner, value_uuid=world.users.not_in_tgt.id)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK
        keep.refresh_from_db()
        assert keep.property_id == tgt_owner.id
        assert not IssuePropertyValue.objects.filter(pk=drop.pk).exists()

    def test_item_without_values_has_empty_property_sections(self, mover_client, world, item, props):
        data = get_preview(mover_client, world, item).data

        assert data["custom_properties"] == {"mapped": [], "dropped": []}


# ------------------------------------------------------------------- permissions
@pytest.mark.contract
@pytest.mark.django_db
class TestPermissions:
    def _user(self, world, ws_role=MEMBER, src_role=None, tgt_role=None):
        user = make_user("perm")
        add_workspace_member(world.workspace, user, ws_role)
        if src_role:
            add_project_member(world.src, user, src_role)
        if tgt_role:
            add_project_member(world.tgt, user, tgt_role)
        return user

    def test_guest_in_source_cannot_move_even_their_own_item(self, world):
        guest = self._user(world, src_role=GUEST, tgt_role=MEMBER)
        own = make_issue(world.src, "Mine", state=world.s.todo, creator=guest)
        before = snapshot(world, own)

        move = post_move(client_for(guest), world, own)
        preview = get_preview(client_for(guest), world, own)

        assert move.status_code == status.HTTP_403_FORBIDDEN
        assert preview.status_code == status.HTTP_403_FORBIDDEN
        assert snapshot(world, own) == before

    def test_member_of_source_but_guest_in_target_is_forbidden(self, world, item):
        client = client_for(world.users.tgt_guest)
        before = snapshot(world, item)

        move = post_move(client, world, item)
        preview = get_preview(client, world, item)

        assert move.status_code == status.HTTP_403_FORBIDDEN
        assert move.data["code"] == "FORBIDDEN_TARGET"
        assert preview.status_code == status.HTTP_403_FORBIDDEN
        assert snapshot(world, item) == before

    def test_member_of_source_but_not_in_target_is_forbidden(self, world, item):
        move = post_move(client_for(world.users.not_in_tgt), world, item)

        assert move.status_code == status.HTTP_403_FORBIDDEN
        assert move.data["code"] == "FORBIDDEN_TARGET"
        assert fresh(item).project_id == world.src.id

    def test_inactive_target_membership_is_forbidden(self, world, item):
        assert post_move(client_for(world.users.tgt_inactive), world, item).status_code == status.HTTP_403_FORBIDDEN

    def test_user_outside_the_source_project_is_forbidden(self, world, item):
        outsider = self._user(world, tgt_role=MEMBER)

        assert post_move(client_for(outsider), world, item).status_code == status.HTTP_403_FORBIDDEN
        assert get_preview(client_for(outsider), world, item).status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_request_is_rejected(self, world, item, api_client):
        response = post_move(api_client, world, item)

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        assert fresh(item).project_id == world.src.id

    @pytest.mark.parametrize("src_role, tgt_role", [(ADMIN, ADMIN), (ADMIN, MEMBER), (MEMBER, ADMIN), (MEMBER, MEMBER)])
    def test_admin_and_member_roles_can_move(self, world, item, src_role, tgt_role):
        user = self._user(world, src_role=src_role, tgt_role=tgt_role)

        response = post_move(client_for(user), world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert fresh(item).project_id == world.tgt.id

    def test_workspace_admin_who_is_a_project_guest_can_move(self, world, item):
        ws_admin = self._user(world, ws_role=ADMIN, src_role=GUEST, tgt_role=GUEST)

        response = post_move(client_for(ws_admin), world, item)

        assert response.status_code == status.HTTP_200_OK, response.data

    def test_workspace_admin_not_in_target_project_is_forbidden(self, world, item):
        ws_admin = self._user(world, ws_role=ADMIN, src_role=GUEST)

        response = post_move(client_for(ws_admin), world, item)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["code"] == "FORBIDDEN_TARGET"

    def test_target_in_another_workspace_is_400(self, mover_client, world, item):
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=world.admin)
        foreign = make_project(other_ws, "FOR", world.admin)
        make_state(foreign, "Todo", "unstarted", default=True)
        add_workspace_member(other_ws, world.mover, MEMBER)
        add_project_member(foreign, world.mover, ADMIN)

        move = post_move(mover_client, world, item, target_project_id=str(foreign.id))
        preview = get_preview(mover_client, world, item, target_project_id=str(foreign.id))

        assert move.status_code == status.HTTP_400_BAD_REQUEST
        assert move.data["code"] == "INVALID_TARGET"
        assert preview.status_code == status.HTTP_400_BAD_REQUEST
        assert fresh(item).project_id == world.src.id

    @pytest.mark.parametrize("target", [None, "", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_missing_or_unknown_target_is_400(self, mover_client, world, item, target):
        body = {} if target is None else {"target_project_id": target}
        response = mover_client.post(move_url(world, world.src.id, item.id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_TARGET"

    def test_same_project_is_400(self, mover_client, world, item):
        response = post_move(mover_client, world, item, target_project_id=str(world.src.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "SAME_PROJECT"

    def test_archived_target_is_400(self, mover_client, world, item):
        Project.objects.filter(pk=world.tgt.pk).update(archived_at=timezone.now())

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "TARGET_ARCHIVED"

    def test_archived_item_is_400(self, mover_client, world, item):
        Issue.objects.filter(pk=item.pk).update(archived_at=date.today())

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "ITEM_ARCHIVED"

    def test_draft_flagged_item_is_400(self, mover_client, world, item):
        Issue.objects.filter(pk=item.pk).update(is_draft=True)

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_ITEM"

    @pytest.mark.parametrize("intake_status", [-2, 0], ids=["pending", "snoozed"])
    def test_pending_intake_item_is_400(self, mover_client, world, item, intake_status):
        intake = Intake.objects.create(project=world.src, name="Intake")
        IntakeIssue.objects.create(intake=intake, issue=item, project=world.src, status=intake_status)

        move = post_move(mover_client, world, item)
        preview = get_preview(mover_client, world, item)

        assert move.status_code == status.HTTP_400_BAD_REQUEST
        assert move.data["code"] == "ITEM_IN_INTAKE"
        assert preview.data["code"] == "ITEM_IN_INTAKE"
        assert fresh(item).project_id == world.src.id

    def test_item_in_triage_state_is_400(self, mover_client, world):
        triage = make_state(world.src, "Triage", "triage", is_triage=True)
        triaged = make_issue(world.src, "Triaged", state=triage, creator=world.mover)

        response = post_move(mover_client, world, triaged)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "ITEM_IN_INTAKE"

    def test_accepted_intake_item_can_move_and_its_intake_record_stays(self, mover_client, world, item):
        intake = Intake.objects.create(project=world.src, name="Intake")
        record = IntakeIssue.objects.create(intake=intake, issue=item, project=world.src, status=1)

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        record.refresh_from_db()
        assert record.project_id == world.src.id
        assert record.issue_id == item.id

    def test_unknown_work_item_is_404(self, mover_client, world):
        response = mover_client.post(
            move_url(world, world.src.id, uuid4()), {"target_project_id": str(world.tgt.id)}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_work_item_under_the_wrong_project_in_the_url_is_404(self, mover_client, world, item):
        response = mover_client.post(
            move_url(world, world.tgt.id, item.id), {"target_project_id": str(world.src.id)}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert fresh(item).project_id == world.src.id


# ----------------------------------------------------------------------- preview
@pytest.mark.contract
@pytest.mark.django_db
class TestPreview:
    def test_preview_is_read_only(self, mover_client, world, item):
        seed_busy_item(world, item)
        child = make_issue(world.src, "Child", state=world.s.done, parent=item, creator=world.mover)
        before = (snapshot(world, item), snapshot(world, child), Issue.objects.count())

        for sub_items in ("move", "detach"):
            assert get_preview(mover_client, world, item, sub_items=sub_items).status_code == status.HTTP_200_OK

        assert (snapshot(world, item), snapshot(world, child), Issue.objects.count()) == before

    def test_preview_shape(self, mover_client, world, item):
        label_issue(item, world.src_labels.bug)
        label_issue(item, world.src_labels.feature)
        assign(item, world.users.tgt_member)
        make_issue(world.tgt, "T1", state=world.t.ready)

        data = get_preview(mover_client, world, item).data

        assert set(data) == {
            "source_project",
            "target_project",
            "work_item",
            "next_identifier",
            "sub_items_mode",
            "sub_items",
            "parent",
            "states",
            "target_states",
            "labels",
            "assignees",
            "subscribers_dropped",
            "cycle",
            "modules",
            "estimate",
            "issue_type",
            "custom_properties",
            "worklogs",
            "carried",
            "warnings",
        }
        assert data["source_project"] == {"id": str(world.src.id), "name": world.src.name, "identifier": "SRC"}
        assert data["target_project"]["identifier"] == "TGT"
        assert data["work_item"] == {"id": str(item.id), "name": "Item", "identifier": "SRC-1"}
        assert data["next_identifier"] == "TGT-2"
        assert data["sub_items_mode"] == "move"
        assert data["parent"] is None
        assert data["cycle"] is None and data["modules"] == []
        (state_row,) = data["states"]
        assert set(state_row) == {"source", "target", "match", "work_item_ids"}
        assert set(state_row["source"]) == {"id", "name", "group", "color"}
        # triage states never appear as targets
        assert {s["name"] for s in data["target_states"]} == {"BACKLOG", "Ready", "In Progress", "Complete"}
        assert [d["name"] for d in data["labels"]["dropped"]] == ["feature"]
        assert data["labels"]["kept"][0]["target"]["id"] == str(world.tgt_labels.bug.id)
        assert [a["id"] for a in data["assignees"]["kept"]] == [str(world.users.tgt_member.id)]
        assert set(data["assignees"]["kept"][0]) == {"id", "display_name", "avatar_url"}
        assert data["warnings"] == ["LABELS_DROPPED"]

    def test_next_identifier_is_indicative_and_does_not_reserve_a_number(self, mover_client, world, item):
        assert get_preview(mover_client, world, item).data["next_identifier"] == "TGT-1"
        assert get_preview(mover_client, world, item).data["next_identifier"] == "TGT-1"
        assert IssueSequence.objects.filter(project=world.tgt).count() == 0

    def test_preview_groups_sub_items_by_source_state(self, mover_client, world, item):
        c1 = make_issue(world.src, "C1", state=world.s.todo, parent=item, creator=world.mover)
        c2 = make_issue(world.src, "C2", state=world.s.done, parent=item, creator=world.mover)

        rows = get_preview(mover_client, world, item).data["states"]

        by_source = {r["source"]["name"]: r for r in rows}
        assert set(by_source["Todo"]["work_item_ids"]) == {str(item.id), str(c1.id)}
        assert by_source["Done"]["work_item_ids"] == [str(c2.id)]
        assert by_source["Done"]["target"]["name"] == "Complete"

    def test_preview_in_detach_mode_only_lists_root_for_mapping(self, mover_client, world, item):
        make_issue(world.src, "C1", state=world.s.done, parent=item, creator=world.mover)

        rows = get_preview(mover_client, world, item, sub_items="detach").data["states"]

        assert [r["source"]["name"] for r in rows] == ["Todo"]


# ---------------------------------------------------------------------- rollback
@pytest.mark.contract
@pytest.mark.django_db
class TestRollback:
    @pytest.mark.parametrize(
        "patch_target, patch_kwargs",
        [
            ("plane.utils.work_item_move._apply_property_plan", {}),
            ("plane.utils.work_item_move.IssueActivity.objects.bulk_create", {}),
        ],
        ids=["property-step", "activity-step"],
    )
    def test_failure_midway_rolls_everything_back(
        self, mover_client, world, item, side_effects, patch_target, patch_kwargs
    ):
        seed_busy_item(world, item)
        child = make_issue(world.src, "Child", state=world.s.done, parent=item, creator=world.mover)
        label_issue(child, world.src_labels.docs)
        before = (snapshot(world, item), snapshot(world, child))

        with mock.patch(patch_target, side_effect=RuntimeError("boom"), **patch_kwargs):
            response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert (snapshot(world, item), snapshot(world, child)) == before
        issue = fresh(item)
        assert (issue.project_id, issue.sequence_id, issue.state_id) == (world.src.id, 1, world.s.todo.id)
        assert not IssueSequence.objects.filter(project=world.tgt).exists()
        assert not IssueActivity.objects.filter(issue=item, field="project").exists()
        side_effects.model_activity.delay.assert_not_called()
        side_effects.notify.assert_not_called()

    def test_failure_does_not_burn_a_sequence_number(self, mover_client, world, item):
        with mock.patch("plane.utils.work_item_move._apply_property_plan", side_effect=RuntimeError("boom")):
            assert post_move(mover_client, world, item).status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["identifier"] == "TGT-1"

    def test_validation_failure_leaves_no_side_effects(self, mover_client, world, item, side_effects):
        before = snapshot(world, item)

        response = post_move(mover_client, world, item, target_project_id=str(world.src.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert snapshot(world, item) == before
        side_effects.model_activity.delay.assert_not_called()
        side_effects.notify.assert_not_called()


# ------------------------------------------------------------------ notifications
@pytest.mark.contract
@pytest.mark.django_db
class TestNotifications:
    def test_subscribers_and_webhooks_are_notified_after_commit(
        self, mover_client, world, item, side_effects, django_capture_on_commit_callbacks
    ):
        subscribe(item, world.users.tgt_member)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            response = post_move(mover_client, world, item)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(callbacks) == 1
        side_effects.notify.assert_called_once()
        kwargs = side_effects.notify.call_args.kwargs
        assert kwargs["type"] == "issue.activity.updated"
        assert kwargs["issue_id"] == str(item.id)
        assert kwargs["actor_id"] == str(world.mover.id)
        assert kwargs["project_id"] == str(world.tgt.id)
        assert kwargs["subscriber"] is True
        activities = json.loads(kwargs["issue_activities_created"])
        assert [a["field"] for a in activities] == ["project"]
        assert activities[0]["old_value"] == "SRC-1"
        assert activities[0]["new_value"] == "TGT-1"

        side_effects.model_activity.delay.assert_called_once()
        webhook = side_effects.model_activity.delay.call_args.kwargs
        assert webhook["model_name"] == "issue"
        assert webhook["model_id"] == str(item.id)
        assert webhook["requested_data"] == {"project_id": str(world.tgt.id)}
        assert json.loads(webhook["current_instance"]) == {
            "project_id": str(world.src.id),
            "project_identifier": "SRC",
        }
        assert webhook["slug"] == world.slug

    def test_nothing_is_sent_before_commit(self, mover_client, world, item, side_effects):
        # Without executing the on_commit callbacks (the test transaction never commits) nothing is dispatched.
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        side_effects.notify.assert_not_called()

    def test_one_notification_per_moved_item(
        self, mover_client, world, item, side_effects, django_capture_on_commit_callbacks
    ):
        c1 = make_issue(world.src, "C1", state=world.s.todo, parent=item, creator=world.mover)
        make_issue(world.src, "C2", state=world.s.todo, parent=c1, creator=world.mover)

        with django_capture_on_commit_callbacks(execute=True):
            assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        assert side_effects.notify.call_count == 3
        # one webhook per request (for the item the user asked to move)
        assert side_effects.model_activity.delay.call_count == 1

    def test_detached_children_are_not_notified_as_moved(
        self, mover_client, world, item, side_effects, django_capture_on_commit_callbacks
    ):
        make_issue(world.src, "C1", state=world.s.todo, parent=item, creator=world.mover)

        with django_capture_on_commit_callbacks(execute=True):
            assert post_move(mover_client, world, item, sub_items="detach").status_code == status.HTTP_200_OK

        assert side_effects.notify.call_count == 1
        assert side_effects.notify.call_args.kwargs["issue_id"] == str(item.id)

    @pytest.mark.parametrize("failure", ["rolled-back", "forbidden", "same-project"])
    def test_failed_moves_notify_nobody(
        self, mover_client, world, item, side_effects, django_capture_on_commit_callbacks, failure
    ):
        with django_capture_on_commit_callbacks(execute=True):
            if failure == "rolled-back":
                with mock.patch("plane.utils.work_item_move._apply_property_plan", side_effect=RuntimeError("x")):
                    response = post_move(mover_client, world, item)
                expected = status.HTTP_500_INTERNAL_SERVER_ERROR
            elif failure == "forbidden":
                response = post_move(client_for(world.users.not_in_tgt), world, item)
                expected = status.HTTP_403_FORBIDDEN
            else:
                response = post_move(mover_client, world, item, target_project_id=str(world.src.id))
                expected = status.HTTP_400_BAD_REQUEST

        assert response.status_code == expected
        side_effects.notify.assert_not_called()
        side_effects.model_activity.delay.assert_not_called()


# ---------------------------------------------------------------- mass assignment
@pytest.mark.contract
@pytest.mark.django_db
class TestMassAssignment:
    def test_unknown_body_keys_are_ignored(self, mover_client, world, item):
        other_ws = Workspace.objects.create(name="Evil", slug="evil-ws", owner=world.admin)
        evil_project = make_project(other_ws, "EVL", world.admin)
        make_issue(world.tgt, "T1", state=world.t.ready)

        response = post_move(
            mover_client,
            world,
            item,
            workspace=str(other_ws.id),
            workspace_id=str(other_ws.id),
            project=str(evil_project.id),
            project_id=str(evil_project.id),
            sequence_id=999,
            id=str(uuid4()),
            name="Hacked",
            state_id=str(world.t.complete.id),
            state=str(world.t.complete.id),
            priority="urgent",
            created_by=str(world.admin.id),
            parent_id=str(uuid4()),
            archived_at="2026-01-01",
            is_draft=True,
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        moved = fresh(item)
        assert moved.id == item.id
        assert moved.project_id == world.tgt.id
        assert moved.workspace_id == world.workspace.id
        assert moved.sequence_id == 2  # allocated, not 999
        assert moved.name == "Item"
        assert moved.state_id == world.t.ready.id  # mapped by rule, not by the body
        assert moved.priority == item.priority
        assert moved.created_by_id == item.created_by_id
        assert moved.archived_at is None and moved.is_draft is False

    def test_target_project_id_in_query_string_is_not_read_by_post(self, mover_client, world, item):
        response = mover_client.post(
            move_url(world, world.src.id, item.id) + f"?target_project_id={world.tgt.id}", {}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["code"] == "INVALID_TARGET"
        assert fresh(item).project_id == world.src.id


# ----------------------------------------------------------------------- misc
@pytest.mark.contract
@pytest.mark.django_db
class TestMoveSideData:
    def test_moved_item_appears_in_target_issue_list_and_not_in_source(self, mover_client, world, item):
        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        assert Issue.issue_objects.filter(project=world.tgt, pk=item.pk).exists()
        assert not Issue.issue_objects.filter(project=world.src, pk=item.pk).exists()

    def test_project_membership_rows_untouched(self, mover_client, world, item):
        before = ProjectMember.objects.count()

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        assert ProjectMember.objects.count() == before

    def test_another_item_of_the_source_project_is_untouched(self, mover_client, world, item):
        sibling = make_issue(world.src, "Sibling", state=world.s.todo, creator=world.mover)
        label_issue(sibling, world.src_labels.bug)
        before = snapshot(world, sibling)

        assert post_move(mover_client, world, item).status_code == status.HTTP_200_OK

        after = snapshot(world, sibling)
        after["sequences"] = before["sequences"]  # the move legitimately adds one TGT sequence row
        assert after == before
