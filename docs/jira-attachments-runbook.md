<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Jira → Plane attachment migration — deploy & test runbook

The Jira importer can bring attachment **files** across on the **API path**
(`--with-attachments` on the CLI, or the "Migrate attachments" checkbox in
**Analytics → Imports**). Unlike the rest of the import, this step transfers
**binaries**: it downloads each file from Jira and uploads it **server-side** into
Plane's object store (S3 or MinIO). Because the upload happens from the server
(not the browser), the store must be reachable **from the API and Celery worker
processes** — that's the one thing to get right before a real run.

- [1. Implementation — wire the store (S3/MinIO)](#1-implementation--wire-the-store-s3minio)
- [2. First-run test checklist](#2-first-run-test-checklist)

---

## 1. Implementation — wire the store (S3/MinIO)

### What runs where

| Trigger                              | Process that uploads files                     | Needs store access |
| ------------------------------------ | ---------------------------------------------- | ------------------ |
| CLI `import_jira --with-attachments` | the container you `exec` into (**api**)        | yes                |
| Wizard "Migrate attachments"         | the **Celery worker** (`run_jira_import_task`) | yes                |

So the storage env below must be present on **both the `api` and `worker`
containers** (add it to `beat` too if you keep env in one place).

### Environment variables

Plane's `S3Storage` (`plane/settings/storage.py`) reads these. Set them per your
store:

| Var                     | AWS S3                          | Self-hosted MinIO                                                         |
| ----------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `USE_MINIO`             | `0`                             | `1` (or `0` if you address MinIO as plain S3)                             |
| `AWS_ACCESS_KEY_ID`     | access key                      | MinIO access key                                                          |
| `AWS_SECRET_ACCESS_KEY` | secret key                      | MinIO secret key                                                          |
| `AWS_S3_BUCKET_NAME`    | bucket name                     | bucket name                                                               |
| `AWS_REGION`            | e.g. `us-east-1`                | can be blank                                                              |
| `AWS_S3_ENDPOINT_URL`   | **leave blank** (boto3 default) | **internal** URL the api/worker can reach, e.g. `http://plane-minio:9000` |
| `FILE_SIZE_LIMIT`       | bytes; default `5242880` (5 MB) | same                                                                      |

### The one gotcha (this is what broke local dev)

`AWS_S3_ENDPOINT_URL` is used **both** to sign browser upload/download URLs **and**
for our server-side upload. If it points at a browser-only address the server
can't reach (e.g. `localhost:9002`), the browser flow works but the **migration
upload fails**. Rules of thumb:

- **AWS S3 (or any public S3-compatible endpoint):** leave `AWS_S3_ENDPOINT_URL`
  blank (or set the real public endpoint) and set `AWS_REGION`. Both browser and
  server reach the same public host — nothing special to do.
- **Self-hosted MinIO:** put MinIO on the same network as the api/worker and set
  `AWS_S3_ENDPOINT_URL` to its **internal** service URL (`http://plane-minio:9000`
  on the compose network). Expose MinIO to browsers separately via your reverse
  proxy if end users also need to open attachments. Plane's stock docker-compose
  already wires the internal endpoint this way.

### Pre-flight checklist for the dev team

- [ ] Bucket exists and the credentials allow **PutObject** and **GetObject** on it.
- [ ] Storage env vars set on the **api** and **worker** containers.
- [ ] `AWS_S3_ENDPOINT_URL` is reachable from inside the worker:
      `docker exec <worker> sh -lc 'curl -sI "$AWS_S3_ENDPOINT_URL"'` returns a response.
- [ ] Outbound HTTPS from api/worker to your Jira host (`https://<org>.atlassian.net`)
      is allowed (needed to download the files).
- [ ] `FILE_SIZE_LIMIT` raised to cover your largest attachment (default is 5 MB).
- [ ] Restart api + worker after changing env.

No code changes are required — this is configuration only. The importer uses the
existing `S3Storage.upload_file(...)` helper.

---

## 2. First-run test checklist

Do this on **staging** with a real Jira token before the production migration.
Attachments are downloaded/uploaded for real here, so it validates the storage
wiring end to end.

### Prerequisites

- Jira: base URL, an account **email + API token** with read access to the project.
- Plane: workspace **slug**, target project **identifier**, and an **initiator**
  user who is a **member** of that project.
- Storage wired per section 1.

### Step 1 — Dry run, smallest project first (no writes)

```bash
docker exec -it <api-container> python manage.py import_jira \
  --slug <slug> --project <PROJECT_IDENTIFIER> --initiator <email> \
  --jira-url https://<org>.atlassian.net --jira-email <email> \
  --jira-token "$JIRA_API_TOKEN" --jira-project <KEY> \
  --with-worklogs --with-attachments
```

- [ ] "Fetched N issue(s)" looks right.
- [ ] "Attachments to migrate: N" is non-zero for a project that has files.
- [ ] Review "Unmapped statuses" / "Unmapped users" warnings (they fall back to the
      default state / the initiator — invite the Jira users into the workspace by
      email first if you want accurate assignees).

### Step 2 — Small real import (add `--execute`)

Re-run the same command with `--execute`, then verify in the Plane UI:

- [ ] Issue count matches (**Would create → Created**).
- [ ] Open a work item that had a file → the **attachment appears** → **click it →
      it downloads/opens**. _(This is the key check: it proves the server-side
      upload AND that Plane can serve the file back.)_
- [ ] Worklogs show under the **Time tracking** section of the work item.
- [ ] Comments are present; parent/sub-task links show where expected.
- [ ] The summary line reports attachments **migrated / skipped (too large) /
      failed** — investigate any **failed** (usually auth or size).

### Step 3 — Idempotency

Run the `--execute` command **again**:

- [ ] "Created: 0, Skipped: N" — issues aren't duplicated.
- [ ] No duplicate attachments on the tickets.

### Step 4 — Full import

Run the large project (e.g. Morguard). Watch the worker/api logs for errors and
spot-check a handful of tickets + files afterwards.

### Step 5 — Wizard path (for non-technical users)

**Analytics → Imports** → select the target project → enter Jira URL / email /
token / project key → tick **Import logged time** and **Migrate attachments** →
**Preview** (confirm the attachment count) → **Start import** → watch the progress
bar → verify as in Step 2.

### Redo / rollback

Imports are additive and idempotent (keyed on `external_source="jira"`). To redo a
project cleanly, delete its imported work items first (deleting an issue cascades
to its worklogs, comments, and attachment `FileAsset` rows), then re-run.

### Known limits (so results aren't a surprise)

- **CSV path can't migrate attachments** — the export lists names/URLs only, and
  those URLs still need Jira auth. Use the API path when files matter.
- Files over `FILE_SIZE_LIMIT` are **skipped and counted**, not fatal.
- Rich text (Jira ADF) is flattened; issue links and custom fields are not
  imported; parent hierarchy comes via the CSV path, not the API path.
