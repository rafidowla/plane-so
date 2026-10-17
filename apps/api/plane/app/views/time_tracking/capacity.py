# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ResourceCapacitySerializer
from plane.db.models import (
    ResourceCapacity,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.limits import read_settings
from plane.utils.time_tracking.reporting import default_weekly_minutes

# FORK: time-tracking-v2 S5 — rate fields exist (in and out) only while billing is on.
RATE_FIELDS = ("cost_rate", "billable_rate", "currency")
# A week has 10080 minutes; anything above it is a typo, not a capacity.
MAX_WEEKLY_MINUTES = 7 * 24 * 60


def _refuse(code, error, status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": error, "code": code}, status=status_code)


def _weekly_minutes(value):
    """(minutes or None, error) for a weekly capacity override (None clears it)."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_WEEKLY_MINUTES:
        return None, f"weekly capacity must be a whole number of minutes from 0 to {MAX_WEEKLY_MINUTES}, or null."
    return value, None


def _is_internal_member(workspace_id, user_id):
    return WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
    ).exists()


def _capacity_state(row):
    return {"user_id": str(row.user_id), "weekly_capacity": row.weekly_capacity} if row else None


class ResourceCapacityViewSet(BaseViewSet):
    """Per-resource weekly capacity and default rates (workspace-scoped).

    FORK: time-tracking-v2 S5 — ``weekly_capacity`` is now the nullable per-person
    override (null = the workspace work week). ``cost_rate``/``billable_rate``/
    ``currency`` are omitted from responses, and ignored on upsert, while the
    workspace's ``billing_enabled`` setting is off.
    """

    serializer_class = ResourceCapacitySerializer
    model = ResourceCapacity

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("user")
            .order_by("user__display_name", "id")
        )

    @staticmethod
    def _out(data, billing):
        if not billing:
            for field in RATE_FIELDS:
                data.pop(field, None)
        return data

    # Resource capacity rows carry internal cost_rate (what the company pays a
    # resource) and billable_rate for every user in the workspace. That is
    # ADMIN-only planning data; list access is restricted accordingly (upsert is
    # already ADMIN-only below).
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def list(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        billing = bool(read_settings(workspace.id).billing_enabled)
        # FORK: time-tracking-v2 S5 — bounded (one row per workspace member at most).
        rows = self.get_queryset()[:1000]
        return Response(
            [self._out(dict(row), billing) for row in ResourceCapacitySerializer(rows, many=True).data],
            status=status.HTTP_200_OK,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def upsert(self, request, slug):
        """Create or update the capacity row for a given user."""
        user_id = request.data.get("user")
        if not user_id:
            return Response({"error": "user is required."}, status=status.HTTP_400_BAD_REQUEST)
        workspace = Workspace.objects.get(slug=slug)
        # FORK: time-tracking-v2 S5 — IDOR: only this workspace's members get rows.
        try:
            is_member = _is_internal_member(workspace.id, user_id)
        except (DjangoValidationError, ValueError, TypeError):
            is_member = False
        if not is_member:
            return _refuse("invalid_user", "user must be an active member of this workspace.")
        billing = bool(read_settings(workspace.id).billing_enabled)
        data = {k: v for k, v in request.data.items() if k not in ("workspace",)}
        if not billing:
            for field in RATE_FIELDS:
                data.pop(field, None)
        if "weekly_capacity" in data:
            _minutes, error = _weekly_minutes(data["weekly_capacity"])
            if error:
                return _refuse("invalid_capacity", error)
        with transaction.atomic():
            capacity, _ = ResourceCapacity.objects.get_or_create(workspace_id=workspace.id, user_id=user_id)
            before = _capacity_state(capacity)
            serializer = ResourceCapacitySerializer(capacity, data=data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            capacity = serializer.save()
            after = _capacity_state(capacity)
            if before != after:
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.SETTING,
                    capacity.id,
                    TimeTrackingAuditAction.UPDATE,
                    before=before,
                    after=after,
                )
        return Response(self._out(dict(serializer.data), billing), status=status.HTTP_200_OK)


class TimeTrackingMemberCapacityEndpoint(BaseAPIView):
    """FORK: time-tracking-v2 S5 — ``PUT time/members/<user_id>/capacity/``
    ``{"weekly_minutes": int | null}`` (workspace admins).

    Sets a person's weekly capacity override: Reports spread it evenly over the
    workspace's working days (20 h/wk on Mon–Fri = 4 h/day). ``null`` clears it, so
    the person follows the work week again. Audited (target ``setting``, before/after
    ``{user_id, weekly_capacity}``)."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, user_id):
        workspace = Workspace.objects.get(slug=slug)
        if not _is_internal_member(workspace.id, user_id):
            return _refuse("invalid_user", "Capacity applies to active workspace members and admins only.")
        if "weekly_minutes" not in request.data:
            return _refuse("invalid_capacity", "weekly_minutes is required (a number of minutes, or null).")
        weekly, error = _weekly_minutes(request.data.get("weekly_minutes"))
        if error:
            return _refuse("invalid_capacity", error)
        with transaction.atomic():
            row = (
                ResourceCapacity.objects.select_for_update().filter(workspace_id=workspace.id, user_id=user_id).first()
            )
            before = _capacity_state(row)
            if row is None and weekly is None:
                after = None
            else:
                if row is None:
                    row = ResourceCapacity(workspace_id=workspace.id, user_id=user_id)
                row.weekly_capacity = weekly
                row.save()
                after = _capacity_state(row)
            if before != after and (before or {}).get("weekly_capacity") != (after or {}).get("weekly_capacity"):
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.SETTING,
                    row.id,
                    TimeTrackingAuditAction.UPDATE,
                    before=before,
                    after=after,
                )
        setting = read_settings(workspace.id)
        return Response(
            {
                "user_id": str(user_id),
                "weekly_capacity_minutes": weekly,
                "default_weekly_minutes": default_weekly_minutes(setting),
            },
            status=status.HTTP_200_OK,
        )
