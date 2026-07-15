/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";

type Props = {
  isEpic?: boolean;
};

/**
 * Trailing `<th>`s for the spreadsheet header — one per active custom property.
 * Mounted via a single 1-line seam after the built-in column map
 * (`spreadsheet-header.tsx`). Returns `null` when the feature is off or when the
 * layout isn't project-scoped, so the header is byte-identical to stock Plane
 * for everyone else. Metrics mirror `spreadsheet-header-column.tsx`.
 *
 * The "+" add-column affordance is added in P5 (column-type picker).
 */
export const CustomPropertyHeaderCells = observer(function CustomPropertyHeaderCells(_props: Props) {
  const { workspaceSlug, projectId } = useParams();
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug?.toString(), projectId?.toString());

  if (!enabled || properties.length === 0) return null;

  return (
    <>
      {properties.map((property) => (
        <th
          key={property.id}
          className="h-11 min-w-36 items-center border border-t-0 border-b-0 border-subtle bg-layer-1 py-1 text-13 font-medium"
        >
          <div className="flex h-full w-full items-center gap-1.5 px-page-x">
            <span className="truncate">{property.display_name || property.name}</span>
          </div>
        </th>
      ))}
    </>
  );
});
