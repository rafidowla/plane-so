/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: comment-attachments (#18)
import React, { useImperativeHandle, useRef, useState } from "react";
import { observer } from "mobx-react";
import { File as FileIcon, Loader, Paperclip, X } from "lucide-react";
// plane imports
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Tooltip } from "@plane/propel/tooltip";
import { convertBytesToSize } from "@plane/utils";
// hooks
import { useFileSize } from "@/hooks/use-file-size";
// FORK: attachment file types (#24)
import {
  ATTACHMENT_ACCEPT_ATTRIBUTE,
  ATTACHMENT_ACCEPT_LABEL,
  isAcceptedAttachmentFile,
} from "@/plane-web/attachment-accept";

type TStagedAsset = {
  assetId: string;
  name: string;
  size: number;
  isUploading: boolean;
};

/** Imperative handle so the toolbar-mounted paperclip can open the picker. */
export type TCommentAttachmentComposerHandle = {
  open: () => void;
};

type Props = {
  /** Uploads one file as a COMMENT_DESCRIPTION asset; resolves with the new asset id. */
  uploadAsset: (file: File) => Promise<string>;
  /** Called whenever the staged set changes — the parent links these ids to the comment on submit. */
  onStagedAssetIdsChange: (assetIds: string[]) => void;
  disabled?: boolean;
  /**
   * FORK (#22): when the paperclip lives in the editor toolbar (via
   * CommentAttachToolbarButton + the imperative handle), hide the inline one
   * so only the staged chips render here.
   */
  hideAttachButton?: boolean;
};

/**
 * Paperclip attach button + staged file chips for the comment composer.
 * Files upload immediately (same flow as inline editor images); the staged
 * ids are reported up so the existing bulk-link call attaches them to the
 * comment when it's submitted.
 */
export const CommentAttachmentComposer = observer(
  React.forwardRef<TCommentAttachmentComposerHandle, Props>(function CommentAttachmentComposer(props, ref) {
    const { uploadAsset, onStagedAssetIdsChange, disabled = false, hideAttachButton = false } = props;
    const [stagedAssets, setStagedAssets] = useState<TStagedAsset[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const { maxFileSize } = useFileSize();

    useImperativeHandle(ref, () => ({ open: () => fileInputRef.current?.click() }), []);

    const updateStaged = (next: TStagedAsset[]) => {
      setStagedAssets(next);
      onStagedAssetIdsChange(next.filter((asset) => !asset.isUploading).map((asset) => asset.assetId));
    };

    const handleFiles = async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      for (const file of Array.from(files)) {
        // FORK: attachment file types (#24) — same accept rules as task attachments
        if (!isAcceptedAttachmentFile(file.name)) {
          setToast({
            type: TOAST_TYPE.ERROR,
            title: "Unsupported file type",
            message: `${file.name} can't be attached. Supported files: ${ATTACHMENT_ACCEPT_LABEL}`,
          });
          continue;
        }
        if (file.size > maxFileSize) {
          setToast({
            type: TOAST_TYPE.ERROR,
            title: "File too large",
            message: `${file.name} exceeds the ${convertBytesToSize(maxFileSize)} limit.`,
          });
          continue;
        }
        // placeholder chip while uploading (keyed by a temp id until the asset id arrives)
        const tempId = `uploading-${file.name}-${file.size}`;
        setStagedAssets((prev) => [...prev, { assetId: tempId, name: file.name, size: file.size, isUploading: true }]);
        try {
          // eslint-disable-next-line no-await-in-loop
          const assetId = await uploadAsset(file);
          setStagedAssets((prev) => {
            const next = prev.map((asset) =>
              asset.assetId === tempId ? { assetId, name: file.name, size: file.size, isUploading: false } : asset
            );
            onStagedAssetIdsChange(next.filter((asset) => !asset.isUploading).map((asset) => asset.assetId));
            return next;
          });
        } catch {
          setStagedAssets((prev) => prev.filter((asset) => asset.assetId !== tempId));
          setToast({
            type: TOAST_TYPE.ERROR,
            title: "Upload failed",
            message: `${file.name} couldn't be attached. Please try again.`,
          });
        }
      }
      // reset so picking the same file again re-triggers onChange
      if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const handleRemove = (assetId: string) => {
      updateStaged(stagedAssets.filter((asset) => asset.assetId !== assetId));
    };

    const fileInput = (
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ATTACHMENT_ACCEPT_ATTRIBUTE}
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
      />
    );

    // nothing to show until a file is staged (button lives in the toolbar)
    if (hideAttachButton && stagedAssets.length === 0) return fileInput;

    return (
      <div className="flex flex-wrap items-center gap-1.5 px-2 pb-2">
        {fileInput}
        {!hideAttachButton && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => fileInputRef.current?.click()}
            title="Attach files"
            className="grid size-7 place-items-center rounded text-secondary hover:bg-layer-2 hover:text-primary disabled:opacity-40"
          >
            <Paperclip className="size-4" />
          </button>
        )}
        {stagedAssets.map((asset) => (
          <span
            key={asset.assetId}
            className="text-xs flex max-w-60 items-center gap-1.5 rounded-md border border-subtle bg-surface-2 px-2 py-1 text-secondary"
          >
            {asset.isUploading ? (
              <Loader className="size-3.5 flex-shrink-0 animate-spin" />
            ) : (
              <FileIcon className="size-3.5 flex-shrink-0" />
            )}
            <span className="truncate">{asset.name}</span>
            <span className="flex-shrink-0 text-placeholder">{convertBytesToSize(asset.size)}</span>
            <button
              type="button"
              onClick={() => handleRemove(asset.assetId)}
              title={`Remove ${asset.name}`}
              className="flex-shrink-0 rounded text-placeholder hover:text-primary"
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
      </div>
    );
  })
);

/**
 * FORK (#22): paperclip styled like the other comment toolbar buttons, so the
 * attach action sits alongside the formatting options. Clicking it opens the
 * composer picker via the composer's imperative handle.
 */
export function CommentAttachToolbarButton(props: { onClick: () => void; disabled?: boolean }) {
  const { onClick, disabled = false } = props;
  return (
    <Tooltip tooltipContent="Attach files">
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className="grid aspect-square place-items-center rounded-xs p-0.5 text-placeholder hover:bg-layer-1 disabled:opacity-40"
      >
        <Paperclip className="h-3.5 w-3.5" strokeWidth={2.5} />
      </button>
    </Tooltip>
  );
}
