# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — server-side limits on a running timer.

Three limits come from the workspace settings (``TimeTrackingSetting``):

* **reminder** at ``started_at + remind_after_minutes`` (10 h) — an in-app
  notification, once (``WorklogTimer.reminded_at``).
* **auto-stop** at ``started_at + auto_stop_after_minutes`` (14 h) — the entry
  is saved with ``auto_stopped=True`` and still counts (PO answer 2).
* **"still working?" check** at a RANDOM time between ``idle_check_min_minutes``
  and ``idle_check_max_minutes`` (both 0 = off) of continuous running, counted
  from ``idle_base`` (``max(started_at, last_confirmed_at, settings.updated_at)``).
  The interval is derived, not stored: ``HMAC-SHA256(SECRET_KEY, "<timer id>:<base>")``
  mod the window size, so the sweep, confirm, stop and the payload all agree
  with no state, and a new base (confirm / settings edit) is a new draw. The
  client is never told the time in advance; the payload's ``check`` is null
  until the server clock reaches it. With no answer by ``check_deadline_at``
  (= prompt + ``IDLE_GRACE``) the timer is stopped AT ``next_check_at`` — the
  moment the prompt appeared — and flagged ``idle_stopped`` (DESIGN §8 item 2).

``cutoff`` is the single rule every stop goes through (user stop, start that
stops the previous timer, confirm-too-late, sweep, system stops), so time past
a limit never counts no matter which path stops the timer or how late the
sweep runs.
"""

# Python imports
import hashlib
import hmac
from datetime import timedelta

# Django imports
from django.conf import settings as django_settings

# How long the "still working?" prompt waits for an answer. A constant so the
# product can change it without a settings field (DESIGN §8 item 2).
IDLE_GRACE = timedelta(minutes=5)

LIMIT_AUTO_STOP = "auto_stop"
LIMIT_IDLE = "idle_check"


def read_settings(workspace_id):
    """The workspace's settings row, or an UNSAVED defaults instance.

    Read-only on purpose: the sweep and the stop path must not create the row,
    because a freshly created row's ``updated_at`` re-bases the idle check (see
    ``idle_base``) and would make the two disagree.
    """
    from plane.db.models import TimeTrackingSetting

    row = TimeTrackingSetting.all_objects.filter(workspace_id=workspace_id).first()
    return row or TimeTrackingSetting(workspace_id=workspace_id)


def settings_for(workspace_ids):
    """``read_settings`` for many workspaces in one query: {workspace_id: setting}."""
    from plane.db.models import TimeTrackingSetting

    ids = set(workspace_ids)
    found = {s.workspace_id: s for s in TimeTrackingSetting.all_objects.filter(workspace_id__in=ids)}
    return {wid: found.get(wid) or TimeTrackingSetting(workspace_id=wid) for wid in ids}


def idle_base(timer, setting):
    """When the current "still working?" interval started.

    ``max(started_at, last_confirmed_at)`` per the PO decision, and also the
    settings row's ``updated_at`` when the row exists: an admin turning the check
    on or shortening it must not retroactively stop timers that were never
    prompted under the new value. A settings edit can only delay a check by at
    most one interval, never bring one forward.
    """
    base = timer.started_at
    if timer.last_confirmed_at and timer.last_confirmed_at > base:
        base = timer.last_confirmed_at
    if setting.pk and setting.updated_at and setting.updated_at > base:
        base = setting.updated_at
    return base


def idle_check_enabled(setting):
    return (setting.idle_check_min_minutes or 0) > 0 and (setting.idle_check_max_minutes or 0) > 0


def idle_interval_minutes(timer, setting, base):
    """The (random but deterministic) minutes from ``base`` to this interval's check."""
    lo, hi = setting.idle_check_min_minutes, setting.idle_check_max_minutes
    digest = hmac.new(
        django_settings.SECRET_KEY.encode(), f"{timer.id}:{base.isoformat()}".encode(), hashlib.sha256
    ).digest()
    return lo + int.from_bytes(digest, "big") % (hi - lo + 1)


def schedule(timer, setting):
    """The limit timestamps for a running timer (all aware datetimes or None)."""
    enabled = idle_check_enabled(setting)
    next_check_at = check_deadline_at = None
    if enabled:
        base = idle_base(timer, setting)
        next_check_at = base + timedelta(minutes=idle_interval_minutes(timer, setting, base))
        check_deadline_at = next_check_at + IDLE_GRACE
    return {
        "remind_at": timer.started_at + timedelta(minutes=setting.remind_after_minutes),
        "auto_stop_at": timer.started_at + timedelta(minutes=setting.auto_stop_after_minutes),
        "idle_check_enabled": enabled,
        "next_check_at": next_check_at,
        "check_deadline_at": check_deadline_at,
    }


def cutoff(timer, setting, end):
    """Apply the limits to a requested stop time.

    Returns ``(stop_at, limit)``: ``limit`` is None (stop at ``end`` as asked),
    ``LIMIT_AUTO_STOP`` (``end`` is at/after the auto-stop time: stop there) or
    ``LIMIT_IDLE`` (``end`` is at/after the check deadline: stop at the prompt
    time). When both apply, the earlier stop point wins.
    """
    s = schedule(timer, setting)
    hits = []
    if end >= s["auto_stop_at"]:
        hits.append((s["auto_stop_at"], LIMIT_AUTO_STOP))
    if s["check_deadline_at"] is not None and end >= s["check_deadline_at"]:
        hits.append((s["next_check_at"], LIMIT_IDLE))
    if not hits:
        return end, None
    stop_at, limit = min(hits, key=lambda h: h[0])
    return max(stop_at, timer.started_at), limit


def due_at(timer, setting):
    """The earliest instant at which the sweep must stop this timer."""
    s = schedule(timer, setting)
    if s["check_deadline_at"] is None:
        return s["auto_stop_at"]
    return min(s["auto_stop_at"], s["check_deadline_at"])
