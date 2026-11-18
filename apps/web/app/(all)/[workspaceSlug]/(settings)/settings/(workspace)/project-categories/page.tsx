/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- workspace project categories. Workspace Admins only (the server enforces it too).

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { TProjectCategory } from "@plane/types";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useProjectCategory } from "@/hooks/store/use-project-category";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { ProjectCategoriesWorkspaceSettingsHeader } from "./header";

const MAX_NAME_LENGTH = 255;

/** The server answers `{ error }` (duplicate name), `{ name: [...] }` (field errors) or `{ detail }`. */
const getErrorMessage = (error: unknown, fallback: string): string => {
  const data = error as { error?: unknown; detail?: unknown; name?: unknown } | undefined;
  if (typeof data?.error === "string") return data.error;
  if (Array.isArray(data?.name) && typeof data.name[0] === "string") return data.name[0];
  if (typeof data?.detail === "string") return data.detail;
  return fallback;
};

type TNameFormProps = {
  initialName?: string;
  submitLabel: string;
  onSubmit: (name: string) => Promise<void>;
  onCancel?: () => void;
};

/** Inline name form used for both "add" and "rename"; shows the server's error text under the field. */
function CategoryNameForm(props: TNameFormProps) {
  const { initialName = "", submitLabel, onSubmit, onCancel } = props;
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t("workspace_settings.settings.project_categories.name_required"));
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await onSubmit(trimmed);
      setName("");
    } catch (err) {
      setError(getErrorMessage(err, t("workspace_settings.settings.project_categories.save_failed")));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex w-full flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-48 grow">
          <InputGroup size="lg">
            <Input
              size="lg"
              name="category-name"
              type="text"
              value={name}
              maxLength={MAX_NAME_LENGTH}
              // eslint-disable-next-line jsx-a11y/no-autofocus -- inline editor opened by an explicit user action
              autoFocus={!!onCancel}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              placeholder={t("workspace_settings.settings.project_categories.name_placeholder")}
              aria-label={t("workspace_settings.settings.project_categories.name_placeholder")}
              aria-invalid={!!error}
            />
          </InputGroup>
        </div>
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          type="submit"
          label={submitLabel}
          loading={isSaving}
          disabled={isSaving}
        />
        {onCancel && (
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            type="button"
            label={t("workspace_settings.settings.project_categories.cancel")}
            disabled={isSaving}
            onClick={onCancel}
          />
        )}
      </div>
      {error && (
        <p role="alert" className="text-caption-md-regular text-danger-primary">
          {error}
        </p>
      )}
    </form>
  );
}

type TRowProps = {
  category: TProjectCategory;
  isEditing: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onRename: (name: string) => Promise<void>;
  onDelete: () => void;
};

function CategoryRow(props: TRowProps) {
  const { category, isEditing, onEdit, onCancelEdit, onRename, onDelete } = props;
  const { t } = useTranslation();
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-1 py-2.5">
      {isEditing ? (
        <CategoryNameForm
          initialName={category.name}
          submitLabel={t("workspace_settings.settings.project_categories.save")}
          onSubmit={onRename}
          onCancel={onCancelEdit}
        />
      ) : (
        <>
          <div className="flex min-w-0 items-baseline gap-3">
            <span className="truncate text-body-sm-medium text-primary">{category.name}</span>
            <span className="shrink-0 text-caption-md-regular text-tertiary">
              {t("workspace_settings.settings.project_categories.projects_count", { count: category.project_count })}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              stretch="auto"
              label={t("workspace_settings.settings.project_categories.rename")}
              onClick={onEdit}
            />
            <Button
              variant="ghost"
              size="sm"
              stretch="auto"
              label={t("workspace_settings.settings.project_categories.delete")}
              onClick={onDelete}
            />
          </div>
        </>
      )}
    </li>
  );
}

function ProjectCategoriesPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  const { t } = useTranslation();
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  const { fetchCategories, getCategoriesForWorkspace, isFetched, createCategory, updateCategory, deleteCategory } =
    useProjectCategory();
  // state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [categoryToDelete, setCategoryToDelete] = useState<TProjectCategory | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  // derived values
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const categories = getCategoriesForWorkspace(workspaceSlug);

  const { error, mutate } = useSWR(
    isAdmin ? `PROJECT_CATEGORIES_${workspaceSlug}` : null,
    () => fetchCategories(workspaceSlug),
    { revalidateOnFocus: false }
  );

  if (workspaceUserInfo[workspaceSlug] && !isAdmin) return <NotAuthorizedView section="settings" className="h-auto" />;

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.project_categories.title")}`
    : undefined;

  const handleCreate = async (name: string) => {
    await createCategory(workspaceSlug, { name });
    setToast({
      type: "success",
      title: t("toast.success"),
      message: t("workspace_settings.settings.project_categories.toast.created"),
    });
  };

  const handleRename = async (categoryId: string, name: string) => {
    await updateCategory(workspaceSlug, categoryId, { name });
    setEditingId(null);
    setToast({
      type: "success",
      title: t("toast.success"),
      message: t("workspace_settings.settings.project_categories.toast.updated"),
    });
  };

  const handleConfirmDelete = async () => {
    if (!categoryToDelete) return;
    setIsDeleting(true);
    try {
      await deleteCategory(workspaceSlug, categoryToDelete.id);
      setToast({
        type: "success",
        title: t("toast.success"),
        message: t("workspace_settings.settings.project_categories.toast.deleted"),
      });
      setCategoryToDelete(null);
    } catch (err) {
      setToast({
        type: "error",
        title: t("toast.error"),
        message: getErrorMessage(err, t("workspace_settings.settings.project_categories.delete_failed")),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <SettingsContentWrapper header={<ProjectCategoriesWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <SettingsHeading
          title={t("workspace_settings.settings.project_categories.title")}
          description={t("workspace_settings.settings.project_categories.description")}
        />
        <div className="mt-4">
          <CategoryNameForm
            submitLabel={t("workspace_settings.settings.project_categories.add")}
            onSubmit={handleCreate}
          />
        </div>
        <div className="mt-3 border-t border-subtle">
          {error && !isFetched(workspaceSlug) ? (
            <div className="flex items-center gap-3 py-6">
              <p className="text-body-xs-regular text-danger-primary">
                {t("workspace_settings.settings.project_categories.load_failed")}
              </p>
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t("workspace_settings.settings.project_categories.retry")}
                onClick={() => void mutate()}
              />
            </div>
          ) : !isFetched(workspaceSlug) ? (
            <div className="space-y-2 py-4">
              {[0, 1, 2].map((i) => (
                <span key={i} className="block h-10 w-full rounded-sm bg-layer-1" />
              ))}
            </div>
          ) : categories.length === 0 ? (
            <p className="py-6 text-body-xs-regular text-tertiary">
              {t("workspace_settings.settings.project_categories.empty")}
            </p>
          ) : (
            <ul>
              {categories.map((category) => (
                <CategoryRow
                  key={category.id}
                  category={category}
                  isEditing={editingId === category.id}
                  onEdit={() => setEditingId(category.id)}
                  onCancelEdit={() => setEditingId(null)}
                  onRename={(name) => handleRename(category.id, name)}
                  onDelete={() => setCategoryToDelete(category)}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
      <ConfirmDialog
        isOpen={!!categoryToDelete}
        handleClose={() => setCategoryToDelete(null)}
        handleSubmit={handleConfirmDelete}
        isSubmitting={isDeleting}
        title={t("workspace_settings.settings.project_categories.delete_title")}
        content={
          <>
            {t("workspace_settings.settings.project_categories.delete_message", { name: categoryToDelete?.name ?? "" })}{" "}
            {t("workspace_settings.settings.project_categories.delete_impact", {
              count: categoryToDelete?.project_count ?? 0,
            })}
          </>
        }
      />
    </SettingsContentWrapper>
  );
}

export default observer(ProjectCategoriesPage);
