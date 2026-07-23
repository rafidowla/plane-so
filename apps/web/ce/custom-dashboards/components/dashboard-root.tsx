/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Plus } from "lucide-react";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Spinner } from "@plane/ui";
import { cn } from "@plane/utils";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { getWidgetReorderSortOrder } from "@/plane-web/store/custom-dashboards";
import { useCustomDashboard } from "@/plane-web/custom-dashboards";
import type { TDashboardWidget } from "@/plane-web/custom-dashboards";
import type { TWidgetCardAdminActionsProp } from "./widget-card-types";
import { WidgetConfigModal } from "./widget-config-modal";
import { AgeTrendBarWidget } from "./widgets/age-trend-bar";
import { DistributionPieWidget } from "./widgets/distribution-pie";
import { ProjectBreakdownPieWidget } from "./widgets/project-breakdown-pie";
import { ViewIssuesTableWidget } from "./widgets/view-issues-table";

type Props = {
  workspaceSlug: string;
};

const renderWidget = (widget: TDashboardWidget, workspaceSlug: string, adminActions?: TWidgetCardAdminActionsProp) => {
  switch (widget.widget_type) {
    case "distribution_pie":
      return (
        <DistributionPieWidget
          widget={widget as TDashboardWidget<"distribution_pie">}
          workspaceSlug={workspaceSlug}
          adminActions={adminActions}
        />
      );
    case "project_breakdown_pie":
      return (
        <ProjectBreakdownPieWidget
          widget={widget as TDashboardWidget<"project_breakdown_pie">}
          workspaceSlug={workspaceSlug}
          adminActions={adminActions}
        />
      );
    case "age_trend":
      return (
        <AgeTrendBarWidget
          widget={widget as TDashboardWidget<"age_trend">}
          workspaceSlug={workspaceSlug}
          adminActions={adminActions}
        />
      );
    case "view_list":
      return (
        <ViewIssuesTableWidget
          widget={widget as TDashboardWidget<"view_list">}
          workspaceSlug={workspaceSlug}
          adminActions={adminActions}
        />
      );
    default:
      return null;
  }
};

export const DashboardRoot = observer(function DashboardRoot(props: Props) {
  const { workspaceSlug } = props;

  const { getWidgetsForWorkspace, fetchWidgets, updateWidget, deleteWidget, reorderWidget, isFetched } =
    useCustomDashboard();
  const { allowPermissions } = useUserPermissions();

  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingWidget, setEditingWidget] = useState<TDashboardWidget | null>(null);

  // fetch on mount (and whenever the workspace changes)
  useSWR(["dashboard-widgets", workspaceSlug], () => fetchWidgets(workspaceSlug));

  const allWidgets = getWidgetsForWorkspace(workspaceSlug);
  // Non-admins never see disabled widgets; admins see them (muted).
  const visibleWidgets = isAdmin ? allWidgets : allWidgets.filter((w) => w.is_enabled);

  const openCreate = () => {
    setEditingWidget(null);
    setIsModalOpen(true);
  };

  const openEdit = (widget: TDashboardWidget) => {
    setEditingWidget(widget);
    setIsModalOpen(true);
  };

  const handleToggleEnabled = async (widget: TDashboardWidget) => {
    try {
      await updateWidget(workspaceSlug, widget.id, { is_enabled: !widget.is_enabled });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: "Couldn't update the widget." });
    }
  };

  const handleDelete = async (widget: TDashboardWidget) => {
    try {
      await deleteWidget(workspaceSlug, widget.id);
      setToast({ type: TOAST_TYPE.SUCCESS, title: "Success", message: "Widget deleted." });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: "Couldn't delete the widget." });
    }
  };

  const handleMove = async (index: number, direction: "up" | "down") => {
    // `allWidgets` is already sorted by sort_order; compute the midpoint slot.
    const destinationIndex = direction === "up" ? index - 1 : index + 2;
    const newSortOrder = getWidgetReorderSortOrder(allWidgets, destinationIndex);
    try {
      await reorderWidget(workspaceSlug, allWidgets[index].id, newSortOrder);
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: "Couldn't reorder the widget." });
    }
  };

  const buildAdminActions = (widget: TDashboardWidget): TWidgetCardAdminActionsProp | undefined => {
    if (!isAdmin) return undefined;
    const index = allWidgets.findIndex((w) => w.id === widget.id);
    return {
      onEdit: () => openEdit(widget),
      onToggleEnabled: () => handleToggleEnabled(widget),
      onDelete: () => handleDelete(widget),
      onMoveUp: () => handleMove(index, "up"),
      onMoveDown: () => handleMove(index, "down"),
      canMoveUp: index > 0,
      canMoveDown: index < allWidgets.length - 1,
    };
  };

  // loading
  if (!isFetched(workspaceSlug)) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="h-full w-full overflow-y-auto p-6">
      <div className="mb-5 flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold text-primary">Dashboard</h2>
        {isAdmin && (
          <Button variant="primary" size="sm" onClick={openCreate} prependIcon={<Plus className="size-4" />}>
            Add widget
          </Button>
        )}
      </div>

      {visibleWidgets.length === 0 ? (
        <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
          {isAdmin ? (
            <>
              <p className="text-sm text-tertiary">No widgets yet. Add your first one to build the dashboard.</p>
              <Button variant="primary" size="sm" onClick={openCreate} prependIcon={<Plus className="size-4" />}>
                Add widget
              </Button>
            </>
          ) : (
            <p className="text-sm text-tertiary">No dashboard configured yet.</p>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {visibleWidgets.map((widget) => (
            <div key={widget.id} className={cn(widget.widget_type === "view_list" && "md:col-span-2")}>
              {renderWidget(widget, workspaceSlug, buildAdminActions(widget))}
            </div>
          ))}
        </div>
      )}

      {isAdmin && (
        <WidgetConfigModal
          isOpen={isModalOpen}
          handleClose={() => setIsModalOpen(false)}
          workspaceSlug={workspaceSlug}
          widget={editingWidget}
        />
      )}
    </div>
  );
});
