/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// FORK: guest-access (PSR-119)
import { useGuestWorkItemAccess } from "@/plane-web/guest-access";
// FORK: custom-properties
import { WorkItemAdditionalSidebarProperties } from "@/plane-web/components/issues/issue-details/additional-properties";
// FORK: time-tracking
import { IssueWorklogProperty } from "@/plane-web/components/issues/worklog/property";
// local imports
import type { TIssueOperations } from "./root";
import { IssueDetailSidebarSections } from "./sidebar-sections";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  issueOperations: TIssueOperations;
  isEditable: boolean;
};

export const IssueDetailsSidebar = observer(function IssueDetailsSidebar(props: Props) {
  const { workspaceSlug, projectId, issueId, issueOperations, isEditable } = props;
  // store hooks
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const issue = getIssueById(issueId);
  // FORK: guest-access (PSR-119) — a guest on their own work item can edit priority and labels only
  const { canGuestEdit } = useGuestWorkItemAccess(workspaceSlug, projectId, issue?.created_by);
  const isGuestEditable = canGuestEdit && !issue?.archived_at; // archived work items are read-only, as in the peek view
  if (!issue) return <></>;

  return (
    <div className="h-full w-full overflow-hidden overflow-y-auto px-6 py-6">
      <IssueDetailSidebarSections
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        issueOperations={issueOperations}
        disabled={!isEditable}
        showUpdatedMeta
        isGuestEditable={isGuestEditable} // FORK: guest-access (PSR-119)
        additionalProperties={
          <>
            {/* FORK: time-tracking */}
            <IssueWorklogProperty
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              issueId={issueId}
              disabled={!isEditable}
            />

            {/* FORK: custom-properties */}
            <WorkItemAdditionalSidebarProperties
              workItemId={issue.id}
              workItemTypeId={issue.type_id}
              projectId={projectId}
              workspaceSlug={workspaceSlug}
              isEditable={isEditable}
            />
          </>
        }
      />
    </div>
  );
});
