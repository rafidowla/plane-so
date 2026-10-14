/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: create-modal-attachments (PSR-88)
import { useRef } from "react";
import { observer } from "mobx-react";
import { AlertCircle, Check, File as FileIcon, Loader, Paperclip, X } from "lucide-react";
// plane imports
import { convertBytesToSize } from "@plane/utils";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// shared accept rules (#24)
import { ATTACHMENT_ACCEPT_ATTRIBUTE } from "../attachment-accept";
import type { TPendingAttachment } from "./pending-attachments";
import type { TPendingAttachmentQueue } from "./use-pending-attachment-queue";

type Props = {
  queue: TPendingAttachmentQueue;
  /** true while the create call / uploads are running — files can no longer be changed */
  disabled?: boolean;
};

const PendingAttachmentChip = observer(function PendingAttachmentChip(props: {
  item: TPendingAttachment;
  progress: number | undefined;
  disabled: boolean;
  onRemove: (id: string) => void;
}) {
  const { item, progress, disabled, onRemove } = props;
  const isBusy = item.status === "uploading";
  const canRemove = !disabled && !isBusy && item.status !== "done";
  return (
    <li
      data-testid="pending-attachment"
      data-status={item.status}
      title={item.status === "failed" ? item.error : item.file.name}
      className="text-xs flex max-w-72 items-center gap-1.5 rounded-md border border-subtle bg-surface-2 px-2 py-1 text-secondary"
    >
      {item.status === "uploading" ? (
        <Loader className="size-3.5 flex-shrink-0 animate-spin" />
      ) : item.status === "done" ? (
        <Check className="size-3.5 flex-shrink-0 text-success-primary" />
      ) : item.status === "failed" ? (
        <AlertCircle className="size-3.5 flex-shrink-0 text-danger-primary" />
      ) : (
        <FileIcon className="size-3.5 flex-shrink-0" />
      )}
      <span className="truncate">{item.file.name}</span>
      <span className="flex-shrink-0 text-placeholder">
        {item.status === "uploading" && progress !== undefined
          ? `${progress}%`
          : item.status === "failed"
            ? "Failed"
            : convertBytesToSize(item.file.size)}
      </span>
      {canRemove && (
        <button
          type="button"
          onClick={() => onRemove(item.id)}
          title={`Remove ${item.file.name}`}
          aria-label={`Remove ${item.file.name}`}
          className="flex-shrink-0 rounded text-placeholder hover:text-primary"
        >
          <X className="size-3" />
        </button>
      )}
    </li>
  );
});

/**
 * "Attach files" control for the create work item modal. Files are only queued here —
 * the modal uploads them after the work item has been created (see base.tsx).
 */
export const PendingAttachmentsField = observer(function PendingAttachmentsField(props: Props) {
  const { queue, disabled = false } = props;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    attachment: { getAttachmentsUploadStatusByIssueId },
  } = useIssueDetail();
  // uploads run one at a time, so the single active status entry belongs to the uploading chip
  const activeProgress = queue.targetIssueId
    ? getAttachmentsUploadStatusByIssueId(queue.targetIssueId)?.[0]?.progress
    : undefined;

  return (
    <div className="flex flex-col gap-2 pt-2" data-testid="create-modal-attachments">
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-1.5 py-1 text-caption-sm-regular text-secondary hover:bg-layer-1 hover:text-primary disabled:opacity-40"
        >
          <Paperclip className="size-3.5" />
          Attach files
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ATTACHMENT_ACCEPT_ATTRIBUTE}
          className="hidden"
          data-testid="create-modal-attachments-input"
          onChange={(event) => {
            queue.addFiles(Array.from(event.target.files ?? []));
            // allow picking the same file again after removing it
            event.target.value = "";
          }}
        />
        {queue.items.length > 0 && (
          <span className="text-caption-sm-regular text-tertiary">Uploaded when the work item is created</span>
        )}
      </div>
      {queue.items.length > 0 && (
        <ul className="flex flex-wrap items-center gap-1.5">
          {queue.items.map((item) => (
            <PendingAttachmentChip
              key={item.id}
              item={item}
              progress={item.status === "uploading" ? activeProgress : undefined}
              disabled={disabled}
              onRemove={queue.remove}
            />
          ))}
        </ul>
      )}
    </div>
  );
});
