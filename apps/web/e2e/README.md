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

## Adding a flow

Create `apps/web/e2e/<flow>.spec.ts`, use the API helpers in `helpers.ts` to
arrange data (create issue / comment / worklog via the proxied API), assert in
the UI, and clean up in `finally` by deleting the issue. Keep `workers: 1` —
several flows share one user-scoped running timer.
