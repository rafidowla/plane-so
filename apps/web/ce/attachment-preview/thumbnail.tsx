/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
// plane imports
import type { TIssueAttachment } from "@plane/types";
// local imports
import { getInlineAttachmentURL, getPreviewKind } from "./helpers";

type Props = {
  attachment: TIssueAttachment;
  /** Rendered when the file isn't an image (or the image fails to load). */
  fallback: React.ReactNode;
};

/** Small real-image thumbnail for image attachments in the list; falls back to
 * the stock file-type icon for everything else. */
export function AttachmentListThumbnail({ attachment, fallback }: Props) {
  const [errored, setErrored] = useState(false);
  const kind = getPreviewKind(attachment?.attributes?.name);
  const src = getInlineAttachmentURL(attachment?.asset_url);

  if (kind !== "image" || !src || errored) return <div className="flex items-center gap-3">{fallback}</div>;

  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      className="size-7 flex-shrink-0 rounded border border-subtle object-cover"
      onError={() => setErrored(true)}
    />
  );
}
