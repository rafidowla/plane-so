# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties serializers.

Read serializers only — writes are handled explicitly in the views (nested
options create, replace-semantics values, per-type validation) rather than
through serializer `.create()`/`.update()`, which keeps the validation branching
readable. See docs/custom-properties-design.md §4.
"""

from plane.app.serializers.base import BaseSerializer

from plane.properties.models import IssueProperty, IssuePropertyOption


class IssuePropertyOptionSerializer(BaseSerializer):
    class Meta:
        model = IssuePropertyOption
        fields = [
            "id",
            "property",
            "name",
            "description",
            "logo_props",
            "sort_order",
            "is_active",
            "is_default",
            "parent",
            "external_source",
            "external_id",
        ]
        read_only_fields = ["id", "property"]


class IssuePropertySerializer(BaseSerializer):
    # Related manager uses the soft-delete manager, so only live options embed.
    options = IssuePropertyOptionSerializer(many=True, read_only=True)

    class Meta:
        model = IssueProperty
        fields = [
            "id",
            "workspace",
            "project",
            "issue_type",
            "name",
            "display_name",
            "description",
            "property_type",
            "relation_type",
            "is_required",
            "default_value",
            "settings",
            "is_active",
            "is_multi",
            "validation_rules",
            "logo_props",
            "sort_order",
            "external_source",
            "external_id",
            "options",
        ]
        read_only_fields = ["id", "workspace", "project", "options"]
