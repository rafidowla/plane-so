# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S5 — report scope and the expected-hours calendar.

**Scope** (DESIGN §2 item 28, enforced here, not in the client):

* workspace admin — everyone's time, every project in detail;
* project admin — themselves plus the active members (role >= 15) of the projects
  they administer. Those members' time in projects the viewer does NOT administer is
  counted (so Expected/Balance stay meaningful) but folded into "Other projects":
  no project, task or note leaves the server for it;
* member — only themselves (all of their own time, in detail);
* guest — nothing (the views 403 before reaching this module).

**Expected** (items 24/25): for each person, every working day in the range
(``TimeTrackingSetting.work_days``) on which they were tracked
(``TimeTrackingMemberStatus`` history; guests never) counts ``minutes_per_day``, or
their weekly capacity override (``ResourceCapacity.weekly_capacity``) spread evenly
over the working days. Expected-to-date counts only days up to the workspace-local
today. Weekend (non-working-day) time counts in Logged and in Weekend, never in
Expected.

**Holidays and leave** (FORK: time-tracking-v2 holidays/leave, PSR-122) reduce Expected only:
on a tracked working day ``off_fraction = min(1, holiday_fraction + leave_fraction)`` and the
day expects ``per_day * (1 - off_fraction)`` (see ``days_off``). They never block logging;
time logged on an off day counts in Logged as usual.
"""

# Python imports
from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction

# Django imports
from django.db.models import Q

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import (
    IssueWorklog,
    ProjectMember,
    ResourceCapacity,
    TimeTrackingMemberStatus,
    WorkspaceMember,
)
from plane.utils.time_tracking import days_off as days_off_util

SCOPE_ALL = "all"
SCOPE_PROJECTS = "projects"
SCOPE_SELF = "self"

# Range limit for the report (DESIGN item 29).
MAX_RANGE_DAYS = 366


@dataclass
class ReportScope:
    """What one viewer may see in reports.

    ``user_ids`` None = every user; ``detail_project_ids`` None = every project.
    Another user's time outside ``detail_project_ids`` is "Other projects".
    """

    kind: str
    viewer_id: object
    user_ids: set = None
    detail_project_ids: set = None
    admin_project_ids: set = field(default_factory=set)

    @property
    def is_all(self):
        return self.kind == SCOPE_ALL

    def allows_user(self, user_id):
        return self.user_ids is None or user_id in self.user_ids

    def user_q(self, prefix=""):
        if self.user_ids is None:
            return Q()
        return Q(**{f"{prefix}logged_by_id__in": self.user_ids})

    def detail_q(self, prefix=""):
        """Rows whose project / task / note the viewer may see."""
        if self.detail_project_ids is None:
            return Q()
        return Q(**{f"{prefix}logged_by_id": self.viewer_id}) | Q(
            **{f"{prefix}project_id__in": self.detail_project_ids}
        )

    def is_detail(self, logged_by_id, project_id):
        if self.detail_project_ids is None:
            return True
        return logged_by_id == self.viewer_id or project_id in self.detail_project_ids


def workspace_role(workspace_id, user_id):
    return (
        WorkspaceMember.objects.filter(workspace_id=workspace_id, member_id=user_id, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


def resolve_scope(workspace_id, user):
    """The viewer's report scope, or None for guests / non-members."""
    role = workspace_role(workspace_id, user.id)
    if role is None or role < ROLE.MEMBER.value:
        return None
    if role == ROLE.ADMIN.value:
        return ReportScope(kind=SCOPE_ALL, viewer_id=user.id)
    admin_projects = set(
        ProjectMember.objects.filter(
            workspace_id=workspace_id,
            member_id=user.id,
            is_active=True,
            role=ROLE.ADMIN.value,
            project__deleted_at__isnull=True,
            project__archived_at__isnull=True,
        ).values_list("project_id", flat=True)
    )
    if not admin_projects:
        return ReportScope(kind=SCOPE_SELF, viewer_id=user.id, user_ids={user.id}, detail_project_ids=set())
    members = set(
        ProjectMember.objects.filter(
            project_id__in=admin_projects, is_active=True, role__gte=ROLE.MEMBER.value
        ).values_list("member_id", flat=True)
    )
    members.add(user.id)
    return ReportScope(
        kind=SCOPE_PROJECTS,
        viewer_id=user.id,
        user_ids=members,
        detail_project_ids=admin_projects,
        admin_project_ids=admin_projects,
    )


def candidate_user_ids(workspace_id, scope, start, end):
    """Everyone the viewer may pick in the report: active internal workspace members
    in scope, plus anyone in scope with time in the range (e.g. a deactivated member).
    """
    members = WorkspaceMember.objects.filter(workspace_id=workspace_id, is_active=True, role__gte=ROLE.MEMBER.value)
    if scope.user_ids is not None:
        members = members.filter(member_id__in=scope.user_ids)
    ids = set(members.values_list("member_id", flat=True))
    logged = IssueWorklog.objects.filter(workspace_id=workspace_id, logged_date__gte=start, logged_date__lte=end)
    if scope.user_ids is not None:
        logged = logged.filter(logged_by_id__in=scope.user_ids)
    ids |= set(logged.values_list("logged_by_id", flat=True).distinct())
    if scope.user_ids is not None:
        ids &= scope.user_ids
    return ids


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def date_range(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def default_weekly_minutes(setting):
    return setting.minutes_per_day * len(setting.work_days or [])


def tracked_days_many(workspace_id, user_ids, start, end):
    """``{user_id: set(dates)}`` the users were tracked on in [start, end] (2 queries).

    Same rule as ``tracking.tracked_days``: latest status row with
    ``effective_from <= day`` wins (ties by ``created_at``); no row = tracked;
    only active workspace admins/members are ever tracked.
    """
    user_ids = set(user_ids)
    internal = set(
        WorkspaceMember.objects.filter(
            workspace_id=workspace_id, member_id__in=user_ids, is_active=True, role__gte=ROLE.MEMBER.value
        ).values_list("member_id", flat=True)
    )
    rows = {}
    for uid, eff, tracked in (
        TimeTrackingMemberStatus.objects.filter(
            workspace_id=workspace_id, user_id__in=internal, effective_from__lte=end
        )
        .order_by("user_id", "effective_from", "created_at")
        .values_list("user_id", "effective_from", "is_tracked")
    ):
        rows.setdefault(uid, []).append((eff, tracked))
    result = {}
    for uid in user_ids:
        if uid not in internal:
            result[uid] = set()
            continue
        history = rows.get(uid, [])
        days = set()
        current = True
        i = 0
        for day in date_range(start, end):
            while i < len(history) and history[i][0] <= day:
                current = history[i][1]
                i += 1
            if current:
                days.add(day)
        result[uid] = days
    return result


def capacity_overrides(workspace_id, user_ids):
    """``{user_id: weekly_minutes}`` for people with a weekly capacity override."""
    return dict(
        ResourceCapacity.objects.filter(
            workspace_id=workspace_id, user_id__in=set(user_ids), weekly_capacity__isnull=False
        ).values_list("user_id", "weekly_capacity")
    )


def per_day_minutes(setting, override):
    """Expected minutes on one working day (exact, as a Fraction)."""
    work_days = setting.work_days or []
    if override is None:
        return Fraction(setting.minutes_per_day)
    if not work_days:
        return Fraction(0)
    return Fraction(override, len(work_days))


@dataclass
class ExpectedCalendar:
    """Per-user, per-day expected minutes over a range.

    FORK: PSR-122 — ``days`` is already reduced by holidays/leave. ``off`` is
    ``{user_id: {date: DayOffEntry}}`` for the tracked working days that are (partly) off, and
    ``off_minutes`` the expected minutes that removed (``{user_id: {date: Fraction}}``).
    """

    days: dict  # {user_id: {date: Fraction}}
    off: dict = field(default_factory=dict)
    off_minutes: dict = field(default_factory=dict)

    def user_total(self, user_id, until=None, start=None, end=None):
        total = Fraction(0)
        for day, minutes in self.days.get(user_id, {}).items():
            if until is not None and day > until:
                continue
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            total += minutes
        return total

    def day_total(self, day, user_ids=None):
        ids = self.days.keys() if user_ids is None else user_ids
        return sum((self.days.get(uid, {}).get(day, Fraction(0)) for uid in ids), Fraction(0))

    def off_total(self, user_id, start=None, end=None):
        """Expected minutes removed by holidays/leave for one user in [start, end]."""
        total = Fraction(0)
        for day, removed in self.off_minutes.get(user_id, {}).items():
            if (start is not None and day < start) or (end is not None and day > end):
                continue
            total += removed
        return total

    def days_off(self, user_id, start=None, end=None):
        """The user's ``TDayOff`` list (tracked working days that are off), sorted by date."""
        return [
            days_off_util.describe(day, entry)
            for day, entry in sorted(self.off.get(user_id, {}).items())
            if (start is None or day >= start) and (end is None or day <= end)
        ]


def expected_calendar(workspace_id, setting, user_ids, start, end):
    work_days = set(setting.work_days or [])
    tracked = tracked_days_many(workspace_id, user_ids, start, end)
    overrides = capacity_overrides(workspace_id, user_ids)
    # FORK: PSR-122 — holidays (effective calendar) and leave, 4 queries for everyone.
    off_days = days_off_util.days_off_many(workspace_id, user_ids, start, end)
    days, off, off_minutes = {}, {}, {}
    for uid in user_ids:
        per_day = per_day_minutes(setting, overrides.get(uid))
        days[uid], off[uid], off_minutes[uid] = {}, {}, {}
        user_off = off_days.get(uid, {})
        for d in tracked.get(uid, ()):
            if d.isoweekday() not in work_days:
                continue
            entry = user_off.get(d)
            fraction = days_off_util.off_fraction(entry) if entry else 0
            days[uid][d] = per_day * (1 - fraction)
            if fraction > 0:
                off[uid][d] = entry
                off_minutes[uid][d] = per_day * fraction
    return ExpectedCalendar(days=days, off=off, off_minutes=off_minutes), tracked, overrides


def is_working_day(setting, day):
    return day.isoweekday() in set(setting.work_days or [])


def minutes(value):
    """Round an exact minute count for output."""
    return int(round(value))
