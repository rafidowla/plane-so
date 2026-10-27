# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- read-only workspace audit log for Super Admins.

One timeline from two append-only tables: ``WorkspaceAuditLog`` (workspace, member, invite,
webhook, delete and restore actions) and the Time and Renewals settings history already kept
in ``TimeTrackingAuditLog`` (worklog and estimate rows are left out -- they are day-to-day
work, not settings). Paged newest first with a keyset cursor on (created_at, id).
"""

# Python imports
import uuid

# Django imports
from django.db.models import Q
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_workspace_super_admin
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    TimeTrackingAuditAction,
    TimeTrackingAuditLog,
    TimeTrackingAuditTarget,
    Workspace,
    WorkspaceAuditAction,
    WorkspaceAuditLog,
)
from plane.utils import workspace_audit

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

SETTING_TARGETS = (
    TimeTrackingAuditTarget.SETTING,
    TimeTrackingAuditTarget.MEMBER_STATUS,
    TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
    TimeTrackingAuditTarget.HOLIDAY,
    TimeTrackingAuditTarget.LEAVE,
    TimeTrackingAuditTarget.MEMBER_CALENDAR,
)

_TT_VERBS = {
    TimeTrackingAuditAction.CREATE: "added",
    TimeTrackingAuditAction.UPDATE: "changed",
    TimeTrackingAuditAction.DELETE: "removed",
}

SOURCES = ("workspace", "time_tracking")


def _cursor(row):
    return f"{row['created_at'].isoformat()}|{row['id']}"


def _parse_cursor(raw):
    created_at, sep, row_id = str(raw).rpartition("|")
    if not sep:
        raise ValueError("bad cursor")
    parsed = parse_datetime(created_at)
    if parsed is None:
        raise ValueError("bad cursor")
    return parsed, uuid.UUID(row_id)


def _before(queryset, cursor):
    if cursor is None:
        return queryset
    created_at, row_id = cursor
    return queryset.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id))


def _actor(entry_actor, name, email):
    if entry_actor is None and not (name or email):
        return None
    return {
        "id": str(entry_actor.id) if entry_actor is not None else None,
        "name": name or workspace_audit.user_label(entry_actor),
        "email": email or (entry_actor.email if entry_actor is not None else ""),
    }


def _workspace_row(entry):
    return {
        "id": str(entry.id),
        "source": "workspace",
        "created_at": entry.created_at,
        "actor": _actor(entry.actor, entry.actor_name, entry.actor_email),
        "action": entry.action,
        "action_label": WorkspaceAuditAction(entry.action).label,
        "target_type": entry.target_type,
        "target_id": str(entry.target_id) if entry.target_id else None,
        "target_label": entry.target_label,
        "before": entry.before,
        "after": entry.after,
    }


def _tt_label(entry):
    for snapshot in (entry.after, entry.before):
        if isinstance(snapshot, dict):
            for key in ("name", "title", "date"):
                if snapshot.get(key):
                    return str(snapshot[key])
    return ""


def _time_tracking_row(entry):
    target = TimeTrackingAuditTarget(entry.target_type).label
    verb = _TT_VERBS.get(entry.action)
    label = f"{target} {verb}" if verb else f"{target}: {TimeTrackingAuditAction(entry.action).label}"
    return {
        "id": str(entry.id),
        "source": "time_tracking",
        "created_at": entry.created_at,
        "actor": _actor(entry.actor, "", ""),
        "action": f"time_tracking.{entry.target_type}.{entry.action}",
        "action_label": f"Time and Renewals: {label}",
        "target_type": entry.target_type,
        "target_id": str(entry.target_id) if entry.target_id else None,
        "target_label": _tt_label(entry),
        "before": entry.before,
        "after": entry.after,
    }


class WorkspaceAuditLogEndpoint(BaseAPIView):
    @allow_workspace_super_admin
    def get(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        try:
            limit = int(request.GET.get("limit", DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        raw_cursor = request.GET.get("cursor")
        try:
            cursor = _parse_cursor(raw_cursor) if raw_cursor else None
        except (TypeError, ValueError):
            return Response({"error": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST)

        source = request.GET.get("source")
        if source and source not in SOURCES:
            return Response({"error": "Invalid source."}, status=status.HTTP_400_BAD_REQUEST)

        rows = []
        if source in (None, "", "workspace"):
            entries = _before(
                WorkspaceAuditLog.objects.filter(workspace_id=workspace.id).select_related("actor"), cursor
            ).order_by("-created_at", "-id")[: limit + 1]
            rows.extend(_workspace_row(e) for e in entries)
        if source in (None, "", "time_tracking"):
            entries = _before(
                TimeTrackingAuditLog.objects.filter(
                    workspace_id=workspace.id, target_type__in=SETTING_TARGETS
                ).select_related("actor"),
                cursor,
            ).order_by("-created_at", "-id")[: limit + 1]
            rows.extend(_time_tracking_row(e) for e in entries)

        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return Response(
            {
                "results": rows,
                "next_cursor": _cursor(rows[-1]) if has_more and rows else None,
            },
            status=status.HTTP_200_OK,
        )
