/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking service rethrows `err.response.data`, so a caught value is the API error payload.
export type TApiError = { error?: string; name?: string[] };

export const asApiError = (err: unknown): TApiError => (typeof err === "object" && err !== null ? err : {});
