# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — the timer bar's task picker (DESIGN §2 item 7)."""

# Python imports
import re

# Django imports
from django.db.models import Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, IssueAssignee, StateGroup, Workspace
from plane.utils.time_tracking.tracking import is_tracked
from plane.utils.time_tracking.tz import local_today, zone
from plane.utils.time_tracking.visibility import content_member_projects

DEFAULT_RECENT = 5
DEFAULT_SEARCH = 20
MAX_LIMIT = 50
MAX_QUERY_LENGTH = 100

# "TTP-12", "ttp 12", "TTP12"
_REF = re.compile(r"^\s*([A-Za-z0-9]+?)[-\s]?(\d+)\s*$")


class TimeMyTasksEndpoint(BaseAPIView):
    """``GET ?q=&limit=`` — work items assigned to me that I can time.

    Items: assigned to the caller, in a project with time tracking on where the
    caller is an active admin/member; Done (completed) included, Cancelled, triage,
    archived and draft items excluded. No ``q`` -> the 5 most recently updated;
    with ``q`` -> matches by identifier (``TTP-12``, ``ttp12``, ``12``) or title.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.only("id", "timezone").get(slug=slug)
        q = (request.query_params.get("q") or "").strip()[:MAX_QUERY_LENGTH]
        try:
            limit = int(request.query_params.get("limit") or (DEFAULT_SEARCH if q else DEFAULT_RECENT))
        except ValueError:
            return Response({"error": "limit must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        limit = max(1, min(limit, MAX_LIMIT))

        tracked = is_tracked(workspace.id, request.user.id, local_today(zone(workspace.timezone)))
        if not tracked:
            return Response({"tracked": False, "results": []}, status=status.HTTP_200_OK)

        issues = Issue.issue_objects.filter(
            workspace_id=workspace.id,
            project__is_time_tracking_enabled=True,
            project_id__in=content_member_projects(request.user.id),
            id__in=IssueAssignee.objects.filter(workspace_id=workspace.id, assignee=request.user).values("issue_id"),
        ).exclude(state__group=StateGroup.CANCELLED.value)

        if q:
            match = Q(name__icontains=q)
            ref = _REF.match(q)
            if ref:
                match |= Q(project__identifier__iexact=ref.group(1), sequence_id=int(ref.group(2)))
            if q.isdigit():
                match |= Q(sequence_id=int(q))
            issues = issues.filter(match)

        rows = issues.order_by("-updated_at", "-id").values(
            "id",
            "name",
            "sequence_id",
            "project_id",
            "project__identifier",
            "state_id",
            "state__name",
            "state__group",
            "updated_at",
        )[:limit]
        results = [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "sequence_id": r["sequence_id"],
                "ref": f"{r['project__identifier']}-{r['sequence_id']}",
                "project_id": str(r["project_id"]),
                "project_identifier": r["project__identifier"],
                "state_id": str(r["state_id"]) if r["state_id"] else None,
                "state_name": r["state__name"],
                "state_group": r["state__group"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
        return Response({"tracked": True, "results": results}, status=status.HTTP_200_OK)
