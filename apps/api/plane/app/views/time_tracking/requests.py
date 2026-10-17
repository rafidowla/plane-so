# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S3 — time-entry requests, approvals and their append-only history.

* ``GET/POST time/requests/`` — list (``scope=mine|review``) / submit.
* ``POST time/requests/<id>/approve|reject|withdraw/``.
* ``GET time/requests/<id>/history/`` and ``GET time/requests/events/``.

Logic lives in ``plane.utils.time_tracking.entry_requests``; guests are refused by the
workspace-level permission (they never track time).
"""

# Python imports
import uuid
from datetime import timedelta

# Django imports
from django.utils.dateparse import parse_date

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    TimeEntryRequest,
    TimeEntryRequestAction,
    TimeEntryRequestEvent,
    TimeEntryRequestStatus,
    TimeEntryRequestType,
    Workspace,
)
from plane.utils.time_tracking import approvers, entry_requests
from plane.utils.time_tracking.timer import TimeTrackingError
from plane.utils.time_tracking.tz import day_start, workspace_tz

MAX_LIMIT = 200


def _workspace_id(slug):
    return Workspace.objects.only("id").get(slug=slug).id


def _bad(error, code="invalid_filter"):
    return Response({"error": error, "code": code}, status=status.HTTP_400_BAD_REQUEST)


def _uuid_param(request, name):
    """``(value, error_response)`` for an optional uuid query param."""
    raw = request.query_params.get(name)
    if not raw:
        return None, None
    try:
        return uuid.UUID(raw), None
    except ValueError:
        return None, _bad(f"{name} is not a valid id.")


def _choices_param(request, name, choices):
    raw = request.query_params.get(name)
    if not raw:
        return None, None
    values = [v.strip() for v in raw.split(",") if v.strip()]
    unknown = [v for v in values if v not in choices]
    if unknown:
        return None, _bad(f"Unknown {name}: {', '.join(unknown)}.")
    return values, None


def _page(request):
    try:
        limit = min(max(int(request.query_params.get("limit", 50)), 1), MAX_LIMIT)
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except ValueError:
        return None, None, _bad("limit and offset must be numbers.")
    return limit, offset, None


def _scoped(request, workspace_id, default="mine"):
    """Requests in the caller's ``scope`` (``mine`` or ``review``) + user/project filters."""
    scope = request.query_params.get("scope") or default
    if scope not in ("mine", "review"):
        return None, _bad("scope must be mine or review.")
    qs = TimeEntryRequest.objects.filter(workspace_id=workspace_id)
    if scope == "mine":
        qs = qs.filter(requester_id=request.user.id)
    else:
        qs = qs.filter(approvers.review_scope_q(workspace_id, request.user.id))
    user_id, err = _uuid_param(request, "user_id")
    if err:
        return None, err
    project_id, err = _uuid_param(request, "project_id")
    if err:
        return None, err
    if user_id:
        qs = qs.filter(requester_id=user_id)
    if project_id:
        qs = qs.filter(project_id=project_id)
    return qs, None


def _error(exc):
    return Response(exc.payload, status=exc.status_code)


class TimeEntryRequestEndpoint(BaseAPIView):
    """``GET ?scope=mine|review&status&type&user_id&project_id&limit&offset`` · ``POST`` submit."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace_id = _workspace_id(slug)
        qs, err = _scoped(request, workspace_id)
        if err:
            return err
        statuses, err = _choices_param(request, "status", TimeEntryRequestStatus.values)
        if err:
            return err
        types, err = _choices_param(request, "type", TimeEntryRequestType.values)
        if err:
            return err
        limit, offset, err = _page(request)
        if err:
            return err
        if statuses:
            qs = qs.filter(status__in=statuses)
        if types:
            qs = qs.filter(type__in=types)
        qs = qs.select_related("requester", "reviewer").order_by("-created_at")
        count = qs.count()
        rows = list(qs[offset : offset + limit])
        return Response(
            {"count": count, "results": [entry_requests.serialize(r, request.user) for r in rows]},
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug):
        try:
            req = entry_requests.submit(_workspace_id(slug), request.user, request.data)
        except TimeTrackingError as exc:
            return _error(exc)
        req = TimeEntryRequest.objects.select_related("requester", "reviewer").get(pk=req.pk)
        return Response(entry_requests.serialize(req, request.user), status=status.HTTP_201_CREATED)


class _ReviewEndpoint(BaseAPIView):
    def respond(self, request, func, *args):
        try:
            req = func(*args)
        except TimeTrackingError as exc:
            return _error(exc)
        req = TimeEntryRequest.objects.select_related("requester", "reviewer").get(pk=req.pk)
        return Response(entry_requests.serialize(req, request.user), status=status.HTTP_200_OK)


class TimeEntryRequestApproveEndpoint(_ReviewEndpoint):
    """``POST {note?, new_ended_at?}`` — ``new_ended_at`` only for ``auto_stop_review`` (trims the session)."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug, pk):
        return self.respond(
            request,
            entry_requests.approve,
            _workspace_id(slug),
            request.user,
            pk,
            request.data.get("note"),
            request.data.get("new_ended_at"),
        )


class TimeEntryRequestRejectEndpoint(_ReviewEndpoint):
    """``POST {note}`` — the note is required."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug, pk):
        return self.respond(
            request, entry_requests.reject, _workspace_id(slug), request.user, pk, request.data.get("note")
        )


class TimeEntryRequestWithdrawEndpoint(_ReviewEndpoint):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug, pk):
        return self.respond(request, entry_requests.withdraw, _workspace_id(slug), request.user, pk)


class TimeEntryRequestHistoryEndpoint(BaseAPIView):
    """The request and its events, oldest first. Requester, its approvers and workspace admins."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, pk):
        req = (
            TimeEntryRequest.objects.select_related("requester", "reviewer")
            .filter(pk=pk, workspace_id=_workspace_id(slug))
            .first()
        )
        if req is None or not entry_requests.visible(req, request.user):
            return Response({"error": "Request not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        events = TimeEntryRequestEvent.objects.filter(request=req).select_related("actor").order_by("created_at")
        return Response(
            {
                "request": entry_requests.serialize(req, request.user),
                "events": [entry_requests.serialize_event(e) for e in events],
            },
            status=status.HTTP_200_OK,
        )


class TimeEntryRequestEventsEndpoint(BaseAPIView):
    """``GET ?scope=review|mine&start_date&end_date&user_id&project_id&action&limit&offset`` — newest first.

    Default scope is ``review`` (the requests the caller may approve). Dates are workspace-local
    days on the event time, both inclusive.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace_id = _workspace_id(slug)
        qs, err = _scoped(request, workspace_id, default="review")
        if err:
            return err
        actions, err = _choices_param(request, "action", TimeEntryRequestAction.values)
        if err:
            return err
        limit, offset, err = _page(request)
        if err:
            return err
        events = TimeEntryRequestEvent.objects.filter(request__in=qs.values("id"))
        tz = workspace_tz(workspace_id)
        for name, op in (("start_date", "gte"), ("end_date", "lt")):
            raw = request.query_params.get(name)
            if not raw:
                continue
            day = parse_date(raw)
            if day is None:
                return _bad(f"{name} must be YYYY-MM-DD.")
            if name == "end_date":
                day = day + timedelta(days=1)
            events = events.filter(**{f"created_at__{op}": day_start(day, tz)})
        if actions:
            events = events.filter(action__in=actions)
        events = events.select_related("actor", "request").order_by("-created_at")
        count = events.count()
        rows = list(events[offset : offset + limit])
        return Response(
            {"count": count, "results": [entry_requests.serialize_event(e, with_request=True) for e in rows]},
            status=status.HTTP_200_OK,
        )
