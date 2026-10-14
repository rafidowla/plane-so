/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) — activity row for `field === "project"` (old_value / new_value are the old / new
// human identifiers, e.g. PSR-12 -> GFV3-40)
import { observer } from "mobx-react";
import { TransferWorkItemOutline } from "@makeplane/propel/icons";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// components
import { IssueActivityBlockComponent } from "./";

type TIssueProjectMoveActivity = { activityId: string; ends: "top" | "bottom" | undefined };

export const IssueProjectMoveActivity = observer(function IssueProjectMoveActivity(props: TIssueProjectMoveActivity) {
  const { activityId, ends } = props;
  // hooks
  const {
    activity: { getActivityById },
  } = useIssueDetail();

  const activity = getActivityById(activityId);

  if (!activity) return <></>;

  const workspaceSlug = activity.workspace_detail?.slug;
  const newIdentifier = activity.new_value;

  return (
    <IssueActivityBlockComponent
      icon={<TransferWorkItemOutline className="h-4 w-4 flex-shrink-0 text-secondary" aria-hidden="true" />}
      activityId={activityId}
      ends={ends}
    >
      <span>moved this work item from </span>
      <span className="font-medium text-primary">{activity.old_value}</span>
      <span> to </span>
      {workspaceSlug && newIdentifier ? (
        <a
          href={`/${workspaceSlug}/browse/${newIdentifier}/`}
          className="inline-flex items-center gap-1 truncate font-medium text-primary hover:underline"
        >
          <span className="truncate">{newIdentifier}</span>
        </a>
      ) : (
        <span className="font-medium text-primary">{newIdentifier}</span>
      )}
      <span>.</span>
    </IssueActivityBlockComponent>
  );
});
