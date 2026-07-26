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

> This grep only covers `.py`/`.ts`/`.tsx`. It will **not** find
> `packages/tailwind-config/variables.css` (the Monday theme's
> `[data-theme="monday"]` block) or `apps/web/package.json` (the
> `@fontsource-variable/figtree` dependency) — CSS and JSON can't carry a
> `FORK:` comment. Check those two by hand during an upgrade; see the "Monday
> theme" and "i18n" rows in the table below. It also won't find markers in
> `.env`/`.sh`/`.yml` files (`.env.example` ×2, `docker-compose.yml`,
> `deployments/**`) — see the "Large attachments" row. Drop the `--include`
> filters (or add `--include=*.env --include=*.sh --include=*.yml`) to catch
> those too.

Everything else we added lives in **new files** that upstream will never touch
(so they can't conflict): `apps/api/plane/properties/**`,
`apps/web/ce/custom-properties/**`, `apps/web/ce/attachment-preview/**`,
`apps/api/plane/app/views/time_tracking/**`, `apps/web/ce/components/analytics/**`,
plus new route/test/doc files.

> **The `ce/` folder itself is not permanent — upstream can delete it out from
> under us with zero warning.** This actually happened on 2026-07-23: two
> upstream refactors deleted their entire `apps/web/ce/` tree (the historical
> Community-Edition swap-in folder our features are built on) and the
> `@/plane-web/*` tsconfig alias, consolidating everything into
> `apps/web/core/**`. Where upstream had only _moved_ a file we'd modified,
> git's rename-tracking carried our diff over fine. Where upstream _deleted_ a
> render slot outright (not just the file — the import + JSX call at the
> call site too), our feature's UI hookup vanished **silently, with no merge
> conflict reported**, because our side had never touched those exact lines.
> The tsconfig alias and every affected call site had to be manually restored
> and re-marked `FORK:` (see `apps/web/core/store/root.store.ts`,
> `apps/web/core/components/issues/issue-detail/sidebar.tsx`,
> `peek-overview/properties.tsx`, `issue-modal/form.tsx`,
> `issue-layouts/properties/all-properties.tsx`,
> `issue-detail/issue-activity/{activity-comment-root,root}.tsx`).
> **The takeaway for every future upgrade: after resolving Git's own
> conflicts, also diff the merged tree against the pre-merge commit for each
> file in the table below** (`git diff <pre-merge-sha> -- <file>`) to catch
> this "no conflict, but silently broken" class — don't trust silence.

**Conflicts can only happen in the marked files below** (plus the
no-conflict-but-broken class described above). There are ~35 of them,
most edits 1–12 lines. Keep this list handy during a merge:

| Area                                 | Marked files                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Custom properties**                | `packages/types/src/settings.ts`, `packages/constants/src/settings/project.ts`, `apps/web/core/components/settings/project/sidebar/item-icon.tsx`, `apps/web/app/routes/core.ts`, `apps/web/core/store/root.store.ts` (moved from `ce/store/root.store.ts` — the ce/ subclass was collapsed into `CoreRootStore`, our 3 store fields now live directly in it), `apps/web/core/components/issues/issue-layouts/spreadsheet/{spreadsheet-header,issue-row}.tsx`, the 4 CE stub files under `apps/web/ce/components/issues/**`, `apps/web/ce/hooks/use-issue-properties.tsx`, `apps/web/core/components/issues/issue-detail/sidebar.tsx`, `peek-overview/properties.tsx`, `issue-modal/form.tsx`, `issue-layouts/properties/all-properties.tsx` (these 4 re-wire the render slot that upstream's ce/ deletion silently dropped — see the warning above), `apps/api/plane/settings/common.py`, `apps/api/plane/urls.py` |
| **Time tracking / Jira**             | `apps/api/plane/app/{serializers,urls,views}/__init__.py`, `apps/api/plane/db/models/__init__.py`, `apps/api/plane/db/models/project.py`, `apps/api/plane/utils/email.py`, `apps/web/core/components/analytics/tabs.tsx` (moved from `ce/components/analytics/tabs.tsx` — its 4 sibling imports now go through `@/plane-web/*` since those folders stayed in ce/), `apps/web/ce/components/issues/worklog/property/{root,index}.tsx`, `apps/web/ce/components/issues/worklog/activity/{root,worklog-create-button}.tsx`, `apps/web/core/components/issues/issue-detail/sidebar.tsx`, `peek-overview/properties.tsx`, `issue-activity/{activity-comment-root,root}.tsx` (re-wired render slots), `apps/web/core/components/project/settings/features-list.tsx` (+ the settings-nav files shared with custom properties)                                                                                              |
| **Attachment preview**               | `apps/web/core/components/issues/attachment/{attachment-list-item,attachment-item-list}.tsx`, `apps/api/plane/app/views/issue/attachment.py`, `apps/api/plane/api/views/asset.py`, `apps/api/plane/app/views/asset/v2.py`, `apps/api/plane/space/views/asset.py` (all three now use upstream's own `SCRIPT_CAPABLE_MIME_TYPES` denylist — see the SVG-XSS note below)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Custom dashboards**                | `apps/api/plane/settings/common.py`, `apps/api/plane/urls.py`, `apps/web/core/store/root.store.ts` (see custom-properties row above), `apps/web/app/routes/core.ts`, `packages/constants/src/workspace.ts`, `apps/web/core/components/workspace/sidebar/helper.tsx` (moved from `ce/components/workspace/sidebar/helper.tsx` via rename-tracking — survived cleanly) (see `docs/custom-dashboards-design.md` §6 for exact line counts)                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Large attachments**                | `apps/api/plane/license/api/views/instance.py`, `.env.example`, `apps/api/.env.example`, `docker-compose.yml`, `deployments/aio/community/{start.sh,variables.env,README.md}`, `deployments/cli/community/{docker-compose.yml,variables.env}` — the `FILE_SIZE_LIMIT` 200MB default (upstream default 5MB); see [DEPLOYMENT-FILE-SIZE-LIMIT.md](./DEPLOYMENT-FILE-SIZE-LIMIT.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Monday theme**                     | `packages/constants/src/themes.ts`, `apps/web/app/root.tsx` — plus `packages/tailwind-config/variables.css` (the `[data-theme="monday"]` block at EOF) and `apps/web/package.json` (the `@fontsource-variable/figtree` dependency), which carry the same feature's changes but can't hold a `FORK:` marker (CSS/JSON)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **i18n (JSON — no marker possible)** | `packages/i18n/src/locales/en/{common,project-settings}.json` — additive keys under `custom_properties`, `project_settings.features.*`, and `common.custom_dashboard`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

> The JSON locale files can't carry a `FORK:` comment. They only conflict if
> upstream adds a key with the exact same name, which Git shows as a normal
> conflict — keep both.

---

## Procedure

Do this on the **`preview`** branch — there is only one branch, it carries
every fork feature including the Monday theme.

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
For each one (and see the ce/-deletion warning above for the silent,
no-conflict-reported class that needs a manual diff check instead):

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

# 5. Apply DB migrations
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

> **Migration numbering — one real trap.** `plane.properties` and
> `plane.dashboards` are separate Django apps, each pinned to upstream's
> `db.0121_alter_estimate_type` (see their `0001_initial.py` dependencies) —
> they cannot clash with a future upstream `db` migration.
>
> Time tracking / Jira are different: their migrations (`db/0122-0124`) live
> **inside the shared `db` app's own numbered chain**, not a separate app. If
> upstream adds its own `db/0122_*` (or later) after this fork branched, `migrate`
> will report two migrations claiming the same number. Resolve with
> `python manage.py makemigrations --merge` (Django will generate a merge
> migration), or manually renumber ours past upstream's latest — check
> `python manage.py showmigrations db` first to see where the fork's and
> upstream's migrations actually landed relative to each other.

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
5. **Monday theme** — selectable from the theme picker like any other theme,
   and applies correctly.
6. **Flag-off safety** — turn a project's custom-properties feature off; the
   spreadsheet/cards look exactly like stock Plane.

---

> **SVG/script stored-XSS — likely to recur.** On 2026-07-23 upstream shipped
> its own fix for the exact same inline-attachment stored-XSS class this fork
> had already patched independently, in the exact same 3 endpoints
> (`GenericAssetEndpoint`, `StaticFileAssetEndpoint`, `EntityAssetEndpoint`).
> Upstream's version (`settings.SCRIPT_CAPABLE_MIME_TYPES`) was adopted since
> it's broader (SVG + JS + HTML + XML, not just SVG) and normalizes the MIME
> type before checking (strips params, lowercases) — closing a bypass our
> own fix didn't. If this shows up again as a conflict, prefer upstream's
> version and just double-check `apps/api/plane/app/views/issue/attachment.py`
> (upstream doesn't own this endpoint, so it never gets fixed for free) is
> using the same constant with the same normalization.

## Finishing up

There is only one branch now — `preview` carries every fork feature,
including the Monday theme (a normal, user-selectable entry in the theme
picker, not a build-time flag). Nothing to fast-forward into a second branch.

```bash
# Merge the verified upgrade back into preview
git checkout preview && git merge upgrade/<date>

# Push
git push fork preview
```

## When upstream changes the _same_ code we did

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
- `CUSTOM_PROPERTIES_ENABLED` — the custom-properties instance kill switch.
  On by default as of 2026-07-26 (no env var needed for a new deployment);
  only set this to `0` if you want to opt an instance out
