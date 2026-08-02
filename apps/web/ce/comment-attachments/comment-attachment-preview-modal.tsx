/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: comment-attachments (#18)
import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { ChevronLeft, ChevronRight, Download, ExternalLink, File as FileIcon, X } from "lucide-react";
// plane imports
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { cn, convertBytesToSize, getFileURL } from "@plane/utils";
// local imports
import { getInlineAttachmentURL, getPreviewKind } from "@/plane-web/attachment-preview";
import type { TCommentAsset } from "./comment-attachments.service";

type Props = {
  assets: TCommentAsset[];
  activeId: string | null;
  onClose: () => void;
  onNavigate: (assetId: string) => void;
};

/**
 * In-app viewer for comment attachments (images / PDF / text / video / audio).
 * Local counterpart to the issue-attachment preview modal — comment assets
 * aren't in the issue attachment store, so this one is driven directly by the
 * comment's own asset list. Everything non-previewable gets a download state.
 */
export const CommentAttachmentPreviewModal = observer(function CommentAttachmentPreviewModal(props: Props) {
  const { assets, activeId, onClose, onNavigate } = props;
  const [zoom, setZoom] = useState(1);

  const index = activeId ? assets.findIndex((asset) => asset.id === activeId) : -1;
  const asset = index >= 0 ? assets[index] : undefined;

  // reset zoom when switching files
  useEffect(() => {
    setZoom(1);
  }, [activeId]);

  // arrow-key navigation while open
  useEffect(() => {
    if (!activeId || assets.length < 2) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const current = assets.findIndex((a) => a.id === activeId);
      if (current < 0) return;
      if (e.key === "ArrowRight") onNavigate(assets[(current + 1) % assets.length].id);
      if (e.key === "ArrowLeft") onNavigate(assets[(current - 1 + assets.length) % assets.length].id);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeId, assets, onNavigate]);

  if (!asset) return null;

  const fileName = asset.attributes?.name ?? "attachment";
  const kind = getPreviewKind(fileName);
  const inlineURL = getInlineAttachmentURL(asset.asset_url);
  const downloadURL = getFileURL(asset.asset_url ?? "");
  const canZoom = kind === "image";

  const iconButtonClass =
    "grid size-7 flex-shrink-0 place-items-center rounded text-secondary hover:bg-layer-2 hover:text-primary disabled:opacity-40 disabled:hover:bg-transparent";

  return (
    <ModalCore
      isOpen={!!activeId}
      handleClose={onClose}
      position={EModalPosition.CENTER}
      width={EModalWidth.VIXL}
      className="flex h-[85vh] flex-col overflow-hidden"
    >
      {/* header */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <p className="text-sm truncate font-medium">{fileName}</p>
          <span className="text-xs flex-shrink-0 text-placeholder">
            {convertBytesToSize(asset.attributes?.size ?? 0)}
          </span>
        </div>
        <div className="flex flex-shrink-0 items-center gap-1">
          {assets.length > 1 && (
            <>
              <button
                type="button"
                className={iconButtonClass}
                onClick={() => onNavigate(assets[(index - 1 + assets.length) % assets.length].id)}
                title="Previous"
              >
                <ChevronLeft className="size-4" />
              </button>
              <span className="text-xs px-1 text-tertiary">
                {index + 1} / {assets.length}
              </span>
              <button
                type="button"
                className={iconButtonClass}
                onClick={() => onNavigate(assets[(index + 1) % assets.length].id)}
                title="Next"
              >
                <ChevronRight className="size-4" />
              </button>
              <span className="mx-1 h-4 w-px bg-layer-2" />
            </>
          )}
          {canZoom && (
            <>
              <button
                type="button"
                className={iconButtonClass}
                onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
                title="Zoom out"
              >
                -
              </button>
              <button
                type="button"
                className="text-xs rounded px-1 text-tertiary hover:bg-layer-2"
                onClick={() => setZoom(1)}
                title="Reset zoom"
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                className={iconButtonClass}
                onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
                title="Zoom in"
              >
                +
              </button>
              <span className="mx-1 h-4 w-px bg-layer-2" />
            </>
          )}
          {inlineURL && (
            <button
              type="button"
              className={iconButtonClass}
              onClick={() => window.open(inlineURL, "_blank")}
              title="Open in new tab"
            >
              <ExternalLink className="size-4" />
            </button>
          )}
          {downloadURL && (
            <button
              type="button"
              className={iconButtonClass}
              onClick={() => window.open(downloadURL, "_blank")}
              title="Download"
            >
              <Download className="size-4" />
            </button>
          )}
          <button type="button" className={iconButtonClass} onClick={onClose} title="Close">
            <X className="size-4" />
          </button>
        </div>
      </div>

      {/* body */}
      <div className="flex-1 overflow-hidden bg-layer-1">
        {kind === "image" && inlineURL && (
          <div className="flex h-full w-full overflow-auto p-2">
            <img
              src={inlineURL}
              alt={fileName}
              className={cn("m-auto rounded-sm", zoom === 1 && "max-h-full max-w-full object-contain")}
              style={zoom !== 1 ? { width: `${zoom * 100}%`, maxWidth: "none" } : undefined}
            />
          </div>
        )}
        {(kind === "pdf" || kind === "text") && inlineURL && (
          // Script-capable types are forced to `disposition=attachment` server-side, so
          // only inert types (PDF/plain text) can ever render here — no sandbox needed.
          // oxlint-disable-next-line iframe-missing-sandbox
          <iframe src={inlineURL} title={fileName} className="h-full w-full border-0 bg-white" />
        )}
        {kind === "video" && inlineURL && (
          <div className="flex h-full w-full items-center justify-center bg-black p-2">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video src={inlineURL} controls preload="metadata" className="max-h-full max-w-full">
              Your browser can&apos;t play this video format.
            </video>
          </div>
        )}
        {kind === "audio" && inlineURL && (
          <div className="flex h-full w-full items-center justify-center p-6">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <audio src={inlineURL} controls preload="metadata" className="w-full max-w-lg" />
          </div>
        )}
        {(!kind || !inlineURL) && (
          <div className="flex h-full w-full flex-col items-center justify-center gap-3 text-tertiary">
            <FileIcon className="size-10" strokeWidth={1.5} />
            <p className="text-sm">Preview isn&apos;t available for this file type.</p>
            {downloadURL && (
              <button
                type="button"
                className="text-sm flex items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-secondary hover:bg-layer-2"
                onClick={() => window.open(downloadURL, "_blank")}
              >
                <Download className="size-3.5" />
                Download to view
              </button>
            )}
          </div>
        )}
      </div>
    </ModalCore>
  );
});
