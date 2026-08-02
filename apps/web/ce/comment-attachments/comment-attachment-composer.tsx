/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: comment-attachments (#18)
import { useRef, useState } from "react";
import { observer } from "mobx-react";
import { File as FileIcon, Loader, Paperclip, X } from "lucide-react";
// plane imports
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { convertBytesToSize } from "@plane/utils";
// hooks
import { useFileSize } from "@/hooks/use-file-size";

type TStagedAsset = {
  assetId: string;
  name: string;
  size: number;
  isUploading: boolean;
};

type Props = {
  /** Uploads one file as a COMMENT_DESCRIPTION asset; resolves with the new asset id. */
  uploadAsset: (file: File) => Promise<string>;
  /** Called whenever the staged set changes — the parent links these ids to the comment on submit. */
  onStagedAssetIdsChange: (assetIds: string[]) => void;
  disabled?: boolean;
};

/**
 * Paperclip attach button + staged file chips for the comment composer.
 * Files upload immediately (same flow as inline editor images); the staged
 * ids are reported up so the existing bulk-link call attaches them to the
 * comment when it's submitted.
 */
export const CommentAttachmentComposer = observer(function CommentAttachmentComposer(props: Props) {
  const { uploadAsset, onStagedAssetIdsChange, disabled = false } = props;
  const [stagedAssets, setStagedAssets] = useState<TStagedAsset[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { maxFileSize } = useFileSize();

  const updateStaged = (next: TStagedAsset[]) => {
    setStagedAssets(next);
    onStagedAssetIdsChange(next.filter((asset) => !asset.isUploading).map((asset) => asset.assetId));
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
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

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-2 pb-2">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => fileInputRef.current?.click()}
        title="Attach files"
        className="grid size-7 place-items-center rounded text-secondary hover:bg-layer-2 hover:text-primary disabled:opacity-40"
      >
        <Paperclip className="size-4" />
      </button>
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
});
