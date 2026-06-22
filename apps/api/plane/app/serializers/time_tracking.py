# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from .base import BaseSerializer
from .user import UserLiteSerializer
from plane.db.models import (
    Client,
    IssueWorklog,
    WorklogTimer,
    Timesheet,
    ResourceCapacity,
)


class ClientSerializer(BaseSerializer):
    project_count = serializers.IntegerField(read_only=True)
    project_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True)

    class Meta:
        model = Client
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]


class IssueWorklogSerializer(BaseSerializer):
    logged_by_detail = UserLiteSerializer(read_only=True, source="logged_by")
    created_by_detail = UserLiteSerializer(read_only=True, source="created_by")
    approved_by_detail = UserLiteSerializer(read_only=True, source="approved_by")

    class Meta:
        model = IssueWorklog
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "project",
            "issue",
            "logged_by",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "approved_by",
            "approved_at",
            "is_locked",
            "approval_status",
            "timesheet",
        ]


class WorklogTimerSerializer(BaseSerializer):
    user_detail = UserLiteSerializer(read_only=True, source="user")

    class Meta:
        model = WorklogTimer
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "project",
            "issue",
            "user",
            "started_at",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]


class TimesheetSerializer(BaseSerializer):
    user_detail = UserLiteSerializer(read_only=True, source="user")
    reviewed_by_detail = UserLiteSerializer(read_only=True, source="reviewed_by")

    class Meta:
        model = Timesheet
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "user",
            "status",
            "submitted_at",
            "reviewed_by",
            "reviewed_at",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]


class ResourceCapacitySerializer(BaseSerializer):
    user_detail = UserLiteSerializer(read_only=True, source="user")

    class Meta:
        model = ResourceCapacity
        fields = "__all__"
        read_only_fields = [
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]
