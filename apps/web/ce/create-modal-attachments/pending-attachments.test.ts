/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: create-modal-attachments (PSR-88, PSR-104)
import { describe, expect, it, vi } from "vitest";
import type { TPendingAttachment } from "./pending-attachments";
import {
  getFailedUploadsMessage,
  getPendingFileRejection,
  getReferencedAssetIds,
  getUploadErrorMessage,
  uploadPendingAttachments,
} from "./pending-attachments";

const makeFile = (name: string, size = 10, lastModified = 1) => {
  const file = new File([new Uint8Array(size)], name, { lastModified });
  return file;
};
const makeItem = (name: string, overrides: Partial<TPendingAttachment> = {}): TPendingAttachment => ({
  id: `id-${name}`,
  file: makeFile(name),
  status: "queued",
  ...overrides,
});

describe("getPendingFileRejection", () => {
  const options = { maxFileSize: 1024, existing: [] as TPendingAttachment[] };

  it("accepts a supported, small, new file", () => {
    expect(getPendingFileRejection(makeFile("report.pdf"), options)).toBeNull();
  });

  it("rejects unsupported extensions with the supported list", () => {
    const rejection = getPendingFileRejection(makeFile("run.exe"), options);
    expect(rejection?.title).toBe("Unsupported file type");
    expect(rejection?.message).toContain("run.exe");
    expect(rejection?.message).toContain(".pdf");
  });

  it("rejects files without an extension", () => {
    expect(getPendingFileRejection(makeFile("README"), options)?.title).toBe("Unsupported file type");
  });

  it("rejects files over the size limit", () => {
    const rejection = getPendingFileRejection(makeFile("big.zip", 2048), options);
    expect(rejection?.title).toBe("File too large");
  });

  it("rejects the same file picked twice, but not a same-named file with different content", () => {
    const first = makeItem("a.pdf");
    expect(getPendingFileRejection(makeFile("a.pdf"), { ...options, existing: [first] })?.title).toBe("Already added");
    expect(getPendingFileRejection(makeFile("a.pdf", 99), { ...options, existing: [first] })).toBeNull();
  });
});

describe("uploadPendingAttachments", () => {
  it("uploads sequentially and reports status transitions per file", async () => {
    const items = [makeItem("a.pdf"), makeItem("b.pdf")];
    const order: string[] = [];
    const events: string[] = [];
    const summary = await uploadPendingAttachments(
      items,
      async (file) => {
        order.push(`start:${file.name}`);
        await Promise.resolve();
        order.push(`end:${file.name}`);
      },
      (id, status) => events.push(`${id}:${status}`)
    );
    expect(order).toEqual(["start:a.pdf", "end:a.pdf", "start:b.pdf", "end:b.pdf"]);
    expect(events).toEqual(["id-a.pdf:uploading", "id-a.pdf:done", "id-b.pdf:uploading", "id-b.pdf:done"]);
    expect(summary.succeeded).toHaveLength(2);
    expect(summary.failed).toHaveLength(0);
  });

  it("never throws: a failing file is recorded and the remaining files still upload", async () => {
    const items = [makeItem("a.pdf"), makeItem("bad.pdf"), makeItem("c.pdf")];
    const upload = vi.fn(async (file: File) => {
      if (file.name === "bad.pdf") throw { error: "Unsupported file type" };
    });
    const onStatusChange = vi.fn();
    const summary = await uploadPendingAttachments(items, upload, onStatusChange);
    expect(upload).toHaveBeenCalledTimes(3);
    expect(summary.succeeded.map((item) => item.file.name)).toEqual(["a.pdf", "c.pdf"]);
    expect(summary.failed.map((item) => item.file.name)).toEqual(["bad.pdf"]);
    expect(summary.failed[0].error).toBe("Unsupported file type");
    expect(onStatusChange).toHaveBeenCalledWith("id-bad.pdf", "failed", "Unsupported file type");
  });

  it("skips files that were already uploaded (retry after a partial failure)", async () => {
    const items = [makeItem("a.pdf", { status: "done" }), makeItem("b.pdf", { status: "failed" })];
    const upload = vi.fn(async () => undefined);
    const summary = await uploadPendingAttachments(items, upload, () => undefined);
    expect(upload).toHaveBeenCalledTimes(1);
    expect(summary.succeeded.map((item) => item.file.name)).toEqual(["b.pdf"]);
  });
});

describe("getUploadErrorMessage", () => {
  it("reads API error payloads, Error instances and strings", () => {
    expect(getUploadErrorMessage({ error: "too big" })).toBe("too big");
    expect(getUploadErrorMessage(new Error("network down"))).toBe("network down");
    expect(getUploadErrorMessage("nope")).toBe("nope");
    expect(getUploadErrorMessage(undefined)).toBe("Upload failed");
  });
});

describe("getFailedUploadsMessage", () => {
  it("names up to three files and counts the rest", () => {
    const failed = ["a", "b", "c", "d", "e"].map((name) => makeItem(`${name}.pdf`, { status: "failed" }));
    const message = getFailedUploadsMessage(failed);
    expect(message).toContain("a.pdf, b.pdf, c.pdf and 2 more");
    expect(message).toContain("The work item was created");
  });

  it("uses singular wording for one file", () => {
    expect(getFailedUploadsMessage([makeItem("a.pdf", { status: "failed" })])).toContain("add the file again");
  });
});

describe("getReferencedAssetIds", () => {
  const html = '<p>hi</p><image-component src="asset-1" width="100"></image-component>';

  it("keeps only ids the final description still references", () => {
    expect(getReferencedAssetIds(["asset-1", "asset-removed"], html)).toEqual(["asset-1"]);
  });

  it("de-duplicates and ignores empty ids", () => {
    expect(getReferencedAssetIds(["asset-1", "asset-1", ""], html)).toEqual(["asset-1"]);
  });

  it("returns nothing for an empty or missing description", () => {
    expect(getReferencedAssetIds(["asset-1"], "")).toEqual([]);
    expect(getReferencedAssetIds(["asset-1"], undefined)).toEqual([]);
    expect(getReferencedAssetIds(["asset-1"], null)).toEqual([]);
  });
});
