# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S6 — per-work-item time estimates: the "required" gate and the lock.

DESIGN §2 items 33-35 (PO decisions, binding).

**Mode.** ``Project.time_estimate_mode`` is ``off`` / ``optional`` / ``required``. It only
has an effect while the project has time tracking on (``is_time_tracking_enabled``): with
tracking off nobody logs time, so nothing is gated and estimates can't be set.

**Gate** (``required`` only). Starting work on an item without an estimate is refused with
400 ``{"code": "estimate_required"}``. Every way into a Started/Completed state is gated
(``gate_state_change``):

* a state change on an existing item (app ``IssueCreateSerializer.validate``, public API
  ``IssueSerializer.validate``);
* creating an item straight into a Started/Completed state (no estimate can exist yet),
  through the same two serializers;
* accepting an intake item into the project's default state (app and API intake serializers);
* moving an item into a "required" project (``utils.work_item_move``);

and starting a timer, adding a time entry, submitting or approving a missing-time request
is gated by ``check_can_log``.

Moving into Cancelled is never gated. An item that was already in a Started/Completed
state when the mode became ``required`` is **grandfathered**: never blocked, and
``grandfathered`` is reported (and stored on the estimate when one is added later). The
flag is a stored row (``IssueEstimateGrandfather``, written by ``grandfather_project`` when
the gate comes into force and backfilled by migration 0133), never derived from the item's
current state — the state is user-controlled, so deriving it let an item skip the gate by
being created or moved straight into a Started state.

**Lock.** The original estimate locks when work starts — first of: timer start, first time
entry, move into Started/Completed (fork receivers in ``db/models/time_tracking.py`` call
``lock_estimate`` / ``lock_if_work_started_state``; ``set_estimate`` re-checks, for
bulk paths that skip signals). Before the lock a new value replaces the original; after
it, a new value is a re-estimate appended to the history and the original never changes.
"""

# Django imports
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

# Third party imports
from rest_framework.exceptions import APIException

# Module imports
from plane.db.models import (
    Issue,
    IssueEstimateGrandfather,
    IssueTimeEstimate,
    IssueTimeEstimateRevision,
    IssueWorklog,
    Project,
    State,
    StateGroup,
    TimeEstimateMode,
    TimeEstimateRevisionKind,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    WorklogTimer,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.timer import TimeTrackingError

# Entering one of these groups is "starting work" (gate + lock).
WORK_GROUPS = (StateGroup.STARTED.value, StateGroup.COMPLETED.value)
MAX_MINUTES = 1000 * 60
ESTIMATE_REQUIRED_MESSAGE = "Add an estimate first."


class EstimateRequired(APIException):
    """Raised from serializer ``validate``: DRF renders it as a flat 400
    ``{"error", "code": "estimate_required", "issue_id"}`` (a ValidationError would wrap
    every value in a list)."""

    status_code = 400
    default_code = "estimate_required"

    def __init__(self, issue_id=None):
        super().__init__(estimate_required_payload(issue_id))


def estimate_required_payload(issue_id=None):
    payload = {"error": ESTIMATE_REQUIRED_MESSAGE, "code": "estimate_required"}
    if issue_id is not None:
        payload["issue_id"] = str(issue_id)
    return payload


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def _project(project_or_id):
    if isinstance(project_or_id, Project):
        return project_or_id
    return Project.objects.only("id", "is_time_tracking_enabled", "time_estimate_mode").get(pk=project_or_id)


def effective_mode(project_or_id):
    """The mode in force: ``off`` whenever the project has time tracking turned off."""
    project = _project(project_or_id)
    if not project.is_time_tracking_enabled:
        return TimeEstimateMode.OFF.value
    return project.time_estimate_mode or TimeEstimateMode.OFF.value


def is_required(project_or_id):
    return effective_mode(project_or_id) == TimeEstimateMode.REQUIRED.value


def has_estimate(issue_id):
    return IssueTimeEstimate.objects.filter(issue_id=issue_id).exists()


def _state_group(state_id):
    if state_id is None:
        return None
    return State.all_state_objects.filter(pk=state_id).values_list("group", flat=True).first()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def is_grandfathered(issue_id):
    """Was the item already in progress when its project's gate came into force."""
    return IssueEstimateGrandfather.objects.filter(issue_id=issue_id).exists()


def grandfather_project(project_id):
    """Flag every item of the project that is in a Started/Completed state without an estimate.
    Called when the gate comes into force (``Project`` post_save receiver) and by migration 0133's
    backfill (which inlines the same query). Idempotent."""
    in_work_group = State.all_state_objects.filter(pk=OuterRef("state_id"), group__in=WORK_GROUPS)
    has_live_estimate = IssueTimeEstimate.objects.filter(issue_id=OuterRef("pk"))
    items = (
        Issue.all_objects.filter(project_id=project_id, deleted_at__isnull=True)
        .filter(Exists(in_work_group))
        .exclude(Exists(has_live_estimate))
        .values_list("id", "workspace_id")
    )
    IssueEstimateGrandfather.objects.bulk_create(
        (IssueEstimateGrandfather(issue_id=i, workspace_id=w, project_id=project_id) for i, w in items.iterator()),
        batch_size=1000,
        ignore_conflicts=True,
    )


def clear_grandfathered(issue_ids):
    """The items left their project: the flag belongs to the project it was written for."""
    IssueEstimateGrandfather.objects.filter(issue_id__in=issue_ids).delete()


def _default_state_group(project_id):
    return State.all_state_objects.filter(project_id=project_id, default=True).values_list("group", flat=True).first()


def gate_state_change(instance, new_state, project_id=None):
    """Serializer hook: refuse putting an item into Started/Completed without an estimate when
    its project requires one. ``instance`` is the existing item (a state change, an intake
    accept) or None (creation; ``new_state`` None means the project's default state). Raises
    ``EstimateRequired``."""
    pid = project_id or getattr(instance, "project_id", None)
    if pid is None or not is_required(pid):
        return
    if new_state is None:
        if instance is not None:
            return  # state not part of this update
        group = _default_state_group(pid)  # creation without a state: the default state applies
    else:
        group = getattr(new_state, "group", None)
        if instance is not None and str(new_state.pk) == str(instance.state_id):
            return
    if group not in WORK_GROUPS:
        return
    if instance is None:
        raise EstimateRequired()  # a new item cannot have an estimate yet
    if has_estimate(instance.pk) or is_grandfathered(instance.pk):
        return
    raise EstimateRequired(instance.pk)


def gate_move(item, target_project, new_state):
    """Work item move: ``item`` lands in ``new_state`` of ``target_project``. Refused (400
    ``estimate_required``) when the target requires estimates, the landing state is
    Started/Completed and the item has none. The source project's grandfather flag does not
    carry over (``clear_grandfathered`` drops it on the move)."""
    if getattr(new_state, "group", None) not in WORK_GROUPS:
        return
    if not is_required(target_project):
        return
    if has_estimate(item.pk):
        return
    raise EstimateRequired(item.pk)


def check_can_log(issue):
    """Service hook (timer start, manual entry, missing-time submit/approve): refuse logging
    time on an unestimated item of a "required" project, unless it is grandfathered (flagged when
    the gate came into force). Raises ``TimeTrackingError`` (400 ``estimate_required``)."""
    if not is_required(issue.project_id):
        return
    if has_estimate(issue.pk) or is_grandfathered(issue.pk):
        return
    raise TimeTrackingError(estimate_required_payload(issue.pk), 400)


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def lock_estimate(issue_id, at=None):
    """Lock the item's original estimate (no-op when there is none or it is locked)."""
    if issue_id is None:
        return 0
    return IssueTimeEstimate.objects.filter(issue_id=issue_id, locked_at__isnull=True).update(
        locked_at=at or timezone.now()
    )


def lock_if_work_started_state(issue):
    """``post_save`` on Issue: lock when the item now sits in a Started/Completed state.
    One UPDATE; it matches nothing unless an unlocked estimate exists."""
    if issue.pk is None or issue.state_id is None:
        return 0
    in_work_group = State.all_state_objects.filter(pk=issue.state_id, group__in=WORK_GROUPS)
    return (
        IssueTimeEstimate.objects.filter(issue_id=issue.pk, locked_at__isnull=True)
        .filter(Exists(in_work_group))
        .update(locked_at=timezone.now())
    )


def work_started(issue):
    """Has work started on the item: a Started/Completed state, a running timer or an entry."""
    if _state_group(issue.state_id) in WORK_GROUPS:
        return True
    if WorklogTimer.objects.filter(issue_id=issue.pk).exists():
        return True
    return IssueWorklog.objects.filter(issue_id=issue.pk).exists()


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def _user_lite(user):
    if user is None:
        return None
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_url": getattr(user, "avatar_url", None),
    }


def _estimate_data(estimate):
    if estimate is None:
        return None
    return {
        "id": str(estimate.id),
        "original_minutes": estimate.original_minutes,
        "original_by": _user_lite(estimate.original_by),
        "original_set_at": estimate.original_set_at,
        "current_minutes": estimate.current_minutes,
        "current_by": _user_lite(estimate.current_by),
        "current_set_at": estimate.current_set_at,
        "locked": estimate.locked_at is not None,
        "locked_at": estimate.locked_at,
        "grandfathered": estimate.grandfathered,
    }


def _current(issue_id):
    return IssueTimeEstimate.objects.select_related("original_by", "current_by").filter(issue_id=issue_id).first()


def estimate_payload(issue, project=None):
    """The GET/PUT response for one work item."""
    project = project or _project(issue.project_id)
    mode = effective_mode(project)
    estimate = _current(issue.pk)
    started = work_started(issue)
    revisions = (
        IssueTimeEstimateRevision.objects.select_related("set_by").filter(issue_id=issue.pk).order_by("created_at")
    )
    if estimate is not None:
        grandfathered = estimate.grandfathered
    else:
        grandfathered = mode == TimeEstimateMode.REQUIRED.value and is_grandfathered(issue.pk)
    return {
        "issue_id": str(issue.pk),
        "project_id": str(issue.project_id),
        "mode": mode,
        "required": mode == TimeEstimateMode.REQUIRED.value,
        "work_started": started,
        "grandfathered": grandfathered,
        "estimate": _estimate_data(estimate),
        "revisions": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "minutes": r.minutes,
                "set_by": _user_lite(r.set_by),
                "reason": r.reason,
                "created_at": r.created_at,
            }
            for r in revisions
        ],
    }


def _refuse(code, error, status_code=400):
    raise TimeTrackingError({"error": error, "code": code}, status_code)


def _parse_minutes(value):
    """Whole minutes 1..MAX_MINUTES (int, or a digit string); ``None`` means clear."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        minutes = value
    elif isinstance(value, str) and value.strip().isdigit():
        minutes = int(value.strip())
    else:
        minutes = 0
    if not 1 <= minutes <= MAX_MINUTES:
        _refuse("invalid_minutes", f"minutes must be a whole number from 1 to {MAX_MINUTES}.")
    return minutes


def _snapshot(estimate):
    return {
        "issue_id": str(estimate.issue_id),
        "original_minutes": estimate.original_minutes,
        "original_by": str(estimate.original_by_id) if estimate.original_by_id else None,
        "current_minutes": estimate.current_minutes,
        "current_by": str(estimate.current_by_id) if estimate.current_by_id else None,
        "locked_at": estimate.locked_at,
        "grandfathered": estimate.grandfathered,
    }


def _revision(estimate, kind, minutes, user, reason):
    return IssueTimeEstimateRevision.objects.create(
        estimate=estimate, issue_id=estimate.issue_id, minutes=minutes, set_by=user, kind=kind, reason=reason
    )


@transaction.atomic
def set_estimate(issue, user, data):
    """PUT: set, replace (before the lock), re-estimate (after) or clear (before the lock).

    ``data``: ``minutes`` (1..MAX_MINUTES, or null to clear) and optional ``reason``.
    Raises ``TimeTrackingError``: 400 ``estimates_off`` / ``invalid_minutes`` /
    ``estimate_locked`` (clearing a locked estimate).
    """
    project = _project(issue.project_id)
    mode = effective_mode(project)
    if mode == TimeEstimateMode.OFF.value:
        _refuse("estimates_off", "Time estimates are turned off for this project.")
    if "minutes" not in data:
        _refuse("invalid_minutes", f"minutes must be a whole number from 1 to {MAX_MINUTES}.")
    minutes = _parse_minutes(data.get("minutes"))
    reason = str(data.get("reason") or "").strip()

    # Serialize writers on one item (no two "first" estimates; replace vs lock is ordered).
    Issue.all_objects.select_for_update(of=("self",)).only("id").get(pk=issue.pk)
    estimate = IssueTimeEstimate.objects.select_for_update().filter(issue_id=issue.pk).first()
    now = timezone.now()
    started = work_started(issue)
    if estimate is not None and estimate.locked_at is None and started:
        # Work started through a path that sends no signal (bulk update): lock now.
        estimate.locked_at = now
        estimate.save(update_fields=["locked_at", "updated_at"])

    if estimate is None:
        if minutes is None:
            return  # nothing to clear
        estimate = IssueTimeEstimate(
            project_id=issue.project_id,
            workspace_id=issue.workspace_id,
            issue_id=issue.pk,
            original_minutes=minutes,
            original_by=user,
            original_set_at=now,
            current_minutes=minutes,
            current_by=user,
            current_set_at=now,
            locked_at=now if started else None,
            grandfathered=mode == TimeEstimateMode.REQUIRED.value and is_grandfathered(issue.pk),
        )
        estimate.save()
        _revision(estimate, TimeEstimateRevisionKind.ORIGINAL_SET, minutes, user, reason)
        audit.record(
            issue.workspace_id,
            user,
            TimeTrackingAuditTarget.ESTIMATE,
            estimate.id,
            TimeTrackingAuditAction.CREATE,
            after=_snapshot(estimate),
        )
        return

    before = _snapshot(estimate)
    if minutes is None:
        if estimate.locked_at is not None:
            _refuse("estimate_locked", "Work has started: the estimate can be changed but not removed.")
        _revision(estimate, TimeEstimateRevisionKind.CLEARED, None, user, reason)
        estimate.delete()  # soft delete; the revisions keep the history
        audit.record(
            issue.workspace_id,
            user,
            TimeTrackingAuditTarget.ESTIMATE,
            estimate.id,
            TimeTrackingAuditAction.DELETE,
            before=before,
        )
        return

    if estimate.locked_at is None:
        if minutes == estimate.original_minutes and minutes == estimate.current_minutes:
            return
        estimate.original_minutes = minutes
        estimate.original_by = user
        estimate.original_set_at = now
        kind = TimeEstimateRevisionKind.ORIGINAL_REPLACED
    else:
        if minutes == estimate.current_minutes:
            return
        kind = TimeEstimateRevisionKind.RE_ESTIMATE
    estimate.current_minutes = minutes
    estimate.current_by = user
    estimate.current_set_at = now
    estimate.save()
    _revision(estimate, kind, minutes, user, reason)
    audit.record(
        issue.workspace_id,
        user,
        TimeTrackingAuditTarget.ESTIMATE,
        estimate.id,
        TimeTrackingAuditAction.UPDATE,
        before=before,
        after=_snapshot(estimate),
    )


__all__ = [
    "EstimateRequired",
    "WORK_GROUPS",
    "check_can_log",
    "effective_mode",
    "estimate_payload",
    "clear_grandfathered",
    "gate_move",
    "gate_state_change",
    "grandfather_project",
    "has_estimate",
    "is_grandfathered",
    "is_required",
    "lock_estimate",
    "lock_if_work_started_state",
    "set_estimate",
    "work_started",
]
