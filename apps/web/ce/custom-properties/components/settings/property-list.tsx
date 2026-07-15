/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Pencil, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore } from "@plane/ui";
// local imports
import type { TIssueProperty } from "@/plane-web/custom-properties";
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { useCustomProperties } from "../../hooks/use-custom-properties";
import { ColumnTypePicker } from "../column-type-picker";
import { PropertyFormModal } from "../property-form-modal";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/**
 * The property management table on the settings page: lists every custom
 * property with its option colour chips, an edit affordance (reuses the form
 * modal), and a delete confirm whose copy spells out that values are removed.
 * Shares the store with the spreadsheet, so edits reflect there without reload.
 */
export const CustomPropertyList = observer(function CustomPropertyList({ workspaceSlug, projectId }: Props) {
  const { t } = useTranslation();
  const store = useCustomProperties();
  const { properties } = useProjectCustomProperties(workspaceSlug, projectId);

  const [editing, setEditing] = useState<TIssueProperty | null>(null);
  const [deleting, setDeleting] = useState<TIssueProperty | null>(null);
  const [isDeleteSubmitting, setIsDeleteSubmitting] = useState(false);

  const handleDelete = async () => {
    if (!deleting) return;
    setIsDeleteSubmitting(true);
    try {
      await store.deleteProperty(workspaceSlug, projectId, deleting.id);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t("common.success"), message: t("custom_properties.delete_property") });
      setDeleting(null);
    } catch (error: unknown) {
      const message = (error as { error?: string })?.error ?? "Something went wrong.";
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message });
    } finally {
      setIsDeleteSubmitting(false);
    }
  };

  return (
    <div className="mt-6 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">{t("custom_properties.options")}</h4>
        <ColumnTypePicker
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          menuPlacement="right"
          triggerClassName="rounded-md border border-subtle px-2.5 py-1"
        />
      </div>

      {properties.length === 0 ? (
        <p className="rounded-md border border-dashed border-subtle p-4 text-xs text-tertiary">
          {t("custom_properties.no_properties")}
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-subtle rounded-md border border-subtle">
          {properties.map((property) => (
            <li key={property.id} className="flex items-center justify-between gap-4 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{property.display_name || property.name}</p>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  {(property.options ?? [])
                    .slice()
                    .sort((a, b) => a.sort_order - b.sort_order)
                    .map((option) => (
                      <span
                        key={option.id}
                        className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]"
                        style={{ backgroundColor: option.logo_props?.color?.background ?? "#e5e5e5" }}
                      >
                        <span className="text-primary/90 mix-blend-luminosity">{option.name}</span>
                      </span>
                    ))}
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => setEditing(property)}
                  className="rounded p-1.5 text-tertiary hover:bg-layer-2 hover:text-primary"
                  title={t("custom_properties.edit_property")}
                >
                  <Pencil className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setDeleting(property)}
                  className="rounded p-1.5 text-tertiary hover:bg-layer-2 hover:text-danger-primary"
                  title={t("custom_properties.delete_property")}
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <PropertyFormModal
          isOpen={!!editing}
          handleClose={() => setEditing(null)}
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          property={editing}
        />
      )}

      <AlertModalCore
        isOpen={!!deleting}
        handleClose={() => setDeleting(null)}
        handleSubmit={handleDelete}
        isSubmitting={isDeleteSubmitting}
        variant="danger"
        title={t("custom_properties.delete_property")}
        content={
          <>
            Deleting <span className="font-medium">{deleting?.display_name || deleting?.name}</span>{" "}
            {t("custom_properties.delete_values_warning")}
          </>
        }
      />
    </div>
  );
});
