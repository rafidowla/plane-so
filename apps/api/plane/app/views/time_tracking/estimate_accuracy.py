# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S7 — estimate accuracy (DESIGN §2 item 36, §6 S7)."""

# Python imports
import uuid
from datetime import timedelta

# Django imports
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.db.models import (
    IssueTimeEstimate,
    IssueWorklog,
    Project,
    ProjectMember,
    StateGroup,
    Workspace,
    WorkspaceMember,
)
from plane.utils.time_tracking import accuracy
from plane.utils.time_tracking.reporting import MAX_RANGE_DAYS, resolve_scope
from plane.utils.time_tracking.tz import day_start, local_today, zone

DEFAULT_TASK_LIMIT = 200
MAX_TASK_LIMIT = 500
# Every aggregate is computed over the whole population, so the population is bounded.
MAX_POPULATION = 20_000
MAX_GROUPS = 500


def _refuse(code, error, status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": error, "code": code}, status=status_code)


def _flag(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _minutes_sum(**filters):
    """Σ duration of the (live) time entries on the estimate's work item."""
    return Coalesce(
        Subquery(
            IssueWorklog.objects.filter(issue_id=OuterRef("issue_id"), **{k: v for k, v in filters.items()})
            .order_by()
            .values("issue_id")
            .annotate(total=Sum("duration"))
            .values("total"),
            output_field=IntegerField(),
        ),
        0,
    )


def scope_q(scope):
    """Which estimates the viewer may see, from the S5 ``ReportScope`` (not a second scoping model).

    Workspace admin: all. Project admin: every estimate on work items of the projects they
    administer, plus their own estimates anywhere. Member: their own estimates only
    (``detail_project_ids`` is empty for them). Credit is by ``original_by``.
    """
    if scope.detail_project_ids is None:
        return Q()
    return Q(original_by_id=scope.viewer_id) | Q(issue__project_id__in=scope.detail_project_ids)


class EstimateAccuracyEndpoint(BaseAPIView):
    """``GET time/estimate-accuracy/``

    Params: ``from`` / ``to`` (inclusive workspace-local *completion* dates, default the 90
    days ending today, at most 366 days); ``solo`` (1 = only tasks where the estimator logged
    >= 80 % of the time); ``granularity`` (``week`` default | ``month``) for the trend;
    ``project`` (uuid) and ``user`` (uuid, the estimator) filters; ``task_limit`` (1..500, 200);
    ``include_grandfathered`` (1 = also judge estimates added after work had started).

    Scope: ``reporting.resolve_scope`` (workspace admin all, project admin their projects,
    member own estimates, guest 403). A ``project`` / ``user`` outside the viewer's scope,
    nonexistent ones included, is a 403 (no existence oracle).
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        tz = zone(workspace.timezone)
        today = local_today(tz, timezone.now())
        scope = resolve_scope(workspace.id, request.user)
        if scope is None:
            return _refuse("forbidden", "You do not have access to estimate accuracy.", status.HTTP_403_FORBIDDEN)
        params = request.query_params
        try:
            start, end = accuracy.parse_range(params, today, MAX_RANGE_DAYS)
        except ValueError as exc:
            return _refuse("invalid_range", str(exc))
        granularity = params.get("granularity") or "week"
        if granularity not in accuracy.GRANULARITIES:
            return _refuse("invalid_granularity", "granularity must be 'week' or 'month'.")
        try:
            task_limit = int(params.get("task_limit") or DEFAULT_TASK_LIMIT)
        except (TypeError, ValueError):
            return _refuse("invalid_limit", "task_limit must be a whole number.")
        if not 1 <= task_limit <= MAX_TASK_LIMIT:
            return _refuse("invalid_limit", f"task_limit must be between 1 and {MAX_TASK_LIMIT}.")
        solo_only = _flag(params.get("solo"))
        include_grandfathered = _flag(params.get("include_grandfathered"))

        project_id = user_id = None
        if params.get("project"):
            try:
                project_id = uuid.UUID(params["project"])
            except (ValueError, TypeError, AttributeError):
                return _refuse("invalid_project", "project must be a project id.")
            if not self._may_view_project(workspace.id, scope, project_id):
                return _refuse("forbidden_project", "You cannot see that project.", status.HTTP_403_FORBIDDEN)
        if params.get("user"):
            try:
                user_id = uuid.UUID(params["user"])
            except (ValueError, TypeError, AttributeError):
                return _refuse("invalid_user", "user must be a user id.")
            if not self._may_view_user(workspace.id, scope, user_id):
                return _refuse("forbidden_user", "You can only see people in your scope.", status.HTTP_403_FORBIDDEN)

        population = (
            IssueTimeEstimate.objects.filter(
                workspace_id=workspace.id,
                issue__state__group=StateGroup.COMPLETED.value,
                issue__project__deleted_at__isnull=True,
            )
            .filter(scope_q(scope))
            .annotate(
                done_at=Coalesce("issue__completed_at", "issue__updated_at"),
                actual=_minutes_sum(),
                estimator_actual=Coalesce(
                    Subquery(
                        IssueWorklog.objects.filter(
                            issue_id=OuterRef("issue_id"), logged_by_id=OuterRef("original_by_id")
                        )
                        .order_by()
                        .values("issue_id")
                        .annotate(total=Sum("duration"))
                        .values("total"),
                        output_field=IntegerField(),
                    ),
                    0,
                ),
            )
            .filter(done_at__gte=day_start(start, tz), done_at__lt=day_start(end + timedelta(days=1), tz))
        )
        if project_id:
            population = population.filter(issue__project_id=project_id)
        if user_id:
            population = population.filter(original_by_id=user_id)

        counts = population.aggregate(
            no_time=Count("id", filter=Q(actual=0)),
            grandfathered=Count("id", filter=Q(actual__gt=0, grandfathered=True)),
        )
        rows_qs = population.filter(actual__gt=0)
        if not include_grandfathered:
            rows_qs = rows_qs.filter(grandfathered=False)
        raw = list(
            rows_qs.order_by("-done_at", "id").values(
                "id",
                "issue_id",
                "issue__name",
                "issue__sequence_id",
                "issue__deleted_at",
                "issue__project_id",
                "issue__project__name",
                "issue__project__identifier",
                "original_by_id",
                "original_by__display_name",
                "original_minutes",
                "current_minutes",
                "grandfathered",
                "done_at",
                "actual",
                "estimator_actual",
            )[: MAX_POPULATION + 1]
        )
        if len(raw) > MAX_POPULATION:
            return _refuse("too_many_tasks", "Too many completed tasks in that range; pick a shorter range.")

        rows = []
        for r in raw:
            row = {
                "issue_id": str(r["issue_id"]),
                "sequence_id": r["issue__sequence_id"],
                "name": r["issue__name"],
                "is_deleted": r["issue__deleted_at"] is not None,
                "project_id": str(r["issue__project_id"]),
                "project_identifier": r["issue__project__identifier"],
                "project_name": r["issue__project__name"],
                "estimator": (
                    {"id": str(r["original_by_id"]), "display_name": r["original_by__display_name"]}
                    if r["original_by_id"]
                    else None
                ),
                "completed_at": r["done_at"],
                "original_minutes": r["original_minutes"],
                "current_minutes": r["current_minutes"],
                "actual_minutes": r["actual"],
                "estimator_minutes": r["estimator_actual"],
                "reestimated": r["current_minutes"] != r["original_minutes"],
                "grandfathered": r["grandfathered"],
                "_local_date": r["done_at"].astimezone(tz).date(),
            }
            row.update(
                accuracy.task_metrics(r["original_minutes"], r["current_minutes"], r["actual"], r["estimator_actual"])
            )
            rows.append(row)
        not_solo = 0
        if solo_only:
            kept = [r for r in rows if r["solo"]]
            not_solo = len(rows) - len(kept)
            rows = kept

        def public(row):
            return {k: v for k, v in row.items() if not k.startswith("_")}

        data = {
            "from": start,
            "to": end,
            "timezone": workspace.timezone,
            "scope": scope.kind,
            "granularity": granularity,
            "solo": solo_only,
            "include_grandfathered": include_grandfathered,
            "thresholds": {
                "within_pct": accuracy.WITHIN_PCT,
                "solo_pct": accuracy.SOLO_PCT,
                "min_sample": accuracy.MIN_SAMPLE,
            },
            "excluded": {
                "no_time": counts["no_time"],
                "grandfathered": 0 if include_grandfathered else counts["grandfathered"],
                "not_solo": not_solo,
            },
            "summary": accuracy.summarize(rows),
            "tasks": {
                "results": [public(r) for r in rows[:task_limit]],
                "limit": task_limit,
                "total": len(rows),
                "truncated": len(rows) > task_limit,
            },
        }

        projects = {}
        people = {}
        buckets = {}
        for r in rows:
            projects.setdefault(r["project_id"], []).append(r)
            people.setdefault(r["estimator"]["id"] if r["estimator"] else None, []).append(r)
            buckets.setdefault(accuracy.bucket_start(r["_local_date"], granularity), []).append(r)

        project_rows = [
            {
                "project_id": pid,
                "project_identifier": group[0]["project_identifier"],
                "project_name": group[0]["project_name"],
                **accuracy.summarize(group),
            }
            for pid, group in projects.items()
        ]
        project_rows.sort(key=lambda g: (-g["n"], g["project_name"] or ""))
        person_rows = [
            {
                "user": group[0]["estimator"],
                **accuracy.summarize(group),
            }
            for group in people.values()
        ]
        person_rows.sort(key=lambda g: (-g["n"], (g["user"] or {}).get("display_name") or ""))
        data["projects"] = project_rows[:MAX_GROUPS]
        data["projects_truncated"] = len(project_rows) > MAX_GROUPS
        data["people"] = person_rows[:MAX_GROUPS]
        data["people_truncated"] = len(person_rows) > MAX_GROUPS
        data["trend"] = [{"bucket": day, **accuracy.summarize(buckets[day])} for day in sorted(buckets)]
        return Response(data, status=status.HTTP_200_OK)

    @staticmethod
    def _may_view_project(workspace_id, scope, project_id):
        """Workspace admins: any live project of the workspace. Others: projects they administer
        or are an active member of (a project without their estimates just returns nothing)."""
        projects = Project.objects.filter(pk=project_id, workspace_id=workspace_id)
        if scope.is_all:
            return projects.exists()
        if project_id in (scope.detail_project_ids or ()):
            return projects.exists()
        return (
            projects.exists()
            and ProjectMember.objects.filter(project_id=project_id, member_id=scope.viewer_id, is_active=True).exists()
        )

    @staticmethod
    def _may_view_user(workspace_id, scope, user_id):
        """Like the S5 report: a person in the viewer's scope who belongs (or belonged) to the workspace."""
        if not scope.allows_user(user_id):
            return False
        return WorkspaceMember.objects.filter(workspace_id=workspace_id, member_id=user_id).exists()
