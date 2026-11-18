/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- favourite toggle + copy-link, extracted from card.tsx so the List view row behaves identically.

import { IS_FAVORITE_MENU_OPEN } from "@plane/constants";
import { useLocalStorage } from "@plane/hooks";
import { setPromiseToast, setToast } from "@plane/blocks/toast";
import { copyUrlToClipboard } from "@plane/utils";
import { useProject } from "@/hooks/store/use-project";

export const useProjectSharedActions = (workspaceSlug: string | undefined, projectId: string) => {
  const { addProjectToFavorites, removeProjectFromFavorites } = useProject();
  const { setValue: toggleFavoriteMenu, storedValue: isFavoriteMenuOpen } = useLocalStorage<boolean>(
    IS_FAVORITE_MENU_OPEN,
    false
  );

  const handleAddToFavorites = () => {
    if (!workspaceSlug) return;

    const addToFavoritePromise = addProjectToFavorites(workspaceSlug, projectId);
    setPromiseToast(addToFavoritePromise, {
      loading: "Adding project to favorites...",
      success: {
        title: "Success!",
        message: () => "Project added to favorites.",
        // propel: the callback exists for its side effect; a toast action is data, and there is
        // none here, so it returns an empty list rather than an empty fragment.
        actionItems: () => {
          if (!isFavoriteMenuOpen) toggleFavoriteMenu(true);
          return [];
        },
      },
      error: {
        title: "Error!",
        message: () => "Couldn't add the project to favorites. Please try again.",
      },
    });
  };

  const handleRemoveFromFavorites = () => {
    if (!workspaceSlug) return;

    const removeFromFavoritePromise = removeProjectFromFavorites(workspaceSlug, projectId);
    setPromiseToast(removeFromFavoritePromise, {
      loading: "Removing project from favorites...",
      success: {
        title: "Success!",
        message: () => "Project removed from favorites.",
      },
      error: {
        title: "Error!",
        message: () => "Couldn't remove the project from favorites. Please try again.",
      },
    });
  };

  const handleCopyLink = () =>
    copyUrlToClipboard(`${workspaceSlug}/projects/${projectId}/issues`).then(() =>
      setToast({
        type: "info",
        title: "Link Copied!",
        message: "Project link copied to clipboard.",
      })
    );

  return { handleAddToFavorites, handleRemoveFromFavorites, handleCopyLink };
};
