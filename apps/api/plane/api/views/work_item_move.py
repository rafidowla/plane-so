# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: move-work-item (PSR-91) — public API v1 endpoints.

Thin wrappers around plane.utils.work_item_move; same body, result and
permission rules as the app API (docs/move-work-item-design.md §5).
"""

# Python imports
import json

# Third party imports
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.views.base import BaseAPIView
from plane.app.permissions import ProjectEntityPermission
from plane.bgtasks.webhook_task import model_activity
from plane.db.models import Issue
from plane.utils.host import base_host
from plane.utils.work_item_mapping import resolve_target_project
from plane.utils.work_item_move import SUB_ITEMS_MOVE, MoveError, build_move_plan, move_work_item


def _get_issue(slug, project_id, issue_id):
    return (
        Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
        .select_related("project", "state", "parent", "estimate_point", "estimate_point__estimate")
        .first()
    )


class WorkItemMoveAPIEndpoint(BaseAPIView):
    """Preview (GET) or perform (POST) a move of a work item to another project."""

    permission_classes = [ProjectEntityPermission]
    use_read_replica = False

    @extend_schema(
        operation_id="preview_move_work_item",
        summary="Preview moving a work item to another project",
        tags=["Work Items"],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, slug, project_id, issue_id):
        issue = _get_issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)
        target = resolve_target_project(issue.workspace_id, request.query_params.get("target_project_id"))
        try:
            plan = build_move_plan(issue, target, request.user, request.query_params.get("sub_items", SUB_ITEMS_MOVE))
        except MoveError as e:
            return Response(e.as_response_data(), status=e.status_code)
        return Response(plan, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="move_work_item",
        summary="Move a work item to another project",
        description=(
            "Body: target_project_id (uuid), sub_items ('move' | 'detach', default 'move'), "
            "state_map ({source_state_id: target_state_id}, optional)."
        ),
        tags=["Work Items"],
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request, slug, project_id, issue_id):
        issue = _get_issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)
        target = resolve_target_project(issue.workspace_id, request.data.get("target_project_id"))
        source_identifier = issue.project.identifier
        try:
            result = move_work_item(
                issue,
                target,
                request.user,
                request.data.get("sub_items", SUB_ITEMS_MOVE),
                request.data.get("state_map") or {},
            )
        except MoveError as e:
            return Response(e.as_response_data(), status=e.status_code)
        model_activity.delay(
            model_name="issue",
            model_id=result["id"],
            requested_data={"project_id": result["project_id"]},
            current_instance=json.dumps({"project_id": str(project_id), "project_identifier": source_identifier}),
            actor_id=request.user.id,
            slug=slug,
            origin=base_host(request=request, is_app=False),
        )
        return Response(result, status=status.HTTP_200_OK)
