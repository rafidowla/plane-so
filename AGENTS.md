# Agent Development Guide

## Commands

- `pnpm dev` - Start all dev servers (web:3000, admin:3001)
- `pnpm build` - Build all packages and apps
- `pnpm check` - Run all checks (format, lint, types)
- `pnpm check:lint` - OxLint across all packages
- `pnpm check:types` - TypeScript type checking
- `pnpm fix` - Auto-fix format and lint issues
- `pnpm turbo run <command> --filter=<package>` - Target specific package/app
- `pnpm --filter=@plane/ui storybook` - Start Storybook on port 6006

## Code Style

- **Imports**: Use `workspace:*` for internal packages, `catalog:` for external deps
- **TypeScript**: Strict mode enabled, all files must be typed
- **Formatting**: oxfmt, run `pnpm fix:format`
- **Linting**: OxLint with shared `.oxlintrc.json` config
- **Naming**: camelCase for variables/functions, PascalCase for components/types
- **Error Handling**: Use try-catch with proper error types, log errors appropriately
- **State Management**: MobX stores in `packages/shared-state`, reactive patterns
- **Testing**: All features require unit tests, use existing test framework per package
- **Components**: Build in `@plane/ui` with Storybook for isolated development

## Backend tests (Docker)

The Django/pytest suite for `apps/api` runs in an isolated stack defined by `docker-compose-test.yml` at the repo root.

Prereq (once): `./setup.sh` — generates `apps/api/.env` from `.env.example`.

- Full suite: `docker compose -f docker-compose-test.yml up --build --abort-on-container-exit --exit-code-from api-tests`
- Subset: `docker compose -f docker-compose-test.yml run --rm api-tests pytest -m unit`
- Teardown: `docker compose -f docker-compose-test.yml down -v`

See `apps/api/tests/RUNNING_TESTS.md` for the full walkthrough and troubleshooting; see `apps/api/tests/TESTING_GUIDE.md` for test conventions and fixtures.

<!-- atlas-wire-begin -->

## Atlas / Lorebase — code intelligence + knowledge layer

This repo is wired to Atlas (workspace `plane-so`). Atlas holds the code graph,
blast-radius, layer/coupling analysis, and the team knowledge graph. **Consult it —
don't fly blind:**

- **Before a broad code search**, use `atlas_subgraph` / `knowledge_recall` for a
  structural answer instead of grepping the whole tree.
- **Before changing a function or file**, run `atlas_blast_radius` on the symbol to see
  what will break (d1 = WILL BREAK).
- **Before/after a schema change** (`*.sql`, `*.prisma`, `migrations/**`), run
  `atlas_schema_drift` and record the WHY with `schema_confirm` so DB churn stays minimal.
- **When you make a non-obvious decision**, persist it with `knowledge_store` so the next
  engineer (human or agent) recalls it.
- **After a commit**, the index goes stale — re-run `atlas_index` on the repo root to refresh.

Atlas tools are reached through the `atlas` MCP server (`atlas_tool_invoke`).

<!-- atlas-wire-end -->
