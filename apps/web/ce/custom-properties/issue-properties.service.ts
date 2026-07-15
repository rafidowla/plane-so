/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";
import type {
  TIssueProperty,
  TIssuePropertyOption,
  TProjectPropertiesFeature,
  TPropertyValuesMap,
} from "./types";

/**
 * Client for the fork's plane.properties endpoints. Mirrors the sibling
 * ce/services/time-tracking.service.ts pattern exactly. Routes match
 * apps/api/plane/properties/urls.py (all mounted under /api/).
 */
export class IssuePropertiesService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  // ---- Feature toggle ----
  async getFeature(workspaceSlug: string, projectId: string): Promise<TProjectPropertiesFeature> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/properties-feature/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async setFeature(workspaceSlug: string, projectId: string, is_enabled: boolean): Promise<TProjectPropertiesFeature> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/properties-feature/`, { is_enabled })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Property definitions ----
  async getProperties(workspaceSlug: string, projectId: string): Promise<TIssueProperty[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createProperty(
    workspaceSlug: string,
    projectId: string,
    data: Partial<TIssueProperty> & { options?: Partial<TIssuePropertyOption>[] }
  ): Promise<TIssueProperty> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/`, data)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateProperty(
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssueProperty>
  ): Promise<TIssueProperty> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/`,
      data
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteProperty(workspaceSlug: string, projectId: string, propertyId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Options ----
  async getOptions(workspaceSlug: string, projectId: string, propertyId: string): Promise<TIssuePropertyOption[]> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/options/`
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async createOption(
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssuePropertyOption>
  ): Promise<TIssuePropertyOption> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/options/`,
      data
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async updateOption(
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    optionId: string,
    data: Partial<TIssuePropertyOption>
  ): Promise<TIssuePropertyOption> {
    return this.patch(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/options/${optionId}/`,
      data
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async deleteOption(
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    optionId: string
  ): Promise<void> {
    return this.delete(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-properties/${propertyId}/options/${optionId}/`
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Values (per issue + property, replace semantics) ----
  async getValues(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    propertyId: string
  ): Promise<{ values: string[] }> {
    return this.get(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-items/${issueId}/work-item-properties/${propertyId}/values/`
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  async setValues(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    propertyId: string,
    values: string[]
  ): Promise<{ values: string[] }> {
    return this.post(
      `/api/workspaces/${workspaceSlug}/projects/${projectId}/work-items/${issueId}/work-item-properties/${propertyId}/values/`,
      { values }
    )
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }

  // ---- Bulk value read (board/spreadsheet hydration) ----
  async getBulkValues(workspaceSlug: string, projectId: string, ids: string[]): Promise<TPropertyValuesMap> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/work-item-property-values/`, {
      params: { work_item_ids: ids.join(",") },
    })
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}

export const issuePropertiesService = new IssuePropertiesService();
