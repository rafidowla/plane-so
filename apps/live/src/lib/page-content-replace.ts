/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-86 -- replace a page's body/title on its existing Yjs document, for the public API.

import type { Hocuspocus } from "@hocuspocus/server";
import * as Y from "yjs";
import { z } from "zod";
// plane imports
import {
  convertBase64StringToBinaryData,
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  replaceDocumentEditorContent,
} from "@plane/editor";

/** The request carries a whole Yjs document (base64), far beyond express.json()'s 100kb default. */
export const PAGE_CONTENT_BODY_LIMIT = "50mb";

export const replacePageContentSchema = z
  .object({
    page_id: z.string().uuid(),
    description_html: z.string().optional(),
    name: z.string().optional(),
    description_binary: z.string().optional(),
  })
  .refine((body) => body.description_html !== undefined || body.name !== undefined, {
    message: "Pass description_html and/or name",
    path: ["description_html"],
  });

export type TReplacePageContentBody = z.infer<typeof replacePageContentSchema>;

export type TReplacePageContentResult = {
  description_binary: string;
  description_html: string;
  description_json: object;
  /** true when the change was applied to a document open in this server (connected editors got it live) */
  loaded: boolean;
};

/** A request this endpoint cannot honour (HTTP 400). */
export class PageContentReplaceError extends Error {}

/**
 * Applies the new body/title as one Yjs transaction on the page's existing document and returns the
 * resulting document in every stored format. Nothing is written to the database: the caller persists it.
 *
 * - Document open in this Hocuspocus instance: the in-memory document is changed, so connected editors
 *   receive the update. The transaction has no origin, so Hocuspocus broadcasts it (and relays it over
 *   Redis) but does not schedule a store of its own.
 * - Otherwise: the change is applied on the stored document sent in `description_binary`.
 */
export const replacePageContent = async (
  instance: Hocuspocus,
  body: TReplacePageContentBody
): Promise<TReplacePageContentResult> => {
  const { page_id, description_html, name, description_binary } = body;

  // A document that is being loaded right now ends up in `documents`; wait for it rather than miss it.
  await instance.loadingDocuments.get(page_id)?.catch(() => undefined);
  const loadedDocument = instance.documents.get(page_id);

  let document: Y.Doc;
  if (loadedDocument) {
    document = loadedDocument;
  } else {
    const storedDocument = convertBase64StringToBinaryData(description_binary ?? "");
    if (storedDocument.byteLength === 0) {
      throw new PageContentReplaceError("description_binary is required when the page is not open in the editor");
    }
    document = new Y.Doc();
    try {
      Y.applyUpdate(document, storedDocument);
    } catch {
      throw new PageContentReplaceError("description_binary is not a valid document");
    }
  }

  replaceDocumentEditorContent(document, { html: description_html, title: name });

  const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromDocumentEditorBinaryData(
    Y.encodeStateAsUpdate(document),
    false
  );
  return {
    description_binary: contentBinaryEncoded,
    description_html: contentHTML,
    description_json: contentJSON,
    loaded: Boolean(loadedDocument),
  };
};
