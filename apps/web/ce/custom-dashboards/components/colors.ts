/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Cell-color resolution for the pie widgets.
 *
 * Reuses the existing shared color constants rather than inventing a new
 * palette:
 *  - `state_group` group_by keys map to the canonical `STATE_GROUPS[*].color`
 *    (packages/constants/src/state.ts) so a "completed" slice is the same green
 *    everywhere in the product.
 *  - everything else (raw state UUIDs, priority values, project ids) falls back
 *    to the shared `CHART_COLOR_PALETTES` "modern" scheme
 *    (packages/constants/src/chart.ts) — the same palette the analytics charts
 *    draw from — indexed deterministically so a given datum keeps its color
 *    across re-renders.
 */

import { CHART_COLOR_PALETTES, STATE_GROUPS } from "@plane/constants";
import type { TChartDatum } from "@/plane-web/custom-dashboards";

const MODERN_PALETTE = CHART_COLOR_PALETTES.find((p) => p.key === "modern")?.light ?? [
  "#6172E8",
  "#8B6EDB",
  "#E05F99",
  "#29A383",
  "#CB8A37",
];

const STATE_GROUP_COLORS: Record<string, string> = Object.fromEntries(
  Object.values(STATE_GROUPS).map((group) => [group.key, group.color])
);

/**
 * Builds the `cells` array the propel `PieChart` expects, one `{ key, fill }`
 * per datum. When `useStateGroupColors` is true (distribution_pie grouped by
 * state_group) known group keys use their canonical color; anything unmatched
 * cycles through the modern palette.
 */
export const buildPieCells = (
  data: TChartDatum[],
  options?: { useStateGroupColors?: boolean }
): { key: string; fill: string }[] =>
  data.map((datum, index) => {
    const canonical = options?.useStateGroupColors ? STATE_GROUP_COLORS[datum.key] : undefined;
    return {
      key: datum.key,
      fill: canonical ?? MODERN_PALETTE[index % MODERN_PALETTE.length],
    };
  });
