/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { Dropdown } from "@plane/ui";
import { getContrastRatio, hexToRgb } from "@plane/utils";
// local imports
import type { TIssuePropertyOption } from "@/plane-web/custom-properties";
import type { TPropertyCellProps } from "./registry";

const WHITE = { r: 255, g: 255, b: 255 };
const NEAR_BLACK = { r: 23, g: 23, b: 23 };
const EMPTY_BG = "#e5e5e5";

/**
 * Monday-style status cell: the option's colour fills the entire cell and the
 * label text flips between white and near-black for whichever clears the higher
 * contrast ratio. Clicking opens the option picker (positioning/keyboard/close
 * handled by the shared `@plane/ui` Dropdown, so we don't fork a popover).
 */
const textColorFor = (background: string): string => {
  try {
    const rgb = hexToRgb(background);
    return getContrastRatio(rgb, WHITE) >= getContrastRatio(rgb, NEAR_BLACK) ? "#ffffff" : "#171717";
  } catch {
    return "#171717";
  }
};

const activeSortedOptions = (options: TIssuePropertyOption[] | undefined): TIssuePropertyOption[] =>
  (options ?? []).filter((o) => o.is_active).sort((a, b) => a.sort_order - b.sort_order);

export const StatusPropertyCell = observer(function StatusPropertyCell(props: TPropertyCellProps) {
  const { property, values, onChange, disabled } = props;

  const options = activeSortedOptions(property.options);
  const selectedId = values[0] ?? "";

  const dropdownOptions = options.map((o) => ({ data: o, value: o.id }));

  const handleChange = (value: string) => {
    // Re-selecting the current option clears it (single-select toggle).
    void onChange(value === selectedId ? [] : [value]);
  };

  return (
    <div className="h-11 border-b-[0.5px] border-subtle">
      <Dropdown
        value={selectedId}
        onChange={handleChange}
        options={dropdownOptions}
        disabled={disabled}
        keyExtractor={(opt) => opt.value}
        placement="bottom-start"
        inputPlaceholder="Search options"
        buttonContainerClassName="h-full w-full"
        buttonClassName="h-full w-full rounded-none p-0 text-left"
        buttonContent={(_isOpen, value) => {
          const opt = options.find((o) => o.id === value);
          if (!opt) {
            return (
              <div className="flex h-full w-full items-center px-page-x text-13 text-tertiary opacity-0 transition-opacity group-hover:opacity-100">
                +
              </div>
            );
          }
          const bg = opt.logo_props?.color?.background ?? EMPTY_BG;
          return (
            <div
              className="flex h-full w-full items-center px-page-x"
              style={{ backgroundColor: bg, color: textColorFor(bg) }}
            >
              <span className="truncate text-13 font-medium">{opt.name}</span>
            </div>
          );
        }}
        renderItem={({ value, selected }) => {
          const opt = options.find((o) => o.id === value);
          if (!opt) return null;
          const bg = opt.logo_props?.color?.background ?? EMPTY_BG;
          return (
            <div className="flex w-full items-center gap-2">
              <span className="h-3 w-3 flex-shrink-0 rounded-sm" style={{ backgroundColor: bg }} />
              <span className="flex-grow truncate">{opt.name}</span>
              {selected && <span className="text-xs text-tertiary">✓</span>}
            </div>
          );
        }}
      />
    </div>
  );
});
