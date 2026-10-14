# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the move-work-item relation policy and the project field mapper (PSR-91/92).

* ``ISSUE_RELATION_POLICY`` / ``COMMENT_RELATION_POLICY`` must classify every
  reverse relation of ``Issue`` / ``IssueComment``. When someone (upstream or
  the fork) adds a new FK to either model, ``TestRelationPolicyGuard`` fails
  until a decision ("repoint" / "special" / "leave") is made, so a move can
  never silently leave project-scoped rows behind.
* ``ProjectFieldMapper`` is the rule set shared by the move and by draft
  re-targeting (docs/move-work-item-design.md section 2).
"""

from uuid import uuid4

import pytest

from plane.db.models import Cycle, Issue, IssueComment, Module, State
from plane.db.models.issue_type import IssueType, ProjectIssueType
from plane.tests.move_helpers import (
    ADMIN,
    GUEST,
    MEMBER,
    add_project_member,
    add_workspace_member,
    build_world,
    make_estimate,
    make_issue,
    make_project,
    make_state,
    make_user,
)
from plane.utils.work_item_mapping import (
    STATE_MATCH_DEFAULT,
    STATE_MATCH_GROUP,
    STATE_MATCH_NAME,
    STATE_MATCH_NONE,
    STATE_MATCH_SAME,
    ProjectFieldMapper,
    can_write_in_project,
    has_project_role,
    is_project_member,
    resolve_target_project,
)
from plane.utils.work_item_move import (
    COMMENT_RELATION_POLICY,
    ISSUE_RELATION_POLICY,
    REPOINT_BY_COMMENT,
    REPOINT_BY_ISSUE,
)

VALID_DECISIONS = {"repoint", "special", "leave"}


def relation_keys(model):
    return {f"{rel.related_model._meta.label}.{rel.field.name}" for rel in model._meta.related_objects}


def unclassified(model, policy):
    return sorted(relation_keys(model) - set(policy))


def stale(model, policy):
    return sorted(set(policy) - relation_keys(model))


# ----------------------------------------------------------------- policy guard
@pytest.mark.unit
class TestRelationPolicyGuard:
    @pytest.mark.parametrize(
        "model, policy",
        [(Issue, ISSUE_RELATION_POLICY), (IssueComment, COMMENT_RELATION_POLICY)],
        ids=["Issue", "IssueComment"],
    )
    def test_every_reverse_relation_is_classified(self, model, policy):
        missing = unclassified(model, policy)

        assert not missing, (
            f"{model.__name__} has reverse relations with no move decision: {missing}. "
            "Add each one to plane/utils/work_item_move.py (REPOINT_BY_* for rows that follow the item, "
            "or ISSUE_RELATION_POLICY / COMMENT_RELATION_POLICY as 'special' / 'leave' with the reason) "
            "and handle it in move_work_item()."
        )

    @pytest.mark.parametrize(
        "model, policy",
        [(Issue, ISSUE_RELATION_POLICY), (IssueComment, COMMENT_RELATION_POLICY)],
        ids=["Issue", "IssueComment"],
    )
    def test_policy_has_no_stale_entries(self, model, policy):
        assert not stale(model, policy), f"Policy entries that match no relation of {model.__name__}"

    @pytest.mark.parametrize("policy", [ISSUE_RELATION_POLICY, COMMENT_RELATION_POLICY], ids=["Issue", "IssueComment"])
    def test_decisions_are_valid(self, policy):
        assert set(policy.values()) <= VALID_DECISIONS

    def test_guard_detects_a_newly_added_foreign_key(self):
        """The guard itself: dropping an entry (what a new, unreviewed FK looks like) is reported."""
        trimmed = dict(ISSUE_RELATION_POLICY)
        removed = "db.IssueLink.issue"
        del trimmed[removed]

        assert unclassified(Issue, trimmed) == [removed]
        assert unclassified(Issue, ISSUE_RELATION_POLICY) == []

    def test_guard_key_format_matches_model_labels(self):
        keys = relation_keys(Issue)

        assert "db.IssueLabel.issue" in keys
        assert "db.Issue.parent" in keys
        assert "properties.IssuePropertyValue.issue" in keys

    @pytest.mark.parametrize(
        "pairs, policy",
        [(REPOINT_BY_ISSUE, ISSUE_RELATION_POLICY), (REPOINT_BY_COMMENT, COMMENT_RELATION_POLICY)],
        ids=["Issue", "IssueComment"],
    )
    def test_repointed_models_are_project_scoped_and_listed_as_repoint(self, pairs, policy):
        for model, fk in pairs:
            assert policy[f"{model._meta.label}.{fk}"] == "repoint"
            assert model._meta.get_field("project"), f"{model.__name__} has no project FK to re-point"
            assert hasattr(model, "all_objects"), f"{model.__name__} needs all_objects so soft-deleted rows follow"

    def test_repoint_lists_cover_every_repoint_decision(self):
        listed = {f"{m._meta.label}.{f}" for m, f in REPOINT_BY_ISSUE}
        decided = {k for k, v in ISSUE_RELATION_POLICY.items() if v == "repoint"}
        assert listed == decided

        listed = {f"{m._meta.label}.{f}" for m, f in REPOINT_BY_COMMENT}
        decided = {k for k, v in COMMENT_RELATION_POLICY.items() if v == "repoint"}
        assert listed == decided


# ------------------------------------------------------------ mapper test world
@pytest.fixture
def world(db, workspace, create_user):
    return build_world(workspace, create_user)


@pytest.fixture
def mapper(world):
    return ProjectFieldMapper(source=world.src, target=world.tgt)


@pytest.mark.unit
@pytest.mark.django_db
class TestMapState:
    @pytest.mark.parametrize(
        "src_key, tgt_key, match",
        [
            ("backlog", "backlog", STATE_MATCH_NAME),
            ("todo", "ready", STATE_MATCH_GROUP),
            ("doing", "progress", STATE_MATCH_GROUP),
            ("review", "progress", STATE_MATCH_GROUP),
            ("done", "complete", STATE_MATCH_GROUP),
            ("cancelled", "ready", STATE_MATCH_DEFAULT),
        ],
    )
    def test_rules(self, world, mapper, src_key, tgt_key, match):
        state, got = mapper.map_state(getattr(world.s, src_key))

        assert got == match
        assert state.id == getattr(world.t, tgt_key).id

    def test_name_match_ignores_case_and_surrounding_whitespace(self, world, mapper):
        state = State.all_state_objects.get(pk=world.s.backlog.pk)
        state.name = "  backlog "

        mapped, match = mapper.map_state(state)

        assert (mapped.id, match) == (world.t.backlog.id, STATE_MATCH_NAME)

    def test_name_beats_group(self, world):
        # "Done" exists by name in the target but lives in another group there.
        make_state(world.tgt, "Done", "started")
        mapped, match = ProjectFieldMapper(source=world.src, target=world.tgt).map_state(world.s.done)

        assert match == STATE_MATCH_NAME
        assert mapped.name == "Done"

    def test_group_match_prefers_the_default_state_of_that_group(self, world):
        second = make_state(world.tgt, "QA", "started", default=True)
        State.objects.filter(pk=world.t.ready.pk).update(default=False)

        mapped, match = ProjectFieldMapper(source=world.src, target=world.tgt).map_state(world.s.doing)

        assert (mapped.id, match) == (second.id, STATE_MATCH_GROUP)

    def test_group_match_without_default_uses_sequence_order(self, world, mapper):
        make_state(world.tgt, "QA", "started")

        mapped, _ = ProjectFieldMapper(source=world.src, target=world.tgt).map_state(world.s.review)

        assert mapped.id == world.t.progress.id

    def test_target_default_is_used_when_group_is_missing(self, world, mapper):
        State.objects.filter(pk=world.t.ready.pk).update(default=False)
        State.objects.filter(pk=world.t.complete.pk).update(default=True)

        mapped, match = ProjectFieldMapper(source=world.src, target=world.tgt).map_state(world.s.cancelled)

        assert (mapped.id, match) == (world.t.complete.id, STATE_MATCH_DEFAULT)

    def test_first_state_is_the_last_resort_when_target_has_no_default(self, world):
        State.objects.filter(project=world.tgt).update(default=False)

        mapped, match = ProjectFieldMapper(source=world.src, target=world.tgt).map_state(world.s.cancelled)

        assert match == STATE_MATCH_DEFAULT
        assert mapped.id == world.t.backlog.id  # lowest sequence

    def test_triage_states_are_never_targets(self, world, mapper):
        assert world.t.triage.id not in {s.id for s in mapper.target_states}
        mapped, match = mapper.map_state(world.s.doing)  # target also has a triage state named "Doing"
        assert mapped.id == world.t.progress.id and match == STATE_MATCH_GROUP

    def test_state_already_in_target_is_kept(self, world, mapper):
        mapped, match = mapper.map_state(world.t.progress)

        assert (mapped.id, match) == (world.t.progress.id, STATE_MATCH_SAME)

    def test_none_state_maps_to_the_target_default(self, world, mapper):
        mapped, match = mapper.map_state(None)

        assert (mapped.id, match) == (world.t.ready.id, STATE_MATCH_DEFAULT)

    def test_target_without_states(self, world):
        empty = make_project(world.workspace, "EMP", world.admin)

        assert ProjectFieldMapper(source=world.src, target=empty).map_state(world.s.todo) == (None, STATE_MATCH_NONE)

    def test_map_state_id_looks_up_triage_states_too(self, world, mapper):
        triage = make_state(world.src, "Intake", "triage", is_triage=True)

        mapped, match = mapper.map_state_id(triage.id)

        assert match == STATE_MATCH_DEFAULT  # no triage target: falls back to the default state
        assert mapped.id == world.t.ready.id

    def test_map_state_id_unknown_id_falls_back_to_default(self, world, mapper):
        assert mapper.map_state_id(uuid4())[1] == STATE_MATCH_DEFAULT
        assert mapper.map_state_id(None)[1] == STATE_MATCH_DEFAULT

    def test_validate_target_state_id(self, world, mapper):
        assert mapper.validate_target_state_id(world.t.complete.id).id == world.t.complete.id
        assert mapper.validate_target_state_id(str(world.t.complete.id)).id == world.t.complete.id
        assert mapper.validate_target_state_id(world.s.done.id) is None  # source state
        assert mapper.validate_target_state_id(world.t.triage.id) is None
        assert mapper.validate_target_state_id(uuid4()) is None
        assert mapper.validate_target_state_id(None) is None
        assert mapper.validate_target_state_id("") is None

    def test_default_target_state(self, world, mapper):
        assert mapper.default_target_state().id == world.t.ready.id


@pytest.mark.unit
@pytest.mark.django_db
class TestMapLabels:
    def test_match_by_name_case_insensitive_and_drop_unmatched(self, world, mapper):
        kept, dropped = mapper.map_labels([world.src_labels.bug, world.src_labels.docs, world.src_labels.feature])

        assert [(s.id, t.id) for s, t in kept] == [
            (world.src_labels.bug.id, world.tgt_labels.bug.id),
            (world.src_labels.docs.id, world.tgt_labels.docs.id),
        ]
        assert [label.name for label in dropped] == ["feature"]

    def test_labels_collapsing_onto_one_target_label_are_deduplicated(self, world, mapper):
        kept, dropped = mapper.map_labels([world.src_labels.bug, world.src_labels.bug_upper])

        assert [t.id for _, t in kept] == [world.tgt_labels.bug.id]
        assert dropped == []

    def test_labels_already_in_target_are_kept_as_is(self, world, mapper):
        kept, dropped = mapper.map_labels([world.tgt_labels.bug])

        assert [(s.id, t.id) for s, t in kept] == [(world.tgt_labels.bug.id, world.tgt_labels.bug.id)]
        assert dropped == []

    def test_workspace_wide_labels_are_kept(self, world, mapper):
        from plane.db.models import Label

        wide = Label.objects.create(workspace=world.workspace, project=None, name="shared", color="#000")

        kept, dropped = mapper.map_labels([wide])

        assert [t.id for _, t in kept] == [wide.id]

    def test_first_target_label_by_sort_order_wins_on_duplicate_names(self, world):
        from plane.db.models import Label

        # Label.save() assigns sort_order itself on create, so set the order afterwards.
        early = Label.objects.create(workspace=world.workspace, project=world.tgt, name="bug", color="#000")
        Label.objects.filter(pk=world.tgt_labels.bug.pk).update(sort_order=200)
        Label.objects.filter(pk=early.pk).update(sort_order=100)

        kept, _ = ProjectFieldMapper(source=world.src, target=world.tgt).map_labels([world.src_labels.bug])

        assert kept[0][1].id == early.id

    def test_map_label_ids(self, world, mapper):
        kept, dropped = mapper.map_label_ids([str(world.src_labels.bug.id), None, "", str(world.src_labels.feature.id)])

        assert [t.id for _, t in kept] == [world.tgt_labels.bug.id]
        assert [label.id for label in dropped] == [world.src_labels.feature.id]
        assert mapper.map_label_ids(None) == ([], [])

    def test_empty(self, mapper):
        assert mapper.map_labels([]) == ([], [])


@pytest.mark.unit
@pytest.mark.django_db
class TestSplitAssignees:
    def test_members_and_admins_kept_others_dropped(self, world, mapper):
        u = world.users
        ids = [u.tgt_guest.id, u.tgt_member.id, u.not_in_tgt.id, u.tgt_admin.id, u.tgt_inactive.id]

        kept, dropped = mapper.split_assignees(ids)

        assert kept == [u.tgt_member.id, u.tgt_admin.id]  # input order preserved
        assert dropped == [u.tgt_guest.id, u.not_in_tgt.id, u.tgt_inactive.id]

    def test_accepts_string_ids_and_skips_empty_values(self, world, mapper):
        u = world.users

        kept, dropped = mapper.split_assignees([str(u.tgt_member.id), None, "", str(u.not_in_tgt.id)])

        assert kept == [str(u.tgt_member.id)]
        assert dropped == [str(u.not_in_tgt.id)]

    def test_garbage_ids_are_dropped_not_raised(self, mapper):
        assert mapper.split_assignees(["not-a-uuid"]) == ([], ["not-a-uuid"])

    def test_empty(self, mapper):
        assert mapper.split_assignees(None) == ([], [])
        assert mapper.split_assignees([]) == ([], [])

    def test_member_ids_include_guests(self, world, mapper):
        assert world.users.tgt_guest.id in mapper.member_ids
        assert world.users.tgt_inactive.id not in mapper.member_ids
        assert world.users.not_in_tgt.id not in mapper.member_ids
        assert world.users.tgt_guest.id not in mapper.assignable_ids


@pytest.mark.unit
@pytest.mark.django_db
class TestMapEstimate:
    def test_same_type_and_value_maps_to_the_target_point(self, world, mapper):
        _, src = make_estimate(world.src, "points", ["1", "2"])
        _, tgt = make_estimate(world.tgt, "points", ["2", "8"])
        mapper = ProjectFieldMapper(source=world.src, target=world.tgt)

        assert mapper.map_estimate_point(src["2"]).id == tgt["2"].id
        assert mapper.map_estimate_point(src["1"]) is None  # no such value in the target

    def test_different_estimate_type_is_cleared(self, world):
        _, src = make_estimate(world.src, "points", ["2"])
        make_estimate(world.tgt, "categories", ["2"])

        assert ProjectFieldMapper(source=world.src, target=world.tgt).map_estimate_point(src["2"]) is None

    def test_target_without_active_estimate_clears(self, world):
        _, src = make_estimate(world.src, "points", ["2"])

        assert ProjectFieldMapper(source=world.src, target=world.tgt).map_estimate_point(src["2"]) is None

    def test_point_already_in_target_is_kept(self, world):
        _, tgt = make_estimate(world.tgt, "points", ["2"])

        mapped = ProjectFieldMapper(source=world.src, target=world.tgt).map_estimate_point(tgt["2"])

        assert mapped.id == tgt["2"].id

    def test_none_and_ids(self, world):
        _, src = make_estimate(world.src, "points", ["2"])
        _, tgt = make_estimate(world.tgt, "points", ["2"])
        mapper = ProjectFieldMapper(source=world.src, target=world.tgt)

        assert mapper.map_estimate_point(None) is None
        assert mapper.map_estimate_point_id(None) is None
        assert mapper.map_estimate_point_id(str(src["2"].id)).id == tgt["2"].id
        assert mapper.map_estimate_point_id(uuid4()) is None


@pytest.mark.unit
@pytest.mark.django_db
class TestIssueTypeCycleModuleParent:
    def test_issue_type_kept_only_when_enabled_in_target(self, world, mapper):
        enabled = IssueType.objects.create(workspace=world.workspace, name="Task")
        disabled = IssueType.objects.create(workspace=world.workspace, name="Bug")
        ProjectIssueType.objects.create(project=world.tgt, issue_type=enabled)

        assert mapper.map_issue_type_id(enabled.id) == enabled.id
        assert mapper.map_issue_type_id(disabled.id) is None
        assert mapper.map_issue_type_id(None) is None

    def test_cycle_survives_only_when_it_belongs_to_the_target(self, world, mapper):
        mine = Cycle.objects.create(project=world.tgt, name="T", owned_by=world.admin)
        theirs = Cycle.objects.create(project=world.src, name="S", owned_by=world.admin)

        assert mapper.keep_cycle_id(mine.id) == mine.id
        assert mapper.keep_cycle_id(theirs.id) is None
        assert mapper.keep_cycle_id(None) is None

    def test_modules_survive_only_when_they_belong_to_the_target(self, world, mapper):
        mine = Module.objects.create(project=world.tgt, name="T")
        theirs = Module.objects.create(project=world.src, name="S")

        assert mapper.keep_module_ids([theirs.id, mine.id]) == [mine.id]
        assert mapper.keep_module_ids([]) == []
        assert mapper.keep_module_ids(None) == []

    def test_parent_survives_only_when_it_is_a_live_target_issue(self, world, mapper):
        in_target = make_issue(world.tgt, "P", state=world.t.ready)
        in_source = make_issue(world.src, "S", state=world.s.todo)

        assert mapper.keep_parent_id(in_target.id) == in_target.id
        assert mapper.keep_parent_id(in_source.id) is None
        assert mapper.keep_parent_id(None) is None


@pytest.mark.unit
@pytest.mark.django_db
class TestMapPayload:
    def _payload(self, world):
        u = world.users
        return {
            "state_id": str(world.s.done.id),
            "label_ids": [str(world.src_labels.bug.id), str(world.src_labels.feature.id)],
            "assignee_ids": [str(u.tgt_member.id), str(u.not_in_tgt.id)],
            "estimate_point": None,
            "type_id": None,
            "parent_id": None,
            "cycle_id": None,
            "module_ids": [],
        }

    def test_maps_and_summarises_what_was_dropped(self, world, mapper):
        result = mapper.map_payload(self._payload(world))

        assert result["state_id"] == str(world.t.complete.id)
        assert result["state_match"] == STATE_MATCH_GROUP
        assert result["label_ids"] == [str(world.tgt_labels.bug.id)]
        assert result["assignee_ids"] == [str(world.users.tgt_member.id)]
        assert [label["name"] for label in result["dropped"]["labels"]] == ["feature"]
        assert [a["id"] for a in result["dropped"]["assignees"]] == [str(world.users.not_in_tgt.id)]
        assert {set_flag for set_flag, value in result["dropped"].items() if value is True} == set()
        assert result["cycle_id"] is None and result["module_ids"] == []

    def test_cycle_modules_parent_and_estimate_are_dropped_and_flagged(self, world, mapper):
        cycle = Cycle.objects.create(project=world.src, name="S", owned_by=world.admin)
        module = Module.objects.create(project=world.src, name="M")
        parent = make_issue(world.src, "P", state=world.s.todo)
        _, src_points = make_estimate(world.src, "points", ["3"])
        payload = {
            **self._payload(world),
            "cycle_id": str(cycle.id),
            "module_ids": [str(module.id)],
            "parent_id": str(parent.id),
            "estimate_point": str(src_points["3"].id),
            "type_id": str(IssueType.objects.create(workspace=world.workspace, name="X").id),
        }

        result = mapper.map_payload(payload)

        assert result["cycle_id"] is None and result["module_ids"] == []
        assert result["parent_id"] is None and result["estimate_point"] is None and result["type_id"] is None
        dropped = result["dropped"]
        assert (dropped["cycle"], dropped["modules"], dropped["parent"], dropped["estimate"], dropped["type"]) == (
            True,
            True,
            True,
            True,
            True,
        )

    def test_no_state_uses_the_target_default(self, world, mapper):
        result = mapper.map_payload({"state_id": None})

        assert result["state_id"] == str(world.t.ready.id)
        assert result["state_match"] == STATE_MATCH_DEFAULT
        assert result["label_ids"] == [] and result["assignee_ids"] == []

    def test_empty_payload(self, world, mapper):
        result = mapper.map_payload({})

        assert result["state_id"] == str(world.t.ready.id)
        assert result["dropped"]["labels"] == [] and result["dropped"]["assignees"] == []

    def test_is_idempotent(self, world, mapper):
        """Values already in the target are kept, so half-mapped form data passes through unchanged."""
        first = mapper.map_payload(self._payload(world))

        second = mapper.map_payload(
            {
                "state_id": first["state_id"],
                "label_ids": first["label_ids"],
                "assignee_ids": first["assignee_ids"],
                "estimate_point": first["estimate_point"],
                "type_id": first["type_id"],
                "parent_id": first["parent_id"],
                "cycle_id": first["cycle_id"],
                "module_ids": first["module_ids"],
            }
        )

        assert second["state_id"] == first["state_id"]
        assert second["state_match"] == STATE_MATCH_SAME
        assert second["label_ids"] == first["label_ids"]
        assert second["assignee_ids"] == first["assignee_ids"]
        assert second["dropped"]["labels"] == [] and second["dropped"]["assignees"] == []

    def test_target_values_pass_through_untouched(self, world, mapper):
        cycle = Cycle.objects.create(project=world.tgt, name="T", owned_by=world.admin)
        module = Module.objects.create(project=world.tgt, name="M")
        parent = make_issue(world.tgt, "P", state=world.t.ready)
        _, tgt_points = make_estimate(world.tgt, "points", ["5"])

        result = mapper.map_payload(
            {
                "state_id": str(world.t.progress.id),
                "label_ids": [str(world.tgt_labels.docs.id)],
                "assignee_ids": [str(world.users.tgt_admin.id)],
                "estimate_point": str(tgt_points["5"].id),
                "parent_id": str(parent.id),
                "cycle_id": str(cycle.id),
                "module_ids": [str(module.id)],
            }
        )

        assert result["state_id"] == str(world.t.progress.id)
        assert result["label_ids"] == [str(world.tgt_labels.docs.id)]
        assert result["assignee_ids"] == [str(world.users.tgt_admin.id)]
        assert result["estimate_point"] == str(tgt_points["5"].id)
        assert result["parent_id"] == str(parent.id)
        assert result["cycle_id"] == str(cycle.id)
        assert result["module_ids"] == [str(module.id)]
        assert not any(v for k, v in result["dropped"].items() if k not in ("labels", "assignees"))

    def test_output_ids_are_strings(self, world, mapper):
        result = mapper.map_payload(self._payload(world))

        assert isinstance(result["state_id"], str)
        assert all(isinstance(i, str) for i in result["label_ids"] + result["assignee_ids"])


# ------------------------------------------------------------ permission helpers
@pytest.mark.unit
@pytest.mark.django_db
class TestPermissionHelpers:
    def _user(self, world, ws_role=MEMBER, src_role=None):
        user = make_user("helper")
        add_workspace_member(world.workspace, user, ws_role)
        if src_role:
            add_project_member(world.src, user, src_role)
        return user

    @pytest.mark.parametrize("role, expected", [(ADMIN, True), (MEMBER, True), (GUEST, False), (None, False)], ids=str)
    def test_can_write_in_project(self, world, role, expected):
        user = self._user(world, src_role=role)

        assert can_write_in_project(user, world.src) is expected

    def test_workspace_admin_who_is_a_project_guest_can_write(self, world):
        user = self._user(world, ws_role=ADMIN, src_role=GUEST)

        assert can_write_in_project(user, world.src) is True

    def test_workspace_admin_outside_the_project_cannot(self, world):
        user = self._user(world, ws_role=ADMIN)

        assert can_write_in_project(user, world.src) is False
        assert is_project_member(user, world.src) is False

    def test_inactive_membership_does_not_count(self, world):
        user = make_user("helper")
        add_workspace_member(world.workspace, user, ADMIN)
        add_project_member(world.src, user, ADMIN, is_active=False)

        assert can_write_in_project(user, world.src) is False
        assert is_project_member(user, world.src) is False

    def test_has_project_role_accepts_enum_values_and_ints(self, world):
        from plane.app.permissions import ROLE

        user = self._user(world, src_role=GUEST)

        assert has_project_role(user, world.src, [ROLE.GUEST]) is True
        assert has_project_role(user, world.src, [5]) is True
        assert has_project_role(user, world.src, [ROLE.ADMIN, ROLE.MEMBER]) is False

    def test_is_project_member_counts_guests(self, world):
        assert is_project_member(self._user(world, src_role=GUEST), world.src) is True


@pytest.mark.unit
@pytest.mark.django_db
class TestResolveTargetProject:
    def test_resolves_a_project_of_the_same_workspace(self, world):
        assert resolve_target_project(world.workspace.id, world.tgt.id).id == world.tgt.id
        assert resolve_target_project(world.workspace.id, str(world.tgt.id)).id == world.tgt.id

    def test_other_workspace_unknown_and_garbage_resolve_to_none(self, world):
        from plane.db.models import Workspace

        other = Workspace.objects.create(name="O", slug="o-ws", owner=world.admin)
        foreign = make_project(other, "FOR", world.admin)

        assert resolve_target_project(world.workspace.id, foreign.id) is None
        assert resolve_target_project(world.workspace.id, uuid4()) is None
        assert resolve_target_project(world.workspace.id, "nope") is None
        assert resolve_target_project(world.workspace.id, None) is None
        assert resolve_target_project(world.workspace.id, "") is None


@pytest.mark.unit
@pytest.mark.django_db
class TestMapperCaching:
    def test_one_mapper_can_serve_many_items(self, world, django_assert_num_queries):
        mapper = ProjectFieldMapper(source=world.src, target=world.tgt)
        mapper.map_state(world.s.todo)  # warms the target-state cache

        with django_assert_num_queries(0):
            for key in ("backlog", "todo", "doing", "review", "done", "cancelled"):
                mapper.map_state(getattr(world.s, key))
