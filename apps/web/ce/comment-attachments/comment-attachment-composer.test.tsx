/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// @vitest-environment jsdom
// FORK: regression test for the PSR-76 reply-composer dev warning. The staged-assets
// callback (a parent setState) used to be invoked inside a setState updater, which React
// runs during render -> "Cannot update a component while rendering a different component".
import { useState } from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

vi.mock("@makeplane/propel/components/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@plane/blocks/toast", () => ({ setToast: vi.fn() }));
vi.mock("@/hooks/use-file-size", () => ({ useFileSize: () => ({ maxFileSize: 5 * 1024 * 1024 }) }));

import { CommentAttachmentComposer } from "./comment-attachment-composer";

// mirrors CommentReplyComposer: parent stores the ids reported by the child in its own state
function Parent({ uploadAsset }: { uploadAsset: (file: File) => Promise<string> }) {
  const [ids, setIds] = useState<string[]>([]);
  return (
    <div>
      <span data-testid="ids">{ids.join(",")}</span>
      <CommentAttachmentComposer uploadAsset={uploadAsset} onStagedAssetIdsChange={setIds} hideAttachButton />
    </div>
  );
}

describe("CommentAttachmentComposer", () => {
  let errorSpy: MockInstance<typeof console.error>;
  beforeEach(() => {
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => {
    cleanup();
    errorSpy.mockRestore();
  });

  it("reports staged ids to the parent without updating it during render", async () => {
    const uploadAsset = vi.fn().mockResolvedValue("asset-1");
    const { container, getByTestId, getByTitle } = render(<Parent uploadAsset={uploadAsset} />);
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });

    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => expect(getByTestId("ids").textContent).toBe("asset-1"));

    await act(async () => {
      fireEvent.click(getByTitle("Remove notes.txt"));
    });
    expect(getByTestId("ids").textContent).toBe("");

    const messages = errorSpy.mock.calls.map((call: unknown[]) => String(call[0]));
    expect(messages.some((m) => m.includes("while rendering a different component"))).toBe(false);
  });
});
