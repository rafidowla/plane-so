# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Module imports
from .base import BaseSerializer
from .user import UserLiteSerializer
from plane.db.models import JiraImportJob


class JiraImportJobSerializer(BaseSerializer):
    initiated_by_detail = UserLiteSerializer(read_only=True, source="initiated_by")

    class Meta:
        model = JiraImportJob
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "project",
            "initiated_by",
            "status",
            "total",
            "processed",
            "result",
            "error",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
