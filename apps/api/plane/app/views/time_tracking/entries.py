# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — workspace-level manual time entries (My timesheet "Add time").

* ``POST time/entries/ {issue_id, logged_by?, started_at, ended_at | logged_date, duration,
  description?, work_type?, is_billable?, billable_rate?, currency?}`` → 201, stop-shaped payload
  (last piece + ``pieces`` + ``total_duration``; an entry over local midnight is split).
* ``PATCH time/entries/<id>/`` → 200 same shape · ``DELETE time/entries/<id>/`` → 204.

Rules (``plane.utils.time_tracking.entries``): workspace admins anything; project admins
other members' entries in their project; members add their own only when the workspace
allows manual entry, edit description only, and request any time change / deletion
(403 ``request_required``). Guests are refused by the workspace-level permission.
"""

# Python imports
import uuid

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.app.views.time_tracking.worklog import stop_response
from plane.db.models import Issue, IssueWorklog, Workspace
from plane.utils.time_tracking import entries
from plane.utils.time_tracking.timer import TimeTrackingError


def _not_found(what="Time entry"):
    return Response({"error": f"{what} not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


class TimeEntryEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.only("id").get(slug=slug)
        issue = (
            Issue.issue_objects.filter(pk=request.data.get("issue_id"), workspace_id=workspace.id)
            .values("id", "project_id")
            .first()
            if _is_uuid(request.data.get("issue_id"))
            else None
        )
        if issue is None:
            return _not_found("Work item")
        try:
            rows = entries.create_entry(workspace.id, issue["project_id"], issue["id"], request.user, request.data)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(stop_response(rows, request.user), status=status.HTTP_201_CREATED)


class TimeEntryDetailEndpoint(BaseAPIView):
    def _get(self, slug, pk):
        return IssueWorklog.objects.filter(pk=pk, workspace__slug=slug).first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def patch(self, request, slug, pk):
        worklog = self._get(slug, pk)
        if worklog is None:
            return _not_found()
        try:
            rows = entries.update_entry(worklog, request.user, request.data)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(stop_response(rows, request.user), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def delete(self, request, slug, pk):
        worklog = self._get(slug, pk)
        if worklog is None:
            return _not_found()
        try:
            entries.delete_entry(worklog, request.user)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True
