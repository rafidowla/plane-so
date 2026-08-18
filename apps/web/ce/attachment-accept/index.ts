/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Fork-owned shared attachment accept rules (#24), reached via the
 * `@/plane-web/attachment-accept` alias. One source of truth so task
 * attachments and comment attachments accept exactly the same files and
 * reject the rest with the same message. Keep in sync with the backend
 * allowlist (apps/api/plane/settings/common.py ATTACHMENT_MIME_TYPES +
 * plane/utils/attachment_mime.py EXTENSION_MIME_OVERRIDES).
 */

/** Extensions accepted in both task and comment attachments. */
export const ATTACHMENT_ACCEPT_EXTENSIONS = [
  // documents
  "pdf",
  "doc",
  "docx",
  "txt",
  "rtf",
  "csv",
  "xls",
  "xlsx",
  "ppt",
  "pptx",
  // images
  "png",
  "jpg",
  "jpeg",
  "gif",
  "svg",
  "webp",
  // video
  "mp4",
  "mov",
  "mkv",
  "webm",
  "wmv",
  // audio
  "mp3",
  "wav",
  "ogg",
  "m4a",
  "aac",
  "flac",
  // data & code
  "json",
  "xml",
  "yaml",
  "yml",
  "sql",
  "log",
  "md",
  // archives
  "zip",
  "rar",
  "7z",
  "tar",
  "gz",
] as const;

/** Value for an <input type="file" accept="..."> attribute. */
export const ATTACHMENT_ACCEPT_ATTRIBUTE = ATTACHMENT_ACCEPT_EXTENSIONS.map((ext) => `.${ext}`).join(",");

/** Human-readable list for validation messages. */
export const ATTACHMENT_ACCEPT_LABEL = ATTACHMENT_ACCEPT_EXTENSIONS.map((ext) => `.${ext}`).join(" ");

export const getFileExtension = (fileName: string): string => {
  const parts = fileName.split(".");
  return parts.length > 1 ? (parts.pop() ?? "").toLowerCase() : "";
};

/** True when the file's extension is in the shared accept list. */
export const isAcceptedAttachmentFile = (fileName: string): boolean => {
  const ext = getFileExtension(fileName);
  return !!ext && (ATTACHMENT_ACCEPT_EXTENSIONS as readonly string[]).includes(ext);
};
