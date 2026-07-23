/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set, sortBy } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";

import { customDashboardsService } from "@/plane-web/custom-dashboards/custom-dashboards.service";
import type {
  TDashboardWidget,
  TDashboardWidgetCreatePayload,
  TDashboardWidgetUpdatePayload,
} from "@/plane-web/custom-dashboards";
import type { RootStore } from "@/plane-web/store/root.store";

// ---------------------------------------------------------------------------
// Custom dashboards (widget list/config only — NOT chart/issue data, see below)
//
// Chart data (`.../widgets/<id>/data/`) and view_list issue rows
// (`.../widgets/<id>/issues/`) are intentionally NOT stored here. They are
// fetched directly via SWR in Phase 6 components, the same way
// apps/web/core/components/analytics/work-items/created-vs-resolved.tsx pulls
// analytics data — this store only owns "what widgets exist and how are they
// configured", a short admin-configured list per workspace, not a high-volume
// data structure.
// ---------------------------------------------------------------------------

export interface ICustomDashboardsStore {
  /** workspaceSlug -> widgets (unsorted; use the getters below for ordered reads). */
  widgetsMap: Record<string, TDashboardWidget[]>;
  fetchedMap: Record<string, boolean>;
  // reads
  getWidgetsForWorkspace: (workspaceSlug: string | null | undefined) => TDashboardWidget[];
  getEnabledWidgetsForWorkspace: (workspaceSlug: string | null | undefined) => TDashboardWidget[];
  isFetched: (workspaceSlug: string | null | undefined) => boolean;
  // actions
  fetchWidgets: (workspaceSlug: string) => Promise<TDashboardWidget[]>;
  createWidget: (workspaceSlug: string, payload: TDashboardWidgetCreatePayload) => Promise<TDashboardWidget>;
  updateWidget: (
    workspaceSlug: string,
    widgetId: string,
    payload: TDashboardWidgetUpdatePayload
  ) => Promise<TDashboardWidget>;
  deleteWidget: (workspaceSlug: string, widgetId: string) => Promise<void>;
  reorderWidget: (workspaceSlug: string, widgetId: string, newSortOrder: number) => Promise<TDashboardWidget>;
}

export class CustomDashboardsStore implements ICustomDashboardsStore {
  widgetsMap: Record<string, TDashboardWidget[]> = {};
  fetchedMap: Record<string, boolean> = {};
  rootStore: RootStore;

  constructor(rootStore: RootStore) {
    makeObservable(this, {
      widgetsMap: observable,
      fetchedMap: observable,
      fetchWidgets: action,
      createWidget: action,
      updateWidget: action,
      deleteWidget: action,
      reorderWidget: action,
    });
    this.rootStore = rootStore;
  }

  getWidgetsForWorkspace = computedFn((workspaceSlug: string | null | undefined): TDashboardWidget[] => {
    if (!workspaceSlug) return [];
    return sortBy(this.widgetsMap[workspaceSlug] ?? [], "sort_order");
  });

  getEnabledWidgetsForWorkspace = computedFn((workspaceSlug: string | null | undefined): TDashboardWidget[] =>
    this.getWidgetsForWorkspace(workspaceSlug).filter((widget) => widget.is_enabled)
  );

  isFetched = computedFn((workspaceSlug: string | null | undefined): boolean =>
    workspaceSlug ? this.fetchedMap[workspaceSlug] === true : false
  );

  fetchWidgets = async (workspaceSlug: string): Promise<TDashboardWidget[]> => {
    const res = await customDashboardsService.list(workspaceSlug);
    runInAction(() => {
      set(this.widgetsMap, [workspaceSlug], res ?? []);
      set(this.fetchedMap, [workspaceSlug], true);
    });
    return res;
  };

  /**
   * Not optimistic — `id` and `sort_order` are server-assigned (the model
   * defaults `sort_order=65535` and the DB assigns the id), so there is
   * nothing valid to show before the response comes back. Mirrors
   * `CustomPropertiesStore.createProperty`: call the API, then merge the
   * returned widget into local state (no full refetch of the list).
   */
  createWidget = async (workspaceSlug: string, payload: TDashboardWidgetCreatePayload): Promise<TDashboardWidget> => {
    const res = await customDashboardsService.create(workspaceSlug, payload);
    runInAction(() => {
      const existing = this.widgetsMap[workspaceSlug] ?? [];
      set(this.widgetsMap, [workspaceSlug], [...existing, res]);
    });
    return res;
  };

  /**
   * Optimistic with revert-on-error, mirroring `CustomPropertiesStore.updateProperty`
   * exactly — applies `payload` to the local copy immediately (so toggling
   * `is_enabled` or editing a title feels instant), then reconciles with the
   * server response, and rolls back to the pre-mutation widget if the request fails.
   */
  updateWidget = async (
    workspaceSlug: string,
    widgetId: string,
    payload: TDashboardWidgetUpdatePayload
  ): Promise<TDashboardWidget> => {
    const existing = this.widgetsMap[workspaceSlug] ?? [];
    const original = existing.find((widget) => widget.id === widgetId);

    runInAction(() => {
      set(
        this.widgetsMap,
        [workspaceSlug],
        existing.map((widget) => (widget.id === widgetId ? Object.assign({}, widget, payload) : widget))
      );
    });

    try {
      const res = await customDashboardsService.update(workspaceSlug, widgetId, payload);
      runInAction(() => {
        const current = this.widgetsMap[workspaceSlug] ?? [];
        set(
          this.widgetsMap,
          [workspaceSlug],
          current.map((widget) => (widget.id === widgetId ? res : widget))
        );
      });
      return res;
    } catch (error) {
      if (original) {
        runInAction(() => {
          const current = this.widgetsMap[workspaceSlug] ?? [];
          set(
            this.widgetsMap,
            [workspaceSlug],
            current.map((widget) => (widget.id === widgetId ? original : widget))
          );
        });
      }
      throw error;
    }
  };

  deleteWidget = async (workspaceSlug: string, widgetId: string): Promise<void> => {
    await customDashboardsService.remove(workspaceSlug, widgetId);
    runInAction(() => {
      const existing = this.widgetsMap[workspaceSlug] ?? [];
      set(
        this.widgetsMap,
        [workspaceSlug],
        existing.filter((widget) => widget.id !== widgetId)
      );
    });
  };

  /**
   * Reorder is just a `sort_order`-only update, so it reuses `updateWidget`'s
   * optimistic-with-revert behavior for the same instant drag-and-drop feedback.
   * `newSortOrder` is expected to already be the midpoint value computed by
   * `getWidgetReorderSortOrder` (below) — this action only applies and persists it.
   */
  reorderWidget = async (workspaceSlug: string, widgetId: string, newSortOrder: number): Promise<TDashboardWidget> =>
    this.updateWidget(workspaceSlug, widgetId, { sort_order: newSortOrder });
}

/**
 * Computes the `sort_order` for a widget dropped at `destinationIndex` within
 * `orderedWidgets` (already sorted by `sort_order`, ascending). Copied from
 * `apps/web/core/store/label.store.ts`'s drag-and-drop reorder math (the
 * `65535` default / `prevSortOrder + 10000` / `nextSortOrder / 2` / midpoint
 * fallbacks), not invented fresh — see that file's `reorderLabel`-adjacent
 * logic (~line 280) for the original. Phase 6 calls this to get the value to
 * pass into `reorderWidget`.
 */
export const getWidgetReorderSortOrder = (orderedWidgets: TDashboardWidget[], destinationIndex: number): number => {
  const prevSortOrder = orderedWidgets[destinationIndex - 1]?.sort_order;
  const nextSortOrder = orderedWidgets[destinationIndex]?.sort_order;

  if (prevSortOrder !== undefined && nextSortOrder !== undefined) {
    return (prevSortOrder + nextSortOrder) / 2;
  }
  if (nextSortOrder !== undefined) {
    return nextSortOrder / 2;
  }
  if (prevSortOrder !== undefined) {
    return prevSortOrder + 10000;
  }
  return 65535;
};
