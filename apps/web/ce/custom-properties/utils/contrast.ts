/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { getContrastRatio, hexToRgb } from "@plane/utils";

const WHITE = { r: 255, g: 255, b: 255 };
const NEAR_BLACK = { r: 23, g: 23, b: 23 };

/**
 * Pick white or near-black label text for a filled option colour — whichever
 * clears the higher contrast ratio (WCAG-style). Falls back to near-black for
 * unparseable colours.
 */
export const textColorFor = (background: string): string => {
  try {
    const rgb = hexToRgb(background);
    return getContrastRatio(rgb, WHITE) >= getContrastRatio(rgb, NEAR_BLACK) ? "#ffffff" : "#171717";
  } catch {
    return "#171717";
  }
};

/** Neutral fill for an option with no colour set. */
export const EMPTY_OPTION_BG = "#e5e5e5";
