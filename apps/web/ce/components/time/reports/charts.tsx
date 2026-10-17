/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — daily (or weekly) logged-vs-expected bars and the running total line,
// built on the same chart library the Analytics tabs use.
import { useMemo } from "react";
import { BarChart } from "@plane/blocks/charts/bar-chart";
import { LineChart } from "@plane/blocks/charts/line-chart";
import { buildChartPoints } from "./report-utils";
import type { TReportSeriesPoint } from "./types";

type TProps = { series: TReportSeriesPoint[] };

const LOGGED = "#3f76ff";
const LOGGED_OFF_DAY = "#9db8ff";
const EXPECTED = "#c1c7d0";

export function ReportCharts({ series }: TProps) {
  const { points, bucket } = useMemo(() => buildChartPoints(series), [series]);
  if (points.length === 0) return null;
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="report-charts">
      <div className="rounded-lg border border-subtle bg-surface-1 p-4">
        <h3 className="text-sm mb-2 font-medium text-primary">
          {bucket === "week" ? "Logged per week (hours)" : "Logged per day (hours)"}
        </h3>
        <BarChart
          className="h-[300px] w-full"
          margin={{ top: 10, right: 20, bottom: 5, left: 0 }}
          data={points}
          bars={[
            {
              key: "logged",
              label: "Logged",
              stackId: "logged",
              fill: (payload: { working?: number }) => (payload.working === 0 ? LOGGED_OFF_DAY : LOGGED),
              textClassName: "",
              showPercentage: false,
              showTopBorderRadius: () => true,
              showBottomBorderRadius: () => true,
            },
            {
              key: "expected",
              label: "Expected",
              stackId: "expected",
              fill: EXPECTED,
              textClassName: "",
              showPercentage: false,
              showTopBorderRadius: () => true,
              showBottomBorderRadius: () => true,
            },
          ]}
          xAxis={{ key: "name" }}
          yAxis={{ key: "logged", label: "Hours", allowDecimals: true, offset: -30, dx: -20 }}
          barSize={bucket === "week" ? 14 : 10}
          legend={{ align: "left", verticalAlign: "bottom", layout: "horizontal" }}
        />
      </div>
      <div className="rounded-lg border border-subtle bg-surface-1 p-4">
        <h3 className="text-sm mb-2 font-medium text-primary">Running total (hours)</h3>
        <LineChart
          className="h-[300px] w-full"
          margin={{ top: 10, right: 20, bottom: 5, left: 0 }}
          data={points}
          lines={[
            {
              key: "cumulative_logged",
              label: "Logged",
              dashedLine: false,
              fill: LOGGED,
              stroke: LOGGED,
              showDot: false,
              smoothCurves: false,
            },
            {
              key: "cumulative_expected",
              label: "Expected",
              dashedLine: true,
              fill: EXPECTED,
              stroke: "#8a93a0",
              showDot: false,
              smoothCurves: false,
            },
          ]}
          xAxis={{ key: "name" }}
          yAxis={{ key: "cumulative_logged", label: "Hours", allowDecimals: true, offset: -30, dx: -20 }}
          legend={{ align: "left", verticalAlign: "bottom", layout: "horizontal" }}
        />
      </div>
    </div>
  );
}
