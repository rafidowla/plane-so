# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — PSR-136 — tell the user when the SYSTEM stopped their timer.

A timer stopped by the "still working?" idle check or the 14 h auto-stop used to leave no
trace except the saved entry. ``notify_limit_stop`` writes one in-app notification per such
stop (``sender="in_app:time_tracking:stopped"``, ``data.time_tracking.kind="timer_stopped"``);
``latest_stop_notice`` turns the newest unread one into the ``stop_notice`` block of the
running-timer payload, so the web app can show the "Timer stopped" card on the user's next
visit even if no tab was open at the time.
"""

# Python imports
import logging
from datetime import timedelta

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import Issue, Notification

logger = logging.getLogger("plane.worker")

STOPPED_SENDER = "in_app:time_tracking:stopped"
STOPPED_KIND = "timer_stopped"
# ``limits`` constant -> the reason string exposed to clients.
REASONS = {"idle_check": "idle_check", "auto_stop": "auto_stop"}
NOTICE_WINDOW = timedelta(hours=24)


def notify_limit_stop(timer, stopped):
    """One notification for a limit stop. ``stopped`` is ``stop_timer``'s result.

    Called by ``system_stop_timer`` only for a stop that really happened (a lost race returns
    before this) and only for the limit reason — NOT for ``issue_deleted`` /
    ``tracking_disabled`` / ``member_removed``: the work item may be gone or the user may have
    lost access to it, and the notification would link to it. Never raises: failing to notify
    must not undo the stop.
    """
    reason = REASONS.get(getattr(stopped, "limit", None))
    if reason is None:
        return None
    try:
        issue = (
            Issue.all_objects.filter(id=timer.issue_id)
            .values("id", "name", "sequence_id", "project__identifier", "state__name", "state__group")
            .first()
            or {}
        )
        stopped_at = stopped[-1].ended_at if stopped and stopped[-1].ended_at else timezone.now()
        minutes = sum(w.duration for w in stopped)
        notification = Notification(
            workspace_id=timer.workspace_id,
            project_id=timer.project_id,
            receiver_id=timer.user_id,
            triggered_by_id=None,
            entity_identifier=timer.issue_id,
            # "issue" keeps it in the web inbox list, which filters on entity_name.
            entity_name="issue",
            sender=STOPPED_SENDER,
            title="Your timer was stopped",
            data={
                "issue": {
                    "id": str(timer.issue_id),
                    "name": issue.get("name"),
                    "identifier": issue.get("project__identifier"),
                    "sequence_id": issue.get("sequence_id"),
                    "state_name": issue.get("state__name"),
                    "state_group": issue.get("state__group"),
                },
                "issue_activity": {
                    "id": None,
                    "verb": "stopped",
                    "field": "worklog_timer",
                    "actor": None,
                    "new_value": None,
                    "old_value": None,
                    "old_identifier": None,
                    "new_identifier": None,
                },
                "time_tracking": {
                    "kind": STOPPED_KIND,
                    "reason": reason,
                    "timer_id": str(timer.id),
                    "issue_id": str(timer.issue_id),
                    "started_at": timer.started_at.isoformat(),
                    "stopped_at": stopped_at.isoformat(),
                    "minutes": minutes,
                },
            },
        )
        # Savepoint: a DB error here must not poison the stop's transaction.
        with transaction.atomic():
            notification.save()
        return notification
    except Exception:
        logger.exception("time tracking: could not create the timer-stopped notification")
        return None


def latest_stop_notice(workspace_slug, user, now=None):
    """The ``stop_notice`` payload block: the user's newest UNREAD ``timer_stopped``
    notification in this workspace from the last 24 h, or None. The caller only asks when no
    timer is running."""
    from plane.utils.time_tracking.timer import issue_label

    now = now or timezone.now()
    notification = (
        Notification.objects.filter(
            workspace__slug=workspace_slug,
            receiver=user,
            sender=STOPPED_SENDER,
            read_at__isnull=True,
            created_at__gte=now - NOTICE_WINDOW,
        )
        .order_by("-created_at")
        .first()
    )
    if notification is None:
        return None
    tt = (notification.data or {}).get("time_tracking") or {}
    return {
        "notification_id": str(notification.id),
        "reason": tt.get("reason"),
        # None when the item was deleted/archived since — the card then just has no link.
        "issue": issue_label(notification.entity_identifier),
        "started_at": tt.get("started_at"),
        "stopped_at": tt.get("stopped_at"),
        "minutes": tt.get("minutes"),
    }


def mark_stop_notices_read(workspace_id, user_id):
    """Starting a timer settles every unread "timer stopped" notice for that user and workspace;
    otherwise a later manual stop would bring an old notice back as the stopped card."""
    Notification.objects.filter(
        workspace_id=workspace_id, receiver_id=user_id, sender=STOPPED_SENDER, read_at__isnull=True
    ).update(read_at=timezone.now())
