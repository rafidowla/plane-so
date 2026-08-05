/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork-owned comment thread replies (#26), reached via the
 * `@/plane-web/comment-replies` alias. Upstream seams (marked
 * `FORK: comment-replies`):
 * - comments/quick-actions.tsx: "Reply" menu item that opens the composer.
 * - comments/card/root.tsx: mounts <CommentReplyComposer /> under the comment
 *   body when replying; submits with `parent` set so the threading display
 *   from #21 (jira-comment-structure) nests the reply.
 * The API already accepts `parent` on comment create (IssueComment.parent,
 * serializer fields="__all__") — no backend change needed.
 */
export { CommentReplyComposer } from "./comment-reply-composer";
