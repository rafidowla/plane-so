/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import type { TIssue } from "@plane/types";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { usePropertyValues } from "../../hooks/use-property-values";
import { PROPERTY_CELL_REGISTRY } from "../cells/registry";

type Props = {
  issue: TIssue;
  disabled: boolean;
};

/**
 * Trailing `<td>`s for one spreadsheet row — one per active custom property,
 * resolved through `PROPERTY_CELL_REGISTRY`. Mounted via a single 1-line seam
 * after the built-in column map (`issue-row.tsx`). On mount it enqueues a value
 * fetch; the store debounces and issues one bulk request per batch of newly
 * visible rows (no N+1), which pairs naturally with `RenderIfVisible`.
 *
 * Column count/order is keyed off the URL project (same source as the header)
 * so header and body stay aligned in project-scoped spreadsheets. Returns
 * `null` when the feature is off, keeping the row identical to stock Plane.
 */
export const CustomPropertyValueCells = observer(function CustomPropertyValueCells({ issue, disabled }: Props) {
  const { workspaceSlug, projectId } = useParams();
  const ws = workspaceSlug?.toString();
  const pid = projectId?.toString();
  const { enabled, properties } = useProjectCustomProperties(ws, pid);
  const valuesStore = usePropertyValues();
  const { allowPermissions } = useUserPermissions();

  useEffect(() => {
    if (enabled && ws && pid && issue.id) valuesStore.enqueueValueFetch(ws, pid, issue.id);
  }, [enabled, ws, pid, issue.id, valuesStore]);

  if (!enabled) return null;

  // Matches the admin-only trailing "+" <th> in the header so columns stay aligned.
  const isAdmin = !!ws && !!pid && allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT, ws, pid);

  return (
    <>
      {properties.map((property) => {
        const Cell = PROPERTY_CELL_REGISTRY[property.property_type];
        const values = valuesStore.getValue(issue.id, property.id);
        return (
          <td
            key={property.id}
            tabIndex={0}
            className="h-11 min-w-36 border-r-[1px] border-subtle text-13 after:absolute after:bottom-[-1px] after:w-full after:border after:border-subtle"
          >
            {Cell ? (
              <Cell
                issue={issue}
                property={property}
                values={values}
                disabled={disabled}
                onChange={async (next) => {
                  if (!ws || !pid) return;
                  await valuesStore.setValue(ws, pid, issue.id, property.id, next);
                }}
              />
            ) : null}
          </td>
        );
      })}
      {isAdmin && (
        <td className="h-11 min-w-11 border-r-[1px] border-subtle after:absolute after:bottom-[-1px] after:w-full after:border after:border-subtle" />
      )}
    </>
  );
});
