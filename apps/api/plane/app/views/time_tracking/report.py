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
from plane.app.views.time_tracking.timesheet import is_workspace_admin
from plane.db.models import IssueWorklog, ProjectMember, ResourceCapacity


def _issue_extras(r):
    """Compose a disambiguated work-item label + the extra id/link fields the
    frontend needs. issue__name alone collides across (and within) projects."""
    ident, seq = r.get("project__identifier"), r.get("issue__sequence_id")
    name = f"{ident}-{seq} {r.get('issue__name') or ''}".strip() if ident and seq else (r.get("issue__name") or "Untitled")
    return {
        "name": name,
        "issue_id": str(r["issue"]) if r["issue"] else None,
        "sequence_id": seq,
        "project_id": str(r["project"]) if r["project"] else None,
        "project_identifier": ident,
        "project_name": r.get("project__name"),
    }


# group_by -> (value fields, key field, name field, [optional extras builder])
GROUP_BY_MAP = {
    "resource": (["logged_by", "logged_by__display_name", "logged_by__email"], "logged_by", "logged_by__display_name"),
    "project": (["project", "project__name", "project__identifier"], "project", "project__name"),
    "client": (["project__client", "project__client__name"], "project__client", "project__client__name"),
    "issue": (
        ["issue", "issue__name", "issue__sequence_id", "project", "project__identifier", "project__name"],
        "issue",
        "issue__name",
        _issue_extras,
    ),
}

# Hard cap on the number of issue-grouped rows returned for the JSON response.
# CSV export (an explicit, deliberate action) is never capped.
MAX_ISSUE_GROUPS = 200

# Billable amount = sum(duration_minutes * billable_rate / 60) over billable entries.
AMOUNT_EXPR = ExpressionWrapper(
    F("duration") * F("billable_rate") / 60.0,
    output_field=DecimalField(max_digits=16, decimal_places=2),
)


class TimeReportEndpoint(BaseAPIView):
    """Aggregated time report grouped by resource, project, client, or issue.

    Query params: group_by (resource|project|client|issue), start_date,
    end_date, project_ids (csv), client_id, user_ids (csv),
    billable (true|false), export (csv to download).

    group_by=issue is capped at MAX_ISSUE_GROUPS rows for JSON (the response
    carries "truncated"/"limit" so a caller can tell); CSV export is uncapped.
    It's also scoped to the caller's visible projects unless they're a
    workspace admin, since work-item titles are project content, unlike the
    workspace-level resource/project/client metadata the other groupings use.
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

    def _aggregate(self, qs, group_by, limit=None):
        values, key_field, name_field, *rest = GROUP_BY_MAP[group_by]
        extras_fn = rest[0] if rest else None
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
        # Rows are already ordered by -total_minutes, so a limit keeps the most
        # time-significant groups rather than an arbitrary prefix.
        truncated = False
        if limit:
            rows = list(rows[: limit + 1])
            truncated = len(rows) > limit
            rows = rows[:limit]
        groups = []
        for r in rows:
            group = {
                "key": str(r[key_field]) if r[key_field] is not None else None,
                "name": r.get(name_field) or "Unassigned",
                "total_minutes": r["total_minutes"],
                "billable_minutes": r["billable_minutes"],
                "non_billable_minutes": r["total_minutes"] - r["billable_minutes"],
                "billable_amount": float(r["billable_amount"] or 0),
                "entry_count": r["entry_count"],
            }
            if extras_fn:
                group.update(extras_fn(r))
            groups.append(group)
        return groups, truncated

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
                {"error": f"group_by must be one of {', '.join(GROUP_BY_MAP)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs, start_date, end_date = self._filtered_worklogs(slug, request)

        # Work item titles are project content, gated everywhere else in Plane by
        # ProjectMember — unlike resource/project/client names, which are
        # workspace-level metadata every workspace MEMBER already sees (see the
        # comment above). A workspace MEMBER who isn't a member of project X must
        # not learn project X's task titles via group_by=issue. Scoped to `issue`
        # only so no other grouping's behavior/tests move.
        if group_by == "issue" and not is_workspace_admin(slug, request.user):
            visible_project_ids = ProjectMember.objects.filter(
                workspace__slug=slug,
                member=request.user,
                is_active=True,
                role__in=[ROLE.ADMIN.value, ROLE.MEMBER.value],
            ).values_list("project_id", flat=True)
            qs = qs.filter(project_id__in=visible_project_ids)

        # Not "format" — that's DRF's reserved content-negotiation query param
        # (DefaultContentNegotiation.select_renderer reads it before this view's
        # get() even runs); since only JSONRenderer is registered, "?format=csv"
        # raises Http404 deep inside DRF before this line is ever reached.
        is_csv = request.GET.get("export") == "csv"
        limit = MAX_ISSUE_GROUPS if (group_by == "issue" and not is_csv) else None
        groups, truncated = self._aggregate(qs, group_by, limit=limit)
        if group_by == "resource":
            groups = self._add_utilization(slug, groups, start_date, end_date)

        # Computed from the unsliced queryset, not by summing `groups` — summing
        # `groups` would silently under-report the grand total the moment any
        # grouping (issue) can be truncated.
        agg = qs.aggregate(
            total_minutes=Coalesce(Sum("duration"), 0),
            billable_minutes=Coalesce(Sum("duration", filter=Q(is_billable=True)), 0),
            billable_amount=Coalesce(
                Sum(AMOUNT_EXPR, filter=Q(is_billable=True)),
                0,
                output_field=DecimalField(max_digits=16, decimal_places=2),
            ),
            entry_count=Count("id"),
        )
        totals = {
            "total_minutes": agg["total_minutes"],
            "billable_minutes": agg["billable_minutes"],
            "billable_amount": round(float(agg["billable_amount"] or 0), 2),
            "entry_count": agg["entry_count"],
        }

        if is_csv:
            return self._csv_response(group_by, groups)

        return Response(
            {
                "group_by": group_by,
                "groups": groups,
                "totals": totals,
                "truncated": truncated,
                "limit": limit,
            },
            status=status.HTTP_200_OK,
        )

    def _csv_response(self, group_by, groups):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="time-report-by-{group_by}.csv"'
        writer = csv.writer(response)
        label = "Work item" if group_by == "issue" else group_by.capitalize()
        header = [label, "Total (h)", "Billable (h)", "Non-billable (h)", "Billable amount", "Entries"]
        if group_by == "resource":
            header += ["Expected (h)", "Utilization %"]
        if group_by == "issue":
            header.insert(1, "Project")
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
            if group_by == "issue":
                row.insert(1, g.get("project_name") or "")
            writer.writerow(row)
        return response
