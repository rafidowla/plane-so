# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: move-work-item (PSR-91) / draft-project-change (PSR-92).

App-API endpoints for moving a work item to another project, and the
stateless field-mapping preview used by the draft modal.
See docs/move-work-item-design.md §5.
"""

# Python imports
import json

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.bgtasks.webhook_task import model_activity
from plane.db.models import Issue, Project
from plane.utils.host import base_host
from plane.utils.work_item_mapping import ProjectFieldMapper, is_project_member, resolve_target_project
from plane.utils.work_item_move import (
    SUB_ITEMS_MOVE,
    MoveError,
    build_move_plan,
    move_work_item,
)


def _get_issue(slug, project_id, issue_id):
    return (
        Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
        .select_related("project", "state", "parent", "estimate_point", "estimate_point__estimate")
        .first()
    )


class WorkItemMovePreviewEndpoint(BaseAPIView):
    use_read_replica = False

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        issue = _get_issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)
        target = resolve_target_project(issue.workspace_id, request.query_params.get("target_project_id"))
        sub_items = request.query_params.get("sub_items", SUB_ITEMS_MOVE)
        try:
            plan = build_move_plan(issue, target, request.user, sub_items)
        except MoveError as e:
            return Response(e.as_response_data(), status=e.status_code)
        return Response(plan, status=status.HTTP_200_OK)


class WorkItemMoveEndpoint(BaseAPIView):
    use_read_replica = False

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = _get_issue(slug, project_id, issue_id)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)
        # Explicit allowlist — nothing else in the body is read (cf. WEB-8332).
        target = resolve_target_project(issue.workspace_id, request.data.get("target_project_id"))
        sub_items = request.data.get("sub_items", SUB_ITEMS_MOVE)
        state_map = request.data.get("state_map") or {}
        source_identifier = issue.project.identifier
        try:
            result = move_work_item(issue, target, request.user, sub_items, state_map)
        except MoveError as e:
            return Response(e.as_response_data(), status=e.status_code)

        model_activity.delay(
            model_name="issue",
            model_id=result["id"],
            requested_data={"project_id": result["project_id"]},
            current_instance=json.dumps({"project_id": str(project_id), "project_identifier": source_identifier}),
            actor_id=request.user.id,
            slug=slug,
            origin=base_host(request=request, is_app=True),
        )
        return Response(result, status=status.HTTP_200_OK)


class DraftProjectMappingEndpoint(BaseAPIView):
    """FORK: draft-project-change (PSR-92) — map draft form values to another project."""

    use_read_replica = False

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def post(self, request, slug):
        data = request.data
        source = Project.objects.filter(workspace__slug=slug, pk=data.get("source_project_id")).first()
        target = Project.objects.filter(workspace__slug=slug, pk=data.get("target_project_id")).first()
        if source is None or target is None:
            return Response(
                {"error": "Project not found", "code": "INVALID_TARGET"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not is_project_member(request.user, source) or not is_project_member(request.user, target):
            return Response(
                {"error": "You are not a member of that project.", "code": "FORBIDDEN_TARGET"},
                status=status.HTTP_403_FORBIDDEN,
            )
        values = {
            key: data.get(key)
            for key in (
                "state_id",
                "label_ids",
                "assignee_ids",
                "estimate_point",
                "type_id",
                "parent_id",
                "cycle_id",
                "module_ids",
            )
        }
        return Response(ProjectFieldMapper(source=source, target=target).map_payload(values))
