/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — Task Type chip in the create-modal header (improvement #20)
import { observer } from "mobx-react";
// plane imports
import { Dropdown } from "@plane/ui";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { useCreatePropertyStaging } from "../hooks/use-create-property-staging";
import { EMPTY_OPTION_BG } from "../utils/contrast";
import { splitTaskTypeProperty } from "../utils/task-type";
import { StatusChip } from "./status-chip";

type Props = {
  workspaceSlug: string;
  projectId: string | null;
  disabled: boolean;
};

/**
 * Compact Task Type selector rendered next to the project selector at the top
 * of the create work-item modal (improvement #20). Uses the same chip visual
 * language as the modal's default property chips and the same staging +
 * last-used/default prefill as the Options rows (shared via
 * useCreatePropertyStaging), so the picked value is saved unchanged by the
 * modal provider once the work item exists.
 */
export const TaskTypeHeaderSelect = observer(function TaskTypeHeaderSelect(props: Props) {
  const { workspaceSlug, projectId, disabled } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId ?? undefined);
  const { taskTypeProperty } = splitTaskTypeProperty(properties);
  const stagingProperties = taskTypeProperty ? [taskTypeProperty] : [];
  const { getStagedValues, handleChange } = useCreatePropertyStaging(projectId ?? "", stagingProperties);

  if (!enabled || !projectId || !taskTypeProperty) return null;

  const options = (taskTypeProperty.options ?? [])
    .filter((o) => o.is_active)
    .toSorted((a, b) => a.sort_order - b.sort_order);
  if (options.length === 0) return null;

  const values = getStagedValues(taskTypeProperty.id);
  const selectedId = values[0] ?? "";
  const selected = options.find((o) => o.id === selectedId);
  const placeholder = taskTypeProperty.display_name || taskTypeProperty.name;

  return (
    <div className="h-7">
      <Dropdown
        value={selectedId}
        onChange={(value: string) => void handleChange(taskTypeProperty.id, value === selectedId ? [] : [value])}
        options={options.map((o) => ({ data: o, value: o.id }))}
        disabled={disabled}
        keyExtractor={(opt) => opt.value}
        queryArray={["name"]}
        placement="bottom-start"
        inputPlaceholder="Search options"
        buttonContainerClassName="h-full"
        buttonClassName="flex h-full items-center gap-1.5 rounded-sm border-[0.5px] border-strong px-2 py-0.5 text-left text-xs hover:bg-layer-2"
        buttonContent={() =>
          selected ? (
            <StatusChip option={selected} />
          ) : (
            <span className="flex-grow truncate text-tertiary">{placeholder}</span>
          )
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
    </div>
  );
});
