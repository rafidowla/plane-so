/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import useSWR from "swr";
// plane imports
import { PriorityIcon, StateGroupIcon } from "@plane/propel/icons";
import type { TIssuePriorities, TStateGroups } from "@plane/types";
import { cn } from "@plane/utils";
// hooks
import { useProject } from "@/hooks/store/use-project";
// local imports
import { customDashboardsService } from "@/plane-web/custom-dashboards";
import type { TDashboardWidget, TViewListIssue, TViewListIssuesResponse } from "@/plane-web/custom-dashboards";
import { WidgetCard } from "../widget-card";
import type { TWidgetCardAdminActionsProp } from "../widget-card-types";

type Props = {
  widget: TDashboardWidget<"view_list">;
  workspaceSlug: string;
  adminActions?: TWidgetCardAdminActionsProp;
};

const isViewDeleted = (res: TViewListIssuesResponse | undefined): res is { data: []; view_deleted: true } =>
  !!res && "view_deleted" in res && res.view_deleted === true;

const IssueRow = observer(function IssueRow(props: { issue: TViewListIssue }) {
  const { issue } = props;
  const { getProjectIdentifierById } = useProject();
  const identifier = getProjectIdentifierById(issue.project_id);

  return (
    <tr className="border-b border-subtle last:border-b-0">
      <td className="whitespace-nowrap py-2 pr-3 text-xs text-tertiary">
        {identifier ? `${identifier}-${issue.sequence_id}` : `#${issue.sequence_id}`}
      </td>
      <td className="max-w-0 py-2 pr-3">
        <span className="block truncate text-sm text-primary" title={issue.name}>
          {issue.name}
        </span>
      </td>
      <td className="py-2 pr-3">
        {issue.state__group && (
          <span className="flex items-center gap-1.5 text-xs text-secondary">
            <StateGroupIcon stateGroup={issue.state__group as TStateGroups} className="size-3.5 flex-shrink-0" />
            <span className="capitalize">{issue.state__group}</span>
          </span>
        )}
      </td>
      <td className="py-2">
        <span className="flex items-center gap-1.5 text-xs text-secondary">
          <PriorityIcon priority={issue.priority as TIssuePriorities} size={14} withContainer />
          <span className="capitalize">{issue.priority}</span>
        </span>
      </td>
    </tr>
  );
});

export const ViewIssuesTableWidget = observer(function ViewIssuesTableWidget(props: Props) {
  const { widget, workspaceSlug, adminActions } = props;
  const perPage = widget.config?.page_size ?? 10;

  // Cursor-driven pagination — matches the `cycle.service.ts#workspaceActiveCycles`
  // convention (BasePaginator `cursor`/`per_page`). `undefined` cursor => first page.
  const [cursor, setCursor] = useState<string | undefined>(undefined);

  const { data, error, isLoading, mutate } = useSWR(
    ["dashboard-widget-issues", widget.id, cursor, perPage],
    () => customDashboardsService.getIssues(workspaceSlug, widget.id, { cursor, per_page: perPage })
  );

  const viewDeleted = isViewDeleted(data);
  const envelope = !viewDeleted ? data : undefined;
  const results = envelope?.results ?? [];

  return (
    <WidgetCard
      widget={widget}
      adminActions={adminActions}
      isLoading={isLoading}
      isError={!!error}
      isEmpty={!isLoading && !error && !viewDeleted && results.length === 0}
      emptyMessage="No issues match this saved view."
      onRetry={() => mutate()}
    >
      {viewDeleted ? (
        <div className="flex flex-1 items-center justify-center text-center">
          <p className="text-sm text-tertiary">The saved view for this widget was deleted.</p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col">
          <div className="flex-1 overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-subtle text-left text-11 uppercase tracking-wide text-tertiary">
                  <th className="py-2 pr-3 font-medium">ID</th>
                  <th className="py-2 pr-3 font-medium">Title</th>
                  <th className="py-2 pr-3 font-medium">State</th>
                  <th className="py-2 font-medium">Priority</th>
                </tr>
              </thead>
              <tbody>
                {results.map((issue) => (
                  <IssueRow key={issue.id} issue={issue} />
                ))}
              </tbody>
            </table>
          </div>

          {envelope && (envelope.prev_page_results || envelope.next_page_results) && (
            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                type="button"
                disabled={!envelope.prev_page_results}
                onClick={() => setCursor(envelope.prev_cursor)}
                className={cn(
                  "flex items-center gap-1 rounded border border-subtle px-2 py-1 text-xs text-secondary hover:bg-layer-2",
                  !envelope.prev_page_results && "cursor-not-allowed opacity-50 hover:bg-transparent"
                )}
              >
                <ChevronLeft className="size-3.5" />
                Prev
              </button>
              <button
                type="button"
                disabled={!envelope.next_page_results}
                onClick={() => setCursor(envelope.next_cursor)}
                className={cn(
                  "flex items-center gap-1 rounded border border-subtle px-2 py-1 text-xs text-secondary hover:bg-layer-2",
                  !envelope.next_page_results && "cursor-not-allowed opacity-50 hover:bg-transparent"
                )}
              >
                Next
                <ChevronRight className="size-3.5" />
              </button>
            </div>
          )}
        </div>
      )}
    </WidgetCard>
  );
});
