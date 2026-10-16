# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import uuid

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
    Issue,
    IssueWorklog,
    WorklogTimer,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    WorklogSource,
)

# FORK: time-tracking-v2 — timer start/stop and the shared helpers moved to a service
# layer (plane.utils.time_tracking) so the sweep and the S2 endpoints reuse them. The
# names below stay importable from here for existing callers.
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.timer import (  # noqa: F401
    TimeTrackingError,
    TimerAlreadyRunning,
    is_project_admin,
    is_project_member,
    issue_label as _issue_ref,
    lock_user_timers,
    resolve_billable_rate,
    start_timer,
    stop_user_timer,
)


def _stop_payload(worklogs):
    """FORK: time-tracking-v2 — a stop can now produce one entry per local day.

    ``worklog`` (and the top-level fields of a stop response) is the LAST piece, so
    single-day stops look exactly as before; ``pieces`` lists every entry and
    ``total_duration`` is their sum.
    """
    pieces = IssueWorklogSerializer(worklogs, many=True).data
    return pieces, sum(w.duration for w in worklogs)


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

        # FORK: security — the work item must live in the project (and workspace) of the URL.
        # Project-admin rights on one project must not let an admin log time against a work
        # item of another project or workspace just by knowing its UUID.
        if not Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).exists():
            return Response({"error": "Work item not found."}, status=status.HTTP_404_NOT_FOUND)

        # The resource the time belongs to (defaults to the admin entering it). FORK: security —
        # on-behalf entries are only for people who are active members of THIS project; otherwise
        # an admin could attach time (and billing) to any user id, including users outside the
        # project/workspace.
        logged_by_id = request.data.get("logged_by") or request.user.id
        if str(logged_by_id) != str(request.user.id):
            try:
                logged_by_id = uuid.UUID(str(logged_by_id))
            except (ValueError, AttributeError):
                return Response({"error": "logged_by is not a valid user."}, status=status.HTTP_400_BAD_REQUEST)
            # FORK: guests are typically client users, so time can't be logged on their behalf.
            if not ProjectMember.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                member_id=logged_by_id,
                is_active=True,
                role__gte=ROLE.MEMBER.value,
            ).exists():
                return Response(
                    {"error": "logged_by must be an active member or admin of this project."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
        # FORK: time-tracking-v2 — audit every manual change.
        audit.record(
            worklog.workspace_id,
            request.user,
            TimeTrackingAuditTarget.WORKLOG,
            worklog.id,
            TimeTrackingAuditAction.CREATE,
            after=audit.worklog_state(worklog),
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
        before = audit.worklog_state(worklog)
        serializer = IssueWorklogSerializer(worklog, data=request.data, partial=True)
        if serializer.is_valid():
            worklog = serializer.save()
            # FORK: time-tracking-v2 — audit every manual change.
            audit.record(
                worklog.workspace_id,
                request.user,
                TimeTrackingAuditTarget.WORKLOG,
                worklog.id,
                TimeTrackingAuditAction.UPDATE,
                before=before,
                after=audit.worklog_state(worklog),
            )
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
        # FORK: time-tracking-v2 — audit every manual change.
        audit.record(
            worklog.workspace_id,
            request.user,
            TimeTrackingAuditTarget.WORKLOG,
            worklog.id,
            TimeTrackingAuditAction.DELETE,
            before=audit.worklog_state(worklog),
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
        # FORK: time-tracking-v2 — logic lives in plane.utils.time_tracking.timer.start_timer
        # (project enabled 400, issue-in-project 404, tracking turned off 403, PSR-101
        # stop-previous-then-start in one locked transaction).
        try:
            timer, stopped = start_timer(
                slug, project_id, issue_id, request.user, request.data.get("description", "")
            )
        except TimerAlreadyRunning as exc:
            return Response(
                {**exc.payload, "timer": WorklogTimerSerializer(exc.timer).data},
                status=exc.status_code,
            )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        data = WorklogTimerSerializer(timer).data
        if stopped:
            pieces, total = _stop_payload(stopped["worklogs"])
            data["stopped"] = {
                "worklog": pieces[-1],
                "duration": total,
                "issue": stopped["issue"],
                "pieces": pieces,
            }
        return Response(data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def delete(self, request, slug, project_id, issue_id):
        """Stop the running timer and convert it to worklog(s)."""
        worklogs = stop_user_timer(slug, issue_id, request.user, description=request.data.get("description"))
        if not worklogs:
            return Response({"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND)
        pieces, total = _stop_payload(worklogs)
        data = dict(pieces[-1])
        data["pieces"] = pieces
        data["total_duration"] = total
        return Response(data, status=status.HTTP_201_CREATED)


class WorklogRunningTimerEndpoint(BaseAPIView):
    """FORK: PSR-105 — the current user's running timer in this workspace.

    Backs the always-visible timer indicator. Unlike the per-issue endpoint it needs
    no project/issue in the URL and returns what the indicator renders (work item
    reference, name, link ids) plus the server clock, so elapsed time is not skewed
    by a wrong client clock. Returns only ``server_now`` when no timer is running.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        now = timezone.now()
        timer = WorklogTimer.objects.filter(workspace__slug=slug, user=request.user).first()
        if not timer:
            return Response({"server_now": now}, status=status.HTTP_200_OK)
        data = WorklogTimerSerializer(timer).data
        data["issue_detail"] = _issue_ref(timer.issue_id)
        data["server_now"] = now
        return Response(data, status=status.HTTP_200_OK)
