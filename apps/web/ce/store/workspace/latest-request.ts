/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- sequence guard for optimistic updates: when calls overlap, only the latest one
// per key may roll back on failure, so an older failing call cannot restore a stale state.

export type TLatestRequestTracker = {
  /** register a new call for `key` and return its id */
  begin: (key: string) => number;
  /** true while no newer call for `key` has begun */
  isLatest: (key: string, id: number) => boolean;
};

export const createLatestRequestTracker = (): TLatestRequestTracker => {
  const latest: Record<string, number> = {};
  return {
    begin: (key) => {
      latest[key] = (latest[key] ?? 0) + 1;
      return latest[key];
    },
    isLatest: (key, id) => latest[key] === id,
  };
};
