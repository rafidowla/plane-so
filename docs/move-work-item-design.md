<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Move a work item to another project (PSR-91) + draft project change (PSR-92)

Fork-owned feature. Jira-style "Move": take a work item (optionally with its
sub-items) out of one project and into another project of the same workspace,
carrying its history, and re-mapping everything that is project-scoped.

PSR-92 reuses the same mapping rules so a **draft** can be re-targeted to a
different project before (or while) it is posted.

## 1. Identity

| Decision                     | Choice                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Row identity                 | **The `Issue` row is updated in place** — same UUID. Everything that points at the work item by id (relations in other projects, page mentions, external links by UUID, notifications) keeps working. No copy + delete.                                                                                                                                                                                       |
| Human identifier             | A **new sequence in the target project** (`PSR-12` → `GFV3-40`). Allocated exactly like `Issue.save()` does: `pg_advisory_xact_lock(<target project>)`, `MAX(IssueSequence.sequence) + 1`, and a new `IssueSequence` row in the target.                                                                                                                                                                       |
| Old identifier               | The **source `IssueSequence` row is left in place** and still points at the issue. So `PSR-12` is never re-used in the source project (its max sequence is unchanged), and it doubles as the redirect table — **no migration needed**.                                                                                                                                                                        |
| Old links (`/browse/PSR-12`) | `IssueDetailIdentifierEndpoint` (FORK) falls back to the source `IssueSequence` row when no live item has that sequence in that project, and returns the moved item (with its new `project_id`/`sequence_id`). Membership is checked against the item's **current** project, so a user who is only in the target can follow an old link. The web browse page then `replace`s the URL with the new identifier. |
| Activity                     | One `IssueActivity` per moved item: `field="project"`, `verb="updated"`, `old_value="PSR-12"`, `new_value="GFV3-40"`, `old_identifier=<source project id>`, `new_identifier=<target project id>`, comment `moved the work item from PSR-12 to GFV3-40` (+ a summary of what was dropped).                                                                                                                     |

## 2. Mapping rules (shared by move and drafts — `plane/utils/work_item_mapping.py`)

All rules are **idempotent**: a value that already belongs to the target
project is kept as-is, so the draft endpoints can pass half-mapped form data
through the same function.

| Field                                                                                                             | Rule                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| State                                                                                                             | Same name (case-insensitive) → else first state of the same **group** (default state of that group wins, then sequence order) → else the target's **default** state. Triage states are never targets. The user can override per source state in the dialog (`state_map`).                                                                        |
| Labels                                                                                                            | Matched by name (case-insensitive). Unmatched labels are **dropped** and listed in the dialog. No labels are auto-created.                                                                                                                                                                                                                       |
| Assignees                                                                                                         | Kept only if they are an active **member or admin** (role ≥ 15) of the target — the same rule `IssueCreateSerializer` applies. Others are dropped and listed.                                                                                                                                                                                    |
| Cycle / modules                                                                                                   | Always **cleared** (they are project-scoped). Listed in the dialog.                                                                                                                                                                                                                                                                              |
| Estimate                                                                                                          | Kept only if the target project's active estimate has the **same type** as the source point's estimate **and** a point with the same value; mapped to that point. Otherwise cleared.                                                                                                                                                             |
| Work item type                                                                                                    | Kept if the target project has that type enabled (`ProjectIssueType`), else cleared.                                                                                                                                                                                                                                                             |
| Parent of the moved item                                                                                          | Plane requires parent and child in the same project, so a parent that stays behind is **detached** (activity recorded).                                                                                                                                                                                                                          |
| Sub-items                                                                                                         | User chooses: **move together** (default — the whole same-project subtree moves, each item gets its own new identifier) or **detach** (sub-items stay in the source project and lose their parent link). Archived sub-items in the subtree move too and stay archived (the preview warns).                                                       |
| Relations                                                                                                         | **Kept**. They are id-based and Plane already allows cross-project relations.                                                                                                                                                                                                                                                                    |
| Comments, replies, reactions, links, attachments, description assets, votes, mentions, versions, activity history | **Move with the item** (`project_id` re-pointed). Storage keys are workspace-scoped, so files are not copied.                                                                                                                                                                                                                                    |
| Subscribers                                                                                                       | Kept if they are an active member of the target project (any role); others are dropped (they could no longer open it).                                                                                                                                                                                                                           |
| Work logs (time tracking)                                                                                         | **Move with the item**, entries untouched — duration, billing-rate snapshot, approval/lock state and timesheet link are history and are not rewritten. The dialog warns when the target has time tracking **off**, when entries are **approved/locked**, or when the target bills a **different client**. A running timer on the item moves too. |
| Custom properties (fork)                                                                                          | Matched by property **name + type** among the target's active properties (`issue_type` null or the item's type). OPTION values map by option name; an option with no match is dropped. USER relations keep only target members. Values with no matching property are **dropped** and listed.                                                     |
| Comment descriptions                                                                                              | A comment's `Description` row and its `DescriptionVersion`s carry a project FK and are re-pointed with the comment. This is a forward FK — the relation-policy guard test only sees reverse FKs of `Issue`/`IssueComment`, so check forward FKs by hand when upstream adds one.                                                                  |
| Notifications / recent visits                                                                                     | Existing rows are re-pointed to the target project so in-app links keep opening.                                                                                                                                                                                                                                                                 |
| Intake                                                                                                            | A **pending** intake item (status pending/snoozed) cannot be moved — accept it first. The same applies to every sub-item in _move together_ mode (detach is allowed). The intake record of an accepted item stays in the source intake as history.                                                                                               |

## 3. Permissions

- Source: the caller must be able to **edit** there: project role ADMIN or
  MEMBER (same rule as `allow_permission` — a workspace admin who is an active
  project member also passes). **Guests can't move**, even items they created.
- Target: the caller must be able to **create** there under the same rule.
- Target must be in the same workspace, not archived, and not the source.
- An **archived work item itself** must be restored first (`ITEM_ARCHIVED`);
  `is_draft` legacy items are refused.
- **Archived sub-items** do not block a "move together": they move with the
  sub-tree and **stay archived** in the target (`archived_at` is untouched).
  The preview lists them (`archived_sub_items`, warning `ARCHIVED_SUB_ITEMS_MOVE`)
  and the dialog shows "N archived sub-items will move too: …" before confirm.
  In _detach_ mode they stay in the source like every other sub-item.
- Permission is checked **before** anything else, so a caller without write
  access gets `403` and learns nothing about intake state or whether a target
  project UUID exists / is archived.
- Old identifiers redirect only for active members of the project the item
  lives in now; anyone else gets the same `404` as for an unknown identifier.
- Mapping lookups of ids from a request body (states, labels, estimate points)
  only resolve within the source/target projects; dropped assignees are only
  named if they are members of the workspace.
- The request body is an explicit allowlist (`target_project_id`, `sub_items`,
  `state_map`) — nothing else is read (cf. upstream WEB-8332).
- Draft (PSR-92): changing a draft's project requires active membership of the
  target; posting a draft requires ADMIN/MEMBER on the project it lands in
  (closes a gap where posting only checked the workspace role). The draft
  serializer's `project` field is now read-only — project changes go through
  the mapping path only (it was mass-assignable before).

## 4. Atomicity and side effects

`move_work_item()` runs in **one `transaction.atomic()`**: advisory lock on
the target project, sequence allocation, every re-point/remap, the activity
rows. Any exception rolls everything back (tested). Notifications to
subscribers are sent **after commit** (`transaction.on_commit`) through the
normal `notifications` task as `issue.activity.updated`, so subscribers get it
like any other update; webhooks fire via `model_activity`.

## 5. API

### App API (web client)

```
GET  /api/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/move/preview/
       ?target_project_id=<uuid>&sub_items=move|detach        → MovePlan
POST /api/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/move/
       {"target_project_id": uuid, "sub_items": "move"|"detach", "state_map": {src_state_id: tgt_state_id}}
                                                               → MoveResult
POST /api/workspaces/{slug}/draft-issues/project-mapping/
       {"source_project_id", "target_project_id", "state_id", "label_ids", "assignee_ids",
        "estimate_point", "type_id", "parent_id", "cycle_id", "module_ids"}
                                                               → FieldMapping
```

`MovePlan`:

```jsonc
{
  "source_project": {"id", "name", "identifier"},
  "target_project": {"id", "name", "identifier"},
  "work_item": {"id", "name", "identifier"},          // "PSR-12"
  "next_identifier": "GFV3-40",                        // indicative only
  "sub_items_mode": "move" | "detach",
  "sub_items": [{"id", "name", "identifier"}],          // whole same-project subtree
  "archived_sub_items": [{"id", "name", "identifier"}], // archived items among those that WILL move ("move" mode only; [] when detaching)
  "parent": {"id", "name", "identifier"} | null,      // will be detached
  "states": [{"source": State, "target": State | null, "match": "name"|"group"|"default"|"none", "work_item_ids": [uuid]}],
  "target_states": [State],                            // State = {id, name, group, color}
  "labels": {"kept": [{"source": Label, "target": Label}], "dropped": [Label]},   // Label = {id, name, color}
  "assignees": {"kept": [User], "dropped": [User]},   // User = {id, display_name, avatar_url}
  "subscribers_dropped": [User],
  "cycle": {"id", "name"} | null,                      // cleared
  "modules": [{"id", "name"}],                         // cleared
  "estimate": {"source": {"id", "value"} | null, "target": {"id", "value"} | null, "action": "kept"|"cleared"|"none"},
  "issue_type": {"action": "kept"|"cleared"|"none"},
  "custom_properties": {
    "mapped": [{"source": Prop, "target": Prop, "dropped_options": [str]}],   // Prop = {id, display_name, property_type}
    "dropped": [Prop]
  },
  "worklogs": {"count", "total_minutes", "locked_count", "running_timers",
               "target_time_tracking_enabled": bool, "client_changes": bool},
  "carried": {"comments", "attachments", "links", "relations"},
  "warnings": ["TIME_TRACKING_DISABLED_IN_TARGET", "LOCKED_WORKLOGS", "CLIENT_CHANGES",
               "LABELS_DROPPED", "ASSIGNEES_DROPPED", "PROPERTIES_DROPPED", "CYCLE_CLEARED",
               "MODULES_CLEARED", "ESTIMATE_CLEARED", "PARENT_DETACHED", "SUB_ITEMS_DETACHED", "ARCHIVED_SUB_ITEMS_MOVE"]
}
```

`MoveResult`: `{"id", "project_id", "sequence_id", "identifier", "moved": [{"id", "old_identifier", "new_identifier"}], "detached_sub_item_ids": [uuid]}`

`FieldMapping`: `{"state_id", "state_match", "label_ids", "assignee_ids", "estimate_point", "type_id", "parent_id", "cycle_id", "module_ids", "dropped": {"labels": [Label], "assignees": [User], "cycle": bool, "modules": bool, "estimate": bool, "parent": bool}}`

Errors are `400 {"error": "...", "code": "<CODE>"}` (`SAME_PROJECT`, `INVALID_TARGET`,
`TARGET_ARCHIVED`, `ITEM_ARCHIVED`, `ITEM_IN_INTAKE`, `INVALID_STATE_MAP`), `403`, or
`409 CONFLICT` when a sub-item left the source project / changed parent while the move ran.

Draft endpoints changed: `PATCH /draft-issues/{id}/` with a different
`project_id` re-targets the draft (server re-maps stored state/labels/
assignees/estimate, clears cycle/modules/parent not in the target);
`POST /draft-to-issue/{id}/` accepts a `project_id` different from the draft's
and re-maps the payload the same way.

### Public API v1

`GET|POST /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{work_item_id}/move/` —
GET is the preview (same query params, returns `MovePlan`), POST performs the move
(same body and result as the app endpoint; API-key auth, same permission rules).

## 6. UI

- **Move to project** in the work item actions menu (list/kanban/spreadsheet
  row menus, detail header, peek) for users who can edit the item.
- Dialog: target project picker (projects where the user is admin/member, same
  workspace, excluding the current one) → preview fetched from the server →
  sections for state mapping (override dropdown per source state), labels
  kept/dropped, assignees kept/dropped, cycle/modules cleared, estimate,
  parent detached, sub-items choice (move together / detach) when the item has
  sub-items, a warning listing archived sub-items that move too, custom properties dropped, work-log warnings, and what is carried.
  Confirm button "Move to GFV3".
- After moving: removed from the source list, toast with the new identifier;
  detail page navigates to the new URL; peek closes.
- Drafts: the project picker in the draft modal is enabled when editing a
  draft; switching project calls `project-mapping` and applies the result to
  the form, listing anything dropped.

## 7. Files

Backend (new): `apps/api/plane/utils/work_item_mapping.py`,
`apps/api/plane/utils/work_item_move.py`, `apps/api/plane/app/views/issue/move.py`,
`apps/api/plane/api/views/work_item_move.py`.
Backend (FORK-marked edits): `app/urls/issue.py`, `app/urls/workspace.py`,
`app/views/__init__.py`, `app/views/issue/base.py` (identifier fallback),
`app/views/workspace/draft.py`, `app/serializers/draft.py`, `api/urls/*`.

No migrations: the old identifier is served by the existing `IssueSequence` rows.
