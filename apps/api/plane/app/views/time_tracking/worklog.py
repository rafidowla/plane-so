# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Sum
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseViewSet, BaseAPIView
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import IssueWorklogSerializer, WorklogTimerSerializer
from plane.db.models import (
    Project,
    Issue,
    IssueWorklog,
    WorklogTimer,
)

# FORK: time-tracking-v2 — timer start/stop and the shared helpers moved to a service
# layer (plane.utils.time_tracking) so the sweep and the S2 endpoints reuse them. The
# names below stay importable from here for existing callers.
# FORK: time-tracking-v2 S3 — manual create/edit/delete rules live in ``entries``.
from plane.utils.time_tracking import billing, entries
from plane.utils.time_tracking.timer import (  # noqa: F401
    TimeTrackingError,
    TimerAlreadyRunning,
    is_project_admin,
    is_project_member,
    issue_label as _issue_ref,
    lock_user_timers,
    resolve_billable_rate,
    start_timer,
    stop_user_timer,
    timer_schedule_fields,
)


def _stop_payload(worklogs, user):
    """FORK: time-tracking-v2 — a stop can now produce one entry per local day.

    ``worklog`` (and the top-level fields of a stop response) is the LAST piece, so
    single-day stops look exactly as before; ``pieces`` lists every entry and
    ``total_duration`` is their sum.
    """
    # FORK: time-tracking-v2 secfix — billing fields only for those who may manage rates.
    pieces = IssueWorklogSerializer(worklogs, many=True, context=billing.serializer_context(user)).data
    return pieces, sum(w.duration for w in worklogs)


def timer_data(timer, now=None):
    """FORK: time-tracking-v2 S2 — the one timer payload every timer endpoint returns:
    the serializer fields + ``issue_detail`` + the limit/idle-check schedule + ``server_now``."""
    data = WorklogTimerSerializer(timer).data
    data["issue_detail"] = _issue_ref(timer.issue_id)
    now = now or timezone.now()
    data.update(timer_schedule_fields(timer, now=now))
    data["server_now"] = now
    return data


def start_response(timer, stopped, state_change, user):
    """FORK: time-tracking-v2 S2 — response of both start endpoints (identical by design)."""
    data = timer_data(timer)
    if stopped:
        pieces, total = _stop_payload(stopped["worklogs"], user)
        data["stopped"] = {
            "worklog": pieces[-1] if pieces else None,
            "duration": total,
            "issue": stopped["issue"],
            "pieces": pieces,
            # FORK: PSR-121 — the stopped run was cut at the person's 24 h for the day.
            "day_limit_clamped": getattr(stopped["worklogs"], "day_limit_clamped", False),
        }
    data["state_change"] = state_change
    return data


def stop_response(worklogs, user):
    """FORK: PSR-121 — ``day_limit_clamped``: the run was cut where the person's local day
    reached 24 h. When the day was already full nothing is saved: ``pieces`` is empty and the
    top-level entry fields are absent (the timer is stopped all the same)."""
    pieces, total = _stop_payload(worklogs, user)
    data = dict(pieces[-1]) if pieces else {}
    data["pieces"] = pieces
    data["total_duration"] = total
    data["day_limit_clamped"] = getattr(worklogs, "day_limit_clamped", False)
    return data


class IssueWorklogViewSet(BaseViewSet):
    """Time entries logged against an issue.

    FORK: time-tracking-v2 S3 — writes follow ``plane.utils.time_tracking.entries``
    (workspace admins any entry; project admins other members' entries via
    ``logged_by``; members description-only, time changes by request).
    """

    serializer_class = IssueWorklogSerializer
    model = IssueWorklog

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .filter(project_id=self.kwargs.get("project_id"))
            .filter(issue_id=self.kwargs.get("issue_id"))
            .select_related("logged_by", "approved_by", "created_by")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def list(self, request, slug, project_id, issue_id):
        worklogs = self.get_queryset()
        # Internal roles (admin/member) get full detail.
        if is_project_member(slug, project_id, request.user):
            return Response(
                IssueWorklogSerializer(worklogs, many=True, context=billing.serializer_context(request.user)).data,
                status=status.HTTP_200_OK,
            )
        # FORK: time-tracking-v2 S2 — guests (clients) get ONE figure, the total logged
        # on a work item they can see (PO answer 4): no entry list, no dates, no names.
        project = Project.objects.only("guest_view_all_features").get(pk=project_id)
        if (
            not project.guest_view_all_features
            and not Issue.objects.filter(pk=issue_id, project_id=project_id, created_by=request.user).exists()
        ):
            return Response({"error": "You are not allowed to view this issue"}, status=status.HTTP_403_FORBIDDEN)
        total = worklogs.aggregate(total=Sum("duration"))["total"] or 0
        return Response({"total_minutes": total}, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def create(self, request, slug, project_id, issue_id):
        # FORK: time-tracking-v2 S3 — same rules as POST time/entries/ (one service):
        # workspace admins any entry; project admins entries for OTHER active members of
        # this project (never themselves); members their own only when the workspace allows
        # manual entry, else 403 ``request_required``; guests 403 ``not_tracked``. Forced
        # source=manual, audited, split at local midnight. Response: the last piece's fields
        # plus ``pieces`` / ``total_duration`` (a superset of the old single-entry body).
        project = Project.objects.only("workspace_id").filter(pk=project_id, workspace__slug=slug).first()
        if project is None:
            return Response({"error": "Work item not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            rows = entries.create_entry(project.workspace_id, project_id, issue_id, request.user, request.data)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(stop_response(rows, request.user), status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def partial_update(self, request, slug, project_id, issue_id, pk):
        # FORK: time-tracking-v2 S3 — members edit only the description of their own entries;
        # time fields → 403 ``request_required`` (a change request). Admin rules as in create.
        worklog = self.get_queryset().get(pk=pk)
        try:
            rows = entries.update_entry(worklog, request.user, request.data)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(stop_response(rows, request.user), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def destroy(self, request, slug, project_id, issue_id, pk):
        # FORK: time-tracking-v2 S3 — a member deleting their own entry → 403 ``request_required``.
        worklog = self.get_queryset().get(pk=pk)
        try:
            entries.delete_entry(worklog, request.user)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorklogTimerEndpoint(BaseAPIView):
    """Start/stop a stopwatch for the current user. One active timer per user."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id):
        timer = WorklogTimer.objects.filter(workspace__slug=slug, user=request.user).first()
        if not timer:
            return Response({}, status=status.HTTP_200_OK)
        # FORK: time-tracking-v2 S2 — same payload as the workspace timer endpoints.
        return Response(timer_data(timer), status=status.HTTP_200_OK)

    # FORK: time-tracking-v2 S2 — GUEST removed: guests never track time (start
    # refuses with 403 ``not_tracked`` in the service for project guests).
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        # FORK: time-tracking-v2 — logic lives in plane.utils.time_tracking.timer.start_timer
        # (shared with POST time/timer/start/, so both refuse/succeed identically).
        try:
            timer, stopped, state_change = start_timer(
                slug, project_id, issue_id, request.user, request.data.get("description", "")
            )
        except TimerAlreadyRunning as exc:
            return Response(
                {**exc.payload, "timer": WorklogTimerSerializer(exc.timer).data},
                status=exc.status_code,
            )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(start_response(timer, stopped, state_change, request.user), status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def delete(self, request, slug, project_id, issue_id):
        """Stop the running timer and convert it to worklog(s)."""
        worklogs = stop_user_timer(slug, issue_id, request.user, description=request.data.get("description"))
        if worklogs is None:  # FORK: PSR-121 — an empty stop (day already full) is still a stop
            return Response({"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(stop_response(worklogs, request.user), status=status.HTTP_201_CREATED)


class WorklogRunningTimerEndpoint(BaseAPIView):
    """FORK: PSR-105 — the current user's running timer in this workspace.

    Backs the always-visible timer indicator. Unlike the per-issue endpoint it needs
    no project/issue in the URL and returns what the indicator renders (work item
    reference, name, link ids) plus the server clock, so elapsed time is not skewed
    by a wrong client clock. Returns only ``server_now`` when no timer is running.
    FORK: time-tracking-v2 S2 — also the idle-check / reminder / auto-stop schedule
    (see ``timer_data``); served at ``worklog-timer/`` and ``time/timer/``.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        now = timezone.now()
        timer = WorklogTimer.objects.filter(workspace__slug=slug, user=request.user).first()
        if not timer:
            return Response({"server_now": now}, status=status.HTTP_200_OK)
        return Response(timer_data(timer, now), status=status.HTTP_200_OK)
