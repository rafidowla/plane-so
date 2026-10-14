# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: move-work-item (PSR-91) / draft-project-change (PSR-92).

Project-to-project field mapping shared by "move a work item to another
project" (plane.utils.work_item_move) and "change a draft's project"
(WorkspaceDraftIssueViewSet). See docs/move-work-item-design.md §2.

Every rule is idempotent: a value that already belongs to the target project
is kept unchanged, so callers may pass partially-mapped data through it.
"""

# Python imports
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Django imports
from django.db.models import Q

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    Cycle,
    EstimatePoint,
    Issue,
    Label,
    Module,
    Project,
    ProjectMember,
    State,
    WorkspaceMember,
)
from plane.db.models.issue_type import ProjectIssueType

STATE_MATCH_NAME = "name"
STATE_MATCH_GROUP = "group"
STATE_MATCH_DEFAULT = "default"
STATE_MATCH_SAME = "same"  # already a target-project state
STATE_MATCH_NONE = "none"  # target has no usable state at all

# Assignees must be members or admins of the target project (mirrors
# IssueCreateSerializer.validate: role__gte=15).
ASSIGNABLE_MIN_ROLE = ROLE.MEMBER.value


def _str(value):
    return str(value) if value is not None else None


def state_lite(state):
    if state is None:
        return None
    return {"id": str(state.id), "name": state.name, "group": state.group, "color": state.color}


def label_lite(label):
    return {"id": str(label.id), "name": label.name, "color": label.color}


def user_lite(user):
    return {"id": str(user.id), "display_name": user.display_name, "avatar_url": user.avatar_url}


def project_lite(project):
    return {"id": str(project.id), "name": project.name, "identifier": project.identifier}


def has_project_role(user, project, allowed_roles: Iterable[int]) -> bool:
    """Same rule as plane.app.permissions.allow_permission at PROJECT level:
    the user's active project role is in ``allowed_roles``, or the user is an
    active project member (any role) AND a workspace admin."""
    allowed_roles = [r.value if isinstance(r, ROLE) else r for r in allowed_roles]
    membership = ProjectMember.objects.filter(member=user, project_id=project.id, is_active=True).first()
    if membership is None:
        return False
    if membership.role in allowed_roles:
        return True
    return WorkspaceMember.objects.filter(
        member=user, workspace_id=project.workspace_id, role=ROLE.ADMIN.value, is_active=True
    ).exists()


def can_write_in_project(user, project) -> bool:
    """Edit (source) / create (target) right used by move and draft posting."""
    return has_project_role(user, project, [ROLE.ADMIN, ROLE.MEMBER])


def is_project_member(user, project) -> bool:
    return ProjectMember.objects.filter(member=user, project_id=project.id, is_active=True).exists()


@dataclass
class ProjectFieldMapper:
    """Maps project-scoped values of a work item from ``source`` to ``target``.

    Lookups are cached per instance, so one mapper can be reused for a whole
    subtree of work items.
    """

    source: Project
    target: Project
    _target_states: Optional[list] = field(default=None, repr=False)
    _target_labels_by_name: Optional[dict] = field(default=None, repr=False)
    _assignable_ids: Optional[set] = field(default=None, repr=False)
    _member_ids: Optional[set] = field(default=None, repr=False)

    # ------------------------------------------------------------------ states
    @property
    def target_states(self):
        if self._target_states is None:
            # State.objects excludes triage states; triage is never a target.
            self._target_states = list(State.objects.filter(project_id=self.target.id).order_by("sequence"))
        return self._target_states

    def default_target_state(self):
        states = self.target_states
        return next((s for s in states if s.default), None) or (states[0] if states else None)

    def map_state(self, state):
        """Return ``(target_state, match)`` for a source ``State`` (or None)."""
        if state is not None and state.project_id == self.target.id and not state.is_triage:
            return state, STATE_MATCH_SAME
        states = self.target_states
        if not states:
            return None, STATE_MATCH_NONE
        if state is not None:
            name = (state.name or "").strip().lower()
            by_name = next((s for s in states if (s.name or "").strip().lower() == name), None)
            if by_name is not None:
                return by_name, STATE_MATCH_NAME
            same_group = [s for s in states if s.group == state.group]
            if same_group:
                return next((s for s in same_group if s.default), same_group[0]), STATE_MATCH_GROUP
        return self.default_target_state(), STATE_MATCH_DEFAULT

    def _scope_project_ids(self):
        # FORK: security — ids coming from a request body are only looked up in the two
        # projects involved; anything else is treated as "not set", so the mapping
        # endpoint cannot be used to read names of labels/states/users by UUID.
        return [self.source.id, self.target.id]

    def map_state_id(self, state_id):
        state = (
            State.all_state_objects.filter(pk=state_id, project_id__in=self._scope_project_ids()).first()
            if _as_uuid(state_id)
            else None
        )
        return self.map_state(state)

    def validate_target_state_id(self, state_id):
        """Return the target ``State`` for a user-supplied override, or None if invalid."""
        if not state_id:
            return None
        return next((s for s in self.target_states if str(s.id) == str(state_id)), None)

    # ------------------------------------------------------------------ labels
    @property
    def target_labels_by_name(self):
        if self._target_labels_by_name is None:
            self._target_labels_by_name = {}
            for label in Label.objects.filter(project_id=self.target.id).order_by("sort_order"):
                self._target_labels_by_name.setdefault((label.name or "").strip().lower(), label)
        return self._target_labels_by_name

    def map_labels(self, labels):
        """Return ``(kept, dropped)``: kept is a list of ``(source_label, target_label)``
        pairs (deduplicated on the target label), dropped a list of source labels.

        Workspace-wide labels (``project`` is NULL) and labels already in the
        target are kept as-is.
        """
        kept, dropped, seen = [], [], set()
        for label in labels:
            if label.project_id is None or label.project_id == self.target.id:
                target_label = label
            else:
                target_label = self.target_labels_by_name.get((label.name or "").strip().lower())
            if target_label is None:
                dropped.append(label)
                continue
            if target_label.id in seen:
                continue
            seen.add(target_label.id)
            kept.append((label, target_label))
        return kept, dropped

    def map_label_ids(self, label_ids):
        ids = [i for i in label_ids or [] if _as_uuid(i)]
        labels = list(
            Label.objects.filter(id__in=ids, workspace_id=self.target.workspace_id).filter(
                Q(project_id__in=self._scope_project_ids()) | Q(project__isnull=True)
            )
        )  # FORK: security — scoped, see _scope_project_ids
        return self.map_labels(labels)

    # --------------------------------------------------------------- assignees
    @property
    def assignable_ids(self):
        if self._assignable_ids is None:
            self._assignable_ids = set(
                ProjectMember.objects.filter(
                    project_id=self.target.id, is_active=True, role__gte=ASSIGNABLE_MIN_ROLE
                ).values_list("member_id", flat=True)
            )
        return self._assignable_ids

    @property
    def member_ids(self):
        """Any active member of the target (guests included)."""
        if self._member_ids is None:
            self._member_ids = set(
                ProjectMember.objects.filter(project_id=self.target.id, is_active=True).values_list(
                    "member_id", flat=True
                )
            )
        return self._member_ids

    def split_assignees(self, user_ids):
        """Return ``(kept_ids, dropped_ids)`` preserving input order."""
        kept, dropped = [], []
        for uid in user_ids or []:
            if not uid:
                continue
            (kept if _as_uuid(uid) in self.assignable_ids else dropped).append(uid)
        return kept, dropped

    # ---------------------------------------------------------------- estimate
    def map_estimate_point(self, point):
        """Same estimate *type* and a point with the same value → mapped, else None."""
        if point is None:
            return None
        if point.project_id == self.target.id:
            return point
        target_estimate_id = self.target.estimate_id
        if not target_estimate_id:
            return None
        source_estimate = point.estimate
        candidates = EstimatePoint.objects.filter(
            estimate_id=target_estimate_id, project_id=self.target.id, value=point.value
        ).select_related("estimate")
        match = candidates.first()
        if match is None or source_estimate is None or match.estimate.type != source_estimate.type:
            return None
        return match

    def map_estimate_point_id(self, point_id):
        if not point_id:
            return None
        if _as_uuid(point_id) is None:
            return None
        point = (
            EstimatePoint.objects.filter(pk=point_id, project_id__in=self._scope_project_ids())
            .select_related("estimate")
            .first()
        )  # FORK: security — scoped, see _scope_project_ids
        return self.map_estimate_point(point)

    # ------------------------------------------------------------ issue type
    def map_issue_type_id(self, type_id):
        if not type_id:
            return None
        enabled = ProjectIssueType.objects.filter(project_id=self.target.id, issue_type_id=type_id).exists()
        return type_id if enabled else None

    # ------------------------------------------- cycle / modules / parent
    def keep_cycle_id(self, cycle_id):
        """Cycles are project-scoped: only an id already in the target survives."""
        if not cycle_id:
            return None
        return cycle_id if Cycle.objects.filter(pk=cycle_id, project_id=self.target.id).exists() else None

    def keep_module_ids(self, module_ids):
        ids = [m for m in module_ids or [] if m]
        if not ids:
            return []
        valid = {
            str(m) for m in Module.objects.filter(pk__in=ids, project_id=self.target.id).values_list("id", flat=True)
        }
        return [m for m in ids if str(m) in valid]

    def keep_parent_id(self, parent_id):
        if not parent_id:
            return None
        return parent_id if Issue.issue_objects.filter(pk=parent_id, project_id=self.target.id).exists() else None

    # --------------------------------------------- flat-payload convenience
    def map_payload(self, values: dict) -> dict:
        """Map a flat work-item payload (draft form / draft row) into the target.

        Input keys (all optional): state_id, label_ids, assignee_ids,
        estimate_point, type_id, parent_id, cycle_id, module_ids.
        Returns the mapped values plus ``state_match`` and a ``dropped`` summary.
        """
        state, state_match = self.map_state_id(values.get("state_id"))
        if not values.get("state_id"):
            # No state chosen yet: let the target's default apply.
            state, state_match = self.default_target_state(), STATE_MATCH_DEFAULT
        kept_labels, dropped_labels = self.map_label_ids(values.get("label_ids"))
        kept_assignees, dropped_assignees = self.split_assignees(values.get("assignee_ids"))
        estimate = self.map_estimate_point_id(values.get("estimate_point"))
        type_id = self.map_issue_type_id(values.get("type_id"))
        parent_id = self.keep_parent_id(values.get("parent_id"))
        cycle_id = self.keep_cycle_id(values.get("cycle_id"))
        module_ids = self.keep_module_ids(values.get("module_ids"))

        from plane.db.models import User  # local import keeps module import light

        # FORK: security — only name people who belong to this workspace
        dropped_users = list(
            User.objects.filter(
                id__in=[a for a in dropped_assignees if _as_uuid(a)],
                member_workspace__workspace_id=self.target.workspace_id,
                member_workspace__is_active=True,
            ).distinct()
        )
        return {
            "state_id": _str(state.id) if state else None,
            "state_match": state_match,
            "label_ids": [str(t.id) for _, t in kept_labels],
            "assignee_ids": [str(a) for a in kept_assignees],
            "estimate_point": _str(estimate.id) if estimate else None,
            "type_id": _str(type_id),
            "parent_id": _str(parent_id),
            "cycle_id": _str(cycle_id),
            "module_ids": [str(m) for m in module_ids],
            "dropped": {
                "labels": [label_lite(label) for label in dropped_labels],
                "assignees": [user_lite(u) for u in dropped_users],
                "cycle": bool(values.get("cycle_id")) and cycle_id is None,
                "modules": len(module_ids) < len([m for m in values.get("module_ids") or [] if m]),
                "estimate": bool(values.get("estimate_point")) and estimate is None,
                "parent": bool(values.get("parent_id")) and parent_id is None,
                "type": bool(values.get("type_id")) and type_id is None,
            },
        }


def _as_uuid(value):
    import uuid

    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def resolve_target_project(workspace_id, target_project_id):
    """Return the target ``Project`` in the same workspace, or None."""
    if not target_project_id or _as_uuid(target_project_id) is None:
        return None
    return Project.objects.filter(pk=target_project_id, workspace_id=workspace_id).first()


__all__ = [
    "ProjectFieldMapper",
    "can_write_in_project",
    "has_project_role",
    "is_project_member",
    "label_lite",
    "project_lite",
    "resolve_target_project",
    "state_lite",
    "user_lite",
]
