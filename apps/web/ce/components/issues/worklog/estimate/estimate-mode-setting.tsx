/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6 — project settings > Time tracking: how estimates are handled in this project.
// Project admins only (the page itself is admin-only). Stored as `Project.time_estimate_mode` through the
// ordinary project PATCH.
import { useState } from "react";
import { observer } from "mobx-react";
import { Select, SelectContent, SelectItem, SelectList, SelectTrigger } from "@makeplane/propel/components/select";
import { setToast } from "@plane/blocks/toast";
import type { IProject } from "@plane/types";
import { SettingsBoxedControlItem } from "@/components/settings/boxed-control-item";
import { useProject } from "@/hooks/store/use-project";
import type { TTimeEstimateMode } from "../types";

export const ESTIMATE_MODE_OPTIONS: { value: TTimeEstimateMode; label: string; help: string }[] = [
  { value: "off", label: "Off", help: "Work items have no estimate." },
  { value: "optional", label: "Optional", help: "People can add an estimate to a work item. Nothing is blocked." },
  {
    value: "required",
    label: "Required",
    help: "A work item needs an estimate before work starts. Without one, moving it to a started or completed state, starting a timer, logging time and approving missing time are all refused with “Add an estimate first”. Creating and cancelling work items are not blocked, and items that were already started when you turn this on are exempt.",
  },
];

type TProps = { workspaceSlug: string; projectId: string };

export const EstimateModeSetting = observer(function EstimateModeSetting({ workspaceSlug, projectId }: TProps) {
  const { getProjectById, updateProject } = useProject();
  const [saving, setSaving] = useState(false);
  const project = getProjectById(projectId) as ({ time_estimate_mode?: TTimeEstimateMode } & object) | undefined;
  const value: TTimeEstimateMode = project?.time_estimate_mode ?? "off";
  const selected = ESTIMATE_MODE_OPTIONS.find((o) => o.value === value) ?? ESTIMATE_MODE_OPTIONS[0];

  const change = async (next: TTimeEstimateMode | null) => {
    if (!next || next === value || saving) return;
    setSaving(true);
    try {
      await updateProject(workspaceSlug, projectId, { time_estimate_mode: next } as Partial<IProject>);
      setToast({ type: "success", title: "Estimates updated", message: `Estimates are now ${next}.` });
    } catch {
      setToast({ type: "error", title: "Error", message: "Could not change the estimate setting. Try again." });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="estimate-mode-setting" data-mode={value}>
      <SettingsBoxedControlItem
        title="Estimates"
        description={
          <>
            <span className="block">Whether work items carry a time estimate (in hours).</span>
            <span className="mt-1 block" data-testid="estimate-mode-help">
              {selected.help}
            </span>
          </>
        }
        control={
          <Select<TTimeEstimateMode>
            value={value}
            onValueChange={(next) => void change(next)}
            items={ESTIMATE_MODE_OPTIONS.map(({ value: v, label }) => ({ value: v, label }))}
            disabled={saving}
          >
            <SelectTrigger size="lg" aria-label="Estimates" data-testid="estimate-mode-select" />
            <SelectContent side="bottom" align="end">
              <SelectList>
                {ESTIMATE_MODE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value} size="lg" label={o.label} />
                ))}
              </SelectList>
            </SelectContent>
          </Select>
        }
      />
    </div>
  );
});
