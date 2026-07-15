/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type React from "react";
// plane imports
import type { TIssue } from "@plane/types";
// local imports
import { EIssuePropertyType } from "@/plane-web/custom-properties";
import type { TIssueProperty } from "@/plane-web/custom-properties";
import { StatusPropertyCell } from "./status-cell";

/**
 * Props every per-type cell renderer receives. `values` are option ids for
 * OPTION and scalars for the other types; `onChange` writes through the
 * property-values store (optimistic).
 */
export type TPropertyCellProps = {
  issue: TIssue;
  property: TIssueProperty;
  values: string[];
  onChange: (values: string[]) => Promise<void>;
  disabled: boolean;
};

/**
 * Per-property-type extension point — the one place a new column type slots in.
 * v1 ships only the OPTION ("Status") renderer; TEXT/DECIMAL/DATETIME/… land
 * here as their renderers arrive, with no changes to the seam components that
 * consume this map. Parallels upstream's `SPREADSHEET_COLUMNS` map.
 */
export const PROPERTY_CELL_REGISTRY: Partial<Record<EIssuePropertyType, React.FC<TPropertyCellProps>>> = {
  [EIssuePropertyType.OPTION]: StatusPropertyCell,
};
