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

| Area          | What you get                                                                           |
| ------------- | -------------------------------------------------------------------------------------- |
| Time tracking | Per-ticket start/stop timer + manual entry; admins/PMs can log on behalf of a resource |
| Billing       | `is_billable` + a snapshotted billable rate per entry (resource rate → client default) |
| Clients       | A `Client` entity between Workspace and Project; per-client default rate/currency      |
| Timesheets    | Weekly submit → approve/reject; approved entries are locked (read-only)                |
| Reporting     | Totals & billable amounts by **resource / project / client**, utilization, CSV export  |
| Jira import   | CLI command **and** an in-app self-service wizard (issues, comments, worklogs)         |
| Provisioning  | First-boot command that claims the instance admin and completes setup unattended       |

Everything is gated by the per-project flag `is_time_tracking_enabled` (the ticket
widget only appears when it is on).

---

## Time tracking

Enable it per project: **Project settings → Features → Time tracking** (sets
`Project.is_time_tracking_enabled`).

On any work item, the **Time tracking** section in the properties sidebar shows:

- **Start / Stop timer** — a live stopwatch. One running timer per user at a time;
  stopping it records a worklog (rounded to the minute, minimum 1).
- **Log time** — a modal for manual entry (hours/minutes, date, description, work
  type, billable flag).
- **On-behalf logging** — project **admins/PMs** see a **Resource** picker in the
  modal and can log time for another member. The entry records `logged_by` (the
  resource) and `created_by` (who entered it) for an audit trail.
- **Worklog list** — recent entries; locked (approved) entries are read-only.

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

- Group by **resource**, **project**, or **client**.
- Columns: total hours, billable vs non-billable, **billable amount**, entry count,
  and (for resources) **utilization %** vs weekly capacity.
- Date-range filter and **Export CSV** (server-rendered, respects all filters).

---

## Jira import

Migrates Jira issues into a Plane project, including **comments** and (optionally)
**worklogs** (which land in the time-tracking tables). Imports are **idempotent** —
re-running skips issues already imported (matched on `Issue.external_id` = Jira key).
Mapping:

- Jira **status → Plane state** (fuzzy match by name; unmapped → the project's
  default state, and reported as a warning).
- Jira **user → member** by email (unmapped assignees fall back to the initiator).
- **Priority** and **labels** (labels are created if missing).

> Note: rich Jira description formatting (ADF) is flattened to text. Attachments,
> issue links, and sub-task hierarchy are not migrated.

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
  --with-worklogs

# Execute the import:
docker exec -it <api-container> python manage.py import_jira ... --execute
```

Jira settings can also come from env vars: `JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`. Use `--sample` for an offline self-test with
bundled fixtures. **Pass the token via the flag/env var — never commit it.**

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

| Method           | Path                                                                | Notes                                                |
| ---------------- | ------------------------------------------------------------------- | ---------------------------------------------------- |
| GET/POST         | `/api/workspaces/<slug>/clients/`                                   | client CRUD (admin to mutate)                        |
| GET/PATCH/DELETE | `/api/workspaces/<slug>/clients/<id>/`                              |                                                      |
| GET/POST         | `/api/workspaces/<slug>/projects/<pid>/issues/<iid>/worklogs/`      | worklog CRUD; POST `logged_by` for on-behalf (admin) |
| GET/POST/DELETE  | `/api/workspaces/<slug>/projects/<pid>/issues/<iid>/worklog-timer/` | current / start / stop                               |
| GET              | `/api/workspaces/<slug>/timesheets/`                                | list (own; admins see all)                           |
| POST             | `/api/workspaces/<slug>/timesheets/submit/`                         | submit a period                                      |
| POST             | `/api/workspaces/<slug>/timesheets/<id>/review/`                    | approve/reject (admin)                               |
| GET/POST         | `/api/workspaces/<slug>/resource-capacities/`                       | per-resource capacity/rates                          |
| GET              | `/api/workspaces/<slug>/time-report/`                               | `group_by=resource\|project\|client`, `format=csv`   |
| POST             | `/api/workspaces/<slug>/projects/<pid>/jira-import/preview/`        | synchronous dry-run                                  |
| GET/POST         | `/api/workspaces/<slug>/projects/<pid>/jira-import/`                | list jobs / start import                             |
| GET              | `/api/workspaces/<slug>/projects/<pid>/jira-import/<id>/`           | job status                                           |

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
