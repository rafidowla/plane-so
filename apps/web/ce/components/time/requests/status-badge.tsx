/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — small status / type pills for request rows.
import { cn } from "@plane/utils";
import type { TTimeRequestStatus, TTimeRequestType } from "../../issues/worklog/types";
import { REQUEST_STATUS_LABEL, REQUEST_TYPE_LABEL } from "./request-utils";

const STATUS_CLASS: Record<TTimeRequestStatus, string> = {
  pending: "bg-warning-subtle text-warning-primary",
  approved: "bg-success-subtle text-success-primary",
  rejected: "bg-danger-subtle text-danger-primary",
  withdrawn: "bg-layer-2 text-tertiary",
};

export function StatusBadge({ status }: { status: TTimeRequestStatus }) {
  return (
    <span
      data-testid="request-status"
      data-status={status}
      className={cn("text-xs shrink-0 rounded-full px-2 py-0.5 font-medium", STATUS_CLASS[status])}
    >
      {REQUEST_STATUS_LABEL[status]}
    </span>
  );
}

export function TypeBadge({ type }: { type: TTimeRequestType }) {
  return (
    <span data-testid="request-type" className="text-xs shrink-0 rounded-full bg-layer-2 px-2 py-0.5 text-secondary">
      {REQUEST_TYPE_LABEL[type]}
    </span>
  );
}
