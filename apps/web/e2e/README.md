# E2E — Playwright QA regression suite

Browser tests for the QA flows that used to be checked by hand each round:
login, long-comment collapse, time-entry delete confirmation, timer auto-stop
(PSR-101) and the running-timer indicator (PSR-105), access-denied on shared links (both the /browse URL and the
peek-overview panel), and the Task Type chip placeholder while values load.

## Prerequisites (once per machine)

```bash
./setup.sh                                   # apps/api/.env, if not done yet
docker compose -f docker-compose-local.yml up -d   # db, redis, mq, api on :8000
./scripts/e2e-seed.sh                        # QA users + dedicated e2e workspace/project
cd apps/web && pnpm exec playwright install chromium
```

### Local env adjustments (required since the 2026-10-23 upstream security sync)

`setup.sh` writes env files aimed at the stock `localhost` dev setup. The suite
runs the web app on `http://127.0.0.1:3002` behind Vite's `/api` proxy, so adjust
these before the first run:

1. **`apps/api/.env`: add `http://127.0.0.1:3002` to `CORS_ALLOWED_ORIGINS`.**
   The Vite proxy (`changeOrigin: true`) rewrites `Host` to the API, but the
   browser's `Origin` stays `http://127.0.0.1:3002`. Django's CSRF check and
   upstream's same-origin check for cookie-authenticated writes both reject it
   unless that origin is listed. Symptom: global setup's sign-in gets Django's
   CSRF failure page, and `time/*` writes return 403. Recreate the API container
   after the change (`docker compose -f docker-compose-local.yml up -d api`).
2. **`apps/web/.env`: set `VITE_API_BASE_URL=""`.** The default
   `http://localhost:8000` makes the SPA call the API cross-origin instead of
   through the proxy. Symptom: "Plane didn't start up correctly".
3. **Mark the instance as set up.** The time specs need `is_setup_done`, and the
   instance endpoint caches its answer in Redis for 2 h, so clear the cache too:

   ```bash
   docker compose -f docker-compose-local.yml exec api python manage.py shell -c \
     "from plane.license.models import Instance; Instance.objects.update(is_setup_done=True); from django.core.cache import cache; cache.clear()"
   ```

4. **Sign-in throttle.** Auth endpoints are throttled per IP
   (`AUTHENTICATION_RATE_LIMIT`, default `10/minute`). One run uses about 7
   sign-ins, so a second run within a minute fails in global setup with
   `RATE_LIMIT_EXCEEDED`. Either wait a minute between runs, or set
   `AUTHENTICATION_RATE_LIMIT="100/minute"` in your local `apps/api/.env` (never
   in a deployed one) and recreate the API container.

If building the API dev image fails at `uv sync` with a Python version error,
check `docker run --rm python:3.12.5-alpine python --version`. A stale local tag
can point at a different Python; `docker pull python:3.12.5-alpine` fixes it.

The seed creates scratch users (`qa-visual@plane.test` admin,
`qa-member@plane.test` and `qa-other@plane.test` members,
`uat-outsider@example.com`; password `QaTest#2026` or `$E2E_PASSWORD`) and an
isolated `e2e` workspace with one time-tracking-enabled project plus a second
project `SEC` that only `qa-other` belongs to (used to check that the Time
dashboard redacts private projects). Global setup logs in as admin, member and
other, saving `.auth/{admin,member,other}.json`. It never
touches demo or real data. Tests create and soft-delete their own work items.

## Run

Terminal 1 — the web dev server (the suite targets its `/api` proxy):

```bash
cd apps/web && pnpm dev --port 3002
```

Terminal 2 — the suite:

```bash
pnpm test:e2e                # from the repo root
E2E_BASE_URL=http://127.0.0.1:3002 pnpm test:e2e   # explicit (default)
```

- First run of the dev server takes a while (dependency optimization); the
  suite's timeouts account for it.
- `pnpm --filter web run e2e:headed` to watch it in a window;
  `e2e:report` for the HTML report of the last run.
- Failures land in `apps/web/e2e/.artifacts/` (traces + screenshots).

## Time dashboard spec (`time-dashboard.spec.ts`)

Time tracking v2 UI: live dashboard (rows appear within the 5 s poll, private
rows redacted, ticking elapsed), keyboard task picker, and the "Still working?"
idle check across two tabs (prompt, "I'm still here", expiry -> "Start again"). The idle test runs
in real time (~16 min: min = max = 5 via `PATCH time/settings/`, the smallest window the API accepts;
the real check is random, 20-80 min); set `E2E_SKIP_SLOW=1` to skip it.
`E2E_SHOT_DIR=<dir>` also saves review screenshots. The instance needs
`is_setup_done` set. Audio chime and the browser notification are not asserted
headlessly; check them by hand.

## Time reports spec (`time-reports.spec.ts`)

Time tracking v2 Reports and Settings tabs: admin team view (tiles, per-person
switch, breakdown tables), member self-only view and the 403 codes
(`forbidden_scope`, `forbidden_user`), CSV download, a per-member capacity
override changing Expected, Settings validation and save, the old Analytics
Time tab redirecting to Reports, and the billing / weekly-approval switches.
The spec flips workspace settings and member capacity through
`PATCH time/settings/` and `time/members/...`, and restores them in `finally`.
It reads the date range from the page's own inputs, so it does not depend on
today's date. Uses the same seeded users (admin, member, other) as the
dashboard spec; no seed change needed. `E2E_SHOT_DIR=<dir>` saves review
screenshots.

## Estimates spec (`time-estimates.spec.ts`)

Time tracking v2 S6: a project in `required` estimate mode refuses a state move, timer start and manual
entry with "Add an estimate first" and opens the estimate field; setting it unblocks the move and locks the
original; a later change is a re-estimate that shows in the history (oldest first). Also covers the project
settings estimate-mode select, and that guests see no estimate controls. The seed adds the project guest
`qa-guest@plane.test` and a started-group state named `In Progress` in the E2E project (global setup logs the
guest in; override with `E2E_GUEST_EMAIL`).
The spec restores the project to estimate mode `off` afterwards.

## Time timesheet spec (`time-timesheet.spec.ts`)

Time tracking v2 S4, the My timesheet tab (`/<ws>/time/timesheet/`): start a timer
from the timesheet's own bar (keyboard only), stop it on the work item, and the one
entry shows in both the week grid and the entries-by-date list; an admin can open a
member's week read-only; a member sees no person select and can ask for time on a
past day. Fast (~20 s). The instance needs `is_setup_done` set.

## Adding a flow

Create `apps/web/e2e/<flow>.spec.ts`, use the API helpers in `helpers.ts` to
arrange data (create issue / comment / worklog via the proxied API), assert in
the UI, and clean up in `finally` by deleting the issue. Keep `workers: 1` —
several flows share one user-scoped running timer.
