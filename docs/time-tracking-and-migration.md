# Time Tracking, Billing, Reporting & Jira Migration

This fork adds **time tracking with billing and approvals**, **client management**,
**time reporting**, a **Jira → Plane importer** (CLI and self-service UI), and an
**unattended first-boot setup** command for one-click/automated deployments.

All additions are **AGPL-3.0** (the same license as Plane's Community Edition).
See [Licensing notes](#licensing-notes) below.

- [Feature overview](#feature-overview)
- [Time tracking](#time-tracking)
- [Clients & rates](#clients--rates)
- [Timesheets & approval](#timesheets--approval)
- [Reporting](#reporting)
- [Jira import](#jira-import)
- [Unattended instance setup (provisioning)](#unattended-instance-setup-provisioning)
- [API reference](#api-reference)
- [Data model](#data-model)
- [Licensing notes](#licensing-notes)

---

## Feature overview

| Area          | What you get                                                                                                              |
| ------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Time tracking | Per-ticket start/stop timer + manual entry; admins/PMs can log on behalf of a resource                                    |
| Billing       | `is_billable` + a snapshotted billable rate per entry (resource rate → client default)                                    |
| Clients       | A `Client` entity between Workspace and Project; per-client default rate/currency                                         |
| Timesheets    | Weekly submit → approve/reject; approved entries are locked (read-only)                                                   |
| Reporting     | Totals & billable amounts by **resource / project / client / task**, member filter, date presets, utilization, CSV export |
| Jira import   | CLI command **and** an in-app self-service wizard (issues, comments, worklogs)                                            |
| Provisioning  | First-boot command that claims the instance admin and completes setup unattended                                          |

Everything is gated by the per-project flag `is_time_tracking_enabled` (the ticket
widget only appears when it is on).

---

## Time tracking

Enable it per project: **Project settings → Features → Time tracking** (sets
`Project.is_time_tracking_enabled`).

On any work item, the **Time tracking** section in the properties sidebar shows:

- **Start / Stop timer** — a live stopwatch, available to members. One running timer
  per user at a time; stopping it records a worklog (rounded to the minute, at least
  one minute) with `source=timer` ("self-tracked").
- **Log time** — a modal for manual entry (hours/minutes, date, description, work
  type, billable flag). **Manual entry is a PM/admin function** (members self-track
  with the timer); entries are recorded with `source=manual` ("PM-reported") and the
  create API enforces this, so a member cannot log manually and the source cannot be
  spoofed.
- **On-behalf logging** — project **admins/PMs** see a **Resource** picker in the
  modal and can log time for another member. The entry records `logged_by` (the
  resource) and `created_by` (who entered it) for an audit trail.
- **Worklog list** — recent entries; each tagged **self** (timer) or **PM** (manual).
  Locked (approved) entries are read-only.

**Self-reported vs PM-reported** is captured by `source` plus the
`logged_by`/`created_by` audit trail, so you can see who tracks their own time
accurately and who relies on a PM entering it.

**Client visibility:** users with the **guest** role (clients) see only the reported
**total** — never the per-entry list, the self/PM source, or who logged it. The
worklog list API returns a stripped, totals-only payload to guests. Admin/PM client
reports aggregate by project/client and never expose who reported the time.

Billable rate is snapshotted at log time: the resource's rate
(`ResourceCapacity.billable_rate`) if set, otherwise the project's client default
rate.

---

## Clients & rates

**Analytics → Clients** (workspace admins):

- Create/delete clients (name, identifier, default billable rate, currency).
- **Project assignments** — assign each project to a client.

Clients sit between Workspace and Project so time can be rolled up and billed per
client: `Workspace → Client → Project → Issue → Worklog`.

Per-resource weekly capacity and rates are stored in `ResourceCapacity`
(used for utilization and as the default billable rate source).

---

## Timesheets & approval

**Analytics → Timesheets**:

- A member submits their week (`period_start` … `period_end`); all their unlocked
  worklogs in range are attached to a `Timesheet` and marked **submitted**.
- A workspace admin **approves** (entries become **locked**/read-only and stamped
  with approver + time) or **rejects** (entries unlocked, status reset).

---

## Reporting

**Analytics → Time**:

- Group by **resource**, **project**, **client**, or **task** (per-work-item
  breakdown).
- Columns: total hours, billable vs non-billable, **billable amount**, entry count,
  and (for resources) **utilization %** vs weekly capacity; the **task** grouping
  adds a **Project** column and links each row to its work item.
- **Member filter**: narrow the report to one or more people (`user_ids`, already
  supported server-side); the CSV export respects the same filter.
- **Date-range presets** (Today, Yesterday, This week, Last week, This month, Last
  month, Last 7/15/30 days, or a custom range via the two date inputs) — computed
  in the browser's **local time** and honoring the user's **Start of the week**
  profile setting, not UTC.
- In **By resource**, expand a row's chevron for an inline per-task breakdown for
  just that person over the same range (auto-expanded when exactly one member is
  selected and exactly one resource row is returned).
- The **task** grouping is capped at 200 rows for the on-screen/JSON view (a
  truncation notice appears; the grand total is still computed over the full,
  unsliced range) — **Export CSV** is never capped.
- The **task** grouping is scoped to the caller's own project memberships unless
  they're a workspace admin — work-item titles are project content, unlike the
  resource/project/client names the other groupings expose to every workspace
  member.
- Date-range filter and **Export CSV** (server-rendered, respects all filters).
- The **By resource** CSV export additionally appends a second table — a flat
  per-(resource, task) breakdown, so the export isn't limited to whichever
  row you happened to expand on screen. **By task**'s own export stays a
  single table (it's already task-level).

---

## Jira import

Migrates Jira issues into a Plane project, including **comments** and (optionally)
**worklogs** (which land in the time-tracking tables). Imports are **idempotent** —
re-running skips issues already imported (matched on `Issue.external_id` = Jira key).
Mapping:

- Jira **status → Plane state** (fuzzy match by name; unmapped → the project's
  default state, and reported as a warning).
- Jira **user → member**: matched by **email** first, falling back to an exact
  (case-insensitive) match on the member's **display name** or **full name**
  when Jira doesn't return an email — which is the common case, not the
  exception: Jira Cloud hides `emailAddress` from the REST API unless the
  requester is an org admin or the user opted into public visibility.
  Unmapped **assignees** are left unassigned (reported as a warning); unmapped
  **comment/worklog authors** fall back to attributing the entry to the
  initiator (also reported as a warning, so an admin can tell how many
  entries were reattributed instead of it being silent).
- **Priority** and **labels** (labels are created if missing).

There are **two ways** to import: over the **Jira REST API** (richest fidelity)
or from a **CSV export** (no API token needed — see [CSV import](#csv-import-no-api-token)).

> Note: rich Jira description/comment formatting (ADF) is flattened to text, and
> issue links are not migrated. **Attachments** can be migrated on the **API path**
> (opt-in, `--with-attachments`) but not the CSV path. Parent/sub-task **hierarchy**
> is preserved by the **CSV path** but not the API path.

**Attachments (API path).** With `--with-attachments`, the importer streams each
Jira attachment's binary from its authenticated `content` URL straight into Plane's
object store (S3/MinIO) and links it to the work item as a real attachment —
idempotent on the Jira attachment id, so re-runs don't duplicate. Files over
`FILE_SIZE_LIMIT` (default **5 MB**) are skipped and counted; raise that env var
before a run if you need larger files. The original uploader is mapped to the
matching Plane member (else the initiator). The CSV export only lists attachment
names/URLs (not the bytes), and those URLs still require Jira auth to download, so
the CSV path cannot migrate attachments on its own. For deployment (S3/MinIO
wiring) and a first-run test checklist, see the
[attachment migration runbook](jira-attachments-runbook.md).

### Self-service UI

**Analytics → Imports** (workspace admins): pick the target Plane project, enter
your Jira URL / email / API token / project key (or tick **Use sample data** to try
it with no Jira account), **Preview** (a dry run — nothing is written, shows counts,
a sample table, and unmapped warnings), then **Start import** (runs in the
background with a progress bar and a completion summary).

The Jira **API token is never persisted** — it is passed transiently to the
background worker only.

### CLI

For bulk/scripted migrations on self-hosted instances:

```bash
# Dry-run (no writes) — review the mapping first:
docker exec -it <api-container> python manage.py import_jira \
  --slug <workspace-slug> --project <PROJECT_IDENTIFIER> \
  --initiator admin@yourco.com \
  --jira-url https://yourorg.atlassian.net --jira-email you@yourco.com \
  --jira-token "$JIRA_API_TOKEN" --jira-project ENG \
  --with-worklogs --with-attachments

# Execute the import:
docker exec -it <api-container> python manage.py import_jira ... --execute
```

`--with-attachments` downloads the binaries into Plane's object store (needs the
Jira credentials above; ignored for `--sample`).

Jira settings can also come from env vars: `JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`. Use `--sample` for an offline self-test with
bundled fixtures. **Pass the token via the flag/env var — never commit it.**

> The API path uses Jira Cloud's enhanced search (`/rest/api/3/search/jql`, which
> replaced the `GET /rest/api/3/search` endpoint retired in 2025) and falls back to
> the legacy `/rest/api/2/search` for older Jira Server / Data Center.

### CSV import (no API token)

For teams that can't issue an API token (or prefer not to), export issues from
Jira (**Issue navigator → Export → CSV "all fields"**) and import the file
directly. Same idempotency and mapping rules as the API path:

```bash
docker exec -it <api-container> python manage.py import_jira_csv \
  --slug <workspace-slug> --project <PROJECT_IDENTIFIER> \
  --initiator admin@yourco.com --file /tmp/export.csv --with-worklogs
# add --execute to write (default is a dry-run preview)
```

It parses Jira's wide CSV (repeated columns for multi-value fields), maps
**status → state**, **priority**, **labels**, **comments**, **worklogs**
(`Log Work` cells), and links **parent/sub-task hierarchy** via the `Parent key`
column. Worklogs go into the time-tracking tables and are billed using the
resource/client rate rules above.

**What CSV can't carry (vs the API path):**

| Aspect                    | CSV export                                                                                                                                                                                                           |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **User identity**         | Display **name only — no email**. Members are matched by name; worklog/comment authors resolve via a name table harvested from the Assignee/Reporter/Creator columns. Non-matching users fall back to the initiator. |
| **Attachments**           | Only **names/URLs** are in the CSV — the binaries aren't, so the CSV path can't migrate files. The **API path** _can_ (`--with-attachments`).                                                                        |
| **Custom fields**         | Dropped (Plane CE has no custom issue properties).                                                                                                                                                                   |
| **Comments**              | Plain text with a single timestamp; rich formatting/mentions lost.                                                                                                                                                   |
| **Sprints / issue links** | Present in the CSV columns but not imported (Plane CE has no sprint/link model).                                                                                                                                     |
| **Freshness**             | A point-in-time snapshot — no incremental re-sync (re-importing the same file just skips already-imported issues).                                                                                                   |

Prefer the **API path** when you have a token and need email-accurate user
mapping; use **CSV** when a token isn't available or for a quick offline migration.

---

## Unattended instance setup (provisioning)

`setup_instance_unattended` claims the instance admin and completes setup at boot,
so an automatically provisioned instance is never left in the open
"first visitor becomes admin" state, and the customer lands on normal workspace
onboarding instead of the God Mode wizard. It is **idempotent** (no-ops once an
admin exists), so it is safe to run on every boot.

Add this **guarded** line after `register_instance` / `configure_instance` in your
API entrypoint (the guard matters — without the vars it errors, and `set -e` would
crash the boot):

```bash
if [ -n "$INSTANCE_ADMIN_EMAIL" ]; then
  python manage.py setup_instance_unattended --settings=plane.settings.production
fi
```

Then your one-click deploy passes the customer's form values as environment
variables:

| Env var                     | Required | Purpose                                        |
| --------------------------- | -------- | ---------------------------------------------- |
| `INSTANCE_ADMIN_EMAIL`      | yes      | first admin's email                            |
| `INSTANCE_ADMIN_PASSWORD`   | yes      | first admin's password (generate a strong one) |
| `INSTANCE_ADMIN_FIRST_NAME` | no       | defaults to the email local-part               |
| `INSTANCE_NAME`             | no       | instance / company name                        |
| `DEFAULT_WORKSPACE_NAME`    | no       | also creates a workspace owned by the admin    |
| `DISABLE_TELEMETRY`         | no       | set `1` to opt out of telemetry                |

---

## API reference

All endpoints are session-authenticated and project/workspace-scoped.

| Method           | Path                                                                | Notes                                                                 |
| ---------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------- |
| GET/POST         | `/api/workspaces/<slug>/clients/`                                   | client CRUD (admin to mutate)                                         |
| GET/PATCH/DELETE | `/api/workspaces/<slug>/clients/<id>/`                              |                                                                       |
| GET/POST         | `/api/workspaces/<slug>/projects/<pid>/issues/<iid>/worklogs/`      | worklog CRUD; POST `logged_by` for on-behalf (admin)                  |
| GET/POST/DELETE  | `/api/workspaces/<slug>/projects/<pid>/issues/<iid>/worklog-timer/` | current / start / stop                                                |
| GET              | `/api/workspaces/<slug>/timesheets/`                                | list (own; admins see all)                                            |
| POST             | `/api/workspaces/<slug>/timesheets/submit/`                         | submit a period                                                       |
| POST             | `/api/workspaces/<slug>/timesheets/<id>/review/`                    | approve/reject (admin)                                                |
| GET/POST         | `/api/workspaces/<slug>/resource-capacities/`                       | per-resource capacity/rates                                           |
| GET              | `/api/workspaces/<slug>/time-report/`                               | `group_by=resource\|project\|client\|issue`, `user_ids`, `export=csv` |
| POST             | `/api/workspaces/<slug>/projects/<pid>/jira-import/preview/`        | synchronous dry-run                                                   |
| GET/POST         | `/api/workspaces/<slug>/projects/<pid>/jira-import/`                | list jobs / start import                                              |
| GET              | `/api/workspaces/<slug>/projects/<pid>/jira-import/<id>/`           | job status                                                            |

---

## Data model

New models (migrations `0122_*` and `0123_jiraimportjob`):

- **`Client`** — workspace-scoped customer; `Project.client` FK assigns projects.
- **`IssueWorklog`** — a time entry (`logged_by`, `duration`, `source`, billing
  snapshot, approval/lock state, optional `timesheet`).
- **`WorklogTimer`** — a running stopwatch (one active per user).
- **`Timesheet`** — a per-resource period with a submit/approve lifecycle.
- **`ResourceCapacity`** — per-resource weekly capacity + default rates.
- **`JiraImportJob`** — tracks a self-service import (status/progress/result; the
  Jira token is never stored).

---

## Licensing notes

This fork's first-party code is uniformly **AGPL-3.0** (verified across the tree).
To host it as a managed/self-service service under the AGPL you must:

1. **Publish your modified source** to your users (AGPL §13 network clause).
2. Keep all license notices; the combined work stays AGPL-3.0.
3. **Branding/trademark:** "Plane" and its logo are Plane's trademarks — use your
   own product brand and remove Plane's logos (you may still describe the service
   as hosting Plane). Trademark is fact-specific; confirm wording with counsel.

> Note: upstream Plane previously shipped one proprietary file
> (`apps/api/plane/utils/email.py`, "Plane Commercial License"). This fork replaced
> it with a clean-room AGPL implementation. Re-check for newly added proprietary
> files (non-`AGPL-3.0-only` SPDX headers) when syncing with upstream.

This is not legal advice — for a commercial offering, review with IP/open-source
counsel and consider a commercial license from Plane if you need to keep code closed.
