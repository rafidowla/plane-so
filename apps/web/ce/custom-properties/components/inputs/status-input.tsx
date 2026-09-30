/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { Select } from "@plane/blocks/select";
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

  const handleChange = (value: string) => {
    // Re-selecting the current option clears it (single-select toggle).
    void onChange(value === selectedId ? [] : [value]);
  };

  return (
    <Select<TIssuePropertyOption>
      value={selected ?? null}
      onChange={handleChange}
      getValues={() => options}
      getOptionValue={(o) => o.id}
      getOptionLabel={(o) => o.name}
      getOptionIcon={(o) => (
        <span
          className="h-3 w-3 flex-shrink-0 rounded-sm"
          style={{ backgroundColor: o.logo_props?.color?.background ?? EMPTY_OPTION_BG }}
        />
      )}
      pinSelected={false}
      searchPlaceholder="Search options"
      disabled={disabled}
    >
      <Select.Trigger<TIssuePropertyOption>
        variant="table-cell"
        className="h-auto w-full rounded border border-subtle px-2 py-1 text-left hover:bg-layer-2"
      >
        {(sel) => (sel[0] ? <StatusChip option={sel[0]} /> : <span className="text-xs text-tertiary">Empty</span>)}
      </Select.Trigger>
    </Select>
  );
});
