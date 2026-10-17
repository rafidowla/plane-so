/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — one request as a card: who, what changes, why, and how it was decided.
// Used by My requests and by the Approvals queue (which passes its buttons as `actions`).
import { useState, type ReactNode } from "react";
import Link from "next/link";
import { Avatar } from "@makeplane/propel/components/avatar";
import { generateWorkItemLink, getFileURL } from "@plane/utils";
import type { TTimeRequest } from "../../issues/worklog/types";
import { RequestHistory } from "./request-history";
import { StatusBadge, TypeBadge } from "./status-badge";
import { describeChange, formatZonedDateTime, issueRef } from "./request-utils";

type TProps = {
  request: TTimeRequest;
  workspaceSlug: string;
  timeZone: string;
  /** show who asked (the approvals views); My requests are all yours */
  showRequester?: boolean;
  actions?: ReactNode;
};

export function RequestRow({ request, workspaceSlug, timeZone, showRequester = false, actions }: TProps) {
  const ref = issueRef(request);
  const href = ref
    ? generateWorkItemLink({
        workspaceSlug,
        projectId: request.project_id,
        issueId: request.issue_id,
        projectIdentifier: request.issue.identifier,
        sequenceId: request.issue.sequence_id,
      })
    : null;
  const [showHistory, setShowHistory] = useState(false);
  const decided = request.status !== "pending" && request.reviewed_at;

  return (
    <div
      data-testid="request-row"
      data-request-id={request.id}
      data-type={request.type}
      data-status={request.status}
      className="flex flex-col gap-2 border-b border-subtle px-4 py-3 last:border-b-0"
    >
      <div className="flex flex-wrap items-center gap-2">
        {showRequester && request.requester && (
          <span className="flex items-center gap-2" data-testid="request-requester">
            <Avatar alt={request.requester.display_name} src={getFileURL(request.requester.avatar_url ?? "")} />
            <span className="text-sm font-medium text-primary">{request.requester.display_name}</span>
          </span>
        )}
        <TypeBadge type={request.type} />
        <StatusBadge status={request.status} />
        {request.route === "workspace_admins" && request.status === "pending" && (
          <span className="text-xs text-tertiary">Workspace admin approves</span>
        )}
        <span className="text-xs ml-auto text-tertiary">{formatZonedDateTime(request.created_at, timeZone)}</span>
      </div>

      <div className="text-sm flex min-w-0 flex-wrap items-center gap-x-2">
        {href ? (
          <Link href={href} className="flex min-w-0 items-center gap-2 hover:underline">
            <span className="shrink-0 font-medium text-secondary">{ref}</span>
            <span className="min-w-0 truncate text-primary">{request.issue.name}</span>
          </Link>
        ) : (
          <span className="text-tertiary">Work item no longer available</span>
        )}
      </div>

      <div className="text-sm text-secondary" data-testid="request-change">
        {describeChange(request, timeZone)}
      </div>
      {request.reason && (
        <div className="text-sm text-tertiary" data-testid="request-reason-text">
          &ldquo;{request.reason}&rdquo;
        </div>
      )}
      {decided && (
        <div className="text-xs text-tertiary" data-testid="request-decision">
          {request.status === "withdrawn" ? "Withdrawn" : request.status === "approved" ? "Approved" : "Rejected"}
          {request.reviewer ? ` by ${request.reviewer.display_name}` : ""}
          {request.review_note ? `: ${request.review_note}` : ""}
        </div>
      )}
      {showHistory && <RequestHistory workspaceSlug={workspaceSlug} requestId={request.id} timeZone={timeZone} />}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {actions}
        <button
          type="button"
          data-testid="request-history-toggle"
          aria-expanded={showHistory}
          onClick={() => setShowHistory((v) => !v)}
          className="text-xs text-tertiary hover:text-primary hover:underline"
        >
          {showHistory ? "Hide history" : "History"}
        </button>
      </div>
    </div>
  );
}
