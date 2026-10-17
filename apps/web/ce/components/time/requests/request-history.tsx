/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — a request's append-only history (submitted, approved or rejected, applied...),
// loaded when the row's "History" is opened. Times are in the workspace time zone.
import useSWR from "swr";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { eventLine } from "./request-utils";

type TProps = { workspaceSlug: string; requestId: string; timeZone: string };

export function RequestHistory({ workspaceSlug, requestId, timeZone }: TProps) {
  const { data, error, isLoading } = useSWR(`TIME_REQUESTS_HISTORY_${workspaceSlug}_${requestId}`, () =>
    timeTrackingService.getTimeRequestHistory(workspaceSlug, requestId)
  );
  if (isLoading && !data) return <div className="text-xs text-tertiary">Loading history...</div>;
  if (error || !data) return <div className="text-xs text-tertiary">History isn&apos;t available.</div>;
  return (
    <ol className="flex flex-col gap-1" data-testid="request-history">
      {data.events.map((event) => (
        <li
          key={event.id}
          className="text-xs text-tertiary"
          data-testid="request-history-event"
          data-action={event.action}
        >
          <span className="text-secondary">{eventLine(event, timeZone)}</span>
          {event.note ? <span>{`: ${event.note}`}</span> : null}
        </li>
      ))}
    </ol>
  );
}
