/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- workspace project categories (admin-managed, any member can read).

import { API_BASE_URL } from "@plane/constants";
import type { TProjectCategory } from "@plane/types";
import { APIService } from "@/services/api.service";

export type TProjectCategoryPayload = { name?: string; sort_order?: number };

/**
 * Rejects with the server's response body: `{ error: string }` for a duplicate name,
 * `{ name: string[] }` for field errors, or `{ error | detail }` for 403 and friends.
 */
export class ProjectCategoryService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async list(workspaceSlug: string): Promise<TProjectCategory[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/project-categories/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async create(workspaceSlug: string, data: TProjectCategoryPayload): Promise<TProjectCategory> {
    return this.post(`/api/workspaces/${workspaceSlug}/project-categories/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async update(workspaceSlug: string, categoryId: string, data: TProjectCategoryPayload): Promise<TProjectCategory> {
    return this.patch(`/api/workspaces/${workspaceSlug}/project-categories/${categoryId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async remove(workspaceSlug: string, categoryId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/project-categories/${categoryId}/`)
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
