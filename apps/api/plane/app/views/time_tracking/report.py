# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import csv
import math
from datetime import datetime

# Django imports
from django.db.models import (
    Sum,
    Count,
    F,
    Q,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.permissions import ROLE, allow_permission
from plane.db.models import IssueWorklog, ResourceCapacity


# group_by -> (value fields, key field, name field)
GROUP_BY_MAP = {
    "resource": (["logged_by", "logged_by__display_name", "logged_by__email"], "logged_by", "logged_by__display_name"),
    "project": (["project", "project__name", "project__identifier"], "project", "project__name"),
    "client": (["project__client", "project__client__name"], "project__client", "project__client__name"),
}

# Billable amount = sum(duration_minutes * billable_rate / 60) over billable entries.
AMOUNT_EXPR = ExpressionWrapper(
    F("duration") * F("billable_rate") / 60.0,
    output_field=DecimalField(max_digits=16, decimal_places=2),
)


class TimeReportEndpoint(BaseAPIView):
    """Aggregated time report grouped by resource, project, or client.

    Query params: group_by (resource|project|client), start_date, end_date,
    project_ids (csv), client_id, user_ids (csv), billable (true|false),
    format (csv to download).
    """

    def _filtered_worklogs(self, slug, request):
        qs = IssueWorklog.objects.filter(workspace__slug=slug)
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        project_ids = request.GET.get("project_ids")
        client_id = request.GET.get("client_id")
        user_ids = request.GET.get("user_ids")
        billable = request.GET.get("billable")

        if start_date:
            qs = qs.filter(logged_date__gte=start_date)
        if end_date:
            qs = qs.filter(logged_date__lte=end_date)
        if project_ids:
            qs = qs.filter(project_id__in=[p for p in project_ids.split(",") if p])
        if client_id:
            qs = qs.filter(project__client_id=client_id)
        if user_ids:
            qs = qs.filter(logged_by_id__in=[u for u in user_ids.split(",") if u])
        if billable in ("true", "false"):
            qs = qs.filter(is_billable=(billable == "true"))
        return qs, start_date, end_date

    def _aggregate(self, qs, group_by):
        values, key_field, name_field = GROUP_BY_MAP[group_by]
        rows = (
            qs.values(*values)
            .annotate(
                total_minutes=Coalesce(Sum("duration"), 0),
                billable_minutes=Coalesce(Sum("duration", filter=Q(is_billable=True)), 0),
                billable_amount=Coalesce(
                    Sum(AMOUNT_EXPR, filter=Q(is_billable=True)),
                    0,
                    output_field=DecimalField(max_digits=16, decimal_places=2),
                ),
                entry_count=Count("id"),
            )
            .order_by("-total_minutes")
        )
        groups = []
        for r in rows:
            groups.append(
                {
                    "key": str(r[key_field]) if r[key_field] is not None else None,
                    "name": r.get(name_field) or "Unassigned",
                    "total_minutes": r["total_minutes"],
                    "billable_minutes": r["billable_minutes"],
                    "non_billable_minutes": r["total_minutes"] - r["billable_minutes"],
                    "billable_amount": float(r["billable_amount"] or 0),
                    "entry_count": r["entry_count"],
                }
            )
        return groups

    def _add_utilization(self, slug, groups, start_date, end_date):
        """For resource grouping, add expected minutes + utilization %."""
        if not (start_date and end_date):
            return groups
        try:
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return groups
        weeks = max(1, math.ceil(((d1 - d0).days + 1) / 7))
        capacities = {
            str(c.user_id): c.weekly_capacity
            for c in ResourceCapacity.objects.filter(workspace__slug=slug)
        }
        for g in groups:
            weekly = capacities.get(g["key"])
            if weekly:
                expected = weekly * weeks
                g["expected_minutes"] = expected
                g["utilization_pct"] = round((g["total_minutes"] / expected) * 100, 1) if expected else None
            else:
                g["expected_minutes"] = None
                g["utilization_pct"] = None
        return groups

    # Billing reports expose resource names, emails, and dollar amounts aggregated
    # across the entire workspace (including projects the requester may not belong
    # to). That is internal business data, not "totals a client should see", so
    # GUEST (client) role is excluded entirely.
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        group_by = request.GET.get("group_by", "resource")
        if group_by not in GROUP_BY_MAP:
            return Response(
                {"error": "group_by must be one of resource, project, client."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs, start_date, end_date = self._filtered_worklogs(slug, request)
        groups = self._aggregate(qs, group_by)
        if group_by == "resource":
            groups = self._add_utilization(slug, groups, start_date, end_date)

        totals = {
            "total_minutes": sum(g["total_minutes"] for g in groups),
            "billable_minutes": sum(g["billable_minutes"] for g in groups),
            "billable_amount": round(sum(g["billable_amount"] for g in groups), 2),
            "entry_count": sum(g["entry_count"] for g in groups),
        }

        if request.GET.get("format") == "csv":
            return self._csv_response(group_by, groups)

        return Response({"group_by": group_by, "groups": groups, "totals": totals}, status=status.HTTP_200_OK)

    def _csv_response(self, group_by, groups):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="time-report-by-{group_by}.csv"'
        writer = csv.writer(response)
        header = [group_by.capitalize(), "Total (h)", "Billable (h)", "Non-billable (h)", "Billable amount", "Entries"]
        if group_by == "resource":
            header += ["Expected (h)", "Utilization %"]
        writer.writerow(header)
        for g in groups:
            row = [
                g["name"],
                round(g["total_minutes"] / 60, 2),
                round(g["billable_minutes"] / 60, 2),
                round(g["non_billable_minutes"] / 60, 2),
                g["billable_amount"],
                g["entry_count"],
            ]
            if group_by == "resource":
                row += [
                    round(g["expected_minutes"] / 60, 2) if g.get("expected_minutes") else "",
                    g.get("utilization_pct") if g.get("utilization_pct") is not None else "",
                ]
            writer.writerow(row)
        return response
