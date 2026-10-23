# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 holidays/leave (PSR-122) — holiday calendars, holidays and the
per-person calendar assignment. Everything is under ``/api/workspaces/<slug>/time/``.

* ``holiday-calendars/`` — ``GET`` (members + admins), ``POST`` (admins). The first calendar
  of a workspace is always the default.
* ``holiday-calendars/<id>/`` — ``PATCH`` / ``DELETE`` (admins). One default per workspace;
  the default can't be un-defaulted directly, nor deleted while other calendars exist.
* ``holiday-calendars/<id>/holidays/`` — ``GET`` (``?year=``) / ``POST``;
  ``.../holidays/<hid>/`` — ``PATCH`` / ``DELETE``.
* ``members/<user_id>/holiday-calendar/`` — ``PUT {calendar_id | null}`` (null = the default).

Holidays only reduce Expected hours (``plane.utils.time_tracking.days_off``). Every write is
audited. Guests get 403 everywhere; errors are ``{"error", "code"}`` (``TimeTrackingError``).
"""

# Python imports
import uuid
from datetime import date

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    DayPortion,
    Holiday,
    HolidayCalendar,
    MemberHolidayCalendar,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.timer import TimeTrackingError

MAX_CALENDAR_NAME = 100
MAX_HOLIDAY_NAME = 200


def refuse(code, error, status_code=status.HTTP_400_BAD_REQUEST):
    return TimeTrackingError({"error": error, "code": code}, status_code)


def not_found(what="Holiday calendar"):
    return refuse("not_found", f"{what} not found.", status.HTTP_404_NOT_FOUND)


def parse_date(value, field):
    """A ``YYYY-MM-DD`` string -> date (anything else is a 400 ``invalid_date``)."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise refuse("invalid_date", f"{field} must be a date (YYYY-MM-DD).")


def parse_portion(value):
    if value in (DayPortion.FULL.value, DayPortion.HALF.value):
        return value
    raise refuse("invalid_portion", 'portion must be "full" or "half".')


def parse_name(value, max_length, field="name"):
    if not isinstance(value, str) or not value.strip():
        raise refuse("invalid_name", f"{field} is required.")
    value = value.strip()
    if len(value) > max_length:
        raise refuse("invalid_name", f"{field} must be at most {max_length} characters.")
    return value


def soft_delete(queryset):
    """Soft-delete a queryset in one UPDATE (no instance ``delete()`` -> no celery cascade)."""
    return queryset.delete()


def is_internal_member(workspace_id, user_id):
    return WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
    ).exists()


def calendar_state(calendar):
    return {"name": calendar.name, "is_default": calendar.is_default}


def holiday_state(holiday):
    return {
        "calendar_id": str(holiday.calendar_id),
        "date": holiday.date.isoformat(),
        "name": holiday.name,
        "portion": holiday.portion,
    }


def calendar_out(calendar, member_count=0):
    return {
        "id": str(calendar.id),
        "name": calendar.name,
        "is_default": calendar.is_default,
        "member_count": member_count,
    }


def holiday_out(holiday):
    return {"id": str(holiday.id), "date": holiday.date, "name": holiday.name, "portion": holiday.portion}


def member_counts(workspace_id):
    """``{calendar_id: people}`` — explicit assignments, plus (for the default) everyone unassigned."""
    internal = set(
        WorkspaceMember.objects.filter(
            workspace_id=workspace_id, is_active=True, role__gte=ROLE.MEMBER.value
        ).values_list("member_id", flat=True)
    )
    counts = {}
    assigned = set()
    for cid, uid in MemberHolidayCalendar.objects.filter(
        workspace_id=workspace_id, calendar__deleted_at__isnull=True, user_id__in=internal
    ).values_list("calendar_id", "user_id"):
        counts[cid] = counts.get(cid, 0) + 1
        assigned.add(uid)
    default_id = (
        HolidayCalendar.objects.filter(workspace_id=workspace_id, is_default=True).values_list("id", flat=True).first()
    )
    if default_id:
        counts[default_id] = counts.get(default_id, 0) + len(internal - assigned)
    return counts


def lock_workspace(workspace_id):
    """Serialize calendar writes of one workspace (default switching, first-calendar rule)."""
    return Workspace.objects.select_for_update().only("id").get(pk=workspace_id)


def name_taken(workspace_id, name, exclude_id=None):
    qs = HolidayCalendar.objects.filter(workspace_id=workspace_id, name__iexact=name)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


class HolidayCalendarEndpoint(BaseAPIView):
    """``GET`` / ``POST holiday-calendars/``."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.only("id").get(slug=slug)
        counts = member_counts(workspace.id)
        calendars = HolidayCalendar.objects.filter(workspace_id=workspace.id).order_by("-is_default", "name", "id")
        return Response([calendar_out(c, counts.get(c.id, 0)) for c in calendars], status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            name = parse_name(request.data.get("name"), MAX_CALENDAR_NAME)
            want_default = request.data.get("is_default", False)
            if not isinstance(want_default, bool):
                raise refuse("invalid_default", "is_default must be true or false.")
            with transaction.atomic():
                lock_workspace(workspace.id)
                if name_taken(workspace.id, name):
                    raise refuse("duplicate_name", "A holiday calendar with this name already exists.")
                others = list(HolidayCalendar.objects.filter(workspace_id=workspace.id, is_default=True))
                make_default = want_default or not HolidayCalendar.objects.filter(workspace_id=workspace.id).exists()
                if make_default:
                    for other in others:
                        before = calendar_state(other)
                        other.is_default = False
                        other.save(update_fields=["is_default", "updated_at"])
                        audit.record(
                            workspace.id,
                            request.user,
                            TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
                            other.id,
                            TimeTrackingAuditAction.UPDATE,
                            before=before,
                            after=calendar_state(other),
                        )
                calendar = HolidayCalendar(workspace_id=workspace.id, name=name, is_default=make_default)
                calendar.save(created_by_id=request.user.id)
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
                    calendar.id,
                    TimeTrackingAuditAction.CREATE,
                    after=calendar_state(calendar),
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(calendar_out(calendar, 0), status=status.HTTP_201_CREATED)


class HolidayCalendarDetailEndpoint(BaseAPIView):
    """``PATCH`` / ``DELETE holiday-calendars/<id>/``."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, calendar_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                lock_workspace(workspace.id)
                calendar = HolidayCalendar.objects.filter(workspace_id=workspace.id, pk=calendar_id).first()
                if calendar is None:
                    raise not_found()
                before = calendar_state(calendar)
                if "name" in request.data:
                    name = parse_name(request.data.get("name"), MAX_CALENDAR_NAME)
                    if name_taken(workspace.id, name, exclude_id=calendar.id):
                        raise refuse("duplicate_name", "A holiday calendar with this name already exists.")
                    calendar.name = name
                if "is_default" in request.data:
                    flag = request.data.get("is_default")
                    if not isinstance(flag, bool):
                        raise refuse("invalid_default", "is_default must be true or false.")
                    if flag and not calendar.is_default:
                        for other in HolidayCalendar.objects.filter(workspace_id=workspace.id, is_default=True):
                            other_before = calendar_state(other)
                            other.is_default = False
                            other.save(update_fields=["is_default", "updated_at"])
                            audit.record(
                                workspace.id,
                                request.user,
                                TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
                                other.id,
                                TimeTrackingAuditAction.UPDATE,
                                before=other_before,
                                after=calendar_state(other),
                            )
                        calendar.is_default = True
                    elif not flag and calendar.is_default:
                        raise refuse(
                            "default_required",
                            "A workspace needs a default calendar: make another calendar the default instead.",
                        )
                after = calendar_state(calendar)
                if after != before:
                    calendar.save()
                    audit.record(
                        workspace.id,
                        request.user,
                        TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
                        calendar.id,
                        TimeTrackingAuditAction.UPDATE,
                        before=before,
                        after=after,
                    )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(calendar_out(calendar, member_counts(workspace.id).get(calendar.id, 0)), status=200)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, calendar_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                lock_workspace(workspace.id)
                calendar = HolidayCalendar.objects.filter(workspace_id=workspace.id, pk=calendar_id).first()
                if calendar is None:
                    raise not_found()
                if (
                    calendar.is_default
                    and HolidayCalendar.objects.filter(workspace_id=workspace.id).exclude(pk=calendar.pk).exists()
                ):
                    raise refuse(
                        "default_calendar",
                        "The default calendar can't be deleted while other calendars exist. "
                        "Make another one the default first.",
                    )
                before = {
                    **calendar_state(calendar),
                    "holidays": Holiday.objects.filter(calendar=calendar).count(),
                    "assigned_members": MemberHolidayCalendar.objects.filter(calendar=calendar).count(),
                }
                # Its holidays and assignments go with it (those people fall back to the default).
                soft_delete(Holiday.objects.filter(calendar=calendar))
                soft_delete(MemberHolidayCalendar.objects.filter(calendar=calendar))
                soft_delete(HolidayCalendar.objects.filter(pk=calendar.pk))
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.HOLIDAY_CALENDAR,
                    calendar.id,
                    TimeTrackingAuditAction.DELETE,
                    before=before,
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HolidayEndpoint(BaseAPIView):
    """``GET`` (``?year=YYYY``) / ``POST holiday-calendars/<id>/holidays/``."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, calendar_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        calendar = HolidayCalendar.objects.filter(workspace_id=workspace.id, pk=calendar_id).first()
        if calendar is None:
            return Response(not_found().payload, status=status.HTTP_404_NOT_FOUND)
        rows = Holiday.objects.filter(calendar=calendar)
        year = request.query_params.get("year")
        if year not in (None, ""):
            try:
                year = int(year)
                rows = rows.filter(date__gte=date(year, 1, 1), date__lte=date(year, 12, 31))
            except (TypeError, ValueError):
                return Response(
                    refuse("invalid_year", "year must be a 4-digit year.").payload, status=status.HTTP_400_BAD_REQUEST
                )
        return Response([holiday_out(h) for h in rows.order_by("date", "id")], status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, calendar_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                calendar = (
                    HolidayCalendar.objects.select_for_update()
                    .filter(workspace_id=workspace.id, pk=calendar_id)
                    .first()
                )
                if calendar is None:
                    raise not_found()
                day = parse_date(request.data.get("date"), "date")
                name = parse_name(request.data.get("name"), MAX_HOLIDAY_NAME)
                portion = parse_portion(request.data.get("portion", DayPortion.FULL.value))
                if Holiday.objects.filter(calendar=calendar, date=day).exists():
                    raise refuse("duplicate_date", "This calendar already has a holiday on that date.")
                holiday = Holiday(calendar=calendar, date=day, name=name, portion=portion)
                holiday.save(created_by_id=request.user.id)
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.HOLIDAY,
                    holiday.id,
                    TimeTrackingAuditAction.CREATE,
                    after=holiday_state(holiday),
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(holiday_out(holiday), status=status.HTTP_201_CREATED)


class HolidayDetailEndpoint(BaseAPIView):
    """``PATCH`` / ``DELETE holiday-calendars/<id>/holidays/<hid>/``."""

    @staticmethod
    def _holiday(workspace_id, calendar_id, holiday_id, lock=False):
        qs = Holiday.objects.filter(
            pk=holiday_id,
            calendar_id=calendar_id,
            calendar__workspace_id=workspace_id,
            calendar__deleted_at__isnull=True,
        )
        if lock:
            qs = qs.select_for_update()
        holiday = qs.first()
        if holiday is None:
            raise not_found("Holiday")
        return holiday

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, calendar_id, holiday_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                holiday = self._holiday(workspace.id, calendar_id, holiday_id, lock=True)
                before = holiday_state(holiday)
                if "date" in request.data:
                    day = parse_date(request.data.get("date"), "date")
                    if (
                        day != holiday.date
                        and Holiday.objects.filter(calendar_id=calendar_id, date=day).exclude(pk=holiday.pk).exists()
                    ):
                        raise refuse("duplicate_date", "This calendar already has a holiday on that date.")
                    holiday.date = day
                if "name" in request.data:
                    holiday.name = parse_name(request.data.get("name"), MAX_HOLIDAY_NAME)
                if "portion" in request.data:
                    holiday.portion = parse_portion(request.data.get("portion"))
                after = holiday_state(holiday)
                if after != before:
                    holiday.save()
                    audit.record(
                        workspace.id,
                        request.user,
                        TimeTrackingAuditTarget.HOLIDAY,
                        holiday.id,
                        TimeTrackingAuditAction.UPDATE,
                        before=before,
                        after=after,
                    )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(holiday_out(holiday), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, calendar_id, holiday_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            with transaction.atomic():
                holiday = self._holiday(workspace.id, calendar_id, holiday_id, lock=True)
                before = holiday_state(holiday)
                soft_delete(Holiday.objects.filter(pk=holiday.pk))
                audit.record(
                    workspace.id,
                    request.user,
                    TimeTrackingAuditTarget.HOLIDAY,
                    holiday.id,
                    TimeTrackingAuditAction.DELETE,
                    before=before,
                )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberHolidayCalendarEndpoint(BaseAPIView):
    """``PUT members/<user_id>/holiday-calendar/`` ``{calendar_id: uuid | null}`` (admins).

    ``null`` clears the assignment: the person uses the workspace default calendar."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, user_id):
        workspace = Workspace.objects.only("id").get(slug=slug)
        try:
            if "calendar_id" not in request.data:
                raise refuse("invalid_calendar", "calendar_id is required (a calendar id, or null for the default).")
            raw = request.data.get("calendar_id")
            if not is_internal_member(workspace.id, user_id):
                raise refuse("not_a_member", "Only active workspace members and admins have a holiday calendar.")
            with transaction.atomic():
                calendar = None
                if raw is not None:
                    try:
                        calendar_pk = uuid.UUID(str(raw))
                    except ValueError:
                        calendar_pk = None
                    calendar = (
                        HolidayCalendar.objects.filter(workspace_id=workspace.id, pk=calendar_pk).first()
                        if calendar_pk
                        else None
                    )
                    if calendar is None:
                        raise refuse("invalid_calendar", "calendar_id must be a holiday calendar of this workspace.")
                row = (
                    MemberHolidayCalendar.objects.select_for_update()
                    .filter(workspace_id=workspace.id, user_id=user_id)
                    .first()
                )
                before = None if row is None else {"user_id": str(user_id), "calendar_id": str(row.calendar_id)}
                if calendar is None:
                    if row is not None:
                        soft_delete(MemberHolidayCalendar.objects.filter(pk=row.pk))
                        audit.record(
                            workspace.id,
                            request.user,
                            TimeTrackingAuditTarget.MEMBER_CALENDAR,
                            row.id,
                            TimeTrackingAuditAction.DELETE,
                            before=before,
                        )
                else:
                    after = {"user_id": str(user_id), "calendar_id": str(calendar.id)}
                    if row is None:
                        row = MemberHolidayCalendar(workspace_id=workspace.id, user_id=user_id, calendar=calendar)
                        row.save(created_by_id=request.user.id)
                        audit.record(
                            workspace.id,
                            request.user,
                            TimeTrackingAuditTarget.MEMBER_CALENDAR,
                            row.id,
                            TimeTrackingAuditAction.CREATE,
                            after=after,
                        )
                    elif row.calendar_id != calendar.id:
                        row.calendar = calendar
                        row.save()
                        audit.record(
                            workspace.id,
                            request.user,
                            TimeTrackingAuditTarget.MEMBER_CALENDAR,
                            row.id,
                            TimeTrackingAuditAction.UPDATE,
                            before=before,
                            after=after,
                        )
        except TimeTrackingError as exc:
            return Response(exc.payload, status=exc.status_code)
        return Response(
            {"user_id": str(user_id), "calendar_id": None if calendar is None else str(calendar.id)},
            status=status.HTTP_200_OK,
        )
