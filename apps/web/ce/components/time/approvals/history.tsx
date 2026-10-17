/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — Approvals > History: every submit / review / apply on requests you can review,
// newest first. The events endpoint carries no work item names, so rows borrow them from the request list.
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTimeRequest } from "../../issues/worklog/types";
import { APPROVALS_KEY } from "../requests/request-keys";
import { REQUEST_TYPE_LABEL, eventLine, issueRef, matchesType } from "../requests/request-utils";
import type { TApprovalFilters } from "./filters";

const PAGE = 50;
const MAX = 200;

type TProps = {
  workspaceSlug: string;
  timeZone: string;
  filters: TApprovalFilters;
  /** requests by id, for names; may be missing old ones */
  requestsById: Map<string, TTimeRequest>;
};

export function ApprovalHistory({ workspaceSlug, timeZone, filters, requestsById }: TProps) {
  const [limit, setLimit] = useState(PAGE);
  const { data, isLoading } = useSWR(
    `${APPROVALS_KEY(workspaceSlug)}_history_${filters.userId}_${filters.projectId}_${filters.from}_${filters.to}_${limit}`,
    () =>
      timeTrackingService.getTimeRequestEvents(workspaceSlug, {
        scope: "review",
        user_id: filters.userId || undefined,
        project_id: filters.projectId || undefined,
        start_date: filters.from || undefined,
        end_date: filters.to || undefined,
        limit,
      }),
    { keepPreviousData: true }
  );
  const rows = (data?.results ?? []).filter((e) => matchesType(e, filters.type));

  if (isLoading && !data) {
    return (
      <Loader className="space-y-3">
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
      </Loader>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="text-sm py-12 text-center text-tertiary" data-testid="approvals-empty">
        No history for these filters.
      </div>
    );
  }
  return (
    <>
      <div className="rounded-lg border border-subtle bg-surface-1" data-testid="approvals-history">
        {rows.map((event) => {
          const request = requestsById.get(event.request_id);
          const type = event.request?.type;
          return (
            <div
              key={event.id}
              data-testid="history-row"
              data-action={event.action}
              className="flex flex-col gap-0.5 border-b border-subtle px-4 py-3 last:border-b-0"
            >
              <div className="text-sm text-primary">
                {request?.requester?.display_name ?? "A team member"}
                <span className="text-tertiary"> · {type ? REQUEST_TYPE_LABEL[type] : "Request"}</span>
                {request && issueRef(request) ? (
                  <span className="text-tertiary">
                    {" "}
                    · {issueRef(request)} {request.issue.name}
                  </span>
                ) : null}
              </div>
              <div className="text-xs text-secondary">{eventLine(event, timeZone)}</div>
              {event.note && <div className="text-xs text-tertiary">&ldquo;{event.note}&rdquo;</div>}
            </div>
          );
        })}
      </div>
      {data && data.count > (data.results?.length ?? 0) && limit < MAX && (
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
    </>
  );
}
