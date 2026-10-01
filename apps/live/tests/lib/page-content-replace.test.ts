/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-86 -- the Yjs "replace content" helper (@plane/editor has no test runner, so it is covered
// here) and the live server's page-content/replace logic built on it.

import { describe, it, expect } from "vitest";
import { Document } from "@hocuspocus/server";
import type { Hocuspocus } from "@hocuspocus/server";
import * as Y from "yjs";
import {
  convertBinaryDataToBase64String,
  getAllDocumentFormatsFromDocumentEditorBinaryData,
  getBinaryDataFromDocumentEditorHTMLString,
  replaceDocumentEditorContent,
} from "@plane/editor";
import { PageContentReplaceError, replacePageContent, replacePageContentSchema } from "@/lib/page-content-replace";

const PAGE_ID = "3f0c8a52-6d0e-4a55-9d0a-3a5a4de5f6b1";
const HTML_1 =
  "<h2>Plan</h2><p>First <strong>draft</strong> of the plan.</p><ul><li><p>one</p></li><li><p>two</p></li></ul>";
const HTML_2 = "<h2>Plan v2</h2><p>Rewritten body.</p><ol><li><p>alpha</p></li></ol><p>Closing line.</p>";

const docFrom = (html: string, title?: string): Y.Doc => {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, getBinaryDataFromDocumentEditorHTMLString(html, title));
  return doc;
};

const read = (doc: Y.Doc) => {
  const { contentHTML, contentJSON, titleHTML } = getAllDocumentFormatsFromDocumentEditorBinaryData(
    Y.encodeStateAsUpdate(doc),
    true
  );
  return { html: contentHTML, json: contentJSON, title: titleHTML };
};

/** What the editor produces for this html on a brand new document. */
const normalised = (html: string) => read(docFrom(html)).html;

/** A browser that already holds the document: same Yjs history, its own client id. */
const clientOf = (doc: Y.Doc): Y.Doc => {
  const client = new Y.Doc();
  Y.applyUpdate(client, Y.encodeStateAsUpdate(doc));
  return client;
};

const hocuspocusWith = (documents: Record<string, Document> = {}) =>
  ({
    documents: new Map(Object.entries(documents)),
    loadingDocuments: new Map(),
  }) as unknown as Hocuspocus;

describe("replaceDocumentEditorContent", () => {
  it("replaces the body for a client that holds the old document, without duplicating it", () => {
    const server = docFrom(HTML_1, "My page");
    const client = clientOf(server);
    const before = Y.encodeStateVector(server);

    replaceDocumentEditorContent(server, { html: HTML_2 });
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server, before));

    expect(read(server).html).toBe(normalised(HTML_2));
    expect(read(client).html).toBe(normalised(HTML_2));
    expect(read(client).json).toEqual(read(docFrom(HTML_2)).json);
    expect(read(client).title).toBe("My page");
  });

  it("converges when the client syncs its whole (old) state back, as a reconnecting browser does", () => {
    const server = docFrom(HTML_1, "My page");
    const client = clientOf(server);

    replaceDocumentEditorContent(server, { html: HTML_2 });
    // two-way sync
    Y.applyUpdate(server, Y.encodeStateAsUpdate(client));
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server));

    expect(read(server).html).toBe(normalised(HTML_2));
    expect(read(client).html).toBe(normalised(HTML_2));
  });

  it("regenerating the document instead is what duplicates content (the bug this replaces)", () => {
    const client = clientOf(docFrom(HTML_1));
    Y.applyUpdate(client, getBinaryDataFromDocumentEditorHTMLString(HTML_2));

    expect(read(client).html).not.toBe(normalised(HTML_2));
    expect(read(client).html).toContain("First");
    expect(read(client).html).toContain("Rewritten body.");
  });

  it("updates the title only, or the title together with the body", () => {
    const server = docFrom(HTML_1, "My page");
    const client = clientOf(server);

    replaceDocumentEditorContent(server, { title: "Renamed" });
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server));
    expect(read(client).title).toBe("Renamed");
    expect(read(client).html).toBe(normalised(HTML_1));

    replaceDocumentEditorContent(server, { html: HTML_2, title: "Renamed again" });
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server));
    expect(read(client).title).toBe("Renamed again");
    expect(read(client).html).toBe(normalised(HTML_2));
  });

  it("is a no-op for identical content", () => {
    const server = docFrom(HTML_1, "My page");
    const before = Y.encodeStateAsUpdate(server);
    let updates = 0;
    server.on("update", () => updates++);

    replaceDocumentEditorContent(server, { html: HTML_1, title: "My page" });
    // the editor's own normalised html is the same content too
    replaceDocumentEditorContent(server, { html: normalised(HTML_1) });
    replaceDocumentEditorContent(server, {});

    expect(updates).toBe(0);
    expect(Y.encodeStateAsUpdate(server)).toEqual(before);
  });

  it("keeps what a client typed in a block the new body leaves alone", () => {
    const server = docFrom("<p>keep me</p><p>old tail</p>");
    const client = clientOf(server);
    const paragraph = client.getXmlFragment("default").get(0) as Y.XmlElement;
    (paragraph.get(0) as Y.XmlText).insert(7, " please");

    replaceDocumentEditorContent(server, { html: "<p>keep me</p><p>new tail</p>" });
    Y.applyUpdate(server, Y.encodeStateAsUpdate(client));
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server));

    expect(read(server).html).toBe(normalised("<p>keep me please</p><p>new tail</p>"));
    expect(read(client).html).toBe(read(server).html);
  });

  it("works on an empty document and empties a document", () => {
    const empty = new Y.Doc();
    replaceDocumentEditorContent(empty, { html: HTML_2, title: "Fresh" });
    expect(read(empty).html).toBe(normalised(HTML_2));
    expect(read(empty).title).toBe("Fresh");

    const server = docFrom(HTML_1, "My page");
    const client = clientOf(server);
    replaceDocumentEditorContent(server, { html: "" });
    Y.applyUpdate(client, Y.encodeStateAsUpdate(server));
    expect(read(client).html).toBe(normalised("<p></p>"));
  });

  it("applies everything in one transaction with no origin", () => {
    const server = docFrom(HTML_1, "My page");
    const origins: unknown[] = [];
    server.on("update", (_update: Uint8Array, origin: unknown) => origins.push(origin));

    replaceDocumentEditorContent(server, { html: HTML_2, title: "Renamed" });

    expect(origins).toEqual([null]);
  });
});

describe("replacePageContent", () => {
  it("applies the change on the stored document when the page is not open", async () => {
    const stored = getBinaryDataFromDocumentEditorHTMLString(HTML_1, "My page");
    const client = new Y.Doc();
    Y.applyUpdate(client, stored);

    const result = await replacePageContent(hocuspocusWith(), {
      page_id: PAGE_ID,
      description_html: HTML_2,
      name: "Renamed",
      description_binary: convertBinaryDataToBase64String(stored),
    });

    expect(result.loaded).toBe(false);
    expect(result.description_html).toBe(normalised(HTML_2));
    expect(result.description_json).toEqual(read(docFrom(HTML_2)).json);
    // the returned document extends the stored one: a browser holding the old state converges on it
    Y.applyUpdate(client, Buffer.from(result.description_binary, "base64"));
    expect(read(client).html).toBe(normalised(HTML_2));
    expect(read(client).title).toBe("Renamed");
  });

  it("applies the change on the open document, broadcasting it without a store of its own", async () => {
    const open = new Document(PAGE_ID);
    Y.applyUpdate(open, getBinaryDataFromDocumentEditorHTMLString(HTML_1, "My page"));
    const client = clientOf(open);
    const updates: { update: Uint8Array; origin: unknown }[] = [];
    open.onUpdate((_document, origin, update) => {
      updates.push({ update, origin });
    });

    const result = await replacePageContent(hocuspocusWith({ [PAGE_ID]: open }), {
      page_id: PAGE_ID,
      description_html: HTML_2,
      // ignored: the open document is the source of truth
      description_binary: convertBinaryDataToBase64String(getBinaryDataFromDocumentEditorHTMLString("<p>stale</p>")),
    });

    expect(result.loaded).toBe(true);
    expect(result.description_html).toBe(normalised(HTML_2));
    expect(read(open).html).toBe(normalised(HTML_2));
    expect(read(open).title).toBe("My page");
    // one update, with no connection as its origin: Hocuspocus sends it to the connections and does
    // not schedule onStoreDocument for it
    expect(updates).toHaveLength(1);
    expect(updates[0].origin).toBeNull();
    Y.applyUpdate(client, updates[0].update);
    expect(read(client).html).toBe(normalised(HTML_2));
    expect(Buffer.from(result.description_binary, "base64")).toEqual(Buffer.from(Y.encodeStateAsUpdate(open)));
  });

  it("waits for a document that is being loaded", async () => {
    const open = new Document(PAGE_ID);
    Y.applyUpdate(open, getBinaryDataFromDocumentEditorHTMLString(HTML_1, "My page"));
    const instance = hocuspocusWith();
    instance.loadingDocuments.set(
      PAGE_ID,
      new Promise((resolve) => {
        setTimeout(() => {
          instance.documents.set(PAGE_ID, open);
          resolve(open);
        }, 5);
      })
    );

    const result = await replacePageContent(instance, { page_id: PAGE_ID, name: "Renamed" });

    expect(result.loaded).toBe(true);
    expect(read(open).title).toBe("Renamed");
  });

  it("refuses a page that is not open when no stored document is sent", async () => {
    await expect(replacePageContent(hocuspocusWith(), { page_id: PAGE_ID, name: "Renamed" })).rejects.toThrow(
      PageContentReplaceError
    );
    await expect(
      replacePageContent(hocuspocusWith(), { page_id: PAGE_ID, name: "Renamed", description_binary: "" })
    ).rejects.toThrow(PageContentReplaceError);
  });
});

describe("replacePageContentSchema", () => {
  it("requires a page id and at least one of description_html / name", () => {
    expect(replacePageContentSchema.safeParse({ page_id: PAGE_ID, name: "x" }).success).toBe(true);
    expect(replacePageContentSchema.safeParse({ page_id: PAGE_ID, description_html: "" }).success).toBe(true);
    expect(replacePageContentSchema.safeParse({ page_id: PAGE_ID, description_binary: "AAAA" }).success).toBe(false);
    expect(replacePageContentSchema.safeParse({ page_id: "not-a-uuid", name: "x" }).success).toBe(false);
    expect(replacePageContentSchema.safeParse({ name: "x" }).success).toBe(false);
  });
});
