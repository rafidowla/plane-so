/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — pure role check behind the Approvals tab (kept apart from the hooks so it is unit-testable).
import { EUserPermissions } from "@plane/constants";

/** True when `roles` (project id -> role) holds at least one project the user administers. */
export const hasAdminProject = (roles: Record<string, unknown> | undefined): boolean =>
  Object.values(roles ?? {}).some((role) => role === EUserPermissions.ADMIN);
