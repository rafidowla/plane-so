# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import math

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseViewSet, BaseAPIView
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import IssueWorklogSerializer, WorklogTimerSerializer
from plane.db.models import (
    Project,
    ProjectMember,
    IssueWorklog,
    WorklogTimer,
    ResourceCapacity,
    WorklogApprovalStatus,
    WorklogSource,
)


def is_project_admin(slug, project_id, user):
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


def is_project_member(slug, project_id, user):
    """Internal roles (admin/member). Guests are clients and excluded."""
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
        is_active=True,
    ).exists()


def resolve_billable_rate(project, logged_by_id):
    """Default billable rate: resource rate, else the project's client default."""
    capacity = ResourceCapacity.objects.filter(
        workspace_id=project.workspace_id, user_id=logged_by_id
    ).first()
    if capacity and capacity.billable_rate is not None:
        return capacity.billable_rate, capacity.currency
    if project.client_id and project.client and project.client.default_billable_rate is not None:
        return project.client.default_billable_rate, project.client.currency
    return None, "USD"


class IssueWorklogViewSet(BaseViewSet):
    """Time entries logged against an issue.

    Regular members log their own time. Project admins (PM) can log on behalf
    of any resource by passing `logged_by`. Locked/approved entries are
    read-only except for project admins.
    """

    serializer_class = IssueWorklogSerializer
    model = IssueWorklog

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .filter(project_id=self.kwargs.get("project_id"))
            .filter(issue_id=self.kwargs.get("issue_id"))
            .select_related("logged_by", "approved_by", "created_by")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def list(self, request, slug, project_id, issue_id):
        worklogs = self.get_queryset()
        # Internal roles (admin/member) get full detail. Guests (clients) get the
        # reported total only — never who logged the time or how (timer vs manual).
        if is_project_member(slug, project_id, request.user):
            return Response(IssueWorklogSerializer(worklogs, many=True).data, status=status.HTTP_200_OK)
        stripped = [
            {
                "id": str(w.id),
                "issue": str(w.issue_id),
                "duration": w.duration,
                "logged_date": w.logged_date,
                "is_billable": w.is_billable,
            }
            for w in worklogs
        ]
        return Response(stripped, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def create(self, request, slug, project_id, issue_id):
        project = Project.objects.get(pk=project_id)
        if not project.is_time_tracking_enabled:
            return Response(
                {"error": "Time tracking is not enabled for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Manual time entry is a PM/admin function. Regular members self-track via
        # the start/stop timer (source=timer); an admin entering time on their
        # behalf produces source=manual ("PM reported"). Keeping the two paths
        # distinct is what lets us tell self-reported from PM-reported time.
        if not is_project_admin(slug, project_id, request.user):
            return Response(
                {
                    "error": "Only project admins/PMs can enter time manually. "
                    "Members log their own time with the start/stop timer."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # The resource the time belongs to (defaults to the admin entering it).
        logged_by_id = request.data.get("logged_by") or request.user.id

        serializer = IssueWorklogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Snapshot the billable rate if billable and none supplied.
        is_billable = serializer.validated_data.get("is_billable", True)
        billable_rate = serializer.validated_data.get("billable_rate")
        currency = serializer.validated_data.get("currency", "USD")
        if is_billable and billable_rate is None:
            billable_rate, currency = resolve_billable_rate(project, logged_by_id)

        # Force source=manual: this endpoint is the manual/PM-reported path. The
        # timer endpoint is the only producer of source=timer, so the client
        # cannot relabel a manually entered record as self-tracked.
        worklog = serializer.save(
            project_id=project_id,
            issue_id=issue_id,
            logged_by_id=logged_by_id,
            billable_rate=billable_rate,
            currency=currency,
            source=WorklogSource.MANUAL,
        )
        return Response(IssueWorklogSerializer(worklog).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def partial_update(self, request, slug, project_id, issue_id, pk):
        worklog = self.get_queryset().get(pk=pk)
        admin = is_project_admin(slug, project_id, request.user)
        if worklog.is_locked and not admin:
            return Response(
                {"error": "This entry is locked and can no longer be edited."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if worklog.logged_by_id != request.user.id and worklog.created_by_id != request.user.id and not admin:
            return Response(
                {"error": "You can only edit your own time entries."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = IssueWorklogSerializer(worklog, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def destroy(self, request, slug, project_id, issue_id, pk):
        worklog = self.get_queryset().get(pk=pk)
        admin = is_project_admin(slug, project_id, request.user)
        if worklog.is_locked and not admin:
            return Response(
                {"error": "This entry is locked and can no longer be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if worklog.logged_by_id != request.user.id and worklog.created_by_id != request.user.id and not admin:
            return Response(
                {"error": "You can only delete your own time entries."},
                status=status.HTTP_403_FORBIDDEN,
            )
        worklog.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorklogTimerEndpoint(BaseAPIView):
    """Start/stop a stopwatch for the current user. One active timer per user."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id):
        timer = WorklogTimer.objects.filter(
            workspace__slug=slug, user=request.user
        ).first()
        if not timer:
            return Response({}, status=status.HTTP_200_OK)
        return Response(WorklogTimerSerializer(timer).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def post(self, request, slug, project_id, issue_id):
        project = Project.objects.get(pk=project_id)
        if not project.is_time_tracking_enabled:
            return Response(
                {"error": "Time tracking is not enabled for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Enforce a single running timer per user across the workspace.
        existing = WorklogTimer.objects.filter(workspace__slug=slug, user=request.user).first()
        if existing:
            return Response(
                {
                    "error": "You already have a running timer.",
                    "timer": WorklogTimerSerializer(existing).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        timer = WorklogTimer.objects.create(
            project_id=project_id,
            issue_id=issue_id,
            user=request.user,
            started_at=timezone.now(),
            description=request.data.get("description", ""),
        )
        return Response(WorklogTimerSerializer(timer).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def delete(self, request, slug, project_id, issue_id):
        """Stop the running timer and convert it to a worklog."""
        timer = WorklogTimer.objects.filter(
            workspace__slug=slug, user=request.user, issue_id=issue_id
        ).first()
        if not timer:
            return Response({"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND)

        project = Project.objects.get(pk=project_id)
        ended_at = timezone.now()
        minutes = max(1, math.floor((ended_at - timer.started_at).total_seconds() / 60))
        billable_rate, currency = resolve_billable_rate(project, request.user.id)

        worklog = IssueWorklog.objects.create(
            project_id=project_id,
            issue_id=issue_id,
            logged_by=request.user,
            duration=minutes,
            description=request.data.get("description", timer.description),
            logged_date=timezone.localdate(timer.started_at),
            started_at=timer.started_at,
            ended_at=ended_at,
            source=WorklogSource.TIMER,
            is_billable=True,
            billable_rate=billable_rate,
            currency=currency,
            approval_status=WorklogApprovalStatus.DRAFT,
        )
        timer.delete()
        return Response(IssueWorklogSerializer(worklog).data, status=status.HTTP_201_CREATED)
