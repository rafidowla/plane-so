/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { ChevronLeft, ChevronRight, Download, ExternalLink, File as FileIcon, X, ZoomIn, ZoomOut } from "lucide-react";
// plane imports
import type { TIssueServiceType } from "@plane/types";
import { EIssueServiceType } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { cn, convertBytesToSize, getFileURL } from "@plane/utils";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import { getInlineAttachmentURL, getPreviewKind, subscribeAttachmentPreview } from "./helpers";

const ZOOM_STEPS = [0.5, 0.75, 1, 1.5, 2, 3];

type Props = {
  issueId: string;
  issueServiceType?: TIssueServiceType;
};

/**
 * In-app attachment viewer (images, PDFs, plain text) with zoom, previous/next
 * navigation across the work item's attachments, open-in-new-tab and download.
 * Mounted once beside the attachment list; opens via requestAttachmentPreview()
 * (see helpers.ts) so the upstream list item only needs a one-line call.
 */
export const AttachmentPreviewModalHost = observer(function AttachmentPreviewModalHost(props: Props) {
  const { issueId, issueServiceType = EIssueServiceType.ISSUES } = props;
  // state
  const [activeId, setActiveId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  // store hooks
  const {
    attachment: { getAttachmentsByIssueId, getAttachmentById },
  } = useIssueDetail(issueServiceType);
  // derived values
  const attachmentIds = getAttachmentsByIssueId(issueId) ?? [];
  const attachment = activeId ? getAttachmentById(activeId) : undefined;
  const index = activeId ? attachmentIds.indexOf(activeId) : -1;

  // open requests from list items — only claim ids that belong to this issue's list
  useEffect(
    () =>
      subscribeAttachmentPreview((attachmentId) => {
        const ids = getAttachmentsByIssueId(issueId) ?? [];
        if (ids.includes(attachmentId)) setActiveId(attachmentId);
      }),
    [getAttachmentsByIssueId, issueId]
  );

  // reset zoom when switching files
  useEffect(() => {
    setZoom(1);
  }, [activeId]);

  const handleNavigate = (direction: 1 | -1) => {
    if (index < 0 || attachmentIds.length === 0) return;
    const nextIndex = (index + direction + attachmentIds.length) % attachmentIds.length;
    setActiveId(attachmentIds[nextIndex]);
  };

  // arrow-key navigation while open
  useEffect(() => {
    if (!activeId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") handleNavigate(1);
      if (e.key === "ArrowLeft") handleNavigate(-1);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, index, attachmentIds.length]);

  if (!attachment) return null;

  const fileName = attachment.attributes?.name ?? "attachment";
  const kind = getPreviewKind(fileName);
  const inlineURL = getInlineAttachmentURL(attachment.asset_url);
  const downloadURL = getFileURL(attachment.asset_url ?? "");
  const canZoom = kind === "image";
  const zoomIndex = ZOOM_STEPS.indexOf(zoom);

  const iconButtonClass =
    "grid size-7 flex-shrink-0 place-items-center rounded text-secondary hover:bg-layer-2 hover:text-primary disabled:opacity-40 disabled:hover:bg-transparent";

  return (
    <ModalCore
      isOpen={!!activeId}
      handleClose={() => setActiveId(null)}
      position={EModalPosition.CENTER}
      width={EModalWidth.VIXL}
      className="flex h-[85vh] flex-col overflow-hidden"
    >
      {/* header */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-sm font-medium">{fileName}</p>
          <span className="flex-shrink-0 text-xs text-placeholder">
            {convertBytesToSize(attachment.attributes?.size ?? 0)}
          </span>
        </div>
        <div className="flex flex-shrink-0 items-center gap-1">
          {attachmentIds.length > 1 && (
            <>
              <button type="button" className={iconButtonClass} onClick={() => handleNavigate(-1)} title="Previous">
                <ChevronLeft className="size-4" />
              </button>
              <span className="px-1 text-xs text-tertiary">
                {index + 1} / {attachmentIds.length}
              </span>
              <button type="button" className={iconButtonClass} onClick={() => handleNavigate(1)} title="Next">
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
                onClick={() => setZoom(ZOOM_STEPS[Math.max(0, (zoomIndex === -1 ? 2 : zoomIndex) - 1)])}
                disabled={zoomIndex <= 0}
                title="Zoom out"
              >
                <ZoomOut className="size-4" />
              </button>
              <button
                type="button"
                className="rounded px-1 text-xs text-tertiary hover:bg-layer-2"
                onClick={() => setZoom(1)}
                title="Reset zoom"
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                className={iconButtonClass}
                onClick={() =>
                  setZoom(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, (zoomIndex === -1 ? 2 : zoomIndex) + 1)])
                }
                disabled={zoomIndex === ZOOM_STEPS.length - 1}
                title="Zoom in"
              >
                <ZoomIn className="size-4" />
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
          <button type="button" className={iconButtonClass} onClick={() => setActiveId(null)} title="Close">
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
          <iframe src={inlineURL} title={fileName} className="h-full w-full border-0 bg-white" />
        )}
        {kind === "video" && inlineURL && (
          <div className="flex h-full w-full items-center justify-center bg-black p-2">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video src={inlineURL} controls autoPlay={false} preload="metadata" className="max-h-full max-w-full">
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
                className="flex items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-sm text-secondary hover:bg-layer-2"
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
