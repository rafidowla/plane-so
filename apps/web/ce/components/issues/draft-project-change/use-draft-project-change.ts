/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: draft-project-change (PSR-92)
import { useCallback, useRef, useState } from "react";
import type { UseFormGetValues, UseFormSetValue } from "react-hook-form";
import { useTranslation } from "@plane/i18n";
import { setToast } from "@plane/blocks/toast";
import type { ISearchIssueResponse, TIssue } from "@plane/types";
import type { TMoveDropRow } from "@plane/utils";
import { buildDraftProjectMappingPayload, getDraftFormValuesFromMapping, summarizeDraftDrops } from "@plane/utils";
// services
import { workItemMoveService } from "@/services/issue/work-item-move.service";

export type TDraftProjectChangeNotice = { projectId: string; rows: TMoveDropRow[] };

type Args = {
  workspaceSlug: string;
  getValues: UseFormGetValues<TIssue>;
  setValue: UseFormSetValue<TIssue>;
  /** Default work item type of the new project, used when the mapping clears the type. */
  getIssueTypeIdOnProjectChange: (projectId: string) => string | null;
  setSelectedParentIssue: React.Dispatch<React.SetStateAction<ISearchIssueResponse | null>>;
  /** Called when the server could not map the values: the caller falls back to the upstream form reset. */
  onMappingFailed: (targetProjectId: string) => void;
};

/**
 * Re-maps a draft's project-scoped form values (state, labels, assignees, estimate, type, parent, cycle, modules)
 * when its project changes, instead of the upstream reset that wipes them. The server owns the mapping rules
 * (`POST /draft-issues/project-mapping/`, shared with "Move to project"); name, description, priority and dates are
 * not touched.
 */
export const useDraftProjectChange = (args: Args) => {
  const { workspaceSlug, getValues, setValue, getIssueTypeIdOnProjectChange, setSelectedParentIssue, onMappingFailed } =
    args;
  const { t } = useTranslation();
  const [notice, setNotice] = useState<TDraftProjectChangeNotice | null>(null);
  const [isMapping, setIsMapping] = useState(false);
  // only the latest request may write to the form
  const requestIdRef = useRef(0);

  const mapDraftToProject = useCallback(
    async (sourceProjectId: string, targetProjectId: string) => {
      const requestId = ++requestIdRef.current;
      setNotice(null);
      setIsMapping(true);
      try {
        const mapping = await workItemMoveService.getDraftProjectMapping(
          workspaceSlug,
          buildDraftProjectMappingPayload(sourceProjectId, targetProjectId, getValues())
        );
        // superseded by a newer switch, or the person already moved on
        if (requestId !== requestIdRef.current || getValues("project_id") !== targetProjectId) return;
        const values = getDraftFormValuesFromMapping(mapping);
        const options = { shouldDirty: true } as const;
        setValue("state_id", values.state_id, options);
        setValue("label_ids", values.label_ids, options);
        setValue("assignee_ids", values.assignee_ids, options);
        setValue("estimate_point", values.estimate_point, options);
        setValue("parent_id", values.parent_id, options);
        setValue("cycle_id", values.cycle_id, options);
        setValue("module_ids", values.module_ids, options);
        setValue("type_id", values.type_id ?? getIssueTypeIdOnProjectChange(targetProjectId), options);
        if (!values.parent_id) setSelectedParentIssue(null);
        const rows = summarizeDraftDrops(mapping);
        setNotice(rows.length > 0 ? { projectId: targetProjectId, rows } : null);
      } catch {
        if (requestId !== requestIdRef.current) return;
        setToast({ type: "error", title: t("error"), message: t("draft_project_change.error") });
        onMappingFailed(targetProjectId);
      } finally {
        if (requestId === requestIdRef.current) setIsMapping(false);
      }
    },
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [workspaceSlug, getValues, setValue, getIssueTypeIdOnProjectChange, setSelectedParentIssue, onMappingFailed]
  );

  const dismissNotice = useCallback(() => setNotice(null), []);

  return { mapDraftToProject, notice, dismissNotice, isMapping };
};
