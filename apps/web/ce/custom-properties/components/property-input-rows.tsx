/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { usePropertyValues } from "../hooks/use-property-values";
import { PROPERTY_INPUT_REGISTRY } from "./cells/registry";

type Props = {
  workspaceSlug: string;
  projectId: string;
  workItemId: string;
  disabled: boolean;
};

/**
 * Shared "label + input" rows for one work item, used by both the issue modal
 * (edit) and the detail/peek sidebar. Hydrates the item's values on mount and
 * writes each change through the store (optimistic). Renders `null` when the
 * feature is off or there are no properties.
 */
export const PropertyInputRows = observer(function PropertyInputRows(props: Props) {
  const { workspaceSlug, projectId, workItemId, disabled } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId);
  const valuesStore = usePropertyValues();

  useEffect(() => {
    if (enabled && workspaceSlug && projectId && workItemId)
      valuesStore.enqueueValueFetch(workspaceSlug, projectId, workItemId);
  }, [enabled, workspaceSlug, projectId, workItemId, valuesStore]);

  if (!enabled || properties.length === 0) return null;

  return (
    <>
      {properties.map((property) => {
        const Input = PROPERTY_INPUT_REGISTRY[property.property_type];
        if (!Input) return null;
        return (
          <div key={property.id} className="flex min-h-8 items-center gap-2">
            <span className="w-2/5 flex-shrink-0 truncate text-sm text-tertiary">
              {property.display_name || property.name}
            </span>
            <div className="w-3/5">
              <Input
                property={property}
                values={valuesStore.getValue(workItemId, property.id)}
                disabled={disabled}
                onChange={async (next) => {
                  await valuesStore.setValue(workspaceSlug, projectId, workItemId, property.id, next);
                }}
              />
            </div>
          </div>
        );
      })}
    </>
  );
});
