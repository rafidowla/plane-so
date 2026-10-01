/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-86 -- replace a page's content on its EXISTING Yjs document.
//
// A page that has been opened in the editor has a Yjs history that browsers keep (IndexedDB cache, open
// sessions). Rebuilding the document from html creates an unrelated history, and when a browser holding
// the old one reconnects the two merge: the content is duplicated or the change is reverted. A change
// from outside the editor therefore has to be a transaction on the existing document, which every holder
// of the old state merges cleanly.

import { getSchema } from "@tiptap/core";
import { generateJSON } from "@tiptap/html";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { prosemirrorToYXmlFragment, yXmlFragmentToProseMirrorRootNode } from "y-prosemirror";
import type * as Y from "yjs";
// extensions
import {
  CoreEditorExtensionsWithoutProps,
  DocumentEditorExtensionsWithoutProps,
} from "@/extensions/core-without-props";
// helpers
import { generateTitleProsemirrorJson } from "@/helpers/yjs-utils";

// Same extension set and schema as the document editor helpers in yjs-utils.ts (not exported there).
const DOCUMENT_EDITOR_EXTENSIONS = [...CoreEditorExtensionsWithoutProps, ...DocumentEditorExtensionsWithoutProps];
const documentEditorSchema = getSchema(DOCUMENT_EDITOR_EXTENSIONS);

const EMPTY_DOCUMENT_HTML = "<p></p>";

export type TReplaceDocumentEditorContentArgs = {
  /** New body (XmlFragment "default"). Omit to leave the body alone. */
  html?: string;
  /** New page title (XmlFragment "title"). Omit to leave the title alone. */
  title?: string;
};

const replaceFragment = (fragment: Y.XmlFragment, target: ProseMirrorNode): void => {
  // y-prosemirror's own ProseMirror -> Yjs update (the one the editor runs on every change): it rewrites
  // only what differs, so identical content produces no operations and untouched blocks keep their
  // identity for anyone editing them at the same moment.
  prosemirrorToYXmlFragment(target, fragment);
  if (yXmlFragmentToProseMirrorRootNode(fragment, documentEditorSchema).eq(target)) return;
  // Not expected; if the diff ever leaves something else behind, replace every child.
  fragment.delete(0, fragment.length);
  prosemirrorToYXmlFragment(target, fragment);
};

/**
 * @description replaces the body and/or the title of a document editor Y.Doc in place, in one transaction
 * with a null origin (so a Hocuspocus document broadcasts it to its connections without treating it as a
 * client change it has to store).
 * @param {Y.Doc} doc - the existing document; mutated
 * @param {TReplaceDocumentEditorContentArgs} args
 */
export const replaceDocumentEditorContent = (doc: Y.Doc, args: TReplaceDocumentEditorContentArgs): void => {
  const { html, title } = args;
  // Convert before touching the document, so a conversion error leaves it unchanged.
  const body =
    html === undefined
      ? undefined
      : documentEditorSchema.nodeFromJSON(generateJSON(html || EMPTY_DOCUMENT_HTML, DOCUMENT_EDITOR_EXTENSIONS));
  const heading =
    title === undefined ? undefined : documentEditorSchema.nodeFromJSON(generateTitleProsemirrorJson(title));
  if (!body && !heading) return;

  doc.transact(() => {
    if (body) replaceFragment(doc.getXmlFragment("default"), body);
    if (heading) replaceFragment(doc.getXmlFragment("title"), heading);
  });
};
