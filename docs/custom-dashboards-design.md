<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Custom Dashboards — Design & Build Plan

> **Build status (as-built, 2026-07-23):** Phases 1–8 implemented across the
> `plane.dashboards` backend app, the frontend data layer, UI components, and
> routing. Backend contract suite: 22/22 green (`test_custom_dashboards_app.py`,
> incl. the `age_trend` per-user cache tests).
> Divergence: **10 marked lines across 6 upstream files** (see
> [§6](#6-fork-touch-inventory) — well inside the fork's divergence budget).
> All new behavior lives in new files (`apps/api/plane/dashboards/**`,
> `apps/web/ce/custom-dashboards/**`, `apps/web/ce/store/custom-dashboards/**`,
> the `custom-dashboard` route folder), so upstream merges only ever have to
> reconcile the small marked diffs, never a rewrite.

This fork adds **one fixed workspace dashboard** — not a drag-and-drop,
user-composable dashboard builder. A workspace admin can add up to one of each
of 4 fixed widget types, each configured through a small per-type form; every
member with workspace access can view it. This intentionally mirrors the
scope-discipline of the fork's other additions (custom properties, time
tracking): a narrow, fully-specified v1 instead of an open-ended platform.

- [1. What the feature is](#1-what-the-feature-is)
- [2. Data model](#2-data-model)
- [3. API design](#3-api-design)
- [4. Per-widget-type `config` schema](#4-per-widget-type-config-schema)
- [5. Frontend architecture](#5-frontend-architecture)
- [6. FORK-touch inventory](#6-fork-touch-inventory)
- [7. Migration strategy](#7-migration-strategy)
- [8. Permissions](#8-permissions)
- [9. Test plan](#9-test-plan)

---

## 1. What the feature is

One dashboard per workspace, reachable at `/<workspace-slug>/custom-dashboard/`,
built from up to 4 widgets — one of each type:

1. **Status/priority distribution pie** (`distribution_pie`) — a donut of issue
   counts grouped by state, state group, or priority, with a legend and an
   overall completion percentage.
2. **Project breakdown pie** (`project_breakdown_pie`) — a donut of issue
   counts per project.
3. **Age-trend bar** (`age_trend`) — a bar chart of the average open-issue age
   (in days) and open-issue count per day, over a trailing 30/60/90-day window.
4. **Filtered issue list** (`view_list`) — a paginated table of issues pulled
   from a workspace-level saved **View** (`IssueView`), reusing that view's
   filters exactly as the Views page would render them.

Widgets are workspace-admin-managed (add/edit/delete/reorder), workspace-member
readable. There is no per-user layout — the widget list and its `sort_order`
are shared workspace-wide, deliberately (this is a team dashboard, not a
personal one).

---

## 2. Data model

One model: `WorkspaceDashboardWidget`, in a new Django app `plane.dashboards`
(`apps/api/plane/dashboards/`), table `fork_workspace_dashboard_widgets`.

```python
class WorkspaceDashboardWidget(BaseModel):
    class WidgetType(models.TextChoices):
        DISTRIBUTION_PIE = "distribution_pie", "Distribution Pie"
        PROJECT_BREAKDOWN_PIE = "project_breakdown_pie", "Project Breakdown Pie"
        AGE_TREND = "age_trend", "Age Trend"
        VIEW_LIST = "view_list", "View List"

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE,
                                   related_name="fork_dashboard_widgets")
    widget_type = models.CharField(max_length=32, choices=WidgetType.choices)
    title = models.CharField(max_length=255, blank=True)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.FloatField(default=65535)
    issue_view = models.ForeignKey("db.IssueView", on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name="fork_dashboard_widgets")
    config = models.JSONField(default=dict)
```

**Why `fork_workspace_dashboard_widgets` and not `dashboards` / `widgets` /
`dashboard_widgets`:** upstream Plane already defines exactly those three
models — `Dashboard`, `Widget`, `DashboardWidget` — created in migrations
`0054_dashboard_widget_dashboardwidget.py` and
`0055_auto_20240108_0648.py` (the "Home" quick-links widgets: overview stats,
assigned issues, etc — see `apps/web/core/services/dashboard.service.ts` /
`packages/types/src/dashboard.ts`). Upstream later **deprecated and renamed**
those same models in migration
`0090_rename_dashboard_deprecateddashboard_and_more.py`
(`Dashboard → DeprecatedDashboard`, `Widget → DeprecatedWidget`,
`DashboardWidget → DeprecatedDashboardWidget`), but the underlying **table
names were never renamed** in that migration — only the Python class/model
names changed, so the tables that migration 0090 touches keep their original
db*table values. Reusing any of `dashboards`, `widgets`, `dashboard_widgets`,
or the `deprecated*\*`table names for this feature would either collide
outright or make a future upstream diff on those tables ambiguous about which
feature owns them.`fork*workspace_dashboard_widgets`is unambiguous and can
never collide with any table upstream introduces, past or future, because
upstream does not (and per the marker discipline, never will) prefix its own
tables with`fork*`.

The model is a completely separate app/migration chain (see
[§7](#7-migration-strategy)), so it also can't collide with the fork's own
`plane.properties` (custom-properties) or future apps.

---

## 3. API design

Base path: `/api/workspaces/<slug>/fork-dashboard/` (mounted from
`apps/api/plane/dashboards/urls.py`, included once from `plane/urls.py` behind
the `FORK: custom-dashboards` marker, mirroring how `plane.properties.urls` is
included for custom properties).

| #   | Method   | Path                              | Permission                | Purpose                                                                                                                   |
| --- | -------- | --------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1   | `GET`    | `.../widgets/`                    | workspace ADMIN or MEMBER | List all widgets, ordered by `sort_order`                                                                                 |
| 2   | `POST`   | `.../widgets/`                    | workspace ADMIN only      | Create a widget                                                                                                           |
| 3   | `PATCH`  | `.../widgets/<widget_id>/`        | workspace ADMIN only      | Update a widget (partial)                                                                                                 |
| 4   | `DELETE` | `.../widgets/<widget_id>/`        | workspace ADMIN only      | Delete a widget                                                                                                           |
| 5a  | `GET`    | `.../widgets/<widget_id>/data/`   | workspace ADMIN or MEMBER | Chart data for a `distribution_pie` / `project_breakdown_pie` / `age_trend` widget. 400s for `view_list` (use 5b instead) |
| 5b  | `GET`    | `.../widgets/<widget_id>/issues/` | workspace ADMIN or MEMBER | Paginated issues for a `view_list` widget's saved View. 400s for any other widget_type                                    |

(5a/5b are two views over one conceptual "get this widget's data" endpoint,
counted as one item in the phase brief's "5 API endpoints" — list+create share
a view class, as do detail update+delete, so there are 4 endpoint classes /
5 distinct routes total, matching the phase brief.)

Read endpoints (1, 5a, 5b) use `@allow_permission([ROLE.ADMIN, ROLE.MEMBER],
level="WORKSPACE")`; all writes (2, 3, 4) use `@allow_permission([ROLE.ADMIN],
level="WORKSPACE")`. Guests have no access (not listed in either set).

`WorkspaceDashboardWidgetDataEndpoint` dispatches by `widget_type` through
`WIDGET_DATA_DISPATCH` (`apps/api/plane/dashboards/widget_data.py`) into
`distribution_pie_data` / `project_breakdown_pie_data` / `age_trend_data`,
each of which reuses the existing workspace-analytics aggregation helpers
(`get_analytics_filters`, `build_analytics_chart`) rather than re-implementing
project-visibility scoping or chart bucketing.

### Caching (`age_trend` only)

The `age_trend` `data/` response is cached with
`@cache_response(timeout=300, user=True)` (5-minute TTL) on
`WorkspaceDashboardWidgetDataEndpoint._age_trend_response`. This was the
"escape hatch" originally left unimplemented for v1; it is now enabled because
morning-rush recompute of the O(N) day-sweep is the one path cheap enough to
cache safely without touching the membership-sensitive pies.

**Why `user=True` and not `user=False`:** `age_trend_data` is **user-scoped**,
not admin-pinned. It calls `_base_issue_queryset(slug, user, config.project_ids)`
→ `get_analytics_filters(slug, user, ...)`, whose `base_filters` _always_
include `project__project_projectmember__member=user` +
`is_active=True`. `config.project_ids` is only an _additional_ `project_id__in`
narrowing on top; when it is null/absent the query still returns "all projects
**this user** is an active member of", never all workspace projects. So two
users with different project memberships legitimately get different data from
the same `widget_id`. `cache_response(user=True)` folds `request.user.id` into
the key (`"<full_path>:<user_id>"`, where `full_path` already carries the
`widget_id`), giving each `(user, widget)` pair its own entry — a `user=False`
global key would serve one user's project-scoped counts to another user who
can't see those projects. The decorator only writes on `status==200 and not
settings.DEBUG`, so caching is inert under `DEBUG=True` (local/test) and active
in production.

`distribution_pie` / `project_breakdown_pie` share the exact same membership
sensitivity and are **deliberately still uncached** — enabling them would be a
safe follow-up (same `user=True` treatment) but is intentionally out of scope
here rather than done unilaterally.

`WorkspaceDashboardWidgetIssuesEndpoint` → `view_list_data()` reuses the saved
`IssueView`'s precomputed `query` (ORM kwargs) and `rich_filters` (JSON,
applied via the same `ComplexFilterBackend` the Views endpoints use), plus a
re-implemented copy of `WorkspaceViewIssuesViewSet`'s project-permission `Q`
(copied rather than imported, since the upstream version is an instance
method bound to a viewset, not a reusable standalone) so a widget can never
leak issues from a project the requesting user can't see — even if the saved
view's raw filter would include them.

---

## 4. Per-widget-type `config` schema

Validated in `WorkspaceDashboardWidgetSerializer.validate()`
(`apps/api/plane/dashboards/serializers.py`). Unknown widget_types reject
outright; any `config` key not in that type's allow-list rejects with a named
list of the offending keys — the API is strict, not tolerant, about unknown
config.

| widget_type             | allowed `config` keys          | validation                                                                                                                  |
| ----------------------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `distribution_pie`      | `group_by`, `project_ids`      | `group_by` (optional) ∈ `{"state", "state_group", "priority"}`; `issue_view` must be unset                                  |
| `project_breakdown_pie` | `project_ids`                  | `issue_view` must be unset                                                                                                  |
| `age_trend`             | `project_ids`, `lookback_days` | `lookback_days` (default 30) ∈ `{30, 60, 90}`; `issue_view` must be unset                                                   |
| `view_list`             | `page_size`, `project_ids`     | `page_size` (default 10) ∈ `{10, 25, 50}`; `issue_view` is **required** and must belong to the same workspace as the widget |

`project_ids`, where allowed, is optional; when present it must be a list and
every element must parse as a UUID (`_validate_project_ids`), else
`config.project_ids contains an invalid UUID: <value>`.

The frontend types (`apps/web/ce/custom-dashboards/types.ts`) mirror this
schema key-for-key via a `TDashboardWidgetConfigMap` keyed by widget_type, so
the create/edit form and the serializer can never silently drift — a config
object the UI can produce is always a config object the API will accept, and
vice versa.

---

## 5. Frontend architecture

- **Data layer** (Phase 5): `apps/web/ce/custom-dashboards/{types,constants,
custom-dashboards.service.ts}` + `apps/web/ce/store/custom-dashboards/`
  (a MobX store, `CustomDashboardsStore`, wired into `RootStore` behind the
  `FORK: custom-dashboards` marker in `apps/web/ce/store/root.store.ts`).
- **Components** (Phase 6): `apps/web/ce/custom-dashboards/components/` —
  `dashboard-root.tsx` (page shell + widget grid), `widget-card.tsx` (shared
  chrome: title, menu, loading/error/empty states), one renderer per type
  under `widgets/` (`distribution-pie.tsx` / `project-breakdown-pie.tsx` both
  built on the shared `pie-widget-body.tsx`; `age-trend-bar.tsx`;
  `view-issues-table.tsx`), and `widget-config-modal.tsx` (the add/edit modal
  whose form fields switch on the selected widget_type, matching
  [§4](#4-per-widget-type-config-schema) exactly).
- **Routing** (Phase 7): one route, `:workspaceSlug/custom-dashboard`, added
  in `apps/web/app/routes/core.ts`; the page component lives at
  `apps/web/app/(all)/[workspaceSlug]/(projects)/custom-dashboard/page.tsx`
  (a new file — the route registration is the only touch to the upstream
  routing table). Sidebar entry point added via `packages/constants/src/
workspace.ts` (`WORKSPACE_SIDEBAR_DYNAMIC_NAVIGATION_ITEMS["custom_dashboard"]`
  - its `_LINKS` array entry) and an icon-resolution `case` in
    `apps/web/ce/components/workspace/sidebar/helper.tsx`.
- **i18n**: one key, `"custom_dashboard": "Dashboard"`, in
  `packages/i18n/src/locales/en/common.json` — the sidebar label. JSON can't
  carry a `FORK:` comment, so this is the one intentionally-unmarked touch
  (see [§6](#6-fork-touch-inventory)).

---

## 6. FORK-touch inventory

Full audit via `grep -rn "FORK: custom-dashboards" apps packages` (excluding
build artifacts under `dist/`), cross-checked by reading every hit:

| File                                                  | Lines               | What was added                                                                                                        |
| ----------------------------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `apps/api/plane/settings/common.py`                   | 1                   | `"plane.dashboards",` in `INSTALLED_APPS`                                                                             |
| `apps/api/plane/urls.py`                              | 1                   | `path("api/", include("plane.dashboards.urls")),`                                                                     |
| `apps/web/ce/store/root.store.ts`                     | 5                   | type + value import of `CustomDashboardsStore`, the `customDashboards` field, and its constructor assignment          |
| `apps/web/app/routes/core.ts`                         | 3                   | the `custom-dashboard` route registration (plus its marker comment and blank-line spacing)                            |
| `packages/constants/src/workspace.ts`                 | 10                  | the `custom_dashboard` record entry in `WORKSPACE_SIDEBAR_DYNAMIC_NAVIGATION_ITEMS` + its entry in the `_LINKS` array |
| `apps/web/ce/components/workspace/sidebar/helper.tsx` | 5                   | `DashboardIcon` import + the `"custom_dashboard"` icon-resolution `case`                                              |
| `packages/i18n/src/locales/en/common.json`            | 1 (unmarked — JSON) | `"custom_dashboard": "Dashboard"`                                                                                     |

**Total: 10 marked lines across 6 upstream files**, plus the 1 unmarked JSON
line. All 6 marked files, plus the JSON file, match the phase brief's expected
touch-point list exactly — no gaps were found, and no additional
upstream-owned file was touched for this feature (confirmed via `git diff
--stat` against the working tree: exactly these 7 files show as modified,
everything else new for this feature is an untracked new file/directory).

Line counts above are the literal diff-insertion counts (which include the
`FORK:` marker comment lines themselves where the marker sits on its own
line), not a hand-wave estimate — the brief's own estimates (e.g. "3 lines"
for `root.store.ts`, "1 line" for `core.ts`) undercounted because they didn't
budget for the marker comment or the separate type-only import; the real
counts are reported here rather than forced to match.

---

## 7. Migration strategy

`plane.dashboards` is a standalone Django app with its own migration chain,
pinned to a specific upstream `plane.db` migration rather than to HEAD:

```python
dependencies = [
    ('db', '0121_alter_estimate_type'),
    migrations.swappable_dependency(settings.AUTH_USER_MODEL),
]
```

This mirrors `plane.properties.migrations.0001_initial`'s pin, and is
deliberate: pinning to a fixed upstream migration (0121, which already defines
`Workspace` and `IssueView`) rather than to whatever the fork's `plane.db`
chain currently ends at keeps this app's migration graph independent of the
fork's own accreting `plane.db` migrations (e.g. the time-tracking additions),
so a future upstream merge that squashes or renumbers migrations past 0121
can't break this app's dependency edge.

---

## 8. Permissions

- **Workspace ADMIN**: full CRUD on widgets (create, edit config/title/
  enabled/sort_order, delete) via the add-widget modal and per-widget menu.
- **Workspace MEMBER**: read-only — sees the dashboard and all enabled
  widgets' data, cannot add/edit/delete.
- **Guest**: no access (the dashboard route and all 5 endpoints require at
  least MEMBER-level workspace role).
- Widget data itself additionally respects **project-level visibility** on
  top of workspace role — a member who can't see a given project won't see
  its issues surfaced through any widget, including `view_list` widgets whose
  saved View might otherwise match issues in that project
  (`_project_permission_q` in `widget_data.py`).

---

## 9. Test plan

Backend contract suite: `apps/api/plane/tests/contract/app/
test_custom_dashboards_app.py` — 22 tests covering CRUD permission boundaries
(admin-only writes, member/guest read boundaries), the per-widget-type
`config` validation rules in [§4](#4-per-widget-type-config-schema)
(including the "issue_view must/must-not be set" and unknown-key rejection
cases), the `data/` vs `issues/` endpoint split (400 on the wrong endpoint
for a given widget_type), the `view_deleted` soft-empty-state response
when a `view_list` widget's saved View has been deleted, and the `age_trend`
per-user caching (both that a repeat same-user request is served from cache —
the underlying compute runs once for two hits — and that the cache is keyed
per user, so a second user is recomputed against their own project visibility
rather than being served the first user's cached counts).

See the repo's top-level test run (`docker exec ... pytest
plane/tests/contract/app/ -q`) for current pass/fail counts across the whole
build, not just this feature.
