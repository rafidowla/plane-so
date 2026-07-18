/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { getFileURL } from "@plane/utils";

/** File kinds the in-app viewer can render. Everything else gets a download state. */
export type TAttachmentPreviewKind = "image" | "pdf" | "text";

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif", "ico"];
const TEXT_EXTENSIONS = ["txt", "log", "md", "csv", "json"];

/** Accepts a full file name ("shot.png") or a bare extension ("png"). */
export const getPreviewKind = (fileNameOrExtension: string | undefined): TAttachmentPreviewKind | null => {
  if (!fileNameOrExtension) return null;
  const raw = fileNameOrExtension.includes(".") ? fileNameOrExtension.split(".").pop() : fileNameOrExtension;
  const ext = (raw ?? "").toLowerCase().trim();
  if (IMAGE_EXTENSIONS.includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (TEXT_EXTENSIONS.includes(ext)) return "text";
  return null;
};

/**
 * Attachment URL that renders inline instead of forcing a download. The
 * attachment endpoint redirects to a presigned URL whose Content-Disposition
 * is `attachment` by default; `?disposition=inline` (fork-added, see
 * apps/api/plane/app/views/issue/attachment.py) switches it so <img>/<iframe>
 * previews display rather than download.
 */
export const getInlineAttachmentURL = (assetUrl: string | undefined): string | undefined => {
  const url = getFileURL(assetUrl ?? "");
  if (!url) return undefined;
  return `${url}${url.includes("?") ? "&" : "?"}disposition=inline`;
};

// ---------------------------------------------------------------------------
// Tiny module-level pubsub so upstream list items can request a preview with a
// single function call, while the modal host (mounted once next to the list)
// owns all state. Keeps the upstream diff to single marked lines.
// ---------------------------------------------------------------------------

type TPreviewListener = (attachmentId: string) => void;

const listeners = new Set<TPreviewListener>();

export const requestAttachmentPreview = (attachmentId: string): void => {
  listeners.forEach((listener) => listener(attachmentId));
};

export const subscribeAttachmentPreview = (listener: TPreviewListener): (() => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
