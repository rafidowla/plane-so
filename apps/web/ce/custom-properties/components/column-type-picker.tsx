/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Fragment, useState } from "react";
import { observer } from "mobx-react";
import { Menu } from "@headlessui/react";
import { Plus } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";
// local imports
import { EIssuePropertyType, PROPERTY_TYPE_META } from "@/plane-web/custom-properties";
import { PropertyFormModal } from "./property-form-modal";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** Trigger contents; defaults to a "+ Add property" affordance. */
  triggerContent?: React.ReactNode;
  triggerClassName?: string;
  menuPlacement?: "left" | "right";
};

/**
 * The "+" column-type menu — lists all property types with only Status (OPTION)
 * enabled in v1; the rest are greyed with a "soon" badge (flipping one on is a
 * one-line change in PROPERTY_TYPE_META once its renderer ships). Selecting
 * Status opens the create form modal. Self-contained so both the settings
 * "Add property" button and the spreadsheet header "+" just drop it in.
 */
export const ColumnTypePicker = observer(function ColumnTypePicker(props: Props) {
  const { workspaceSlug, projectId, triggerContent, triggerClassName, menuPlacement = "left" } = props;
  const { t } = useTranslation();
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handlePick = (enabled: boolean, type: EIssuePropertyType) => {
    if (!enabled) return;
    if (type === EIssuePropertyType.OPTION) setIsModalOpen(true);
  };

  return (
    <>
      <Menu as="div" className="relative inline-block text-left">
        <Menu.Button
          className={cn(
            "flex items-center gap-1 rounded text-xs text-secondary hover:text-primary focus:outline-none",
            triggerClassName
          )}
        >
          {triggerContent ?? (
            <span className="flex items-center gap-1">
              <Plus className="size-3.5" />
              {t("custom_properties.add_property")}
            </span>
          )}
        </Menu.Button>
        <Menu.Items
          className={cn(
            "absolute z-30 mt-1 max-h-72 w-48 overflow-y-auto rounded-md border border-subtle bg-layer-1 py-1 shadow-lg focus:outline-none",
            menuPlacement === "right" ? "right-0" : "left-0"
          )}
        >
          {PROPERTY_TYPE_META.map((meta) => (
            <Menu.Item key={meta.type} as={Fragment} disabled={!meta.enabled}>
              {({ active }) => (
                <button
                  type="button"
                  onClick={() => handlePick(meta.enabled, meta.type)}
                  disabled={!meta.enabled}
                  className={cn(
                    "flex w-full items-center justify-between px-3 py-1.5 text-left text-13",
                    meta.enabled ? "text-primary" : "cursor-default text-tertiary",
                    active && meta.enabled ? "bg-layer-2" : ""
                  )}
                >
                  <span>{t(meta.i18n_label)}</span>
                  {!meta.enabled && (
                    <span className="rounded bg-layer-2 px-1.5 py-0.5 text-[10px] uppercase text-tertiary">
                      {t("custom_properties.soon")}
                    </span>
                  )}
                </button>
              )}
            </Menu.Item>
          ))}
        </Menu.Items>
      </Menu>

      <PropertyFormModal
        isOpen={isModalOpen}
        handleClose={() => setIsModalOpen(false)}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
      />
    </>
  );
});
