/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import useSWR from "swr";

import type { TIssueProperty } from "@/plane-web/custom-properties";
import { useCustomProperties } from "./use-custom-properties";

const SWR_OPTS = { revalidateIfStale: false, revalidateOnFocus: false } as const;

/**
 * Fetches the per-project feature flag and (when enabled) the property list, and
 * returns both. Every custom-properties seam component uses this to gate itself:
 * with the feature off, `enabled` is false and `properties` is empty, so nothing
 * renders and the DOM stays byte-identical to stock Plane.
 */
export const useProjectCustomProperties = (
  workspaceSlug: string | undefined,
  projectId: string | undefined
): { enabled: boolean; properties: TIssueProperty[] } => {
  const store = useCustomProperties();

  useSWR(
    workspaceSlug && projectId ? `CUSTOM_PROPERTIES_FEATURE_${workspaceSlug}_${projectId}` : null,
    workspaceSlug && projectId ? () => store.fetchFeature(workspaceSlug, projectId) : null,
    SWR_OPTS
  );

  const enabled = store.isFeatureEnabled(projectId);

  useSWR(
    enabled && workspaceSlug && projectId ? `CUSTOM_PROPERTIES_LIST_${workspaceSlug}_${projectId}` : null,
    enabled && workspaceSlug && projectId ? () => store.fetchProjectProperties(workspaceSlug, projectId) : null,
    SWR_OPTS
  );

  return { enabled, properties: enabled ? store.getProjectProperties(projectId) : [] };
};

/** Thin gate: just whether custom properties are enabled for the project. */
export const useCustomPropertiesEnabled = (
  workspaceSlug: string | undefined,
  projectId: string | undefined
): boolean => useProjectCustomProperties(workspaceSlug, projectId).enabled;
