# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S6 — a work item's time estimate (DESIGN §2 items 33-35, §4).

* ``GET  projects/<p>/issues/<i>/time-estimate/`` → 200 ``{issue_id, project_id, mode,
  required, work_started, grandfathered, can_edit, estimate, revisions}``.
* ``PUT  projects/<p>/issues/<i>/time-estimate/ {minutes, reason?}`` → 200 same shape.
  ``minutes`` null clears an estimate that is not locked yet.

Project admins and members only (guests 403). Rules live in
``plane.utils.time_tracking.estimate``.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, TimeEstimateMode
from plane.utils.time_tracking import estimate as estimates
from plane.utils.time_tracking.timer import TimeTrackingError


class IssueTimeEstimateEndpoint(BaseAPIView):
    def _issue(self, slug, project_id, issue_id):
        return Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()

    def _payload(self, issue):
        payload = estimates.estimate_payload(issue)
        payload["can_edit"] = payload["mode"] != TimeEstimateMode.OFF.value
        return payload

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._payload(issue), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def put(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            estimates.set_estimate(issue, request.user, request.data)
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(self._payload(issue), status=status.HTTP_200_OK)
