/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
// plane imports
import type { TIssueServiceType, TWorkItemWidgets } from "@plane/types";
// local imports
import { IssueDetailWidgetActionButtons } from "./action-buttons";
import { IssueDetailWidgetCollapsibles } from "./issue-detail-widget-collapsibles";
import { IssueDetailWidgetModals } from "./issue-detail-widget-modals";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
  /** FORK: guest-access (PSR-119) — attachments can stay enabled for a guest on their own work item while the rest is read-only */
  attachmentsDisabled?: boolean;
  /** FORK: guest-access (PSR-119) — a guest on an archived work item gets no "Add …" buttons, only the read-only lists */
  hideActionButtons?: boolean;
  renderWidgetModals?: boolean;
  issueServiceType: TIssueServiceType;
  hideWidgets?: TWorkItemWidgets[];
};

export function IssueDetailWidgets(props: Props) {
  const {
    workspaceSlug,
    projectId,
    issueId,
    disabled,
    attachmentsDisabled = disabled,
    hideActionButtons = false,
    renderWidgetModals = true,
    issueServiceType,
    hideWidgets,
  } = props;

  return (
    <>
      <div className="flex flex-col space-y-4">
        {!hideActionButtons && (
          <IssueDetailWidgetActionButtons
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            issueId={issueId}
            disabled={disabled}
            attachmentsDisabled={attachmentsDisabled}
            issueServiceType={issueServiceType}
            hideWidgets={hideWidgets}
          />
        )}
        <IssueDetailWidgetCollapsibles
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          issueId={issueId}
          disabled={disabled}
          attachmentsDisabled={attachmentsDisabled}
          issueServiceType={issueServiceType}
          hideWidgets={hideWidgets}
        />
      </div>
      {renderWidgetModals && (
        <IssueDetailWidgetModals
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          issueId={issueId}
          issueServiceType={issueServiceType}
          hideWidgets={hideWidgets}
        />
      )}
    </>
  );
}
