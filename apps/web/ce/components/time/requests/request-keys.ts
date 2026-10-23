/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — SWR keys for the requests / approvals data, and one call that refreshes them all.
import { mutate as globalMutate } from "swr";

export const MY_REQUESTS_KEY = (slug: string) => `TIME_REQUESTS_MINE_${slug}`;
export const PENDING_COUNT_KEY = (slug: string) => `TIME_REQUESTS_PENDING_COUNT_${slug}`;
export const APPROVALS_KEY = (slug: string) => `TIME_APPROVALS_${slug}`;
/** pending requests targeting entries of one work item (the "Change pending" badge) */
export const ISSUE_PENDING_KEY = (slug: string, issueId: string) => `TIME_REQUESTS_ISSUE_${slug}_${issueId}`;

/** Does this SWR key belong to the requests / approvals data of workspace `slug`? */
export const isRequestKey = (key: unknown, slug: string): boolean =>
  typeof key === "string" &&
  key.includes(`_${slug}`) &&
  (key.startsWith("TIME_REQUESTS_") || key.startsWith("TIME_APPROVALS_"));

/** Refresh every request list, the Approvals badge and the work item badges after any change. */
export const refreshRequestData = async (slug: string): Promise<void> => {
  await globalMutate((key) => isRequestKey(key, slug));
};
