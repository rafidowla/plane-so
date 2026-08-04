/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — create-time staging with default/last-used prefill (improvements #19, #20)
import { observer } from "mobx-react";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { useCreatePropertyStaging } from "../hooks/use-create-property-staging";
import { splitTaskTypeProperty } from "../utils/task-type";
import { PROPERTY_INPUT_REGISTRY } from "./cells/registry";

type Props = {
  workspaceSlug: string;
  projectId: string;
  disabled: boolean;
};

/**
 * Create-mode counterpart to PropertyInputRows: there is no work item yet, so
 * values are staged on the IssueModalContext and saved by the provider's
 * `handleCreateUpdatePropertyValues` once the work item exists. Staging,
 * prefill (last-used → default) and touched-tracking live in the shared
 * useCreatePropertyStaging hook (improvements #19, #20).
 *
 * The Task Type property is promoted to the modal header (TaskTypeHeaderSelect,
 * improvement #20) and is therefore excluded from these Options rows.
 */
export const CreatePropertyInputRows = observer(function CreatePropertyInputRows(props: Props) {
  const { workspaceSlug, projectId, disabled } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId);
  const { otherProperties } = splitTaskTypeProperty(properties);
  const { getStagedValues, handleChange } = useCreatePropertyStaging(projectId, otherProperties);

  if (!enabled || otherProperties.length === 0) return null;

  return (
    <>
      {otherProperties.map((property) => {
        const Input = PROPERTY_INPUT_REGISTRY[property.property_type];
        if (!Input) return null;
        return (
          <div key={property.id} className="flex min-h-8 items-center gap-2">
            <span className="text-sm w-2/5 flex-shrink-0 truncate text-tertiary">
              {property.display_name || property.name}
            </span>
            <div className="w-3/5">
              <Input
                property={property}
                values={getStagedValues(property.id)}
                disabled={disabled}
                onChange={async (next) => handleChange(property.id, next)}
              />
            </div>
          </div>
        );
      })}
    </>
  );
});
