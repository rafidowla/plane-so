/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import type { E_SORT_ORDER } from "@plane/constants";
import type { TCommentsOperations, TIssueComment } from "@plane/types";
// local components
import { CommentCard } from "./card/root";
import { CommentCreate } from "./comment-create";

type TCommentsWrapper = {
  projectId?: string;
  entityId: string;
  isEditingAllowed?: boolean;
  activityOperations: TCommentsOperations;
  comments: TIssueComment[] | string[];
  sortOrder?: E_SORT_ORDER;
  getCommentById?: (activityId: string) => TIssueComment | undefined;
  showAccessSpecifier?: boolean;
  showCopyLinkOption?: boolean;
  enableReplies?: boolean;
};

export const CommentsWrapper = observer(function CommentsWrapper(props: TCommentsWrapper) {
  const {
    entityId,
    activityOperations,
    comments,
    getCommentById,
    isEditingAllowed = true,
    projectId,
    showAccessSpecifier = false,
    showCopyLinkOption = false,
    enableReplies = false,
  } = props;
  // router
  const { workspaceSlug: routerWorkspaceSlug } = useParams();
  const workspaceSlug = routerWorkspaceSlug?.toString();
  const renderCommentCreate = useMemo(
    () =>
      isEditingAllowed && (
        <CommentCreate
          workspaceSlug={workspaceSlug}
          entityId={entityId}
          activityOperations={activityOperations}
          projectId={projectId}
        />
      ),
    [isEditingAllowed, workspaceSlug, entityId, activityOperations, projectId]
  );

  return (
    <div className="relative flex h-full flex-col gap-y-2 overflow-hidden">
      {renderCommentCreate}
      <div className="flex-grow overflow-y-auto py-4">
        {(() => {
          // FORK: jira-comment-structure (#21) — group reply comments under
          // their parent so migrated Jira threads keep their discussion flow.
          const resolved = comments
            ?.map((data) => (typeof data === "string" ? getCommentById?.(data) : data))
            .filter((c): c is TIssueComment => !!c);
          if (!resolved) return null;
          const visibleIds = new Set(resolved.map((c) => c.id));
          const topLevel = resolved.filter((c) => !c.parent || !visibleIds.has(c.parent));
          const repliesByParent = new Map<string, TIssueComment[]>();
          for (const comment of resolved) {
            if (!comment.parent || !visibleIds.has(comment.parent)) continue;
            const siblings = repliesByParent.get(comment.parent) ?? [];
            siblings.push(comment);
            repliesByParent.set(comment.parent, siblings);
          }
          return topLevel.map((comment, index) => {
            const replies = repliesByParent.get(comment.id) ?? [];
            return (
              <div key={comment.id}>
                <CommentCard
                  workspaceSlug={workspaceSlug}
                  entityId={entityId}
                  comment={comment}
                  activityOperations={activityOperations}
                  disabled={!isEditingAllowed}
                  ends={index === 0 ? "top" : index === topLevel.length - 1 ? "bottom" : undefined}
                  projectId={projectId}
                  showAccessSpecifier={showAccessSpecifier}
                  showCopyLinkOption={showCopyLinkOption}
                  enableReplies={enableReplies}
                />
                {replies.length > 0 && (
                  <div className="ml-10 border-l border-subtle pl-3">
                    {replies.map((reply) => (
                      <CommentCard
                        key={reply.id}
                        workspaceSlug={workspaceSlug}
                        entityId={entityId}
                        comment={reply}
                        activityOperations={activityOperations}
                        disabled={!isEditingAllowed}
                        ends={undefined}
                        projectId={projectId}
                        showAccessSpecifier={showAccessSpecifier}
                        showCopyLinkOption={showCopyLinkOption}
                        enableReplies={enableReplies}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          });
        })()}
      </div>
    </div>
  );
});
