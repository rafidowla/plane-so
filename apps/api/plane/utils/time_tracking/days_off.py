# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 holidays/leave (PSR-122) — which days are off for whom.

Holidays and leave only reduce **Expected** hours; they never block logging time. The rule
(applied in ``reporting.expected_calendar``): for a tracked working day,
``off_fraction = min(1, holiday_fraction + leave_fraction)`` and
``expected = per_day * (1 - off_fraction)``.

A person's *effective* holiday calendar is their assignment (``MemberHolidayCalendar``), else
the workspace default calendar, else none. ``days_off_many`` answers for many people in a
constant number of queries (no per-person / per-day queries).
"""

# Python imports
from collections import namedtuple
from datetime import timedelta
from fractions import Fraction

# Module imports
from plane.db.models import DayPortion, Holiday, HolidayCalendar, MemberHolidayCalendar, MemberLeave

PORTION_FRACTION = {DayPortion.FULL.value: Fraction(1), DayPortion.HALF.value: Fraction(1, 2)}

# One person-day. Fractions are exact; names/notes are "" when that side doesn't apply.
DayOffEntry = namedtuple("DayOffEntry", "holiday_fraction holiday_name leave_fraction leave_note")

ZERO = Fraction(0)


def off_fraction(entry):
    return min(Fraction(1), entry.holiday_fraction + entry.leave_fraction)


def describe(day, entry):
    """The API's ``TDayOff`` for one person-day (``entry`` must have off_fraction > 0)."""
    has_holiday = entry.holiday_fraction > 0
    has_leave = entry.leave_fraction > 0
    if has_holiday and has_leave:
        kind, label = "both", f"{entry.holiday_name} + leave"
    elif has_holiday:
        kind, label = "holiday", entry.holiday_name
    else:
        kind, label = "leave", entry.leave_note or "Leave"
    return {
        "date": day,
        "kind": kind,
        "portion": "full" if off_fraction(entry) >= 1 else "half",
        "label": label,
    }


def effective_calendar_ids(workspace_id, user_ids):
    """``{user_id: calendar_id or None}`` — assignment, else the workspace default (2 queries)."""
    default_id = (
        HolidayCalendar.objects.filter(workspace_id=workspace_id, is_default=True).values_list("id", flat=True).first()
    )
    assigned = dict(
        MemberHolidayCalendar.objects.filter(
            workspace_id=workspace_id, user_id__in=set(user_ids), calendar__deleted_at__isnull=True
        ).values_list("user_id", "calendar_id")
    )
    return {uid: assigned.get(uid, default_id) for uid in user_ids}


def days_off_many(workspace_id, user_ids, start, end):
    """``{user_id: {date: DayOffEntry}}`` for days in [start, end] with a holiday or leave.

    Only days that are off appear. Weekends / untracked days are NOT filtered here: the
    caller decides which days count (see ``reporting.expected_calendar``). 4 queries.
    """
    user_ids = set(user_ids)
    result = {uid: {} for uid in user_ids}
    if not user_ids:
        return result

    calendar_of = effective_calendar_ids(workspace_id, user_ids)
    calendar_ids = {cid for cid in calendar_of.values() if cid}
    holidays = {}  # {calendar_id: {date: (fraction, name)}}
    if calendar_ids:
        for cid, day, name, portion in Holiday.objects.filter(
            calendar_id__in=calendar_ids, calendar__deleted_at__isnull=True, date__gte=start, date__lte=end
        ).values_list("calendar_id", "date", "name", "portion"):
            holidays.setdefault(cid, {})[day] = (PORTION_FRACTION.get(portion, Fraction(1)), name)

    for uid in user_ids:
        for day, (fraction, name) in holidays.get(calendar_of.get(uid), {}).items():
            result[uid][day] = DayOffEntry(fraction, name, ZERO, "")

    for uid, first, last, portion, note in MemberLeave.objects.filter(
        workspace_id=workspace_id, user_id__in=user_ids, start_date__lte=end, end_date__gte=start
    ).values_list("user_id", "start_date", "end_date", "portion", "note"):
        fraction = PORTION_FRACTION.get(portion, Fraction(1))
        day, stop = max(first, start), min(last, end)
        while day <= stop:
            current = result[uid].get(day)
            if current is None:
                result[uid][day] = DayOffEntry(ZERO, "", fraction, note or "")
            elif fraction > current.leave_fraction:
                result[uid][day] = current._replace(leave_fraction=fraction, leave_note=note or "")
            day += timedelta(days=1)
    return result
