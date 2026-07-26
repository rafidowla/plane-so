<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Custom Properties (Typed Columns) — Design & Build Plan

> **Build status (as-built, 2026-07-15):** P0–P6 implemented, browser-verified,
> and committed on `feat/custom-properties`. Ships the v1 **Status (OPTION)**
> type end-to-end: instance + per-project feature toggle, spreadsheet column
> (Monday-style filled cell), column-type picker + property CRUD (create/edit/
> delete with option colours), kanban/list card chips, detail-sidebar & peek
> rows, and edit-modal inputs. Verified upstream-safety: flag-off renders a
> byte-identical DOM. Divergence **25 marked lines / 13 upstream files** (budget
> ≤40). Backend contract suite: 19/19 green.
>
> Deliberately deferred to a follow-up: create-time modal value staging (task
> 6.2, EDIT-mode only for now), column drag-reorder (5.5), non-OPTION property
> types, and the optional public `/api/v1/` parity endpoints (7.3).

This fork adds **user-defined custom properties** to work items — starting with
Monday.com-style **Status columns** (a user-defined dropdown whose options each
have a label + color, rendered as a full-color filled cell in the spreadsheet) —
with a data model and architecture that lets other property types (text, number,
date, member, select, checkbox, url) layer on later.

Custom properties are an **EE/cloud feature in upstream Plane** ("Work item types
& custom properties"). This design deliberately **mirrors upstream's schema and
API contract** and isolates all new code behind the same seams upstream itself
uses for the EE feature, so that `git merge upstream/preview`
(upstream = `makeplane/plane`, default branch `preview`) stays cheap forever.

- [1. Goals / non-goals](#1-goals--non-goals)
- [2. Upstream-compatibility strategy](#2-upstream-compatibility-strategy)
- [3. Data model](#3-data-model)
- [4. API design](#4-api-design)
- [5. Frontend architecture](#5-frontend-architecture)
- [6. Exact insertion points into upstream-owned files](#6-exact-insertion-points-into-upstream-owned-files)
- [7. Migration strategy](#7-migration-strategy)
- [8. Feature flag](#8-feature-flag)
- [9. Granular build plan](#9-granular-build-plan)
- [10. Upgrade runbook](#10-upgrade-runbook)
- [11. Risks & mitigations](#11-risks--mitigations)
- [12. Test plan](#12-test-plan)
- [Appendix A: verified seam inventory](#appendix-a-verified-seam-inventory)
- [Appendix B: unverified mirror-targets](#appendix-b-unverified-mirror-targets)

---

## 1. Goals / non-goals

### Goals

1. **Status-type columns first**: a project admin can define a "Status"-style
   property (property_type `OPTION`), give it options with label + color, and see
   it as a **full-color filled cell** column in the spreadsheet layout, with the
   value editable inline via a dropdown.
2. **Extensible property system**: the data model, API, and frontend cell
   registry are shaped so `TEXT`, `DECIMAL` (number), `DATETIME` (date),
   `RELATION/USER` (member), `OPTION` single/multi (select), `BOOLEAN`
   (checkbox), and `URL` types can be added _without any further schema or
   core-file changes_ — only new cell renderers and input widgets.
3. **Column-type picker**: a "+" affordance at the end of the spreadsheet header
   (and a project-settings page) that lists property types and creates a new
   column; only Status is enabled in v1, other types render greyed-out.
4. **Custom values visible everywhere issues render**: spreadsheet column
   (primary), kanban/list card chip, create/edit issue modal, issue detail
   sidebar + peek view — all via existing upstream seams.
5. **Mirror upstream's contract** (model fields, enum values, endpoint nesting,
   payload keys) so a future migration to actual Plane EE — or upstream
   open-sourcing the feature into CE — is a data-adoption exercise, not a
   rewrite.
6. **Default off**, per-project opt-in, instance-level kill switch.

### Non-goals (explicitly out of scope)

- Segmented progress bars, formula columns (`FORMULA` type is in the enum for
  contract parity but not implemented), mirror columns, dependency columns, or
  any other Monday.com "Work-OS" constructs.
- **Work item types UI**: upstream attaches properties to `IssueType`. We keep
  the FK for schema parity but do not build type management UI; properties are
  project-scoped in v1 (see §3).
- Grouping / ordering / filtering the layouts **by** a custom property (the
  group-by and order-by pipelines are deep in upstream core; touching them
  violates the divergence budget). Header sort menus for custom columns are
  disabled in v1.
- Custom properties on epics, drafts, intake items, or the public "space" app.
- Activity/history entries for property value changes (phase 7 stretch).
- Public `/api/v1/` parity endpoints (phase 7 stretch, shapes already designed).
- Bulk-operations integration, exports (CSV/XLSX), and webhooks payload
  enrichment.

---

## 2. Upstream-compatibility strategy

**The overriding constraint: `git merge upstream/preview` must stay near-trivial.**
Every decision below is justified by how it minimizes and _isolates_ divergence.
Current fork divergence baseline (time-tracking feature, measured with
`git diff --stat origin/preview...HEAD`): 49 files, of which only ~6 are
upstream-owned core files with small edits. Custom properties must hold the same
bar or better.

### 2.1 Principles, each tied to a concrete mechanism in this repo

| #   | Principle                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Concrete mechanism (verified in this repo)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | **Build behind the edition seam.** All frontend code lives under the `@/plane-web` alias, which maps to `ce/` — `apps/web/tsconfig.json` line 9: `"@/plane-web/*": ["./ce/*"]`. This is exactly how upstream injects EE features (EE repo remaps the alias to `ee/`; this checkout has no `ee/` dir).                                                                                                                                                                                                          | New code goes in a fork-owned subtree `apps/web/ce/custom-properties/**` that upstream will never create, so upstream merges can't collide with it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| P2  | **Use the seams upstream already cut for THIS feature.** Upstream CE ships no-op stubs whose only purpose is to be overridden by the EE custom-properties implementation. We fill those stubs instead of inventing new hooks.                                                                                                                                                                                                                                                                                  | Verified stubs + their core consumers: `ce/components/issues/issue-modal/modal-additional-properties.tsx` (consumed by `core/components/issues/issue-modal/form.tsx:50,482`), `ce/components/issues/issue-details/additional-properties.tsx` (consumed by `core/components/issues/issue-detail/sidebar.tsx:40,269` and `core/components/issues/peek-overview/properties.tsx:39,263`), `ce/components/issues/issue-layouts/additional-properties.tsx` (consumed by `core/components/issues/issue-layouts/properties/all-properties.tsx:46,475`), `ce/hooks/use-issue-properties.tsx` (consumed by `core/components/issues/peek-overview/root.tsx:22,53`). Zero core edits needed for modal, sidebar, peek, kanban/list cards. |
| P3  | **Separate Django app = zero migration collisions.** All upstream models live in the single `plane.db` app with one numbered chain (currently `0121_alter_estimate_type.py` is the last upstream migration; `0122`–`0124` are fork-added — an existing collision risk we will _not_ repeat). A new `plane.properties` app owns its own `0001_...` chain that upstream can never renumber. Precedent: `plane.license` is a separate app with its own `0001–0006` chain, and holds cross-app FKs to `db` models. | New app at `apps/api/plane/properties/`; registered with **one marked line** in `INSTALLED_APPS` (`apps/api/plane/settings/common.py:79–100`) and **one marked line** in the root urlconf (`apps/api/plane/urls.py:17–23`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| P4  | **Mirror upstream's schema/API.** Model fields, enums, table names, endpoint nesting and payload keys copy Plane's real work-item-properties contract (verified against `developers.plane.so` API reference; see §3/§4 and Appendix B). If upstream ever ships the feature into CE, we adopt their code and fake-migrate onto our identical tables instead of rewriting data.                                                                                                                                  | `db_table = "issue_properties" / "issue_property_options" / "issue_property_values"`, `property_type` enum `TEXT, DATETIME, DECIMAL, BOOLEAN, OPTION, RELATION, URL, EMAIL, FILE, FORMULA`, endpoints nested as `.../work-item-types/:type_id/work-item-properties/...` and `.../work-items/:id/work-item-properties/:property_id/values/`.                                                                                                                                                                                                                                                                                                                                                                                  |
| P5  | **Minimize + MARK every core-file edit.** Every touched upstream-owned line carries a grep-able marker so divergence is auditable in seconds.                                                                                                                                                                                                                                                                                                                                                                  | Marker convention: `FORK: custom-properties` in a trailing or preceding comment (`# FORK: custom-properties` in Python, `{/* FORK: custom-properties */}` / `// FORK: custom-properties` in TS/TSX). Total budget: **≤ 40 marked lines across ≤ 10 upstream-owned files** (enumerated in §6). Audit: `grep -rn "FORK: custom-properties" apps packages`.                                                                                                                                                                                                                                                                                                                                                                     |
| P6  | **Feature-flag off by default.** No behavior change for anyone until a project explicitly enables it; instance-level kill switch for operators.                                                                                                                                                                                                                                                                                                                                                                | Fork-owned toggle table + env var; **no new column on `db.Project`** (unlike the time-tracking feature's `is_time_tracking_enabled` at `apps/api/plane/db/models/project.py:98`, which required a `db` migration — a pattern we're retiring). See §8.                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| P7  | **Merge-health metric.** The divergence in upstream-owned paths must stay near-empty and constant between merges.                                                                                                                                                                                                                                                                                                                                                                                              | `git diff --numstat origin/preview...HEAD -- apps/web/core apps/api/plane/app apps/api/plane/db apps/api/plane/settings apps/api/plane/urls.py packages/` — the custom-properties contribution to this diff must exactly match the §6 table (≤ 40 lines). A helper script `scripts/fork-divergence-report.sh` (new file, P0) prints this plus the marker inventory; run it in CI and after every upstream merge.                                                                                                                                                                                                                                                                                                             |

### 2.2 What we deliberately do NOT do

- **No edits to the issue list pipeline.** Issue lists are serialized by
  `issue_on_results()` → `queryset.values(*required_fields)` in
  `apps/api/plane/utils/grouper.py:93–141` (used by
  `apps/api/plane/app/views/issue/base.py:161–163, 326, 362, 389`). Injecting
  property values there would touch hot upstream code for every request.
  Instead, values ride a **fork-owned bulk endpoint** consumed by a frontend
  store (§4.3, §5.4). The optional detail-serializer hook (§4.4) is 5 marked
  lines and flag-gated.
- **No changes to `IIssueDisplayProperties` / `SPREADSHEET_PROPERTY_LIST`**
  (`packages/constants/src/issue/common.ts:213`,
  `packages/types/src/view-props.ts:268`). Custom columns render through a
  _parallel_ seam appended after the built-in columns, not by widening upstream
  types whose keys upstream regularly extends.
- **No new fields on upstream models** (`db.Project`, `db.Issue`, …).
- **No forking of `packages/*` UI primitives** — Status cells reuse
  `packages/utils/src/color.ts` (`hexToRgb` L49, `getLuminance` L157,
  `getContrastRatio` L178) and existing dropdown/popover primitives.

---

## 3. Data model

All models live in the new **`plane.properties`** Django app
(`apps/api/plane/properties/`). They import upstream's abstract bases
(`plane.db.models.base.BaseModel` for UUID pk + audit fields) and reference
upstream models by string label (`"db.Workspace"`, `"db.Project"`, `"db.Issue"`,
`"db.IssueType"`) — the exact pattern `apps/api/plane/db/models/issue_type.py:15`
already uses. Cross-app FKs are ordinary Django; `plane.license` proves the
pattern in this codebase.

### 3.1 Why a separate app (and not `plane.db`)

- `plane.db` has ONE migration chain, currently at `0124` locally with `0122+`
  being fork-added. Every upstream release appends to that chain; any fork
  migration numbered into it is a **guaranteed rename/renumber conflict** on
  merge. The time-tracking feature already pays this tax; custom properties
  will not.
- A separate app's `migrations/0001_initial.py` can never collide with upstream
  numbering. Upstream cannot add files to an app it doesn't have.
- `INSTALLED_APPS` registration costs exactly one marked line.
- Trade-off: cross-app FK migrations must declare a dependency on a `db`
  migration (see §7); and if upstream later ships identically-named tables,
  we adopt rather than collide (see §10.4, Risk R2).

### 3.2 Models (mirror-targets; see Appendix B for verification status)

Upstream nests properties under work item types
(`.../work-item-types/{type_id}/work-item-properties/`). This fork is
project-first (Monday-style: a column applies to every row of the project), so
`issue_type` is **nullable**: `NULL` = "applies to all work items in the
project". A payload that supplies `issue_type` still round-trips — that keeps
us shape-compatible with upstream while deferring the types UI.

```python
# apps/api/plane/properties/models.py

class PropertyTypeEnum(models.TextChoices):
    TEXT = "TEXT"
    DATETIME = "DATETIME"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    OPTION = "OPTION"
    RELATION = "RELATION"
    URL = "URL"
    EMAIL = "EMAIL"
    FILE = "FILE"
    FORMULA = "FORMULA"       # contract parity only — rejected by validation in v1

class RelationTypeEnum(models.TextChoices):
    ISSUE = "ISSUE"
    USER = "USER"

class IssueProperty(BaseModel):
    workspace = models.ForeignKey("db.Workspace", related_name="issue_properties", on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", related_name="issue_properties", on_delete=models.CASCADE)
    issue_type = models.ForeignKey("db.IssueType", related_name="properties",
                                   null=True, blank=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)              # machine name (slugified display_name)
    display_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    property_type = models.CharField(max_length=255, choices=PropertyTypeEnum.choices)
    relation_type = models.CharField(max_length=255, choices=RelationTypeEnum.choices, null=True, blank=True)
    is_required = models.BooleanField(default=False)
    default_value = models.JSONField(default=list)       # array — matches API contract
    settings = models.JSONField(default=dict)            # per-type settings (e.g. TEXT display_format)
    is_active = models.BooleanField(default=True)
    is_multi = models.BooleanField(default=False)
    validation_rules = models.JSONField(default=dict)
    logo_props = models.JSONField(default=dict)
    sort_order = models.FloatField(default=65535)        # column order, left→right
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_properties"                    # mirror upstream (Risk R2)
        ordering = ("sort_order",)

class IssuePropertyOption(BaseModel):
    workspace = models.ForeignKey("db.Workspace", related_name="issue_property_options", on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", related_name="issue_property_options", on_delete=models.CASCADE)
    property = models.ForeignKey(IssueProperty, related_name="options", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    logo_props = models.JSONField(default=dict)          # color lives here — see §3.3
    sort_order = models.FloatField(default=65535)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    parent = models.ForeignKey("self", related_name="children", null=True, blank=True, on_delete=models.CASCADE)
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_property_options"              # mirror upstream (Risk R2)
        ordering = ("sort_order",)

class IssuePropertyValue(BaseModel):
    workspace = models.ForeignKey("db.Workspace", related_name="issue_property_values", on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", related_name="issue_property_values", on_delete=models.CASCADE)
    issue = models.ForeignKey("db.Issue", related_name="property_values", on_delete=models.CASCADE)
    property = models.ForeignKey(IssueProperty, related_name="property_values", on_delete=models.CASCADE)
    # typed value columns — exactly one is populated per row, per property_type
    value_text = models.TextField(blank=True)
    value_boolean = models.BooleanField(default=False)
    value_decimal = models.FloatField(default=0)
    value_datetime = models.DateTimeField(null=True, blank=True)
    value_uuid = models.UUIDField(null=True, blank=True)             # RELATION targets
    value_option = models.ForeignKey(IssuePropertyOption, related_name="values",
                                     null=True, blank=True, on_delete=models.CASCADE)
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_property_values"               # mirror upstream (Risk R2)
        indexes = [
            models.Index(fields=["issue"]),
            models.Index(fields=["property"]),
            models.Index(fields=["project", "property"]),
        ]

# Fork-only (no upstream mirror exists) → fork-prefixed table name, safe forever.
class ProjectPropertiesFeature(BaseModel):
    project = models.OneToOneField("db.Project", related_name="properties_feature", on_delete=models.CASCADE)
    workspace = models.ForeignKey("db.Workspace", related_name="properties_features", on_delete=models.CASCADE)
    is_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "fork_project_properties_feature"
```

Semantics:

- **Multi-value = multiple rows** per (issue, property) — matches upstream's
  values-as-discrete-objects contract and makes `is_multi` trivial.
- **Status column v1** = `property_type="OPTION"`, `is_multi=False`; the value
  row uses only `value_option`. Constraint enforced in the serializer, plus a
  partial unique index `(issue, property)` where `deleted_at IS NULL` applied
  only for single-value semantics at write time (application-level, so the
  schema stays upstream-identical).
- Soft-delete comes free from `BaseModel` (`deleted_at`), consistent with
  upstream models like `ProjectIssueType`
  (`apps/api/plane/db/models/issue_type.py:41–48`).

### 3.3 Status option color

Upstream's option object has **no dedicated `color` column** — visual identity
lives in `logo_props` (JSON), the same convention as `IssueType.logo_props`
(`apps/api/plane/db/models/issue_type.py:18`). We store:

```json
{ "in_use": "color", "color": { "name": "green", "background": "#00C875" } }
```

- `background` is the fill for the Monday-style cell; text color is computed
  client-side for contrast (§5.5) — never stored.
- The settings UI offers a fixed palette (reuse `LABEL_COLOR_OPTIONS` from
  `@plane/constants`, already used by
  `apps/web/core/components/labels/create-update-label-inline.tsx:14,198`)
  plus a free hex input.
- The exact `logo_props` key shape inside upstream EE is unverified
  (Appendix B) — but because it's a JSON blob, adopting upstream's keys later
  is a data-migration inside our own app, not a schema change.

---

## 4. API design

All endpoints live in `plane.properties` (`apps/api/plane/properties/{urls,views,serializers}.py`),
mounted with one marked line in `apps/api/plane/urls.py`:
`path("api/", include("plane.properties.urls")),  # FORK: custom-properties`.
Views subclass `plane.app.views.base.BaseViewSet` / `BaseAPIView` and use
`from plane.app.permissions import ROLE, allow_permission` — the exact pattern
of the fork's `apps/api/plane/app/views/time_tracking/worklog.py:16–17`.

Path segment names mirror the public upstream API
(`work-item-types`, `work-item-properties`, `values`) so the vocabulary matches
upstream docs even though these are internal (`/api/...`, session-auth) routes.

### 4.1 Property definitions (project-scoped)

| Method               | Path                                                                               | Perm                              | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------------- | ---------------------------------------------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET                  | `/api/workspaces/<slug>/projects/<project_id>/work-item-properties/`               | project member (incl. guest read) | Returns all properties for the project (both `issue_type=NULL` and typed), `options` embedded for `OPTION` type. Empty list + `200` when feature disabled.                                                                                                                                                                                                                                                                                                 |
| POST                 | same                                                                               | project **admin**                 | Body mirrors upstream create contract: `display_name` (required), `property_type` (required), `description?`, `is_required?`, `is_active?`, `is_multi?`, `default_value?`, `settings?`, `validation_rules?`, `relation_type?`, `logo_props?`, `options?` (array — created atomically with the property), `external_source?`, `external_id?`. v1 validation: only `property_type="OPTION"` accepted; others → `400 {"error": "property_type not enabled"}`. |
| GET / PATCH / DELETE | `/api/workspaces/<slug>/projects/<project_id>/work-item-properties/<property_id>/` | admin (GET: member)               | PATCH accepts the same fields; DELETE soft-deletes property + cascades options/values (soft).                                                                                                                                                                                                                                                                                                                                                              |

### 4.2 Options

| Method         | Path                                                                                       | Perm           |
| -------------- | ------------------------------------------------------------------------------------------ | -------------- |
| GET / POST     | `/api/workspaces/<slug>/projects/<project_id>/work-item-properties/<property_id>/options/` | member / admin |
| PATCH / DELETE | `.../options/<option_id>/`                                                                 | admin          |

Option payload: `name` (required), `description?`, `is_default?`, `is_active?`,
`sort_order?`, `logo_props?` (color, §3.3), `parent?`, `external_*?`.
`is_default=true` on one option auto-applies it to _new_ issues only (no
backfill).

### 4.3 Values

Mirror endpoint (upstream-shaped, per issue + property):

| Method | Path                                                                                                                | Perm              |
| ------ | ------------------------------------------------------------------------------------------------------------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/workspaces/<slug>/projects/<project_id>/work-items/<work_item_id>/work-item-properties/<property_id>/values/` | member/guest read |
| POST   | same                                                                                                                | member            | Body: `{"values": ["<option_id>"]}` for OPTION (array of scalars per type; array length 1 unless `is_multi`). **Replace semantics**: existing live rows for (issue, property) are soft-deleted and recreated — idempotent, and single-select needs no separate DELETE call. |

Fork convenience endpoints (needed for spreadsheet/board hydration; clearly a
fork extension — upstream has no bulk read, do not assume it survives an EE
migration):

| Method    | Path                                                                                                    | Purpose                                                                                                                                                                                                                                                   |
| --------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET       | `/api/workspaces/<slug>/projects/<project_id>/work-item-property-values/?work_item_ids=<uuid,uuid,...>` | Bulk read. Response: `{"<issue_id>": {"<property_id>": ["<option_id>", ...]}, ...}`. Capped at 100 ids per call; the frontend batches (§5.4). One query: `IssuePropertyValue.objects.filter(issue_id__in=..., deleted_at__isnull=True).values_list(...)`. |
| GET/PATCH | `/api/workspaces/<slug>/projects/<project_id>/properties-feature/`                                      | Read/toggle `ProjectPropertiesFeature.is_enabled` (PATCH admin-only).                                                                                                                                                                                     |

### 4.4 The ONE serializer hook (optional, flag-gated)

The issue **list** payload is _not_ serializer-built — it's
`queryset.values(*required_fields)` via `issue_on_results`
(`apps/api/plane/utils/grouper.py:93–141`) — so we do not touch it (P7 metric).
The issue **detail** payload (`IssueDetailSerializer`,
`apps/api/plane/app/serializers/issue.py:925`) gets exactly one marked hook so
API consumers (and the peek view, if we ever want to drop the extra fetch) see
values inline:

```python
# apps/api/plane/app/serializers/issue.py  (inside IssueDetailSerializer)
    def to_representation(self, instance):  # FORK: custom-properties
        data = super().to_representation(instance)
        from plane.properties.serializers import attach_issue_property_values  # FORK: custom-properties
        return attach_issue_property_values(data, instance)  # adds data["property_values"] when enabled
```

`attach_issue_property_values` no-ops (returns `data` unchanged, adds no key)
when the kill switch or the project toggle is off — zero payload drift while
disabled. The frontend does **not** depend on this hook (it uses §4.3), so the
hook can be dropped wholesale if a merge ever makes it inconvenient.

---

## 5. Frontend architecture

### 5.1 Module layout — one fork-owned subtree

All implementation lives in `apps/web/ce/custom-properties/**` — a directory
upstream will never create, reached through the existing `@/plane-web/*` alias
(`apps/web/tsconfig.json:9`). Upstream-owned CE stub files become 1–4-line
delegations into this subtree (§6, group B).

```
apps/web/ce/custom-properties/
├── index.ts                       # public surface consumed by the stubs/seams
├── types.ts                       # TIssueProperty, TIssuePropertyOption, EIssuePropertyType, ...
├── constants.ts                   # PROPERTY_TYPE_META (icon, i18n key, enabled flag per type)
├── services/
│   └── issue-properties.service.ts  # APIService subclass (pattern: ce/services/time-tracking.service.ts:6-22)
├── store/
│   ├── custom-properties.store.ts # definitions, options, feature toggle per project
│   └── property-values.store.ts   # values per issue + debounced bulk fetch queue
├── components/
│   ├── cells/
│   │   ├── registry.ts            # PROPERTY_CELL_REGISTRY (see 5.3)
│   │   └── status-cell.tsx        # Monday-style filled cell + option dropdown
│   ├── inputs/
│   │   └── status-input.tsx       # modal/sidebar variant (chip + dropdown, not full-cell)
│   ├── spreadsheet/
│   │   ├── additional-headers.tsx # custom column <th>s + "+" add-column button
│   │   └── additional-columns.tsx # custom column <td>s for one issue row
│   ├── column-type-picker.tsx     # the "+" dropdown: Status enabled, rest greyed
│   ├── property-form-modal.tsx    # create/edit property (name, options editor w/ colors)
│   ├── layouts-additional-properties.tsx  # kanban/list card chips (fills upstream stub)
│   ├── sidebar-additional-properties.tsx  # detail sidebar + peek (fills upstream stub)
│   └── modal-additional-properties.tsx    # create/edit issue modal (fills upstream stub)
└── settings/
    └── project-properties-settings.tsx    # settings page body (route file mounts this)
```

New route files (new files ⇒ no merge risk):
`apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/features/custom-properties/page.tsx`
(+ `header.tsx`), following the fork's existing
`.../features/time-tracking/page.tsx` precedent; registered with 2 marked lines
in `apps/web/app/routes/core.ts` (precedent: lines 327–328).

### 5.2 Store design (MobX, wired via the CE root store)

`ce/store/root.store.ts` already extends `CoreRootStore` (fork precedent adds
`timelineStore` there, lines 12–19). We add one field + one constructor line
(marked):

```ts
customProperties: CustomPropertiesRootStore; // FORK: custom-properties
```

Store shape:

- `featureByProjectId: Record<string, boolean>` — from `properties-feature/` endpoint.
- `propertyIdsByProjectId: Record<string, string[]>`, `propertyMap: Record<string, TIssueProperty>`,
  `optionsByPropertyId: Record<string, TIssuePropertyOption[]>` — from the definitions endpoint.
- `valuesByIssueId: Record<string, Record<string, string[]>>` — hydrated by the bulk endpoint.
- Actions: `fetchFeature(projectId)`, `fetchProperties(projectId)`,
  `createProperty/updateProperty/deleteProperty`, `create/update/deleteOption`,
  `enqueueValueFetch(issueId)` (§5.4), `updateValue(issueId, propertyId, values)`
  with optimistic update + rollback on API failure (same UX contract as
  upstream's issue update flow).

### 5.3 Cell renderer registry (per-type extension point)

```ts
// components/cells/registry.ts
export type TPropertyCellProps = {
  issue: TIssue;
  property: TIssueProperty;
  values: string[]; // option ids for OPTION; scalars for others
  onChange: (values: string[]) => Promise<void>;
  disabled: boolean;
};
export const PROPERTY_CELL_REGISTRY: Partial<Record<EIssuePropertyType, React.FC<TPropertyCellProps>>> = {
  OPTION: StatusPropertyCell, // v1
  // TEXT: TextPropertyCell, DECIMAL: NumberPropertyCell, ...  ← later types slot in here
};
```

This intentionally parallels upstream's `SPREADSHEET_COLUMNS` map
(`apps/web/ce/components/issues/issue-layouts/utils.tsx:97–112`, consumed via
`core/.../spreadsheet/issue-column.tsx:12,32`), and the props parallel
`TSpreadsheetColumn` (`packages/types/src/view-props.ts:268`) — but we do NOT
extend those upstream types: custom columns are keyed by property UUID, not by
`keyof IIssueDisplayProperties`, and flow through our own seam components.
A second registry (`PROPERTY_INPUT_REGISTRY`) covers the modal/sidebar input
variants.

### 5.4 Spreadsheet integration & data flow

Verified render pipeline: `spreadsheet-view.tsx:72–78` builds
`spreadsheetColumnsList` from `SPREADSHEET_PROPERTY_LIST` → `spreadsheet-header.tsx:79–89`
maps it to `<SpreadsheetHeaderColumn>` → each row (`issue-row.tsx:390–400`) maps
it to `<IssueColumn>` which resolves the renderer from the plane-web
`SPREADSHEET_COLUMNS` map. Rows lazy-render via `RenderIfVisible`
(`issue-row.tsx:94`).

Custom columns append after the built-in columns via **two 1-line JSX seams**
(§6, group A):

- `<CustomPropertyHeaderCells />` after the map in `spreadsheet-header.tsx` —
  renders one `<th>` per active property (label, color dot, an options-edit
  menu for admins; sort menu intentionally absent in v1) **plus the trailing
  "+" `<th>`** that opens the column-type picker. Renders `null` when the
  feature is off, so `colSpan`/layout is untouched for everyone else.
- `<CustomPropertyValueCells issue={issueDetail} disabled={disableUserActions} />`
  after the map in `issue-row.tsx` — renders one `<td>` per active property via
  the registry. On mount it calls `enqueueValueFetch(issue.id)`; the store
  debounces ~50 ms and issues one bulk request (§4.3) per batch of newly
  visible rows — no N+1, and works naturally with `RenderIfVisible`.

Both seam components are `observer`s and read everything from the store, so the
core files pass no new props and need no new state.

### 5.5 Status cell rendering (color + contrast)

Reference implementations for colored properties today:
`spreadsheet/columns/state-column.tsx:21–39` (thin wrapper around
`core/components/dropdowns/state/dropdown.tsx`, color sourced from the state
store) and `priority-column.tsx` (PriorityDropdown). The Status cell follows the
same wrapper-around-dropdown structure but fills the **entire cell**
Monday-style:

- `<td>`-filling button, `background: option.logo_props.color.background`,
  centered label, `h-11` to match `issue-column.tsx:48` cell metrics.
- Text color computed with `getContrastRatio` / `getLuminance` from
  `packages/utils/src/color.ts:157–192`: pick white or near-black, whichever
  clears ≥ 4.5:1 (falls back to the higher ratio).
- Empty value renders a neutral hatched/soft cell with a "+" on hover.
- Click opens a popover listing options as full-width color swatches
  (structure cloned from `StateDropdown`'s Combobox usage); selecting calls
  `updateValue` (optimistic).
- Kanban/list cards render the same option as a small filled chip through the
  `WorkItemLayoutAdditionalProperties` stub (mounted at
  `all-properties.tsx:475`, between link-count and labels).

### 5.6 Column-type picker

One component (`column-type-picker.tsx`) used from both entry points:

1. **Spreadsheet**: trailing "+" header cell (inside `additional-headers.tsx`).
2. **Settings page**: "Add property" button on
   `settings/projects/:projectId/features/custom-properties`.

It lists all ten `property_type`s with icon + label from `PROPERTY_TYPE_META`;
only `OPTION` ("Status") is enabled in v1, others show a "soon" badge and are
non-interactive (`enabled: false` in the meta — flipping a later type on is a
one-line change once its renderer ships). Picking Status opens
`property-form-modal.tsx`: display name, options list (inline add/rename,
color palette from `LABEL_COLOR_OPTIONS` + hex input, drag-reorder →
`sort_order`, default toggle). Admin-only (mirrors `canEditProperties` /
`ROLE.ADMIN` gate server-side).

---

## 6. Exact insertion points into upstream-owned files

This is the complete divergence budget. Anything not listed here is a **new
file** (zero merge risk) under `apps/api/plane/properties/**`,
`apps/web/ce/custom-properties/**`, new route/test/doc files.

Marker: every edited line/block carries `FORK: custom-properties`.
Audit command: `grep -rn "FORK: custom-properties" apps packages | sort`.

### Group A — core files (upstream-owned, non-CE): 6 files, ~22 lines

| #                   | File                                                                               | Edit                                                                                                                                                                                                                 | Lines |
| ------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| A1                  | `apps/web/core/components/issues/issue-layouts/spreadsheet/spreadsheet-header.tsx` | `import { CustomPropertyHeaderCells } from "@/plane-web/custom-properties";` + `<CustomPropertyHeaderCells isEpic={isEpic} />` immediately after the `spreadsheetColumnsList.map(...)` block (after current line 89) | 2     |
| A2                  | `apps/web/core/components/issues/issue-layouts/spreadsheet/issue-row.tsx`          | `import { CustomPropertyValueCells } from "@/plane-web/custom-properties";` + `<CustomPropertyValueCells issue={issueDetail} disabled={disableUserActions} />` after the columns map (after current line 400)        | 2     |
| A3                  | `apps/web/app/routes/core.ts`                                                      | route registration pair for the settings page (same shape as time-tracking's lines 327–328)                                                                                                                          | 2     |
| A4                  | `packages/constants/src/settings/project.ts`                                       | `features_custom_properties` nav entry + one line in the features list (precedent: `features_time_tracking` at lines 82–88, 129)                                                                                     | ~9    |
| A5                  | `apps/api/plane/settings/common.py`                                                | `"plane.properties",  # FORK: custom-properties` appended to `INSTALLED_APPS` (after line 95)                                                                                                                        | 1     |
| A6                  | `apps/api/plane/urls.py`                                                           | `path("api/", include("plane.properties.urls")),  # FORK: custom-properties` in `urlpatterns` (after line 18)                                                                                                        | 1     |
| A7 _(optional, P7)_ | `apps/api/plane/app/serializers/issue.py`                                          | 5-line `to_representation` hook in `IssueDetailSerializer` (§4.4)                                                                                                                                                    | 5     |

i18n note: new strings go in `packages/i18n/src/locales/*/project-settings.json`
etc. — JSON key additions are append-only and merge trivially; not counted
against the budget but still marked in the PR description.

### Group B — CE stub files (upstream-owned but built to be overridden): 5 files, ~18 lines

These files exist in upstream CE _specifically_ as EE override points; upstream
churn on them is rare and conflicts are single-function-sized.

| #   | File                                                                        | Edit                                                                                                                                             |
| --- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| B1  | `apps/web/ce/components/issues/issue-modal/modal-additional-properties.tsx` | Replace `return null;` body with delegation: `return <CustomPropertiesModalSection {...props} />;` (import from `@/plane-web/custom-properties`) |
| B2  | `apps/web/ce/components/issues/issue-details/additional-properties.tsx`     | Same pattern → `<CustomPropertiesSidebarSection {...props} />` (covers detail sidebar **and** peek view — both consumers verified)               |
| B3  | `apps/web/ce/components/issues/issue-layouts/additional-properties.tsx`     | Same pattern → `<CustomPropertiesCardChips {...props} />` (kanban + list cards)                                                                  |
| B4  | `apps/web/ce/hooks/use-issue-properties.tsx`                                | Implement `useWorkItemProperties` to hydrate the peeked issue's values (store-backed; keeps upstream's exact signature)                          |
| B5  | `apps/web/ce/store/root.store.ts`                                           | `customProperties` store field + constructor init (2 lines + import)                                                                             |

**Rule for group B:** the stub file must never contain logic — only the
delegation call — so an upstream change to a stub's signature is a 2-minute
re-plumb, not a re-implementation.

### What is deliberately NOT touched

`grouper.py`, `views/issue/base.py`, `IIssueDisplayProperties`,
`SPREADSHEET_PROPERTY_LIST/DETAILS` (`packages/constants/src/issue/common.ts:213,230`),
`SPREADSHEET_COLUMNS` (`ce/components/issues/issue-layouts/utils.tsx:97`),
`db.Project` model, `features-list.tsx` (the fork settings page carries its own
toggle instead), all `db` migrations.

---

## 7. Migration strategy

1. **Own chain**: `apps/api/plane/properties/migrations/0001_initial.py`
   creates all four tables. Future fork changes append `0002_...` etc. in a
   namespace upstream can never write to. Precedent in-tree: `plane.license`
   (`apps/api/plane/license/migrations/0001…0006`).
2. **Cross-app dependency pinning**: `makemigrations` will auto-add
   `dependencies = [("db", "<latest-local-db-migration>")]`. **Hand-edit this**
   to the newest _upstream_ migration that already contains
   `Workspace/Project/Issue/IssueType` — pin to `("db", "0121_alter_estimate_type")`
   — so our migration never references fork-local `db` migrations
   (`0122–0124`), keeping the graphs independent if time-tracking migrations
   are ever squashed/renamed during a merge.
3. **No `db` app edits**: no new models in `plane/db/models/`, no exports added
   to `apps/api/plane/db/models/__init__.py`, no `db` migrations. (Contrast:
   time-tracking added `0122–0124` to the upstream chain — when upstream ships
   its own `0122_*.py`, that renumber conflict must be resolved by hand. Custom
   properties adds zero exposure of that kind.)
4. **Upstream merge behavior**: upstream adds `db` migrations `0122+` → they
   apply cleanly; our app's graph only references `0121`, which upstream never
   rewrites (released migrations are immutable upstream).
5. **Mirrored table names** (`issue_properties`, `issue_property_options`,
   `issue_property_values`) are a deliberate bet: see Risk R2 + §10.4 for the
   adoption procedure if upstream ships these tables into CE. The fork-only
   toggle table is `fork_`-prefixed since it has no upstream counterpart.
6. **Rollback**: `python manage.py migrate properties zero` drops only our
   tables; removing the two marked backend lines (A5/A6) fully unhooks the app.

---

## 8. Feature flag

Two layers, no upstream schema touched:

1. **Instance kill switch (env)**: `CUSTOM_PROPERTIES_ENABLED` (default `"1"`
   — **on** by default as of 2026-07-26, so a new deployment needs no env var
   at all; set it to `"0"` to opt the instance out), read inside
   `plane/properties/` code only (`os.environ`), so `settings/common.py`
   needs no extra line beyond A5. When off: every `plane.properties`
   endpoint returns `403 {"error": "custom properties disabled"}`, the
   feature endpoint reports `{"is_enabled": false, "instance_enabled": false}`,
   and the serializer hook (if built) no-ops.
2. **Per-project toggle** (still **off** by default, unaffected by the above):
   `ProjectPropertiesFeature.is_enabled`
   (fork-owned table, §3.2), surfaced at
   `GET/PATCH /api/workspaces/<slug>/projects/<project_id>/properties-feature/`
   (PATCH = project admin). No row ⇒ disabled. The frontend store treats
   "unknown" as disabled, so nothing custom renders during fetch.

Frontend gating: every seam component (`CustomPropertyHeaderCells`,
`CustomPropertyValueCells`, stub delegations) early-returns `null` unless
`customProperties.featureByProjectId[projectId] === true`. With the flag off the
rendered DOM is byte-identical to stock Plane.

Why not a `db.Project` boolean like `is_time_tracking_enabled`
(`project.py:98`) or reusing upstream's `is_issue_type_enabled` (`project.py:99`)?
The former costs a `db` migration (P3 violation); the latter collides with
upstream EE semantics for work-item-types and could auto-enable surprising
upstream UI after a merge. Own-table is equally simple and conflict-free.

---

## 9. Granular build plan

Phases are ordered; every task is independently verifiable and lists its
upstream-risk (which §6 rows it touches; "none" = new files only).
"Verify" assumes the local docker-compose dev stack
(`docker-compose-local.override.yml`) and `pnpm` workspace tooling
(`pnpm lint` = OxLint per `docs/linting.md`, `pnpm typecheck`).

### P0 — Scaffolding, flag, divergence tooling (no user-visible change)

| #   | Task                                                                                                                                                                                                                    | Files                                     | Verify                                                               | Upstream risk        |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------------- | -------------------- |
| 0.1 | Create `plane.properties` Django app skeleton: `__init__.py`, `apps.py` (`name="plane.properties"`), empty `models.py`, `urls.py` (empty `urlpatterns`), `views.py`, `serializers.py`, `migrations/__init__.py`         | `apps/api/plane/properties/**` (new)      | `python manage.py check` passes                                      | none                 |
| 0.2 | Register app + urls                                                                                                                                                                                                     | A5 (`settings/common.py`), A6 (`urls.py`) | `python manage.py check`; `curl /api/` unchanged                     | **A5, A6** (2 lines) |
| 0.3 | Env kill switch helper `plane/properties/flags.py` (`is_instance_enabled()`), used by a stub healthcheck view `GET /api/custom-properties/health/` returning `{"instance_enabled": bool}`                               | new files                                 | With/without `CUSTOM_PROPERTIES_ENABLED=1` the health endpoint flips | none                 |
| 0.4 | Divergence report script `scripts/fork-divergence-report.sh`: prints `git diff --numstat origin/preview...HEAD` filtered to upstream-owned paths + `grep -c "FORK: custom-properties"` inventory vs the §6 budget table | new file                                  | Run it; output matches §6 (only A5/A6 so far)                        | none                 |
| 0.5 | Frontend subtree skeleton: `ce/custom-properties/{index.ts,types.ts,constants.ts}` with `EIssuePropertyType` enum + `PROPERTY_TYPE_META`                                                                                | new files                                 | `pnpm typecheck` passes                                              | none                 |

### P1 — Backend models + feature toggle (5 tasks)

| #   | Task                                                                                                                                                  | Files                                                                   | Verify                                                                                                                  | Upstream risk |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------- |
| 1.1 | Models per §3.2 (`IssueProperty`, `IssuePropertyOption`, `IssuePropertyValue`, `ProjectPropertiesFeature`)                                            | `properties/models.py`                                                  | `python manage.py makemigrations properties --check --dry-run` clean after 1.2                                          | none          |
| 1.2 | Generate `0001_initial.py`; **hand-pin** dependency to `("db", "0121_alter_estimate_type")`                                                           | `properties/migrations/0001_initial.py`                                 | `python manage.py migrate properties` on a fresh DB and on a prod-like DB; `migrate properties zero` rolls back cleanly | none          |
| 1.3 | Feature endpoint `GET/PATCH .../properties-feature/` (admin PATCH; auto-creates row)                                                                  | `properties/{views,serializers,urls}.py`                                | curl as admin/member/guest: 200/200/403-on-patch matrix                                                                 | none          |
| 1.4 | Admin registration optional — skip (upstream doesn't use Django admin). Add model `__str__`s + indexes review instead                                 | `properties/models.py`                                                  | `python manage.py check`                                                                                                | none          |
| 1.5 | Contract tests for models + toggle (`test_custom_properties_app.py` scaffold, pattern: `apps/api/plane/tests/contract/app/test_time_tracking_app.py`) | `apps/api/plane/tests/contract/app/test_custom_properties_app.py` (new) | `pytest apps/api/plane/tests/contract/app/test_custom_properties_app.py`                                                | none          |

### P2 — Property/option/value APIs (6 tasks)

| #   | Task                                                                                                                                                                                                                                                       | Files                                    | Verify                                                                                | Upstream risk |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |
| 2.1 | Definitions CRUD (§4.1) incl. nested `options` create, OPTION-only validation, soft-delete cascade                                                                                                                                                         | `properties/{views,serializers,urls}.py` | curl matrix + contract tests: create Status w/ 3 colored options, list, patch, delete | none          |
| 2.2 | Options CRUD (§4.2) incl. reorder (`sort_order`) and `is_default`                                                                                                                                                                                          | same                                     | contract tests                                                                        | none          |
| 2.3 | Per-issue values GET/POST with replace semantics (§4.3), `is_multi` enforcement, option-belongs-to-property validation                                                                                                                                     | same                                     | contract tests incl. cross-property option rejection                                  | none          |
| 2.4 | Bulk values endpoint (`work-item-property-values/?work_item_ids=`), 100-id cap, single query                                                                                                                                                               | same                                     | contract test with 3 issues × 2 properties; `assertNumQueries`                        | none          |
| 2.5 | Kill-switch + project-toggle enforcement on every endpoint (403 / empty-list behavior per §8)                                                                                                                                                              | same                                     | contract tests with flag off                                                          | none          |
| 2.6 | Default-option application on issue create: a `post_save`-free approach — values endpoint `GET` synthesizes `is_default` option for issues with no row? **No** — keep explicit: apply defaults in the modal (frontend) only; document the decision in code | `properties/views.py` docstring          | test: new issue has no value rows until user/modal sets one                           | none          |

### P3 — Frontend foundation (5 tasks)

| #   | Task                                                                                                                                                  | Files                                                                                                        | Verify                                                                                                   | Upstream risk |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- | ------------- |
| 3.1 | `types.ts` finalized to serializer payloads; `issue-properties.service.ts` (APIService subclass; pattern `ce/services/time-tracking.service.ts`)      | new files                                                                                                    | `pnpm typecheck`                                                                                         | none          |
| 3.2 | `custom-properties.store.ts` (feature, defs, options) + `property-values.store.ts` (values map, debounced bulk fetch queue, optimistic `updateValue`) | new files                                                                                                    | unit-test the debounce/batch logic if vitest present; else typecheck + manual                            | none          |
| 3.3 | Wire store into CE root store                                                                                                                         | **B5** (`ce/store/root.store.ts`, ~3 lines)                                                                  | app boots, store visible in devtools                                                                     | B5            |
| 3.4 | Settings page: route + page + `project-properties-settings.tsx` with feature toggle switch (admin-gated), empty-state                                 | **A3** (`routes/core.ts`, 2 lines), **A4** (`settings/project.ts`, ~9 lines), new route files, new i18n keys | Navigate to `/settings/projects/:id/features/custom-properties`, toggle flips via PATCH, survives reload | A3, A4        |
| 3.5 | Feature gating helper `useCustomPropertiesEnabled(projectId)` used by every later component                                                           | new file                                                                                                     | with toggle off, hook returns false everywhere                                                           | none          |

### P4 — Status renderer + spreadsheet columns (6 tasks)

| #   | Task                                                                                                                                                                                       | Files                                                                         | Verify                                                                                                                                                                                                             | Upstream risk |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- |
| 4.1 | `status-cell.tsx`: filled cell, contrast-computed text (`packages/utils/src/color.ts` `getContrastRatio`), empty-state, dropdown popover of color swatches                                 | new files                                                                     | Storybook-less manual check on a seeded project; keyboard nav works                                                                                                                                                | none          |
| 4.2 | `registry.ts` with `OPTION → StatusPropertyCell`                                                                                                                                           | new file                                                                      | typecheck                                                                                                                                                                                                          | none          |
| 4.3 | `additional-headers.tsx` (`CustomPropertyHeaderCells`): `<th>` per active property matching `spreadsheet-header-column.tsx` metrics (`h-11 min-w-36`, border classes) — no sort menu in v1 | new file                                                                      | renders standalone in isolation with mocked store                                                                                                                                                                  | none          |
| 4.4 | `additional-columns.tsx` (`CustomPropertyValueCells`): `<td>` per property via registry; `enqueueValueFetch` on mount                                                                      | new file                                                                      | same                                                                                                                                                                                                               | none          |
| 4.5 | **Insert the two spreadsheet seams**                                                                                                                                                       | **A1** (`spreadsheet-header.tsx`, 2 lines), **A2** (`issue-row.tsx`, 2 lines) | Flag off ⇒ DOM identical (diff rendered HTML); flag on ⇒ column appears, edits persist, sub-issue rows align; `RenderIfVisible` scroll triggers batched value fetches (≤1 request per scroll burst in network tab) | **A1, A2**    |
| 4.6 | Guest/read-only behavior: `disabled` cells (respect `canEditProperties` passthrough)                                                                                                       | `additional-columns.tsx`                                                      | login as guest: visible, not editable                                                                                                                                                                              | none          |

### P5 — Column-type picker + property management (5 tasks)

| #   | Task                                                                                                                                       | Files                                      | Verify                                                                              | Upstream risk |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------- | ------------- |
| 5.1 | `column-type-picker.tsx`: 10 types, Status enabled, rest greyed w/ "soon"                                                                  | new file                                   | menu renders; disabled types inert                                                  | none          |
| 5.2 | `property-form-modal.tsx`: name, options editor (add/rename/delete, color palette `LABEL_COLOR_OPTIONS` + hex, drag reorder, default flag) | new file                                   | create Status "Deal stage" with 4 options end-to-end                                | none          |
| 5.3 | "+" header cell in `additional-headers.tsx` opening the picker (admin-only)                                                                | existing new file                          | member sees no "+", admin does                                                      | none          |
| 5.4 | Settings page property list: CRUD table reusing the modal; delete confirm w/ "values will be removed" copy                                 | `settings/project-properties-settings.tsx` | full CRUD from settings; spreadsheet reflects changes without reload (store shared) | none          |
| 5.5 | Column reorder (drag header or settings list → `sort_order` PATCH)                                                                         | new files                                  | order persists across reload                                                        | none          |

### P6 — Kanban cards, issue modal, sidebar/peek (5 tasks)

| #   | Task                                                                                                                                                                                                                                                     | Files                          | Verify                                                                | Upstream risk      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | --------------------------------------------------------------------- | ------------------ |
| 6.1 | `layouts-additional-properties.tsx`: filled chips per OPTION value; fill stub                                                                                                                                                                            | **B3** (delegation) + new file | kanban + list card show Status chip; flag off ⇒ nothing               | B3                 |
| 6.2 | `modal-additional-properties.tsx` impl: property inputs (Status dropdown w/ default option preselected); staged values applied after issue create (`workItemId` prop arrives on edit; on create, submit values post-create keyed off the modal provider) | **B1** + new files             | create issue with Status set from modal; default option auto-selected | B1                 |
| 6.3 | `sidebar-additional-properties.tsx` impl: property rows under the built-in list                                                                                                                                                                          | **B2** + new file              | detail page sidebar edits persist                                     | B2                 |
| 6.4 | Peek view: implement `useWorkItemProperties` to hydrate values for the peeked issue                                                                                                                                                                      | **B4**                         | peek any issue: values render without opening detail page             | B4                 |
| 6.5 | i18n pass: all new strings through `packages/i18n` `en` locale (per repo `translate` skill rules)                                                                                                                                                        | i18n JSON (append-only)        | `pnpm lint` i18n checks                                               | none (append-only) |

### P7 — Hardening, optional payload hook, docs (5 tasks)

| #   | Task                                                                                                                            | Files                                                                                                | Verify                                                                            | Upstream risk |
| --- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------- |
| 7.1 | Full backend contract suite (§12 list) green + `assertNumQueries` guards                                                        | test file                                                                                            | `pytest`                                                                          | none          |
| 7.2 | _(optional)_ Detail-serializer hook (§4.4)                                                                                      | **A7** (5 lines)                                                                                     | detail payload has `property_values` only when enabled; contract test both states | A7            |
| 7.3 | _(optional)_ Public `/api/v1/` parity endpoints mirroring `developers.plane.so` work-item-types nesting (external integrations) | `properties/api/**` new files + 1 marked include in `plane/api/urls` **only if** pursued — else skip | v1 curl matrix                                                                    | deferred      |
| 7.4 | Update `docs/custom-properties-design.md` status column ("as-built" notes) + README feature table row                           | docs                                                                                                 | review                                                                            | none          |
| 7.5 | Run `scripts/fork-divergence-report.sh`; reconcile against §6 budget; record the number in the PR description                   | —                                                                                                    | report matches budget exactly                                                     | none          |

Rough totals: P0=5, P1=5, P2=6, P3=5, P4=6, P5=5, P6=5, P7=5 → **42 tasks**.
Core-file-touching tasks: 0.2, 3.3, 3.4, 4.5, 6.1–6.4, 7.2 — nine tasks, each
touching only its §6 rows.

---

## 10. Upgrade runbook

### 10.1 One-time setup

```bash
git remote add upstream https://github.com/makeplane/plane.git   # this checkout already has it as "origin"
git fetch origin  # origin == makeplane/plane here; fork remote is "fork"
```

(Note: in this clone `origin` IS upstream and `fork` is the team remote —
keep that mapping; commands below use `origin/preview` as upstream.)

### 10.2 Merge cadence & procedure (per upstream release, ~monthly)

1. `git fetch origin && git checkout -b merge/upstream-$(date +%Y%m%d) preview-fork-main`
2. **Pre-merge snapshot**: `scripts/fork-divergence-report.sh > /tmp/pre.txt`
3. `git merge origin/preview`
4. **Expected conflict surface** (everything else conflicting is a regression —
   investigate):
   - Group A files (§6): context-line conflicts around the marked insertions in
     `spreadsheet-header.tsx`, `issue-row.tsx`, `routes/core.ts`,
     `settings/project.ts`, `settings/common.py`, `urls.py`,
     (`serializers/issue.py` if A7 built). Resolution: take upstream's version
     of the surrounding code, re-apply the marked line(s). The marker makes
     `git diff` review mechanical.
   - Group B stubs: if upstream changed a stub signature, take upstream's file
     wholesale, then re-add the single delegation line and adjust the
     delegate's props in `ce/custom-properties/**` (fork-owned, conflict-free).
   - Time-tracking's known surfaces (`db` migrations 0122+, features-list) —
     out of scope here but handled in the same session.
5. **Re-apply check**: `grep -rn "FORK: custom-properties" apps packages | wc -l`
   must equal the pre-merge count; `scripts/fork-divergence-report.sh` diff vs
   `/tmp/pre.txt` must show only upstream-driven changes.
6. **Migration graph check**: `python manage.py makemigrations --check --dry-run`
   and `python manage.py migrate --plan` — `properties.0001` must still resolve
   (it depends only on `db.0121`, which upstream never rewrites).
7. **CI gate** (all must pass before merging to the fork mainline):
   `pnpm lint`, `pnpm typecheck`, web build, `pytest apps/api/plane/tests/contract/`,
   plus the §12 smoke checklist on the compose stack.
8. Merge, tag `fork-sync-<upstream-sha>`.

### 10.3 If upstream moves a seam

- Stub file deleted/renamed (e.g. `modal-additional-properties.tsx` →
  something new): find the new consumer via
  `grep -rn "AdditionalProperties" apps/web/core/components/issues/issue-modal/`,
  move the delegation. The implementation in `ce/custom-properties/**` is
  untouched.
- Spreadsheet refactor (header/row files rewritten): re-locate the column map
  loops (search `spreadsheetColumnsList.map`), re-insert A1/A2. Because the
  seam components are self-contained observers taking only `issue`/`disabled`,
  re-insertion is 2 lines regardless of how upstream reshapes the table.

### 10.4 If upstream ships custom properties into CE (`plane.db`)

This is the planned end-of-life for the fork feature, not a failure mode:

1. Freeze fork endpoints (announce read-only window).
2. Compare schemas (`manage.py sqlmigrate` upstream vs our tables). Our tables
   mirror names/fields, so expect near-identity; write a data migration for any
   drift (e.g. `logo_props` key shapes).
3. `python manage.py migrate db <their-migration> --fake` where their migration
   creates the identical tables **after** detaching ours:
   `migrate properties zero --fake` + drop `plane.properties` from
   `INSTALLED_APPS` (delete lines A5/A6), keeping the data in place. Rehearse
   on a staging copy first — this is the R2 payoff.
4. Delete `ce/custom-properties/**` and the group A/B insertions; adopt
   upstream UI.

### 10.5 Rollback (feature misbehaves post-merge)

1. Instance kill switch: set `CUSTOM_PROPERTIES_ENABLED=0` (the flag is now on
   by default, so unsetting it does nothing — it must be explicitly set to
   `"0"`) → all endpoints 403, UI renders stock. No deploy rollback needed.
2. Full removal: revert the group A/B lines (one grep-guided commit),
   `manage.py migrate properties zero`, remove the app dir. No upstream table
   was ever altered.

---

## 11. Risks & mitigations

| #   | Risk                                                                                                                    | Likelihood | Impact                          | Mitigation                                                                                                                                                                                                                                                           |
| --- | ----------------------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | Upstream refactors the spreadsheet (A1/A2 context drift)                                                                | Medium     | Low                             | Seam components take minimal props; §10.3 re-insert procedure; markers make loss detectable (`grep` count in CI).                                                                                                                                                    |
| R2  | Upstream ships CE tables named `issue_properties` etc. → `migrate` collision                                            | Low–Medium | High if unplanned               | Deliberate mirror bet + rehearsed adoption path (§10.4). Detection: upgrade runbook step 6 fails loudly at `migrate --plan`. Fallback if schemas diverge badly: rename our tables via `ALTER TABLE` migration inside `plane.properties` and bridge with a data copy. |
| R3  | Values contract mismatch with real upstream EE (Appendix B unknowns: value payload key shapes, `logo_props` color keys) | Medium     | Low                             | Unverified pieces are isolated in serializers + one JSON blob; contract tests document OUR shape; adoption diff is mechanical.                                                                                                                                       |
| R4  | Spreadsheet perf: N properties × M rows (extra `<td>`s + bulk fetch)                                                    | Low        | Medium                          | One debounced bulk request per scroll burst (100-id cap); store-level memoized observers per cell; `RenderIfVisible` already virtualizes rows (verified `issue-row.tsx:94`). Guard with `assertNumQueries` server-side.                                              |
| R5  | Stub-signature churn in group B files                                                                                   | Low        | Low                             | Stubs contain only a delegation line (§6 rule); re-plumbing is minutes.                                                                                                                                                                                              |
| R6  | Feature interacts with upstream work-item-types if the team later enables EE-ish types                                  | Low        | Medium                          | `issue_type` FK already models it (properties scoped to a type behave per upstream semantics); project-scoped (`NULL`) properties keep working; documented in §3.2.                                                                                                  |
| R7  | Fork developers "just add a field" to `db.Project`/serializers over time                                                | Medium     | High (defeats the whole design) | Divergence report in CI fails the build when unmarked diffs appear in upstream-owned paths; this doc is the policy reference.                                                                                                                                        |
| R8  | AGPL obligations                                                                                                        | —          | —                               | All additions are AGPL-3.0 like the rest of CE; file headers per repo convention (this doc included). No upstream EE code may be copied — mirror from **public API docs only** (all schema knowledge in this doc is from `developers.plane.so` or this repo).        |

---

## 12. Test plan

### Backend (contract tests — `apps/api/plane/tests/contract/app/test_custom_properties_app.py`, pattern: `test_time_tracking_app.py`)

1. **Flags**: all endpoints 403 with env off; empty/403 with project toggle off;
   toggle PATCH admin-only (member/guest → 403).
2. **Definitions**: create Status with nested options (colors in `logo_props`);
   non-OPTION `property_type` → 400; PATCH display_name/`is_active`; soft
   delete cascades to options/values; list excludes soft-deleted; guest can
   read, member cannot create.
3. **Options**: CRUD, reorder persists `sort_order`, `is_default` uniqueness
   per property (last-write wins, verified), cross-project access denied.
4. **Values**: POST replace semantics (single-select → exactly one live row);
   `is_multi=False` rejects 2 values; option from another property → 400;
   GET per-issue; bulk endpoint shape + 100-id cap + `assertNumQueries(≤3)`;
   values for soft-deleted property/option excluded.
5. **Detail hook (if A7 built)**: `property_values` key present ⇔ both flags on;
   absent otherwise (byte-parity guard on the disabled path).
6. **Migration**: `migrate properties` on empty DB; `migrate properties zero`;
   `makemigrations --check` clean.

### Frontend

1. **Static gates**: `pnpm lint` (OxLint, single root config per
   `docs/linting.md`), `pnpm typecheck`, production build.
2. **Flag-off parity**: with feature disabled, rendered spreadsheet DOM for a
   seeded project is identical to a stock build (manual DOM diff once per
   release; cheap and catches seam leaks).
3. **Smoke checklist** (compose stack, seeded project, run per PR touching this
   feature and after every upstream merge):
   - enable feature in settings → "+" appears (admin) / doesn't (member);
   - create Status column w/ 3 colors → column renders filled cells; contrast
     readable on light + dark theme;
   - set/change/clear a value inline; reload persists; optimistic update
     rolls back on forced API failure (devtools offline);
   - scroll 200-row spreadsheet → batched value fetches only, no jank;
   - kanban card chip, modal input (default preselected), sidebar row, peek
     view all reflect the same value;
   - guest sees values everywhere, can edit nowhere;
   - disable feature → everything vanishes, no console errors.
4. **Divergence gate**: `scripts/fork-divergence-report.sh` output equals the
   §6 budget (CI).

---

## Appendix A: verified seam inventory

Facts verified by reading this checkout (branch `feat/custom-properties`):

| Seam                                            | Evidence                                                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Edition alias                                   | `apps/web/tsconfig.json:9` `"@/plane-web/*": ["./ce/*"]`; no `ee/` dir exists                                                                                                                                                                                                                                                                                                     |
| Spreadsheet cell map already behind alias       | `core/.../spreadsheet/issue-column.tsx:12` imports `SPREADSHEET_COLUMNS` from `@/plane-web/components/issues/issue-layouts/utils` (map at `ce/.../utils.tsx:97–112`)                                                                                                                                                                                                              |
| Column list assembly                            | `core/.../spreadsheet/spreadsheet-view.tsx:72–78` from `SPREADSHEET_PROPERTY_LIST` (`packages/constants/src/issue/common.ts:213`); header map `spreadsheet-header.tsx:79–89`; row map `issue-row.tsx:390–400`; header details `columns/header-column.tsx:39` from `SPREADSHEET_PROPERTY_DETAILS` (`common.ts:230`)                                                                |
| Cell renderer contract                          | `TSpreadsheetColumn` `packages/types/src/view-props.ts:268–273`; cell metrics `issue-column.tsx:48` (`h-11 min-w-36`)                                                                                                                                                                                                                                                             |
| Colored property reference                      | `spreadsheet/columns/state-column.tsx:21–39` → `core/components/dropdowns/state/dropdown.tsx`; color math `packages/utils/src/color.ts` (`hexToRgb` L49, `getLuminance` L157, `getContrastRatio` L178, `generateIconColors` L221)                                                                                                                                                 |
| EE-override stubs + consumers                   | listed in §2.1 P2 (all consumer files + line numbers read)                                                                                                                                                                                                                                                                                                                        |
| Issue list payload bypasses serializers         | `apps/api/plane/utils/grouper.py:93–141` (`issue_on_results` → `.values()`); `apps/api/plane/app/views/issue/base.py:161–163`                                                                                                                                                                                                                                                     |
| Issue serializers                               | `apps/api/plane/app/serializers/issue.py`: `IssueSerializer` L761, `IssueListDetailSerializer` L815, `IssueDetailSerializer` L925                                                                                                                                                                                                                                                 |
| Single `db` app / migration chain               | `apps/api/plane/db/migrations/` last = `0124` (`0122–0124` fork-added; last upstream = `0121_alter_estimate_type.py`); `INSTALLED_APPS` `apps/api/plane/settings/common.py:79–100`                                                                                                                                                                                                |
| Separate-app precedent                          | `plane.license` own chain `0001–0006`, FK into user model; root urlconf include points `apps/api/plane/urls.py:17–23`                                                                                                                                                                                                                                                             |
| Work-item-type scaffolding (no property models) | `apps/api/plane/db/models/issue_type.py` (`IssueType` L14, `ProjectIssueType` L35); `db.Project.is_issue_type_enabled` `project.py:99`                                                                                                                                                                                                                                            |
| Fork feature precedents                         | `is_time_tracking_enabled` `project.py:98`; settings nav `packages/constants/src/settings/project.ts:82–88,129`; route registration `apps/web/app/routes/core.ts:327–328`; CE service pattern `ce/services/time-tracking.service.ts`; CE root store extension `ce/store/root.store.ts:12–19`; contract-test pattern `apps/api/plane/tests/contract/app/test_time_tracking_app.py` |
| Divergence baseline                             | `git diff --stat origin/preview...HEAD` = 49 files (time-tracking + docs), ~6 upstream-owned files edited                                                                                                                                                                                                                                                                         |

## Appendix B: unverified mirror-targets (verify against Plane before/while building)

Confirmed from Plane's public API reference (`developers.plane.so`, July 2026):
endpoint nesting `/api/v1/workspaces/{slug}/projects/{project_id}/work-item-types/{type_id}/work-item-properties/`
and `.../work-items/{work_item_id}/work-item-properties/{property_id}/values/`;
property fields `id, name, display_name, description, property_type,
relation_type, logo_props, sort_order, is_required, is_active, is_multi,
default_value (array), settings, validation_rules, options (nested create),
external_source, external_id, formula_config`; `property_type` enum
`TEXT, DATETIME, DECIMAL, BOOLEAN, OPTION, RELATION, URL, EMAIL, FILE, FORMULA`;
`relation_type` enum `ISSUE, USER`; option fields `name, sort_order,
description, logo_props, is_active, is_default, parent, property, external_*`.

**Could NOT verify** (plane-ee source is private; docs are silent) — these are
proposed and marked "mirror-target, verify against Plane API/EE":

1. **Internal DB table names** `issue_properties` / `issue_property_options` /
   `issue_property_values` — inferred from upstream naming conventions
   (`issue_types` at `issue_type.py:29`). Verify against an EE instance's
   schema before relying on §10.4 fake-adoption.
2. **`IssuePropertyValue` typed columns** (`value_text/boolean/decimal/
datetime/uuid/option`) — inferred; the public values API example shows a
   generic `{value, value_type}` doc placeholder instead of a concrete schema.
3. **Values POST body** `{"values": [...]}` and replace semantics — inferred
   from the docs' "add property values" endpoint; exact key unverified.
4. **`logo_props` color key shape** for options (we use
   `{"in_use":"color","color":{"name","background"}}`) — fork-defined inside an
   upstream-shaped JSON field.
5. Whether upstream auto-provisions a **default work item type** per project
   and requires `issue_type` non-null on properties (we relax to nullable).
6. Upstream EE's **internal** (non-v1) endpoint paths for the web app — our
   internal paths reuse the public v1 vocabulary instead.
