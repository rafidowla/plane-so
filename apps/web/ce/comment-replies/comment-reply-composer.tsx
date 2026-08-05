/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { useForm } from "react-hook-form";
// plane imports
import { EIssueCommentAccessSpecifier } from "@plane/constants";
import type { EditorRefApi } from "@plane/editor";
import { useTranslation } from "@plane/i18n";
import { CheckIcon, CloseIcon } from "@plane/propel/icons";
import type { TCommentsOperations, TIssueComment } from "@plane/types";
import { cn, isCommentEmpty } from "@plane/utils";
// components
import { LiteTextEditor } from "@/components/editor/lite-text";
// services
import { FileService } from "@/services/file.service";

type Props = {
  activityOperations: TCommentsOperations;
  parentCommentId: string;
  projectId?: string;
  workspaceId: string;
  workspaceSlug: string;
  /** called after a successful reply OR when the user cancels */
  onDone: () => void;
};

// services
const fileService = new FileService();

/**
 * Inline composer for replying to a comment in a thread. Submits with
 * `parent` set so the reply nests under the parent comment (the threading
 * display from #21 renders it). Modeled on CommentCardEditForm.
 */
export const CommentReplyComposer = observer(function CommentReplyComposer(props: Props) {
  const { activityOperations, parentCommentId, projectId, workspaceId, workspaceSlug, onDone } = props;
  // states
  const [uploadedAssetIds, setUploadedAssetIds] = useState<string[]>([]);
  // refs
  const editorRef = useRef<EditorRefApi>(null);
  // translation
  const { t } = useTranslation();
  // form info
  const {
    formState: { isSubmitting },
    handleSubmit,
    setFocus,
    watch,
    setValue,
  } = useForm<Partial<TIssueComment>>({
    defaultValues: { comment_html: "<p></p>" },
  });
  const commentHTML = watch("comment_html");

  const isEmpty = isCommentEmpty(commentHTML);
  const isSubmitButtonDisabled = isSubmitting || !editorRef.current?.isEditorReadyToDiscard();
  const isDisabled = isSubmitting || isEmpty || isSubmitButtonDisabled;

  const onSubmit = async (formData: Partial<TIssueComment>) => {
    if (isSubmitting) return;
    try {
      const reply = await activityOperations.createComment({
        comment_html: formData.comment_html,
        access: EIssueCommentAccessSpecifier.INTERNAL,
        parent: parentCommentId,
      });
      // same bulk-link flow as the main composer: editor images are uploaded
      // first and only get linked once the comment exists
      if (uploadedAssetIds.length > 0 && reply?.id) {
        if (projectId) {
          await fileService.updateBulkProjectAssetsUploadStatus(workspaceSlug, projectId.toString(), reply.id, {
            asset_ids: uploadedAssetIds,
          });
        } else {
          await fileService.updateBulkWorkspaceAssetsUploadStatus(workspaceSlug, reply.id, {
            asset_ids: uploadedAssetIds,
          });
        }
      }
      onDone();
    } catch (error) {
      console.error(error);
    }
  };

  useEffect(() => {
    setFocus("comment_html");
  }, [setFocus]);

  return (
    <form className="flex flex-col gap-2">
      <div
        role="presentation"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !isEmpty) handleSubmit(onSubmit)(e);
        }}
      >
        <LiteTextEditor
          editable
          workspaceId={workspaceId}
          workspaceSlug={workspaceSlug}
          ref={editorRef}
          id={`reply_comment_${parentCommentId}`}
          initialValue="<p></p>"
          value={null}
          onChange={(_comment_json, comment_html) => setValue("comment_html", comment_html)}
          onEnterKeyPress={(e) => {
            if (!isEmpty && !isSubmitting) {
              handleSubmit(onSubmit)(e);
            }
          }}
          showSubmitButton={false}
          uploadFile={async (blockId, file) => {
            const { asset_id } = await activityOperations.uploadCommentAsset(blockId, file);
            setUploadedAssetIds((prev) => [...prev, asset_id]);
            return asset_id;
          }}
          duplicateFile={async (assetId: string) => {
            const { asset_id } = await activityOperations.duplicateCommentAsset(assetId);
            setUploadedAssetIds((prev) => [...prev, asset_id]);
            return asset_id;
          }}
          projectId={projectId}
          parentClassName="p-2 bg-surface-1"
          displayConfig={{
            fontSize: "small-font",
          }}
        />
      </div>
      <div className="flex items-center gap-2 self-end">
        <span className="text-caption-sm-regular text-tertiary">
          {t("issue.comments.replying_hint", { defaultValue: "Replying in thread" })}
        </span>
        {!isEmpty && (
          <button
            type="button"
            aria-label={t("issue.comments.reply", { defaultValue: "Reply" })}
            onClick={handleSubmit(onSubmit)}
            disabled={isDisabled}
            className={cn(
              "group grid size-7 place-items-center rounded-lg border border-success-subtle bg-success-subtle shadow-raised-100 duration-300",
              isDisabled ? "" : "hover:bg-success-subtle-1"
            )}
          >
            <CheckIcon className="size-4 text-success-primary" />
          </button>
        )}
        <button
          type="button"
          aria-label={t("common.actions.cancel", { defaultValue: "Cancel" })}
          disabled={isSubmitting}
          className={cn(
            "group grid size-7 place-items-center rounded-lg border border-danger-subtle bg-danger-subtle shadow-raised-100 duration-300",
            isSubmitting ? "" : "hover:bg-danger-subtle-hover"
          )}
          onClick={onDone}
        >
          <CloseIcon className="size-4 text-danger-primary" />
        </button>
      </div>
    </form>
  );
});
