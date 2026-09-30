/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import type { ISearchIssueResponse, TIssue, TIssuePropertyValues } from "@plane/types";
// components
import { IssueModalContext } from "@/components/issues/issue-modal/context";
// hooks
import { useUser } from "@/hooks/store/user/user-user";
// FORK: custom-properties — create-time staged values are saved after the work item exists
import { issuePropertiesService } from "@/plane-web/custom-properties/issue-properties.service";

export type TIssueModalProviderProps = {
  templateId?: string;
  dataForPreload?: Partial<TIssue>;
  allowedProjectIds?: string[];
  children: React.ReactNode;
};

export const IssueModalProvider = observer(function IssueModalProvider(props: TIssueModalProviderProps) {
  const { children, allowedProjectIds } = props;
  // states
  const [selectedParentIssue, setSelectedParentIssue] = useState<ISearchIssueResponse | null>(null);
  // FORK: custom-properties — staged create-time values (propertyId -> values)
  const [issuePropertyValues, setIssuePropertyValues] = useState<TIssuePropertyValues>({});
  // store hooks
  const { projectsWithCreatePermissions } = useUser();
  // derived values
  const projectIdsWithCreatePermissions = Object.keys(projectsWithCreatePermissions ?? {});

  // FORK: custom-properties — persist staged values against the just-created
  // work item (improvement #19). Failures must not fail issue creation, and
  // staging is kept so "create more" reuses the last selection.
  const handleCreateUpdatePropertyValues = async (args: {
    issueId: string;
    projectId: string;
    workspaceSlug: string;
  }): Promise<void> => {
    const staged = Object.entries(issuePropertyValues).filter(([, values]) => Array.isArray(values));
    if (!args.workspaceSlug || !args.projectId || !args.issueId || staged.length === 0) return;
    await Promise.all(
      staged.map(([propertyId, values]) =>
        issuePropertiesService
          .setValues(args.workspaceSlug, args.projectId, args.issueId, propertyId, values as string[])
          .catch((error) => console.error(`Failed to save property ${propertyId} for ${args.issueId}:`, error))
      )
    );
  };

  return (
    <IssueModalContext.Provider
      // oxlint-disable-next-line react/jsx-no-constructed-context-values
      value={{
        allowedProjectIds: allowedProjectIds ?? projectIdsWithCreatePermissions,
        workItemTemplateId: null,
        setWorkItemTemplateId: () => {},
        isApplyingTemplate: false,
        setIsApplyingTemplate: () => {},
        selectedParentIssue,
        setSelectedParentIssue,
        issuePropertyValues,
        setIssuePropertyValues,
        issuePropertyValueErrors: {},
        setIssuePropertyValueErrors: () => {},
        getIssueTypeIdOnProjectChange: () => null,
        getActiveAdditionalPropertiesLength: () => 0,
        handlePropertyValuesValidation: () => true,
        handleCreateUpdatePropertyValues,
        handleProjectEntitiesFetch: () => Promise.resolve(),
        handleTemplateChange: () => Promise.resolve(),
        handleConvert: () => Promise.resolve(),
        handleCreateSubWorkItem: () => Promise.resolve(),
      }}
    >
      {children}
    </IssueModalContext.Provider>
  );
});
