/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
// FORK: custom-properties
import { WorkItemAdditionalSidebarProperties } from "@/plane-web/components/issues/issue-details/additional-properties";
// FORK: time-tracking
import { IssueWorklogProperty } from "@/plane-web/components/issues/worklog/property";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// FORK: guest-access (PSR-119)
import { useGuestWorkItemAccess } from "@/plane-web/guest-access";
// local imports
import type { TIssueOperations } from "../issue-detail";
import { IssueDetailSidebarSections } from "../issue-detail/sidebar-sections";

interface IPeekOverviewProperties {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
  issueOperations: TIssueOperations;
}

export const PeekOverviewProperties = observer(function PeekOverviewProperties(props: IPeekOverviewProperties) {
  const { workspaceSlug, projectId, issueId, issueOperations, disabled } = props;
  // store hooks
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const issue = getIssueById(issueId);
  // FORK: guest-access (PSR-119) — a guest on their own (non-archived) work item can edit priority and labels only
  const { isGuestEditable } = useGuestWorkItemAccess(workspaceSlug, projectId, issue?.created_by, issue?.archived_at);
  if (!issue) return <></>;

  return (
    <div className="flex w-full flex-col gap-4 py-6">
      <IssueDetailSidebarSections
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        issueOperations={issueOperations}
        disabled={disabled}
        isGuestEditable={isGuestEditable} // FORK: guest-access (PSR-119)
        additionalProperties={
          <>
            {/* FORK: time-tracking */}
            <IssueWorklogProperty
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              issueId={issueId}
              disabled={disabled}
            />

            {/* FORK: custom-properties */}
            <WorkItemAdditionalSidebarProperties
              workItemId={issue.id}
              workItemTypeId={issue.type_id}
              projectId={projectId}
              workspaceSlug={workspaceSlug}
              isEditable={!disabled}
              isPeekView
            />
          </>
        }
      />
    </div>
  );
});
