# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: move-work-item (PSR-91).

Move a work item (optionally with its sub-items) to another project of the
same workspace. Design: docs/move-work-item-design.md.

Two entry points, both used by the app API and the public API v1:

* ``build_move_plan`` – read-only preview of what will be mapped / dropped.
* ``move_work_item``  – performs the move in a single transaction.

The ``Issue`` row is updated in place (same UUID); it gets a new sequence in
the target project, and its old ``IssueSequence`` row is left in the source
project so the old identifier keeps resolving (see
``resolve_moved_work_item``).
"""

# Python imports
import json
from collections import OrderedDict

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

# Module imports
from plane.db.models import (
    CommentReaction,
    CycleIssue,
    Description,
    DescriptionVersion,
    FileAsset,
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
    IssueVersion,
    IssueVote,
    IssueWorklog,
    IntakeIssue,
    ModuleIssue,
    Notification,
    StateGroup,
    User,
    UserRecentVisit,
    WorklogTimer,
    TimeEntryRequest,  # FORK: time-tracking-v2 S3
)
from plane.utils.time_tracking.snapshot import refresh_worklog_snapshots  # FORK: time-tracking-v2
from plane.db.models.issue import IssueAttachment
from plane.utils.uuid import convert_uuid_to_integer
from plane.utils.work_item_mapping import (
    ProjectFieldMapper,
    STATE_MATCH_NONE,
    can_write_in_project,
    label_lite,
    project_lite,
    state_lite,
    user_lite,
)

SUB_ITEMS_MOVE = "move"
SUB_ITEMS_DETACH = "detach"
SUB_ITEMS_MODES = (SUB_ITEMS_MOVE, SUB_ITEMS_DETACH)

# IntakeIssue.status values that mean "still waiting in intake".
PENDING_INTAKE_STATUSES = (-2, 0)


class MoveError(Exception):
    def __init__(self, code, message, status_code=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_response_data(self):
        return {"error": self.message, "code": self.code}


# ---------------------------------------------------------------------------
# Relation policy
# ---------------------------------------------------------------------------
# Every reverse relation of Issue / IssueComment must be classified here.
# tests/unit/utils/test_work_item_move_policy.py fails when upstream (or the
# fork) adds a new FK to Issue/IssueComment without a decision being made.
#
#   "repoint" – the row moves with the item: project_id := target
#   "special" – handled explicitly in move_work_item (remap / filter / clear)
#   "leave"   – deliberately left untouched (reason in the comment)
REPOINT_BY_ISSUE = [
    # (model, fk field to Issue)
    (IssueMention, "issue"),
    (IssueLink, "issue"),
    (IssueAttachment, "issue"),
    (IssueActivity, "issue"),
    (IssueComment, "issue"),
    (IssueReaction, "issue"),
    (IssueVote, "issue"),
    (IssueVersion, "issue"),
    (IssueDescriptionVersion, "issue"),
    (IssueWorklog, "issue"),
    (WorklogTimer, "issue"),
    # FORK: time-tracking-v2 S3 — requests follow the item; approvers are resolved from the
    # request's project at read time, so pending requests re-route to the new project's admins.
    (TimeEntryRequest, "issue"),
    (FileAsset, "issue"),
    (IssueRelation, "issue"),  # the moved item's own side of a relation
    (IssueBlocker, "block"),
]
REPOINT_BY_COMMENT = [
    (CommentReaction, "comment"),
    (FileAsset, "comment"),
]

ISSUE_RELATION_POLICY = {
    **{f"{m._meta.label}.{f}": "repoint" for m, f in REPOINT_BY_ISSUE},
    "db.Issue.parent": "special",  # sub-items: moved together or detached
    "db.IssueAssignee.issue": "special",  # kept only for target members
    "db.IssueLabel.issue": "special",  # remapped by name / dropped
    "db.IssueSubscriber.issue": "special",  # kept only for target members
    "db.CycleIssue.issue": "special",  # cleared
    "db.ModuleIssue.issue": "special",  # cleared
    "db.IssueSequence.issue": "special",  # old row kept (redirect), new row in target
    "properties.IssuePropertyValue.issue": "special",  # remapped by property name+type
    "db.IssueRelation.related_issue": "leave",  # the other item's side; ids are stable
    "db.IssueBlocker.blocked_by": "leave",  # the other item's side
    "db.IntakeIssue.issue": "leave",  # intake history of an accepted item stays in source
    "db.IntakeIssue.duplicate_to": "leave",  # id-based pointer from another intake item
    "db.DraftIssue.parent": "leave",  # a draft's parent is re-validated when it is posted
    "db.GithubIssueSync.issue": "leave",  # bound to the source project's repository sync
}
COMMENT_RELATION_POLICY = {
    **{f"{m._meta.label}.{f}": "repoint" for m, f in REPOINT_BY_COMMENT},
    "db.IssueComment.parent": "leave",  # replies: same issue, repointed via IssueComment.issue
    "db.IssueActivity.issue_comment": "leave",  # repointed via IssueActivity.issue
    "db.GithubCommentSync.comment": "leave",  # bound to the source project's repository sync
}


def identifier_of(project, sequence_id):
    return f"{project.identifier}-{sequence_id}"


# ---------------------------------------------------------------------------
# Analysis (shared by preview and execution)
# ---------------------------------------------------------------------------
def collect_subtree(root):
    """All descendants of ``root`` in its own project, breadth-first (root excluded)."""
    result, seen, frontier = [], {root.id}, [root.id]
    while frontier:
        children = list(
            Issue.objects.filter(parent_id__in=frontier, project_id=root.project_id)
            .exclude(id__in=seen)
            .select_related("state", "estimate_point", "estimate_point__estimate")
            .order_by("sequence_id")
        )
        frontier = []
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            frontier.append(child.id)
            result.append(child)
    return result


def validate_move(issue, target, user):
    source = issue.project
    # FORK: security — permission checks come first, so a caller without write access
    # (e.g. a guest on the public API preview) learns nothing about the item's intake
    # state or whether a project UUID exists / is archived.
    if not can_write_in_project(user, source):
        raise MoveError("FORBIDDEN_SOURCE", "You don't have permission to move this work item.", 403)
    if target is None:
        raise MoveError("INVALID_TARGET", "Target project not found in this workspace.")
    if target.id == source.id:
        raise MoveError("SAME_PROJECT", "The work item is already in this project.")
    if not can_write_in_project(user, target):
        raise MoveError("FORBIDDEN_TARGET", "You don't have permission to create work items in that project.", 403)
    if target.archived_at is not None:
        raise MoveError("TARGET_ARCHIVED", "The target project is archived.")
    if issue.archived_at is not None:
        raise MoveError("ITEM_ARCHIVED", "Restore the work item before moving it.")
    if issue.is_draft:
        raise MoveError("INVALID_ITEM", "Draft work items cannot be moved.")
    if _pending_intake_ids([issue]):
        raise MoveError("ITEM_IN_INTAKE", "Accept the intake work item before moving it.")


def _pending_intake_ids(items):
    """Ids of ``items`` still waiting in intake (triage state or a pending/snoozed record)."""
    pending = {i.id for i in items if i.state is not None and i.state.group == StateGroup.TRIAGE.value}
    pending.update(
        IntakeIssue.objects.filter(
            issue_id__in=[i.id for i in items], status__in=PENDING_INTAKE_STATUSES
        ).values_list("issue_id", flat=True)
    )
    return pending


class _Analysis:
    """Everything the preview shows and the move applies, computed once."""

    def __init__(self, issue, target, sub_items_mode):
        if sub_items_mode not in SUB_ITEMS_MODES:
            raise MoveError("INVALID_SUB_ITEMS", "sub_items must be 'move' or 'detach'.")
        self.issue = issue
        self.source = issue.project
        self.target = target
        self.mode = sub_items_mode
        self.mapper = ProjectFieldMapper(source=self.source, target=target)
        self.subtree = collect_subtree(issue)
        self.items = [issue] + (self.subtree if self.mode == SUB_ITEMS_MOVE else [])
        self.item_ids = [i.id for i in self.items]
        self.detached_children = (
            [c for c in self.subtree if c.parent_id == issue.id] if self.mode == SUB_ITEMS_DETACH else []
        )
        # FORK: archived sub-items in the sub-tree move silently with it (the move only updates the
        # project/sequence/state columns, so ``archived_at`` is preserved). Surface them in the preview.
        self.archived_moving_sub_items = (
            [c for c in self.subtree if c.archived_at is not None] if self.mode == SUB_ITEMS_MOVE else []
        )
        # FORK: security — the intake rule applies to every item that moves, not just the
        # root: a pending intake sub-item would otherwise leave intake (and its triage
        # state) without being accepted.
        if self.mode == SUB_ITEMS_MOVE and self.subtree and _pending_intake_ids(self.subtree):
            raise MoveError("ITEM_IN_INTAKE", "A sub-item is still in intake; accept it (or detach sub-items) first.")

        # States: group items by source state.
        self.state_groups = OrderedDict()
        for item in self.items:
            key = item.state_id
            if key not in self.state_groups:
                target_state, match = self.mapper.map_state(item.state)
                self.state_groups[key] = {"source": item.state, "target": target_state, "match": match, "items": []}
            self.state_groups[key]["items"].append(item.id)

        # Labels
        issue_labels = list(IssueLabel.objects.filter(issue_id__in=self.item_ids).select_related("label"))
        distinct_labels = list(OrderedDict((il.label_id, il.label) for il in issue_labels).values())
        self.labels_kept, self.labels_dropped = self.mapper.map_labels(distinct_labels)
        # FORK: map each source label on its own. ``labels_kept`` is deduplicated on the
        # target label (preview), so two source labels that collapse onto one target
        # label ("bug" / "Bug") would otherwise leave the second one unmapped and
        # silently strip it from the items that carry only that spelling.
        self.label_target_by_source = {}
        for label in distinct_labels:
            mapped, _ = self.mapper.map_labels([label])
            if mapped:
                self.label_target_by_source[label.id] = mapped[0][1]

        # Assignees
        assignee_ids = list(
            OrderedDict.fromkeys(
                IssueAssignee.objects.filter(issue_id__in=self.item_ids).values_list("assignee_id", flat=True)
            )
        )
        kept, dropped = self.mapper.split_assignees(assignee_ids)
        self.assignees_kept_ids, self.assignees_dropped_ids = kept, dropped

        # Subscribers (any active target member may stay subscribed)
        subscriber_ids = set(
            IssueSubscriber.objects.filter(issue_id__in=self.item_ids).values_list("subscriber_id", flat=True)
        )
        self.subscribers_dropped_ids = [s for s in subscriber_ids if s not in self.mapper.member_ids]

        # Cycle / modules (cleared)
        self.cycle_issues = list(CycleIssue.objects.filter(issue_id__in=self.item_ids).select_related("cycle"))
        self.module_issues = list(ModuleIssue.objects.filter(issue_id__in=self.item_ids).select_related("module"))

        # Estimate / type per item
        self.estimate_by_item = {i.id: self.mapper.map_estimate_point(i.estimate_point) for i in self.items}
        self.type_by_item = {i.id: self.mapper.map_issue_type_id(i.type_id) for i in self.items}

        # Custom properties (fork feature)
        self.property_plan = _plan_property_values(self.item_ids, self.mapper, self.type_by_item)

    # --------------------------------------------------------------- preview
    def as_plan(self):
        issue, source, target = self.issue, self.source, self.target
        last_sequence = IssueSequence.objects.filter(project_id=target.id).aggregate(m=Max("sequence"))["m"] or 0
        estimate_target = self.estimate_by_item.get(issue.id)
        if issue.estimate_point_id is None:
            estimate_action = "none"
        else:
            estimate_action = "kept" if estimate_target else "cleared"
        if issue.type_id is None:
            type_action = "none"
        else:
            type_action = "kept" if self.type_by_item.get(issue.id) else "cleared"

        worklogs = IssueWorklog.objects.filter(issue_id__in=self.item_ids)
        worklog_count = worklogs.count()
        worklog_minutes = worklogs.aggregate(total=Sum("duration"))["total"] or 0
        locked = worklogs.filter(Q(is_locked=True) | Q(approval_status="approved")).count()
        timers = WorklogTimer.objects.filter(issue_id__in=self.item_ids).count()
        client_changes = (worklog_count > 0 or timers > 0) and source.client_id != target.client_id

        users = {
            u.id: u
            for u in User.objects.filter(
                id__in=self.assignees_kept_ids + self.assignees_dropped_ids + self.subscribers_dropped_ids
            )
        }
        parent = issue.parent if issue.parent_id else None
        cycles = OrderedDict((ci.cycle_id, ci.cycle) for ci in self.cycle_issues if ci.cycle is not None)
        modules = OrderedDict((mi.module_id, mi.module) for mi in self.module_issues if mi.module is not None)

        warnings = []
        if (worklog_count or timers) and not target.is_time_tracking_enabled:
            warnings.append("TIME_TRACKING_DISABLED_IN_TARGET")
        if locked:
            warnings.append("LOCKED_WORKLOGS")
        if client_changes:
            warnings.append("CLIENT_CHANGES")
        if self.labels_dropped:
            warnings.append("LABELS_DROPPED")
        if self.assignees_dropped_ids:
            warnings.append("ASSIGNEES_DROPPED")
        if self.property_plan["dropped"] or any(m["dropped_options"] for m in self.property_plan["mapped"]):
            warnings.append("PROPERTIES_DROPPED")
        if cycles:
            warnings.append("CYCLE_CLEARED")
        if modules:
            warnings.append("MODULES_CLEARED")
        if any(i.estimate_point_id and not self.estimate_by_item.get(i.id) for i in self.items):
            warnings.append("ESTIMATE_CLEARED")
        if parent is not None:
            warnings.append("PARENT_DETACHED")
        if self.detached_children:
            warnings.append("SUB_ITEMS_DETACHED")
        if self.archived_moving_sub_items:
            warnings.append("ARCHIVED_SUB_ITEMS_MOVE")
        if any(g["match"] == STATE_MATCH_NONE for g in self.state_groups.values()):
            warnings.append("NO_TARGET_STATE")

        return {
            "source_project": project_lite(source),
            "target_project": project_lite(target),
            "work_item": {
                "id": str(issue.id),
                "name": issue.name,
                "identifier": identifier_of(source, issue.sequence_id),
            },
            "next_identifier": identifier_of(target, last_sequence + 1),
            "sub_items_mode": self.mode,
            "sub_items": [
                {"id": str(c.id), "name": c.name, "identifier": identifier_of(source, c.sequence_id)}
                for c in self.subtree
            ],
            # FORK: archived sub-items that travel with the item (move-together mode only). They are
            # allowed to move and stay archived in the target; the dialog warns before confirm.
            "archived_sub_items": [
                {"id": str(c.id), "name": c.name, "identifier": identifier_of(source, c.sequence_id)}
                for c in self.archived_moving_sub_items
            ],
            "parent": (
                {"id": str(parent.id), "name": parent.name, "identifier": identifier_of(source, parent.sequence_id)}
                if parent is not None
                else None
            ),
            "states": [
                {
                    "source": state_lite(g["source"]),
                    "target": state_lite(g["target"]),
                    "match": g["match"],
                    "work_item_ids": [str(i) for i in g["items"]],
                }
                for g in self.state_groups.values()
            ],
            "target_states": [state_lite(s) for s in self.mapper.target_states],
            "labels": {
                "kept": [{"source": label_lite(s), "target": label_lite(t)} for s, t in self.labels_kept],
                "dropped": [label_lite(label) for label in self.labels_dropped],
            },
            "assignees": {
                "kept": [user_lite(users[u]) for u in self.assignees_kept_ids if u in users],
                "dropped": [user_lite(users[u]) for u in self.assignees_dropped_ids if u in users],
            },
            "subscribers_dropped": [user_lite(users[u]) for u in self.subscribers_dropped_ids if u in users],
            "cycle": next(({"id": str(c.id), "name": c.name} for c in cycles.values()), None),
            "modules": [{"id": str(m.id), "name": m.name} for m in modules.values()],
            "estimate": {
                "source": (
                    {"id": str(issue.estimate_point.id), "value": issue.estimate_point.value}
                    if issue.estimate_point_id
                    else None
                ),
                "target": (
                    {"id": str(estimate_target.id), "value": estimate_target.value} if estimate_target else None
                ),
                "action": estimate_action,
            },
            "issue_type": {"action": type_action},
            "custom_properties": {
                "mapped": [
                    {
                        "source": _prop_lite(m["source"]),
                        "target": _prop_lite(m["target"]),
                        "dropped_options": m["dropped_options"],
                    }
                    for m in self.property_plan["mapped"]
                ],
                "dropped": [_prop_lite(p) for p in self.property_plan["dropped"]],
            },
            "worklogs": {
                "count": worklog_count,
                "total_minutes": worklog_minutes,
                "locked_count": locked,
                "running_timers": timers,
                "target_time_tracking_enabled": bool(target.is_time_tracking_enabled),
                "client_changes": bool(client_changes),
            },
            "carried": {
                "comments": IssueComment.objects.filter(issue_id__in=self.item_ids).count(),
                "attachments": FileAsset.objects.filter(
                    issue_id__in=self.item_ids, entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT
                ).count(),
                "links": IssueLink.objects.filter(issue_id__in=self.item_ids).count(),
                "relations": IssueRelation.objects.filter(issue_id__in=self.item_ids).count()
                + IssueRelation.objects.filter(related_issue_id__in=self.item_ids)
                .exclude(issue_id__in=self.item_ids)
                .count(),
            },
            "warnings": warnings,
        }


def _prop_lite(prop):
    return {"id": str(prop.id), "display_name": prop.display_name, "property_type": prop.property_type}


def _plan_property_values(item_ids, mapper, type_by_item):
    """Map custom property values (plane.properties) by property name + type.

    Returns {"mapped": [{source, target, dropped_options}], "dropped": [IssueProperty],
             "rows": [(value_row, target_property | None, target_option_id | None)]}.
    """
    from plane.properties.models import IssueProperty, IssuePropertyOption, IssuePropertyValue

    values = list(IssuePropertyValue.objects.filter(issue_id__in=item_ids).select_related("property", "value_option"))
    if not values:
        return {"mapped": [], "dropped": [], "rows": []}

    target_props = list(IssueProperty.objects.filter(project_id=mapper.target.id, is_active=True))
    options_by_prop = {}
    for option in IssuePropertyOption.objects.filter(property__in=target_props, is_active=True).order_by("sort_order"):
        options_by_prop.setdefault(option.property_id, {}).setdefault((option.name or "").strip().lower(), option)

    def find_target(prop, item_id):
        item_type = type_by_item.get(item_id)
        candidates = [
            p
            for p in target_props
            if p.name == prop.name
            and p.property_type == prop.property_type
            and (p.relation_type or None) == (prop.relation_type or None)
            and (p.issue_type_id is None or (item_type and str(p.issue_type_id) == str(item_type)))
        ]
        return candidates[0] if candidates else None

    mapped, dropped, rows = OrderedDict(), OrderedDict(), []
    single_seen = set()
    for value in values:
        prop = value.property
        target = find_target(prop, value.issue_id)
        if target is None:
            dropped.setdefault(prop.id, prop)
            rows.append((value, None, None))
            continue
        entry = mapped.setdefault(prop.id, {"source": prop, "target": target, "dropped_options": []})
        option_id = None
        keep = True
        if prop.property_type == "OPTION":
            option = value.value_option
            match = options_by_prop.get(target.id, {}).get((option.name or "").strip().lower()) if option else None
            if match is None:
                keep = False
                if option is not None and option.name not in entry["dropped_options"]:
                    entry["dropped_options"].append(option.name)
            else:
                option_id = match.id
        elif prop.property_type == "RELATION" and prop.relation_type == "USER":
            if value.value_uuid is None or value.value_uuid not in mapper.member_ids:
                keep = False
        if keep and not target.is_multi:
            key = (value.issue_id, target.id)
            if key in single_seen:
                keep = False
            single_seen.add(key)
        rows.append((value, target if keep else None, option_id))
    return {"mapped": list(mapped.values()), "dropped": list(dropped.values()), "rows": rows}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def build_move_plan(issue, target, user, sub_items_mode=SUB_ITEMS_MOVE):
    validate_move(issue, target, user)
    return _Analysis(issue, target, sub_items_mode).as_plan()


def move_work_item(issue, target, user, sub_items_mode=SUB_ITEMS_MOVE, state_map=None, notify=True):
    """Move ``issue`` (and, in "move" mode, its subtree) to ``target``.

    ``state_map`` maps source state ids to target state ids (user overrides);
    unmapped states use the default rule. Returns a MoveResult dict.
    """
    validate_move(issue, target, user)
    state_map = state_map or {}
    if not isinstance(state_map, dict):
        raise MoveError("INVALID_STATE_MAP", "state_map must be an object.")

    with transaction.atomic():
        # Re-read the root under a row lock so concurrent moves serialise.
        locked_id = Issue.objects.select_for_update().filter(pk=issue.id).values_list("id", flat=True).first()
        if locked_id is None:
            raise MoveError("NOT_FOUND", "Work item not found.", 404)
        issue = Issue.objects.select_related("project", "state", "parent").get(pk=locked_id)
        validate_move(issue, target, user)
        analysis = _Analysis(issue, target, sub_items_mode)
        source = analysis.source
        mapper = analysis.mapper

        # FORK: lock the sub-items too (ancestors first, same order as a move rooted at a
        # sub-item would take them) and make sure none left the source project between
        # the analysis and now — e.g. a concurrent move of a sub-item on its own, which
        # would otherwise be silently overwritten here.
        for sub_item in analysis.subtree if sub_items_mode == SUB_ITEMS_MOVE else []:
            if not (
                Issue.objects.select_for_update()
                .filter(pk=sub_item.id, project_id=source.id, parent_id=sub_item.parent_id)
                .values_list("id", flat=True)
                .first()
            ):
                raise MoveError("CONFLICT", "The work item's sub-items changed while moving; try again.", 409)

        overrides = {}
        for src_state_id, tgt_state_id in state_map.items():
            tgt_state = mapper.validate_target_state_id(tgt_state_id)
            if tgt_state is None:
                raise MoveError("INVALID_STATE_MAP", "state_map points at a state outside the target project.")
            overrides[str(src_state_id)] = tgt_state

        # Allocate sequences exactly like Issue.save(): per-project advisory lock.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [convert_uuid_to_integer(target.id)])
        last_sequence = IssueSequence.objects.filter(project=target).aggregate(m=Max("sequence"))["m"] or 0

        now = timezone.now()
        epoch = int(now.timestamp())
        activities = []
        moved = []
        old_parent = issue.parent if issue.parent_id else None

        for offset, item in enumerate(analysis.items, start=1):
            old_identifier = identifier_of(source, item.sequence_id)
            new_sequence = last_sequence + offset
            group = analysis.state_groups[item.state_id]
            new_state = overrides.get(str(item.state_id)) or group["target"]

            item.project = target
            item.workspace_id = target.workspace_id
            item.sequence_id = new_sequence
            item.state = new_state
            item.estimate_point = analysis.estimate_by_item.get(item.id)
            item.type_id = analysis.type_by_item.get(item.id)
            if item.id == issue.id:
                item.parent = None  # its parent stays behind
            item.save(
                update_fields=[
                    "project",
                    "workspace",
                    "sequence_id",
                    "state",
                    "estimate_point",
                    "type",
                    "parent",
                    "updated_at",
                    "updated_by",
                ]
            )
            IssueSequence.objects.create(issue=item, sequence=new_sequence, project=target)

            new_identifier = identifier_of(target, new_sequence)
            moved.append({"id": str(item.id), "old_identifier": old_identifier, "new_identifier": new_identifier})
            activities.append(
                IssueActivity(
                    issue_id=item.id,
                    actor_id=user.id,
                    verb="updated",
                    field="project",
                    old_value=old_identifier,
                    new_value=new_identifier,
                    old_identifier=source.id,
                    new_identifier=target.id,
                    comment=f"moved the work item from {old_identifier} to {new_identifier}",
                    project_id=target.id,
                    workspace_id=target.workspace_id,
                    epoch=epoch,
                )
            )

        item_ids = analysis.item_ids

        # Rows that simply follow the item.
        for model, fk in REPOINT_BY_ISSUE:
            model.all_objects.filter(**{f"{fk}_id__in": item_ids}).update(project_id=target.id)
        # FORK: time-tracking-v2 — worklogs carry a snapshot of the item's identifier
        # (e.g. "TTP-1"); refresh it now that the item has its new project/sequence.
        refresh_worklog_snapshots(item_ids)
        comment_ids = list(IssueComment.all_objects.filter(issue_id__in=item_ids).values_list("id", flat=True))
        for model, fk in REPOINT_BY_COMMENT:
            model.all_objects.filter(**{f"{fk}_id__in": comment_ids}).update(project_id=target.id)
        # FORK: a comment's Description row (and its versions) carries a project FK
        # too; left behind it would cascade-delete the moved comment with the
        # source project.
        description_ids = [
            d
            for d in IssueComment.all_objects.filter(issue_id__in=item_ids).values_list("description_id", flat=True)
            if d
        ]
        Description.all_objects.filter(id__in=description_ids).update(project_id=target.id)
        DescriptionVersion.all_objects.filter(description_id__in=description_ids).update(project_id=target.id)

        # Assignees: keep target members/admins only.
        IssueAssignee.objects.filter(issue_id__in=item_ids, assignee_id__in=analysis.assignees_dropped_ids).delete()
        IssueAssignee.all_objects.filter(issue_id__in=item_ids).update(project_id=target.id)

        # Labels: remap by name, drop the rest, never duplicate a target label on one item.
        seen_labels, drop_label_rows = set(), []
        for issue_label in IssueLabel.objects.filter(issue_id__in=item_ids).order_by("created_at"):
            target_label = analysis.label_target_by_source.get(issue_label.label_id)
            key = (issue_label.issue_id, target_label.id if target_label else None)
            if target_label is None or key in seen_labels:
                drop_label_rows.append(issue_label.id)
                continue
            seen_labels.add(key)
            IssueLabel.objects.filter(pk=issue_label.id).update(
                label_id=target_label.id, project_id=target.id, updated_at=now
            )
        IssueLabel.objects.filter(pk__in=drop_label_rows).delete()  # soft delete, like a label removal

        # Subscribers: keep anyone who can still open the item.
        IssueSubscriber.objects.filter(
            issue_id__in=item_ids, subscriber_id__in=analysis.subscribers_dropped_ids
        ).delete()
        IssueSubscriber.all_objects.filter(issue_id__in=item_ids).update(project_id=target.id)

        # Cycle / modules are project-scoped: clear.
        CycleIssue.objects.filter(issue_id__in=item_ids).delete()
        ModuleIssue.objects.filter(issue_id__in=item_ids).delete()

        # Custom property values (fork).
        _apply_property_plan(analysis.property_plan, target)

        # In-app links (notifications, recents) open the item under its project.
        Notification.objects.filter(entity_name="issue", entity_identifier__in=item_ids).update(project_id=target.id)
        UserRecentVisit.objects.filter(entity_name="issue", entity_identifier__in=item_ids).update(project_id=target.id)

        # Parent left behind / sub-items detached.
        if old_parent is not None:
            activities.append(_parent_activity(issue, old_parent, source, user, target, epoch))
        for child in analysis.detached_children:
            Issue.objects.filter(pk=child.id).update(parent=None, updated_at=now)
            activities.append(
                IssueActivity(
                    issue_id=child.id,
                    actor_id=user.id,
                    verb="updated",
                    field="parent",
                    old_value=moved[0]["new_identifier"],
                    new_value="",
                    old_identifier=issue.id,
                    new_identifier=None,
                    comment="removed the parent work item (moved to another project)",
                    project_id=source.id,
                    workspace_id=source.workspace_id,
                    epoch=epoch,
                )
            )

        created_activities = IssueActivity.objects.bulk_create(activities)

        if notify:
            transaction.on_commit(lambda: _notify(created_activities, user, target, analysis))

    root = moved[0]
    return {
        "id": root["id"],
        "project_id": str(target.id),
        "sequence_id": last_sequence + 1,
        "identifier": root["new_identifier"],
        "moved": moved,
        "detached_sub_item_ids": [str(c.id) for c in analysis.detached_children],
    }


def _parent_activity(issue, old_parent, source, user, target, epoch):
    return IssueActivity(
        issue_id=issue.id,
        actor_id=user.id,
        verb="updated",
        field="parent",
        old_value=identifier_of(source, old_parent.sequence_id),
        new_value="",
        old_identifier=old_parent.id,
        new_identifier=None,
        comment="removed the parent work item (it stays in the source project)",
        project_id=target.id,
        workspace_id=target.workspace_id,
        epoch=epoch,
    )


def _apply_property_plan(plan, target):
    from plane.properties.models import IssuePropertyValue

    drop_ids = []
    for value, target_prop, option_id in plan["rows"]:
        if target_prop is None:
            drop_ids.append(value.id)
            continue
        changes = {"project_id": target.id, "property_id": target_prop.id, "updated_at": timezone.now()}
        if option_id is not None:
            changes["value_option_id"] = option_id
        IssuePropertyValue.objects.filter(pk=value.id).update(**changes)
    IssuePropertyValue.objects.filter(pk__in=drop_ids).delete()  # soft delete


def _notify(activities, user, target, analysis):
    """Tell subscribers, as a normal update (issue.activity.updated), after commit."""
    from plane.app.serializers import IssueActivitySerializer
    from plane.bgtasks.notification_task import notifications

    by_item = OrderedDict()
    for activity in activities:
        if activity.project_id == target.id:
            by_item.setdefault(activity.issue_id, []).append(activity)
    for item_id, item_activities in by_item.items():
        item = next((i for i in analysis.items if i.id == item_id), None)
        snapshot = json.dumps({"description_html": item.description_html if item else ""})
        notifications.delay(
            type="issue.activity.updated",
            issue_id=str(item_id),
            actor_id=str(user.id),
            project_id=str(target.id),
            subscriber=True,
            issue_activities_created=json.dumps(
                IssueActivitySerializer(item_activities, many=True).data, cls=DjangoJSONEncoder
            ),
            requested_data=snapshot,
            current_instance=snapshot,
        )


# ---------------------------------------------------------------------------
# Old-identifier lookup
# ---------------------------------------------------------------------------
def resolve_moved_work_item(project, sequence):
    """Return the work item that used to be ``<project.identifier>-<sequence>``
    and has since been moved (possibly more than once), or None.

    The source ``IssueSequence`` row is never removed by a move, so it still
    points at the item wherever it lives now.
    """
    row = (
        IssueSequence.objects.filter(project_id=project.id, sequence=sequence, issue__isnull=False)
        .select_related("issue", "issue__project")
        .order_by("-created_at")
        .first()
    )
    if row is None or row.issue is None or row.issue.deleted_at is not None:
        return None
    issue = row.issue
    if issue.project_id == project.id and issue.sequence_id == sequence:
        return None  # not moved — the caller's normal lookup handles it
    return issue
