/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- the Projects page layout (List default / Grid), saved on the user's account per workspace
// through the workspace user-properties endpoint (display_filters.projects_layout). Not stored in localStorage.

import { useCallback } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { WORKSPACE_PROJECT_NAVIGATION_PREFERENCES } from "@plane/constants";
import { setToast } from "@plane/blocks/toast";
import type { TProjectsLayout } from "@plane/types";
import { useWorkspace } from "@/hooks/store/use-workspace";

export const useProjectsLayout = () => {
  const { workspaceSlug: routeSlug } = useParams();
  const workspaceSlug = routeSlug?.toString();
  const { getProjectNavigationPreferences, fetchProjectNavigationPreferences, updateProjectNavigationPreferences } =
    useWorkspace();
  // same SWR key as the workspace wrapper, so this dedupes to one request; only used to learn about a failure
  const { error } = useSWR(
    workspaceSlug ? WORKSPACE_PROJECT_NAVIGATION_PREFERENCES(workspaceSlug) : null,
    workspaceSlug ? () => fetchProjectNavigationPreferences(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );
  const preferences = workspaceSlug ? getProjectNavigationPreferences(workspaceSlug) : undefined;
  // while the preference loads callers show the loader (no flash of the wrong layout); if loading fails, fall back to list
  const isLoaded = preferences !== undefined || !!error;
  const layout: TProjectsLayout = preferences?.display_filters?.projects_layout ?? "list";

  const setLayout = useCallback(
    (next: TProjectsLayout) => {
      if (!workspaceSlug || next === layout) return;
      updateProjectNavigationPreferences(workspaceSlug, { display_filters: { projects_layout: next } }).catch(() =>
        setToast({
          type: "error",
          title: "Error!",
          message: "Couldn't save your layout preference. Please try again.",
        })
      );
    },
    [workspaceSlug, layout, updateProjectNavigationPreferences]
  );

  return { layout, isLoaded, setLayout };
};
