/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6 — "Add an estimate first". The server answers 400 `estimate_required` when a
// project that requires estimates sees a state move into Started/Completed, a timer start, a manual entry
// or a missing-time request on an unestimated item. Those calls live all over the app, so they just call
// `notifyEstimateRequired`; the host (toast) and the work item's estimate field (opens itself) listen.
import type { TTimeEstimateApiError } from "../types";

export type TEstimateRequiredDetail = {
  issueId?: string | null;
  workspaceSlug?: string | null;
  projectId?: string | null;
  /** "create": refused while creating the item, so there is no work item to set the estimate on yet. */
  context?: "create";
};

type TListener = (detail: TEstimateRequiredDetail) => void;

const listeners = new Set<TListener>();
const mountedFields = new Map<string, number>();

/** The API payload (or anything thrown by the services, which rethrow it) says "estimate required". */
export function isEstimateRequiredError(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as TTimeEstimateApiError).code === "estimate_required";
}

export function subscribeEstimateRequired(listener: TListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function notifyEstimateRequired(detail: TEstimateRequiredDetail): void {
  listeners.forEach((listener) => listener(detail));
}

/** Convenience for catch blocks: returns true (and notifies) when `err` is estimate_required. */
export function handleEstimateRequired(err: unknown, detail: TEstimateRequiredDetail): boolean {
  if (!isEstimateRequiredError(err)) return false;
  // The payload names the item for state changes where the caller may only know the id from the request.
  const issueId = detail.issueId ?? (err as TTimeEstimateApiError).issue_id ?? null;
  notifyEstimateRequired({ ...detail, issueId });
  return true;
}

/** An estimate field for this work item is on screen (it can take focus instead of linking away). */
export function registerEstimateField(issueId: string): () => void {
  mountedFields.set(issueId, (mountedFields.get(issueId) ?? 0) + 1);
  return () => {
    const next = (mountedFields.get(issueId) ?? 1) - 1;
    if (next <= 0) mountedFields.delete(issueId);
    else mountedFields.set(issueId, next);
  };
}

export const isEstimateFieldMounted = (issueId: string | null | undefined): boolean =>
  Boolean(issueId) && mountedFields.has(issueId as string);

export const ESTIMATE_REQUIRED_TITLE = "Add an estimate first";
export const ESTIMATE_REQUIRED_MESSAGE =
  "This project needs an estimate before work starts. Set one on the work item, then try again.";
// FORK: time-tracking-v2 — the create modal has no work item yet, so point at the create-then-move route.
export const ESTIMATE_REQUIRED_CREATE_MESSAGE =
  "This project needs an estimate before work starts. Create the item in a not-started state, add an estimate, then move it.";
