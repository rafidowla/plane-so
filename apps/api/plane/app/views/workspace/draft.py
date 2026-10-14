# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json

# Django imports
from django.utils import timezone
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields import ArrayField
from django.db.models import Q, UUIDField, Value, Subquery, OuterRef
from django.db.models.functions import Coalesce
from django.utils.decorators import method_decorator
from django.views.decorators.gzip import gzip_page

# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers import (
    IssueCreateSerializer,
    DraftIssueCreateSerializer,
    DraftIssueSerializer,
    DraftIssueDetailSerializer,
)
from plane.db.models import (
    Issue,
    DraftIssue,
    CycleIssue,
    ModuleIssue,
    DraftIssueCycle,
    Workspace,
    FileAsset,
)
from .. import BaseViewSet
from plane.bgtasks.issue_activities_task import issue_activity
from plane.utils.issue_filters import issue_filters
from plane.utils.host import base_host

# FORK: draft-project-change (PSR-92)
from django.db import transaction
from plane.db.models import DraftIssueAssignee, DraftIssueLabel, DraftIssueModule
from plane.utils.work_item_mapping import (
    ProjectFieldMapper,
    can_write_in_project,
    is_project_member,
    resolve_target_project,
)

_DRAFT_MAPPED_KEYS = (
    "state_id",
    "label_ids",
    "assignee_ids",
    "estimate_point",
    "type_id",
    "parent_id",
    "cycle_id",
    "module_ids",
)


def _request_payload(request):
    """Mutable copy of the JSON body without the mass-assignable ``project`` key."""
    data = dict(request.data.items()) if hasattr(request.data, "items") else {}
    data.pop("project", None)
    return data


def _apply_mapping(data, mapped):
    for key in ("state_id", "label_ids", "assignee_ids", "estimate_point", "parent_id", "cycle_id", "module_ids"):
        data[key] = mapped[key]
    data["type_id"] = mapped["type_id"]
    return data


def _draft_stored_values(draft):
    return {
        "state_id": draft.state_id,
        "label_ids": list(DraftIssueLabel.objects.filter(draft_issue=draft).values_list("label_id", flat=True)),
        "assignee_ids": list(
            DraftIssueAssignee.objects.filter(draft_issue=draft).values_list("assignee_id", flat=True)
        ),
        "estimate_point": draft.estimate_point_id,
        "type_id": draft.type_id,
        "parent_id": draft.parent_id,
        "cycle_id": DraftIssueCycle.objects.filter(draft_issue=draft).values_list("cycle_id", flat=True).first(),
        "module_ids": list(DraftIssueModule.objects.filter(draft_issue=draft).values_list("module_id", flat=True)),
    }


class WorkspaceDraftIssueViewSet(BaseViewSet):
    model = DraftIssue

    def get_queryset(self):
        return (
            DraftIssue.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("workspace", "project", "state", "parent")
            .prefetch_related("assignees", "labels", "draft_issue_module__module")
            .annotate(
                cycle_id=Subquery(
                    DraftIssueCycle.objects.filter(draft_issue=OuterRef("id"), deleted_at__isnull=True).values(
                        "cycle_id"
                    )[:1]
                )
            )
            .annotate(
                label_ids=Coalesce(
                    ArrayAgg(
                        "labels__id",
                        distinct=True,
                        filter=Q(~Q(labels__id__isnull=True) & (Q(draft_label_issue__deleted_at__isnull=True))),
                    ),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
                assignee_ids=Coalesce(
                    ArrayAgg(
                        "assignees__id",
                        distinct=True,
                        filter=Q(
                            ~Q(assignees__id__isnull=True)
                            & Q(assignees__member_project__is_active=True)
                            & Q(draft_issue_assignee__deleted_at__isnull=True)
                        ),
                    ),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
                module_ids=Coalesce(
                    ArrayAgg(
                        "draft_issue_module__module_id",
                        distinct=True,
                        filter=Q(
                            ~Q(draft_issue_module__module_id__isnull=True)
                            & Q(draft_issue_module__module__archived_at__isnull=True)
                            & Q(draft_issue_module__deleted_at__isnull=True)
                        ),
                    ),
                    Value([], output_field=ArrayField(UUIDField())),
                ),
            )
        ).distinct()

    @method_decorator(gzip_page)
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        filters = issue_filters(request.query_params, "GET")
        issues = self.get_queryset().filter(created_by=request.user).order_by("-created_at")

        issues = issues.filter(**filters)
        # List Paginate
        return self.paginate(
            request=request,
            queryset=(issues),
            on_results=lambda issues: DraftIssueSerializer(issues, many=True).data,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        # FORK: draft-project-change (PSR-92) — a draft can only target a project the caller belongs to.
        draft_project_id = request.data.get("project_id", None)
        if draft_project_id:
            draft_project = resolve_target_project(workspace.id, draft_project_id)
            if draft_project is None or not is_project_member(request.user, draft_project):
                return Response(
                    {"error": "You are not a member of that project.", "code": "FORBIDDEN_TARGET"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = DraftIssueCreateSerializer(
            data=request.data,
            context={
                "workspace_id": workspace.id,
                "project_id": request.data.get("project_id", None),
            },
        )
        if serializer.is_valid():
            serializer.save()
            issue = (
                self.get_queryset()
                .filter(pk=serializer.data.get("id"))
                .values(
                    "id",
                    "name",
                    "state_id",
                    "sort_order",
                    "completed_at",
                    "estimate_point",
                    "priority",
                    "start_date",
                    "target_date",
                    "project_id",
                    "parent_id",
                    "cycle_id",
                    "module_ids",
                    "label_ids",
                    "assignee_ids",
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                    "type_id",
                    "description_html",
                )
                .first()
            )

            return Response(issue, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(
        allowed_roles=[ROLE.ADMIN, ROLE.MEMBER],
        creator=True,
        model=Issue,
        level="WORKSPACE",
    )
    def partial_update(self, request, slug, pk):
        issue = self.get_queryset().filter(pk=pk, created_by=request.user).first()

        if not issue:
            return Response({"error": "Issue not found"}, status=status.HTTP_404_NOT_FOUND)

        # FORK: draft-project-change (PSR-92) — a draft can be re-targeted to
        # another project; stored + submitted values are re-mapped with the
        # move-work-item rules. `project` is never taken from the body.
        data = _request_payload(request)
        project_id = data.get("project_id") or issue.project_id
        cycle_context = data.get("cycle_id", "not_provided")

        with transaction.atomic():
            if issue.project_id is None or str(project_id) != str(issue.project_id):
                target = resolve_target_project(issue.workspace_id, project_id)
                if target is None or target.archived_at is not None:
                    return Response(
                        {"error": "Project not found", "code": "INVALID_TARGET"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not is_project_member(request.user, target):
                    return Response(
                        {"error": "You are not a member of that project.", "code": "FORBIDDEN_TARGET"},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                stored = _draft_stored_values(issue)
                values = {key: data.get(key, stored[key]) for key in _DRAFT_MAPPED_KEYS}
                mapped = ProjectFieldMapper(source=issue.project or target, target=target).map_payload(values)
                _apply_mapping(data, mapped)
                data["type"] = mapped["type_id"]
                cycle_context = mapped["cycle_id"]
                DraftIssue.objects.filter(pk=issue.pk).update(project_id=target.id)
                issue.project = target
                project_id = target.id

            serializer = DraftIssueCreateSerializer(
                issue,
                data=data,
                partial=True,
                context={
                    "project_id": project_id,
                    "cycle_id": cycle_context,
                },
            )

            if serializer.is_valid():
                serializer.save()

                return Response(status=status.HTTP_204_NO_CONTENT)
            transaction.set_rollback(True)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(allowed_roles=[ROLE.ADMIN], creator=True, model=Issue, level="WORKSPACE")
    def retrieve(self, request, slug, pk=None):
        issue = self.get_queryset().filter(pk=pk, created_by=request.user).first()

        if not issue:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = DraftIssueDetailSerializer(issue)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], creator=True, model=DraftIssue, level="WORKSPACE")
    def destroy(self, request, slug, pk=None):
        draft_issue = DraftIssue.objects.get(workspace__slug=slug, pk=pk)
        draft_issue.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def create_draft_to_issue(self, request, slug, draft_id):
        # Drafts are private to their creator; scope the lookup to the owner so a
        # workspace member cannot convert/steal/delete another user's draft
        # Mirrors retrieve/partial_update.
        draft_issue = self.get_queryset().filter(pk=draft_id, created_by=request.user).first()

        if not draft_issue:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # FORK: draft-project-change (PSR-92) — the draft may be posted into a
        # different project than the one it was started in; the payload is
        # re-mapped with the move-work-item rules, and the caller must be able
        # to create work items there (project admin/member).
        data = _request_payload(request)
        target = resolve_target_project(draft_issue.workspace_id, data.get("project_id") or draft_issue.project_id)
        if target is None:
            return Response(
                {"error": "Project is required to create an issue."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.archived_at is not None:
            return Response(
                {"error": "The project is archived.", "code": "TARGET_ARCHIVED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not can_write_in_project(request.user, target):
            return Response(
                {"error": "You don't have permission to create work items in that project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if target.id != draft_issue.project_id:
            values = {key: data.get(key) for key in _DRAFT_MAPPED_KEYS}
            mapped = ProjectFieldMapper(source=draft_issue.project or target, target=target).map_payload(values)
            _apply_mapping(data, mapped)
        project_id = target.id

        serializer = IssueCreateSerializer(
            data=data,
            context={
                "project_id": project_id,
                "workspace_id": target.workspace_id,
                "default_assignee_id": target.default_assignee_id,
            },
        )

        if serializer.is_valid():
            serializer.save()

            issue_activity.delay(
                type="issue.activity.created",
                requested_data=json.dumps(data, cls=DjangoJSONEncoder),
                actor_id=str(request.user.id),
                issue_id=str(serializer.data.get("id", None)),
                project_id=str(project_id),
                current_instance=None,
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=base_host(request=request, is_app=True),
            )

            if data.get("cycle_id", None):
                created_records = CycleIssue.objects.create(
                    cycle_id=data.get("cycle_id", None),
                    issue_id=serializer.data.get("id", None),
                    project_id=project_id,
                    workspace_id=draft_issue.workspace_id,
                    created_by_id=draft_issue.created_by_id,
                    updated_by_id=draft_issue.updated_by_id,
                )
                # Capture Issue Activity
                issue_activity.delay(
                    type="cycle.activity.created",
                    requested_data=None,
                    actor_id=str(self.request.user.id),
                    issue_id=None,
                    project_id=str(project_id),  # FORK: PSR-92 (was self.kwargs project_id, always None)
                    current_instance=json.dumps(
                        {
                            "updated_cycle_issues": None,
                            "created_cycle_issues": serializers.serialize("json", [created_records]),
                        }
                    ),
                    epoch=int(timezone.now().timestamp()),
                    notification=True,
                    origin=base_host(request=request, is_app=True),
                )

            if data.get("module_ids", []):
                # bulk create the module
                ModuleIssue.objects.bulk_create(
                    [
                        ModuleIssue(
                            module_id=module,
                            issue_id=serializer.data.get("id", None),
                            workspace_id=draft_issue.workspace_id,
                            project_id=project_id,
                            created_by_id=draft_issue.created_by_id,
                            updated_by_id=draft_issue.updated_by_id,
                        )
                        for module in data.get("module_ids", [])
                    ],
                    batch_size=10,
                )
                # Update the activity
                _ = [
                    issue_activity.delay(
                        type="module.activity.created",
                        requested_data=json.dumps({"module_id": str(module)}),
                        actor_id=str(request.user.id),
                        issue_id=serializer.data.get("id", None),
                        project_id=project_id,
                        current_instance=None,
                        epoch=int(timezone.now().timestamp()),
                        notification=True,
                        origin=base_host(request=request, is_app=True),
                    )
                    for module in data.get("module_ids", [])
                ]

            # Update file assets
            file_assets = FileAsset.objects.filter(draft_issue_id=draft_id)
            file_assets.update(
                issue_id=serializer.data.get("id", None),
                entity_type=FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
                draft_issue_id=None,
            )

            # delete the draft issue
            draft_issue.delete()

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
