/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Plus } from "lucide-react";
// plane imports
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@makeplane/propel/components/menu";
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
      <Menu>
        <MenuTrigger
          render={
            <button
              type="button"
              className={cn(
                "text-xs flex items-center gap-1 rounded text-secondary hover:text-primary focus:outline-none",
                triggerClassName
              )}
            />
          }
        >
          {triggerContent ?? (
            <span className="flex items-center gap-1">
              <Plus className="size-3.5" />
              {t("custom_properties.add_property")}
            </span>
          )}
        </MenuTrigger>
        <MenuContent side="bottom" align={menuPlacement === "right" ? "end" : "start"}>
          {PROPERTY_TYPE_META.map((meta) => (
            <MenuItem
              key={meta.type}
              label={t(meta.i18n_label)}
              disabled={!meta.enabled}
              onClick={() => handlePick(meta.enabled, meta.type)}
              trailing={
                !meta.enabled ? (
                  <span className="rounded bg-layer-2 px-1.5 py-0.5 text-[10px] text-tertiary uppercase">
                    {t("custom_properties.soon")}
                  </span>
                ) : undefined
              }
            />
          ))}
        </MenuContent>
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
