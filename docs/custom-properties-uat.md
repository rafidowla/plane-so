<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Custom Properties — UAT & Pilot-Readiness Checklist

Companion to [`custom-properties-design.md`](./custom-properties-design.md). This
is the script a human runs to accept the feature and make the go / no-go call
for a live pilot. Every scenario lists the exact steps and the expected result;
mark each ✅ / ❌ and capture a note on any ❌.

**Scope of v1 (what's being accepted):** a user-defined **Status** column
(OPTION type) — colored options, project-scoped — visible and editable in the
spreadsheet, on kanban/list cards, in the work-item detail sidebar, in the peek
overview, and in the edit modal; managed from **Settings → Features → Custom
Properties** and from a spreadsheet header **"+"**. Gated by an instance kill
switch and a per-project toggle.

---

## 0. Prerequisites & environment

| Item | Expected |
|---|---|
| Instance flag | `CUSTOM_PROPERTIES_ENABLED=1` set on the **api** (and worker) container env |
| Health endpoint | `GET /api/custom-properties/health/` → `{"instance_enabled": true}` |
| DB migration | `plane.properties` migration `0001_initial` applied (tables `issue_properties`, `issue_property_options`, `issue_property_values`, `fork_project_properties_feature`) |
| Test data | At least one project with several work items; one **admin**, one **member**, one **guest** account on that project; ideally a **second** project for the multi-project test (§7) |

> Kill switch: unset `CUSTOM_PROPERTIES_ENABLED` (or set to `0`) and restart the
> api → the entire feature disappears (see §6). This is the rollback lever.

---

## 1. Feature enablement & gating

| # | Steps | Expected |
|---|---|---|
| 1.1 | As **admin**, open Settings → Features → **Custom Properties** | Page loads; "Enable custom properties" toggle visible |
| 1.2 | With the instance flag **off** (`CUSTOM_PROPERTIES_ENABLED` unset) | Toggle is **disabled** and greyed, with the notice "Custom properties are turned off for this deployment…" |
| 1.3 | With the instance flag **on**, flip the project toggle **on** | Toggle turns on; the "Options" management list + "Add property" appear below |
| 1.4 | Reload the page | Toggle stays on (persisted) |
| 1.5 | As a **member** (non-admin), open the same settings URL | "Not authorized" view — members can't manage properties |
| 1.6 | Flip the project toggle **off** again | Management list disappears; columns/chips vanish everywhere (see §6) |

## 2. Property management (create / edit / delete)

| # | Steps | Expected |
|---|---|---|
| 2.1 | Click **Add property** | Type menu: **Status** enabled; Text/Number/Date/Checkbox/Member/URL/Email/File greyed with **"Soon"** |
| 2.2 | Pick **Status** | "New property" modal: name field + two option rows (each: colour swatch, label, Default, delete) |
| 2.3 | Name it "Deal stage", add 3–4 options with distinct colours, set one Default, click **Create** | Modal closes; property appears in the list with its colour chips; success toast |
| 2.4 | Click a colour swatch | Native colour picker opens; chosen colour applies to that option |
| 2.5 | **Edit** the property; rename an option, add one, delete one, change the default; **Save changes** | All changes persist; the option list reflects them without a page reload |
| 2.6 | **Delete** a property | Confirm dialog states values will be removed and it can't be undone; on confirm, the property (and its values) disappear |
| 2.7 | Try to create a property with an empty name / no options | Blocked with a clear error toast |

## 3. Spreadsheet column

| # | Steps | Expected |
|---|---|---|
| 3.1 | Open the project's **Spreadsheet** layout | A trailing column per property (label as header); an admin-only **"+"** header at the far right |
| 3.2 | Scroll right; click an empty Status cell | Option picker opens with colour swatches; a working **search** box (type to filter) |
| 3.3 | Pick an option | Cell fills edge-to-edge with the option colour; label text auto-contrasts (readable on light and dark options) |
| 3.4 | Re-select the same option | Clears the value (cell empties) |
| 3.5 | Reload the page | The set value persists and re-renders |
| 3.6 | Scroll through many rows quickly (watch the network tab) | Values load in **batched** requests (one per burst of rows), not one-per-row |
| 3.7 | Click the header **"+"** as admin | Opens the type picker → can create a property inline |
| 3.8 | As a **member**, open the spreadsheet | Columns and values visible; **no "+"** header; cells editable per project role |

## 4. Cards, modal, sidebar, peek

| # | Steps | Expected |
|---|---|---|
| 4.1 | Set a Status on a work item, view the **kanban** and **list** layouts | The item's card shows a small filled Status **chip**; items with no value show no chip |
| 4.2 | Open a work item's **peek** (click the card) | Properties section includes the Status row with the current value |
| 4.3 | Change the Status from the peek | Persists; the card chip and spreadsheet cell update |
| 4.4 | Open the work item **detail page** sidebar | Same Status row; editable |
| 4.5 | Open the **edit** modal ("…" → Edit) | "Options" section shows the Status input in edit mode |
| 4.6 | *(known gap)* Create a **brand-new** work item via the modal | The Status input is **not** shown during create (set it right after creating). This is the documented v1 limitation |

## 5. Permissions

| # | Steps | Expected |
|---|---|---|
| 5.1 | **Admin**: create/edit/delete properties, set values | All allowed |
| 5.2 | **Member**: set values on work items | Allowed to set values; cannot manage property definitions (no settings access, no "+") |
| 5.3 | **Guest**: view work items | Sees Status columns/chips **read-only** (cells not editable) |
| 5.4 | Attempt a property-create API call as member/guest | Server returns 403 (client also hides the affordance) |

## 6. Upstream-safety (the fork's core promise)

| # | Steps | Expected |
|---|---|---|
| 6.1 | Turn the project feature **off** (or the instance flag off) | Spreadsheet has **no** custom columns; cards have **no** chips; sidebar/peek/modal show **no** custom rows — identical to stock Plane |
| 6.2 | Diff the rendered spreadsheet header cell count on vs off | Off = the stock built-in columns only; no empty trailing column or stray nodes |
| 6.3 | `git merge upstream/preview` dry-run (engineering) | Conflicts limited to the marked `FORK: custom-properties` lines (25 lines / 13 files) + the two additive i18n JSON files |

## 7. Multi-project board (regression guard for the fixed queue bug)

> This exercises the HIGH-severity bug found in review and fixed — worth an
> explicit pass.

| # | Steps | Expected |
|---|---|---|
| 7.1 | Enable the feature and add a Status property **in two different projects**; set values on items in **both** | — |
| 7.2 | Open a **workspace-level / multi-project** view (e.g. "Your Work", or a global board) that shows items from both projects together | **Every** item's Status chip renders correctly — no project's chips are silently blank |
| 7.3 | Scroll to lazily load more mixed-project rows | Newly revealed items also show their correct Status values |

## 8. Edge cases & resilience

| # | Steps | Expected |
|---|---|---|
| 8.1 | Property with **zero** options; try to set a value | Picker shows no options; no crash |
| 8.2 | Option with an odd/edge colour (very light, very dark) | Label stays readable (contrast picker) |
| 8.3 | Two admins edit the same property concurrently | Last write wins; no crash or orphaned options |
| 8.4 | Kill the api mid-edit, retry | Optimistic value rolls back on failure; a later retry succeeds (no permanently-blank cell) |
| 8.5 | Delete a property that has values on many items | Property and all its values removed; no orphan rows left behind |

---

## Known limitations (v1 — by design)

- Only the **Status (OPTION)** type ships; other types are visible but "Soon".
- The **create**-work-item modal does not stage custom-property values (edit mode
  only) — set values immediately after creating.
- **No column drag-reorder** yet (order follows `sort_order`).
- No public `/api/v1/` parity endpoints for external integrations yet.
- Values are **single-select** for Status in v1.

## Rollback

Unset `CUSTOM_PROPERTIES_ENABLED` on the api/worker and restart. All endpoints
return "disabled", all seams render nothing, and stock Plane behaviour is
restored. Data is retained (soft-deleted rows stay); re-enabling restores it.

## Go / no-go criteria (suggested)

- **Go:** §1–§7 all ✅; §8 no data-loss or crash; rollback (§Rollback) verified.
- **Hold:** any ❌ in §5 (permissions), §6 (upstream-safety), or §7 (multi-project
  blanking) — these are correctness/safety gates.
- **Note-and-go:** cosmetic issues in §2/§3/§4 with a tracked follow-up.

## Pilot monitoring (first weeks)

- Watch api logs for 4xx/5xx on `/work-item-properties*` and
  `/work-item-property-values*`.
- Spot-check that turning the feature off for a project cleanly hides everything.
- Collect user feedback on the missing types and create-modal staging to
  prioritise the follow-up.
