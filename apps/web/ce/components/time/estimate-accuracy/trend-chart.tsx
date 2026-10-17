/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S7 — accuracy over time. Buckets with too few tasks are drawn grey: they are noise, not a trend.
import { useMemo } from "react";
import { BarChart } from "@plane/blocks/charts/bar-chart";
import type { TBarItem } from "@plane/types";
import { trendChartData, type TTrendDatum, type TTrendMetric } from "./accuracy-utils";
import type { TAccuracyGranularity, TAccuracyTrendPoint } from "./types";

const COLOR = "#6172E8";
const COLOR_LOW_SAMPLE = "#C4C7CF";

type Props = {
  trend: TAccuracyTrendPoint[];
  granularity: TAccuracyGranularity;
  metric: TTrendMetric;
  minSample: number;
};

export function TrendChart({ trend, granularity, metric, minSample }: Props) {
  const data = useMemo(() => trendChartData(trend, granularity, metric), [trend, granularity, metric]);
  const label = metric === "ratio" ? "Actual as % of estimate" : "Tasks within band (%)";
  const axisLabel = metric === "ratio" ? "% of estimate" : "% within band";

  const bars: TBarItem<"value">[] = useMemo(
    () => [
      {
        key: "value",
        label,
        fill: (payload: TTrendDatum) => (payload.low_sample === 1 ? COLOR_LOW_SAMPLE : COLOR),
        textClassName: "text-primary",
        stackId: "accuracy",
      },
    ],
    [label]
  );

  if (data.length === 0) {
    return <div className="text-sm py-10 text-center text-tertiary">No completed, estimated work in this range.</div>;
  }

  return (
    <div data-testid="accuracy-trend">
      <BarChart
        className="h-[260px] w-full"
        data={data}
        bars={bars}
        xAxis={{ key: "name" }}
        yAxis={{
          key: "value",
          label: axisLabel,
          allowDecimals: false,
          ...(metric === "within" ? { domain: [0, 100] as [number, number] } : {}),
        }}
        customTooltipContent={({ active, label: name, payload }) => {
          const item = active ? (payload?.[0]?.payload as TTrendDatum | undefined) : undefined;
          if (!item) return null;
          return (
            <div className="text-xs rounded-md border border-subtle bg-surface-1 px-3 py-2 shadow-raised-200">
              <div className="font-medium text-primary">{name}</div>
              <div className="text-secondary">{item.tooltip}</div>
            </div>
          );
        }}
      />
      <p className="text-xs mt-2 text-tertiary">
        <span className="mr-1 inline-block size-2 rounded-sm align-middle" style={{ background: COLOR_LOW_SAMPLE }} />
        Grey bars have fewer than {minSample} tasks, too few to read.
      </p>
    </div>
  );
}
