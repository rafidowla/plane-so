# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Workspace-wide work item list and count endpoints (FORK: PSR-85).

Serve the stock Plane MCP connector's ``GET workspaces/{slug}/work-items/`` and
``GET workspaces/{slug}/work-items/count/`` calls, filtered by ``?pql=`` /
``?filters=`` through the PQL engine in `plane.api.pql`.

Visibility mirrors the internal app (``_get_project_permission_filters`` in
``plane/app/views/view/base.py``): only issues in non-archived projects where
the caller is an active project member; guests (role 5) only see issues they
created unless the project has ``guest_view_all_features``. It is applied as
``project_id__in`` subqueries so it can never duplicate rows.
"""

from collections import defaultdict
from datetime import date

from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response

from plane.api.pql import PQLContext, PQLParamError, compile_request_filters
from plane.api.serializers import IssueSerializer
from plane.api.views.base import BaseAPIView
from plane.api.views.issue import IssueListCreateAPIEndpoint
from plane.app.permissions import ROLE, WorkspaceEntityPermission
from plane.db.models import CycleIssue, Issue, IssueAssignee, IssueLabel, ModuleIssue, ProjectMember
from plane.utils.openapi import (
    CURSOR_PARAMETER,
    EXPAND_PARAMETER,
    FIELDS_PARAMETER,
    FORBIDDEN_RESPONSE,
    INVALID_REQUEST_RESPONSE,
    ORDER_BY_PARAMETER,
    PER_PAGE_PARAMETER,
    UNAUTHORIZED_RESPONSE,
    WORKSPACE_NOT_FOUND_RESPONSE,
    WORKSPACE_SLUG_PARAMETER,
    create_paginated_response,
)
from plane.utils.openapi.pql_parameters import (
    FILTERS_PARAMETER,
    GROUP_BY_PARAMETER,
    PQL_PARAMETER,
    SUB_GROUP_BY_PARAMETER,
)

# group_by value -> Issue column
DIRECT_GROUPS = {
    "state_id": "state_id",
    "state__group": "state__group",
    "priority": "priority",
    "project_id": "project_id",
    "created_by": "created_by_id",
    "target_date": "target_date",
    "start_date": "start_date",
}
# group_by value -> (through model, key column); soft-deleted through-rows never count
THROUGH_GROUPS = {
    "labels__id": (IssueLabel, "label_id"),
    "assignees__id": (IssueAssignee, "assignee_id"),
    "issue_module__module_id": (ModuleIssue, "module_id"),
    "cycle_id": (CycleIssue, "cycle_id"),
}
SUPPORTED_GROUPS = (*DIRECT_GROUPS, *THROUGH_GROUPS)
NONE_KEY = "None"


def visible_issues_q(user, slug) -> Q:
    """Issues ``user`` may see in workspace ``slug`` (see module docstring)."""
    memberships = ProjectMember.objects.filter(
        member=user,
        is_active=True,
        workspace__slug=slug,
        project__archived_at__isnull=True,
        project__deleted_at__isnull=True,
    )
    full_access = memberships.filter(
        Q(role__gt=ROLE.GUEST.value) | Q(role=ROLE.GUEST.value, project__guest_view_all_features=True)
    ).values("project_id")
    own_only = memberships.filter(role=ROLE.GUEST.value, project__guest_view_all_features=False).values("project_id")
    return Q(project_id__in=full_access) | Q(project_id__in=own_only, created_by=user)


def _filter_q_or_error(request, slug):
    """Return ``(filter_q, None)`` or ``(None, 400 response)``."""
    try:
        return compile_request_filters(request.GET, PQLContext(workspace_slug=slug, user=request.user)), None
    except PQLParamError as exc:
        return None, Response(exc.as_response_body(), status=status.HTTP_400_BAD_REQUEST)


def _key(value) -> str:
    if value is None:
        return NONE_KEY
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _keys_by_issue(dimension: str, issue_ids) -> dict:
    """Map issue id -> group keys for ``dimension``; ``issue_ids`` is a ``values("id")`` subquery.

    Issues with no through-rows are absent; callers bucket them under ``"None"``.
    """
    if dimension in DIRECT_GROUPS:
        rows = Issue.objects.filter(id__in=issue_ids).values_list("id", DIRECT_GROUPS[dimension])
        return {issue_id: [_key(value)] for issue_id, value in rows}
    model, column = THROUGH_GROUPS[dimension]
    keys = defaultdict(list)
    rows = model.objects.filter(issue_id__in=issue_ids, deleted_at__isnull=True).values_list("issue_id", column)
    for issue_id, value in rows.distinct():
        keys[issue_id].append(_key(value))
    return keys


class WorkspaceWorkItemListEndpoint(IssueListCreateAPIEndpoint):
    """Paginated work items across every project in the workspace the caller can see."""

    permission_classes = [WorkspaceEntityPermission]

    def get_scope_q(self):
        return visible_issues_q(self.request.user, self.kwargs.get("slug"))

    @extend_schema(
        operation_id="list_workspace_work_items",
        tags=["Work Items"],
        summary="List workspace work items",
        description=(
            "Retrieve a paginated list of work items across all projects in the workspace that the caller "
            "can access. Supports pql/filters, ordering and field selection like the project list."
        ),
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            ORDER_BY_PARAMETER,
            FIELDS_PARAMETER,
            EXPAND_PARAMETER,
            PQL_PARAMETER,
            FILTERS_PARAMETER,
        ],
        responses={
            200: create_paginated_response(
                IssueSerializer,
                "PaginatedWorkspaceWorkItemResponse",
                "Paginated list of work items across the workspace",
                "Paginated Workspace Work Items",
            ),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug):
        filter_q, error = _filter_q_or_error(request, slug)
        if error is not None:
            return error
        return self.list_work_items(request, filter_q)


class WorkspaceWorkItemCountEndpoint(BaseAPIView):
    """Counts of matching work items, optionally grouped by one or two dimensions."""

    permission_classes = [WorkspaceEntityPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="count_workspace_work_items",
        tags=["Work Items"],
        summary="Count workspace work items",
        description=(
            "Count work items the caller can access in the workspace, filtered by pql/filters and "
            "optionally grouped by group_by and sub_group_by. Group keys are ids, plain values or ISO "
            'dates; "None" collects items with no value. Many-valued dimensions (labels, assignees, '
            "modules) count an item once in each of its groups."
        ),
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PQL_PARAMETER,
            FILTERS_PARAMETER,
            GROUP_BY_PARAMETER,
            SUB_GROUP_BY_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(description="Work item counts"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug):
        group_by = request.GET.get("group_by") or None
        sub_group_by = request.GET.get("sub_group_by") or None
        for param, value in (("group_by", group_by), ("sub_group_by", sub_group_by)):
            if value is not None and value not in SUPPORTED_GROUPS:
                return self._bad_request(
                    f"{param} '{value}' is not supported on this Plane edition; "
                    f"supported: {', '.join(SUPPORTED_GROUPS)}."
                )
        if sub_group_by and not group_by:
            return self._bad_request("sub_group_by requires group_by.")
        if sub_group_by and sub_group_by == group_by:
            return self._bad_request("sub_group_by must be different from group_by.")

        filter_q, error = _filter_q_or_error(request, slug)
        if error is not None:
            return error

        issue_ids = (
            Issue.issue_objects.filter(workspace__slug=slug)
            .filter(visible_issues_q(request.user, slug))
            .filter(filter_q)
            .values("id")
        )
        body = {
            "grouped_by": group_by,
            "sub_grouped_by": sub_group_by,
            "total_count": issue_ids.count(),
            "grouped_counts": {},
        }
        if group_by:
            body["grouped_counts"] = self._grouped_counts(issue_ids, group_by, sub_group_by)
        return Response(body, status=status.HTTP_200_OK)

    @staticmethod
    def _bad_request(message):
        return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _grouped_counts(issue_ids, group_by, sub_group_by):
        ids = list(issue_ids.values_list("id", flat=True))
        groups = _keys_by_issue(group_by, issue_ids)
        subs = _keys_by_issue(sub_group_by, issue_ids) if sub_group_by else {}

        counts = defaultdict(int)
        sub_counts = defaultdict(lambda: defaultdict(int))
        for issue_id in ids:
            for group in groups.get(issue_id) or [NONE_KEY]:
                counts[group] += 1
                if sub_group_by:
                    for sub in subs.get(issue_id) or [NONE_KEY]:
                        sub_counts[group][sub] += 1

        result = {}
        for group in sorted(counts, key=lambda k: (-counts[k], k == NONE_KEY, k)):
            entry = {"count": counts[group]}
            if sub_group_by:
                subs_for_group = sub_counts[group]
                entry["sub_grouped_counts"] = {
                    sub: {"count": subs_for_group[sub]}
                    for sub in sorted(subs_for_group, key=lambda k: (-subs_for_group[k], k == NONE_KEY, k))
                }
            result[group] = entry
        return result
