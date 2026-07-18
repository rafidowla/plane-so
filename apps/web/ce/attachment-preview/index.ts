/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork-owned in-app attachment preview (images / PDF / text), reached via the
 * `@/plane-web/attachment-preview` alias. Upstream seams (each a single marked
 * line, `FORK: attachment-preview`):
 * - attachment-list-item.tsx: click → requestAttachmentPreview() for
 *   previewable kinds + AttachmentListThumbnail for image rows.
 * - attachment-item-list.tsx: mounts <AttachmentPreviewModalHost /> beside the list.
 * - api .../views/issue/attachment.py: `?disposition=inline` support.
 */

export * from "./helpers";
export * from "./preview-modal";
export * from "./thumbnail";
