# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — workspace-timezone date bucketing.

Time is bucketed into days in the WORKSPACE timezone (``Workspace.timezone``),
never the server's (``TIME_ZONE = "UTC"``) and never per viewer: a timer
started at 00:30 in Asia/Dhaka belongs to that Dhaka date, not the previous
UTC date.
"""

# Python imports
import math
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Django imports
from django.utils import timezone

# A timer older than this is not split day by day any further (defensive cap;
# the 14 h auto-stop keeps real timers far below it).
MAX_SPLIT_DAYS = 366


def zone(name):
    """ZoneInfo for a tz name; falls back to UTC for empty/unknown names."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def workspace_tz(workspace_id):
    from plane.db.models import Workspace

    name = Workspace.all_objects.filter(pk=workspace_id).values_list("timezone", flat=True).first()
    return zone(name)


def local_date(dt, tz):
    return dt.astimezone(tz).date()


def local_today(tz, now=None):
    return local_date(now or timezone.now(), tz)


def day_start(d, tz):
    """The instant local day ``d`` starts in ``tz``, as an aware UTC datetime."""
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(dt_timezone.utc)


def total_minutes(started_at, ended_at):
    """Whole minutes, rounded down, with a one-minute minimum (existing rule)."""
    seconds = max(0.0, (ended_at - started_at).total_seconds())
    return max(1, math.floor(seconds / 60))


def split_by_local_day(started_at, ended_at, tz):
    """Split [started_at, ended_at) at each local midnight in ``tz``.

    Returns a list of dicts ``{started_at, ended_at, logged_date, duration}``.
    Piece durations are cut on whole minutes elapsed since ``started_at``, so
    they always sum to exactly ``total_minutes(started_at, ended_at)``. A piece
    that rounds to zero minutes (e.g. 20 s before midnight) is dropped.
    """
    if ended_at < started_at:
        ended_at = started_at
    total = total_minutes(started_at, ended_at)

    bounds = [started_at]
    day = local_date(started_at, tz)
    for _ in range(MAX_SPLIT_DAYS):
        day = day + timedelta(days=1)
        boundary = day_start(day, tz)
        if boundary >= ended_at:
            break
        bounds.append(boundary)
    bounds.append(ended_at)

    elapsed = [math.floor((b - started_at).total_seconds() / 60) for b in bounds]
    elapsed[-1] = total
    pieces = []
    for i in range(len(bounds) - 1):
        minutes = elapsed[i + 1] - elapsed[i]
        if minutes <= 0:
            continue
        pieces.append(
            {
                "started_at": bounds[i],
                "ended_at": bounds[i + 1],
                "logged_date": local_date(bounds[i], tz),
                "duration": minutes,
            }
        )
    return pieces
