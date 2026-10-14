/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: create-modal-attachments (PSR-88)
import { useCallback, useMemo, useRef, useState } from "react";
import { setToast } from "@plane/blocks/toast";
import { useFileSize } from "@/hooks/use-file-size";
import type { TPendingAttachment, TPendingAttachmentStatus, TPendingUploadSummary } from "./pending-attachments";
import { getPendingFileRejection, uploadPendingAttachments } from "./pending-attachments";

export type TPendingAttachmentQueue = {
  items: TPendingAttachment[];
  /** id of the work item the queue is currently uploading to (drives per-file progress) */
  targetIssueId: string | undefined;
  addFiles: (files: File[]) => void;
  remove: (id: string) => void;
  /** drop everything (modal reset / "create more") */
  clear: () => void;
  /** drop only the files that were uploaded, keep failed ones visible */
  clearUploaded: () => void;
  /** upload everything not yet uploaded to the given work item. Never throws. */
  upload: (issueId: string, uploadFile: (file: File) => Promise<unknown>) => Promise<TPendingUploadSummary>;
};

let pendingIdCounter = 0;

/**
 * State for files picked in the create modal before the work item exists. The queue lives
 * in the modal root so it survives the form re-rendering, and is uploaded by the create
 * handler as soon as the create call returns an id.
 */
export const usePendingAttachmentQueue = (): TPendingAttachmentQueue => {
  const [items, setItems] = useState<TPendingAttachment[]>([]);
  const [targetIssueId, setTargetIssueId] = useState<string | undefined>(undefined);
  // the create handler reads the queue at submit time; keep a ref so it never sees a stale render
  const itemsRef = useRef<TPendingAttachment[]>([]);
  const { maxFileSize } = useFileSize();

  const commit = useCallback((updater: (prev: TPendingAttachment[]) => TPendingAttachment[]) => {
    itemsRef.current = updater(itemsRef.current);
    setItems(itemsRef.current);
  }, []);

  const addFiles = useCallback(
    (files: File[]) => {
      const accepted: TPendingAttachment[] = [];
      for (const file of files) {
        const rejection = getPendingFileRejection(file, {
          maxFileSize,
          existing: [...itemsRef.current, ...accepted],
        });
        if (rejection) {
          setToast({ type: "error", title: rejection.title, message: rejection.message });
          continue;
        }
        pendingIdCounter += 1;
        accepted.push({ id: `pending-attachment-${pendingIdCounter}`, file, status: "queued" });
      }
      if (accepted.length > 0) commit((prev) => [...prev, ...accepted]);
    },
    [commit, maxFileSize]
  );

  const remove = useCallback((id: string) => commit((prev) => prev.filter((item) => item.id !== id)), [commit]);

  const clear = useCallback(() => {
    commit(() => []);
    setTargetIssueId(undefined);
  }, [commit]);

  const clearUploaded = useCallback(() => commit((prev) => prev.filter((item) => item.status !== "done")), [commit]);

  const upload = useCallback<TPendingAttachmentQueue["upload"]>(
    async (issueId, uploadFile) => {
      setTargetIssueId(issueId);
      const setStatus = (id: string, status: TPendingAttachmentStatus, error?: string) =>
        commit((prev) => prev.map((item) => (item.id === id ? { ...item, status, error } : item)));
      return uploadPendingAttachments(itemsRef.current, uploadFile, setStatus);
    },
    [commit]
  );

  return useMemo(
    () => ({ items, targetIssueId, addFiles, remove, clear, clearUploaded, upload }),
    [items, targetIssueId, addFiles, remove, clear, clearUploaded, upload]
  );
};
