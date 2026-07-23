/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { type ReactNode } from "react";
import { ArrowDown, ArrowUp, Eye, EyeOff, MoreHorizontal, Pencil, RefreshCw, Trash2 } from "lucide-react";
// plane imports
import { CustomMenu, Loader } from "@plane/ui";
import { cn } from "@plane/utils";
// local imports
import { DASHBOARD_WIDGET_TYPE_LABELS } from "@/plane-web/custom-dashboards";
import type { TDashboardWidget } from "@/plane-web/custom-dashboards";
import type { TWidgetCardAdminActionsProp } from "./widget-card-types";

type Props = {
  widget: TDashboardWidget;
  /** Admin-only overflow menu. Omit for non-admins — the menu is not rendered at all. */
  adminActions?: TWidgetCardAdminActionsProp;
  /** Content states — pass exactly one truthy of loading/error/isEmpty, else children render. */
  isLoading?: boolean;
  isError?: boolean;
  isEmpty?: boolean;
  errorMessage?: string;
  emptyMessage?: string;
  onRetry?: () => void;
  className?: string;
  children?: ReactNode;
};

export function WidgetCard(props: Props) {
  const {
    widget,
    adminActions,
    isLoading,
    isError,
    isEmpty,
    errorMessage = "Couldn't load this widget.",
    emptyMessage = "No data for the current filters.",
    onRetry,
    className,
    children,
  } = props;

  const title = widget.title?.trim() || DASHBOARD_WIDGET_TYPE_LABELS[widget.widget_type];

  return (
    <div
      className={cn(
        "flex min-h-[320px] flex-col rounded-lg border border-subtle bg-surface-1 p-4",
        // muted treatment for disabled widgets (only ever rendered to admins)
        !widget.is_enabled && "opacity-60",
        className
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h4 className="truncate text-sm font-medium text-primary" title={title}>
            {title}
          </h4>
          {!widget.is_enabled && (
            <span className="flex-shrink-0 rounded bg-layer-2 px-1.5 py-0.5 text-11 uppercase tracking-wide text-tertiary">
              Disabled
            </span>
          )}
        </div>
        {adminActions && (
          <CustomMenu
            customButton={
              <span className="grid size-6 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-primary">
                <MoreHorizontal className="size-4" />
              </span>
            }
            placement="bottom-end"
            closeOnSelect
          >
            <CustomMenu.MenuItem onClick={adminActions.onEdit} className="flex items-center gap-2">
              <Pencil className="size-3.5" />
              Edit config
            </CustomMenu.MenuItem>
            <CustomMenu.MenuItem onClick={adminActions.onToggleEnabled} className="flex items-center gap-2">
              {widget.is_enabled ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
              {widget.is_enabled ? "Disable" : "Enable"}
            </CustomMenu.MenuItem>
            <CustomMenu.MenuItem
              onClick={adminActions.onMoveUp}
              disabled={!adminActions.canMoveUp}
              className="flex items-center gap-2"
            >
              <ArrowUp className="size-3.5" />
              Move up
            </CustomMenu.MenuItem>
            <CustomMenu.MenuItem
              onClick={adminActions.onMoveDown}
              disabled={!adminActions.canMoveDown}
              className="flex items-center gap-2"
            >
              <ArrowDown className="size-3.5" />
              Move down
            </CustomMenu.MenuItem>
            <CustomMenu.MenuItem
              onClick={adminActions.onDelete}
              className="flex items-center gap-2 text-danger-primary"
            >
              <Trash2 className="size-3.5" />
              Delete
            </CustomMenu.MenuItem>
          </CustomMenu>
        )}
      </div>

      <div className="flex flex-1 flex-col">
        {isLoading ? (
          <Loader className="flex h-full w-full flex-1 items-center justify-center">
            <Loader.Item height="220px" width="100%" />
          </Loader>
        ) : isError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-tertiary">{errorMessage}</p>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="flex items-center gap-1 text-xs text-accent-primary hover:underline"
              >
                <RefreshCw className="size-3.5" />
                Retry
              </button>
            )}
          </div>
        ) : isEmpty ? (
          <div className="flex flex-1 items-center justify-center text-center">
            <p className="text-sm text-tertiary">{emptyMessage}</p>
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
