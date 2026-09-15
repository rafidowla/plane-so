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
import { Dropdown } from "@plane/ui";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { EIssuePropertyType } from "@/plane-web/custom-properties";
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { usePropertyValues } from "../hooks/use-property-values";
import { StatusChip } from "./status-chip";

type Props = {
  issue: TIssue;
};

/**
 * Status chips shown on kanban/list cards, keyed off the card's own project
 * (cards can span projects on a board). Fills the CE layout stub; returns
 * `null` when the feature is off so cards match stock Plane.
 *
 * FORK: PSR-34 — project admins/members can click a chip to change the value
 * inline (same picker as the spreadsheet cell); guests stay read-only.
 */
export const CustomPropertiesCardChips = observer(function CustomPropertiesCardChips({ issue }: Props) {
  const { workspaceSlug } = useParams();
  const ws = workspaceSlug?.toString();
  const pid = issue.project_id ?? undefined;
  const { enabled, properties } = useProjectCustomProperties(ws, pid);
  const valuesStore = usePropertyValues();
  const { allowPermissions } = useUserPermissions();

  useEffect(() => {
    if (enabled && ws && pid && issue.id) valuesStore.enqueueValueFetch(ws, pid, issue.id);
  }, [enabled, ws, pid, issue.id, valuesStore]);

  if (!enabled) return null;

  const optionProperties = properties.filter((property) => property.property_type === EIssuePropertyType.OPTION);

  // FORK: PSR-60 — until the bulk fetch resolves for this issue, hold a
  // placeholder chip instead of blank space, so Task Type labels don't
  // disappear-and-reappear after a reload.
  if (!valuesStore.isValuesKnown(issue.id)) {
    if (optionProperties.length === 0) return null;
    return (
      <div className="flex flex-wrap items-center gap-1" aria-hidden>
        {optionProperties.map((property) => (
          <span key={property.id} className="h-4 w-16 animate-pulse rounded-full bg-layer-1" />
        ))}
      </div>
    );
  }

  const canEdit =
    !!ws &&
    !!pid &&
    allowPermissions([EUserPermissions.ADMIN, EUserPermissions.MEMBER], EUserPermissionsLevel.PROJECT, ws, pid);

  const chips = optionProperties
    .map((property) => {
      const selectedId = valuesStore.getValue(issue.id, property.id)[0];
      const options = [...(property.options ?? [])]
        .filter((o) => o.is_active)
        // oxlint-disable-next-line unicorn/no-array-sort-mutation -- sorting a fresh copy
        .sort((a, b) => a.sort_order - b.sort_order);
      if (!canEdit) {
        if (!selectedId) return null;
        const option = options.find((o) => o.id === selectedId);
        return option ? <StatusChip key={property.id} option={option} /> : null;
      }
      const handleChange = (value: string) => {
        if (!ws || !pid) return;
        // Re-selecting the current option clears it (single-select toggle).
        const next = value === selectedId ? [] : [value];
        void valuesStore.setValue(ws, pid, issue.id, property.id, next);
      };
      return (
        <Dropdown
          key={property.id}
          value={selectedId ?? ""}
          onChange={handleChange}
          options={options.map((o) => ({ data: o, value: o.id }))}
          tabIndex={0}
          keyExtractor={(opt) => opt.value}
          queryArray={["name"]}
          placement="bottom-start"
          inputPlaceholder="Search options"
          buttonContainerClassName="rounded"
          buttonContent={(isOpen, value) => {
            const option = options.find((o) => o.id === value);
            if (!option) {
              return (
                <span className="inline-flex h-4 w-4 items-center justify-center rounded border border-subtle text-[10px] text-tertiary">
                  +
                </span>
              );
            }
            return <StatusChip option={option} className={isOpen ? "ring-1 ring-accent-strong" : undefined} />;
          }}
          renderItem={({ value, selected }) => {
            const option = options.find((o) => o.id === value);
            if (!option) return null;
            return (
              <div className="flex w-full items-center gap-2">
                <StatusChip option={option} />
                {selected && <span className="text-xs text-tertiary">✓</span>}
              </div>
            );
          }}
        />
      );
    })
    .filter(Boolean);

  if (chips.length === 0) return null;

  return <div className="flex flex-wrap items-center gap-1">{chips}</div>;
});
