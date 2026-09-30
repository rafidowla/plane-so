/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — Task Type chip in the create-modal header (improvement #20)
import { observer } from "mobx-react";
// plane imports
import { Select } from "@plane/blocks/select";
// local imports
import type { TIssuePropertyOption } from "@/plane-web/custom-properties";
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
    // oxlint-disable-next-line no-array-sort — matches status-input.tsx; toSorted needs es2023 lib
    .sort((a, b) => a.sort_order - b.sort_order);
  if (options.length === 0) return null;

  const values = getStagedValues(taskTypeProperty.id);
  const selectedId = values[0] ?? "";
  const selected = options.find((o) => o.id === selectedId);
  const placeholder = taskTypeProperty.display_name || taskTypeProperty.name;

  return (
    <div className="h-7">
      <Select<TIssuePropertyOption>
        value={selected ?? null}
        onChange={(value) => void handleChange(taskTypeProperty.id, value === selectedId ? [] : [value])}
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
          className="text-xs h-full w-auto gap-1.5 rounded-sm border-[0.5px] border-strong px-2 py-0.5 text-left hover:bg-layer-2"
        >
          {(sel) =>
            sel[0] ? (
              <>
                <span className="shrink-0 text-tertiary">{placeholder}:</span>
                <StatusChip option={sel[0]} />
              </>
            ) : (
              <span className="flex-grow truncate text-tertiary">{placeholder}</span>
            )
          }
        </Select.Trigger>
      </Select>
    </div>
  );
});
