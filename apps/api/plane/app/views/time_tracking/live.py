# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — live team dashboard (DESIGN §2 items 26-27, PO answer 1).

Polled every 5 s, so it is three data queries regardless of team size (members +
tracking status; running timers; worklog sums) plus the workspace lookup.

SECURITY: the payload is built field by field from an allow-list below — never
from a serializer — and a work item's id/identifier/title and its project's
id/identifier/name are emitted only when the viewer is an active Admin/Member of
that project (``visibility.can_see_project``). Otherwise the running row says
"private project" and the hours fold into "Other projects".
"""

# Python imports
from datetime import date, timedelta

# Django imports
from django.db.models import Max, OuterRef, Q, Subquery, Sum
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    Issue,
    IssueWorklog,
    TimeTrackingMemberStatus,
    Workspace,
    WorklogTimer,
    WorkspaceMember,
)
from plane.utils.time_tracking.tz import day_start, local_today, zone
from plane.utils.time_tracking.visibility import OTHER_PROJECTS_LABEL, PRIVATE_LABEL, can_see_project

MAX_RANGE_DAYS = 366


def _parse_range(params, today):
    raw_start, raw_end = params.get("start_date"), params.get("end_date")
    try:
        start = date.fromisoformat(raw_start) if raw_start else today
        end = date.fromisoformat(raw_end) if raw_end else (start if raw_start else today)
    except ValueError:
        raise ValueError("start_date / end_date must be dates (YYYY-MM-DD).")
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"The range can be at most {MAX_RANGE_DAYS} days.")
    return start, end


def _avatar_url(avatar_asset_id, avatar):
    # Same rule as User.avatar_url, without loading the asset row.
    if avatar_asset_id:
        return f"/api/assets/v2/static/{avatar_asset_id}/"
    return avatar or None


class TimeLiveDashboardEndpoint(BaseAPIView):
    """``GET ?start_date&end_date`` (default: today) — who is working now.

    Workspace Admin/Member only (guests 403). One row per TRACKED workspace
    admin/member (untracked today = absent): running timers first (newest start
    first), then stopped (latest stop first, then name). Every row has today's
    total in the workspace timezone. ``range`` (hours by project) is filled for
    every row when the viewer is a workspace admin, and only for the viewer's own
    row otherwise (``null`` for others).
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.only("id", "timezone").get(slug=slug)
        tz = zone(workspace.timezone)
        now = timezone.now()
        today = local_today(tz, now)
        try:
            start, end = _parse_range(request.query_params, today)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        viewer_id = request.user.id

        # Query 1 — tracked members (latest status row on/before today; none = tracked).
        latest_status = (
            TimeTrackingMemberStatus.objects.filter(
                workspace_id=workspace.id, user_id=OuterRef("member_id"), effective_from__lte=today
            )
            .order_by("-effective_from", "-created_at")
            .values("is_tracked")[:1]
        )
        members = list(
            WorkspaceMember.objects.filter(
                workspace_id=workspace.id, is_active=True, role__gte=ROLE.MEMBER.value, member__is_bot=False
            )
            .annotate(status_tracked=Subquery(latest_status))
            .values(
                "member_id",
                "role",
                "status_tracked",
                "member__display_name",
                "member__first_name",
                "member__last_name",
                "member__avatar",
                "member__avatar_asset_id",
            )
        )
        viewer_is_admin = any(m["member_id"] == viewer_id and m["role"] == ROLE.ADMIN.value for m in members)
        members = [m for m in members if m["status_tracked"] is not False]
        member_ids = {m["member_id"] for m in members}

        # Query 2 — running timers, with the viewer's right to see each project.
        issue_row = Issue.all_objects.filter(pk=OuterRef("issue_id"))
        timers = {
            t["user_id"]: t
            for t in WorklogTimer.objects.filter(workspace_id=workspace.id, user_id__in=member_ids)
            .annotate(
                visible=can_see_project(viewer_id),
                issue_name=Subquery(issue_row.values("name")[:1]),
                issue_sequence_id=Subquery(issue_row.values("sequence_id")[:1]),
            )
            .values(
                "user_id",
                "issue_id",
                "project_id",
                "started_at",
                "visible",
                "issue_name",
                "issue_sequence_id",
                "project__identifier",
                "project__name",
            )
        }

        # Query 3 — worklog sums per (member, project) over today ∪ range.
        window_start, window_end = min(start, today), max(end, today)
        sums = (
            IssueWorklog.objects.filter(
                workspace_id=workspace.id,
                logged_by_id__in=member_ids,
                logged_date__gte=window_start,
                logged_date__lte=window_end,
            )
            .annotate(visible=can_see_project(viewer_id))
            .values(
                "logged_by_id", "project_id", "visible", "project__identifier", "project__name", "project__logo_props"
            )
            .annotate(
                today_minutes=Sum("duration", filter=Q(logged_date=today)),
                range_minutes=Sum("duration", filter=Q(logged_date__gte=start, logged_date__lte=end)),
                last_ended_at=Max("ended_at"),
            )
        )
        per_user = {}
        for row in sums:
            per_user.setdefault(row["logged_by_id"], []).append(row)

        today_start = day_start(today, tz)
        rows = []
        for m in members:
            uid = m["member_id"]
            entries = per_user.get(uid, [])
            timer = timers.get(uid)
            today_logged = sum(e["today_minutes"] or 0 for e in entries)
            running_today = 0
            if timer:
                running_today = max(0, int((now - max(timer["started_at"], today_start)).total_seconds() // 60))
            last_stops = [e["last_ended_at"] for e in entries if e["last_ended_at"]]
            show_range = viewer_is_admin or uid == viewer_id
            rows.append(
                {
                    "user": {
                        "id": str(uid),
                        "display_name": m["member__display_name"],
                        "first_name": m["member__first_name"],
                        "last_name": m["member__last_name"],
                        "avatar_url": _avatar_url(m["member__avatar_asset_id"], m["member__avatar"]),
                    },
                    "is_me": uid == viewer_id,
                    "status": "running" if timer else "stopped",
                    "timer": self._timer(timer) if timer else None,
                    "last_stopped_at": max(last_stops) if last_stops else None,
                    "today_logged_minutes": today_logged,
                    "today_minutes": today_logged + running_today,
                    "range": self._range(entries) if show_range else None,
                }
            )

        far_past = now - timedelta(days=36500)

        def sort_key(r):
            if r["timer"]:
                return (0, -r["timer"]["started_at"].timestamp(), "")
            stop = r["last_stopped_at"] or far_past
            return (1, -stop.timestamp(), (r["user"]["display_name"] or "").lower())

        rows.sort(key=sort_key)
        return Response(
            {
                "server_now": now,
                "date": today,
                "timezone": str(tz.key),
                "start_date": start,
                "end_date": end,
                "scope": "all" if viewer_is_admin else "self",
                "rows": rows,
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _timer(t):
        if not t["visible"]:
            return {
                "started_at": t["started_at"],
                "private": True,
                "label": PRIVATE_LABEL,
                "issue": None,
                "project": None,
            }
        ref = f"{t['project__identifier']}-{t['issue_sequence_id']}" if t["issue_sequence_id"] is not None else None
        return {
            "started_at": t["started_at"],
            "private": False,
            "label": None,
            "issue": {"id": str(t["issue_id"]), "ref": ref, "name": t["issue_name"]},
            "project": {
                "id": str(t["project_id"]),
                "identifier": t["project__identifier"],
                "name": t["project__name"],
            },
        }

    @staticmethod
    def _range(entries):
        projects, other, total = [], 0, 0
        for e in entries:
            minutes = e["range_minutes"] or 0
            if not minutes:
                continue
            total += minutes
            if e["visible"]:
                projects.append(
                    {
                        "id": str(e["project_id"]),
                        "identifier": e["project__identifier"],
                        "name": e["project__name"],
                        "logo_props": e["project__logo_props"],
                        "minutes": minutes,
                    }
                )
            else:
                other += minutes
        projects.sort(key=lambda p: (-p["minutes"], p["identifier"] or ""))
        return {
            "total_minutes": total,
            "projects": projects,
            "other_projects": {"label": OTHER_PROJECTS_LABEL, "minutes": other},
        }
