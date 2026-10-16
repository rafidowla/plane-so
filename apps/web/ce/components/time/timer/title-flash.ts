/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — fallback when the chime is blocked: alternate the tab title with
// "⏰ Still working?" so a background tab is still noticeable.
export const TITLE_FLASH_INTERVAL_MS = 1_000;
export const TITLE_FLASH_TEXT = "⏰ Still working?";

/** Starts flashing; returns a stop function that restores the original title. */
export function startTitleFlash(doc: Pick<Document, "title"> = document): () => void {
  let original = doc.title;
  let showing = false;
  const handle = setInterval(() => {
    showing = !showing;
    if (showing) {
      // Remember whatever title the page has right now (it may change while we flash).
      if (doc.title !== TITLE_FLASH_TEXT) original = doc.title;
      doc.title = TITLE_FLASH_TEXT;
    } else {
      doc.title = original;
    }
  }, TITLE_FLASH_INTERVAL_MS);
  return () => {
    clearInterval(handle);
    if (doc.title === TITLE_FLASH_TEXT) doc.title = original;
  };
}
