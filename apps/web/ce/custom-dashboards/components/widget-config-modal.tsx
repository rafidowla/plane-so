/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import useSWR from "swr";
// plane imports
import { Button } from "@plane/propel/button";
import { GlobeIcon, LockIcon, ViewsIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { ICustomSearchSelectOption } from "@plane/types";
import { EViewAccess } from "@plane/types";
import { CustomSearchSelect, CustomSelect, EModalWidth, Input, ModalCore } from "@plane/ui";
import { cn } from "@plane/utils";
// components
import { ProjectDropdown } from "@/components/dropdowns/project/dropdown";
// hooks
import { useGlobalView } from "@/hooks/store/use-global-view";
// local imports
import {
  AGE_TREND_LOOKBACK_OPTIONS,
  DASHBOARD_WIDGET_PAGE_SIZE_OPTIONS,
  DASHBOARD_WIDGET_TYPE_LABELS,
  DEFAULT_WIDGET_CONFIG,
  useCustomDashboard,
} from "@/plane-web/custom-dashboards";
import type {
  TAgeTrendLookbackDays,
  TDashboardWidget,
  TDashboardWidgetConfig,
  TDashboardWidgetCreatePayload,
  TDashboardWidgetType,
  TDistributionPieGroupBy,
  TViewListPageSize,
} from "@/plane-web/custom-dashboards";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  /** Present ⇒ edit mode (type is fixed). Absent ⇒ create mode. */
  widget?: TDashboardWidget | null;
};

const WIDGET_TYPE_ORDER: TDashboardWidgetType[] = [
  "distribution_pie",
  "project_breakdown_pie",
  "age_trend",
  "view_list",
];

const GROUP_BY_OPTIONS: { value: TDistributionPieGroupBy; label: string }[] = [
  { value: "state", label: "State" },
  { value: "state_group", label: "State group" },
  { value: "priority", label: "Priority" },
];

export const WidgetConfigModal = observer(function WidgetConfigModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, widget } = props;
  const isEdit = !!widget;

  const { createWidget, updateWidget } = useCustomDashboard();
  const { fetchAllGlobalViews, currentWorkspaceViews, getViewDetailsById } = useGlobalView();

  // Views are only needed for the view_list picker; fetched lazily when the modal opens.
  const { isLoading: isLoadingViews } = useSWR(isOpen ? ["dashboard-global-views", workspaceSlug] : null, () =>
    fetchAllGlobalViews(workspaceSlug)
  );

  // form state
  const [widgetType, setWidgetType] = useState<TDashboardWidgetType | null>(null);
  const [title, setTitle] = useState("");
  const [groupBy, setGroupBy] = useState<TDistributionPieGroupBy>("state");
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [lookbackDays, setLookbackDays] = useState<TAgeTrendLookbackDays>(30);
  const [issueViewId, setIssueViewId] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState<TViewListPageSize>(10);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // (re)initialize whenever the modal opens or the target widget changes
  useEffect(() => {
    if (!isOpen) return;
    if (widget) {
      setWidgetType(widget.widget_type);
      setTitle(widget.title ?? "");
      const config = (widget.config ?? {}) as Record<string, unknown>;
      setGroupBy((config.group_by as TDistributionPieGroupBy) ?? "state");
      setProjectIds((config.project_ids as string[] | null) ?? []);
      setLookbackDays((config.lookback_days as TAgeTrendLookbackDays) ?? 30);
      setPageSize((config.page_size as TViewListPageSize) ?? 10);
      setIssueViewId(widget.issue_view ?? null);
    } else {
      setWidgetType(null);
      setTitle("");
      setGroupBy("state");
      setProjectIds([]);
      setLookbackDays(30);
      setPageSize(10);
      setIssueViewId(null);
    }
  }, [isOpen, widget]);

  // All views returned by useGlobalView are workspace-level (the API filters out project-scoped
  // views), so there's no project/workspace grouping to show here — every option lives in one
  // flat, workspace-wide list. Access (public/private) is the only per-view distinguisher worth
  // surfacing, so it's shown as secondary text/icon instead.
  const viewOptions: ICustomSearchSelectOption[] = useMemo(
    () =>
      (currentWorkspaceViews ?? []).flatMap((viewId) => {
        const view = getViewDetailsById(viewId);
        if (!view) return [];
        const isPrivate = view.access === EViewAccess.PRIVATE;
        return [
          {
            value: view.id,
            query: view.name.toLowerCase(),
            tooltip: view.description || undefined,
            content: (
              <div className="flex w-full items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 truncate">
                  <ViewsIcon className="h-3.5 w-3.5 flex-shrink-0 text-tertiary" />
                  <span className="truncate">{view.name}</span>
                </span>
                <span className="flex flex-shrink-0 items-center gap-1 text-11 text-secondary">
                  {isPrivate ? (
                    <LockIcon className="h-3 w-3 flex-shrink-0" />
                  ) : (
                    <GlobeIcon className="h-3 w-3 flex-shrink-0" />
                  )}
                  {isPrivate ? "Private" : "Public"}
                </span>
              </div>
            ),
          },
        ];
      }),
    [currentWorkspaceViews, getViewDetailsById]
  );

  const isLoadingViewOptions = isLoadingViews && viewOptions.length === 0;

  const buildConfig = (type: TDashboardWidgetType): TDashboardWidgetConfig => {
    switch (type) {
      case "distribution_pie":
        return { group_by: groupBy, project_ids: projectIds.length ? projectIds : null };
      case "project_breakdown_pie":
        return { project_ids: projectIds.length ? projectIds : null };
      case "age_trend":
        return { project_ids: projectIds.length ? projectIds : null, lookback_days: lookbackDays };
      case "view_list":
        return { page_size: pageSize };
      default:
        return DEFAULT_WIDGET_CONFIG[type];
    }
  };

  const submit = async () => {
    if (!widgetType) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: "Pick a widget type first." });
      return;
    }
    if (widgetType === "view_list" && !issueViewId) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: "Select a saved view for this widget." });
      return;
    }

    setIsSubmitting(true);
    try {
      const config = buildConfig(widgetType);
      if (isEdit && widget) {
        await updateWidget(workspaceSlug, widget.id, {
          title: title.trim(),
          config,
          ...(widgetType === "view_list" ? { issue_view: issueViewId } : {}),
        });
      } else {
        const payload: TDashboardWidgetCreatePayload = {
          widget_type: widgetType,
          title: title.trim() || undefined,
          config,
          ...(widgetType === "view_list" ? { issue_view: issueViewId } : {}),
        };
        await createWidget(workspaceSlug, payload);
      }
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Success",
        message: isEdit ? "Widget updated." : "Widget created.",
      });
      handleClose();
    } catch (error: unknown) {
      // Surface DRF 400 validation errors (the service rethrows `err.response.data`).
      const err = error as { detail?: string; error?: string; [key: string]: unknown };
      const message =
        err?.detail ??
        err?.error ??
        (typeof err === "object" && err ? Object.values(err).flat().join(" ") : undefined) ??
        "Something went wrong.";
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: String(message) });
    } finally {
      setIsSubmitting(false);
    }
  };

  const showProjectPicker =
    widgetType === "distribution_pie" || widgetType === "project_breakdown_pie" || widgetType === "age_trend";

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-lg font-medium">{isEdit ? "Edit widget" : "Add widget"}</h3>

        {/* widget-type picker (create mode only) */}
        {!isEdit && (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-secondary">Widget type</span>
            <div className="grid grid-cols-2 gap-2">
              {WIDGET_TYPE_ORDER.map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setWidgetType(type)}
                  className={cn(
                    "text-sm rounded border px-3 py-2 text-left transition-colors",
                    widgetType === type
                      ? "border-accent-primary bg-accent-primary/10 text-accent-primary"
                      : "border-subtle text-secondary hover:bg-layer-2"
                  )}
                >
                  {DASHBOARD_WIDGET_TYPE_LABELS[type]}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* per-type config form (once a type exists) */}
        {widgetType && (
          <>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-secondary">Title (optional)</span>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={DASHBOARD_WIDGET_TYPE_LABELS[widgetType]}
                className="w-full"
              />
            </div>

            {widgetType === "distribution_pie" && (
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-secondary">Group by</span>
                <CustomSelect
                  value={groupBy}
                  onChange={(val: TDistributionPieGroupBy) => setGroupBy(val)}
                  label={GROUP_BY_OPTIONS.find((o) => o.value === groupBy)?.label ?? "Select"}
                  buttonClassName="w-full justify-between border border-subtle"
                  input
                >
                  {GROUP_BY_OPTIONS.map((option) => (
                    <CustomSelect.Option key={option.value} value={option.value}>
                      {option.label}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
              </div>
            )}

            {widgetType === "age_trend" && (
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-secondary">Lookback window</span>
                <CustomSelect
                  value={lookbackDays}
                  onChange={(val: TAgeTrendLookbackDays) => setLookbackDays(val)}
                  label={AGE_TREND_LOOKBACK_OPTIONS.find((o) => o.value === lookbackDays)?.label ?? "Select"}
                  buttonClassName="w-full justify-between border border-subtle"
                  input
                >
                  {AGE_TREND_LOOKBACK_OPTIONS.map((option) => (
                    <CustomSelect.Option key={option.value} value={option.value}>
                      {option.label}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
              </div>
            )}

            {showProjectPicker && (
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-secondary">Projects (optional — all if empty)</span>
                <ProjectDropdown
                  value={projectIds}
                  onChange={(val) => setProjectIds(Array.isArray(val) ? val : [val])}
                  multiple
                  buttonVariant="border-with-text"
                  buttonContainerClassName="w-full"
                  placeholder="All projects"
                />
              </div>
            )}

            {widgetType === "view_list" && (
              <>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-secondary">Saved view (required)</span>
                  {!isLoadingViewOptions && viewOptions.length === 0 ? (
                    <div className="text-xs rounded border border-dashed border-subtle px-3 py-2 text-secondary">
                      No saved views yet — create one from the{" "}
                      <Link
                        href={`/${workspaceSlug}/workspace-views`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-accent-primary hover:underline"
                      >
                        Views tab
                      </Link>{" "}
                      first.
                    </div>
                  ) : (
                    <CustomSearchSelect
                      value={issueViewId}
                      onChange={(val: string) => setIssueViewId(val)}
                      options={isLoadingViewOptions ? undefined : viewOptions}
                      label={issueViewId ? (getViewDetailsById(issueViewId)?.name ?? "Select a view") : "Select a view"}
                      buttonClassName="w-full justify-between border border-subtle"
                      optionsClassName="w-full"
                      noResultsMessage="No saved views found."
                    />
                  )}
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-secondary">Page size</span>
                  <CustomSelect
                    value={pageSize}
                    onChange={(val: TViewListPageSize) => setPageSize(val)}
                    label={DASHBOARD_WIDGET_PAGE_SIZE_OPTIONS.find((o) => o.value === pageSize)?.label ?? "Select"}
                    buttonClassName="w-full justify-between border border-subtle"
                    input
                  >
                    {DASHBOARD_WIDGET_PAGE_SIZE_OPTIONS.map((option) => (
                      <CustomSelect.Option key={option.value} value={option.value}>
                        {option.label}
                      </CustomSelect.Option>
                    ))}
                  </CustomSelect>
                </div>
              </>
            )}
          </>
        )}

        <div className="mt-2 flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={handleClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={submit} loading={isSubmitting} disabled={!widgetType}>
            {isEdit ? "Save changes" : "Create widget"}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
