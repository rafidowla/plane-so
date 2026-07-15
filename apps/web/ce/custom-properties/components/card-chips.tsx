/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import type { TIssue } from "@plane/types";
// local imports
import { EIssuePropertyType } from "@/plane-web/custom-properties";
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { usePropertyValues } from "../hooks/use-property-values";
import { StatusChip } from "./status-chip";

type Props = {
  issue: TIssue;
};

/**
 * Read-only Status chips shown on kanban/list cards, keyed off the card's own
 * project (cards can span projects on a board). Fills the CE layout stub;
 * returns `null` when the feature is off so cards match stock Plane.
 */
export const CustomPropertiesCardChips = observer(function CustomPropertiesCardChips({ issue }: Props) {
  const { workspaceSlug } = useParams();
  const ws = workspaceSlug?.toString();
  const pid = issue.project_id ?? undefined;
  const { enabled, properties } = useProjectCustomProperties(ws, pid);
  const valuesStore = usePropertyValues();

  useEffect(() => {
    if (enabled && ws && pid && issue.id) valuesStore.enqueueValueFetch(ws, pid, issue.id);
  }, [enabled, ws, pid, issue.id, valuesStore]);

  if (!enabled) return null;

  const chips = properties
    .filter((property) => property.property_type === EIssuePropertyType.OPTION)
    .map((property) => {
      const selectedId = valuesStore.getValue(issue.id, property.id)[0];
      if (!selectedId) return null;
      const option = (property.options ?? []).find((o) => o.id === selectedId);
      return option ? <StatusChip key={property.id} option={option} /> : null;
    })
    .filter(Boolean);

  if (chips.length === 0) return null;

  return <div className="flex flex-wrap items-center gap-1">{chips}</div>;
});
