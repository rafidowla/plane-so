/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set, sortBy } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";

import { issuePropertiesService } from "@/plane-web/custom-properties/issue-properties.service";
import type {
  TIssueProperty,
  TIssuePropertyOption,
  TProjectPropertiesFeature,
} from "@/plane-web/custom-properties";
import type { RootStore } from "@/plane-web/store/root.store";

// ---------------------------------------------------------------------------
// Custom properties (definitions + options + per-project feature toggle)
// ---------------------------------------------------------------------------

export interface ICustomPropertiesStore {
  propertyMap: Record<string, TIssueProperty>;
  featureMap: Record<string, boolean>;
  fetchedMap: Record<string, boolean>;
  // reads
  getProjectProperties: (projectId: string | null | undefined) => TIssueProperty[];
  getPropertyById: (propertyId: string) => TIssueProperty | null;
  isFeatureEnabled: (projectId: string | null | undefined) => boolean;
  isFetched: (projectId: string | null | undefined) => boolean;
  // feature
  fetchFeature: (workspaceSlug: string, projectId: string) => Promise<TProjectPropertiesFeature>;
  toggleFeature: (workspaceSlug: string, projectId: string, isEnabled: boolean) => Promise<TProjectPropertiesFeature>;
  // property CRUD
  fetchProjectProperties: (workspaceSlug: string, projectId: string) => Promise<TIssueProperty[]>;
  createProperty: (
    workspaceSlug: string,
    projectId: string,
    data: Omit<Partial<TIssueProperty>, "options"> & { options?: Partial<TIssuePropertyOption>[] }
  ) => Promise<TIssueProperty>;
  updateProperty: (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssueProperty>
  ) => Promise<TIssueProperty>;
  deleteProperty: (workspaceSlug: string, projectId: string, propertyId: string) => Promise<void>;
  // option CRUD (mutates the embedded options of the parent property)
  createOption: (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssuePropertyOption>
  ) => Promise<TIssuePropertyOption>;
  updateOption: (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    optionId: string,
    data: Partial<TIssuePropertyOption>
  ) => Promise<TIssuePropertyOption>;
  deleteOption: (workspaceSlug: string, projectId: string, propertyId: string, optionId: string) => Promise<void>;
}

export class CustomPropertiesStore implements ICustomPropertiesStore {
  propertyMap: Record<string, TIssueProperty> = {};
  featureMap: Record<string, boolean> = {};
  fetchedMap: Record<string, boolean> = {};
  rootStore: RootStore;

  constructor(rootStore: RootStore) {
    makeObservable(this, {
      propertyMap: observable,
      featureMap: observable,
      fetchedMap: observable,
      fetchFeature: action,
      toggleFeature: action,
      fetchProjectProperties: action,
      createProperty: action,
      updateProperty: action,
      deleteProperty: action,
      createOption: action,
      updateOption: action,
      deleteOption: action,
    });
    this.rootStore = rootStore;
  }

  getProjectProperties = computedFn((projectId: string | null | undefined): TIssueProperty[] => {
    if (!projectId) return [];
    return sortBy(
      Object.values(this.propertyMap).filter((p) => p?.project === projectId && p?.is_active),
      "sort_order"
    );
  });

  getPropertyById = computedFn((propertyId: string): TIssueProperty | null => this.propertyMap?.[propertyId] || null);

  isFeatureEnabled = computedFn((projectId: string | null | undefined): boolean =>
    projectId ? this.featureMap[projectId] === true : false
  );

  isFetched = computedFn((projectId: string | null | undefined): boolean =>
    projectId ? this.fetchedMap[projectId] === true : false
  );

  fetchFeature = async (workspaceSlug: string, projectId: string): Promise<TProjectPropertiesFeature> => {
    const res = await issuePropertiesService.getFeature(workspaceSlug, projectId);
    runInAction(() => set(this.featureMap, [projectId], !!res?.is_enabled));
    return res;
  };

  toggleFeature = async (
    workspaceSlug: string,
    projectId: string,
    isEnabled: boolean
  ): Promise<TProjectPropertiesFeature> => {
    const res = await issuePropertiesService.setFeature(workspaceSlug, projectId, isEnabled);
    runInAction(() => set(this.featureMap, [projectId], !!res?.is_enabled));
    return res;
  };

  fetchProjectProperties = async (workspaceSlug: string, projectId: string): Promise<TIssueProperty[]> => {
    const res = await issuePropertiesService.getProperties(workspaceSlug, projectId);
    runInAction(() => {
      (res || []).forEach((property) => set(this.propertyMap, [property.id], property));
      set(this.fetchedMap, [projectId], true);
    });
    return res;
  };

  createProperty = async (
    workspaceSlug: string,
    projectId: string,
    data: Omit<Partial<TIssueProperty>, "options"> & { options?: Partial<TIssuePropertyOption>[] }
  ): Promise<TIssueProperty> => {
    const res = await issuePropertiesService.createProperty(workspaceSlug, projectId, data);
    runInAction(() => set(this.propertyMap, [res.id], res));
    return res;
  };

  updateProperty = async (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssueProperty>
  ): Promise<TIssueProperty> => {
    const original = this.propertyMap[propertyId];
    runInAction(() => set(this.propertyMap, [propertyId], { ...original, ...data }));
    try {
      const res = await issuePropertiesService.updateProperty(workspaceSlug, projectId, propertyId, data);
      runInAction(() => set(this.propertyMap, [propertyId], res));
      return res;
    } catch (error) {
      runInAction(() => set(this.propertyMap, [propertyId], original));
      throw error;
    }
  };

  deleteProperty = async (workspaceSlug: string, projectId: string, propertyId: string): Promise<void> => {
    await issuePropertiesService.deleteProperty(workspaceSlug, projectId, propertyId);
    runInAction(() => {
      delete this.propertyMap[propertyId];
    });
  };

  createOption = async (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    data: Partial<TIssuePropertyOption>
  ): Promise<TIssuePropertyOption> => {
    const res = await issuePropertiesService.createOption(workspaceSlug, projectId, propertyId, data);
    runInAction(() => {
      const property = this.propertyMap[propertyId];
      if (property) {
        set(this.propertyMap, [propertyId], { ...property, options: [...(property.options ?? []), res] });
      }
    });
    return res;
  };

  updateOption = async (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    optionId: string,
    data: Partial<TIssuePropertyOption>
  ): Promise<TIssuePropertyOption> => {
    const res = await issuePropertiesService.updateOption(workspaceSlug, projectId, propertyId, optionId, data);
    runInAction(() => {
      const property = this.propertyMap[propertyId];
      if (property) {
        const options = (property.options ?? []).map((o) => (o.id === optionId ? res : o));
        set(this.propertyMap, [propertyId], { ...property, options });
      }
    });
    return res;
  };

  deleteOption = async (
    workspaceSlug: string,
    projectId: string,
    propertyId: string,
    optionId: string
  ): Promise<void> => {
    await issuePropertiesService.deleteOption(workspaceSlug, projectId, propertyId, optionId);
    runInAction(() => {
      const property = this.propertyMap[propertyId];
      if (property) {
        const options = (property.options ?? []).filter((o) => o.id !== optionId);
        set(this.propertyMap, [propertyId], { ...property, options });
      }
    });
  };
}

// ---------------------------------------------------------------------------
// Per-issue property values (with a debounced bulk-fetch queue for the board)
// ---------------------------------------------------------------------------

const BULK_CAP = 100;
const FLUSH_DELAY_MS = 60;

export interface IPropertyValuesStore {
  valuesMap: Record<string, Record<string, string[]>>;
  getIssueValues: (issueId: string) => Record<string, string[]>;
  getValue: (issueId: string, propertyId: string) => string[];
  enqueueValueFetch: (workspaceSlug: string, projectId: string, issueId: string) => void;
  fetchBulkValues: (workspaceSlug: string, projectId: string, issueIds: string[]) => Promise<void>;
  setValue: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    propertyId: string,
    values: string[]
  ) => Promise<string[]>;
}

export class PropertyValuesStore implements IPropertyValuesStore {
  valuesMap: Record<string, Record<string, string[]>> = {};
  rootStore: RootStore;
  // non-observable batch state
  private requested = new Set<string>();
  private pendingIds = new Set<string>();
  private pendingContext: { workspaceSlug: string; projectId: string } | null = null;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(rootStore: RootStore) {
    makeObservable(this, {
      valuesMap: observable,
      fetchBulkValues: action,
      setValue: action,
    });
    this.rootStore = rootStore;
  }

  getIssueValues = computedFn((issueId: string): Record<string, string[]> => this.valuesMap[issueId] ?? {});

  getValue = computedFn((issueId: string, propertyId: string): string[] => this.valuesMap[issueId]?.[propertyId] ?? []);

  enqueueValueFetch = (workspaceSlug: string, projectId: string, issueId: string): void => {
    if (this.requested.has(issueId)) return;
    this.pendingIds.add(issueId);
    this.pendingContext = { workspaceSlug, projectId };
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.flushTimer = setTimeout(() => {
      void this.flush();
    }, FLUSH_DELAY_MS);
  };

  private flush = async (): Promise<void> => {
    const context = this.pendingContext;
    const ids = Array.from(this.pendingIds);
    this.pendingIds.clear();
    this.flushTimer = null;
    if (!context || ids.length === 0) return;
    for (let i = 0; i < ids.length; i += BULK_CAP) {
      await this.fetchBulkValues(context.workspaceSlug, context.projectId, ids.slice(i, i + BULK_CAP));
    }
  };

  fetchBulkValues = async (workspaceSlug: string, projectId: string, issueIds: string[]): Promise<void> => {
    if (issueIds.length === 0) return;
    const res = await issuePropertiesService.getBulkValues(workspaceSlug, projectId, issueIds);
    runInAction(() => {
      // Requested issues without values still get an empty map so they aren't re-fetched.
      issueIds.forEach((id) => {
        set(this.valuesMap, [id], res?.[id] ?? {});
        this.requested.add(id);
      });
    });
  };

  setValue = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    propertyId: string,
    values: string[]
  ): Promise<string[]> => {
    const original = this.valuesMap[issueId]?.[propertyId];
    runInAction(() => {
      if (!this.valuesMap[issueId]) set(this.valuesMap, [issueId], {});
      set(this.valuesMap, [issueId, propertyId], values);
      this.requested.add(issueId);
    });
    try {
      const res = await issuePropertiesService.setValues(workspaceSlug, projectId, issueId, propertyId, values);
      runInAction(() => set(this.valuesMap, [issueId, propertyId], res.values));
      return res.values;
    } catch (error) {
      runInAction(() => {
        if (original === undefined) {
          const rest = { ...this.valuesMap[issueId] };
          delete rest[propertyId];
          set(this.valuesMap, [issueId], rest);
        } else {
          set(this.valuesMap, [issueId, propertyId], original);
        }
      });
      throw error;
    }
  };
}
