# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — workspace-level timer endpoints (My timesheet's timer bar).

``POST time/timer/start/`` runs the SAME service as the per-issue
``worklog-timer/`` POST and returns the same payload. ``GET time/timer/`` is the
running-timer payload (``WorklogRunningTimerEndpoint``).
"""

# Python imports
import uuid

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import WorklogTimerSerializer
from plane.app.views.base import BaseAPIView
from plane.app.views.time_tracking.worklog import start_response, stop_response, timer_data
from plane.db.models import Issue, ProjectMember
from plane.utils.time_tracking.timer import (
    TimerAlreadyRunning,
    TimeTrackingError,
    confirm_timer,
    start_timer,
    stop_running_timer,
)


class WorkspaceTimerStartEndpoint(BaseAPIView):
    """``POST {issue_id, description?}`` — start a timer on any work item the caller can track."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        try:
            issue_id = uuid.UUID(str(request.data.get("issue_id")))
        except (TypeError, ValueError, AttributeError):
            return Response({"error": "issue_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        issue = Issue.objects.filter(pk=issue_id, workspace__slug=slug).values("project_id").first()
        # 404 (not 403) when the caller is not in the item's project at all, so the
        # endpoint can't be used to probe which work item ids exist.
        if (
            issue is None
            or not ProjectMember.objects.filter(
                workspace__slug=slug, project_id=issue["project_id"], member=request.user, is_active=True
            ).exists()
        ):
            return Response({"error": "Work item not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            timer, stopped, state_change = start_timer(
                slug, issue["project_id"], issue_id, request.user, request.data.get("description", "")
            )
        except TimerAlreadyRunning as exc:
            return Response(
                {**exc.payload, "timer": WorklogTimerSerializer(exc.timer).data},
                status=exc.status_code,
            )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(start_response(timer, stopped, state_change), status=status.HTTP_201_CREATED)


class WorkspaceTimerStopEndpoint(BaseAPIView):
    """``POST {description?}`` — stop the caller's running timer in this workspace."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        label, worklogs = stop_running_timer(slug, request.user, description=request.data.get("description"))
        if not worklogs:
            return Response({"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND)
        data = stop_response(worklogs)
        data["issue_detail"] = label
        return Response(data, status=status.HTTP_201_CREATED)


class WorkspaceTimerConfirmEndpoint(BaseAPIView):
    """``POST`` — the "Still working?" Yes button.

    200: confirmed; body = the running-timer payload with the next check.
    409: too late — the timer was already due to stop; it is stopped now at the
    prompt time (``code: idle_stopped``) or at the 14 h limit (``code: auto_stopped``)
    and ``stopped`` holds the stop payload. The UI then offers "Resume".
    404: no running timer in this workspace.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        outcome, result = confirm_timer(slug, request.user)
        if outcome is None:
            return Response({"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND)
        if outcome == "stopped":
            auto = any(w.auto_stopped for w in result)
            return Response(
                {
                    "error": "The timer was already stopped.",
                    "code": "auto_stopped" if auto else "idle_stopped",
                    "stopped": stop_response(result),
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(timer_data(result), status=status.HTTP_200_OK)
