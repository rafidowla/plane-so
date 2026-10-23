# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 holidays/leave (PSR-122) — per-person leave.

* ``GET  time/leave/?user_id=&from=&to=`` — admins: everyone (filters optional); members: only
  their own (another ``user_id`` is a 403). Rows overlapping [from, to], newest first.
* ``POST time/leave/`` ``{user_id, start_date, end_date, portion="full", note=""}`` (admins).
* ``PATCH`` / ``DELETE time/leave/<id>/`` (admins).

Leave only reduces Expected hours (``plane.utils.time_tracking.days_off``): no approval flow,
no blocking of time entries. Every write is audited; errors are ``{"error", "code"}``.
"""

# Python imports
import uuid

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.app.views.time_tracking.holidays import (
    parse_date,
    parse_portion,
    refuse,
    soft_delete,
)
from plane.db.models import (
    DayPortion,
    MemberLeave,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.timer import TimeTrackingError

MAX_NOTE = 500
# One page is plenty for a workspace's leave; narrow with from/to/user_id beyond it.
MAX_LEAVE_ROWS = 5000


def leave_state(leave):
    return {
        "user_id": str(leave.user_id),
        "start_date": leave.start_date.isoformat(),
        "end_date": leave.end_date.isoformat(),
        "portion": leave.portion,
        "note": leave.note,
    }


def leave_out(leave, display_name):
    return {
        "id": str(leave.id),
        "user_id": str(leave.user_id),
        "display_name": display_name,
        "start_date": leave.start_date,
        "end_date": leave.end_date,
        "portion": leave.portion,
        "note": leave.note,
        "created_by": str(leave.created_by_id) if leave.created_by_id else None,
        "created_at": leave.created_at,
    }


def parse_note(value):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value.strip()) > MAX_NOTE:
        raise refuse("invalid_note", f"note must be text of at most {MAX_NOTE} characters.")
    return value.strip()


def validate_leave(workspace_id, user_id, start, end, portion, exclude_id=None):
    """Range, half-day and overlap rules (callers hold the member row lock)."""
    if end < start:
        raise refuse("invalid_range", "end_date can't be before start_date.")
    if portion == DayPortion.HALF.value and start != end:
        raise refuse("half_day_range", "A half day of leave must start and end on the same date.")
    overlapping = MemberLeave.objects.filter(
        workspace_id=workspace_id, user_id=user_id, start_date__lte=end, end_date__gte=start
    )
    if exclude_id:
        overlapping = overlapping.exclude(pk=exclude_id)
    if overlapping.exists():
        raise refuse("overlap", "This person already has leave on some of those dates.")


def lock_member(workspace_id, user_id):
    """Lock the person's membership row (serializes their leave writes); None if not internal."""
    return (
        WorkspaceMember.objects.select_for_update()
        .filter(workspace_id=workspace_id, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value)
        .first()
    )


def display_name_of(user_id):
    from plane.db.models import User

    return User.objects.filter(pk=user_id).values_list("display_name", flat=True).first() or ""


class MemberLeaveEndpoint(BaseAPIView):
    """``GET`` / ``POST time/leave/``."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.only("id").get(slug=slug)
        is_admin = WorkspaceMember.objects.filter(
            workspace_id=workspace.id, member=request.user, role=ROLE.ADMIN.value, is_active=True
        ).exists()
        params = request.query_params
        rows = MemberLeave.objects.filter(workspace_id=workspace.id)
        try:
            raw_user = params.get("user_id")
            if raw_user:
                if not is_admin and str(raw_user).lower() != str(request.user.id):
                    raise refuse("forbidden_user", "You can only see your own leave.", status.HTTP_403_FORBIDDEN)
                try:
                    rows = rows.filter(user_id=uuid.UUID(str(raw_user)))
                except ValueError:
                    raise refuse("invalid_user", "user_id must be a user id.")
            elif not is_admin:
                rows = rows.filter(user_id=request.user.id)
            if params.get("from"):
                rows = rows.filter(end_date__gte=parse_date(params.get("from"), "from"))
            if params.get("to"):
                rows = rows.filter(start_date__lte=parse_date(params.get("to"), "to"))
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        rows = list(rows.select_related("user").order_by("-start_date", "-created_at", "id")[:MAX_LEAVE_ROWS])
        return Response([leave_out(r, r.user.display_name or "") for r in rows], status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.only("id").get(slug=slug)
        data = request.data
        try:
            start = parse_date(data.get("start_date"), "start_date")
            end = parse_date(data.get("end_date"), "end_date")
            portion = parse_portion(data.get("portion", DayPortion.FULL.value))
            note = parse_note(data.get("note", ""))
            try:
                user_id = uuid.UUID(str(data.get("user_id")))
            except ValueError:
                user_id = None
            with transaction.atomic():
                if user_id is None or lock_member(workspace.id, user_id) is None:
                    raise refuse("not_a_member", "Leave applies to active workspace members and admins only.")
                validate_leave(workspace.id, user_id, start, end, portion)
                leave = MemberLeave(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    start_date=start,
                    end_date=end,
                    portion=portion,
                    note=note,
                )
                leave.save(created_by_id=request.user.id)
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.LEAVE,
                    leave.id,
                    TimeTrackingAuditAction.CREATE,
                    after=leave_state(leave),
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(leave_out(leave, display_name_of(user_id)), status=status.HTTP_201_CREATED)


class MemberLeaveDetailEndpoint(BaseAPIView):
    """``PATCH`` / ``DELETE time/leave/<id>/``."""

    @staticmethod
    def _leave(workspace_id, leave_id):
        leave = MemberLeave.objects.select_for_update().filter(workspace_id=workspace_id, pk=leave_id).first()
        if leave is None:
            raise refuse("not_found", "Leave not found.", status.HTTP_404_NOT_FOUND)
        return leave

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, leave_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        data = request.data
        try:
            with transaction.atomic():
                leave = self._leave(workspace.id, leave_id)
                lock_member(workspace.id, leave.user_id)  # serialize with other writes for this person
                before = leave_state(leave)
                if "start_date" in data:
                    leave.start_date = parse_date(data.get("start_date"), "start_date")
                if "end_date" in data:
                    leave.end_date = parse_date(data.get("end_date"), "end_date")
                if "portion" in data:
                    leave.portion = parse_portion(data.get("portion"))
                if "note" in data:
                    leave.note = parse_note(data.get("note"))
                validate_leave(
                    workspace.id, leave.user_id, leave.start_date, leave.end_date, leave.portion, exclude_id=leave.pk
                )
                after = leave_state(leave)
                if after != before:
                    leave.save()
                    audit.record(
                        workspace.id,
                        request.user,
                        TimeTrackingAuditTarget.LEAVE,
                        leave.id,
                        TimeTrackingAuditAction.UPDATE,
                        before=before,
                        after=after,
                    )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(leave_out(leave, display_name_of(leave.user_id)), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, leave_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                leave = self._leave(workspace.id, leave_id)
                before = leave_state(leave)
                soft_delete(MemberLeave.objects.filter(pk=leave.pk))
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.LEAVE,
                    leave.id,
                    TimeTrackingAuditAction.DELETE,
                    before=before,
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)
