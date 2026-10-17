# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — direct (manual) time-entry rules and shared interval helpers.

Who may write an entry directly (DESIGN §2 items 14, 16, 18):

* **Workspace admin** — any entry, any (active project member) user. Audited.
* **Project admin** — entries in their project for *other* active members (role >= 15),
  never their own (their own missing time is a request to a workspace admin).
* **Member** (incl. a project admin for their own time) — may *add* their own entries only
  when the workspace setting ``allow_member_manual_entry`` is on; changing start/end/duration/
  date of an own entry, or deleting it, is a request (403 ``request_required``).
  Description-only edits stay direct.

Both the workspace-level ``time/entries/`` endpoints and the per-issue
``IssueWorklogViewSet`` call these functions, so they refuse identically.
"""

# Python imports
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

# Django imports
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    Issue,
    IssueWorklog,
    Project,
    ProjectMember,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    WorklogSource,
    WorklogTimer,
)
from plane.utils.time_tracking import audit, limits
from plane.utils.time_tracking.approvers import is_project_admin, workspace_role
from plane.utils.time_tracking.snapshot import issue_snapshot
from plane.utils.time_tracking.timer import TimeTrackingError, lock_user_timers, resolve_billable_rate
from plane.utils.time_tracking.tracking import tracking_turned_off
from plane.utils.time_tracking.tz import local_date, local_today, split_by_local_day, total_minutes, workspace_tz

MAX_ENTRY = timedelta(hours=24)
# Fields that move time (and therefore totals): members change these only through a request.
TIME_FIELDS = ("started_at", "ended_at", "duration", "logged_date")
# Fields a member may change directly on their own entry.
MEMBER_DIRECT_FIELDS = ("description", "work_type")


def refuse(code, error, status_code=400, **extra):
    raise TimeTrackingError({"error": error, "code": code, **extra}, status_code)


def parse_instant(value, field):
    """An aware datetime from an ISO string (or datetime); 400 ``invalid_interval`` otherwise."""
    if value in (None, ""):
        refuse("invalid_interval", f"{field} is required.")
    parsed = value if hasattr(value, "tzinfo") else parse_datetime(str(value))
    if parsed is None:
        refuse("invalid_interval", f"{field} is not a valid date-time.")
    if timezone.is_naive(parsed):
        refuse("invalid_interval", f"{field} must include a time zone offset.")
    return parsed


def validate_interval(started_at, ended_at, now=None):
    """end > start, end <= now (so the date is <= today), at most 24 h."""
    now = now or timezone.now()
    if ended_at <= started_at:
        refuse("invalid_interval", "The end must be after the start.")
    if ended_at > now:
        refuse("invalid_interval", "Time can't be logged in the future.")
    if ended_at - started_at > MAX_ENTRY:
        refuse("invalid_interval", "One entry can cover at most 24 hours.")


def check_tracked(workspace_id, user_id, started_at, ended_at, tz, status_code=403):
    """403 ``tracking_disabled`` if tracking was turned off for the user on any day of the interval."""
    day = local_date(started_at, tz)
    last = local_date(ended_at, tz)
    while day <= last:
        if tracking_turned_off(workspace_id, user_id, day):
            refuse("tracking_disabled", "Time tracking is turned off for this person on that date.", status_code)
        day += timedelta(days=1)


def overlapping(workspace_id, user_id, started_at, ended_at, exclude_ids=()):
    """The user's entries (and running timer) in the workspace that overlap [started_at, ended_at)."""
    entries = (
        IssueWorklog.objects.filter(
            workspace_id=workspace_id,
            logged_by_id=user_id,
            started_at__isnull=False,
            ended_at__isnull=False,
            started_at__lt=ended_at,
            ended_at__gt=started_at,
        )
        .exclude(pk__in=list(exclude_ids))
        .values_list("id", flat=True)
    )
    timer = WorklogTimer.objects.filter(workspace_id=workspace_id, user_id=user_id, started_at__lt=ended_at).exists()
    return list(entries), timer


def check_overlap(workspace_id, user_id, started_at, ended_at, exclude_ids=(), status_code=400, detail=False):
    """409/400 ``overlap``. ``detail=True`` adds ``overlapping`` (what the clash is) — only for callers
    whose actor *is* ``user_id``, so a project admin never learns about someone's other-project work."""
    ids, timer = overlapping(workspace_id, user_id, started_at, ended_at, exclude_ids)
    if ids or timer:
        extra = {}
        if detail and ids:
            extra["overlapping"] = [
                {
                    "id": str(w.id),
                    "issue_ref": w.issue_ref,
                    "issue_name": w.issue_name,
                    "started_at": w.started_at,
                    "ended_at": w.ended_at,
                }
                for w in IssueWorklog.objects.filter(pk__in=ids).order_by("started_at")
            ]
        refuse(
            "overlap",
            "This time overlaps another entry or the running timer.",
            status_code,
            overlapping_ids=[str(i) for i in ids],
            overlaps_timer=timer,
            **extra,
        )


DAY_CAP_MINUTES = 24 * 60


def check_day_total(workspace_id, user_id, additions, exclude_ids=(), status_code=400):
    """``day_limit`` when the person's entries on any day in ``additions`` would total more than
    24 h. ``additions`` maps a local date to the minutes about to be written on it; every row
    counts (interval or legacy date+duration) except ``exclude_ids`` (the rows being rewritten).
    Callers hold ``lock_user_timers`` so two writers can't both pass."""
    if not additions:
        return
    existing = dict(
        IssueWorklog.objects.filter(workspace_id=workspace_id, logged_by_id=user_id, logged_date__in=list(additions))
        .exclude(pk__in=list(exclude_ids))
        .order_by()
        .values("logged_date")
        .annotate(total=Sum("duration"))
        .values_list("logged_date", "total")
    )
    for day, minutes in additions.items():
        if (existing.get(day) or 0) + minutes > DAY_CAP_MINUTES:
            refuse(
                "day_limit",
                "A person can't have more than 24 hours of time on one day.",
                status_code,
                date=str(day),
            )


def interval_day_minutes(started_at, ended_at, tz):
    """{local date: minutes} for an interval, split at local midnight (as ``rewrite`` stores it)."""
    additions = {}
    for piece in split_by_local_day(started_at, ended_at, tz):
        additions[piece["logged_date"]] = additions.get(piece["logged_date"], 0) + piece["duration"]
    return additions


def is_active_project_member(project_id, user_id):
    return ProjectMember.objects.filter(
        project_id=project_id, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
    ).exists()


def rewrite(rows, started_at, ended_at, tz, request=None):
    """Make ``rows`` (one entry or one timer session, in time order) cover exactly
    [started_at, ended_at), split at each workspace-local midnight.

    Rows are reused in order, extra pieces are created as copies of the first row, and
    leftover rows are soft-deleted. All resulting pieces share one ``session_id`` when
    there is more than one. Returns ``(kept, removed)``.
    """
    pieces = split_by_local_day(started_at, ended_at, tz)
    rows = list(rows)
    first = rows[0]
    session_id = first.session_id or (uuid.uuid4() if len(pieces) > 1 else None)
    kept = []
    for i, piece in enumerate(pieces):
        if i < len(rows):
            row = rows[i]
        else:
            row = IssueWorklog(
                project_id=first.project_id,
                workspace_id=first.workspace_id,
                issue_id=first.issue_id,
                issue_ref=first.issue_ref,
                issue_name=first.issue_name,
                logged_by_id=first.logged_by_id,
                description=first.description,
                source=first.source,
                work_type=first.work_type,
                is_billable=first.is_billable,
                billable_rate=first.billable_rate,
                currency=first.currency,
                approval_status=first.approval_status,
                # FORK: time-tracking-v2 — a split piece of a locked entry (admin override) stays locked.
                is_locked=first.is_locked,
                auto_stopped=first.auto_stopped,
                idle_stopped=first.idle_stopped,
            )
        row.started_at = piece["started_at"]
        row.ended_at = piece["ended_at"]
        row.logged_date = piece["logged_date"]
        row.duration = piece["duration"]
        row.session_id = session_id
        if request is not None:
            row.request = request
        row.save()
        kept.append(row)
    removed = rows[len(pieces) :]
    for row in removed:
        row.delete()
    return kept, removed


# ---------------------------------------------------------------------------
# Direct writes
# ---------------------------------------------------------------------------


def _target_entry_rules(workspace_id, project_id, actor, target_user_id):
    """Who may write an entry belonging to ``target_user_id`` directly. Returns the actor's
    workspace role; raises when refused."""
    ws_role = workspace_role(workspace_id, actor.id)
    if ws_role is None or ws_role < ROLE.MEMBER.value:
        refuse("not_tracked", "Guests can't track time.", 403)
    if ws_role == ROLE.ADMIN.value:
        return ws_role
    own = str(target_user_id) == str(actor.id)
    if own:
        if not limits.read_settings(workspace_id).allow_member_manual_entry:
            refuse(
                "request_required",
                "Adding or changing your own time needs a request to your approver.",
                403,
            )
        return ws_role
    if not is_project_admin(project_id, actor.id):
        refuse("not_allowed", "You can only change your own time entries.", 403)
    return ws_role


@transaction.atomic
def create_entry(workspace_id, project_id, issue_id, actor, data):
    """Create a manual entry (one per local day when it crosses midnight). Returns the rows.

    ``data``: ``logged_by`` (default: actor), either ``started_at`` + ``ended_at`` (preferred;
    overlap-checked and split by day) or ``logged_date`` + ``duration`` (minutes, legacy
    shape), plus optional ``description``, ``work_type``, ``is_billable``,
    ``billable_rate``, ``currency``.
    """
    project = Project.objects.filter(pk=project_id, workspace_id=workspace_id).first()
    issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace_id=workspace_id).first()
    if project is None or issue is None:
        refuse("not_found", "Work item not found.", 404)
    if (
        not is_active_project_member(project_id, actor.id)
        and workspace_role(workspace_id, actor.id) != ROLE.ADMIN.value
    ):
        refuse("not_tracked", "Guests can't track time.", 403)
    if not project.is_time_tracking_enabled:
        refuse("project_tracking_disabled", "Time tracking is not enabled for this project.")
    # FORK: time-tracking-v2 S6 — "required" projects need an estimate before time is logged.
    from plane.utils.time_tracking.estimate import check_can_log

    check_can_log(issue)

    target = data.get("logged_by") or actor.id
    try:
        target = uuid.UUID(str(target))
    except (ValueError, AttributeError):
        refuse("invalid_user", "logged_by is not a valid user.")
    ws_role = _target_entry_rules(workspace_id, project_id, actor, target)
    # Guests are client users: time is never logged for them (nor for non-members).
    if not is_active_project_member(project_id, target):
        refuse("invalid_user", "logged_by must be an active member or admin of this project.")

    # Same per-user lock as timer start/stop and request approval: no two writers can
    # both pass the overlap check for the same person.
    lock_user_timers(target)
    tz = workspace_tz(workspace_id)
    started_at = ended_at = None
    if data.get("started_at") or data.get("ended_at"):
        started_at = parse_instant(data.get("started_at"), "started_at")
        ended_at = parse_instant(data.get("ended_at"), "ended_at")
        validate_interval(started_at, ended_at)
        check_tracked(workspace_id, target, started_at, ended_at, tz)
        check_overlap(workspace_id, target, started_at, ended_at)
        check_day_total(workspace_id, target, interval_day_minutes(started_at, ended_at, tz))
    else:
        logged_date = data.get("logged_date")
        logged_date = logged_date if hasattr(logged_date, "year") else parse_date(str(logged_date or ""))
        try:
            duration = int(data.get("duration"))
        except (TypeError, ValueError):
            duration = 0
        if logged_date is None or not 0 < duration <= 24 * 60:
            refuse("invalid_interval", "Give started_at and ended_at, or logged_date and duration (1-1440 min).")
        if logged_date > local_today(tz):
            refuse("invalid_interval", "Time can't be logged in the future.")
        if tracking_turned_off(workspace_id, target, logged_date):
            refuse("tracking_disabled", "Time tracking is turned off for this person on that date.", 403)
        # FORK: time-tracking-v2 secfix — the legacy shape has no interval to overlap-check,
        # but it still counts toward (and may not exceed) the person's 24 h for the day.
        check_day_total(workspace_id, target, {logged_date: duration})

    is_billable = data.get("is_billable", True)
    is_billable = is_billable if isinstance(is_billable, bool) else str(is_billable).lower() in ("1", "true")
    billable_rate = data.get("billable_rate")
    currency = data.get("currency") or "USD"
    # Billing fields are an admin's call; a member adding their own time gets the defaults.
    if ws_role != ROLE.ADMIN.value and not is_project_admin(project_id, actor.id):
        billable_rate, currency, is_billable = None, "USD", True
    if is_billable and billable_rate in (None, ""):
        billable_rate, currency = resolve_billable_rate(project, target)
    issue_ref, issue_name = issue_snapshot(issue_id)
    base = IssueWorklog(
        project=project,
        issue_id=issue_id,
        issue_ref=issue_ref,
        issue_name=issue_name,
        logged_by_id=target,
        description=str(data.get("description") or ""),
        work_type=data.get("work_type") or None,
        source=WorklogSource.MANUAL,
        is_billable=is_billable,
        billable_rate=billable_rate,
        currency=currency,
    )
    if started_at is not None:
        base.started_at, base.ended_at = started_at, ended_at
        base.logged_date, base.duration = local_date(started_at, tz), total_minutes(started_at, ended_at)
        rows, _ = rewrite([base], started_at, ended_at, tz)
    else:
        base.logged_date, base.duration = logged_date, duration
        base.save()
        rows = [base]
    for row in rows:
        audit.record(
            workspace_id,
            actor,
            TimeTrackingAuditTarget.WORKLOG,
            row.id,
            TimeTrackingAuditAction.CREATE,
            after=audit.worklog_state(row),
        )
    return rows


def _entry_rules(worklog, actor, changes_time):
    """Rules for changing/deleting an existing entry directly. Returns the actor's ws role."""
    ws_role = workspace_role(worklog.workspace_id, actor.id)
    if ws_role is None or ws_role < ROLE.MEMBER.value:
        refuse("not_tracked", "Guests can't track time.", 403)
    if ws_role == ROLE.ADMIN.value:
        return ws_role
    own = str(worklog.logged_by_id) == str(actor.id)
    if own:
        if changes_time:
            refuse(
                "request_required",
                "Changing or deleting your own time needs a request to your approver.",
                403,
            )
        if worklog.is_locked:
            refuse("entry_locked", "This entry is locked and can no longer be edited.")
        return ws_role
    if not is_project_admin(worklog.project_id, actor.id):
        refuse("not_allowed", "You can only change your own time entries.", 403)
    # FORK: time-tracking-v2 secfix — a locked (approved-timesheet) entry of someone else is
    # a workspace admin's override only (DESIGN §8 item 6); project admins are refused.
    if worklog.is_locked:
        refuse(
            "entry_locked",
            "This entry is locked. Only a workspace admin can change it.",
            403,
        )
    return ws_role


def is_locked_override(worklog, ws_role):
    """A workspace admin changing a locked entry: allowed, but audited as an override."""
    return bool(worklog.is_locked) and ws_role == ROLE.ADMIN.value


OVERRIDE_MARKER = {"override": "locked_entry"}


def _audited(state, override):
    return {**state, **OVERRIDE_MARKER} if override and state is not None else state


@transaction.atomic
def update_entry(worklog, actor, data):
    """PATCH an entry directly. Returns the entry's rows after the change (a start/end
    change that crosses local midnight splits it)."""
    changes_time = any(f in data for f in TIME_FIELDS)
    ws_role = _entry_rules(worklog, actor, changes_time)
    override = is_locked_override(worklog, ws_role)
    # Own entry (not a workspace admin): description/work type only. Someone else's entry
    # reaching here belongs to a workspace admin or that project's admin: any field.
    if ws_role != ROLE.ADMIN.value and str(worklog.logged_by_id) == str(actor.id):
        extra = sorted(set(data) - set(MEMBER_DIRECT_FIELDS))
        if extra:
            refuse("not_allowed", f"You can't change {', '.join(extra)} directly.", 403)

    lock_user_timers(worklog.logged_by_id)
    before = audit.worklog_state(worklog)
    tz = workspace_tz(worklog.workspace_id)
    for field in ("description", "work_type", "is_billable", "billable_rate", "currency"):
        if field in data:
            setattr(worklog, field, data[field] if field != "description" else str(data[field] or ""))
    rows = [worklog]
    if changes_time:
        started_at = ended_at = None
        if "started_at" in data or "ended_at" in data:
            started_at = parse_instant(data.get("started_at", worklog.started_at), "started_at")
            ended_at = parse_instant(data.get("ended_at", worklog.ended_at), "ended_at")
        elif worklog.started_at is not None and worklog.ended_at is not None:
            # FORK: time-tracking-v2 secfix — a date/duration edit of an entry that has an interval
            # moves the interval with it (same local time of day, start + duration), so the stored
            # start/end can't go stale and the overlap checks still cover it.
            started_at, ended_at = _legacy_change_to_interval(worklog, data, tz)
        if started_at is not None:
            validate_interval(started_at, ended_at)
            check_tracked(worklog.workspace_id, worklog.logged_by_id, started_at, ended_at, tz)
            check_overlap(worklog.workspace_id, worklog.logged_by_id, started_at, ended_at, exclude_ids=[worklog.id])
            check_day_total(
                worklog.workspace_id,
                worklog.logged_by_id,
                interval_day_minutes(started_at, ended_at, tz),
                exclude_ids=[worklog.id],
            )
            rows, _ = rewrite([worklog], started_at, ended_at, tz)
        else:
            # An entry with no interval (legacy date + duration shape).
            logged_date, duration = worklog.logged_date, worklog.duration
            if "logged_date" in data:
                logged_date = _parse_logged_date(data["logged_date"], tz)
            if "duration" in data:
                duration = _parse_duration(data["duration"])
            if tracking_turned_off(worklog.workspace_id, worklog.logged_by_id, logged_date):
                refuse("tracking_disabled", "Time tracking is turned off for this person on that date.", 403)
            check_day_total(
                worklog.workspace_id, worklog.logged_by_id, {logged_date: duration}, exclude_ids=[worklog.id]
            )
            worklog.logged_date, worklog.duration = logged_date, duration
            worklog.save()
    else:
        worklog.save()
    for row in rows:
        audit.record(
            row.workspace_id,
            actor,
            TimeTrackingAuditTarget.WORKLOG,
            row.id,
            TimeTrackingAuditAction.UPDATE if row.pk == worklog.pk else TimeTrackingAuditAction.CREATE,
            before=_audited(before if row.pk == worklog.pk else None, override),
            after=_audited(audit.worklog_state(row), override),
        )
    return rows


def _parse_logged_date(value, tz):
    logged_date = value if hasattr(value, "year") else parse_date(str(value or ""))
    if logged_date is None or logged_date > local_today(tz):
        refuse("invalid_interval", "logged_date must be a date that is not in the future.")
    return logged_date


def _parse_duration(value):
    try:
        duration = int(value)
    except (TypeError, ValueError):
        duration = 0
    if not 0 < duration <= 24 * 60:
        refuse("invalid_interval", "duration must be 1-1440 minutes.")
    return duration


def _legacy_change_to_interval(worklog, data, tz):
    """The (started_at, ended_at) an interval entry gets from a ``logged_date`` / ``duration`` edit."""
    duration = _parse_duration(data["duration"]) if "duration" in data else worklog.duration
    started_at = worklog.started_at
    if "logged_date" in data:
        new_date = _parse_logged_date(data["logged_date"], tz)
        local_start = worklog.started_at.astimezone(tz)
        started_at = datetime.combine(new_date, local_start.timetz().replace(tzinfo=None), tzinfo=tz).astimezone(
            dt_timezone.utc
        )
    return started_at, started_at + timedelta(minutes=duration)


@transaction.atomic
def delete_entry(worklog, actor):
    ws_role = _entry_rules(worklog, actor, changes_time=True)
    override = is_locked_override(worklog, ws_role)
    audit.record(
        worklog.workspace_id,
        actor,
        TimeTrackingAuditTarget.WORKLOG,
        worklog.id,
        TimeTrackingAuditAction.DELETE,
        before=_audited(audit.worklog_state(worklog), override),
        after=OVERRIDE_MARKER if override else None,
    )
    worklog.delete()
