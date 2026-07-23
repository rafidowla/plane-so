<!--
Copyright (c) 2023-present Plane Software, Inc. and contributors
SPDX-License-Identifier: AGPL-3.0-only
-->

# Taking a Plane update into this fork

This fork adds features (custom properties, time tracking, Jira import, in-app
attachment preview, Monday theme) on top of upstream Plane
(`makeplane/plane`, default branch `preview`). This is the one-page procedure a
developer follows to pull a new upstream release **without breaking our
additions**.

**Time:** usually 15–60 min. **Do it on a branch, never straight to
production, and always re-test before deploying.**

---

## The one rule that makes this safe

Every edit we made to an **upstream-owned file** is tagged with a `FORK:`
comment. Find them all in seconds:

```bash
grep -rn "FORK:" apps packages --include=*.py --include=*.ts --include=*.tsx
```

Everything else we added lives in **new files** that upstream will never touch
(so they can't conflict): `apps/api/plane/properties/**`,
`apps/web/ce/custom-properties/**`, `apps/web/ce/attachment-preview/**`,
`apps/api/plane/app/views/time_tracking/**`, `apps/web/ce/components/analytics/**`,
plus new route/test/doc files.

**Conflicts can only happen in the marked files below.** There are ~17 of them,
most edits 1–12 lines. Keep this list handy during a merge:

| Area | Marked files |
|---|---|
| **Custom properties** | `packages/types/src/settings.ts`, `packages/constants/src/settings/project.ts`, `apps/web/core/components/settings/project/sidebar/item-icon.tsx`, `apps/web/app/routes/core.ts`, `apps/web/ce/store/root.store.ts`, `apps/web/core/components/issues/issue-layouts/spreadsheet/{spreadsheet-header,issue-row}.tsx`, the 4 CE stub files under `apps/web/ce/components/issues/**`, `apps/web/ce/hooks/use-issue-properties.tsx`, `apps/api/plane/settings/common.py`, `apps/api/plane/urls.py` |
| **Time tracking / Jira** | `apps/api/plane/app/{serializers,urls,views}/__init__.py`, `apps/api/plane/db/models/__init__.py`, `apps/api/plane/db/models/project.py`, `apps/api/plane/utils/email.py`, `apps/web/ce/components/analytics/tabs.tsx`, `apps/web/ce/components/issues/worklog/property/root.tsx`, `apps/web/core/components/project/settings/features-list.tsx` (+ the settings-nav files shared with custom properties) |
| **Attachment preview** | `apps/web/core/components/issues/attachment/{attachment-list-item,attachment-item-list}.tsx`, `apps/api/plane/app/views/issue/attachment.py` |
| **Custom dashboards** | `apps/api/plane/settings/common.py`, `apps/api/plane/urls.py`, `apps/web/ce/store/root.store.ts`, `apps/web/app/routes/core.ts`, `packages/constants/src/workspace.ts`, `apps/web/ce/components/workspace/sidebar/helper.tsx` (see `docs/custom-dashboards-design.md` §6 for exact line counts) |
| **i18n (JSON — no marker possible)** | `packages/i18n/src/locales/en/{common,project-settings}.json` — additive keys under `custom_properties`, `project_settings.features.*`, and `common.custom_dashboard` |

> The JSON locale files can't carry a `FORK:` comment. They only conflict if
> upstream adds a key with the exact same name, which Git shows as a normal
> conflict — keep both.

---

## Procedure

Do this on the **`preview`** branch first, then fast-forward the Monday branch.

```bash
# 0. One-time: make sure upstream is a remote
git remote get-url origin || git remote add origin https://github.com/makeplane/plane.git

# 1. Fetch the latest upstream
git fetch origin

# 2. Branch off our current preview so nothing is at risk
git checkout preview
git checkout -b upgrade/$(date +%Y-%m-%d)

# 3. Merge upstream in
git merge origin/preview
```

**If Git reports conflicts:** they will only be in the marked files above.
For each one:
```bash
git diff --name-only --diff-filter=U        # list conflicted files
grep -n "FORK:" <file>                       # find our addition inside it
```
Keep **both** sides: upstream's new code **and** the line(s) tagged `FORK:`.
Our additions are almost always independent (a new import, a new list entry, a
new route), so "keep both" is nearly always correct. Then:
```bash
git add <resolved-files>
git merge --continue
```

```bash
# 4. Reinstall deps (upstream may have changed package versions)
pnpm install

# 5. Apply DB migrations (ours are pinned to upstream's chain and won't clash)
docker exec -i -e DJANGO_SETTINGS_MODULE=plane.settings.local <api-container> \
  python manage.py migrate

# 6. Build gates — both MUST be clean before you trust the merge
docker exec -i -e DJANGO_SETTINGS_MODULE=plane.settings.local <api-container> \
  python manage.py check
pnpm turbo run check:types --filter=web

# 7. Run our test suites
docker exec -i -e DJANGO_SETTINGS_MODULE=plane.settings.local <api-container> \
  python -m pytest plane/tests/contract/app/ --create-db -q
```

---

## Smoke test (do this before deploying — the automated checks don't cover UI)

Open a work item and confirm our additions still render:

1. **Custom properties** — Settings → Features → Custom Properties toggles; a
   Status column shows in the spreadsheet.
2. **Time tracking** — the worklog widget on a work item; Analytics → Time /
   Timesheets / Clients / Imports tabs load.
3. **Attachment preview** — click an image/PDF attachment → it opens in the
   in-app viewer.
4. **Jira import** — Analytics → Imports runs a preview.
5. **Monday theme** (Monday branch only) — the theme is selectable and applies.
6. **Flag-off safety** — turn a project's custom-properties feature off; the
   spreadsheet/cards look exactly like stock Plane.

---

## Finishing up

```bash
# Merge the verified upgrade back into preview
git checkout preview && git merge upgrade/<date>

# Bring the same update into the Monday branch
git checkout feat/monday-custom-properties && git merge preview

# Push both
git push fork preview
git push fork feat/monday-custom-properties
```

## When upstream changes the *same* code we did

Rare, but the honest failure mode. If upstream rewrites, say, the attachment
list item or the analytics tabs in the same spot as our `FORK:` line, Git
conflicts and you re-apply our small change against their new structure by hand
(the `FORK:` comment tells you exactly what to re-add). Because our edits are
minimal and clearly marked, this is a few-minutes fix per file, not a rewrite —
that is the whole point of the marker discipline.

## Deployment settings (not code — carry these across any upgrade)

These live in your server's environment, not in git, so they must be
re-checked on each deploy:

- `FILE_SIZE_LIMIT=209715200` — 200 MB uploads + Jira migration. Our fork
  already defaults to this, but an upgrade will **not** change a value already
  set in your server's `.env`, and upstream's own default is 5 MB — so re-check
  it after every merge. Must be set for the **worker** too, not just the api:
  see [DEPLOYMENT-FILE-SIZE-LIMIT.md](./DEPLOYMENT-FILE-SIZE-LIMIT.md)
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` /
  `EMAIL_FROM` — SMTP, for notification + invitation emails (or set them in
  God Mode → Email)
- `CUSTOM_PROPERTIES_ENABLED=1` — the custom-properties instance kill switch
