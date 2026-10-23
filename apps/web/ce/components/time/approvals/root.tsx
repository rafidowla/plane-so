/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — the Approvals tab (workspace admins and project admins): Pending, Reviewed and
// History views over the requests the signed-in reviewer may act on.
import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { cn } from "@plane/utils";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTimeRequest } from "../../issues/worklog/types";
import { APPROVALS_KEY } from "../requests/request-keys";
import { RequestRow } from "../requests/request-row";
import { safeTimeZone, type TReviewMode } from "../requests/request-utils";
import { EMPTY_FILTERS, ApprovalFilters, filterOptions, withinDates, type TApprovalFilters } from "./filters";
import { ApprovalHistory } from "./history";
import { ReviewDialog } from "./review-dialog";

export type TApprovalView = "pending" | "reviewed" | "history";

const VIEWS: { key: TApprovalView; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "reviewed", label: "Reviewed" },
  { key: "history", label: "History" },
];

const PAGE = 50;
const MAX = 200;
const REVIEWED_STATUSES = "approved,rejected,withdrawn";

export const ApprovalsTab = observer(function ApprovalsTab() {
  const { currentWorkspace } = useWorkspace();
  const { getProjectById } = useProject();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const timeZone = safeTimeZone(currentWorkspace?.timezone);

  const [view, setView] = useState<TApprovalView>("pending");
  const [filters, setFilters] = useState<TApprovalFilters>(EMPTY_FILTERS);
  const [limit, setLimit] = useState(PAGE);
  const [review, setReview] = useState<{ request: TTimeRequest; mode: TReviewMode } | null>(null);

  // One unfiltered read feeds the dropdown choices and the History view's work item names.
  const { data: everything } = useSWR(
    workspaceSlug ? `${APPROVALS_KEY(workspaceSlug)}_options` : null,
    () => timeTrackingService.getTimeRequests(workspaceSlug, { scope: "review", limit: MAX }),
    { keepPreviousData: true }
  );
  const options = useMemo(() => filterOptions(everything?.results ?? []), [everything]);
  const requestsById = useMemo(() => new Map((everything?.results ?? []).map((r) => [r.id, r])), [everything]);

  const listKey = `${APPROVALS_KEY(workspaceSlug)}_${view}_${filters.userId}_${filters.projectId}_${filters.type}_${limit}`;
  const { data, isLoading } = useSWR(
    workspaceSlug && view !== "history" ? listKey : null,
    () =>
      timeTrackingService.getTimeRequests(workspaceSlug, {
        scope: "review",
        status: view === "pending" ? "pending" : REVIEWED_STATUSES,
        type: filters.type || undefined,
        user_id: filters.userId || undefined,
        project_id: filters.projectId || undefined,
        limit,
      }),
    { keepPreviousData: true }
  );
  const rows = withinDates(data?.results ?? [], filters.from, filters.to);

  if (!workspaceSlug) return null;

  const changeView = (next: TApprovalView) => {
    setView(next);
    setLimit(PAGE);
  };
  const changeFilters = (next: TApprovalFilters) => {
    setFilters(next);
    setLimit(PAGE);
  };

  return (
    <div className="flex flex-col gap-3 px-4 py-4" data-testid="approvals">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-16 font-semibold text-primary">Approvals</h2>
        <div role="tablist" aria-label="Approvals view" className="flex items-center gap-1 rounded-md bg-layer-1 p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              type="button"
              role="tab"
              aria-selected={view === v.key}
              data-testid={`approvals-view-${v.key}`}
              onClick={() => changeView(v.key)}
              className={cn("text-sm rounded px-3 py-1 text-secondary", {
                "bg-surface-1 font-medium text-primary shadow-raised-100": view === v.key,
              })}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>

      <ApprovalFilters
        value={filters}
        onChange={changeFilters}
        people={options.people}
        projects={options.projects.map((p) => ({
          id: p.id,
          label: getProjectById(p.id)?.name || p.identifier || "Project",
        }))}
      />

      {view === "history" ? (
        <ApprovalHistory
          workspaceSlug={workspaceSlug}
          timeZone={timeZone}
          filters={filters}
          requestsById={requestsById}
        />
      ) : isLoading && !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="88px" />
          <Loader.Item height="88px" />
        </Loader>
      ) : rows.length === 0 ? (
        <div className="text-sm py-12 text-center text-tertiary" data-testid="approvals-empty">
          {view === "pending" ? "Nothing is waiting for your review." : "No reviewed requests for these filters."}
        </div>
      ) : (
        <div className="rounded-lg border border-subtle bg-surface-1" data-testid="approvals-list">
          {rows.map((request) => (
            <RequestRow
              key={request.id}
              request={request}
              workspaceSlug={workspaceSlug}
              timeZone={timeZone}
              showRequester
              actions={
                view === "pending" && request.can_approve ? (
                  <>
                    <Button
                      variant="primary"
                      size="sm"
                      stretch="auto"
                      label={request.type === "auto_stop_review" ? "Confirm" : "Approve"}
                      data-testid="request-approve"
                      onClick={() => setReview({ request, mode: "approve" })}
                    />
                    {request.type === "auto_stop_review" && (
                      <Button
                        variant="secondary"
                        size="sm"
                        stretch="auto"
                        label="Confirm with end time"
                        data-testid="request-confirm-end"
                        onClick={() => setReview({ request, mode: "confirm_end" })}
                      />
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      stretch="auto"
                      label="Reject"
                      data-testid="request-reject"
                      onClick={() => setReview({ request, mode: "reject" })}
                    />
                  </>
                ) : undefined
              }
            />
          ))}
        </div>
      )}

      {view !== "history" && data && data.count > (data.results?.length ?? 0) && limit < MAX && (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label="Show more"
            onClick={() => setLimit((l) => Math.min(MAX, l + PAGE))}
          />
        </div>
      )}

      <ReviewDialog
        request={review?.request ?? null}
        mode={review?.mode ?? "approve"}
        workspaceSlug={workspaceSlug}
        timeZone={timeZone}
        onClose={() => setReview(null)}
        onSwitchMode={(mode) => setReview((r) => (r ? { ...r, mode } : r))}
      />
    </div>
  );
});
