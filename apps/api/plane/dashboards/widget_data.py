# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-dashboards per-widget data computation (Phase 3).

Pure data-fetching functions, kept separate from ``views.py`` so the view layer
stays thin. Each function corresponds to one ``WorkspaceDashboardWidget``
widget_type and reuses the workspace analytics aggregation/filtering machinery
(``get_analytics_filters`` + ``build_analytics_chart``) rather than duplicating
it. See docs/custom-dashboards-design.md.

Day-boundary convention (age_trend): all day math is done in UTC. ``today`` is
``timezone.now().date()`` (matching ``date_utils.get_analytics_date_range`` /
``advance.py``) and each issue datetime is bucketed with ``.date()`` on the
UTC-aware value returned by the ORM (USE_TZ=True stores/returns UTC). The DB
prefilter uses explicit UTC datetime bounds (not ``__date`` lookups) so it is a
strict superset regardless of the request's active timezone; the exact
open-on-day test is then re-evaluated in Python.
"""

from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace

from django.db.models import Count, Q
from django.utils import timezone

from plane.db.models import Issue
from plane.utils.build_chart import build_analytics_chart
from plane.utils.date_utils import get_analytics_filters
from plane.utils.filters import ComplexFilterBackend, IssueFilterSet

# Maps WorkspaceDashboardWidget distribution_pie ``config.group_by`` values to the
# x_axis constants understood by ``build_analytics_chart`` (see
# ``plane.utils.build_chart.x_axis_mapper`` / ``get_x_axis_field``).
_GROUP_BY_TO_X_AXIS = {
    "state": "STATES",
    "state_group": "STATE_GROUPS",
    "priority": "PRIORITY",
}
_DEFAULT_GROUP_BY = "state"


def _base_issue_queryset(slug, user, project_ids):
    """Issue queryset scoped to the workspace + the projects the user can see.

    Reuses ``get_analytics_filters`` so the project-visibility scoping is
    identical to the workspace analytics endpoints (membership + not
    deleted/archived), and ``Issue.issue_objects`` so triage/archived/draft
    issues are already excluded.
    """
    filters = get_analytics_filters(
        slug=slug,
        user=user,
        type="chart",
        date_filter=None,
        project_ids=project_ids,
    )
    return Issue.issue_objects.filter(**filters["base_filters"])


def distribution_pie_data(widget, slug, user):
    """distribution_pie: issue counts grouped by state / state_group / priority,
    plus the completion percentage over the same base queryset.
    """
    config = widget.config or {}
    group_by = config.get("group_by") or _DEFAULT_GROUP_BY
    x_axis = _GROUP_BY_TO_X_AXIS[group_by]

    queryset = _base_issue_queryset(slug, user, config.get("project_ids"))

    chart = build_analytics_chart(queryset, x_axis=x_axis)

    totals = queryset.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(state__group="completed")),
    )
    total = totals["total"] or 0
    completed = totals["completed"] or 0
    completed_percentage = round(completed / total * 100) if total else None

    return {
        "data": chart["data"],
        "schema": chart["schema"],
        "completed_percentage": completed_percentage,
    }


def project_breakdown_pie_data(widget, slug, user):
    """project_breakdown_pie: issue counts per project.

    Reshaped to the same ``{key, name, count}`` item shape that
    ``build_analytics_chart`` produces for distribution_pie so the frontend can
    render both pies through one code path.
    """
    config = widget.config or {}
    queryset = _base_issue_queryset(slug, user, config.get("project_ids"))

    rows = (
        queryset.values("project_id", "project__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    data = [
        {
            "key": str(row["project_id"]) if row["project_id"] else "None",
            "name": row["project__name"] if row["project__name"] else "None",
            "count": row["count"],
        }
        for row in rows
    ]

    return {"data": data}


def age_trend_data(widget, slug, user):
    """age_trend: per-day average age (days) and open count over the trailing
    ``config.lookback_days`` window ending today.

    "Open on day D" == created_at.date() <= D AND (completed_at IS NULL OR
    completed_at.date() >= D). ``avg_age_days`` is the mean of
    ``(D - created_at.date()).days`` over the issues open on day D.

    Single query (a superset of possibly-open issues) + a Python day sweep,
    following the gap-fill-loop structure of
    ``advance.py::work_item_completion_chart`` (which buckets by month, not
    daily average age, so only its shape is reused, not its query).
    """
    config = widget.config or {}
    lookback_days = config.get("lookback_days", 30)

    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=lookback_days - 1)

    # Explicit UTC datetime bounds -> strict superset, timezone-independent.
    start_dt = datetime.combine(start_date, time.min, tzinfo=dt_timezone.utc)
    end_dt = datetime.combine(end_date, time.max, tzinfo=dt_timezone.utc)

    queryset = _base_issue_queryset(slug, user, config.get("project_ids")).filter(
        created_at__lte=end_dt
    ).filter(Q(completed_at__isnull=True) | Q(completed_at__gte=start_dt))

    # (created_date, completed_date) pairs; completed_date is None for open issues.
    issues = [
        (created_at.date(), completed_at.date() if completed_at else None)
        for created_at, completed_at in queryset.values_list("created_at", "completed_at")
    ]

    data = []
    day = start_date
    while day <= end_date:
        age_sum = 0
        open_count = 0
        for created_date, completed_date in issues:
            if created_date <= day and (completed_date is None or completed_date >= day):
                age_sum += (day - created_date).days
                open_count += 1
        avg_age_days = round(age_sum / open_count, 2) if open_count else 0.0
        data.append(
            {
                "key": day.strftime("%Y-%m-%d"),
                "name": day.strftime("%b %d, %Y"),
                "avg_age_days": avg_age_days,
                "open_count": open_count,
            }
        )
        day += timedelta(days=1)

    return {"data": data}


def _project_permission_q(user):
    """Project-visibility Q for issue lists, re-implemented from
    ``WorkspaceViewIssuesViewSet._get_project_permission_filters``.

    Re-implemented (not imported) because the upstream version is an instance
    method bound to ``self.request.user`` on that viewset and is not a reusable
    standalone; copying the identical Q keeps guest/role scoping in lockstep
    without importing view-coupled internals. A dashboard widget must not leak
    issues from projects the requester cannot see, even if the saved view's raw
    filter would include them.
    """
    return Q(
        Q(
            project__project_projectmember__role=5,
            project__guest_view_all_features=True,
        )
        | Q(
            project__project_projectmember__role=5,
            project__guest_view_all_features=False,
            created_by=user,
        )
        | Q(project__project_projectmember__role__gt=5),
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
    )


def _apply_view_list_annotations(queryset):
    """Annotations/prefetches consumed by ``ViewIssueListSerializer``.

    Mirrors ``WorkspaceViewIssuesViewSet.apply_annotations`` (every annotation
    it adds is read by the serializer: cycle_id, link_count, attachment_count,
    sub_issues_count, and the assignee/label/module prefetches). ``state`` is
    additionally ``select_related`` because the serializer reads
    ``instance.state.group``.
    """
    from django.db.models import F, Func, OuterRef, Prefetch, Subquery
    from plane.db.models import (
        CycleIssue,
        FileAsset,
        IssueAssignee,
        IssueLabel,
        IssueLink,
        ModuleIssue,
    )

    return (
        queryset.select_related("state")
        .annotate(
            cycle_id=Subquery(
                CycleIssue.objects.filter(
                    issue=OuterRef("id"), deleted_at__isnull=True
                ).values("cycle_id")[:1]
            )
        )
        .annotate(
            link_count=IssueLink.objects.filter(issue=OuterRef("id"))
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .annotate(
            attachment_count=FileAsset.objects.filter(
                issue_id=OuterRef("id"),
                entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            )
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .annotate(
            sub_issues_count=Issue.issue_objects.filter(parent=OuterRef("id"))
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .prefetch_related(Prefetch("issue_assignee", queryset=IssueAssignee.objects.all()))
        .prefetch_related(Prefetch("label_issue", queryset=IssueLabel.objects.all()))
        .prefetch_related(Prefetch("issue_module", queryset=ModuleIssue.objects.all()))
    )


def view_list_data(widget, slug, request, view):
    """view_list: paginated issues for the widget's saved ``IssueView``.

    Returns a DRF ``Response``. If the saved view was soft-deleted (SET_NULL
    left ``issue_view`` null) a clean empty-state ``{"data": [], "view_deleted":
    True}`` is returned instead of erroring.

    ``view`` is the calling ``BaseAPIView`` instance: it supplies ``.paginate``
    (BasePaginator) and ``.filterset_class = IssueFilterSet`` for the complex
    filter backend.
    """
    from plane.app.serializers import ViewIssueListSerializer

    issue_view = widget.issue_view
    if issue_view is None:
        from rest_framework import status
        from rest_framework.response import Response

        return Response({"data": [], "view_deleted": True}, status=status.HTTP_200_OK)

    config = widget.config or {}
    page_size = config.get("page_size", 10)

    queryset = Issue.issue_objects.filter(workspace__slug=slug)

    # Optional extra project scoping from widget config (permitted config key).
    project_ids = config.get("project_ids")
    if project_ids:
        queryset = queryset.filter(project_id__in=project_ids)

    # Project-visibility permission scoping (must run regardless of view filters).
    queryset = queryset.filter(_project_permission_q(request.user))

    # The saved view's precomputed ORM kwargs (from IssueView.save()).
    if issue_view.query:
        queryset = queryset.filter(**issue_view.query)

    # Rich (JSON) filters, applied through the same backend the Views endpoint uses.
    if issue_view.rich_filters:
        filter_view = SimpleNamespace(filterset_class=IssueFilterSet)
        queryset = ComplexFilterBackend().filter_queryset(
            request, queryset, filter_view, filter_data=issue_view.rich_filters
        )

    queryset = queryset.distinct()

    total_count_queryset = queryset.only("id")
    queryset = _apply_view_list_annotations(queryset)

    return view.paginate(
        order_by="-created_at",
        request=request,
        queryset=queryset,
        on_results=lambda issues: ViewIssueListSerializer(issues, many=True).data,
        total_count_queryset=total_count_queryset,
        default_per_page=page_size,
        max_per_page=page_size,
    )


WIDGET_DATA_DISPATCH = {
    "distribution_pie": distribution_pie_data,
    "project_breakdown_pie": project_breakdown_pie_data,
    "age_trend": age_trend_data,
}
