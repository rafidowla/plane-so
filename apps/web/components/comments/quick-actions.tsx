/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
import {
  DeleteOutline,
  EditOutline,
  GlobeOutline,
  LinkOutline,
  LockOutline,
  MoreHorizontalOutline,
  ReplyOutline, // FORK: comment-replies (#26)
} from "@makeplane/propel/icons";
// plane imports
import { EIssueCommentAccessSpecifier } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@makeplane/propel/components/menu";
import type { TIssueComment, TCommentsOperations } from "@plane/types";
import type { TContextMenuItem } from "@plane/blocks/context-menu";
import { resolveItemVariant } from "@plane/blocks/context-menu";
import { ConfirmDialog } from "@plane/blocks/dialog"; // FORK: comment delete confirmation
// hooks
import { useUser } from "@/hooks/store/user";

type TCommentCard = {
  activityOperations: TCommentsOperations;
  comment: TIssueComment;
  setEditMode: () => void;
  showAccessSpecifier: boolean;
  showCopyLinkOption: boolean;
  // FORK: comment-replies (#26)
  setReplyMode?: () => void;
};

export const CommentQuickActions = observer(function CommentQuickActions(props: TCommentCard) {
  const { activityOperations, comment, setEditMode, showAccessSpecifier, showCopyLinkOption, setReplyMode } = props;
  // store hooks
  const { data: currentUser } = useUser();
  // FORK: comment delete confirmation — deleting used to be one click with no undo
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  // derived values
  const isAuthor = currentUser?.id === comment.actor;
  const canEdit = isAuthor;
  const canDelete = isAuthor;
  // translation
  const { t } = useTranslation();

  const MENU_ITEMS = useMemo(
    function MENU_ITEMS(): TContextMenuItem[] {
      return [
        {
          // FORK: comment-replies (#26) — anyone who can comment can reply in thread
          key: "reply",
          action: () => setReplyMode?.(),
          title: t("issue.comments.reply", { defaultValue: "Reply" }),
          icon: ReplyOutline,
          shouldRender: !!setReplyMode,
        },
        {
          key: "edit",
          action: setEditMode,
          title: t("common.actions.edit"),
          icon: EditOutline,
          shouldRender: canEdit,
        },
        {
          key: "copy_link",
          action: () => activityOperations.copyCommentLink(comment.id),
          title: t("common.actions.copy_link"),
          icon: LinkOutline,
          shouldRender: showCopyLinkOption,
        },
        {
          key: "access_specifier",
          action: () =>
            activityOperations.updateComment(comment.id, {
              access:
                comment.access === EIssueCommentAccessSpecifier.INTERNAL
                  ? EIssueCommentAccessSpecifier.EXTERNAL
                  : EIssueCommentAccessSpecifier.INTERNAL,
            }),
          title:
            comment.access === EIssueCommentAccessSpecifier.INTERNAL
              ? t("issue.comments.switch.public")
              : t("issue.comments.switch.private"),
          icon: comment.access === EIssueCommentAccessSpecifier.INTERNAL ? GlobeOutline : LockOutline,
          shouldRender: showAccessSpecifier,
        },
        {
          key: "delete",
          // FORK: comment delete confirmation — open the dialog instead of deleting immediately
          action: () => setIsDeleteModalOpen(true),
          title: t("common.actions.delete"),
          icon: DeleteOutline,
          shouldRender: canDelete,
        },
      ].filter((item) => item.shouldRender !== false);
    },
    [
      t,
      setEditMode,
      canEdit,
      showCopyLinkOption,
      activityOperations,
      comment,
      showAccessSpecifier,
      canDelete,
      setReplyMode,
    ]
  );

  if (MENU_ITEMS.length === 0) return null;

  // FORK: comment delete confirmation
  const handleDeleteConfirm = () => {
    setIsDeleting(true);
    Promise.resolve(activityOperations.removeComment(comment.id))
      .catch(() => {})
      .finally(() => {
        setIsDeleting(false);
        setIsDeleteModalOpen(false);
      });
  };

  return (
    <>
      {/* FORK: comment delete confirmation */}
      <ConfirmDialog
        isOpen={isDeleteModalOpen}
        handleClose={() => setIsDeleteModalOpen(false)}
        handleSubmit={handleDeleteConfirm}
        isSubmitting={isDeleting}
        title={t("issue.comments.delete_comment", { defaultValue: "Delete comment" })}
        content={t("issue.comments.delete_comment_message", {
          defaultValue: "Are you sure you want to delete this comment? This action cannot be undone.",
        })}
      />
    <Menu>
      <MenuTrigger
        render={
          <IconButton
            icon={<Icon icon={MoreHorizontalOutline} />}
            aria-label={t("common.options")}
            variant="ghost"
            size="xs"
          />
        }
      />
      <MenuContent side="bottom" align="end">
        {MENU_ITEMS.map((item) => (
          <MenuItem
            key={item.key}
            variant={resolveItemVariant(item)}
            label={item.title ?? ""}
            description={item.description}
            icon={item.icon ? <Icon icon={item.icon} /> : undefined}
            disabled={item.disabled}
            onClick={() => item.action()}
          />
        ))}
      </MenuContent>
    </Menu>
    </>
  );
});
