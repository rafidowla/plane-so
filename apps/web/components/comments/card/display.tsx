/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { usePathname } from "next/navigation";
import { ChevronDown, ChevronUp } from "lucide-react";
// plane imports
import { Avatar } from "@makeplane/propel/components/avatar";
import { ActivityCommentCard, ActivityCommentHeader, ActivityTimelineTimestamp } from "@plane/blocks/activity";
import type { EditorRefApi } from "@plane/editor";
import { useHashScroll } from "@plane/hooks";
import { GlobeOutline, LockOutline } from "@makeplane/propel/icons";
import { EIssueCommentAccessSpecifier } from "@plane/types";
import type { TCommentsOperations, TIssueComment } from "@plane/types";
import { calculateTimeAgo, cn, getFileURL, renderFormattedDate, renderFormattedTime } from "@plane/utils";
// components
import { LiteTextEditor } from "@/components/editor/lite-text";
// local imports
import { CommentReactions } from "../comment-reaction";
import { CommentCardEditForm } from "./edit-form";
import { EmojiReactionButton, EmojiReactionPicker } from "@plane/blocks/emoji-reaction";
import { useMember } from "@/hooks/store/use-member";
// FORK: comment-attachments (#18)
import { CommentAttachmentList } from "@/plane-web/comment-attachments";

export type TCommentCardDisplayProps = {
  activityOperations: TCommentsOperations;
  comment: TIssueComment;
  disabled: boolean;
  entityId: string;
  projectId?: string;
  readOnlyEditorRef: React.RefObject<EditorRefApi | null>;
  showAccessSpecifier: boolean;
  workspaceId: string;
  workspaceSlug: string;
  isEditing?: boolean;
  setIsEditing?: (isEditing: boolean) => void;
  renderFooter?: (ReactionsComponent: ReactNode | null) => ReactNode;
  renderQuickActions?: () => ReactNode;
  /** Draw the timeline rail down to the next entry. */
  showConnector?: boolean;
};

export const CommentCardDisplay = observer(function CommentCardDisplay(props: TCommentCardDisplayProps) {
  const {
    activityOperations,
    comment,
    disabled,
    projectId,
    readOnlyEditorRef,
    showAccessSpecifier,
    workspaceId,
    workspaceSlug,
    isEditing = false,
    setIsEditing,
    renderFooter,
    renderQuickActions,
    showConnector = true,
  } = props;
  // states
  const [isHighlighted, setIsHighlighted] = useState(false);
  // state
  const [isPickerOpen, setIsPickerOpen] = useState(false);
  // FORK: PSR-57 — long comments collapse behind a Show more toggle
  const COMMENT_COLLAPSE_HEIGHT = 320;
  const commentBodyRef = useRef<HTMLDivElement | null>(null);
  const [isClampable, setIsClampable] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  // Measure the full content height (ref sits on the inner div, so the clamp
  // never interferes with scrollHeight). Re-measures when images etc. load.
  useEffect(() => {
    const el = commentBodyRef.current;
    if (!el || isExpanded) return;
    const measure = () => setIsClampable(el.scrollHeight > COMMENT_COLLAPSE_HEIGHT + 8);
    measure();
    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(el);
    return () => resizeObserver.disconnect();
  }, [isExpanded, comment.comment_html]);
  // store hooks
  const { getUserDetails } = useMember();
  // derived values
  const userDetails = getUserDetails(comment?.actor);
  // FORK: jira-comment-structure (#21) — for migrated comments whose Jira
  // author isn't a workspace member, show the original Jira display name
  // instead of the import initiator the comment was attributed to.
  const externalAuthorName = comment?.external_actor_display?.trim() || null;
  const displayName =
    externalAuthorName ??
    (comment?.actor_detail?.is_bot
      ? comment?.actor_detail?.first_name + `Bot`
      : (userDetails?.display_name ?? comment?.actor_detail?.display_name));
  const avatarUrl = externalAuthorName ? undefined : (userDetails?.avatar_url ?? comment?.actor_detail?.avatar_url);

  const userReactions = activityOperations.userReactions(comment.id);

  // navigation
  const pathname = usePathname();
  // derived values
  const commentBlockId = `comment-${comment?.id}`;
  // Check if there are any reactions to determine if we should render the footer
  const reactionIds = activityOperations.reactionIds(comment.id);
  const hasReactions = reactionIds && Object.keys(reactionIds).some((key) => reactionIds[key]?.length > 0);

  // scroll to comment
  const { isHashMatch } = useHashScroll({
    elementId: commentBlockId,
    pathname,
  });

  useEffect(() => {
    if (!isHashMatch) return;
    setIsHighlighted(true);
    const timeout = setTimeout(() => {
      setIsHighlighted(false);
    }, 8000);

    return () => clearTimeout(timeout);
  }, [isHashMatch]);

  const handleEmojiSelect = useCallback(
    (emoji: string) => {
      if (!userReactions) return;
      // emoji is already in decimal string format from EmojiReactionPicker
      void activityOperations.react(comment.id, emoji, userReactions);
    },
    [activityOperations, comment.id, userReactions]
  );

  const shouldRenderReactions = hasReactions && !disabled;

  const reactions = shouldRenderReactions ? (
    <CommentReactions comment={comment} disabled={disabled} activityOperations={activityOperations} />
  ) : null;

  return (
    <ActivityCommentCard
      id={comment.id}
      showConnector={showConnector}
      highlighted={isHighlighted}
      avatar={
        <Avatar
          alt={displayName}
          fallback={displayName?.[0]?.toUpperCase()}
          size="2xs"
          src={avatarUrl ? getFileURL(avatarUrl) : undefined}
        />
      }
      header={
        <ActivityCommentHeader
          // FORK: jira-comment-structure (#21) — flag comments migrated from Jira
          author={externalAuthorName ? `${displayName} (via Jira)` : displayName}
          action="commented"
          visibilityIcon={
            showAccessSpecifier ? (
              comment.access === EIssueCommentAccessSpecifier.INTERNAL ? (
                <LockOutline className="size-3" />
              ) : (
                <GlobeOutline className="size-3" />
              )
            ) : undefined
          }
          timestamp={
            <ActivityTimelineTimestamp
              tooltip={`${renderFormattedDate(comment.created_at)} at ${renderFormattedTime(comment.created_at)}`}
            >
              {calculateTimeAgo(comment.created_at)}
              {comment.edited_at && " (edited)"}
            </ActivityTimelineTimestamp>
          }
          actions={
            !disabled ? (
              <>
                <EmojiReactionPicker
                  isOpen={isPickerOpen}
                  handleToggle={setIsPickerOpen}
                  onChange={handleEmojiSelect}
                  disabled={disabled}
                  render={<EmojiReactionButton />}
                  placement="bottom-start"
                />
                {renderQuickActions ? renderQuickActions() : null}
              </>
            ) : undefined
          }
        />
      }
      footer={!isEditing && reactions ? (renderFooter ? renderFooter(reactions) : reactions) : undefined}
    >
      <div id={commentBlockId}>
        {isEditing && setIsEditing ? (
          <CommentCardEditForm
            activityOperations={activityOperations}
            comment={comment}
            isEditing={isEditing}
            readOnlyEditorRef={readOnlyEditorRef.current}
            setIsEditing={setIsEditing}
            projectId={projectId}
            workspaceId={workspaceId}
            workspaceSlug={workspaceSlug}
          />
        ) : (
          <>
            {/* FORK: PSR-57 — clamp long comments; the inner ref div is measured, the outer div clips */}
            <div className={cn("relative", !isExpanded && isClampable && "max-h-[320px] overflow-hidden")}>
              <div ref={commentBodyRef}>
                <LiteTextEditor
                  editable={false}
                  ref={readOnlyEditorRef}
                  id={comment.id}
                  initialValue={comment.comment_html ?? ""}
                  workspaceId={workspaceId}
                  workspaceSlug={workspaceSlug}
                  containerClassName="p-0!"
                  projectId={projectId?.toString()}
                  displayConfig={{
                    fontSize: "small-font",
                  }}
                  parentClassName="border-none"
                />
              </div>
              {!isExpanded && isClampable && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-surface-1 to-transparent" />
              )}
            </div>
            {isClampable && (
              <button
                type="button"
                onClick={() => setIsExpanded((prev) => !prev)}
                className="flex w-fit items-center gap-1 text-xs font-medium text-tertiary transition-colors hover:text-secondary"
              >
                {isExpanded ? (
                  <>
                    Show less <ChevronUp className="size-3" />
                  </>
                ) : (
                  <>
                    Show more <ChevronDown className="size-3" />
                  </>
                )}
              </button>
            )}
            {/* FORK: comment-attachments (#18) */}
            {projectId && (
              <CommentAttachmentList
                workspaceSlug={workspaceSlug}
                projectId={projectId.toString()}
                commentId={comment.id}
              />
            )}
          </>
        )}
      </div>
    </ActivityCommentCard>
  );
});
