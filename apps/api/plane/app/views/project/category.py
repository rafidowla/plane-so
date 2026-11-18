# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: PSR-123 -- workspace project categories (admins manage, every active member reads).

# Django imports
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Project, ProjectCategory, Workspace, WorkspaceMember

DUPLICATE_MESSAGE = "A category with this name already exists in this workspace."


def _count_affected_projects(category):
    """Projects a category delete would clear: every live project, archived ones included."""
    return Project.all_objects.filter(category=category, deleted_at__isnull=True).count()


def _serialize(category, project_count=None, include_count=True):
    data = {
        "id": category.id,
        "name": category.name,
        "sort_order": category.sort_order,
    }
    # project_count is admin-only: for other roles it would leak how many (possibly private) projects use it.
    if include_count:
        data["project_count"] = _count_affected_projects(category) if project_count is None else project_count
    return data


def _clean_payload(data, require_name):
    """Return (values, error). Only keys present in ``data`` are returned."""
    values = {}
    if "name" in data or require_name:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, {"name": ["This field is required and cannot be blank."]}
        name = name.strip()
        if len(name) > 100:
            return None, {"name": ["Ensure this field has no more than 100 characters."]}
        values["name"] = name
    if "sort_order" in data:
        try:
            sort_order = float(data["sort_order"])
        except (TypeError, ValueError):
            return None, {"sort_order": ["A valid number is required."]}
        if sort_order != sort_order or sort_order in (float("inf"), float("-inf")):
            return None, {"sort_order": ["A valid number is required."]}
        values["sort_order"] = sort_order
    return values, None


class ProjectCategoryEndpoint(BaseAPIView):
    def _duplicate_exists(self, workspace, name, exclude_id=None):
        qs = ProjectCategory.objects.filter(workspace=workspace).annotate(_n=Lower("name")).filter(_n=name.lower())
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return qs.exists()

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        is_admin = WorkspaceMember.objects.filter(
            workspace__slug=slug, member=request.user, role=ROLE.ADMIN.value, is_active=True
        ).exists()
        categories = ProjectCategory.objects.filter(workspace__slug=slug)
        if is_admin:
            categories = categories.annotate(
                project_count=Count("projects", filter=Q(projects__deleted_at__isnull=True))
            )
        categories = categories.order_by("sort_order", "name")
        return Response(
            [
                _serialize(c, c.project_count if is_admin else None, include_count=is_admin)
                for c in categories
            ],
            status=status.HTTP_200_OK,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        values, error = _clean_payload(request.data, require_name=True)
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)
        if self._duplicate_exists(workspace, values["name"]):
            return Response({"error": DUPLICATE_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                category = ProjectCategory.objects.create(workspace=workspace, **values)
        except IntegrityError:
            return Response({"error": DUPLICATE_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize(category, 0), status=status.HTTP_201_CREATED)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, pk):
        category = ProjectCategory.objects.filter(workspace__slug=slug, pk=pk).first()
        if category is None:
            return Response({"error": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        values, error = _clean_payload(request.data, require_name=False)
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)
        if "name" in values and self._duplicate_exists(category.workspace, values["name"], exclude_id=category.pk):
            return Response({"error": DUPLICATE_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        for key, value in values.items():
            setattr(category, key, value)
        try:
            with transaction.atomic():
                category.save()
        except IntegrityError:
            return Response({"error": DUPLICATE_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize(category), status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, pk):
        category = ProjectCategory.objects.filter(workspace__slug=slug, pk=pk).first()
        if category is None:
            return Response({"error": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
        # Clear the FK synchronously (soft delete leaves it to a queued task), archived projects included.
        with transaction.atomic():
            Project.all_objects.filter(category=category).update(category=None)
            category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
