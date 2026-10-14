/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) / draft-project-change (PSR-92) — fork-owned service, see docs/move-work-item-design.md §5
import { API_BASE_URL } from "@plane/constants";
import type {
  TDraftProjectMapping,
  TDraftProjectMappingPayload,
  TMovePayload,
  TMovePlan,
  TMoveResult,
  TMoveSubItemsMode,
} from "@plane/utils";
import { APIService } from "@/services/api.service";

/** Shape of a 4xx from the move endpoints: `{ error, code }` (codes in the design doc, e.g. `SAME_PROJECT`). */
export type TWorkItemMoveError = { error?: string; code?: string };

export class WorkItemMoveService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /** Dry-run: what would happen to every project-scoped field if the item (and sub-items) moved to the target. */
  async getMovePreview(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    targetProjectId: string,
    subItems: TMoveSubItemsMode
  ): Promise<TMovePlan> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-items/${issueId}/move/preview/`, {
      params: { target_project_id: targetProjectId, sub_items: subItems },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async moveWorkItem(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    payload: TMovePayload
  ): Promise<TMoveResult> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-items/${issueId}/move/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  /** Re-maps a draft's project-scoped form values (state, labels, assignees, ...) onto another project. */
  async getDraftProjectMapping(
    workspaceSlug: string,
    payload: TDraftProjectMappingPayload
  ): Promise<TDraftProjectMapping> {
    return this.post(`/api/workspaces/${workspaceSlug}/draft-issues/project-mapping/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }
}

export const workItemMoveService = new WorkItemMoveService();
