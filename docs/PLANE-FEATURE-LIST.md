# Plane.so — Product Feature List & QA Evaluation Guide

**Purpose:** Complete inventory of every feature in the current product, organized for structured end-to-end QA testing. Each section is a testable module; the bullet items are test cases.

**Product context:** This is a fork of Plane Community Edition (AGPL-3.0) with fork-specific additions marked **[FORK]** throughout: time tracking & timesheets, clients & billing rates, custom properties, custom dashboards, Jira importer, comment replies & attachments, in-app attachment preview, 200 MB attachment limit, Monday theme, unattended first-boot provisioning.

**Apps in scope:**

| App                | Port | Purpose                                |
| ------------------ | ---- | -------------------------------------- |
| Web (`apps/web`)   | 3000 | Main product UI                        |
| API (`apps/api`)   | —    | Django REST backend + background jobs  |
| Admin ("God Mode") | 3001 | Instance administration                |
| Space (`/spaces`)  | 3002 | Public published projects/pages        |
| Live (`/live`)     | 3100 | Real-time collaborative editing server |

**Dev environment:** `pnpm dev` starts web + admin. Backend tests run via `docker-compose-test.yml` (see `apps/api/tests/RUNNING_TESTS.md`).

---

## 1. Authentication & Onboarding

**Routes:** `/sign-up`, `/accounts/forgot-password`, `/accounts/reset-password`, `/accounts/set-password`, `/onboarding`, `/create-workspace`, `/invitations`

- [ ] Email + password sign up / sign in
- [ ] Magic-code (passwordless email code) sign in
- [ ] OAuth sign in: Google, GitHub, GitLab, Gitea _(must be configured by instance admin)_
- [ ] Forgot password / reset password / set password flows
- [ ] Sign out
- [ ] Terms & conditions display; not-authorized error views
- [ ] Onboarding wizard: profile setup → create or join workspace → invite members → product tour
- [ ] Workspace invitations: accept/decline via link (`/invitations`, `/workspace-invitations`)
- [ ] Account switching between workspaces

## 2. Workspace Management

**Routes:** `/[workspaceSlug]/`

- [ ] Create workspace (name, slug/URL, logo); slug availability check
- [ ] Workspace home dashboard (see §7)
- [ ] Workspace switcher; resizable sidebar
- [ ] Workspace settings — General: rename, logo, URL, **delete workspace**
- [ ] Workspace themes
- [ ] Roles: Admin / Member / Guest (guests see totals-only on time tracking)

### 2.1 Members (`/settings/members`)

- [ ] Invite members by email with role; invitations list; resend/cancel
- [ ] Member list with role columns; change role; remove member; leave workspace
- [ ] Export members

### 2.2 Billing & Plans (admin only)

- [ ] Billing page, plan/edition badge, upgrade prompts (Pro-gated features show upgrade banners: bulk operations, workspace active cycles, epics)

## 3. Project Management

**Routes:** `/[workspaceSlug]/projects`, `/projects/[projectId]/...`

- [ ] Create project (name, identifier/prefix, description, cover/emoji, lead)
- [ ] Project list (all projects), favorites, join/leave
- [ ] Project settings — General: edit details, network visibility (private/public/secret), **archive/restore**, **delete project**
- [ ] Project members: add/remove, roles, invitations
- [ ] **Feature toggles per project** (Settings → Features): Cycles, Modules, Views, Pages, Intake, **Time Tracking [FORK]**, **Custom Properties [FORK]**
- [ ] Project publish (public deploy board → Space app, see §14)
- [ ] Project navigation header, actions menu, breadcrumbs

### 3.1 States (workflow)

- [ ] CRUD states in groups: Backlog, Unstarted, Started, Completed, Cancelled
- [ ] Drag-drop reorder; set default state; delete state

### 3.2 Labels

- [ ] Label CRUD, colors, label groups, inline create/edit

### 3.3 Estimates

- [ ] Estimate systems (point scales): create/edit/delete, enable/disable, search

### 3.4 Automations

- [ ] Auto-archive closed issues after X months
- [ ] Auto-close inactive issues after X months

## 4. Work Items (Issues)

**Routes:** `/projects/[projectId]/issues`, `/issues/[issueId]`, `/browse/[workItem]` (deep link)

### 4.1 Core CRUD

- [ ] Create issue modal: title, rich description, all properties, "create more" toggle, draft-save, discard confirmation
- [ ] Edit issue detail: title inline, rich-text description editor, identifier
- [ ] Delete issue; **archive** issue; view/restore archived issues (`/archives/issues`)
- [ ] Issue types (Epic switcher is Pro-gated stub)

### 4.2 Properties (sidebar dropdowns)

- [ ] State, priority, assignees, start date, due date
- [ ] Labels, estimates, cycle assignment, module assignment, parent issue
- [ ] Subscribe/unsubscribe to an issue

### 4.3 Structure & relations

- [ ] Sub-issues: create, list, remove, sub-issue count
- [ ] Relations: **blocking**, **blocked by**, **duplicate**, **relates to**
- [ ] Links: attach external URLs with title
- [ ] Attachments: upload (drag/drop, up to **200 MB [FORK]**), list, **in-app preview [FORK]**, download, delete

### 4.4 Activity & comments

- [ ] Unified activity + comment feed with filters (all/comments/activity) and sorting
- [ ] Comment create/edit/delete with rich text; **comment replies [FORK]**; **comment attachments [FORK]**
- [ ] Emoji reactions on issues and comments
- [ ] Mentions in comments/descriptions

### 4.5 Layouts & organization

- [ ] Layouts: **Kanban, List, Spreadsheet, Calendar, Gantt**
- [ ] Grouping by state/priority/assignee/labels/cycle/module/creator/project; sub-grouping
- [ ] Ordering options; display-properties toggles (key, dates, counts, estimates, etc.)
- [ ] Filters + advanced **rich filters** builder; applied-filter chips; clear/save
- [ ] Quick-add rows, drag-drop between groups, per-card quick actions
- [ ] Peek overview (side/full) with properties panel; hover preview card
- [ ] Bulk operations _(Pro-gated banner in this build)_

### 4.6 Drafts

- [ ] Workspace drafts (`/drafts`): auto-saved incomplete issues, restore/discard

## 5. Cycles

**Routes:** `/projects/[projectId]/cycles`, `/cycles/[cycleId]`, `/active-cycles` (workspace), `/archives/cycles`

- [ ] Cycle CRUD (name, date range, description); list/board/Gantt views
- [ ] Cycle status (upcoming/active/completed) derived from dates
- [ ] Add/remove issues; **transfer incomplete issues** to another cycle
- [ ] Cycle detail: issues + analytics sidebar (progress, estimates, assignee/label distribution)
- [ ] Favorite, copy link, quick actions; archive/restore
- [ ] Workspace-level active cycles page _(Pro upgrade banner)_
- [ ] Cycle peek overview

## 6. Modules

**Routes:** `/projects/[projectId]/modules`, `/modules/[moduleId]`, `/archives/modules`

- [ ] Module CRUD; status: backlog / planned / in-progress / paused / completed / cancelled
- [ ] Lead & members assignment
- [ ] Add/remove issues; list and Gantt views
- [ ] Module detail: issues + analytics sidebar + links
- [ ] Favorite, quick actions, archive/restore, peek overview

## 7. Home Dashboard

**Route:** `/[workspaceSlug]/` (workspace home)

- [ ] Greeting header; **Manage Widgets** modal (enable/disable, drag to reorder)
- [ ] Widgets: Recents (issues/pages/projects), Quick Links (CRUD links), Stickies, Recent Activity
- [ ] **Stickies** (`/stickies`): colored sticky notes, drag ordering, delete, search modal

## 8. Views (Saved Filters)

**Routes:** `/projects/[projectId]/views`, `/workspace-views`, `/workspace-views/[globalViewId]`

- [ ] Project views: create/update with filters + ordering; delete; quick actions
- [ ] **Publish/share a view** publicly (anchor URL → Space app)
- [ ] Workspace default views: All Issues, Assigned, Created, Subscribed
- [ ] Custom global views with same filter system

## 9. Pages (Wiki / Docs)

**Routes:** `/projects/[projectId]/pages`, `/pages/[pageId]`

- [ ] Page CRUD; list view; access control (public/private within project)
- [ ] Rich document editor (see §16 for editor capabilities) with title + emoji/logo picker
- [ ] **Real-time collaborative editing** (via live server)
- [ ] Version history: browse versions, restore
- [ ] Navigation pane: outline + info tabs
- [ ] AI assistance in editor _(requires instance AI config)_
- [ ] Export page to PDF
- [ ] Lock/unlock, archive, favorite, subscribe; page labels

## 10. Intake (Triage)

**Route:** `/projects/[projectId]/intake`

- [ ] Intake list + detail split view; create intake issue
- [ ] Statuses: **pending, accepted, declined, snoozed, duplicate**
- [ ] Accept → promotes into project issues; decline; snooze until date; mark duplicate; delete
- [ ] Edit properties before acceptance; inbox filters

## 11. Notifications

**Route:** `/[workspaceSlug]/notifications`

- [ ] Notification inbox with unread count in sidebar
- [ ] Filter by type and read/unread; mark read/unread/all-read
- [ ] Snooze and archive notifications
- [ ] Per-type notification preferences (profile settings)
- [ ] Aggregated email digests (requires SMTP)

## 12. Analytics & Reporting

**Route:** `/[workspaceSlug]/analytics/[tabId]`

- [ ] **Overview** tab: active projects, project insights
- [ ] **Work Items** tab: created vs. resolved trends, priority chart, insight tables, customized-insights modal, scope filters
- [ ] **Time** tab [FORK]: date-range filter, task breakdown
- [ ] **Timesheets** tab [FORK]
- [ ] **Clients** tab [FORK]
- [ ] **Imports** tab [FORK]
- [ ] Export analytics; saved analytic views

### 12.1 Custom Dashboards [FORK]

- [ ] `/custom-dashboard`: widget-based dashboards — distribution pie, age trend bar, project breakdown pie, view-issues table

## 13. Time Tracking [FORK]

_(Enable per project: Settings → Features → Time Tracking)_

- [ ] Log work (worklog) on issues: date, duration, description
- [ ] Live worklog timer: start/stop
- [ ] Timesheets: submit for review; review/approve workflow
- [ ] Clients CRUD with billing rates
- [ ] Resource capacity planning
- [ ] Time reports (analytics Time/Timesheets tabs)
- [ ] Guests see totals only

## 14. Publish / Space App (Public)

**Base path:** `/spaces/{workspaceSlug}/{projectId}` and `/issues/[anchor]`

- [ ] Publish project or page with unique anchor URL
- [ ] Publish toggles: comments on/off, reactions on/off, votes on/off
- [ ] Public layouts: kanban, list, spreadsheet, calendar, gantt (per publish settings)
- [ ] Public filters (state/priority/labels) and display properties
- [ ] Issue peek overview publicly
- [ ] Anonymous/space-user comments, emoji reactions, up/down votes
- [ ] Public intake submission
- [ ] Space auth: email/password, magic code, OAuth
- [ ] Theme toggle in public navbar

## 15. Profile & Personal Settings

**Routes:** `/settings/profile/[tab]`, `/[workspaceSlug]/profile/[userId]`

- [ ] **General**: name, avatar, cover
- [ ] **Security**: change password, deactivate account
- [ ] **Preferences**: theme (incl. **Monday theme [FORK]**), timezone, start of week, language (19 locales)
- [ ] **Notifications**: per-type preferences
- [ ] **API tokens**: create personal access token, list, revoke
- [ ] Public profile page: workload, state/priority distribution charts, assigned/created/subscribed tabs, activity log with download

## 16. Rich Text Editor (cross-cutting)

- [ ] Variants: document editor (pages), rich-text (issues), lite-text (comments), read-only
- [ ] Blocks: headings H1–H6, bullet/ordered/task lists, blockquote, code block w/ syntax highlighting, inline code, horizontal rule, tables, callouts (color/icon)
- [ ] Text alignment, custom colors, links, images (upload/align/delete), emoji picker
- [ ] Mentions, work-item embeds
- [ ] Slash commands menu; bubble/floating/block menus; AI menu
- [ ] Drag-and-drop block reordering (side menu handle)
- [ ] Collaborative editing (Yjs via live server); title sync

## 17. Command Palette (Power-K) & Navigation

- [ ] Global command palette from top nav search
- [ ] Creation commands (issue/cycle/module/page/project), navigation commands, account, preferences (theme), help
- [ ] Contextual menus: workspace/project/cycle/module/view/label/member/settings
- [ ] Global keyboard shortcuts
- [ ] Customizable app rail + tab navigation with overflow menu
- [ ] Favorites (sidebar), recent visits tracking
- [ ] Global search across workspaces/projects/issues/cycles/modules/pages/views/intake

## 18. Workspace Settings — Features & Developer

- [ ] **Exports** (`/settings/exports`): trigger export (JSON/CSV/XLSX), history, download
- [ ] **Imports**: Jira importer **[FORK — dedicated job with preview/execute/status; status-based ticket selection — pick which Jira statuses to migrate (e.g. skip Done/Closed), Select All, per-migration]**; GitHub/Jira service imports
- [ ] **Integrations** (`/settings/integrations`): GitHub repository sync (issues/comments), Slack project sync
- [ ] **Webhooks** (admin): CRUD, URL + secret, event triggers, regenerate secret, test, delivery logs

## 19. Instance Admin ("God Mode", port 3001)

- [ ] Instance admin sign-in; first-boot setup form; **unattended provisioning [FORK]**
- [ ] **General**: instance name, telemetry opt-in/out, instance admins list
- [ ] **Email/SMTP**: host, port, credentials, TLS/SSL, from-address, master switch, **send test email**
- [ ] **Workspaces**: list all workspaces (paginated), create workspace on behalf of users, disable workspace creation toggle
- [ ] **Authentication**: per-provider config (Google, GitHub, GitLab, Gitea), email/password toggle, magic-code toggle
- [ ] **AI**: OpenAI API key + model config
- [ ] **Images**: Unsplash access key

## 20. Public REST API (`/api/v1/`)

_(Token-authenticated, OpenAPI docs at Swagger/Redoc, rate-limited 60/min default)_

- [ ] Projects CRUD/archive/summary; work items CRUD/search/relations
- [ ] Labels, states, cycles (+transfer), modules, intake issues, estimates
- [ ] Issue links/comments/activities/attachments; stickies; members & invitations; assets (presigned upload)
- [ ] API activity logging; throttling behavior

## 21. Platform / Non-Functional

- [ ] File storage: S3 or MinIO, presigned URLs, 200 MB limit [FORK], asset cleanup
- [ ] Background jobs (Celery): invitation/notification emails, auto-archive/close, webhook delivery, import/export, activity generation, version sync
- [ ] i18n: 19 languages; themes; empty states and loaders for every module
- [ ] Rate limiting on auth + API endpoints; session/device management
- [ ] Hard-delete retention (60 days default)

---

## Known Gaps / Not Implemented in This Build

_(Useful baseline for the intern's improvement report)_

- **Epics, Initiatives, Customers** — types exist but features are stubbed (Plane EE-only upstream)
- **OIDC/SAML/LDAP SSO** — advertised on paid plans only; not in this build
- **Bulk operations** — UI present behind Pro upgrade banner
- **Workspace-level active cycles** — behind Pro upgrade banner
- **GitLab issue sync** — OAuth login supported, but no GitLab repo integration (GitHub only)
- Editor EE extensions — stub directory only
- No Intercom/in-app support chat

---

## Suggested Testing Approach for the Intern

1. **Setup**: run `pnpm dev` + the Docker backend; seed a workspace with 2+ users (admin + member + guest) to test role differences.
2. **Order**: test in the section order above — it follows dependency order (auth → workspace → project → work items → everything else).
3. **Per module**: verify CRUD, permissions per role, empty states, error handling, and cross-module effects (e.g., archiving a cycle affects its issues; deleting a project cascades).
4. **Cross-cutting checks**: filters/search consistency, notifications fire on mentions/assignments, webhooks deliver on events, exports contain expected data.
5. **Report format**: for each section — pass/fail per item, severity for failures, and gap/enhancement suggestions mapped against §"Known Gaps".
