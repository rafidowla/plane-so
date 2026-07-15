/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Check, Plus, Trash2 } from "lucide-react";
// plane imports
import { LABEL_COLOR_OPTIONS } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, Input, ModalCore } from "@plane/ui";
import { cn } from "@plane/utils";
// local imports
import { EIssuePropertyType } from "@/plane-web/custom-properties";
import type { TIssueProperty } from "@/plane-web/custom-properties";
import { useCustomProperties } from "../hooks/use-custom-properties";

type OptionDraft = {
  id?: string;
  name: string;
  color: string;
  is_default: boolean;
};

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  projectId: string;
  /** Present ⇒ edit mode. */
  property?: TIssueProperty | null;
};

const colorForIndex = (i: number): string => LABEL_COLOR_OPTIONS[i % LABEL_COLOR_OPTIONS.length];

const draftFromProperty = (property: TIssueProperty): OptionDraft[] =>
  (property.options ?? [])
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((o) => ({
      id: o.id,
      name: o.name,
      color: o.logo_props?.color?.background ?? colorForIndex(0),
      is_default: o.is_default,
    }));

export const PropertyFormModal = observer(function PropertyFormModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, projectId, property } = props;
  const { t } = useTranslation();
  const store = useCustomProperties();
  const isEdit = !!property;

  const [displayName, setDisplayName] = useState("");
  const [options, setOptions] = useState<OptionDraft[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    if (property) {
      setDisplayName(property.display_name || property.name);
      setOptions(draftFromProperty(property));
    } else {
      setDisplayName("");
      setOptions([
        { name: "", color: colorForIndex(0), is_default: true },
        { name: "", color: colorForIndex(1), is_default: false },
      ]);
    }
  }, [isOpen, property]);

  const addOption = () =>
    setOptions((prev) => [...prev, { name: "", color: colorForIndex(prev.length), is_default: prev.length === 0 }]);

  const updateOptionAt = (index: number, patch: Partial<OptionDraft>) =>
    setOptions((prev) => prev.map((o, i) => (i === index ? { ...o, ...patch } : o)));

  const setDefaultAt = (index: number) =>
    setOptions((prev) => prev.map((o, i) => ({ ...o, is_default: i === index })));

  const removeOptionAt = (index: number) =>
    setOptions((prev) => {
      const next = prev.filter((_, i) => i !== index);
      // keep exactly one default if any options remain
      if (next.length > 0 && !next.some((o) => o.is_default)) next[0].is_default = true;
      return next;
    });

  const submit = async () => {
    const name = displayName.trim();
    if (!name) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: t("custom_properties.name_required") });
      return;
    }
    const cleanOptions = options.map((o) => ({ ...o, name: o.name.trim() })).filter((o) => o.name.length > 0);
    if (cleanOptions.length === 0) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: t("custom_properties.options_required") });
      return;
    }

    setIsSubmitting(true);
    try {
      if (isEdit && property) {
        if (name !== (property.display_name || property.name)) {
          await store.updateProperty(workspaceSlug, projectId, property.id, { display_name: name });
        }
        const existingIds = new Set((property.options ?? []).map((o) => o.id));
        const keptIds = new Set(cleanOptions.filter((o) => o.id).map((o) => o.id));
        // deletions
        for (const opt of property.options ?? []) {
          if (!keptIds.has(opt.id)) await store.deleteOption(workspaceSlug, projectId, property.id, opt.id);
        }
        // creates + updates
        for (let i = 0; i < cleanOptions.length; i++) {
          const o = cleanOptions[i];
          const payload = {
            name: o.name,
            logo_props: { color: { background: o.color } },
            is_default: o.is_default,
            sort_order: (i + 1) * 1000,
          };
          if (o.id && existingIds.has(o.id)) {
            await store.updateOption(workspaceSlug, projectId, property.id, o.id, payload);
          } else {
            await store.createOption(workspaceSlug, projectId, property.id, payload);
          }
        }
        // resync embedded options ordering/state
        await store.fetchProjectProperties(workspaceSlug, projectId);
      } else {
        await store.createProperty(workspaceSlug, projectId, {
          display_name: name,
          property_type: EIssuePropertyType.OPTION,
          is_active: true,
          is_multi: false,
          options: cleanOptions.map((o, i) => ({
            name: o.name,
            logo_props: { color: { background: o.color } },
            is_default: o.is_default,
            sort_order: (i + 1) * 1000,
          })),
        });
      }
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: isEdit ? t("custom_properties.edit_property") : t("custom_properties.new_property"),
      });
      handleClose();
    } catch (error: unknown) {
      const message = (error as { error?: string })?.error ?? "Something went wrong.";
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-lg font-medium">
          {isEdit ? t("custom_properties.edit_property") : t("custom_properties.new_property")}
        </h3>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-secondary">{t("custom_properties.property_name")}</label>
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t("custom_properties.property_name_placeholder")}
            className="w-full"
            autoFocus
          />
        </div>

        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium text-secondary">{t("custom_properties.options")}</label>
          <div className="flex flex-col gap-2">
            {options.map((option, index) => (
              <div key={option.id ?? `new-${index}`} className="flex items-center gap-2">
                <label
                  className="relative size-6 flex-shrink-0 cursor-pointer rounded border border-subtle"
                  style={{ backgroundColor: option.color }}
                  title="Pick colour"
                >
                  <input
                    type="color"
                    value={option.color}
                    onChange={(e) => updateOptionAt(index, { color: e.target.value })}
                    className="absolute inset-0 cursor-pointer opacity-0"
                  />
                </label>
                <Input
                  value={option.name}
                  onChange={(e) => updateOptionAt(index, { name: e.target.value })}
                  placeholder={t("custom_properties.option_name_placeholder")}
                  className="flex-grow"
                />
                <button
                  type="button"
                  onClick={() => setDefaultAt(index)}
                  className={cn(
                    "flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                    option.is_default ? "bg-accent-primary/10 text-accent-primary" : "text-tertiary hover:bg-layer-2"
                  )}
                  title={t("custom_properties.set_default")}
                >
                  {option.is_default && <Check className="size-3" />}
                  {t("custom_properties.default_badge")}
                </button>
                <button
                  type="button"
                  onClick={() => removeOptionAt(index)}
                  className="rounded p-1 text-tertiary hover:bg-layer-2 hover:text-danger-primary"
                  title="Remove"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={addOption}
            className="mt-1 flex w-fit items-center gap-1 text-xs text-accent-primary hover:underline"
          >
            <Plus className="size-3.5" />
            {t("custom_properties.add_option")}
          </button>
        </div>

        <div className="mt-2 flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={submit} loading={isSubmitting}>
            {isEdit
              ? isSubmitting
                ? t("custom_properties.saving")
                : t("custom_properties.save_changes")
              : isSubmitting
                ? t("custom_properties.creating")
                : t("custom_properties.create")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
