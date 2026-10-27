# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — workspace time-tracking settings and member tracking status."""

# Python imports
from datetime import date

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission, allow_workspace_super_admin  # FORK: PSR-125
from plane.app.serializers import (
    IssueWorklogSerializer,
    TimeTrackingMemberStatusSerializer,
    TimeTrackingSettingSerializer,
)
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    MemberHolidayCalendar,
    ResourceCapacity,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    TimeTrackingMemberStatus,
    TimeTrackingSetting,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import audit, billing
from plane.utils.time_tracking.limits import read_settings
from plane.utils.time_tracking.reporting import default_weekly_minutes
from plane.utils.time_tracking.tracking import get_settings, is_tracked, set_member_tracking
from plane.utils.time_tracking.tz import local_today, zone

# Fields a non-admin member does not need to see.
ADMIN_ONLY_SETTING_FIELDS = ("billing_enabled", "updated_at")


class TimeTrackingSettingsEndpoint(BaseAPIView):
    """``GET`` (workspace members/admins) and ``PATCH`` (workspace admins) the settings."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        data = TimeTrackingSettingSerializer(get_settings(workspace.id)).data
        if not WorkspaceMember.objects.filter(
            workspace=workspace, member=request.user, role=ROLE.ADMIN.value, is_active=True
        ).exists():
            for field in ADMIN_ONLY_SETTING_FIELDS:
                data.pop(field, None)
        return Response(data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    @allow_workspace_super_admin  # FORK: PSR-125
    def patch(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        with transaction.atomic():
            setting = get_settings(workspace.id)
            setting = TimeTrackingSetting.all_objects.select_for_update().get(pk=setting.pk)
            before = audit.setting_state(setting)
            serializer = TimeTrackingSettingSerializer(setting, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            setting = serializer.save()
            after = audit.setting_state(setting)
            if after != before:
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.SETTING,
                    setting.id,
                    TimeTrackingAuditAction.UPDATE,
                    before=before,
                    after=after,
                )
        return Response(TimeTrackingSettingSerializer(setting).data, status=status.HTTP_200_OK)


class TimeTrackingMembersEndpoint(BaseAPIView):
    """Workspace members/admins with their tracking status today and their history."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        today = local_today(zone(workspace.timezone))
        members = (
            WorkspaceMember.objects.filter(workspace=workspace, is_active=True, role__gte=ROLE.MEMBER.value)
            .select_related("member")
            .order_by("member__display_name")
        )
        history = {}
        for row in TimeTrackingMemberStatus.objects.filter(workspace=workspace).order_by(
            "-effective_from", "-created_at"
        ):
            history.setdefault(row.user_id, []).append(TimeTrackingMemberStatusSerializer(row).data)
        # FORK: time-tracking-v2 S5 — per-person weekly capacity override (null =
        # the workspace work week, ``default_weekly_minutes``).
        capacities = dict(
            ResourceCapacity.objects.filter(workspace=workspace, weekly_capacity__isnull=False).values_list(
                "user_id", "weekly_capacity"
            )
        )
        # FORK: time-tracking-v2 holidays/leave (PSR-122) — the ASSIGNED calendar (absent = default).
        calendars = dict(
            MemberHolidayCalendar.objects.filter(workspace=workspace, calendar__deleted_at__isnull=True).values_list(
                "user_id", "calendar_id"
            )
        )
        data = [
            {
                "user_id": str(m.member_id),
                "display_name": m.member.display_name,
                "role": m.role,
                "is_tracked": is_tracked(workspace.id, m.member_id, today),
                "history": history.get(m.member_id, []),
                "weekly_capacity_minutes": capacities.get(m.member_id),
                "holiday_calendar_id": None if m.member_id not in calendars else str(calendars[m.member_id]),
            }
            for m in members
        ]
        return Response(
            {
                "date": today,
                "default_weekly_minutes": default_weekly_minutes(read_settings(workspace.id)),
                "members": data,
            },
            status=status.HTTP_200_OK,
        )


class TimeTrackingMemberStatusEndpoint(BaseAPIView):
    """Turn a member's tracking on/off from a date. Turning it off for today or
    earlier stops their running timer now."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, user_id):
        workspace = Workspace.objects.get(slug=slug)
        if not WorkspaceMember.objects.filter(
            workspace=workspace, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
        ).exists():
            return Response(
                {"error": "Tracking applies to active workspace members and admins only."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tracked = request.data.get("is_tracked")
        if not isinstance(tracked, bool):
            return Response({"error": "is_tracked must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
        today = local_today(zone(workspace.timezone))
        raw_date = request.data.get("effective_from")
        try:
            effective_from = date.fromisoformat(raw_date) if raw_date else today
        except (TypeError, ValueError):
            return Response(
                {"error": "effective_from must be a date (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST
            )
        row, stopped = set_member_tracking(workspace.id, user_id, tracked, effective_from, request.user)
        return Response(
            {
                "status": TimeTrackingMemberStatusSerializer(row).data,
                "is_tracked_today": is_tracked(workspace.id, user_id, today),
                "stopped": IssueWorklogSerializer(
                    stopped, many=True, context=billing.serializer_context(request.user)
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )
