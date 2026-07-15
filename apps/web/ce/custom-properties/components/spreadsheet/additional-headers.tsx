/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Plus } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { ColumnTypePicker } from "../column-type-picker";

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
  const ws = workspaceSlug?.toString();
  const pid = projectId?.toString();
  const { enabled, properties } = useProjectCustomProperties(ws, pid);
  const { allowPermissions } = useUserPermissions();

  if (!enabled) return null;

  const isAdmin = !!ws && !!pid && allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT, ws, pid);

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
      {isAdmin && ws && pid && (
        <th className="h-11 min-w-11 items-center border border-t-0 border-b-0 border-subtle bg-layer-1 py-1 text-13 font-medium">
          <div className="flex h-full w-full items-center justify-center">
            <ColumnTypePicker
              workspaceSlug={ws}
              projectId={pid}
              menuPlacement="right"
              triggerContent={<Plus className="size-4" />}
              triggerClassName="rounded p-1 hover:bg-layer-2"
            />
          </div>
        </th>
      )}
    </>
  );
});
