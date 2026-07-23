/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import useSWR from "swr";
// plane imports
import { BarChart } from "@plane/propel/charts/bar-chart";
import type { TBarItem } from "@plane/types";
// local imports
import { customDashboardsService } from "@/plane-web/custom-dashboards";
import type { TAgeTrendData, TDashboardWidget } from "@/plane-web/custom-dashboards";
import { WidgetCard } from "../widget-card";
import type { TWidgetCardAdminActionsProp } from "../widget-card-types";

type Props = {
  widget: TDashboardWidget<"age_trend">;
  workspaceSlug: string;
  adminActions?: TWidgetCardAdminActionsProp;
};

export function AgeTrendBarWidget(props: Props) {
  const { widget, workspaceSlug, adminActions } = props;

  const { data, error, isLoading, mutate } = useSWR(
    ["dashboard-widget-data", widget.id],
    () => customDashboardsService.getChartData(workspaceSlug, widget.id) as Promise<TAgeTrendData>
  );

  // Backend already returns a display-ready `name` ("Jul 23, 2026"), so `name`
  // is used directly as the x-axis label — no client-side reformatting.
  const chartData = data?.data ?? [];

  const bars: TBarItem<"avg_age_days">[] = useMemo(
    () => [
      {
        key: "avg_age_days",
        label: "Avg age (days)",
        fill: "#6172E8",
        textClassName: "text-primary",
        stackId: "age",
      },
    ],
    []
  );

  return (
    <WidgetCard
      widget={widget}
      adminActions={adminActions}
      isLoading={isLoading}
      isError={!!error}
      isEmpty={!isLoading && !error && chartData.length === 0}
      onRetry={() => mutate()}
    >
      <BarChart
        className="h-[260px] w-full"
        data={chartData}
        bars={bars}
        xAxis={{ key: "name" }}
        yAxis={{ key: "avg_age_days", label: "Avg age (days)", allowDecimals: true }}
      />
    </WidgetCard>
  );
}
