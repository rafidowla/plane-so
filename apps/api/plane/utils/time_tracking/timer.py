# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — timer start/stop service.

Moved out of ``app/views/time_tracking/worklog.py`` so every entry point (the
per-issue endpoint, the S2 workspace endpoints, the celery sweep, turning a
member's tracking off) starts and stops timers the same way. Behaviour is the
pre-v2 behaviour plus two fixes: the entry date is the WORKSPACE-local date of
the start (was the server/UTC date), and a run that crosses local midnight is
split into one entry per day sharing a ``session_id``.
"""

# Python imports
import uuid

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    Issue,
    IssueWorklog,
    Project,
    ProjectMember,
    ResourceCapacity,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    User,
    WorklogApprovalStatus,
    WorklogSource,
    WorklogTimer,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.snapshot import issue_snapshot
from plane.utils.time_tracking.tracking import tracking_turned_off
from plane.utils.time_tracking.tz import local_today, split_by_local_day, workspace_tz


class TimeTrackingError(Exception):
    """A refused time-tracking action; views turn it into ``Response(payload, status_code)``."""

    def __init__(self, payload, status_code=400):
        super().__init__(payload.get("error", "time tracking error"))
        self.payload = payload
        self.status_code = status_code


class TimerAlreadyRunning(TimeTrackingError):
    def __init__(self, timer, where):
        super().__init__({"error": f"A timer is already running on {where}."}, 400)
        self.timer = timer


def is_project_admin(slug, project_id, user):
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


def is_project_member(slug, project_id, user):
    """Internal roles (admin/member). Guests are clients and excluded."""
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
        is_active=True,
    ).exists()


def resolve_billable_rate(project, logged_by_id):
    """Default billable rate: resource rate, else the project's client default."""
    capacity = ResourceCapacity.objects.filter(workspace_id=project.workspace_id, user_id=logged_by_id).first()
    if capacity and capacity.billable_rate is not None:
        return capacity.billable_rate, capacity.currency
    if project.client_id and project.client and project.client.default_billable_rate is not None:
        return project.client.default_billable_rate, project.client.currency
    return None, "USD"


def issue_label(issue_id):
    """Human-readable reference for an issue, e.g. {"ref": "TTP-1", "name": "..."}.

    Returns None when the issue can no longer be resolved (deleted/archived).
    """
    row = (
        Issue.issue_objects.filter(id=issue_id)
        .values("id", "name", "sequence_id", "project_id", "project__identifier")
        .first()
    )
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "sequence_id": row["sequence_id"],
        "project_id": str(row["project_id"]),
        "project_identifier": row["project__identifier"],
        "ref": f"{row['project__identifier']}-{row['sequence_id']}",
    }


def lock_user_timers(user):
    """Serialize timer start/stop for one user.

    Must be called inside ``transaction.atomic()``. Locking the user's row means
    two concurrent requests from the same user queue up, so there is never a window
    where two timers run (start/start), where a timer is stopped twice
    (stop/stop => duplicate worklogs) or where a start races a stop.
    """
    User.objects.select_for_update().only("id").get(pk=getattr(user, "pk", user))


def stop_timer(timer, user=None, description=None, ended_at=None, actor=None, reason=None):
    """Convert a running timer into timer-sourced worklog(s) and remove the timer.

    Must run inside ``transaction.atomic()`` holding ``lock_user_timers``.

    The run is split at each workspace-local midnight (one entry per local day,
    all sharing one ``session_id``); durations are whole minutes, rounded down,
    one-minute minimum, and the pieces sum to the run's total. Returns the
    created worklogs in time order.

    ``reason`` marks a stop the user did not make themselves (``issue_deleted``,
    ``tracking_disabled``): each entry gets an ``auto_stop`` audit row with
    ``actor`` (None = system).
    """
    user = user or timer.user
    ended_at = ended_at or timezone.now()
    project = timer.project
    pieces = split_by_local_day(timer.started_at, ended_at, workspace_tz(timer.workspace_id))
    billable_rate, currency = resolve_billable_rate(project, user.id)
    issue_ref, issue_name = issue_snapshot(timer.issue_id)
    session_id = uuid.uuid4()
    worklogs = []
    for piece in pieces:
        worklogs.append(
            IssueWorklog.objects.create(
                project=project,
                issue_id=timer.issue_id,
                issue_ref=issue_ref,
                issue_name=issue_name,
                logged_by=user,
                duration=piece["duration"],
                description=timer.description if description is None else description,
                logged_date=piece["logged_date"],
                started_at=piece["started_at"],
                ended_at=piece["ended_at"],
                source=WorklogSource.TIMER,
                is_billable=True,
                billable_rate=billable_rate,
                currency=currency,
                approval_status=WorklogApprovalStatus.DRAFT,
                session_id=session_id,
            )
        )
    timer.delete()
    if reason:
        for worklog in worklogs:
            audit.record(
                worklog.workspace_id,
                actor,
                TimeTrackingAuditTarget.WORKLOG,
                worklog.id,
                TimeTrackingAuditAction.AUTO_STOP,
                after={**audit.worklog_state(worklog), "reason": reason, "timer_id": timer.id},
            )
    return worklogs


def start_timer(slug, project_id, issue_id, user, description=""):
    """Start a timer for ``user`` on an issue, stopping any running one first.

    Returns ``(timer, stopped)``; ``stopped`` is None or
    ``{"issue": issue_label, "worklogs": [...]}`` for the timer that was stopped.
    Raises ``TimeTrackingError`` (or ``TimerAlreadyRunning``) when refused; a
    refused start never touches the running timer.
    """
    project = Project.objects.get(pk=project_id)
    if not project.is_time_tracking_enabled:
        raise TimeTrackingError({"error": "Time tracking is not enabled for this project."}, 400)
    # FORK: security — the work item must live in this project. Without this a member
    # of any time-tracked project could time a work item of another project (or
    # workspace) by UUID, and the running-timer endpoint would then echo back that
    # item's name and identifier.
    if not Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).exists():
        raise TimeTrackingError({"error": "Work item not found."}, 404)
    # Timers only run "today", so the member's status today decides.
    if tracking_turned_off(project.workspace_id, user.id, local_today(workspace_tz(project.workspace_id))):
        raise TimeTrackingError(
            {"error": "Time tracking is turned off for you in this workspace.", "code": "tracking_disabled"},
            403,
        )
    # FORK: PSR-101 — starting a timer while another one runs elsewhere stops
    # the running one (logging its time as a normal worklog) and starts the new
    # one, all in ONE transaction under a per-user row lock: no window where two
    # timers run, and no time is lost if any step fails (everything rolls back).
    with transaction.atomic():
        lock_user_timers(user)
        # One running timer per user, across workspaces (the DB constraint is
        # per user, not per workspace).
        existing = WorklogTimer.objects.select_related("project").filter(user=user).first()
        stopped = None
        if existing:
            if str(existing.issue_id) == str(issue_id):
                running_issue = issue_label(existing.issue_id)
                where = f'{running_issue["ref"]}: "{running_issue["name"]}"' if running_issue else "this work item"
                raise TimerAlreadyRunning(existing, where)
            stopped_issue = issue_label(existing.issue_id)
            stopped = {"issue": stopped_issue, "worklogs": stop_timer(existing, user)}
        timer = WorklogTimer.objects.create(
            project_id=project_id,
            issue_id=issue_id,
            user=user,
            started_at=timezone.now(),
            description=description or "",
        )
    return timer, stopped


def stop_user_timer(slug, issue_id, user, description=None):
    """Stop the user's running timer on this issue. Returns the worklogs, or None."""
    with transaction.atomic():
        # Same lock as start, so a double-click on Stop can't log twice.
        lock_user_timers(user)
        timer = (
            WorklogTimer.objects.select_related("project")
            .filter(workspace__slug=slug, user=user, issue_id=issue_id)
            .first()
        )
        if not timer:
            return None
        return stop_timer(timer, user, description=description)


def system_stop_timer(timer_id, ended_at, reason, actor=None):
    """Stop a timer the user did not stop themselves (sweep / tracking off).

    Re-reads the timer under the user's lock, so a concurrent user stop wins and
    nothing is logged twice. ``ended_at`` is clamped to [started_at, now].
    Returns the worklogs, or None if the timer was already gone.
    """
    timer = WorklogTimer.objects.filter(pk=timer_id).only("user_id").first()
    if not timer:
        return None
    with transaction.atomic():
        lock_user_timers(timer.user_id)
        timer = WorklogTimer.objects.select_related("project", "user").filter(pk=timer_id).first()
        if not timer:
            return None
        now = timezone.now()
        ended_at = min(max(ended_at or now, timer.started_at), now)
        return stop_timer(timer, timer.user, ended_at=ended_at, actor=actor, reason=reason)
