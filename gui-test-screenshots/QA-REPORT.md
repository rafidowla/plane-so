# Visual QA & Regression Report — Improvements 20–24

**Date:** 2026-08-04 (round 2 same day) · **Branch:** `preview` (with 4 uncommitted QA fixes) · **Tester:** black-box GUI testing in a real browser at 1280×720, plus database verification after each action.

**Short version:** you were right — the last version had a real UX bug that made comment attachments unusable. It's fixed. One more gap was found and fixed during testing. Everything else passed.

---

## Bug 1 (P0, fixed): paperclip hidden behind the Comment button

- **What was wrong:** the attachment paperclip sat inside the toolbar's scrolling area. At a normal window size the sticky "Comment" button overlapped it — you simply couldn't see or click it. This is the "poor UX" you spotted.
- **The fix:** the paperclip is now pinned next to the Comment button, outside the scrolling area, so it's always visible. Toolbar groups were tightened so nothing gets clipped.
- **Files changed:** `apps/web/core/components/editor/lite-text/toolbar.tsx`

Before — paperclip covered by the Comment button:

![overlap](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/02c-paperclip-overlap.png)

After — paperclip always visible next to Comment:

![fixed](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/06b-crop.png)

## Bug 2 (P1, fixed): the "Attach" button accepted any file type

- **What was wrong:** the "Attach" quick-action button on the work-item page (a second way to add task attachments) had no file-type rules at all. The other pickers did. Unsupported files would fail later with no clear message.
- **The fix:** same file-type rules as everywhere else, plus a clear error toast listing what's allowed.
- **Files changed:** `apps/web/core/components/issues/issue-detail-widgets/attachments/quick-action-button.tsx`

---

## What passed

### #20 — Task Type picker in the Create dialog

- Picker renders next to the project picker, pre-filled with the default ("Feature").
- Changing it to "Bug", saving, and checking the database: value stored correctly.
- The new work item's sidebar shows "Task Type — Bug".
- Last-used type is remembered: reopening the dialog showed "Bug" pre-selected.
- Test item was deleted after; workspace left clean.

![picker](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/11-create-dialog-tasktype-rendered.png)

![dropdown](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/12-tasktype-dropdown-open.png)

![saved](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/28-demo47-tasktype.png)

### #21 — Jira comment threading & provenance

- Threaded Jira replies display nested with a connector line (DEMO-35).
- Timestamps show original dates ("about 1 month ago" matches the July import).
- **Caveat:** old imported comments show "admin" as author because they were imported before the provenance feature existed. The author field is empty in the database for those rows. New imports will show the original Jira author. If you want old comments to show real authors, that needs a one-time data fix — say the word.

![threads](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/17-comment-posted.png)

### #22 — Paperclip inside the comment toolbar

- Covered by Bug 1 above. Fixed and verified.

### #23 — Cancelled file picker / empty comments

- Empty comment cannot be submitted (Comment button stays disabled) — verified.
- The actual file-picker-cancel flow can't be simulated in the test browser (native dialogs unsupported); the code path was reviewed instead and blocks placeholder-only comments.

### #24 — Attachment file types

- Comment editor picker, task attachment dropzone, and the "Attach" button (after Bug 2 fix) all report the same 38 allowed types.
- Unsupported types get a clear toast naming the file and listing allowed types.

### Regression — comments & activity

- Post comment → saved, appears instantly. ![posted](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/17-comment-posted.png)
- Edit comment → saved, shows "(edited)" marker. ![edited](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/24-comment-edited.png)
- Delete comment → removed, soft-deleted in DB. Each step wrote the right activity-feed entries (created / updated / deleted).
- Type checks and lint pass on both fixed files.

---

## Minor notes — all 3 fixed in round 2 (2026-08-04)

### Fix 1: Comment delete now asks for confirmation

- Clicking Delete in the comment "…" menu opens a "Delete comment" dialog (Are you sure… / Cancel / Delete) instead of deleting instantly.
- Verified both paths: Cancel leaves the comment untouched; Delete removes it with a success toast.
- **Files changed:** `apps/web/core/components/comments/quick-actions.tsx`

![confirm](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/32-comment-delete-confirm.png)

![deleted](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/33-comment-deleted-via-confirm.png)

### Fix 2: Old Jira comments cleaned up (one-time data fix)

- Stripped the raw `[~accountid:…]` mention codes from 5 old comments (database update, both HTML and plain-text columns).
- The real author names were never captured by the old import and are not recoverable (no import job data left), so the 22 old Jira comments now show **"Jira user (via Jira)"** instead of "admin". Honest and clear.
- Verified on DEMO-11 and DEMO-35 (threading still intact).

![cleaned](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/31-jira-comment-cleaned.png)

![demo35](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/35-demo35-jira-comments-clean.png)

### Fix 3: Task Type chip now has a label

- The chip in the create dialog header reads "Task Type: Bug" instead of just "Bug".
- **Files changed:** `apps/web/ce/custom-properties/components/task-type-header-select.tsx`

![chip](file:///Users/rdowla/Downloads/AiDev/Marketplace/Plane.so/gui-test-screenshots/34-tasktype-chip-label.png)

Type checks and lint pass on all 4 changed files.

**Still open (cosmetic, out of scope):** some old Jira comments contain leftover Jira attachment markup like `!video.mp4|width=587!` or `[image: file.wmv]` as plain text — that's missing file content from the original import, not a display bug.

## Housekeeping done during testing

- DEMO-35's description was briefly overwritten by a stray test keystroke; it was restored from the activity history within minutes and verified. No lasting change.
- Test work item DEMO-47 ("QA task type test") was deleted.

## Uncommitted changes (need a branch + commit)

Four files are modified in the working tree — two from the first QA round, two from round 2:

- `apps/web/core/components/editor/lite-text/toolbar.tsx` — paperclip pinned next to Comment button (#22 fix)
- `apps/web/core/components/issues/issue-detail-widgets/attachments/quick-action-button.tsx` — file-type rules on the Attach button (#24 gap)
- `apps/web/core/components/comments/quick-actions.tsx` — comment delete confirmation (Fix 1)
- `apps/web/ce/custom-properties/components/task-type-header-select.tsx` — "Task Type:" label on the chip (Fix 3)

(Fix 2 was a database-only change — no code.)

Suggested: new branch `feat/25-qa-toolbar-attach-fixes` off `preview`, commit all four, merge with the usual "Merge feat/25-…: …(#25)" message.
