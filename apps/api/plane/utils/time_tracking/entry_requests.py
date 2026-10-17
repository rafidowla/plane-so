# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — time-entry requests: submit, approve, reject, withdraw.

DESIGN §2 items 9, 15-21. A request never changes an entry until it is approved, so
pending requests never move totals. Approval runs in one transaction holding the
requester's user-row lock (``lock_user_timers``, the same lock timer start/stop use),
re-validates against the entry's *current* state and either applies completely or not
at all:

* the entry changed since the request was made → 409 ``stale``;
* a target entry is locked → 409 ``entry_locked``;
* the new interval now overlaps another entry or the running timer → 409 ``overlap``.

Every state change writes an append-only ``TimeEntryRequestEvent`` (submitted,
approved/rejected + applied, withdrawn, superseded) and an audit row for each entry it
touches.
"""

# Python imports
import uuid

# Django imports
from django.db import IntegrityError, transaction
from django.utils import timezone

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    Issue,
    IssueWorklog,
    Notification,
    Project,
    TimeEntryActorRole,
    TimeEntryRequest,
    TimeEntryRequestAction,
    TimeEntryRequestEvent,
    TimeEntryRequestStatus,
    TimeEntryRequestType,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    User,
    WorklogSource,
)
from plane.utils.time_tracking import approvers, audit
from plane.utils.time_tracking.entries import (
    check_overlap,
    check_tracked,
    is_active_project_member,
    parse_instant,
    refuse,
    rewrite,
    validate_interval,
)
from plane.utils.time_tracking.snapshot import issue_snapshot
from plane.utils.time_tracking.timer import lock_user_timers, resolve_billable_rate
from plane.utils.time_tracking.tz import local_date, total_minutes, workspace_tz

SUBMITTABLE = (
    TimeEntryRequestType.MISSING_TIME,
    TimeEntryRequestType.TIME_CHANGE,
    TimeEntryRequestType.DELETION,
)
SENDER_PREFIX = "in_app:time_tracking:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(request, action, actor_id=None, actor_role=TimeEntryActorRole.SYSTEM, note="", payload=None):
    return TimeEntryRequestEvent.objects.create(
        request=request,
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        note=note or "",
        payload=payload or {},
    )


def _session_rows(worklog):
    """The live rows of the timer session ``worklog`` belongs to (just itself if none), in time order."""
    if worklog.session_id is None:
        return [worklog] if worklog.deleted_at is None else []
    return list(
        IssueWorklog.objects.filter(
            workspace_id=worklog.workspace_id, logged_by_id=worklog.logged_by_id, session_id=worklog.session_id
        ).order_by("started_at", "created_at")
    )


def _span(rows):
    """(start, end, minutes) covered by ``rows``."""
    if not rows:
        return None, None, 0
    return rows[0].started_at, rows[-1].ended_at, sum(r.duration for r in rows)


def visible(request, user):
    """Requester, anyone who may approve it, and workspace admins (who may approve all but their own)."""
    if str(request.requester_id) == str(user.id):
        return True
    return approvers.can_approve(user.id, request)


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


def serialize(request, viewer):
    new_duration = None
    if request.new_started_at and request.new_ended_at:
        new_duration = total_minutes(request.new_started_at, request.new_ended_at)
    pending = request.status == TimeEntryRequestStatus.PENDING
    is_requester = str(request.requester_id) == str(viewer.id)
    return {
        "id": str(request.id),
        "type": request.type,
        "status": request.status,
        "workspace_id": str(request.workspace_id),
        "project_id": str(request.project_id),
        "issue_id": str(request.issue_id),
        "issue": _issue_brief(request.issue_id),
        "worklog_id": str(request.worklog_id) if request.worklog_id else None,
        "requester": _user_lite(request.requester),
        "work_date": request.work_date,
        "old_started_at": request.old_started_at,
        "old_ended_at": request.old_ended_at,
        "old_duration": request.old_duration,
        "new_started_at": request.new_started_at,
        "new_ended_at": request.new_ended_at,
        "new_duration": new_duration,
        "description": request.description,
        "reason": request.reason,
        "reviewer": _user_lite(request.reviewer),
        "reviewer_role": request.reviewer_role or None,
        "reviewed_at": request.reviewed_at,
        "review_note": request.review_note,
        "created_at": request.created_at,
        "route": approvers.route(request),
        "can_approve": pending and not is_requester and approvers.can_approve(viewer.id, request),
        "can_withdraw": pending and is_requester,
    }


def serialize_event(event, with_request=False):
    data = {
        "id": str(event.id),
        "request_id": str(event.request_id),
        "action": event.action,
        "actor": _user_lite(event.actor) if event.actor_id else None,
        "actor_role": event.actor_role,
        "note": event.note,
        "payload": event.payload,
        "created_at": event.created_at,
    }
    if with_request:
        req = event.request
        data["request"] = {
            "type": req.type,
            "status": req.status,
            "requester_id": str(req.requester_id),
            "project_id": str(req.project_id),
            "issue_id": str(req.issue_id),
            "work_date": req.work_date,
        }
    return data


def _issue_brief(issue_id):
    row = (
        Issue.all_objects.filter(pk=issue_id)
        .values("id", "name", "sequence_id", "project__identifier", "state__name", "state__group")
        .first()
    )
    if not row:
        return {"id": str(issue_id)}
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "identifier": row["project__identifier"],
        "sequence_id": row["sequence_id"],
        "state_name": row["state__name"],
        "state_group": row["state__group"],
    }


def _notify(request, receiver_ids, kind, title, triggered_by_id=None):
    """In-app notifications (same shape as the S2 timer reminder) — sent after commit."""
    receiver_ids = [r for r in receiver_ids if r]
    if not receiver_ids:
        return
    issue = _issue_brief(request.issue_id)

    def send():
        Notification.objects.bulk_create(
            [
                Notification(
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    receiver_id=receiver_id,
                    triggered_by_id=triggered_by_id,
                    entity_identifier=request.issue_id,
                    # "issue" keeps it in the web inbox list, which filters on entity_name.
                    entity_name="issue",
                    sender=f"{SENDER_PREFIX}{kind}",
                    title=title,
                    data={
                        "issue": issue,
                        "issue_activity": {
                            "id": None,
                            "verb": kind,
                            "field": "time_entry_request",
                            "actor": str(triggered_by_id) if triggered_by_id else None,
                            "new_value": None,
                            "old_value": None,
                            "old_identifier": None,
                            "new_identifier": None,
                        },
                        "time_tracking": {
                            "kind": kind,
                            "request_id": str(request.id),
                            "request_type": request.type,
                            "status": request.status,
                        },
                    },
                )
                for receiver_id in receiver_ids
            ]
        )

    transaction.on_commit(send)


def _notify_approvers(request, triggered_by_id):
    titles = {
        TimeEntryRequestType.MISSING_TIME: "New missing-time request",
        TimeEntryRequestType.TIME_CHANGE: "New time-change request",
        TimeEntryRequestType.DELETION: "New time-deletion request",
        TimeEntryRequestType.AUTO_STOP_REVIEW: "Auto-stopped timer to review",
    }
    _notify(request, approvers.approver_ids(request), "request_submitted", titles[request.type], triggered_by_id)


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def submit(workspace_id, actor, data):
    """Create a request (``missing_time`` / ``time_change`` / ``deletion``) for the actor's own time."""
    kind = data.get("type")
    if kind not in SUBMITTABLE:
        refuse("invalid_type", f"type must be one of {', '.join(SUBMITTABLE)}.")
    ws_role = approvers.workspace_role(workspace_id, actor.id)
    if ws_role is None or ws_role < ROLE.MEMBER.value:
        refuse("not_tracked", "Guests can't track time.", 403)
    if ws_role == ROLE.ADMIN.value:
        refuse("edit_directly", "Workspace admins change time directly; no request is needed.")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        refuse("reason_required", "Give a reason for the request.")
    tz = workspace_tz(workspace_id)
    req = TimeEntryRequest(id=uuid.uuid4(), requester_id=actor.id, type=kind, reason=reason)

    with transaction.atomic():
        lock_user_timers(actor)
        superseded = None
        if kind == TimeEntryRequestType.MISSING_TIME:
            issue = Issue.issue_objects.filter(pk=data.get("issue_id"), workspace_id=workspace_id).first()
            if issue is None:
                refuse("not_found", "Work item not found.", 404)
            project = Project.objects.get(pk=issue.project_id)
            if not is_active_project_member(project.id, actor.id):
                refuse("not_tracked", "Only members of the work item's project can log time on it.", 403)
            if not project.is_time_tracking_enabled:
                refuse("project_tracking_disabled", "Time tracking is not enabled for this project.")
            started_at = parse_instant(data.get("started_at"), "started_at")
            ended_at = parse_instant(data.get("ended_at"), "ended_at")
            validate_interval(started_at, ended_at)
            check_tracked(workspace_id, actor.id, started_at, ended_at, tz)
            check_overlap(workspace_id, actor.id, started_at, ended_at)
            # TODO(S6): estimate gate (400 ``estimate_required`` when the project requires one).
            req.project_id, req.issue_id = project.id, issue.id
            req.new_started_at, req.new_ended_at = started_at, ended_at
            req.work_date = local_date(started_at, tz)
            req.description = str(data.get("description") or "")
        else:
            worklog = (
                IssueWorklog.objects.select_for_update()
                .filter(pk=data.get("worklog_id"), workspace_id=workspace_id, logged_by_id=actor.id)
                .first()
            )
            if worklog is None:
                refuse("not_found", "Time entry not found.", 404)
            if not is_active_project_member(worklog.project_id, actor.id):
                refuse("not_tracked", "You are no longer a member of this entry's project.", 403)
            if worklog.is_locked:
                refuse("entry_locked", "This entry is locked and can no longer be changed.")
            if kind == TimeEntryRequestType.TIME_CHANGE:
                started_at = parse_instant(data.get("started_at"), "started_at")
                ended_at = parse_instant(data.get("ended_at"), "ended_at")
                validate_interval(started_at, ended_at)
                check_tracked(workspace_id, actor.id, started_at, ended_at, tz)
                check_overlap(workspace_id, actor.id, started_at, ended_at, exclude_ids=[worklog.id])
                req.new_started_at, req.new_ended_at = started_at, ended_at
            req.project_id, req.issue_id, req.worklog_id = worklog.project_id, worklog.issue_id, worklog.id
            req.work_date = worklog.logged_date
            req.old_started_at, req.old_ended_at = worklog.started_at, worklog.ended_at
            req.old_duration = worklog.duration
            existing = (
                TimeEntryRequest.objects.select_for_update()
                .filter(worklog_id=worklog.id, status=TimeEntryRequestStatus.PENDING)
                .first()
            )
            if existing is not None:
                if existing.type != TimeEntryRequestType.AUTO_STOP_REVIEW:
                    refuse(
                        "pending_exists", "This entry already has a pending request.", 409, request_id=str(existing.id)
                    )
                # The member's own correction replaces the auto-stop review (DESIGN §2 item 9).
                existing.status = TimeEntryRequestStatus.WITHDRAWN
                existing.save(update_fields=["status", "updated_at"])
                superseded = existing
        req.workspace_id = workspace_id
        try:
            with transaction.atomic():
                req.save()
        except IntegrityError:
            refuse("pending_exists", "This entry already has a pending request.", 409)
        role = approvers.actor_role(workspace_id, req.project_id, actor.id)
        if superseded is not None:
            _event(superseded, TimeEntryRequestAction.SUPERSEDED, actor.id, role, payload={"by": str(req.id)})
        _event(
            req,
            TimeEntryRequestAction.SUBMITTED,
            actor.id,
            role,
            note=reason,
            payload={
                "new_started_at": req.new_started_at,
                "new_ended_at": req.new_ended_at,
                "old_started_at": req.old_started_at,
                "old_ended_at": req.old_ended_at,
                "old_duration": req.old_duration,
            },
        )
        _notify_approvers(req, actor.id)
    return req


def open_auto_stop_review(worklogs):
    """Open the ``auto_stop_review`` request for an auto-stopped timer session (system actor).

    Called by ``stop_timer`` inside its transaction. One request per session, linked to the
    session's LAST entry. Never touches ``TimeTrackingSetting``.
    """
    if not worklogs:
        return None
    last = worklogs[-1]
    start, end, minutes = _span(worklogs)
    req = TimeEntryRequest(
        workspace_id=last.workspace_id,
        project_id=last.project_id,
        requester_id=last.logged_by_id,
        issue_id=last.issue_id,
        worklog_id=last.id,
        type=TimeEntryRequestType.AUTO_STOP_REVIEW,
        work_date=worklogs[0].logged_date,
        old_started_at=start,
        old_ended_at=end,
        old_duration=minutes,
    )
    req.save(disable_auto_set_user=True)
    _event(
        req,
        TimeEntryRequestAction.SUBMITTED,
        payload={"worklog_ids": [str(w.id) for w in worklogs], "old_duration": minutes},
    )
    _notify_approvers(req, None)
    return req


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _load_for_review(workspace_id, actor, request_id):
    """Lock the requester's row, then the request. 404 when the actor can't see it."""
    row = TimeEntryRequest.objects.filter(pk=request_id, workspace_id=workspace_id).only("requester_id").first()
    if row is None:
        refuse("not_found", "Request not found.", 404)
    lock_user_timers(row.requester_id)
    req = TimeEntryRequest.objects.select_for_update().get(pk=row.pk)
    if not visible(req, actor):
        refuse("not_found", "Request not found.", 404)
    return req


def _check_reviewer(req, actor):
    if str(req.requester_id) == str(actor.id):
        refuse("own_request", "You can't review your own request.", 403)
    if not approvers.can_approve(actor.id, req):
        refuse("not_approver", "You are not an approver for this request.", 403)
    if req.status != TimeEntryRequestStatus.PENDING:
        refuse("not_pending", f"This request is already {req.status}.", 409)


def _close(req, actor, status, note):
    role = approvers.actor_role(req.workspace_id, req.project_id, actor.id)
    req.status = status
    req.reviewer_id = actor.id
    req.reviewer_role = role
    req.reviewed_at = timezone.now()
    req.review_note = note
    req.save(update_fields=["status", "reviewer", "reviewer_role", "reviewed_at", "review_note", "updated_at"])
    return role


def _stale():
    refuse("stale", "The entry changed since this request was made. Ask for a new request.", 409)


def _audit_rows(req, actor, rows, action, before=None):
    for row in rows:
        audit.record(
            req.workspace_id,
            actor,
            TimeTrackingAuditTarget.WORKLOG,
            row.id,
            action,
            before=before.get(row.id) if before else None,
            after={**audit.worklog_state(row), "request_id": str(req.id)},
        )


def _apply(req, actor, new_ended_at):
    """Apply an approved request. Returns the event payload. Raises (→ rollback) when stale."""
    tz = workspace_tz(req.workspace_id)
    requester = User.objects.get(pk=req.requester_id)
    if req.type == TimeEntryRequestType.MISSING_TIME:
        check_overlap(req.workspace_id, requester.id, req.new_started_at, req.new_ended_at, status_code=409)
        project = Project.objects.get(pk=req.project_id)
        billable_rate, currency = resolve_billable_rate(project, requester.id)
        issue_ref, issue_name = issue_snapshot(req.issue_id)
        base = IssueWorklog(
            project=project,
            issue_id=req.issue_id,
            issue_ref=issue_ref,
            issue_name=issue_name,
            logged_by=requester,
            description=req.description,
            source=WorklogSource.MANUAL,
            is_billable=True,
            billable_rate=billable_rate,
            currency=currency,
        )
        rows, _ = rewrite([base], req.new_started_at, req.new_ended_at, tz, request=req)
        req.worklog_id = rows[0].id
        req.save(update_fields=["worklog", "updated_at"])
        _audit_rows(req, actor, rows, TimeTrackingAuditAction.APPLY_REQUEST)
        return {"worklog_ids": [str(r.id) for r in rows], "removed_ids": []}

    worklog = IssueWorklog.all_objects.select_for_update().filter(pk=req.worklog_id).first()
    if worklog is None or worklog.deleted_at is not None:
        _stale()

    if req.type == TimeEntryRequestType.AUTO_STOP_REVIEW:
        rows = _session_rows(worklog)
        start, end, minutes = _span(rows)
        if (start, end, minutes) != (req.old_started_at, req.old_ended_at, req.old_duration):
            _stale()
        if any(r.is_locked for r in rows):
            refuse("entry_locked", "This entry is locked and can no longer be changed.", 409)
        before = {r.id: audit.worklog_state(r) for r in rows}
        if new_ended_at is None or new_ended_at == end:
            for row in rows:
                row.request = req
                row.save(update_fields=["request", "updated_at"])
            _audit_rows(req, actor, rows, TimeTrackingAuditAction.CONFIRM, before)
            return {"worklog_ids": [str(r.id) for r in rows], "removed_ids": [], "trimmed": False}
        if not start < new_ended_at < end:
            refuse("invalid_interval", "new_ended_at must be after the start and before the auto-stop time.")
        kept, removed = rewrite(rows, start, new_ended_at, tz, request=req)
        _audit_rows(req, actor, kept, TimeTrackingAuditAction.APPLY_REQUEST, before)
        for row in removed:
            audit.record(
                req.workspace_id,
                actor,
                TimeTrackingAuditTarget.WORKLOG,
                row.id,
                TimeTrackingAuditAction.DELETE,
                before=before.get(row.id),
                after={"request_id": str(req.id)},
            )
        return {
            "worklog_ids": [str(r.id) for r in kept],
            "removed_ids": [str(r.id) for r in removed],
            "trimmed": True,
            "new_ended_at": new_ended_at,
        }

    # time_change / deletion: the entry must be exactly as it was when the request was made.
    if (worklog.started_at, worklog.ended_at, worklog.duration, worklog.logged_date) != (
        req.old_started_at,
        req.old_ended_at,
        req.old_duration,
        req.work_date,
    ):
        _stale()
    if worklog.is_locked:
        refuse("entry_locked", "This entry is locked and can no longer be changed.", 409)
    before = {worklog.id: audit.worklog_state(worklog)}
    if req.type == TimeEntryRequestType.DELETION:
        worklog.request = req
        worklog.save(update_fields=["request", "updated_at"])
        audit.record(
            req.workspace_id,
            actor,
            TimeTrackingAuditTarget.WORKLOG,
            worklog.id,
            TimeTrackingAuditAction.APPLY_REQUEST,
            before=before[worklog.id],
            after={"deleted": True, "request_id": str(req.id)},
        )
        worklog.delete()
        return {"worklog_ids": [], "removed_ids": [str(worklog.id)]}
    check_overlap(
        req.workspace_id, requester.id, req.new_started_at, req.new_ended_at, exclude_ids=[worklog.id], status_code=409
    )
    rows, _ = rewrite([worklog], req.new_started_at, req.new_ended_at, tz, request=req)
    _audit_rows(req, actor, rows, TimeTrackingAuditAction.APPLY_REQUEST, before)
    return {"worklog_ids": [str(r.id) for r in rows], "removed_ids": []}


def approve(workspace_id, actor, request_id, note="", new_ended_at=None):
    note = str(note or "").strip()
    with transaction.atomic():
        req = _load_for_review(workspace_id, actor, request_id)
        _check_reviewer(req, actor)
        if new_ended_at not in (None, ""):
            if req.type != TimeEntryRequestType.AUTO_STOP_REVIEW:
                refuse("invalid_field", "new_ended_at is only for auto-stop reviews.")
            new_ended_at = parse_instant(new_ended_at, "new_ended_at")
        else:
            new_ended_at = None
        applied = _apply(req, actor, new_ended_at)
        role = _close(req, actor, TimeEntryRequestStatus.APPROVED, note)
        _event(req, TimeEntryRequestAction.APPROVED, actor.id, role, note=note)
        _event(req, TimeEntryRequestAction.APPLIED, actor.id, role, payload=applied)
        _notify(req, [req.requester_id], "request_approved", "Your time request was approved", actor.id)
    return req


def reject(workspace_id, actor, request_id, note):
    note = str(note or "").strip()
    with transaction.atomic():
        req = _load_for_review(workspace_id, actor, request_id)
        _check_reviewer(req, actor)
        if not note:
            refuse("note_required", "Say why the request is rejected.")
        role = _close(req, actor, TimeEntryRequestStatus.REJECTED, note)
        _event(req, TimeEntryRequestAction.REJECTED, actor.id, role, note=note)
        _notify(req, [req.requester_id], "request_rejected", "Your time request was rejected", actor.id)
    return req


def withdraw(workspace_id, actor, request_id):
    with transaction.atomic():
        req = _load_for_review(workspace_id, actor, request_id)
        if str(req.requester_id) != str(actor.id):
            refuse("not_requester", "Only the person who made the request can withdraw it.", 403)
        if req.status != TimeEntryRequestStatus.PENDING:
            refuse("not_pending", f"This request is already {req.status}.", 409)
        req.status = TimeEntryRequestStatus.WITHDRAWN
        req.save(update_fields=["status", "updated_at"])
        _event(
            req,
            TimeEntryRequestAction.WITHDRAWN,
            actor.id,
            approvers.actor_role(workspace_id, req.project_id, actor.id),
        )
    return req
