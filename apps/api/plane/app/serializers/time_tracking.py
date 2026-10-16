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
    TimeTrackingMemberStatus,
    TimeTrackingSetting,
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
            # FORK: time-tracking-v2 — set by the server only.
            "issue_ref",
            "issue_name",
            "session_id",
            "auto_stopped",
            "idle_stopped",
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
            # FORK: time-tracking-v2 — set by the server only (S2 reminder / still-working check).
            "reminded_at",
            "last_confirmed_at",
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


class TimeTrackingSettingSerializer(BaseSerializer):
    """FORK: time-tracking-v2 — workspace time-tracking settings."""

    timezone = serializers.CharField(source="workspace.timezone", read_only=True)

    class Meta:
        model = TimeTrackingSetting
        fields = [
            "id",
            "workspace",
            "timezone",
            "work_days",
            "minutes_per_day",
            "remind_after_minutes",
            "auto_stop_after_minutes",
            "idle_check_minutes",
            "allow_member_manual_entry",
            "weekly_approval_enabled",
            "billing_enabled",
            "updated_at",
        ]
        read_only_fields = ["id", "workspace", "timezone", "updated_at"]

    def validate_work_days(self, value):
        if not value:
            raise serializers.ValidationError("Pick at least one work day.")
        if len(set(value)) != len(value) or any(d < 1 or d > 7 for d in value):
            raise serializers.ValidationError("Work days are distinct ISO weekdays, 1 (Mon) to 7 (Sun).")
        return sorted(value)

    def validate_minutes_per_day(self, value):
        if not 1 <= value <= 1440:
            raise serializers.ValidationError("Must be between 1 and 1440 minutes.")
        return value

    def validate_idle_check_minutes(self, value):
        if value > 1440:
            raise serializers.ValidationError("Must be between 0 (off) and 1440 minutes.")
        return value

    def validate(self, attrs):
        remind = attrs.get("remind_after_minutes", getattr(self.instance, "remind_after_minutes", None))
        auto_stop = attrs.get("auto_stop_after_minutes", getattr(self.instance, "auto_stop_after_minutes", None))
        for name, value in (("remind_after_minutes", remind), ("auto_stop_after_minutes", auto_stop)):
            if value is not None and not 1 <= value <= 1440:
                raise serializers.ValidationError({name: "Must be between 1 and 1440 minutes."})
        if remind is not None and auto_stop is not None and auto_stop <= remind:
            raise serializers.ValidationError(
                {"auto_stop_after_minutes": "Auto-stop must come after the reminder."}
            )
        return attrs


class TimeTrackingMemberStatusSerializer(serializers.ModelSerializer):
    """FORK: time-tracking-v2 — one row of a member's tracking on/off history."""

    class Meta:
        model = TimeTrackingMemberStatus
        fields = ["id", "workspace", "user", "is_tracked", "effective_from", "changed_by", "created_at"]
        read_only_fields = fields
