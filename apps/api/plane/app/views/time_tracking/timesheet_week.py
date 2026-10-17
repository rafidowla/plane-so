# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S4 — "My timesheet".

* ``GET time/timesheet/?week_start=&user_id=`` — one Mon-Sun week (workspace TZ) as a grid
  (project > work item rows x 7 days), expected minutes per day, and the entries grouped by
  date (badge count, span, total).
* ``GET time/timesheet/people/?q=`` — who the viewer may switch to.

Visibility mirrors the reports: members see only themselves, a project admin sees people in
their projects (and only the time logged in those projects; the rest is a count), a workspace
admin sees every internal member, guests are refused. Nothing here exposes billing fields.
"""

# Python imports
import uuid
from datetime import timedelta

# Django imports
from django.db.models import Count, Sum
from django.utils.dateparse import parse_date

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    Issue,
    IssueWorklog,
    Timesheet,
    TimeEntryRequest,
    TimeEntryRequestStatus,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import entry_requests, limits
from plane.utils.time_tracking.reporting import (
    SCOPE_ALL,
    SCOPE_PROJECTS,
    date_range,
    default_weekly_minutes,
    expected_calendar,
    is_working_day,
    minutes,
    resolve_scope,
)
from plane.utils.time_tracking.tz import local_today, workspace_tz

# Hard caps: a week of one person never needs more, and the page must stay bounded.
MAX_ENTRIES = 500
MAX_PENDING_REQUESTS = 100
MAX_PEOPLE = 200
MIN_YEAR, MAX_YEAR = 2000, 2100


def _bad(error, code="invalid_filter", http=status.HTTP_400_BAD_REQUEST):
    return Response({"error": error, "code": code}, status=http)


def _monday(day):
    return day - timedelta(days=day.weekday())


def _user_lite(user):
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
    }


class _Target:
    """Whose week is shown, and what part of it the viewer may see."""

    def __init__(self, user, is_self, admin_project_ids=None):
        self.user = user
        self.is_self = is_self
        # None = every project; a set = only these (project admin viewing someone else).
        self.admin_project_ids = admin_project_ids


def _resolve_target(request, workspace_id):
    """``(target, error_response)``. Self by default; another user only when in scope."""
    raw = request.query_params.get("user_id")
    if not raw or raw == str(request.user.id):
        return _Target(request.user, True), None
    try:
        user_id = uuid.UUID(raw)
    except ValueError:
        return None, _bad("user_id is not a valid id.")
    scope = resolve_scope(workspace_id, request.user)
    if scope is None:
        return None, _bad("Not allowed.", "not_allowed", status.HTTP_403_FORBIDDEN)
    if scope.kind == SCOPE_ALL:
        admin_projects = None
    elif scope.kind == SCOPE_PROJECTS and scope.allows_user(user_id):
        admin_projects = set(scope.admin_project_ids)
    else:
        # Members and project admins asking about someone outside their projects get the
        # same answer, so the response does not reveal who exists.
        return None, _bad("You can only view your own timesheet.", "not_allowed", status.HTTP_403_FORBIDDEN)
    is_internal = WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=user_id, role__gte=ROLE.MEMBER.value
    ).exists()
    user = User.objects.filter(pk=user_id).first() if is_internal else None
    if user is None:
        return None, _bad("User not found.", "not_found", status.HTTP_404_NOT_FOUND)
    return _Target(user, False, admin_projects), None


def _parse_week(request, tz):
    raw = request.query_params.get("week_start")
    if not raw:
        return _monday(local_today(tz)), None
    try:
        day = parse_date(raw) if len(raw) == 10 else None
    except ValueError:  # well-formed but impossible, e.g. 2026-13-01
        day = None
    if day is None or not (MIN_YEAR <= day.year <= MAX_YEAR):
        return None, _bad("week_start must be a date (YYYY-MM-DD).")
    return _monday(day), None


def _live_issues(issue_ids):
    ids = [i for i in issue_ids if i]
    return {
        row[0]: row[1:]
        for row in Issue.all_objects.filter(id__in=ids).values_list(
            "id", "name", "sequence_id", "project__identifier", "deleted_at"
        )
    }


def _issue_info(issue_id, live, snap_ref, snap_name):
    hit = live.get(issue_id)
    if hit:
        name, seq, ident, deleted_at = hit
        ref = f"{ident}-{seq}" if ident and seq else ""
        return {
            "id": str(issue_id),
            "ref": ref,
            "name": name or "Untitled",
            "is_deleted": deleted_at is not None,
        }
    return {
        "id": str(issue_id),
        "ref": snap_ref or "",
        "name": snap_name or "Untitled",
        "is_deleted": True,
    }


class TimesheetWeekEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.only("id", "timezone").get(slug=slug)
        workspace_id = workspace.id
        tz = workspace_tz(workspace_id)
        week_start, err = _parse_week(request, tz)
        if err:
            return err
        target, err = _resolve_target(request, workspace_id)
        if err:
            return err
        week_end = week_start + timedelta(days=6)
        days = list(date_range(week_start, week_end))
        today = local_today(tz)
        setting = limits.read_settings(workspace_id)
        uid = target.user.id

        calendar, tracked, _ = expected_calendar(workspace_id, setting, {uid}, week_start, week_end)
        expected = calendar.days.get(uid, {})
        tracked_days = tracked.get(uid, set())

        base = IssueWorklog.objects.filter(
            workspace_id=workspace_id, logged_by_id=uid, logged_date__gte=week_start, logged_date__lte=week_end
        )
        visible = base
        if target.admin_project_ids is not None:
            visible = base.filter(project_id__in=target.admin_project_ids)

        grid_rows = list(
            visible.values("project_id", "issue_id", "logged_date").annotate(total=Sum("duration"), count=Count("id"))
        )
        # Snapshot labels, for work items that no longer exist.
        snaps = {}
        for iid, ref, name in visible.values_list("issue_id", "issue_ref", "issue_name").distinct():
            snaps.setdefault(iid, (ref, name))
        live = _live_issues({r["issue_id"] for r in grid_rows})
        projects_meta = {p["id"]: p for p in _projects({r["project_id"] for r in grid_rows})}

        day_index = {d: i for i, d in enumerate(days)}
        day_logged = [0] * 7
        projects = {}
        for r in grid_rows:
            i = day_index[r["logged_date"]]
            total = r["total"] or 0
            day_logged[i] += total
            proj = projects.setdefault(
                r["project_id"],
                {"minutes": [0] * 7, "total": 0, "issues": {}},
            )
            proj["minutes"][i] += total
            proj["total"] += total
            row = proj["issues"].get(r["issue_id"])
            if row is None:
                ref, name = snaps.get(r["issue_id"], ("", ""))
                row = {**_issue_info(r["issue_id"], live, ref, name), "minutes": [0] * 7, "total": 0}
                proj["issues"][r["issue_id"]] = row
            row["minutes"][i] += total
            row["total"] += total

        other_by_day = [0] * 7
        if target.admin_project_ids is not None:
            for r in (
                base.exclude(project_id__in=target.admin_project_ids)
                .values("logged_date")
                .annotate(total=Sum("duration"))
            ):
                other_by_day[day_index[r["logged_date"]]] += r["total"] or 0

        day_payload = []
        for i, d in enumerate(days):
            day_payload.append(
                {
                    "date": d,
                    "weekday": d.isoweekday(),
                    "is_weekend": not is_working_day(setting, d),
                    "is_today": d == today,
                    "is_future": d > today,
                    "tracked": d in tracked_days,
                    "expected_minutes": minutes(expected.get(d, 0)),
                    "logged_minutes": day_logged[i],
                    "other_projects_minutes": other_by_day[i],
                }
            )

        project_payload = []
        for pid, proj in projects.items():
            meta = projects_meta.get(pid, {"id": pid, "name": "", "identifier": ""})
            issues = sorted(proj["issues"].values(), key=lambda x: (-x["total"], x["ref"], x["name"]))
            project_payload.append(
                {
                    "project": {"id": str(pid), "name": meta["name"], "identifier": meta["identifier"]},
                    "minutes": proj["minutes"],
                    "total": proj["total"],
                    "issues": issues,
                }
            )
        project_payload.sort(key=lambda p: (-p["total"], p["project"]["identifier"]))

        entries_truncated, entries_by_date = self._entries(visible, live)
        pending = self._pending_requests(request, workspace_id, target, week_start, week_end)
        approval = self._approval(workspace_id, uid, week_start, setting)

        weekend_total = sum(day_logged[i] for i, d in enumerate(days) if not is_working_day(setting, d))
        expected_total = sum(p["expected_minutes"] for p in day_payload)
        expected_to_date = sum(p["expected_minutes"] for p in day_payload if p["date"] <= today)
        is_ws_admin = WorkspaceMember.objects.filter(
            workspace_id=workspace_id, member=request.user, role=ROLE.ADMIN.value, is_active=True
        ).exists()
        return Response(
            {
                "week_start": week_start,
                "week_end": week_end,
                "timezone": str(tz),
                "today": today,
                "user": _user_lite(target.user),
                "is_self": target.is_self,
                "scoped": target.admin_project_ids is not None,
                "viewer": {
                    "is_workspace_admin": is_ws_admin,
                    "can_add_time": target.is_self and (is_ws_admin or setting.allow_member_manual_entry),
                },
                "settings": {
                    "allow_member_manual_entry": setting.allow_member_manual_entry,
                    "weekly_approval_enabled": setting.weekly_approval_enabled,
                    "minutes_per_day": setting.minutes_per_day,
                    "weekly_minutes": default_weekly_minutes(setting),
                },
                "days": day_payload,
                "projects": project_payload,
                "totals": {
                    "logged": sum(day_logged),
                    "expected": expected_total,
                    "expected_to_date": expected_to_date,
                    "weekend": weekend_total,
                    "other_projects": sum(other_by_day),
                },
                "entries_by_date": entries_by_date,
                "entries_truncated": entries_truncated,
                "pending_requests": pending,
                "approval": approval,
            },
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------

    def _entries(self, visible, live):
        rows = list(visible.order_by("logged_date", "started_at", "created_at")[: MAX_ENTRIES + 1])
        truncated = len(rows) > MAX_ENTRIES
        rows = rows[:MAX_ENTRIES]
        pending_by_worklog = {}
        ids = [r.id for r in rows]
        if ids:
            for req_id, wl_id in TimeEntryRequest.objects.filter(
                worklog_id__in=ids, status=TimeEntryRequestStatus.PENDING
            ).values_list("id", "worklog_id"):
                pending_by_worklog[wl_id] = str(req_id)
        by_date = {}
        for r in rows:
            issue = _issue_info(r.issue_id, live, r.issue_ref, r.issue_name)
            day = by_date.setdefault(r.logged_date, {"date": r.logged_date, "total_minutes": 0, "groups": {}})
            day["total_minutes"] += r.duration
            group = day["groups"].setdefault(
                r.issue_id,
                {
                    "issue": issue,
                    "project_id": str(r.project_id),
                    "count": 0,
                    "total_minutes": 0,
                    "span_started_at": None,
                    "span_ended_at": None,
                    "entries": [],
                },
            )
            group["count"] += 1
            group["total_minutes"] += r.duration
            if r.started_at and (group["span_started_at"] is None or r.started_at < group["span_started_at"]):
                group["span_started_at"] = r.started_at
            if r.ended_at and (group["span_ended_at"] is None or r.ended_at > group["span_ended_at"]):
                group["span_ended_at"] = r.ended_at
            group["entries"].append(
                {
                    "id": str(r.id),
                    "issue_id": str(r.issue_id),
                    "project_id": str(r.project_id),
                    "logged_date": r.logged_date,
                    "started_at": r.started_at,
                    "ended_at": r.ended_at,
                    "duration": r.duration,
                    "description": r.description,
                    "source": r.source,
                    "work_type": r.work_type,
                    "is_locked": r.is_locked,
                    "approval_status": r.approval_status,
                    "auto_stopped": r.auto_stopped,
                    "idle_stopped": r.idle_stopped,
                    "request_id": str(r.request_id) if r.request_id else None,
                    "pending_request_id": pending_by_worklog.get(r.id),
                }
            )
        payload = []
        for d in sorted(by_date, reverse=True):
            day = by_date[d]
            payload.append(
                {
                    "date": day["date"],
                    "total_minutes": day["total_minutes"],
                    "groups": list(day["groups"].values()),
                }
            )
        return truncated, payload

    def _pending_requests(self, request, workspace_id, target, week_start, week_end):
        qs = TimeEntryRequest.objects.filter(
            workspace_id=workspace_id,
            requester_id=target.user.id,
            status=TimeEntryRequestStatus.PENDING,
            work_date__gte=week_start,
            work_date__lte=week_end,
        )
        if target.admin_project_ids is not None:
            qs = qs.filter(project_id__in=target.admin_project_ids)
        rows = qs.select_related("requester", "reviewer").order_by("work_date", "created_at")[:MAX_PENDING_REQUESTS]
        return [entry_requests.serialize(r, request.user) for r in rows]

    def _approval(self, workspace_id, user_id, week_start, setting):
        row = Timesheet.objects.filter(workspace_id=workspace_id, user_id=user_id, period_start=week_start).first()
        return {
            "enabled": setting.weekly_approval_enabled,
            "status": row.status if row else "open",
            "timesheet_id": str(row.id) if row else None,
            "review_note": row.review_note if row else "",
        }


def _projects(project_ids):
    from plane.db.models import Project

    return [
        {"id": p[0], "name": p[1], "identifier": p[2]}
        for p in Project.all_objects.filter(id__in=project_ids).values_list("id", "name", "identifier")
    ]


class TimesheetPeopleEndpoint(BaseAPIView):
    """Whom the viewer may open in the timesheet (always includes themselves)."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace_id = Workspace.objects.only("id").get(slug=slug).id
        scope = resolve_scope(workspace_id, request.user)
        if scope is None:
            return _bad("Not allowed.", "not_allowed", status.HTTP_403_FORBIDDEN)
        members = WorkspaceMember.objects.filter(workspace_id=workspace_id, is_active=True, role__gte=ROLE.MEMBER.value)
        if scope.user_ids is not None:
            members = members.filter(member_id__in=scope.user_ids)
        q = (request.query_params.get("q") or "").strip()[:100]
        if q:
            members = members.filter(member__display_name__icontains=q)
        members = members.select_related("member").order_by("member__display_name")[:MAX_PEOPLE]
        people = [_user_lite(m.member) for m in members if m.member_id != request.user.id]
        return Response(
            {"viewer_id": str(request.user.id), "results": people, "scope": scope.kind},
            status=status.HTTP_200_OK,
        )
