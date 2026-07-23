/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";
// local imports
import { customDashboardsService } from "@/plane-web/custom-dashboards";
import type { TDashboardWidget, TDistributionPieData } from "@/plane-web/custom-dashboards";
import { WidgetCard } from "../widget-card";
import type { TWidgetCardAdminActionsProp } from "../widget-card-types";
import { PieWidgetBody } from "./pie-widget-body";

type Props = {
  widget: TDashboardWidget<"distribution_pie">;
  workspaceSlug: string;
  adminActions?: TWidgetCardAdminActionsProp;
};

/** Capitalize the first letter of raw state_group / priority values for display. */
const prettify = (value: string): string => (value ? value.charAt(0).toUpperCase() + value.slice(1) : value);

export function DistributionPieWidget(props: Props) {
  const { widget, workspaceSlug, adminActions } = props;

  const { data, error, isLoading, mutate } = useSWR(
    ["dashboard-widget-data", widget.id],
    () => customDashboardsService.getChartData(workspaceSlug, widget.id) as Promise<TDistributionPieData>
  );

  const groupBy = widget.config?.group_by;
  const chartData = data?.data ?? [];
  const showCenterLabel = data?.completed_percentage !== null && data?.completed_percentage !== undefined;

  return (
    <WidgetCard
      widget={widget}
      adminActions={adminActions}
      isLoading={isLoading}
      isError={!!error}
      isEmpty={!isLoading && !error && chartData.length === 0}
      onRetry={() => mutate()}
    >
      <PieWidgetBody
        data={chartData}
        useStateGroupColors={groupBy === "state_group"}
        centerLabelText={showCenterLabel ? `${data?.completed_percentage ?? 0}% Done` : undefined}
        formatName={groupBy === "state" ? undefined : (datum) => prettify(datum.name)}
      />
    </WidgetCard>
  );
}
