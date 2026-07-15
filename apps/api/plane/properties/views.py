# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties views.

Phase 0 ships only a capability/health probe. Property/option/value viewsets
are added in Phase 1/2 (see docs/custom-properties-design.md §4).
"""

from django.utils.text import slugify

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, Project, ProjectMember
from plane.properties.flags import is_instance_enabled
from plane.properties.models import (
    IssueProperty,
    IssuePropertyOption,
    IssuePropertyValue,
    ProjectPropertiesFeature,
    PropertyTypeEnum,
)
from plane.properties.serializers import (
    IssuePropertyOptionSerializer,
    IssuePropertySerializer,
)


def is_project_admin(slug, project_id, user):
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


def project_feature_enabled(slug, project_id):
    """The feature is live for a project only when the instance kill switch is on
    AND the per-project toggle is on (docs/custom-properties-design.md §8)."""
    return is_instance_enabled() and ProjectPropertiesFeature.objects.filter(
        workspace__slug=slug, project_id=project_id, is_enabled=True
    ).exists()


class _PropertiesBaseView(BaseAPIView):
    """Shared flag guards for the custom-properties data endpoints."""

    def require_instance(self):
        # Instance kill switch off -> hard 403 on data endpoints (§8).
        if not is_instance_enabled():
            return Response(
                {"error": "custom properties disabled"}, status=status.HTTP_403_FORBIDDEN
            )
        return None

    def require_enabled(self, slug, project_id):
        # Writes require instance on AND the per-project toggle on.
        guard = self.require_instance()
        if guard:
            return guard
        if not project_feature_enabled(slug, project_id):
            return Response(
                {"error": "Custom properties are not enabled for this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None


class CustomPropertiesHealthEndpoint(APIView):
    """Report whether the instance kill switch is on. No auth: reveals a single
    boolean capability flag and nothing project-specific."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"instance_enabled": is_instance_enabled()})


class ProjectPropertiesFeatureEndpoint(BaseAPIView):
    """Read / toggle the per-project custom-properties switch.

    GET is a capability probe: it always answers, reporting the stored toggle
    masked by the instance kill switch (so the client sees ``is_enabled: false``
    whenever the operator has the feature off). PATCH requires the instance
    switch on *and* a project admin.
    """

    def _payload(self, feature):
        instance = is_instance_enabled()
        return {
            "is_enabled": bool(feature and feature.is_enabled) and instance,
            "instance_enabled": instance,
        }

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id):
        feature = ProjectPropertiesFeature.objects.filter(
            workspace__slug=slug, project_id=project_id
        ).first()
        return Response(self._payload(feature), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def patch(self, request, slug, project_id):
        if not is_instance_enabled():
            return Response(
                {"error": "custom properties disabled"}, status=status.HTTP_403_FORBIDDEN
            )
        if not is_project_admin(slug, project_id, request.user):
            return Response(
                {"error": "Only project admins can toggle custom properties."},
                status=status.HTTP_403_FORBIDDEN,
            )
        project = Project.objects.get(pk=project_id)
        feature, _ = ProjectPropertiesFeature.objects.get_or_create(
            project_id=project_id,
            defaults={"workspace_id": project.workspace_id},
        )
        if "is_enabled" in request.data:
            feature.is_enabled = bool(request.data.get("is_enabled"))
            feature.save()
        return Response(self._payload(feature), status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Property definitions (§4.1)
# ---------------------------------------------------------------------------

# Fields a client may set on create/update (machine `name` is derived, not set).
_PROPERTY_WRITE_FIELDS = [
    "description",
    "is_required",
    "is_active",
    "is_multi",
    "default_value",
    "settings",
    "validation_rules",
    "relation_type",
    "logo_props",
    "sort_order",
    "external_source",
    "external_id",
]


class IssuePropertiesEndpoint(_PropertiesBaseView):
    def _next_sort_order(self, slug, project_id):
        last = (
            IssueProperty.objects.filter(workspace__slug=slug, project_id=project_id)
            .order_by("-sort_order")
            .first()
        )
        return (last.sort_order + 1000) if last else 1000

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id):
        # Disabled (instance or project) -> empty list, so the UI renders nothing.
        if not project_feature_enabled(slug, project_id):
            return Response([], status=status.HTTP_200_OK)
        props = (
            IssueProperty.objects.filter(
                workspace__slug=slug, project_id=project_id, is_active=True
            )
            .prefetch_related("options")
        )
        return Response(IssuePropertySerializer(props, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        display_name = (request.data.get("display_name") or "").strip()
        if not display_name:
            return Response({"error": "display_name is required"}, status=status.HTTP_400_BAD_REQUEST)
        # v1: only Status (OPTION) columns are supported.
        if request.data.get("property_type") != PropertyTypeEnum.OPTION:
            return Response({"error": "property_type not enabled"}, status=status.HTTP_400_BAD_REQUEST)

        project = Project.objects.get(pk=project_id)
        prop = IssueProperty(
            workspace_id=project.workspace_id,
            project_id=project_id,
            display_name=display_name,
            name=slugify(display_name) or display_name,
            property_type=PropertyTypeEnum.OPTION,
            sort_order=self._next_sort_order(slug, project_id),
        )
        _apply_property_fields(prop, request.data)
        prop.save()

        for idx, opt in enumerate(request.data.get("options", []) or []):
            IssuePropertyOption.objects.create(
                workspace_id=project.workspace_id,
                project_id=project_id,
                property=prop,
                name=(opt.get("name") or "").strip(),
                description=opt.get("description", ""),
                logo_props=opt.get("logo_props", {}) or {},
                is_default=bool(opt.get("is_default", False)),
                is_active=bool(opt.get("is_active", True)),
                sort_order=opt.get("sort_order", (idx + 1) * 1000),
            )
        prop = IssueProperty.objects.prefetch_related("options").get(pk=prop.pk)
        return Response(IssuePropertySerializer(prop).data, status=status.HTTP_201_CREATED)


class IssuePropertyDetailEndpoint(_PropertiesBaseView):
    def _get(self, slug, project_id, property_id):
        return (
            IssueProperty.objects.filter(
                workspace__slug=slug, project_id=project_id, pk=property_id
            )
            .prefetch_related("options")
            .first()
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, property_id):
        guard = self.require_instance()
        if guard:
            return guard
        prop = self._get(slug, project_id, property_id)
        if not prop:
            return Response({"error": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(IssuePropertySerializer(prop).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id, property_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        prop = self._get(slug, project_id, property_id)
        if not prop:
            return Response({"error": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        if "display_name" in request.data:
            display_name = (request.data.get("display_name") or "").strip()
            if not display_name:
                return Response({"error": "display_name is required"}, status=status.HTTP_400_BAD_REQUEST)
            prop.display_name = display_name
            prop.name = slugify(display_name) or display_name
        _apply_property_fields(prop, request.data)
        prop.save()
        prop = IssueProperty.objects.prefetch_related("options").get(pk=prop.pk)
        return Response(IssuePropertySerializer(prop).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, property_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        prop = self._get(slug, project_id, property_id)
        if not prop:
            return Response({"error": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        # Explicit synchronous soft-delete cascade (the model's own cascade runs
        # via an async Celery task; do it inline so the UI reflects it at once).
        IssuePropertyValue.objects.filter(property=prop).delete()
        IssuePropertyOption.objects.filter(property=prop).delete()
        prop.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _apply_property_fields(prop, data):
    for field in _PROPERTY_WRITE_FIELDS:
        if field in data:
            setattr(prop, field, data[field])


# ---------------------------------------------------------------------------
# Options (§4.2)
# ---------------------------------------------------------------------------


class IssuePropertyOptionsEndpoint(_PropertiesBaseView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, property_id):
        guard = self.require_instance()
        if guard:
            return guard
        opts = IssuePropertyOption.objects.filter(
            project_id=project_id, property_id=property_id
        )
        return Response(IssuePropertyOptionSerializer(opts, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id, property_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        prop = IssueProperty.objects.filter(
            workspace__slug=slug, project_id=project_id, pk=property_id
        ).first()
        if not prop:
            return Response({"error": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        project = Project.objects.get(pk=project_id)
        opt = IssuePropertyOption.objects.create(
            workspace_id=project.workspace_id,
            project_id=project_id,
            property=prop,
            name=(request.data.get("name") or "").strip(),
            description=request.data.get("description", ""),
            logo_props=request.data.get("logo_props", {}) or {},
            is_default=bool(request.data.get("is_default", False)),
            is_active=bool(request.data.get("is_active", True)),
            sort_order=request.data.get("sort_order", 65535),
            parent_id=request.data.get("parent"),
            external_source=request.data.get("external_source"),
            external_id=request.data.get("external_id"),
        )
        return Response(IssuePropertyOptionSerializer(opt).data, status=status.HTTP_201_CREATED)


class IssuePropertyOptionDetailEndpoint(_PropertiesBaseView):
    _WRITE_FIELDS = ["name", "description", "logo_props", "is_default", "is_active", "sort_order"]

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id, property_id, option_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        opt = IssuePropertyOption.objects.filter(
            project_id=project_id, property_id=property_id, pk=option_id
        ).first()
        if not opt:
            return Response({"error": "Option not found."}, status=status.HTTP_404_NOT_FOUND)
        for field in self._WRITE_FIELDS:
            if field in request.data:
                setattr(opt, field, request.data[field])
        opt.save()
        return Response(IssuePropertyOptionSerializer(opt).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, property_id, option_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        opt = IssuePropertyOption.objects.filter(
            project_id=project_id, property_id=property_id, pk=option_id
        ).first()
        if not opt:
            return Response({"error": "Option not found."}, status=status.HTTP_404_NOT_FOUND)
        # Soft-delete value rows that referenced this option, then the option.
        IssuePropertyValue.objects.filter(value_option=opt).delete()
        opt.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Values (§4.3)
# ---------------------------------------------------------------------------


class IssuePropertyValuesEndpoint(_PropertiesBaseView):
    # Design decision (§9 task 2.6): a property's ``is_default`` option is applied
    # by the create/edit modal on the frontend, NOT the backend. Existing issues
    # are never back-filled — an issue simply has no value row for a property
    # until a user (or the modal) sets one, and GET returns an empty list for it.
    # This keeps the API explicit (no hidden writes) and avoids a bulk backfill
    # when a default is added to a property that already has thousands of issues.
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, work_item_id, property_id):
        guard = self.require_instance()
        if guard:
            return guard
        option_ids = list(
            IssuePropertyValue.objects.filter(
                project_id=project_id, issue_id=work_item_id, property_id=property_id
            )
            .exclude(value_option__isnull=True)
            .values_list("value_option_id", flat=True)
        )
        return Response({"values": [str(x) for x in option_ids]}, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, work_item_id, property_id):
        guard = self.require_enabled(slug, project_id)
        if guard:
            return guard
        prop = IssueProperty.objects.filter(
            workspace__slug=slug, project_id=project_id, pk=property_id
        ).first()
        if not prop:
            return Response({"error": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        if not Issue.objects.filter(project_id=project_id, pk=work_item_id).exists():
            return Response({"error": "Work item not found."}, status=status.HTTP_404_NOT_FOUND)
        if prop.property_type != PropertyTypeEnum.OPTION:
            return Response(
                {"error": "Only OPTION values are supported in v1."}, status=status.HTTP_400_BAD_REQUEST
            )
        values = request.data.get("values", []) or []
        if not prop.is_multi and len(values) > 1:
            return Response(
                {"error": "This property is single-select."}, status=status.HTTP_400_BAD_REQUEST
            )
        valid_ids = {
            str(x) for x in IssuePropertyOption.objects.filter(property=prop).values_list("id", flat=True)
        }
        for value in values:
            if str(value) not in valid_ids:
                return Response(
                    {"error": f"Option {value} does not belong to this property."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        project = Project.objects.get(pk=project_id)
        # Replace semantics: soft-delete existing live rows, recreate.
        IssuePropertyValue.objects.filter(
            project_id=project_id, issue_id=work_item_id, property=prop
        ).delete()
        created = []
        for value in values:
            row = IssuePropertyValue.objects.create(
                workspace_id=project.workspace_id,
                project_id=project_id,
                issue_id=work_item_id,
                property=prop,
                value_option_id=value,
            )
            created.append(str(row.value_option_id))
        return Response({"values": created}, status=status.HTTP_200_OK)


class BulkIssuePropertyValuesEndpoint(_PropertiesBaseView):
    """Board/spreadsheet hydration: values for many issues in one query.

    Response: {"<issue_id>": {"<property_id>": ["<option_id>", ...]}}.
    Capped at 100 work-item ids; the frontend batches larger sets.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id):
        if not project_feature_enabled(slug, project_id):
            return Response({}, status=status.HTTP_200_OK)
        raw = request.query_params.get("work_item_ids", "")
        ids = [x for x in raw.split(",") if x][:100]
        if not ids:
            return Response({}, status=status.HTTP_200_OK)
        rows = (
            IssuePropertyValue.objects.filter(project_id=project_id, issue_id__in=ids)
            .exclude(value_option__isnull=True)
            .values_list("issue_id", "property_id", "value_option_id")
        )
        result = {}
        for issue_id, property_id, option_id in rows:
            result.setdefault(str(issue_id), {}).setdefault(str(property_id), []).append(str(option_id))
        return Response(result, status=status.HTTP_200_OK)
