/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useState, useRef } from "react";
import { debounce } from "lodash-es";
import { observer } from "mobx-react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import type { EditorRefApi, TExtensions } from "@plane/editor";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { EFileAssetType, TNameDescriptionLoader } from "@plane/types";
import { getDescriptionPlaceholderI18n } from "@plane/utils";
// components
import { RichTextEditor } from "@/components/editor/rich-text";
// hooks
import { useEditorAsset } from "@/hooks/store/use-editor-asset";
import { useWorkspace } from "@/hooks/store/use-workspace";
// plane web services
import { WorkspaceService } from "@/services/workspace.service";
// local imports
import { DescriptionInputLoader } from "./loader";
// services init
const workspaceService = new WorkspaceService();

type TFormData = {
  id: string;
  description_html: string;
  description_json?: object;
  isMigrationUpdate: boolean;
};

type Props = {
  /**
   * @description Container class name, this will be used to add custom styles to the editor container
   */
  containerClassName?: string;
  /**
   * @description Disabled, this will be used to disable the editor
   */
  disabled?: boolean;
  /**
   * @description Disabled extensions, this will be used to disable the extensions in the editor
   */
  disabledExtensions?: TExtensions[];
  /**
   * @description Editor ref, this will be used to imperatively attach editor related helper functions
   */
  editorRef?: React.RefObject<EditorRefApi>;
  /**
   * @description Entity ID, this will be used for file uploads and as the unique identifier for the entity
   */
  entityId: string;
  /**
   * @description File asset type, this will be used to upload the file to the editor
   */
  fileAssetType: EFileAssetType;
  /**
   * @description Initial value, pass the actual description to initialize the editor
   */
  initialValue: string | undefined;
  /**
   * @description Key, to ensure the editor is re-rendered when the key changes
   */
  key: string;
  /**
   * @description Submit handler, the actual function which will be called when the form is submitted
   */
  onSubmit: (
    value: {
      description_html: string;
      description_json: object | undefined;
    },
    isMigrationUpdate?: boolean
  ) => Promise<void>;
  /**
   * @description Placeholder, if not provided, the placeholder will be the default placeholder
   */
  placeholder?: string | ((isFocused: boolean, value: string) => string);
  /**
   * @description projectId, if not provided, the entity will be considered as a workspace entity
   */
  projectId?: string;
  /**
   * @description Set is submitting, use it to set the loading state of the form
   */
  setIsSubmitting: (initialValue: TNameDescriptionLoader) => void;
  /**
   * @description SWR description, use it only if you want to sync changes in realtime(pseudo realtime)
   */
  swrDescription?: string | null | undefined;
  /**
   * @description Workspace slug, this will be used to get the workspace details
   */
  workspaceSlug: string;
  /**
   * @description Issue sequence id, this will be used to get the issue sequence id
   */
  issueSequenceId?: number;
  /**
   * @description Save mode — "auto" debounces saves while typing (upstream behavior);
   * "explicit" shows Save/Cancel buttons and only persists on Save.
   * FORK: description-save-cancel (#16) — defaults to "explicit".
   */
  saveMode?: "auto" | "explicit";
};

/**
 * @description DescriptionInput component for rich text editor. "auto" mode autosaves
 * with a debounce (and on unmount); "explicit" mode (default, FORK #16) shows
 * Save/Cancel actions and only applies changes on Save.
 */
export const DescriptionInput = observer(function DescriptionInput(props: Props) {
  const {
    containerClassName,
    disabled,
    disabledExtensions,
    editorRef,
    entityId,
    fileAssetType,
    initialValue,
    issueSequenceId,
    onSubmit,
    placeholder,
    projectId,
    saveMode = "explicit", // FORK: description-save-cancel (#16)
    setIsSubmitting,
    swrDescription,
    workspaceSlug,
  } = props;
  // states
  const [localDescription, setLocalDescription] = useState<TFormData>({
    id: entityId,
    description_html: initialValue?.trim() ?? "",
    isMigrationUpdate: false,
  });
  // FORK: description-save-cancel (#16) — state mirror of hasUnsavedChanges so
  // the Save/Cancel bar re-renders when the dirty flag flips
  const [hasUnsaved, setHasUnsaved] = useState(false);
  // ref to track if there are unsaved changes
  const hasUnsavedChanges = useRef(false);
  // ref to track last saved content (to skip onChange when content hasn't actually changed)
  const lastSavedContent = useRef(initialValue?.trim() === "" ? "<p></p>" : (initialValue ?? "<p></p>"));
  // FORK: description-save-cancel (#16) — internal ref so Cancel can reset the
  // editor even when the consumer didn't pass one
  const fallbackEditorRef = useRef<EditorRefApi>(null);
  const activeEditorRef = editorRef ?? fallbackEditorRef;
  // store hooks
  const { getWorkspaceBySlug } = useWorkspace();
  const { uploadEditorAsset, duplicateEditorAsset } = useEditorAsset();
  // derived values
  const workspaceDetails = getWorkspaceBySlug(workspaceSlug);
  // translation
  const { t } = useTranslation();
  // form info
  const { handleSubmit, reset, control, setValue } = useForm<TFormData>({
    defaultValues: {
      id: entityId,
      description_html: initialValue?.trim() ?? "",
      isMigrationUpdate: false,
    },
  });

  // submit handler
  const handleDescriptionFormSubmit = useCallback(
    async (formData: TFormData) => {
      await onSubmit(
        {
          description_html: formData.description_html,
          description_json: formData.description_json,
        },
        formData.isMigrationUpdate
      );
      // Update lastSavedContent after successful save
      lastSavedContent.current = formData.description_html;
    },
    [onSubmit]
  );

  // reset form values
  useEffect(() => {
    if (!entityId) return;
    const normalizedValue = initialValue?.trim() === "" ? "<p></p>" : (initialValue ?? "<p></p>");
    // Update last saved content when entity/initialValue changes
    lastSavedContent.current = normalizedValue;
    reset({
      id: entityId,
      description_html: normalizedValue,
      isMigrationUpdate: false,
    });
    setLocalDescription({
      id: entityId,
      description_html: normalizedValue,
      isMigrationUpdate: false,
    });
    // Reset unsaved changes flag when form is reset
    hasUnsavedChanges.current = false;
    setHasUnsaved(false);
  }, [entityId, initialValue, reset]);

  // ADDING handleDescriptionFormSubmit TO DEPENDENCY ARRAY PRODUCES ADVERSE EFFECTS
  // TODO: Verify the exhaustive-deps warning
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const debouncedFormSave = useCallback(
    debounce(async () => {
      handleSubmit(handleDescriptionFormSubmit)()
        .catch((error) => console.error(`Failed to save description for ${entityId}:`, error))
        .finally(() => {
          setIsSubmitting("submitted");
          hasUnsavedChanges.current = false;
          setHasUnsaved(false);
        });
    }, 1500),
    [entityId, handleSubmit]
  );

  // FORK: description-save-cancel (#16) — explicit save: persist only on click
  const handleExplicitSave = useCallback(() => {
    debouncedFormSave.cancel();
    handleSubmit(handleDescriptionFormSubmit)()
      .catch((error) => console.error(`Failed to save description for ${entityId}:`, error))
      .finally(() => {
        setIsSubmitting("submitted");
        hasUnsavedChanges.current = false;
        setHasUnsaved(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, handleSubmit]);

  // FORK: description-save-cancel (#16) — cancel: restore the last saved content
  const handleExplicitCancel = useCallback(() => {
    debouncedFormSave.cancel();
    activeEditorRef.current?.setEditorValue(lastSavedContent.current, false);
    setValue("description_html", lastSavedContent.current);
    hasUnsavedChanges.current = false;
    setHasUnsaved(false);
    setIsSubmitting("saved");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeEditorRef, setValue, setIsSubmitting]);

  // Save on unmount if there are unsaved changes
  useEffect(
    () => () => {
      debouncedFormSave.cancel();

      // FORK: description-save-cancel (#16) — explicit mode never saves on
      // unmount; unsaved edits are discarded unless Save was clicked
      if (saveMode === "explicit") return;

      if (hasUnsavedChanges.current) {
        handleSubmit(handleDescriptionFormSubmit)()
          .catch((error) => {
            console.error("Failed to save description on unmount:", error);
          })
          .finally(() => {
            setIsSubmitting("submitted");
            hasUnsavedChanges.current = false;
          });
      }
    },
    // since we don't want to save on unmount if there are no unsaved changes, no deps are needed
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [saveMode]
  );

  if (!workspaceDetails) return null;

  if (!localDescription.description_html) return <DescriptionInputLoader />;

  return (
    <>
      <Controller
        name="description_html"
        control={control}
        render={({ field: { onChange } }) => (
          <RichTextEditor
            key={entityId}
            editable={!disabled}
            ref={activeEditorRef}
            id={entityId}
            issueSequenceId={issueSequenceId}
            disabledExtensions={disabledExtensions}
            initialValue={localDescription.description_html ?? "<p></p>"}
            value={swrDescription ?? null}
            workspaceSlug={workspaceSlug}
            workspaceId={workspaceDetails.id}
            projectId={projectId}
            dragDropEnabled
            onChange={(description_json, description_html, options) => {
              if (description_html === lastSavedContent.current) return;
              setIsSubmitting("submitting");
              onChange(description_html);
              setValue("isMigrationUpdate", !!options?.isMigrationUpdate);
              setValue("description_json", description_json);
              hasUnsavedChanges.current = true;
              setHasUnsaved(true);
              // FORK: description-save-cancel (#16) — explicit mode waits for the Save button
              if (saveMode === "auto") debouncedFormSave();
            }}
            placeholder={placeholder ?? ((isFocused, value) => t(getDescriptionPlaceholderI18n(isFocused, value)))}
            searchMentionCallback={async (payload) =>
              await workspaceService.searchEntity(workspaceSlug?.toString() ?? "", {
                ...payload,
                project_id: projectId,
              })
            }
            containerClassName={containerClassName}
            uploadFile={async (blockId, file) => {
              try {
                const { asset_id } = await uploadEditorAsset({
                  blockId,
                  data: {
                    entity_identifier: entityId,
                    entity_type: fileAssetType,
                  },
                  file,
                  projectId,
                  workspaceSlug,
                });
                return asset_id;
              } catch (error) {
                console.log("Error in uploading asset:", error);
                throw new Error("Asset upload failed. Please try again later.", { cause: error });
              }
            }}
            duplicateFile={async (assetId: string) => {
              try {
                const { asset_id } = await duplicateEditorAsset({
                  assetId,
                  entityType: fileAssetType,
                  projectId,
                  workspaceSlug,
                });
                return asset_id;
              } catch {
                throw new Error("Asset duplication failed. Please try again later.");
              }
            }}
          />
        )}
      />
      {/* FORK: description-save-cancel (#16) — explicit Save/Cancel bar */}
      {saveMode === "explicit" && hasUnsaved && !disabled && (
        <div className="flex items-center justify-end gap-2 px-2 pb-2">
          <Button variant="secondary" size="sm" onClick={handleExplicitCancel}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleExplicitSave}>
            {t("common.save")}
          </Button>
        </div>
      )}
    </>
  );
});
