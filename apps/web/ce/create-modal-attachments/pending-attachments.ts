/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: create-modal-attachments (PSR-88, PSR-104)
// Pure (React-free, store-free) logic for the "Create new work item" modal:
// - a client-side queue of files picked before the work item exists, uploaded
//   once the create call has returned an id;
// - the list of editor-image asset ids that still need linking to the new work item.

import { ATTACHMENT_ACCEPT_LABEL, isAcceptedAttachmentFile } from "../attachment-accept";

export type TPendingAttachmentStatus = "queued" | "uploading" | "done" | "failed";

export type TPendingAttachment = {
  id: string;
  file: File;
  status: TPendingAttachmentStatus;
  error?: string;
};

export type TPendingFileRejection = {
  title: string;
  message: string;
};

const formatMegabytes = (bytes: number) => `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;

/** Stable identity for "is this the same file picked twice". */
export const getPendingFileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

/**
 * Same rules as the work item attachment dropzone (type allow-list + size limit),
 * plus duplicate detection against what is already queued.
 * Returns a rejection to show to the user, or `null` when the file can be queued.
 */
export const getPendingFileRejection = (
  file: File,
  options: { maxFileSize: number; existing: readonly TPendingAttachment[] }
): TPendingFileRejection | null => {
  const { maxFileSize, existing } = options;
  if (!isAcceptedAttachmentFile(file.name)) {
    return {
      title: "Unsupported file type",
      message: `${file.name} can't be attached. Supported files: ${ATTACHMENT_ACCEPT_LABEL}`,
    };
  }
  if (file.size > maxFileSize) {
    return {
      title: "File too large",
      message: `${file.name} exceeds the ${formatMegabytes(maxFileSize)} limit.`,
    };
  }
  const key = getPendingFileKey(file);
  if (existing.some((item) => getPendingFileKey(item.file) === key)) {
    return {
      title: "Already added",
      message: `${file.name} is already in the list.`,
    };
  }
  return null;
};

export type TPendingUploadSummary = {
  succeeded: TPendingAttachment[];
  failed: TPendingAttachment[];
};

/**
 * Uploads queued files one at a time (so a single progress source maps to a single file)
 * and NEVER throws: a failing upload is recorded on its item and the remaining files still
 * upload. The caller has already created the work item, which must survive any failure here.
 */
export const uploadPendingAttachments = async (
  items: readonly TPendingAttachment[],
  upload: (file: File) => Promise<unknown>,
  onStatusChange: (id: string, status: TPendingAttachmentStatus, error?: string) => void
): Promise<TPendingUploadSummary> => {
  const succeeded: TPendingAttachment[] = [];
  const failed: TPendingAttachment[] = [];
  // only (re)try what has not been uploaded yet
  for (const item of items.filter((candidate) => candidate.status !== "done")) {
    onStatusChange(item.id, "uploading");
    try {
      // sequential on purpose — see the function comment
      // eslint-disable-next-line no-await-in-loop
      await upload(item.file);
      onStatusChange(item.id, "done");
      succeeded.push({ ...item, status: "done" });
    } catch (error) {
      const message = getUploadErrorMessage(error);
      onStatusChange(item.id, "failed", message);
      failed.push({ ...item, status: "failed", error: message });
    }
  }
  return { succeeded, failed };
};

export const getUploadErrorMessage = (error: unknown): string => {
  if (typeof error === "string" && error) return error;
  if (error && typeof error === "object") {
    const candidate =
      (error as { error?: unknown; message?: unknown }).error ?? (error as { message?: unknown }).message;
    if (typeof candidate === "string" && candidate) return candidate;
  }
  return "Upload failed";
};

/** Toast copy for files that could not be attached after the work item was created. */
export const getFailedUploadsMessage = (failed: readonly TPendingAttachment[]): string => {
  const names = failed.map((item) => item.file.name);
  const shown = names.slice(0, 3).join(", ");
  const rest = names.length > 3 ? ` and ${names.length - 3} more` : "";
  return `${shown}${rest} could not be attached. The work item was created — open it to add ${
    names.length === 1 ? "the file" : "the files"
  } again.`;
};

/**
 * Editor images are uploaded as soon as they are added, before the work item exists, and
 * linked to it afterwards. Only link ids that the final description still references:
 * an image the user removed again has already been deleted server-side, and sending its id
 * made the whole link call fail with "The requested asset could not be found".
 */
export const getReferencedAssetIds = (assetIds: readonly string[], descriptionHtml: string | null | undefined) => {
  if (!descriptionHtml) return [];
  return Array.from(new Set(assetIds)).filter((assetId) => assetId && descriptionHtml.includes(assetId));
};
