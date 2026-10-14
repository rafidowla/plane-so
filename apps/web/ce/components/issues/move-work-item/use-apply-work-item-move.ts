/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91)
import { useCallback } from "react";
import { pull } from "lodash-es";
import { runInAction } from "mobx";
import type { TMoveResult } from "@plane/utils";
import { getMovedWorkItemIds } from "@plane/utils";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";

/**
 * After the server confirms a move, make the MobX stores agree with it:
 *  - every moved item (the item + sub-items that travelled with it) leaves every *loaded* list of every
 *    list store (project, cycle, module, workspace views, ...), with the stores' own counters kept in step;
 *  - the item itself stays in the work item map under its new project / sequence (so an open detail page
 *    does not flash "not found" before it navigates), with the project-scoped relations cleared;
 *  - moved sub-items are dropped from the map (they are re-fetched on demand);
 *  - sub-items that were detached stay where they are but lose their parent link;
 *  - the old parent's sub-item list no longer shows the moved item.
 */
export const useApplyWorkItemMove = () => {
  const { rootIssueStore, subIssues } = useIssueDetail();

  return useCallback(
    (result: TMoveResult) => {
      const issuesMap = rootIssueStore.issues;
      const movedIds = getMovedWorkItemIds(result);
      const previous = issuesMap.getIssueById(result.id);

      // Lists first: removeIssueFromList reads the issue from the map to find the groups it sits in.
      const listStores = [
        rootIssueStore.projectIssues,
        rootIssueStore.cycleIssues,
        rootIssueStore.moduleIssues,
        rootIssueStore.workspaceIssues,
        rootIssueStore.projectViewIssues,
        rootIssueStore.teamViewIssues,
        rootIssueStore.profileIssues,
      ];
      for (const store of listStores) {
        // Skip stores that never loaded a list: removing from an empty store would only create empty groups.
        if (!store?.groupedIssueIds) continue;
        for (const id of movedIds) {
          try {
            store.removeIssueFromList(id);
          } catch (error) {
            console.error("Failed to remove a moved work item from a list", error);
          }
        }
      }

      runInAction(() => {
        // The parent that stays behind no longer lists the moved item.
        if (previous?.parent_id) {
          const siblings = subIssues.subIssues[previous.parent_id];
          if (siblings) pull(siblings, result.id);
          const parent = issuesMap.getIssueById(previous.parent_id);
          if (parent) {
            issuesMap.updateIssue(previous.parent_id, {
              sub_issues_count: Math.max(0, (parent.sub_issues_count ?? 1) - 1),
            });
          }
        }

        // The item itself: re-point it at the target project.
        issuesMap.updateIssue(result.id, {
          project_id: result.project_id,
          sequence_id: result.sequence_id,
          parent_id: null,
          cycle_id: null,
          module_ids: [],
        });
        const moved = result.moved.find((m) => m.id === result.id);
        const newIdentifier = moved?.new_identifier ?? result.identifier;
        issuesMap.addIssueIdentifier(newIdentifier, result.id);

        // Sub-items that travelled with it.
        for (const id of movedIds) {
          if (id === result.id) continue;
          issuesMap.removeIssue(id);
        }

        // Sub-items that stayed behind lose their parent link.
        for (const id of result.detached_sub_item_ids ?? []) {
          if (issuesMap.getIssueById(id)) issuesMap.updateIssue(id, { parent_id: null });
        }
        if (result.detached_sub_item_ids?.length) delete subIssues.subIssues[result.id];
      });
    },
    [rootIssueStore, subIssues]
  );
};
