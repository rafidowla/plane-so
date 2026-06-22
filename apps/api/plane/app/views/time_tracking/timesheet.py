# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseViewSet
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import TimesheetSerializer
from plane.db.models import (
    WorkspaceMember,
    Timesheet,
    IssueWorklog,
    TimesheetStatus,
    WorklogApprovalStatus,
)


def is_workspace_admin(slug, user):
    return WorkspaceMember.objects.filter(
        workspace__slug=slug, member=user, role=ROLE.ADMIN.value, is_active=True
    ).exists()


class TimesheetViewSet(BaseViewSet):
    """Per-resource weekly timesheets with a submit -> approve/reject lifecycle."""

    serializer_class = TimesheetSerializer
    model = Timesheet

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("user", "reviewed_by")
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        queryset = self.get_queryset()
        # Non-admins only see their own timesheets.
        if not is_workspace_admin(slug, request.user):
            queryset = queryset.filter(user=request.user)
        else:
            user_id = request.GET.get("user_id")
            status_filter = request.GET.get("status")
            if user_id:
                queryset = queryset.filter(user_id=user_id)
            if status_filter:
                queryset = queryset.filter(status=status_filter)
        return Response(TimesheetSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def submit(self, request, slug):
        """Submit a period for approval: bundle the user's worklogs in range."""
        from plane.db.models import Workspace

        period_start = request.data.get("period_start")
        period_end = request.data.get("period_end")
        if not period_start or not period_end:
            return Response(
                {"error": "period_start and period_end are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = Workspace.objects.get(slug=slug)
        timesheet, _ = Timesheet.objects.get_or_create(
            workspace_id=workspace.id,
            user=request.user,
            period_start=period_start,
            defaults={"period_end": period_end},
        )
        if timesheet.status == TimesheetStatus.APPROVED:
            return Response(
                {"error": "This period is already approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        worklogs = IssueWorklog.objects.filter(
            workspace_id=workspace.id,
            logged_by=request.user,
            logged_date__gte=period_start,
            logged_date__lte=period_end,
            is_locked=False,
        )
        worklogs.update(timesheet=timesheet, approval_status=WorklogApprovalStatus.SUBMITTED)
        timesheet.period_end = period_end
        timesheet.status = TimesheetStatus.SUBMITTED
        timesheet.submitted_at = timezone.now()
        timesheet.save()
        return Response(TimesheetSerializer(timesheet).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def review(self, request, slug, pk):
        """Approve or reject a submitted timesheet (PM/admin only)."""
        action = request.data.get("action")
        note = request.data.get("note", "")
        if action not in ("approve", "reject"):
            return Response(
                {"error": "action must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        timesheet = self.get_queryset().get(pk=pk)
        worklogs = IssueWorklog.objects.filter(timesheet_id=timesheet.id)
        if action == "approve":
            timesheet.status = TimesheetStatus.APPROVED
            worklogs.update(
                approval_status=WorklogApprovalStatus.APPROVED,
                is_locked=True,
                approved_by=request.user,
                approved_at=timezone.now(),
            )
        else:
            timesheet.status = TimesheetStatus.REJECTED
            worklogs.update(approval_status=WorklogApprovalStatus.REJECTED, is_locked=False)
        timesheet.reviewed_by = request.user
        timesheet.reviewed_at = timezone.now()
        timesheet.review_note = note
        timesheet.save()
        return Response(TimesheetSerializer(timesheet).data, status=status.HTTP_200_OK)
