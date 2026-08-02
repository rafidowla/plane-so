/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: comment-attachments (#18)
import { useState } from "react";
import useSWR from "swr";
import { observer } from "mobx-react";
import { Download, File as FileIcon, FileAudio, FileImage, FileText, FileVideo } from "lucide-react";
// plane imports
import { convertBytesToSize, getFileURL } from "@plane/utils";
// local imports
import { getPreviewKind } from "@/plane-web/attachment-preview";
import type { TCommentAsset } from "./comment-attachments.service";
import { commentAttachmentsService } from "./comment-attachments.service";
import { CommentAttachmentPreviewModal } from "./comment-attachment-preview-modal";

const COMMENT_ASSETS_KEY = (commentId: string) => `COMMENT_ASSETS_${commentId}`;

const kindIcon = (fileName: string | undefined) => {
  switch (getPreviewKind(fileName)) {
    case "image":
      return FileImage;
    case "pdf":
    case "text":
      return FileText;
    case "video":
      return FileVideo;
    case "audio":
      return FileAudio;
    default:
      return FileIcon;
  }
};

type Props = {
  workspaceSlug: string;
  projectId: string;
  commentId: string;
};

/**
 * Attachment row under a saved comment: one chip per file. Previewable kinds
 * open the in-app viewer; everything else downloads with its original name.
 */
export const CommentAttachmentList = observer(function CommentAttachmentList(props: Props) {
  const { workspaceSlug, projectId, commentId } = props;
  const [activeAssetId, setActiveAssetId] = useState<string | null>(null);

  const { data: assets } = useSWR(
    workspaceSlug && projectId && commentId ? COMMENT_ASSETS_KEY(commentId) : null,
    workspaceSlug && projectId && commentId
      ? () => commentAttachmentsService.getCommentAssets(workspaceSlug, projectId, commentId)
      : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  if (!assets || assets.length === 0) return null;

  const handleClick = (asset: TCommentAsset) => {
    if (getPreviewKind(asset.attributes?.name)) setActiveAssetId(asset.id);
    else {
      const url = getFileURL(asset.asset_url ?? "");
      if (url) window.open(url, "_blank");
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        {assets.map((asset) => {
          const name = asset.attributes?.name ?? "attachment";
          const Icon = kindIcon(name);
          const previewable = !!getPreviewKind(name);
          return (
            <button
              key={asset.id}
              type="button"
              onClick={() => handleClick(asset)}
              title={previewable ? `Preview ${name}` : `Download ${name}`}
              className="text-xs flex max-w-60 items-center gap-1.5 rounded-md border border-subtle bg-surface-2 px-2 py-1 text-secondary hover:bg-layer-2 hover:text-primary"
            >
              <Icon className="size-3.5 flex-shrink-0" />
              <span className="truncate">{name}</span>
              <span className="flex-shrink-0 text-placeholder">{convertBytesToSize(asset.attributes?.size ?? 0)}</span>
              {!previewable && <Download className="size-3 flex-shrink-0 text-placeholder" />}
            </button>
          );
        })}
      </div>
      <CommentAttachmentPreviewModal
        assets={assets}
        activeId={activeAssetId}
        onClose={() => setActiveAssetId(null)}
        onNavigate={(assetId) => setActiveAssetId(assetId)}
      />
    </>
  );
});
