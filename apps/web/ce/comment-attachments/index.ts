/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork-owned comment file attachments (#18), reached via the
 * `@/plane-web/comment-attachments` alias. Upstream seams (single marked lines,
 * `FORK: comment-attachments`):
 * - comments/comment-create.tsx: mounts <CommentAttachmentComposer /> under the
 *   editor (staged chips; #22 hides the inline paperclip) and folds staged ids
 *   into the existing bulk-link flow.
 * - comments/comment-create.tsx + editor/lite-text/{editor,toolbar}.tsx (#22):
 *   the paperclip button renders inside the comment toolbar via the
 *   `toolbarAccessory` slot (CommentAttachToolbarButton → composer imperative
 *   handle).
 * - comments/card/display.tsx: mounts <CommentAttachmentList /> under the
 *   comment body.
 * - api apps/api/plane/app/views/asset/v2.py: broad MIME list for
 *   COMMENT_DESCRIPTION uploads, `?disposition=inline` on the project asset
 *   GET, and the ProjectCommentAssetsEndpoint list endpoint.
 * - api apps/api/plane/utils/attachment_mime.py (#24): extension fallback so
 *   empty/generic browser MIME types still validate.
 * - shared accept rules (#24): `@/plane-web/attachment-accept` — one extension
 *   list used by both task and comment attachment pickers.
 */

export * from "./comment-attachments.service";
export * from "./comment-attachment-composer";
export * from "./comment-attachment-list";
export * from "./comment-attachment-preview-modal";
