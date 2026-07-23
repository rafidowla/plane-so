/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
// plane imports
import { PieChart } from "@plane/propel/charts/pie-chart";
// local imports
import type { TChartDatum } from "@/plane-web/custom-dashboards";
import { buildPieCells } from "../colors";

type Props = {
  data: TChartDatum[];
  /** Map state_group keys to their canonical colors (distribution_pie grouped by state_group). */
  useStateGroupColors?: boolean;
  /** Center donut label, e.g. "81% Done". Omit to render a plain donut. */
  centerLabelText?: string;
  /** Optional prettifier for legend row labels (e.g. capitalize priority values). */
  formatName?: (datum: TChartDatum) => string;
};

/**
 * Shared donut + legend body used by both pie widgets. A manual legend (colored
 * dot + name + count) is rendered instead of recharts' built-in legend so each
 * row can show the count alongside the name.
 */
export function PieWidgetBody(props: Props) {
  const { data, useStateGroupColors, centerLabelText, formatName } = props;

  const cells = useMemo(() => buildPieCells(data, { useStateGroupColors }), [data, useStateGroupColors]);
  const colorByKey = useMemo(() => Object.fromEntries(cells.map((c) => [c.key, c.fill])), [cells]);

  return (
    <div className="flex h-full flex-1 flex-col gap-3 md:flex-row md:items-center">
      <PieChart
        className="h-[220px] w-full md:w-1/2"
        data={data}
        dataKey="count"
        cells={cells}
        innerRadius={55}
        outerRadius={85}
        showLabel={false}
        centerLabel={centerLabelText ? { text: centerLabelText, fill: "var(--text-color-primary)" } : undefined}
      />
      <ul className="flex max-h-[220px] flex-col gap-1.5 overflow-y-auto md:w-1/2">
        {data.map((datum) => (
          <li key={datum.key} className="flex items-center gap-2 text-sm">
            <span
              className="size-2.5 flex-shrink-0 rounded-full"
              style={{ backgroundColor: colorByKey[datum.key] }}
            />
            <span className="min-w-0 flex-1 truncate text-secondary" title={formatName ? formatName(datum) : datum.name}>
              {formatName ? formatName(datum) : datum.name}
            </span>
            <span className="flex-shrink-0 font-medium text-primary">{datum.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
