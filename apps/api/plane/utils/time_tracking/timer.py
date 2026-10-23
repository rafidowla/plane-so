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
import json
import uuid
from datetime import timedelta

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
    State,
    StateGroup,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    User,
    WorklogApprovalStatus,
    WorklogSource,
    WorklogTimer,
)
from plane.utils.time_tracking import audit, limits
from plane.utils.time_tracking.snapshot import issue_snapshot
from plane.utils.time_tracking.tracking import is_internal_member, tracking_turned_off
from plane.utils.time_tracking.tz import local_today, split_by_local_day, workspace_tz


class TimeTrackingError(Exception):
    """A refused time-tracking action; views turn it into ``Response(payload, status_code)``."""

    def __init__(self, payload, status_code=400):
        super().__init__(payload.get("error", "time tracking error"))
        self.payload = payload
        self.status_code = status_code


class TimerAlreadyRunning(TimeTrackingError):
    def __init__(self, timer, where):
        super().__init__({"error": f"A timer is already running on {where}.", "code": "already_running"}, 400)
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


class StoppedWorklogs(list):
    """FORK: PSR-121 — what ``stop_timer`` returns: the entries in time order (a list, as
    before) plus ``day_limit_clamped`` (the run was cut where the person's day reached 24 h)
    and ``limit`` (the auto-stop / idle-check rule that ended it, or None). It may be EMPTY
    when the day was already full: the timer still stopped, nothing was saved."""

    def __init__(self, worklogs=(), day_limit_clamped=False, limit=None):
        super().__init__(worklogs)
        self.day_limit_clamped = day_limit_clamped
        self.limit = limit


def _clamp_to_day_cap(workspace_id, user_id, pieces):
    """FORK: PSR-121 — apply the 24 h per person per local day cap (``entries.DAY_CAP_MINUTES``)
    to a timer run without refusing the stop (a refused stop would leave the timer running).

    Walks the per-day pieces in time order and ends the run where a day reaches 24 h: that piece
    is shortened (``ended_at`` = its start + the minutes left), and it and everything after it
    stop there. Returns ``(pieces, clamped)``.

    Reachable because the overlap checks only cover intervals: legacy ``logged_date`` +
    ``duration`` rows (manual legacy entries, imported Jira worklogs) take day capacity without
    taking clock time, so a timer can still run over a day they already filled. Interval rows
    and timers never overlap, so without legacy rows a day can only overflow on a 25 h DST day.
    """
    from plane.utils.time_tracking.entries import DAY_CAP_MINUTES, day_totals

    if not pieces:
        return pieces, False
    logged = day_totals(workspace_id, user_id, {piece["logged_date"] for piece in pieces})
    kept = []
    for piece in pieces:
        day = piece["logged_date"]
        room = DAY_CAP_MINUTES - (logged.get(day) or 0)
        if piece["duration"] > room:
            if room > 0:
                kept.append(
                    {**piece, "duration": room, "ended_at": piece["started_at"] + timedelta(minutes=room)}
                )
            return kept, True
        kept.append(piece)
        logged[day] = (logged.get(day) or 0) + piece["duration"]
    return kept, False


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

    S2: every stop goes through ``limits.cutoff`` — a stop requested after the
    auto-stop time or after an unanswered "still working?" deadline ends at that
    limit instead (prompt time for the idle check), whoever triggered it. Such a
    stop sets ``auto_stopped`` / ``idle_stopped`` and is audited as a system
    ``auto_stop`` with ``reason`` ``auto_stop`` / ``idle_check``.

    FORK: PSR-121 — the run is also cut where the person's local day reaches 24 h
    (``_clamp_to_day_cap``); the result (a ``StoppedWorklogs``) then has
    ``day_limit_clamped`` set and may hold no entry at all.
    """
    user = user or timer.user
    ended_at = ended_at or timezone.now()
    ended_at, limit = limits.cutoff(timer, limits.read_settings(timer.workspace_id), ended_at)
    if limit:
        # The rule stopped it, not whoever made this call.
        reason, actor = limit, None
    project = timer.project
    pieces = split_by_local_day(timer.started_at, ended_at, workspace_tz(timer.workspace_id))
    # FORK: PSR-121 — the 24 h day cap: never refuse the stop, cut the saved run instead.
    pieces, day_limit_clamped = _clamp_to_day_cap(timer.workspace_id, user.id, pieces)
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
                # FORK: time-tracking-v2 S2 — flags for the 14 h auto-stop and the idle check.
                auto_stopped=limit == limits.LIMIT_AUTO_STOP,
                idle_stopped=limit == limits.LIMIT_IDLE,
            )
        )
    timer.delete()
    if day_limit_clamped and not reason:
        # FORK: PSR-121 — the day cap cut it, not whoever made this call.
        reason, actor = "day_limit", None
    if reason:
        for worklog in worklogs:
            after = {**audit.worklog_state(worklog), "reason": reason, "timer_id": timer.id}
            if day_limit_clamped:
                after["day_limit_clamped"] = True
            audit.record(
                worklog.workspace_id,
                actor,
                TimeTrackingAuditTarget.WORKLOG,
                worklog.id,
                TimeTrackingAuditAction.AUTO_STOP,
                after=after,
            )
    if limit == limits.LIMIT_AUTO_STOP:
        # S3: the 14 h auto-stop opens a review for the entry's approver (DESIGN §2 item 9).
        # The entry counts as saved meanwhile. An idle-check stop opens no review.
        from plane.utils.time_tracking.entry_requests import open_auto_stop_review

        open_auto_stop_review(worklogs)
    return StoppedWorklogs(worklogs, day_limit_clamped=day_limit_clamped, limit=limit)


def _move_to_started(issue, user):
    """FORK: time-tracking-v2 S2 — Backlog/Unstarted -> the project's first "started" state.

    Runs inside the start transaction. Returns ``{"from": state, "to": state}``
    (id/name/group) or None when the item is already past Unstarted or the
    project has no started state. The activity row is written by the normal
    ``issue_activity`` task after commit, exactly like a manual state change.
    """
    if issue.state is None or issue.state.group not in (StateGroup.BACKLOG.value, StateGroup.UNSTARTED.value):
        return None
    target = (
        State.objects.filter(project_id=issue.project_id, group=StateGroup.STARTED.value)
        .order_by("sequence", "created_at")
        .first()
    )
    if target is None:
        return None
    old = issue.state
    issue.state = target
    issue.updated_by = user
    # update_fields + disable_auto_set_user: BaseModel.save() would otherwise null
    # created_by/updated_by when there is no request user (crum) in scope.
    issue.save(update_fields=["state", "updated_at", "updated_by"], disable_auto_set_user=True)

    from plane.bgtasks.issue_activities_task import issue_activity

    payload = {
        "type": "issue.activity.updated",
        "requested_data": json.dumps({"state_id": str(target.id)}),
        "current_instance": json.dumps({"state_id": str(old.id)}),
        "issue_id": str(issue.id),
        "actor_id": str(user.id),
        "project_id": str(issue.project_id),
        "epoch": int(timezone.now().timestamp()),
        "notification": True,
        "origin": None,
    }
    transaction.on_commit(lambda: issue_activity.delay(**payload))

    def _state(st):
        return {"id": str(st.id), "name": st.name, "group": st.group}

    return {"from": _state(old), "to": _state(target)}


def start_timer(slug, project_id, issue_id, user, description=""):
    """Start a timer for ``user`` on an issue, stopping any running one first.

    Returns ``(timer, stopped, state_change)``; ``stopped`` is None or
    ``{"issue": issue_label, "worklogs": [...]}`` for the timer that was stopped,
    ``state_change`` is None or ``{"from": state, "to": state}`` (see
    ``_move_to_started``). Raises ``TimeTrackingError`` (or ``TimerAlreadyRunning``)
    when refused; a refused start never touches the running timer.

    Both start endpoints (per-issue and ``time/timer/start/``) call this, so they
    refuse and succeed identically. Order of checks (first failure wins):
    issue not in project 404 · guest / not an active project admin-member 403
    ``not_tracked`` · project tracking off 400 ``project_tracking_disabled`` ·
    member tracking turned off 403 ``tracking_disabled`` · Cancelled item 400
    ``issue_cancelled`` · no estimate on a "required" project 400 ``estimate_required``.
    """
    project = Project.objects.get(pk=project_id)
    # FORK: security — the work item must live in this project. Without this a member
    # of any time-tracked project could time a work item of another project (or
    # workspace) by UUID, and the running-timer endpoint would then echo back that
    # item's name and identifier.
    issue = (
        Issue.objects.select_related("state")
        .filter(pk=issue_id, project_id=project_id, workspace__slug=slug)
        .first()
    )
    if issue is None:
        raise TimeTrackingError({"error": "Work item not found."}, 404)
    today = local_today(workspace_tz(project.workspace_id))
    # FORK: time-tracking-v2 S2 — guests never track time (DESIGN §2 item 13): the
    # caller must be an active project admin/member AND a workspace admin/member.
    if not is_project_member(slug, project_id, user) or not is_internal_member(project.workspace_id, user.id):
        raise TimeTrackingError({"error": "Guests can't track time.", "code": "not_tracked"}, 403)
    if not project.is_time_tracking_enabled:
        raise TimeTrackingError(
            {"error": "Time tracking is not enabled for this project.", "code": "project_tracking_disabled"}, 400
        )
    # Timers only run "today", so the member's status today decides.
    if tracking_turned_off(project.workspace_id, user.id, today):
        raise TimeTrackingError(
            {"error": "Time tracking is turned off for you in this workspace.", "code": "tracking_disabled"},
            403,
        )
    if issue.state is not None and issue.state.group == StateGroup.CANCELLED.value:
        raise TimeTrackingError({"error": "This work item is cancelled.", "code": "issue_cancelled"}, 400)
    # FORK: time-tracking-v2 S6 — "required" projects need an estimate before a timer starts.
    from plane.utils.time_tracking.estimate import check_can_log

    check_can_log(issue)
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
        issue = Issue.objects.select_for_update(of=("self",)).select_related("state").get(pk=issue.pk)
        state_change = _move_to_started(issue, user)
    return timer, stopped, state_change


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


def stop_running_timer(slug, user, description=None):
    """FORK: time-tracking-v2 S2 — stop the user's running timer in this workspace,
    whatever work item it is on (``time/timer/stop/``). Returns ``(issue_label, worklogs)``
    or ``(None, None)`` when nothing runs here."""
    with transaction.atomic():
        lock_user_timers(user)
        timer = WorklogTimer.objects.select_related("project").filter(workspace__slug=slug, user=user).first()
        if not timer:
            return None, None
        label = issue_label(timer.issue_id)
        return label, stop_timer(timer, user, description=description)


def confirm_timer(slug, user):
    """FORK: time-tracking-v2 S2 — answer "Yes, still working".

    Sets ``last_confirmed_at = now`` (the next check is one interval later) and
    returns ``("confirmed", timer)``. If the answer comes too late — the check
    deadline (or the auto-stop time) has already passed but the sweep has not
    run yet — the timer is stopped exactly as the sweep would have stopped it
    (at the prompt time, ``idle_stopped``) and ``("stopped", worklogs)`` is
    returned: unconfirmed time never counts. ``(None, None)`` when no timer runs.
    """
    with transaction.atomic():
        lock_user_timers(user)
        timer = WorklogTimer.objects.select_related("project").filter(workspace__slug=slug, user=user).first()
        if not timer:
            return None, None
        now = timezone.now()
        _, limit = limits.cutoff(timer, limits.read_settings(timer.workspace_id), now)
        if limit:
            return "stopped", stop_timer(timer, user, ended_at=now)
        timer.last_confirmed_at = now
        # update_fields: see _move_to_started on BaseModel.save().
        timer.save(update_fields=["last_confirmed_at", "updated_at"], disable_auto_set_user=True)
        return "confirmed", timer


def timer_schedule_fields(timer, setting=None, now=None):
    """FORK: time-tracking-v2 S2 — the limit fields the UI needs for a running timer.

    ``idle_check_enabled``: whether the "still working?" check is on. ``check``:
    null until the server clock reaches the (random, never pre-announced) check
    time, then ``{"prompted_at", "deadline_at"}`` — the prompt time, and when the
    timer is stopped (at ``prompted_at``) if unanswered. ``remind_at`` /
    ``auto_stop_at``: the 10 h reminder and 14 h auto-stop. ``reminded``: the
    reminder has been sent.
    """
    setting = setting or limits.read_settings(timer.workspace_id)
    s = limits.schedule(timer, setting)
    now = now or timezone.now()
    check = None
    if s["next_check_at"] is not None and now >= s["next_check_at"]:
        check = {"prompted_at": s["next_check_at"], "deadline_at": s["check_deadline_at"]}
    return {
        "idle_check_enabled": s["idle_check_enabled"],
        "idle_grace_seconds": int(limits.IDLE_GRACE.total_seconds()),
        "check": check,
        "remind_at": s["remind_at"],
        "auto_stop_at": s["auto_stop_at"],
        "reminded": timer.reminded_at is not None,
    }


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
