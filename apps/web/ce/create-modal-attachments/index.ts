/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork-owned create-modal attachments (PSR-88) and description-image linking (PSR-104),
 * reached via the `@/plane-web/create-modal-attachments` alias. Upstream seams (single
 * marked lines, `FORK: create-modal-attachments`):
 * - issues/issue-modal/base.tsx: owns the queue (usePendingAttachmentQueue), uploads it
 *   right after the create call returns, and links editor images (getReferencedAssetIds)
 *   without letting a failure there undo or hide the created work item.
 * - issues/issue-modal/form.tsx: renders <PendingAttachmentsField /> under the editor.
 * - issues/issue-modal/draft-issue-layout.tsx: links editor images to the saved draft.
 * - api apps/api/plane/app/views/asset/v2.py ProjectBulkAssetEndpoint: unattached
 *   description images uploaded in another project may be re-homed (PSR-104).
 */

export * from "./pending-attachments";
export * from "./pending-attachments-field";
export * from "./use-pending-attachment-queue";
