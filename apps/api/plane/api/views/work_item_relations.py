# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Work item dependencies, built-in custom relations and relation definitions (FORK: PSR-83, PSR-84).

Serve the stock Plane MCP connector's ``workitem_relation`` tool, which targets the
commercial API shape:

- ``.../work-items/<issue_id>/dependencies/`` (GET/POST) and ``.../dependencies/<related_id>/`` (DELETE)
  for the six directional dependency types.
- ``.../work-items/<issue_id>/work-item-relations/`` (GET/POST) and ``.../<related_id>/`` (DELETE)
  for "custom" relations. This edition has no relation-definition table, so a fixed built-in set
  (relates to / duplicate) is served with stable per-workspace ids and maps onto the existing
  ``IssueRelation`` types. ``implemented_by`` is deliberately not exposed: this edition's UI never shows
  it, so agent-made links would be invisible to people. Existing ``implemented_by`` rows are ignored
  by both GETs (they still count as an existing link for the 409 check).
- ``workspaces/<slug>/work-item-relation-definitions/`` lists that built-in set; writes are rejected.

Storage convention (same as the app and ``/relations/`` endpoints): a row
``(issue=A, related_issue=B, relation_type=T)`` reads "A T B", e.g. ``blocked_by`` = A is blocked by B.
From B's side the type is ``get_inverse_relation(T)``. Only the stored forms (``get_actual_relation``)
are ever written.

Conflicts: the DB allows one live row per ordered pair, and two rows in opposite directions would
contradict each other, so a POST rejects any target already linked to the source in either direction
with 409 -- except an exact repeat of the same link, which is idempotent (returned again, status 200
when nothing new was created, no new activity). Validation is all-or-nothing per request.

Visibility: targets and listed items are limited to work items the caller can see
(``visible_issues_q`` from PSR-85); anything else is reported as "not found or not accessible" with
its id only.
"""

import json
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.response import Response

from plane.api.views.base import BaseAPIView
from plane.api.views.work_item_query import visible_issues_q
from plane.app.permissions import ProjectEntityPermission, WorkspaceViewerPermission
from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import Issue, IssueAssignee, IssueLabel, IssueRelation, Workspace
from plane.utils.host import base_host
from plane.utils.issue_relation_mapper import get_actual_relation, get_inverse_relation
from plane.utils.openapi import (
    CURSOR_PARAMETER,
    FORBIDDEN_RESPONSE,
    INVALID_REQUEST_RESPONSE,
    ISSUE_ID_PARAMETER,
    PER_PAGE_PARAMETER,
    PROJECT_ID_PARAMETER,
    UNAUTHORIZED_RESPONSE,
    WORK_ITEM_NOT_FOUND_RESPONSE,
    WORKSPACE_NOT_FOUND_RESPONSE,
    WORKSPACE_SLUG_PARAMETER,
    CONFLICT_RESPONSE,
    DELETED_RESPONSE,
)

# Perspective types the dependency endpoints accept/return, and the stored forms behind them.
DEPENDENCY_TYPES = ("blocking", "blocked_by", "start_before", "start_after", "finish_before", "finish_after")
STORED_DEPENDENCY_TYPES = ("blocked_by", "start_before", "finish_before")
STORED_CUSTOM_TYPES = ("relates_to", "duplicate")
# Perspective types whose stored row points from the target to the source.
REVERSED_TYPES = ("blocking", "start_after", "finish_after")

# Built-in relation definitions: (key, name, outward, inward, outward type, inward type).
# Outward = "this item <outward> target"; inward = "this item <inward> target".
BUILT_IN_DEFINITIONS = (
    ("relates_to", "Relates to", "relates to", "relates to", "relates_to", "relates_to"),
    ("duplicate", "Duplicate", "duplicate of", "duplicate of", "duplicate", "duplicate"),
)
# Perspective type -> label used as the custom-relations GET key and item relation_type.
CUSTOM_LABELS = {
    "relates_to": "relates to",
    "duplicate": "duplicate of",
}
DEFINITION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://plane.so/fork/psr-83/work-item-relation-definitions")
DEFINITIONS_READ_ONLY_ERROR = (
    "Custom relation definitions are not available on this Plane edition. Use the built-in definitions "
    "from list_definitions, or a relation_type for dependencies."
)

ITEM_FIELDS = (
    "id",
    "name",
    "sequence_id",
    "project_id",
    "state_id",
    "priority",
    "type_id",
    "sort_order",
    "created_at",
    "updated_at",
    "created_by_id",
    "updated_by_id",
)


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": message}, status=code)


def _definition_id(workspace_id, key) -> str:
    return str(uuid.uuid5(DEFINITION_NAMESPACE, f"{workspace_id}:{key}"))


def _definitions(workspace):
    created = workspace.created_at.isoformat() if workspace.created_at else None
    return [
        {
            "id": _definition_id(workspace.id, key),
            "name": name,
            "outward": outward,
            "inward": inward,
            "is_default": True,
            "is_active": True,
            "color": None,
            "sort_order": float((index + 1) * 1000),
            "created_at": created,
            "updated_at": created,
            "workspace": str(workspace.id),
        }
        for index, (key, name, outward, inward, _, _) in enumerate(BUILT_IN_DEFINITIONS)
    ]


def _perspective(row, issue_id) -> str:
    """The relation type of ``row`` as seen from ``issue_id``."""
    if str(row.issue_id) == str(issue_id):
        return row.relation_type
    return get_inverse_relation(row.relation_type)


def _stored_pair(issue_id, target_id, perspective_type):
    """``(issue_id, related_issue_id, stored_type)`` that records ``issue_id <perspective_type> target_id``."""
    if perspective_type in REVERSED_TYPES:
        return target_id, issue_id, get_actual_relation(perspective_type)
    return issue_id, target_id, get_actual_relation(perspective_type)


def _serialize_items(issue_ids, relation_types):
    """WorkItemWithRelationType dicts for visible ``issue_ids`` (a queryset of ids, already scoped)."""
    rows = {row["id"]: row for row in Issue.issue_objects.filter(id__in=issue_ids).values(*ITEM_FIELDS)}
    labels, assignees = {}, {}
    for issue_id, label_id in IssueLabel.objects.filter(
        issue_id__in=rows, deleted_at__isnull=True, label__deleted_at__isnull=True
    ).values_list("issue_id", "label_id"):
        labels.setdefault(issue_id, []).append(str(label_id))
    for issue_id, assignee_id in IssueAssignee.objects.filter(issue_id__in=rows, deleted_at__isnull=True).values_list(
        "issue_id", "assignee_id"
    ):
        assignees.setdefault(issue_id, []).append(str(assignee_id))

    items = []
    for issue_id, relation_type in relation_types:
        row = rows.get(issue_id)
        if row is None:
            continue
        items.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "sequence_id": row["sequence_id"],
                "project_id": str(row["project_id"]),
                "state_id": str(row["state_id"]) if row["state_id"] else None,
                "priority": row["priority"],
                "type_id": str(row["type_id"]) if row["type_id"] else None,
                "is_epic": False,
                "label_ids": sorted(labels.get(issue_id, [])),
                "assignee_ids": sorted(assignees.get(issue_id, [])),
                "sort_order": row["sort_order"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "created_by": str(row["created_by_id"]) if row["created_by_id"] else None,
                "updated_by": str(row["updated_by_id"]) if row["updated_by_id"] else None,
                "relation_type": relation_type,
            }
        )
    return items


class _WorkItemRelationBase(BaseAPIView):
    """Shared plumbing for the dependency and custom-relation endpoints."""

    permission_classes = [ProjectEntityPermission]
    # Perspective types this endpoint manages, stored forms, and the label used in responses.
    stored_types: tuple = ()

    def response_type(self, perspective_type):
        return perspective_type

    def get_source(self, slug, project_id, issue_id):
        return (
            Issue.issue_objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug)
            .filter(visible_issues_q(self.request.user, slug))
            .first()
        )

    def visible_ids(self, slug):
        return Issue.issue_objects.filter(workspace__slug=slug).filter(visible_issues_q(self.request.user, slug))

    def relation_rows(self, issue_id, other_ids=None):
        rows = IssueRelation.objects.filter(Q(issue_id=issue_id) | Q(related_issue_id=issue_id))
        if other_ids is not None:
            rows = rows.filter(Q(issue_id__in=other_ids) | Q(related_issue_id__in=other_ids))
        return rows

    def grouped(self, slug, issue_id):
        """``[(other_id, perspective_type)]`` for this endpoint's types, visible items only."""
        pairs = []
        seen = set()
        for row in self.relation_rows(issue_id).filter(relation_type__in=self.stored_types).order_by("created_at"):
            other = row.related_issue_id if str(row.issue_id) == str(issue_id) else row.issue_id
            perspective = _perspective(row, issue_id)
            if (other, perspective) not in seen:
                seen.add((other, perspective))
                pairs.append((other, perspective))
        visible = self.visible_ids(slug).filter(id__in=[other for other, _ in pairs]).values("id")
        return _serialize_items(visible, [(other, self.response_type(p)) for other, p in pairs])

    def create_links(self, request, slug, project_id, issue_id, perspective_type, raw_ids):
        source = self.get_source(slug, project_id, issue_id)
        if source is None:
            return _error("Work item not found.", status.HTTP_404_NOT_FOUND)

        if not isinstance(raw_ids, list) or not raw_ids:
            return _error("work_item_ids must be a non-empty list of work item ids.")
        target_ids, invalid = [], []
        for raw in raw_ids:
            try:
                target = uuid.UUID(str(raw))
            except (TypeError, ValueError, AttributeError):
                invalid.append(str(raw))
                continue
            if target not in target_ids:
                target_ids.append(target)
        if source.id in target_ids:
            return _error("A work item cannot be linked to itself.")
        found = set(self.visible_ids(slug).filter(id__in=target_ids).values_list("id", flat=True))
        invalid += [str(t) for t in target_ids if t not in found]
        if invalid:
            return _error(f"Work item(s) not found or not accessible: {', '.join(invalid)}.")

        existing_rows = {}
        for row in self.relation_rows(source.id, target_ids):
            other = row.related_issue_id if row.issue_id == source.id else row.issue_id
            existing_rows.setdefault(other, []).append(row)

        to_create = []
        for target in target_ids:
            rows = existing_rows.get(target, [])
            if not rows:
                to_create.append(target)
                continue
            current = _perspective(rows[0], source.id)
            if len(rows) == 1 and current == perspective_type:
                continue  # exact repeat: idempotent, nothing to create
            return _error(
                f"Work items {source.id} and {target} are already linked as '{self.response_type(current)}'; "
                "remove that link first.",
                status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                for target in to_create:
                    issue_ref, related_ref, stored = _stored_pair(source.id, target, perspective_type)
                    IssueRelation.objects.create(
                        issue_id=issue_ref,
                        related_issue_id=related_ref,
                        relation_type=stored,
                        project_id=source.project_id,
                        workspace_id=source.workspace_id,
                        created_by=request.user,
                        updated_by=request.user,
                    )
        except IntegrityError:
            return _error(
                "One of these work items was linked concurrently; list the relations and retry.",
                status.HTTP_409_CONFLICT,
            )

        if to_create:
            issue_activity.delay(
                type="issue_relation.activity.created",
                requested_data=json.dumps(
                    {"relation_type": perspective_type, "issues": [str(t) for t in to_create]},
                    cls=DjangoJSONEncoder,
                ),
                actor_id=str(request.user.id),
                issue_id=str(source.id),
                project_id=str(source.project_id),
                current_instance=None,
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=base_host(request=request, is_app=True),
            )

        items = _serialize_items(
            self.visible_ids(slug).filter(id__in=target_ids).values("id"),
            [(t, self.response_type(perspective_type)) for t in target_ids],
        )
        return Response(items, status=status.HTTP_201_CREATED if to_create else status.HTTP_200_OK)

    def remove_link(self, request, slug, project_id, issue_id, related_id):
        source = self.get_source(slug, project_id, issue_id)
        if source is None:
            return _error("Work item not found.", status.HTTP_404_NOT_FOUND)
        rows = list(self.relation_rows(source.id, [related_id]).filter(relation_type__in=self.stored_types))
        if not rows:
            return _error("No such relation between these work items.", status.HTTP_404_NOT_FOUND)
        for row in rows:
            perspective = _perspective(row, source.id)
            current_instance = json.dumps(
                {
                    "id": str(row.id),
                    "issue": str(row.issue_id),
                    "related_issue": str(row.related_issue_id),
                    "relation_type": row.relation_type,
                },
                cls=DjangoJSONEncoder,
            )
            row.delete()
            issue_activity.delay(
                type="issue_relation.activity.deleted",
                requested_data=json.dumps(
                    {"related_issue": str(related_id), "relation_type": perspective}, cls=DjangoJSONEncoder
                ),
                actor_id=str(request.user.id),
                issue_id=str(source.id),
                project_id=str(source.project_id),
                current_instance=current_instance,
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=base_host(request=request, is_app=True),
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkItemDependencyListCreateEndpoint(_WorkItemRelationBase):
    """Dependencies of a work item, grouped by direction from its perspective."""

    stored_types = STORED_DEPENDENCY_TYPES

    @extend_schema(
        operation_id="list_work_item_dependencies",
        tags=["Work Item Relations"],
        summary="List work item dependencies",
        description=(
            "Dependencies of a work item grouped as blocking, blocked_by, start_before, start_after, "
            "finish_before and finish_after, from this work item's perspective. Only work items the caller "
            "can access are listed."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, ISSUE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(description="Dependencies grouped by direction"),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug, project_id, issue_id):
        source = self.get_source(slug, project_id, issue_id)
        if source is None:
            return _error("Work item not found.", status.HTTP_404_NOT_FOUND)
        body = {key: [] for key in DEPENDENCY_TYPES}
        for item in self.grouped(slug, source.id):
            body[item["relation_type"]].append(item)
        return Response(body, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="create_work_item_dependencies",
        tags=["Work Item Relations"],
        summary="Create work item dependencies",
        description=(
            'Body: {"relation_type": one of blocking, blocked_by, start_before, start_after, finish_before, '
            'finish_after, "work_item_ids": [uuid, ...]}. relation_type is from this work item\'s perspective. '
            "All-or-nothing: 400 for self-links or ids the caller can't access, 409 if a pair is already linked "
            "differently. Repeating an existing identical link is idempotent (200)."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, ISSUE_ID_PARAMETER],
        request=OpenApiTypes.OBJECT,
        responses={
            200: OpenApiResponse(description="All links already existed; the linked work items"),
            201: OpenApiResponse(description="The linked work items, each with relation_type"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
            409: CONFLICT_RESPONSE,
        },
    )
    def post(self, request, slug, project_id, issue_id):
        data = request.data if isinstance(request.data, dict) else {}
        relation_type = data.get("relation_type")
        if relation_type not in DEPENDENCY_TYPES:
            return _error(f"relation_type must be one of: {', '.join(DEPENDENCY_TYPES)}.")
        return self.create_links(request, slug, project_id, issue_id, relation_type, data.get("work_item_ids"))


class WorkItemDependencyDetailEndpoint(_WorkItemRelationBase):
    """Remove the dependency between two work items (PSR-84)."""

    stored_types = STORED_DEPENDENCY_TYPES

    @extend_schema(
        operation_id="delete_work_item_dependency",
        tags=["Work Item Relations"],
        summary="Remove work item dependency",
        description="Remove the dependency (any direction) between this work item and the related one.",
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PROJECT_ID_PARAMETER,
            ISSUE_ID_PARAMETER,
            OpenApiParameter("related_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={
            204: DELETED_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, slug, project_id, issue_id, related_id):
        return self.remove_link(request, slug, project_id, issue_id, related_id)


class _CustomRelationMixin:
    stored_types = STORED_CUSTOM_TYPES

    def response_type(self, perspective_type):
        return CUSTOM_LABELS.get(perspective_type, perspective_type)


class WorkItemCustomRelationListCreateEndpoint(_CustomRelationMixin, _WorkItemRelationBase):
    """Relations typed by the built-in definitions (relates to / duplicate of)."""

    @extend_schema(
        operation_id="list_work_item_custom_relations",
        tags=["Work Item Relations"],
        summary="List work item custom relations",
        description=(
            "Relations typed by the built-in relation definitions, keyed by label (relates to, duplicate of) "
            "from this work item's perspective. Only non-empty labels are present."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, ISSUE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(description="Related work items keyed by label"),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug, project_id, issue_id):
        source = self.get_source(slug, project_id, issue_id)
        if source is None:
            return _error("Work item not found.", status.HTTP_404_NOT_FOUND)
        body = {}
        for item in self.grouped(slug, source.id):
            body.setdefault(item["relation_type"], []).append(item)
        return Response(body, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="create_work_item_custom_relations",
        tags=["Work Item Relations"],
        summary="Create work item custom relations",
        description=(
            'Body: {"relation_definition_id": id from the definitions list, "relation_definition_type": that '
            'definition\'s outward or inward label (sets direction), "work_item_ids": [uuid, ...]}. Same '
            "validation, conflict and idempotency rules as dependencies."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, ISSUE_ID_PARAMETER],
        request=OpenApiTypes.OBJECT,
        responses={
            200: OpenApiResponse(description="All links already existed; the linked work items"),
            201: OpenApiResponse(description="The linked work items, each with relation_type set to the label"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
            409: CONFLICT_RESPONSE,
        },
    )
    def post(self, request, slug, project_id, issue_id):
        data = request.data if isinstance(request.data, dict) else {}
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return _error("Workspace not found.", status.HTTP_404_NOT_FOUND)
        definition_id = str(data.get("relation_definition_id") or "")
        label = data.get("relation_definition_type")
        by_id = {_definition_id(workspace.id, d[0]): d for d in BUILT_IN_DEFINITIONS}
        definition = by_id.get(definition_id)
        if definition is None:
            valid = "; ".join(f"{d[1]} ({_definition_id(workspace.id, d[0])})" for d in BUILT_IN_DEFINITIONS)
            return _error(
                f"Unknown relation_definition_id '{definition_id}'. Built-in definitions: {valid}. "
                "Call list_definitions to see them."
            )
        _, name, outward, inward, outward_type, inward_type = definition
        if label == outward:
            perspective_type = outward_type
        elif label == inward:
            perspective_type = inward_type
        else:
            labels = sorted({outward, inward})
            return _error(
                f"relation_definition_type '{label}' does not belong to '{name}'; "
                f"valid labels: {', '.join(repr(x) for x in labels)}."
            )
        return self.create_links(request, slug, project_id, issue_id, perspective_type, data.get("work_item_ids"))


class WorkItemCustomRelationDetailEndpoint(_CustomRelationMixin, _WorkItemRelationBase):
    """Remove the built-in custom relation between two work items."""

    @extend_schema(
        operation_id="delete_work_item_custom_relation",
        tags=["Work Item Relations"],
        summary="Remove work item custom relation",
        description=(
            "Remove the relates to / duplicate of relation (any direction) between this work item and the related one."
        ),
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PROJECT_ID_PARAMETER,
            ISSUE_ID_PARAMETER,
            OpenApiParameter("related_id", OpenApiTypes.UUID, OpenApiParameter.PATH),
        ],
        responses={
            204: DELETED_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORK_ITEM_NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, slug, project_id, issue_id, related_id):
        return self.remove_link(request, slug, project_id, issue_id, related_id)


def _bool_param(request, name):
    """``(value, error)``; value is None when the parameter is absent."""
    raw = request.GET.get(name)
    if raw is None or raw == "":
        return None, None
    lowered = raw.lower()
    if lowered in ("true", "1"):
        return True, None
    if lowered in ("false", "0"):
        return False, None
    return None, _error(f"{name} must be true or false.")


class WorkItemRelationDefinitionEndpoint(BaseAPIView):
    """The fixed, built-in relation definitions; this edition does not support custom ones."""

    permission_classes = [WorkspaceViewerPermission]

    @extend_schema(
        operation_id="list_work_item_relation_definitions",
        tags=["Work Item Relations"],
        summary="List work item relation definitions",
        description=(
            "Built-in relation definitions (Relates to, Duplicate) with stable per-workspace ids. "
            "Filter with is_default / is_active; paginate with per_page / cursor."
        ),
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            OpenApiParameter("is_default", OpenApiTypes.BOOL, OpenApiParameter.QUERY),
            OpenApiParameter("is_active", OpenApiTypes.BOOL, OpenApiParameter.QUERY),
        ],
        responses={
            200: OpenApiResponse(description="Paginated relation definitions"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return _error("Workspace not found.", status.HTTP_404_NOT_FOUND)
        definitions = _definitions(workspace)

        for name in ("is_default", "is_active"):
            value, error = _bool_param(request, name)
            if error is not None:
                return error
            if value is not None:
                definitions = [d for d in definitions if d[name] is value]

        try:
            per_page = int(request.GET.get("per_page", 100))
            page = int(request.GET.get("cursor", f"{per_page}:0:0").split(":")[1])
        except (TypeError, ValueError, IndexError):
            return _error("Invalid per_page or cursor parameter.")
        if per_page < 1 or per_page > 1000 or page < 0:
            return _error("Invalid per_page or cursor parameter.")

        total = len(definitions)
        results = definitions[page * per_page : (page + 1) * per_page]
        has_next = (page + 1) * per_page < total
        return Response(
            {
                "grouped_by": None,
                "sub_grouped_by": None,
                "total_count": total,
                "next_cursor": f"{per_page}:{page + 1}:0",
                "prev_cursor": f"{per_page}:{page - 1}:1",
                "next_page_results": has_next,
                "prev_page_results": page > 0,
                "count": len(results),
                "total_pages": -(-total // per_page),
                "total_results": total,
                "extra_stats": None,
                "results": results,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="create_work_item_relation_definition",
        tags=["Work Item Relations"],
        summary="Create work item relation definition (not supported)",
        description="Always 400: this edition only has the built-in definitions.",
        parameters=[WORKSPACE_SLUG_PARAMETER],
        request=OpenApiTypes.OBJECT,
        responses={400: INVALID_REQUEST_RESPONSE, 401: UNAUTHORIZED_RESPONSE, 403: FORBIDDEN_RESPONSE},
    )
    def post(self, request, slug):
        return _error(DEFINITIONS_READ_ONLY_ERROR)

    @extend_schema(
        operation_id="update_work_item_relation_definition",
        tags=["Work Item Relations"],
        summary="Update work item relation definition (not supported)",
        description="Always 400: this edition only has the built-in definitions.",
        parameters=[WORKSPACE_SLUG_PARAMETER],
        request=OpenApiTypes.OBJECT,
        responses={400: INVALID_REQUEST_RESPONSE, 401: UNAUTHORIZED_RESPONSE, 403: FORBIDDEN_RESPONSE},
    )
    def patch(self, request, slug, pk=None):
        return _error(DEFINITIONS_READ_ONLY_ERROR)

    @extend_schema(
        operation_id="delete_work_item_relation_definition",
        tags=["Work Item Relations"],
        summary="Delete work item relation definition (not supported)",
        description="Always 400: this edition only has the built-in definitions.",
        parameters=[WORKSPACE_SLUG_PARAMETER],
        responses={400: INVALID_REQUEST_RESPONSE, 401: UNAUTHORIZED_RESPONSE, 403: FORBIDDEN_RESPONSE},
    )
    def delete(self, request, slug, pk=None):
        return _error(DEFINITIONS_READ_ONLY_ERROR)
