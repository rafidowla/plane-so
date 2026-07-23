# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-dashboards serializers.

Phase 2 ships CRUD for ``WorkspaceDashboardWidget`` only. Per-widget-type data
fetching (chart data, issue lists) is Phase 3 and does not live here. See
docs/custom-dashboards-design.md.
"""

import uuid

from rest_framework import serializers

from plane.app.serializers.base import BaseSerializer

from plane.dashboards.models import WorkspaceDashboardWidget

WidgetType = WorkspaceDashboardWidget.WidgetType

_LOOKBACK_DAYS_CHOICES = (30, 60, 90)
_PAGE_SIZE_CHOICES = (10, 25, 50)
_GROUP_BY_CHOICES = ("state", "state_group", "priority")

# Config keys each widget_type accepts. Anything outside this set is rejected.
_ALLOWED_CONFIG_KEYS = {
    WidgetType.DISTRIBUTION_PIE: {"group_by", "project_ids"},
    WidgetType.PROJECT_BREAKDOWN_PIE: {"project_ids"},
    WidgetType.AGE_TREND: {"project_ids", "lookback_days"},
    WidgetType.VIEW_LIST: {"page_size", "project_ids"},
}


def _validate_project_ids(project_ids):
    if project_ids is None:
        return
    if not isinstance(project_ids, list):
        raise serializers.ValidationError("config.project_ids must be a list of UUID strings.")
    for project_id in project_ids:
        try:
            uuid.UUID(str(project_id))
        except (ValueError, AttributeError, TypeError):
            raise serializers.ValidationError(
                f"config.project_ids contains an invalid UUID: {project_id!r}"
            )


class WorkspaceDashboardWidgetSerializer(BaseSerializer):
    class Meta:
        model = WorkspaceDashboardWidget
        fields = [
            "id",
            "workspace",
            "widget_type",
            "title",
            "is_enabled",
            "sort_order",
            "issue_view",
            "config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "workspace", "created_at", "updated_at"]

    def validate(self, attrs):
        widget_type = attrs.get("widget_type", getattr(self.instance, "widget_type", None))
        config = attrs.get("config", getattr(self.instance, "config", None) or {})
        issue_view = (
            attrs["issue_view"] if "issue_view" in attrs else getattr(self.instance, "issue_view", None)
        )

        if not isinstance(config, dict):
            raise serializers.ValidationError("config must be an object.")

        allowed_keys = _ALLOWED_CONFIG_KEYS.get(widget_type)
        if allowed_keys is None:
            raise serializers.ValidationError(f"Unknown widget_type: {widget_type!r}")

        unknown_keys = set(config.keys()) - allowed_keys
        if unknown_keys:
            raise serializers.ValidationError(
                f"config contains unsupported key(s) for widget_type "
                f"{widget_type!r}: {', '.join(sorted(unknown_keys))}"
            )

        if widget_type == WidgetType.DISTRIBUTION_PIE:
            group_by = config.get("group_by")
            if group_by is not None and group_by not in _GROUP_BY_CHOICES:
                raise serializers.ValidationError(
                    f"config.group_by must be one of {_GROUP_BY_CHOICES}."
                )
            _validate_project_ids(config.get("project_ids"))
            if issue_view is not None:
                raise serializers.ValidationError(
                    "issue_view must not be set for distribution_pie widgets."
                )

        elif widget_type == WidgetType.PROJECT_BREAKDOWN_PIE:
            _validate_project_ids(config.get("project_ids"))
            if issue_view is not None:
                raise serializers.ValidationError(
                    "issue_view must not be set for project_breakdown_pie widgets."
                )

        elif widget_type == WidgetType.AGE_TREND:
            lookback_days = config.get("lookback_days", 30)
            if lookback_days not in _LOOKBACK_DAYS_CHOICES:
                raise serializers.ValidationError(
                    f"config.lookback_days must be one of {_LOOKBACK_DAYS_CHOICES}."
                )
            _validate_project_ids(config.get("project_ids"))
            if issue_view is not None:
                raise serializers.ValidationError(
                    "issue_view must not be set for age_trend widgets."
                )

        elif widget_type == WidgetType.VIEW_LIST:
            page_size = config.get("page_size", 10)
            if page_size not in _PAGE_SIZE_CHOICES:
                raise serializers.ValidationError(
                    f"config.page_size must be one of {_PAGE_SIZE_CHOICES}."
                )
            _validate_project_ids(config.get("project_ids"))
            if issue_view is None:
                raise serializers.ValidationError("issue_view is required for view_list widgets.")
            workspace = self.context.get("workspace")
            if workspace is not None and issue_view.workspace_id != workspace.id:
                raise serializers.ValidationError(
                    "issue_view must belong to the same workspace as the widget."
                )

        return attrs
