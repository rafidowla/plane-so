/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import type { E_SORT_ORDER, TActivityFilters, EActivityFilterType } from "@plane/constants";
import { BASE_ACTIVITY_FILTER_TYPES, filterActivityOnSelectedFilters } from "@plane/constants";
import type { TCommentsOperations } from "@plane/types";
// components
import { CommentCard } from "@/components/comments/card/root";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import { IssueActivityItem } from "./activity/activity-list";
import { IssueActivityLoader } from "./loader";
// FORK: time-tracking
import { IssueActivityWorklog } from "@/plane-web/components/issues/worklog/activity/root";

type TIssueActivityCommentRoot = {
  workspaceSlug: string;
  projectId: string;
  isIntakeIssue: boolean;
  issueId: string;
  selectedFilters: TActivityFilters[];
  activityOperations: TCommentsOperations;
  showAccessSpecifier?: boolean;
  disabled?: boolean;
  sortOrder: E_SORT_ORDER;
};

export const IssueActivityCommentRoot = observer(function IssueActivityCommentRoot(props: TIssueActivityCommentRoot) {
  const {
    workspaceSlug,
    isIntakeIssue,
    issueId,
    selectedFilters,
    activityOperations,
    showAccessSpecifier,
    projectId,
    disabled,
    sortOrder,
  } = props;
  // store hooks
  const {
    activity: { getActivityAndCommentsByIssueId },
    comment: { getCommentById },
  } = useIssueDetail();
  // derived values
  const activityAndComments = getActivityAndCommentsByIssueId(issueId, sortOrder);

  if (!activityAndComments) return <IssueActivityLoader />;

  if (activityAndComments.length <= 0) return null;

  const filteredActivityAndComments = filterActivityOnSelectedFilters(activityAndComments, selectedFilters);

  // FORK: jira-comment-structure (#21) — render reply comments indented under
  // their parent instead of as standalone entries, preserving the migrated
  // Jira discussion flow. A reply is only grouped when its parent is visible
  // in the current feed/filter; otherwise it renders as a normal comment.
  const visibleCommentIds = new Set(
    filteredActivityAndComments.filter((ac) => ac.activity_type === "COMMENT").map((ac) => ac.id)
  );
  const repliesByParent = new Map<string, NonNullable<ReturnType<typeof getCommentById>>[]>();
  for (const activityComment of filteredActivityAndComments) {
    if (activityComment.activity_type !== "COMMENT") continue;
    const comment = getCommentById(activityComment.id);
    if (!comment?.parent || !visibleCommentIds.has(comment.parent)) continue;
    const siblings = repliesByParent.get(comment.parent) ?? [];
    siblings.push(comment);
    repliesByParent.set(comment.parent, siblings);
  }

  return (
    <div>
      {filteredActivityAndComments.map((activityComment, index) => {
        const comment = getCommentById(activityComment.id);
        // replies render under their parent, not as standalone entries
        if (activityComment.activity_type === "COMMENT" && comment?.parent && visibleCommentIds.has(comment.parent))
          return null;
        const replies = repliesByParent.get(activityComment.id) ?? [];
        return activityComment.activity_type === "COMMENT" ? (
          <div key={activityComment.id}>
            <CommentCard
              workspaceSlug={workspaceSlug}
              entityId={issueId}
              comment={comment}
              activityOperations={activityOperations}
              ends={index === 0 ? "top" : index === filteredActivityAndComments.length - 1 ? "bottom" : undefined}
              showAccessSpecifier={!!showAccessSpecifier}
              showCopyLinkOption={!isIntakeIssue}
              disabled={disabled}
              projectId={projectId}
              enableReplies
            />
            {replies.length > 0 && (
              <div className="ml-10 border-l border-subtle pl-3">
                {replies.map((reply) => (
                  <CommentCard
                    key={reply.id}
                    workspaceSlug={workspaceSlug}
                    entityId={issueId}
                    comment={reply}
                    activityOperations={activityOperations}
                    ends={undefined}
                    showAccessSpecifier={!!showAccessSpecifier}
                    showCopyLinkOption={!isIntakeIssue}
                    disabled={disabled}
                    projectId={projectId}
                    enableReplies
                  />
                ))}
              </div>
            )}
          </div>
        ) : activityComment.activity_type === "WORKLOG" ? (
          // FORK: time-tracking
          <IssueActivityWorklog
            key={activityComment.id}
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            issueId={issueId}
            activityComment={activityComment}
            ends={index === 0 ? "top" : index === filteredActivityAndComments.length - 1 ? "bottom" : undefined}
          />
        ) : BASE_ACTIVITY_FILTER_TYPES.includes(activityComment.activity_type as EActivityFilterType) ? (
          <IssueActivityItem
            key={activityComment.id}
            activityId={activityComment.id}
            ends={index === 0 ? "top" : index === filteredActivityAndComments.length - 1 ? "bottom" : undefined}
          />
        ) : null;
      })}
    </div>
  );
});
