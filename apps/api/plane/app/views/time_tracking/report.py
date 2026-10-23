# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import csv
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

# Django imports
from django.db.models import (
    Sum,
    Count,
    Max,
    F,
    Q,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.permissions import ROLE, allow_permission
from plane.db.models import IssueWorklog, User, Workspace, WorkspaceMember, WorklogSource, WorklogTimer
from plane.utils.time_tracking.limits import read_settings
from plane.utils.time_tracking.reporting import (
    MAX_RANGE_DAYS,
    SCOPE_SELF,
    candidate_user_ids,
    date_range,
    default_weekly_minutes,
    expected_calendar,
    is_working_day,
    minutes,
    resolve_scope,
    tracked_days_many,
)
from plane.utils.time_tracking.tz import day_start, local_today, zone
from plane.utils.time_tracking.visibility import OTHER_PROJECTS_LABEL


_FORMULA_LEAD_CHARS = ("=", "+", "-", "@", "\t", "\r")

# FORK: time-tracking-v2 S5 — list limits (DESIGN: every list is bounded).
MAX_REPORT_PEOPLE = 500
DEFAULT_TASK_LIMIT = 200
MAX_TASK_LIMIT = 500
# The entry CSV is a deliberate export but still bounded; ask for a shorter range.
MAX_CSV_ROWS = 100_000


def _csv_safe(value):
    """Neutralize spreadsheet formula injection ("CSV injection").

    Every string cell here ultimately comes from user-editable content (issue
    titles, display names, project/client names) — Excel/Sheets evaluates any
    cell starting with =, +, -, or @ as a formula on open, which can range from
    a DDE/RCE payload in older Excel to a data-exfiltrating =HYPERLINK(). A
    leading apostrophe is the standard mitigation: every major spreadsheet app
    then renders the cell as literal text instead of evaluating it.
    """
    text = str(value) if value is not None else ""
    if text.startswith(_FORMULA_LEAD_CHARS):
        return "'" + text
    return text


def _refuse(code, error, status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": error, "code": code}, status=status_code)


def _live_issues(issue_ids):
    """FORK: time-tracking-v2 — {issue_id: (name, sequence_id, deleted_at)} for the
    work items still in the DB (soft-deleted included). Worklogs outlive their work
    item, so the report never joins ``issue__*`` (an INNER JOIN would drop rows
    whose work item was hard-deleted); it falls back to the worklog's snapshot."""
    from plane.db.models import Issue

    ids = [i for i in issue_ids if i]
    return {
        row[0]: row[1:]
        for row in Issue.all_objects.filter(id__in=ids).values_list("id", "name", "sequence_id", "deleted_at")
    }


def _issue_label(r, live):
    """(display label, sequence_id, is_deleted) for an issue-grouped row."""
    ident = r.get("project__identifier")
    hit = live.get(r["issue"])
    if hit:
        name, seq, deleted_at = hit
        label = f"{ident}-{seq} {name or ''}".strip() if ident and seq else (name or "Untitled")
        is_deleted = deleted_at is not None
    else:
        # Hard-deleted (or never resolvable): use the snapshot taken when the time was logged.
        seq = None
        label = f"{r.get('snap_ref') or ''} {r.get('snap_name') or ''}".strip() or "Untitled"
        is_deleted = True
    if is_deleted:
        label = f"(deleted) {label}"
    return label, seq, is_deleted


def _issue_extras(r, live):
    """Compose a disambiguated work-item label + the extra id/link fields the
    frontend needs. The name alone collides across (and within) projects."""
    name, seq, is_deleted = _issue_label(r, live)
    return {
        "name": name,
        "issue_id": str(r["issue"]) if r["issue"] else None,
        "sequence_id": seq,
        "project_id": str(r["project"]) if r["project"] else None,
        "project_identifier": r.get("project__identifier"),
        "project_name": r.get("project__name"),
        # FORK: time-tracking-v2 — the work item is gone; its time is still reported.
        "is_deleted": is_deleted,
    }


# FORK: time-tracking-v2 — snapshot columns, aggregated per group (constant within an issue).
ISSUE_SNAPSHOT_ANNOTATIONS = {"snap_ref": Max("issue_ref"), "snap_name": Max("issue_name")}


# group_by -> (value fields, key field, name field, [optional extras builder])
GROUP_BY_MAP = {
    "resource": (["logged_by", "logged_by__display_name", "logged_by__email"], "logged_by", "logged_by__display_name"),
    "project": (["project", "project__name", "project__identifier"], "project", "project__name"),
    "client": (["project__client", "project__client__name"], "project__client", "project__client__name"),
    "issue": (
        ["issue", "project", "project__identifier", "project__name"],
        "issue",
        "snap_name",
        _issue_extras,
    ),
}

# Hard cap on the number of issue-grouped rows returned for the JSON response.
# CSV export is capped separately at MAX_CSV_ROWS.
MAX_ISSUE_GROUPS = 200

# Billable amount = sum(duration_minutes * billable_rate / 60) over billable entries.
AMOUNT_EXPR = ExpressionWrapper(
    F("duration") * F("billable_rate") / 60.0,
    output_field=DecimalField(max_digits=16, decimal_places=2),
)

# FORK: time-tracking-v2 S5 — the billing fields, present only while billing is on.
BILLING_FIELDS = ("billable_minutes", "non_billable_minutes", "billable_amount")


def _billing_aggregates():
    return {
        "billable_minutes": Coalesce(Sum("duration", filter=Q(is_billable=True)), 0),
        "billable_amount": Coalesce(
            Sum(AMOUNT_EXPR, filter=Q(is_billable=True)),
            0,
            output_field=DecimalField(max_digits=16, decimal_places=2),
        ),
    }


def _billing_out(total_minutes, billable_minutes, amount):
    return {
        "billable_minutes": billable_minutes,
        "non_billable_minutes": total_minutes - billable_minutes,
        "billable_amount": round(float(amount or 0), 2),
    }


def _hours(total_minutes):
    """Decimal hours for CSV cells, exact enough that a column sum stays within
    0.1 h of the Logged tile for any export under MAX_CSV_ROWS (6 dp: at most
    1/3,000,000 h of rounding per row); trailing zeros dropped (90 min -> 1.5)."""
    text = f"{Decimal(total_minutes) / Decimal(60):.6f}"
    return text.rstrip("0").rstrip(".")


def _parse_uuid_list(raw, field):
    ids = []
    for token in (t.strip() for t in (raw or "").split(",")):
        if not token:
            continue
        try:
            ids.append(uuid.UUID(token))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{field} must be a comma-separated list of valid UUIDs.")
    return ids


class TimeReportEndpoint(BaseAPIView):
    """Aggregated time report grouped by resource, project, client, or issue.

    Query params: group_by (resource|project|client|issue), start_date,
    end_date, project_ids (csv), client_id, user_ids (csv),
    billable (true|false), export (csv to download).

    group_by=issue is capped at MAX_ISSUE_GROUPS rows for JSON (the response
    carries "truncated"/"limit" so a caller can tell); a CSV export is capped at
    MAX_CSV_ROWS rows (400 ``too_many_rows`` beyond that). FORK secfix: the date range is
    at most MAX_RANGE_DAYS (an omitted bound is filled in; see ``_resolve_range``).

    FORK: time-tracking-v2 S5 — scoped per viewer (``reporting.resolve_scope``):
    a member sees only their own time, a project admin their projects' members
    (time those members spent in other projects is one "Other projects" group, with
    ``is_other`` true, and no project/task names), a workspace admin everyone.
    ``user_ids`` naming someone outside the scope is a 403 (``forbidden_user``).
    Billing fields/columns (and the ``billable`` filter) exist only while the
    workspace's ``billing_enabled`` setting is on. Resource ``expected_minutes`` /
    ``utilization_pct`` use the work-week + tracking-history calendar.

    The group_by=resource CSV export additionally appends a per-(resource,
    task) breakdown table after the summary table (see _resource_task_breakdown).
    """

    def _validate_query_params(self, request, workspace):
        """Return an error string if a query param is malformed, else None.

        Before this check existed, a bad value here (an invalid date, a
        non-UUID id) reached the ORM's .filter() call directly and raised a
        bare django.core.exceptions.ValidationError. DRF's exception_handler
        only translates Http404/PermissionDenied — anything else falls
        through and Django returns an unhandled 500, not a clean 400.
        """
        for field in ("start_date", "end_date"):
            value = request.GET.get(field)
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return f"{field} must be a valid date in YYYY-MM-DD format."

        # FORK: time-tracking-v2 secfix — the same range cap as the v2 report (MAX_RANGE_DAYS);
        # the old endpoint accepted any span, or none (the whole history).
        error = self._resolve_range(request, workspace)
        if error:
            return error

        for field in ("project_ids", "user_ids"):
            try:
                _parse_uuid_list(request.GET.get(field), field)
            except ValueError as exc:
                return str(exc)

        client_id = request.GET.get("client_id")
        if client_id:
            try:
                uuid.UUID(client_id)
            except (ValueError, AttributeError, TypeError):
                return "client_id must be a valid UUID."

        return None

    @staticmethod
    def _resolve_range(request, workspace):
        """Fill in a missing ``start_date`` / ``end_date`` so the span is never longer than
        ``MAX_RANGE_DAYS``, and refuse a longer one. Stores the dates on ``request``; returns
        an error string or None. Both given are used as-is; one given defaults the other to
        a span of ``MAX_RANGE_DAYS``; neither = the last ``MAX_RANGE_DAYS`` days."""
        raw_start, raw_end = request.GET.get("start_date"), request.GET.get("end_date")
        span = timedelta(days=MAX_RANGE_DAYS - 1)
        start = datetime.strptime(raw_start, "%Y-%m-%d").date() if raw_start else None
        end = datetime.strptime(raw_end, "%Y-%m-%d").date() if raw_end else None
        if start is None and end is None:
            end = local_today(zone(workspace.timezone))
        if start is None:
            start = end - span
        elif end is None:
            end = start + span
        if end < start:
            return "end_date must be on or after start_date."
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            return f"The range can be at most {MAX_RANGE_DAYS} days."
        request.time_report_range = (start.isoformat(), end.isoformat())
        return None

    def _filtered_worklogs(self, workspace, request, scope, billing):
        qs = IssueWorklog.objects.filter(workspace_id=workspace.id).filter(scope.user_q())
        start_date, end_date = request.time_report_range
        project_ids = _parse_uuid_list(request.GET.get("project_ids"), "project_ids")
        client_id = request.GET.get("client_id")
        user_ids = _parse_uuid_list(request.GET.get("user_ids"), "user_ids")
        billable = request.GET.get("billable")

        if start_date:
            qs = qs.filter(logged_date__gte=start_date)
        if end_date:
            qs = qs.filter(logged_date__lte=end_date)
        # FORK: time-tracking-v2 S5 — a project/client filter selects by project, so
        # it may only ever see detail rows (never another person's "Other projects").
        if project_ids:
            qs = qs.filter(project_id__in=project_ids).filter(scope.detail_q())
        if client_id:
            qs = qs.filter(project__client_id=client_id).filter(scope.detail_q())
        if user_ids:
            qs = qs.filter(logged_by_id__in=user_ids)
        if billing and billable in ("true", "false"):
            qs = qs.filter(is_billable=(billable == "true"))
        return qs, start_date, end_date

    def _group(self, r, key_field, name_field, extras_fn, live, billing):
        group = {
            "key": str(r[key_field]) if r[key_field] is not None else None,
            "name": r.get(name_field) or "Unassigned",
            "total_minutes": r["total_minutes"],
            "entry_count": r["entry_count"],
        }
        if billing:
            group.update(_billing_out(r["total_minutes"], r["billable_minutes"], r["billable_amount"]))
        if extras_fn:
            group.update(extras_fn(r, live))
        return group

    def _aggregate(self, qs, group_by, billing, limit=None):
        values, key_field, name_field, *rest = GROUP_BY_MAP[group_by]
        extras_fn = rest[0] if rest else None
        snapshot = ISSUE_SNAPSHOT_ANNOTATIONS if group_by == "issue" else {}
        rows = (
            qs.values(*values)
            .annotate(
                **snapshot,
                total_minutes=Coalesce(Sum("duration"), 0),
                **(_billing_aggregates() if billing else {}),
                entry_count=Count("id"),
            )
            # key_field's raw id as a tiebreaker: Postgres doesn't guarantee
            # stable ordering among equal total_minutes, so without it the
            # 200-row truncation below could return a different arbitrary
            # subset of tied rows on every identical request. key_field is
            # always a FK (issue/project/logged_by/project__client) - ordering
            # by the bare field name would make Django chase the related
            # model's own Meta.ordering (e.g. Issue's "-created_at") instead
            # of the id actually being grouped on, so use "<key_field>_id" to
            # get the raw column.
            .order_by("-total_minutes", f"{key_field}_id")
        )
        # Rows are already ordered by -total_minutes, so a limit keeps the most
        # time-significant groups rather than an arbitrary prefix.
        truncated = False
        if limit:
            rows = list(rows[: limit + 1])
            truncated = len(rows) > limit
            rows = rows[:limit]
        rows = list(rows)
        live = _live_issues([r["issue"] for r in rows]) if extras_fn else {}
        groups = [self._group(r, key_field, name_field, extras_fn, live, billing) for r in rows]
        return groups, truncated

    def _other_group(self, qs, billing):
        """FORK: time-tracking-v2 S5 — the in-scope people's time outside the viewer's
        detail projects, as one nameless group (None when there is none)."""
        agg = qs.aggregate(
            total_minutes=Coalesce(Sum("duration"), 0),
            **(_billing_aggregates() if billing else {}),
            entry_count=Count("id"),
        )
        if not agg["entry_count"]:
            return None
        group = {
            "key": None,
            "name": OTHER_PROJECTS_LABEL,
            "is_other": True,
            "total_minutes": agg["total_minutes"],
            "entry_count": agg["entry_count"],
        }
        if billing:
            group.update(_billing_out(agg["total_minutes"], agg["billable_minutes"], agg["billable_amount"]))
        return group

    def _resource_task_breakdown(self, qs, scope, billing):
        """Per-(resource, task) rows for the "By resource" CSV export.

        The UI's per-resource expand-row already shows this (task-breakdown.tsx,
        scoped to one resource at a time via user_ids); this mirrors the same
        data for every resource at once, flattened into one export so it isn't
        limited to whichever row a user happened to expand on screen.

        FORK: time-tracking-v2 S5 — rows outside the viewer's detail projects are
        one "Other projects" line per resource.
        """
        billing_agg = {"billable_minutes": Coalesce(Sum("duration", filter=Q(is_billable=True)), 0)} if billing else {}
        detail = qs.filter(scope.detail_q())
        rows = list(
            detail.values(
                "logged_by",
                "logged_by__display_name",
                "issue",
                "project__identifier",
                "project__name",
            )
            .annotate(
                **ISSUE_SNAPSHOT_ANNOTATIONS,
                total_minutes=Coalesce(Sum("duration"), 0),
                **billing_agg,
                entry_count=Count("id"),
            )
            .order_by("logged_by__display_name", "-total_minutes")[: MAX_CSV_ROWS + 1]
        )
        others = []
        if scope.detail_project_ids is not None:
            others = list(
                qs.exclude(scope.detail_q())
                .values("logged_by", "logged_by__display_name")
                .annotate(total_minutes=Coalesce(Sum("duration"), 0), **billing_agg, entry_count=Count("id"))
                .order_by("logged_by__display_name")
            )
        live = _live_issues({r["issue"] for r in rows})
        breakdown = []
        for r in rows:
            name, _seq, _deleted = _issue_label(r, live)
            breakdown.append(
                {
                    "resource_name": r.get("logged_by__display_name") or "Unassigned",
                    "task_name": name,
                    "project_name": r.get("project__name") or "",
                    "total_minutes": r["total_minutes"],
                    "billable_minutes": r.get("billable_minutes"),
                    "entry_count": r["entry_count"],
                }
            )
        for r in others:
            breakdown.append(
                {
                    "resource_name": r.get("logged_by__display_name") or "Unassigned",
                    "task_name": "",
                    "project_name": OTHER_PROJECTS_LABEL,
                    "total_minutes": r["total_minutes"],
                    "billable_minutes": r.get("billable_minutes"),
                    "entry_count": r["entry_count"],
                }
            )
        return breakdown

    def _add_utilization(self, workspace, setting, groups, start_date, end_date):
        """For resource grouping, add expected minutes + utilization %.

        FORK: time-tracking-v2 S5 — Expected is the work-week calendar (working days
        the person was tracked x their daily capacity), not weekly x ceil(weeks).
        """
        if not (start_date and end_date):
            return groups
        try:
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return groups
        if d1 < d0 or (d1 - d0).days >= MAX_RANGE_DAYS:
            return groups
        ids = [uuid.UUID(g["key"]) for g in groups if g["key"]]
        calendar, _tracked, _overrides = expected_calendar(workspace.id, setting, ids, d0, d1)
        for g in groups:
            expected = minutes(calendar.user_total(uuid.UUID(g["key"]))) if g["key"] else 0
            g["expected_minutes"] = expected or None
            g["utilization_pct"] = round((g["total_minutes"] / expected) * 100, 1) if expected else None
        return groups

    # Billing reports expose resource names and (when billing is on) dollar
    # amounts. That is internal business data, not "totals a client should see",
    # so GUEST (client) role is excluded entirely.
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        group_by = request.GET.get("group_by", "resource")
        if group_by not in GROUP_BY_MAP:
            return Response(
                {"error": f"group_by must be one of {', '.join(GROUP_BY_MAP)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = Workspace.objects.get(slug=slug)
        param_error = self._validate_query_params(request, workspace)
        if param_error:
            return Response({"error": param_error}, status=status.HTTP_400_BAD_REQUEST)
        # FORK: time-tracking-v2 S5 — the HARD scope fix: members no longer see
        # everyone's time; see resolve_scope.
        scope = resolve_scope(workspace.id, request.user)
        if scope is None:
            return _refuse("forbidden", "You do not have access to reports.", status.HTTP_403_FORBIDDEN)
        for uid in _parse_uuid_list(request.GET.get("user_ids"), "user_ids"):
            if not scope.allows_user(uid):
                return _refuse(
                    "forbidden_user", "You can only report on people in your scope.", status.HTTP_403_FORBIDDEN
                )
        setting = read_settings(workspace.id)
        # FORK: time-tracking-v2 secfix — rates/amounts are for workspace admins and project
        # admins only; a plain member's own-time report carries no billing fields.
        billing = bool(setting.billing_enabled) and scope.kind != SCOPE_SELF
        qs, start_date, end_date = self._filtered_worklogs(workspace, request, scope, billing)

        # Not "format" — that's DRF's reserved content-negotiation query param
        # (DefaultContentNegotiation.select_renderer reads it before this view's
        # get() even runs); since only JSONRenderer is registered, "?format=csv"
        # raises Http404 deep inside DRF before this line is ever reached.
        is_csv = request.GET.get("export") == "csv"
        # FORK: time-tracking-v2 secfix — the CSV is capped too (MAX_CSV_ROWS, as the v2 CSV).
        limit = (MAX_CSV_ROWS if is_csv else MAX_ISSUE_GROUPS) if group_by == "issue" else None
        if group_by == "resource":
            groups, truncated = self._aggregate(qs, group_by, billing, limit=limit)
            groups = self._add_utilization(workspace, setting, groups, start_date, end_date)
        else:
            # Project/client/work-item names: detail rows only, plus one "Other projects".
            groups, truncated = self._aggregate(qs.filter(scope.detail_q()), group_by, billing, limit=limit)
            if scope.detail_project_ids is not None:
                other = self._other_group(qs.exclude(scope.detail_q()), billing)
                if other:
                    groups.append(other)

        # Computed from the unsliced queryset, not by summing `groups` — summing
        # `groups` would silently under-report the grand total the moment any
        # grouping (issue) can be truncated.
        agg = qs.aggregate(
            total_minutes=Coalesce(Sum("duration"), 0),
            **(_billing_aggregates() if billing else {}),
            entry_count=Count("id"),
        )
        totals = {"total_minutes": agg["total_minutes"], "entry_count": agg["entry_count"]}
        if billing:
            totals["billable_minutes"] = agg["billable_minutes"]
            totals["billable_amount"] = round(float(agg["billable_amount"] or 0), 2)

        if is_csv and truncated:
            return _refuse("too_many_rows", f"The export is limited to {MAX_CSV_ROWS} rows; pick a shorter range.")
        if is_csv:
            # "By resource" is the only export that also gets a task-level
            # breakdown appended — it's the one place the UI itself offers a
            # per-task drill-down (the expand-row), just not per-resource-at-a-
            # time like the on-screen version.
            task_breakdown = None
            if group_by == "resource":
                task_breakdown = self._resource_task_breakdown(qs, scope, billing)
                if len(task_breakdown) > MAX_CSV_ROWS:
                    return _refuse(
                        "too_many_rows", f"The export is limited to {MAX_CSV_ROWS} rows; pick a shorter range."
                    )
            return self._csv_response(group_by, groups, billing, task_breakdown=task_breakdown)

        return Response(
            {
                "group_by": group_by,
                "groups": groups,
                "totals": totals,
                "truncated": truncated,
                "limit": limit,
                "scope": scope.kind,
                "billing_enabled": billing,
            },
            status=status.HTTP_200_OK,
        )

    def _csv_response(self, group_by, groups, billing, task_breakdown=None):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="time-report-by-{group_by}.csv"'
        writer = csv.writer(response)
        label = "Work item" if group_by == "issue" else group_by.capitalize()
        header = [label, "Total (h)"]
        if billing:
            header += ["Billable (h)", "Non-billable (h)", "Billable amount"]
        header.append("Entries")
        if group_by == "resource":
            header += ["Expected (h)", "Utilization %"]
        if group_by == "issue":
            header.insert(1, "Project")
        writer.writerow(header)
        for g in groups:
            row = [_csv_safe(g["name"]), round(g["total_minutes"] / 60, 2)]
            if billing:
                row += [
                    round(g["billable_minutes"] / 60, 2),
                    round(g["non_billable_minutes"] / 60, 2),
                    g["billable_amount"],
                ]
            row.append(g["entry_count"])
            if group_by == "resource":
                row += [
                    round(g["expected_minutes"] / 60, 2) if g.get("expected_minutes") else "",
                    g.get("utilization_pct") if g.get("utilization_pct") is not None else "",
                ]
            if group_by == "issue":
                row.insert(1, _csv_safe(g.get("project_name") or ""))
            writer.writerow(row)

        # Flat, not nested — CSV has no real concept of a sub-table, so this is
        # a second header + row block in the same file rather than one row per
        # resource with a further-indented table under it.
        if task_breakdown is not None:
            writer.writerow([])
            sub_header = ["Resource", "Task", "Project", "Total (h)"]
            if billing:
                sub_header.append("Billable (h)")
            writer.writerow(sub_header + ["Entries"])
            for t in task_breakdown:
                row = [
                    _csv_safe(t["resource_name"]),
                    _csv_safe(t["task_name"]),
                    _csv_safe(t["project_name"]),
                    round(t["total_minutes"] / 60, 2),
                ]
                if billing:
                    row.append(round((t["billable_minutes"] or 0) / 60, 2))
                writer.writerow(row + [t["entry_count"]])
        return response


# ---------------------------------------------------------------------------
# FORK: time-tracking-v2 S5 — Reports v2 (tiles, series, team/member tables, CSV)
# ---------------------------------------------------------------------------


def _parse_range(params, today):
    """Inclusive workspace-local [from, to]; default the current Mon–Sun week."""
    raw_from = params.get("from") or params.get("start_date")
    raw_to = params.get("to") or params.get("end_date")
    try:
        start = date.fromisoformat(raw_from) if raw_from else today - timedelta(days=today.isoweekday() - 1)
        end = date.fromisoformat(raw_to) if raw_to else (start if raw_from else start + timedelta(days=6))
    except (TypeError, ValueError):
        raise ValueError("from and to must be dates (YYYY-MM-DD).")
    if end < start:
        raise ValueError("to must be on or after from.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"The range can be at most {MAX_RANGE_DAYS} days.")
    return start, end


def _week_start(day):
    return day - timedelta(days=day.isoweekday() - 1)


def _source_label(row):
    return "timer" if row["source"] == WorklogSource.TIMER else "manual"


def _local_hm(value, tz):
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M") if value else ""


class TimeReportV2Endpoint(BaseAPIView):
    """``GET time/report/`` (JSON) and ``GET time/report.csv/`` (entry rows).

    Params: ``from``/``to`` (inclusive workspace-local dates, default this
    Mon–Sun week, at most 366 days); ``user`` = ``team`` (default for project and
    workspace admins) or a user id (default for members: themselves);
    ``task_limit`` (1..500, default 200); ``export=csv`` = same as ``report.csv``.

    Scope as in ``reporting.resolve_scope``. ``user=team`` from a member, or a
    user id outside the viewer's scope (nonexistent ids included, so it is no
    existence oracle), is a 403. Billing fields only while billing is on.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, csv_export=False):
        workspace = Workspace.objects.get(slug=slug)
        tz = zone(workspace.timezone)
        now = timezone.now()
        today = local_today(tz, now)
        scope = resolve_scope(workspace.id, request.user)
        if scope is None:
            return _refuse("forbidden", "You do not have access to reports.", status.HTTP_403_FORBIDDEN)
        params = request.query_params
        try:
            start, end = _parse_range(params, today)
        except ValueError as exc:
            return _refuse("invalid_range", str(exc))
        try:
            task_limit = int(params.get("task_limit") or DEFAULT_TASK_LIMIT)
        except (TypeError, ValueError):
            return _refuse("invalid_limit", "task_limit must be a whole number.")
        if not 1 <= task_limit <= MAX_TASK_LIMIT:
            return _refuse("invalid_limit", f"task_limit must be between 1 and {MAX_TASK_LIMIT}.")

        setting = read_settings(workspace.id)
        # FORK: time-tracking-v2 secfix — rates/amounts are for workspace admins and project
        # admins only; a plain member's own-time report carries no billing fields.
        billing = bool(setting.billing_enabled) and scope.kind != SCOPE_SELF
        candidates = candidate_user_ids(workspace.id, scope, start, end)

        target = params.get("user") or ("me" if scope.kind == SCOPE_SELF else "team")
        if target == "team":
            if scope.kind == SCOPE_SELF:
                return _refuse("forbidden_scope", "Members can only see their own report.", status.HTTP_403_FORBIDDEN)
            target_user = None
        else:
            if target == "me":
                target_user = request.user.id
            else:
                try:
                    target_user = uuid.UUID(target)
                except (ValueError, AttributeError, TypeError):
                    return _refuse("invalid_user", "user must be 'team' or a user id.")
            if not self._may_view_user(workspace.id, scope, candidates, target_user):
                return _refuse(
                    "forbidden_user", "You can only see reports for people in your scope.", status.HTTP_403_FORBIDDEN
                )

        base = IssueWorklog.objects.filter(workspace_id=workspace.id, logged_date__gte=start, logged_date__lte=end)
        if target_user is not None:
            base = base.filter(logged_by_id=target_user)
        else:
            base = base.filter(logged_by_id__in=candidates)

        if csv_export or params.get("export") == "csv":
            return self._csv(base, scope, billing, tz, start, end)

        calendar, tracked, overrides = expected_calendar(
            workspace.id, setting, [target_user] if target_user else candidates, start, end
        )

        per_day = {}  # {(user_id, date): agg}
        for r in (
            base.values("logged_by_id", "logged_date")
            .annotate(
                total_minutes=Coalesce(Sum("duration"), 0),
                **(_billing_aggregates() if billing else {}),
                entry_count=Count("id"),
            )
            .order_by()
        ):
            per_day[(r["logged_by_id"], r["logged_date"])] = r

        if target_user is not None:
            people_ids = [target_user]
        else:
            # The team = everyone in scope tracked on at least one day of the range,
            # or with time in it (e.g. an untracked or departed member's past entries).
            logged_ids = {uid for uid, _d in per_day}
            people_ids = [uid for uid in candidates if tracked.get(uid) or uid in logged_ids]

        names = dict(User.objects.filter(id__in=set(candidates) | {*people_ids}).values_list("id", "display_name"))
        tracked_now = tracked_days_many(workspace.id, set(candidates) | set(people_ids), today, today)

        def stats(user_ids, d0=start, d1=end):
            logged = weekend = entries = billable = 0
            amount = Decimal(0)
            for uid in user_ids:
                for day in date_range(d0, d1):
                    r = per_day.get((uid, day))
                    if not r:
                        continue
                    logged += r["total_minutes"]
                    entries += r["entry_count"]
                    if not is_working_day(setting, day):
                        weekend += r["total_minutes"]
                    if billing:
                        billable += r["billable_minutes"]
                        amount += r["billable_amount"] or 0
            expected = sum((calendar.user_total(uid, start=d0, end=d1) for uid in user_ids), 0)
            to_date = sum((calendar.user_total(uid, start=d0, end=min(d1, today)) for uid in user_ids), 0)
            # FORK: time-tracking-v2 holidays/leave (PSR-122) — expected minutes removed by
            # holidays/leave in [d0, d1] (the "to date" figure is not split).
            off_total = sum((calendar.off_total(uid, start=d0, end=d1) for uid in user_ids), 0)
            out = {
                "logged_minutes": logged,
                "expected_minutes": minutes(expected),
                "off_minutes": minutes(off_total),
                "expected_to_date_minutes": minutes(to_date),
                "balance_minutes": logged - minutes(expected),
                "balance_to_date_minutes": logged - minutes(to_date),
                "weekend_minutes": weekend,
                "entry_count": entries,
            }
            if billing:
                out.update(_billing_out(logged, billable, amount))
            return out

        tiles = stats(people_ids)
        tiles["member_count"] = len(people_ids)
        tiles["running_minutes"] = self._running_minutes(workspace.id, people_ids, today, tz, now, start, end)

        series = []
        cum_logged = 0
        cum_expected = 0
        for day in date_range(start, end):
            logged = sum(per_day[(uid, day)]["total_minutes"] for uid in people_ids if (uid, day) in per_day)
            expected = calendar.day_total(day, people_ids)
            cum_logged += logged
            cum_expected += expected
            series.append(
                {
                    "date": day,
                    "logged_minutes": logged,
                    "expected_minutes": minutes(expected),
                    "is_working_day": is_working_day(setting, day),
                    "is_future": day > today,
                    "cumulative_logged_minutes": cum_logged,
                    "cumulative_expected_minutes": minutes(cum_expected),
                }
            )

        data = {
            "from": start,
            "to": end,
            "today": today,
            "timezone": str(tz),
            "scope": scope.kind,
            "billing_enabled": billing,
            "work_week": {
                "work_days": list(setting.work_days or []),
                "minutes_per_day": setting.minutes_per_day,
                "weekly_minutes": default_weekly_minutes(setting),
            },
            "user": None,
            "tiles": tiles,
            "series": series,
        }

        if target_user is None:
            ordered = sorted(people_ids, key=lambda uid: ((names.get(uid) or "").lower(), str(uid)))
            data["members"] = [
                {
                    "user_id": str(uid),
                    "display_name": names.get(uid) or "",
                    "is_tracked_now": bool(tracked_now.get(uid)),
                    "weekly_capacity_minutes": overrides.get(uid),
                    # FORK: time-tracking-v2 holidays/leave (PSR-122)
                    "days_off": calendar.days_off(uid, start, end),
                    **stats([uid]),
                }
                for uid in ordered[:MAX_REPORT_PEOPLE]
            ]
            data["members_truncated"] = len(ordered) > MAX_REPORT_PEOPLE
        else:
            data["user"] = {
                "user_id": str(target_user),
                "display_name": names.get(target_user) or "",
                "is_tracked_now": bool(tracked_now.get(target_user)),
                "weekly_capacity_minutes": overrides.get(target_user),
                # FORK: time-tracking-v2 holidays/leave (PSR-122)
                "days_off": calendar.days_off(target_user, start, end),
            }
            weeks = []
            week = _week_start(start)
            while week <= end:
                d0, d1 = max(week, start), min(week + timedelta(days=6), end)
                weeks.append({"week_start": week, "from": d0, "to": d1, **stats([target_user], d0, d1)})
                week += timedelta(days=7)
            data["weeks"] = weeks

        data["projects"] = self._projects(base, scope)
        data["tasks"] = self._tasks(base, scope, task_limit)
        people = sorted(candidates, key=lambda uid: ((names.get(uid) or "").lower(), str(uid)))
        data["people"] = [
            {"user_id": str(uid), "display_name": names.get(uid) or "", "is_tracked_now": bool(tracked_now.get(uid))}
            for uid in people[:MAX_REPORT_PEOPLE]
        ]
        data["people_truncated"] = len(people) > MAX_REPORT_PEOPLE
        return Response(data, status=status.HTTP_200_OK)

    @staticmethod
    def _may_view_user(workspace_id, scope, candidates, user_id):
        if user_id in candidates:
            return True
        if not scope.allows_user(user_id):
            return False
        # In scope but no active membership and no time in the range (a departed
        # member's empty period): allowed, if they ever belonged to the workspace.
        return WorkspaceMember.objects.filter(workspace_id=workspace_id, member_id=user_id).exists()

    @staticmethod
    def _running_minutes(workspace_id, user_ids, today, tz, now, start, end):
        """Today's part of the running timers (not yet in Logged), when today is in range."""
        if not (start <= today <= end) or not user_ids:
            return 0
        since = day_start(today, tz)
        total = 0
        for started_at in WorklogTimer.objects.filter(workspace_id=workspace_id, user_id__in=user_ids).values_list(
            "started_at", flat=True
        ):
            total += max(0, int((now - max(started_at, since)).total_seconds() // 60))
        return total

    @staticmethod
    def _projects(base, scope):
        rows = (
            base.filter(scope.detail_q())
            .values("project_id", "project__name", "project__identifier")
            .annotate(total_minutes=Coalesce(Sum("duration"), 0), entry_count=Count("id"))
            .order_by("-total_minutes", "project_id")
        )
        other = 0
        if scope.detail_project_ids is not None:
            other = base.exclude(scope.detail_q()).aggregate(m=Coalesce(Sum("duration"), 0))["m"]
        return {
            "results": [
                {
                    "project_id": str(r["project_id"]),
                    "name": r["project__name"] or "",
                    "identifier": r["project__identifier"] or "",
                    "logged_minutes": r["total_minutes"],
                    "entry_count": r["entry_count"],
                }
                for r in rows[:MAX_TASK_LIMIT]
            ],
            "other_projects": {"label": OTHER_PROJECTS_LABEL, "logged_minutes": other},
        }

    @staticmethod
    def _tasks(base, scope, limit):
        rows = list(
            base.filter(scope.detail_q())
            .values("issue", "project", "project__identifier", "project__name")
            .annotate(
                **ISSUE_SNAPSHOT_ANNOTATIONS,
                total_minutes=Coalesce(Sum("duration"), 0),
                entry_count=Count("id"),
            )
            .order_by("-total_minutes", "issue_id")[: limit + 1]
        )
        truncated = len(rows) > limit
        rows = rows[:limit]
        live = _live_issues([r["issue"] for r in rows])
        other = 0
        if scope.detail_project_ids is not None:
            other = base.exclude(scope.detail_q()).aggregate(m=Coalesce(Sum("duration"), 0))["m"]
        results = []
        for r in rows:
            extras = _issue_extras(r, live)
            results.append({**extras, "logged_minutes": r["total_minutes"], "entry_count": r["entry_count"]})
        return {"results": results, "limit": limit, "truncated": truncated, "other_projects_minutes": other}

    def _csv(self, base, scope, billing, tz, start, end):
        """One row per entry. Hours have 6 dp so the column sums to Logged within
        0.1 h; every text cell is formula-neutralized. Rows outside the viewer's
        detail projects show "Other projects" with no task or note."""
        if base.count() > MAX_CSV_ROWS:
            return _refuse("too_many_rows", f"The export is limited to {MAX_CSV_ROWS} entries; pick a shorter range.")
        issue_ids = set(base.filter(scope.detail_q()).values_list("issue_id", flat=True).distinct())
        live = _live_issues(issue_ids)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="time-report-{start}-to-{end}.csv"'
        writer = csv.writer(response)
        header = ["Date", "Member", "Project", "Task ID", "Task title", "Start", "End", "Hours", "Minutes"]
        header += ["Source", "Note"]
        if billing:
            header += ["Billable", "Rate", "Currency", "Amount"]
        writer.writerow(header)
        rows = (
            base.values(
                "logged_date",
                "logged_by_id",
                "logged_by__display_name",
                "project_id",
                "project__name",
                "project__identifier",
                "issue_id",
                "issue_ref",
                "issue_name",
                "started_at",
                "ended_at",
                "duration",
                "source",
                "description",
                "is_billable",
                "billable_rate",
                "currency",
            )
            .order_by("logged_date", "logged_by__display_name", "started_at", "created_at", "id")
            .iterator(chunk_size=2000)
        )
        for r in rows:
            detail = scope.is_detail(r["logged_by_id"], r["project_id"])
            if detail:
                hit = live.get(r["issue_id"])
                if hit:
                    name, seq, deleted_at = hit
                    ident = f"{r['project__identifier']}-{seq}" if r["project__identifier"] and seq else ""
                    title = f"(deleted) {name or ''}".strip() if deleted_at else (name or "")
                else:
                    ident = r["issue_ref"] or ""
                    title = f"(deleted) {r['issue_name'] or ''}".strip()
                project, note = r["project__name"] or "", r["description"] or ""
            else:
                project, ident, title, note = OTHER_PROJECTS_LABEL, "", "", ""
            row = [
                r["logged_date"].isoformat(),
                _csv_safe(r["logged_by__display_name"] or ""),
                _csv_safe(project),
                _csv_safe(ident),
                _csv_safe(title),
                _local_hm(r["started_at"], tz),
                _local_hm(r["ended_at"], tz),
                _hours(r["duration"]),
                r["duration"],
                _source_label(r),
                _csv_safe(note),
            ]
            if billing:
                # A project admin sees rates only for the projects they administer.
                rate = r["billable_rate"] if (scope.is_all or r["project_id"] in scope.admin_project_ids) else None
                amount = round(r["duration"] * rate / 60, 2) if (rate is not None and r["is_billable"]) else ""
                row += [
                    "yes" if r["is_billable"] else "no",
                    rate if rate is not None else "",
                    _csv_safe(r["currency"] or ""),
                    amount,
                ]
            writer.writerow(row)
        return response
