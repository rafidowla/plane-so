/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";
// local imports
import { customDashboardsService } from "@/plane-web/custom-dashboards";
import type { TDashboardWidget, TProjectBreakdownPieData } from "@/plane-web/custom-dashboards";
import { WidgetCard } from "../widget-card";
import type { TWidgetCardAdminActionsProp } from "../widget-card-types";
import { PieWidgetBody } from "./pie-widget-body";

type Props = {
  widget: TDashboardWidget<"project_breakdown_pie">;
  workspaceSlug: string;
  adminActions?: TWidgetCardAdminActionsProp;
};

export function ProjectBreakdownPieWidget(props: Props) {
  const { widget, workspaceSlug, adminActions } = props;

  const { data, error, isLoading, mutate } = useSWR(
    ["dashboard-widget-data", widget.id],
    () => customDashboardsService.getChartData(workspaceSlug, widget.id) as Promise<TProjectBreakdownPieData>
  );

  const chartData = data?.data ?? [];

  return (
    <WidgetCard
      widget={widget}
      adminActions={adminActions}
      isLoading={isLoading}
      isError={!!error}
      isEmpty={!isLoading && !error && chartData.length === 0}
      onRetry={() => mutate()}
    >
      <PieWidgetBody data={chartData} />
    </WidgetCard>
  );
}
