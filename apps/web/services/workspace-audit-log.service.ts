/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- read-only workspace audit log (Super Admins only).

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

export type TWorkspaceAuditLogSource = "workspace" | "time_tracking";

export type TWorkspaceAuditLogEntry = {
  id: string;
  source: TWorkspaceAuditLogSource;
  created_at: string;
  actor: { id: string | null; name: string; email: string } | null;
  action: string;
  action_label: string;
  target_type: string;
  target_id: string | null;
  target_label: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

export type TWorkspaceAuditLogPage = {
  results: TWorkspaceAuditLogEntry[];
  next_cursor: string | null;
};

export class WorkspaceAuditLogService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async list(
    workspaceSlug: string,
    params: { cursor?: string | null; source?: TWorkspaceAuditLogSource | null; limit?: number } = {}
  ): Promise<TWorkspaceAuditLogPage> {
    const query: Record<string, string | number> = { limit: params.limit ?? 50 };
    if (params.cursor) query.cursor = params.cursor;
    if (params.source) query.source = params.source;
    return this.get(`/api/workspaces/${workspaceSlug}/audit-log/`, { params: query })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
