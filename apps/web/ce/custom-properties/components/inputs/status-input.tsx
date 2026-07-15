/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { Dropdown } from "@plane/ui";
// local imports
import type { TIssuePropertyOption } from "@/plane-web/custom-properties";
import { EMPTY_OPTION_BG } from "../../utils/contrast";
import type { TPropertyCellProps } from "../cells/registry";
import { StatusChip } from "../status-chip";

const activeSortedOptions = (options: TIssuePropertyOption[] | undefined): TIssuePropertyOption[] =>
  (options ?? []).filter((o) => o.is_active).sort((a, b) => a.sort_order - b.sort_order);

/**
 * Compact Status input (chip + option picker) for the issue modal and detail
 * sidebar — same option picker as the spreadsheet cell but rendered as a chip
 * rather than a full-cell fill. Registered in PROPERTY_INPUT_REGISTRY.
 */
export const StatusPropertyInput = observer(function StatusPropertyInput(props: TPropertyCellProps) {
  const { property, values, onChange, disabled } = props;

  const options = activeSortedOptions(property.options);
  const selectedId = values[0] ?? "";
  const selected = options.find((o) => o.id === selectedId);

  const dropdownOptions = options.map((o) => ({ data: o, value: o.id }));

  const handleChange = (value: string) => {
    void onChange(value === selectedId ? [] : [value]);
  };

  return (
    <Dropdown
      value={selectedId}
      onChange={handleChange}
      options={dropdownOptions}
      disabled={disabled}
      keyExtractor={(opt) => opt.value}
      queryArray={["name"]}
      placement="bottom-start"
      inputPlaceholder="Search options"
      buttonContainerClassName="w-full"
      buttonClassName="w-full rounded border border-subtle px-2 py-1 text-left hover:bg-layer-2"
      buttonContent={() =>
        selected ? <StatusChip option={selected} /> : <span className="text-xs text-tertiary">Empty</span>
      }
      renderItem={({ value }) => {
        const opt = options.find((o) => o.id === value);
        if (!opt) return null;
        const bg = opt.logo_props?.color?.background ?? EMPTY_OPTION_BG;
        return (
          <div className="flex w-full items-center gap-2">
            <span className="h-3 w-3 flex-shrink-0 rounded-sm" style={{ backgroundColor: bg }} />
            <span className="flex-grow truncate">{opt.name}</span>
          </div>
        );
      }}
    />
  );
});
