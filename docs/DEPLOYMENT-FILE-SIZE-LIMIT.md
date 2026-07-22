<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Raising the attachment size limit on a deployment

The fork now defaults to **200 MB** (`209715200` bytes). Upstream Plane defaults
to 5 MB. If your server was deployed before this change — or was deployed with
an explicit `FILE_SIZE_LIMIT` — that older value is still in effect and will
keep rejecting large files.

**Symptom this fixes:** a Jira migration that brings across small and medium
attachments but silently skips large ones (e.g. "45 MB video migrated, 85 MB
video did not"). The importer counts these as `attachments_skipped_size` on the
import job — it is not a crash and not a network failure.

---

## The limit is enforced in three places

All three must allow the size you want, or the largest one still fails:

| Where | What it controls | Set via |
|---|---|---|
| **api** container | Upload authorization, instance config served to the browser | `FILE_SIZE_LIMIT` in `apps/api/.env` |
| **worker** container | **Jira attachment migration** (runs in the background) | same `apps/api/.env` |
| **proxy** container (Caddy) | `request_body max_size` for browser uploads | `FILE_SIZE_LIMIT` in the root `.env` |

> The one most often missed is the **worker**. The api container can be set
> correctly and uploads through the UI will work, while Jira migration still
> uses the old limit — because the migration runs in the worker, not the api.
>
> The proxy does **not** affect Jira migration (the worker pulls from Jira
> server-side and writes straight to storage), but it does cap manual uploads
> from the browser.

---

## Steps

### 1. Set the value in both env files

```bash
cd /path/to/plane
grep -n FILE_SIZE_LIMIT .env apps/api/.env
```

Each file needs this line — add it if missing, correct it if it differs:

```
FILE_SIZE_LIMIT=209715200
```

Put the value on its own line with **no trailing comment**. Some `.env` parsers
treat `FILE_SIZE_LIMIT=209715200 # 200MB` as the literal string
`209715200 # 200MB`, which makes the API fail to start.

Sizes in bytes, if you want a different ceiling:

| Limit | Value |
|---|---|
| 100 MB | `104857600` |
| 200 MB | `209715200` |
| 500 MB | `524288000` |

### 2. Recreate the affected containers

Environment variables are read at container start, so a `restart` is not always
enough — use `up -d` so Compose recreates them with the new value:

```bash
docker compose up -d --force-recreate api worker beat-worker proxy
```

If your deployment uses the CLI installer instead, set `FILE_SIZE_LIMIT` in
`variables.env` and re-run the installer's restart command.

### 3. Verify it took effect

```bash
docker compose exec api printenv FILE_SIZE_LIMIT; docker compose exec worker printenv FILE_SIZE_LIMIT
```

Both must print `209715200`. Then confirm the application actually parsed it:

```bash
docker compose exec api python manage.py shell -c "from django.conf import settings; print(settings.FILE_SIZE_LIMIT)"
```

If that errors with `ValueError: invalid literal for int()`, the value has a
stray comment or space in it — see step 1.

### 4. Confirm in the UI

Hard-refresh the browser (the size limit is delivered in the instance config at
page load, so a cached tab shows the old number). Open a work item, click
**Add attachment**, and the rejection message should now quote 200 MB.

---

## If a large file still fails after this

Check the import job's counters — they distinguish the two causes precisely:

- **`attachments_skipped_size` went up** → the file is still over the limit
  somewhere. Re-check step 3, especially the **worker**.
- **`attachments_failed` went up** → not a size problem. The download from Jira
  or the upload to storage failed. Check worker logs:

  ```bash
  docker compose logs --tail=200 worker
  ```

  Very large files over a slow link can exceed the download read timeout. It
  defaults to 300 seconds per chunk and is tunable:

  ```
  JIRA_ATTACHMENT_READ_TIMEOUT=600
  ```

  Add it to `apps/api/.env` and recreate the worker.

---

## Storage capacity

Raising the limit does not raise your disk. A migration that pulls in many
large videos can fill the storage volume, and Plane reports that as a failed
upload rather than a clear "disk full" message. Before a large migration, check
headroom on whatever backs your uploads (the MinIO volume, or your S3 bucket
quota):

```bash
docker compose exec plane-minio df -h /data
```
