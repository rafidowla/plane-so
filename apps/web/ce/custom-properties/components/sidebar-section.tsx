/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { PropertyInputRows } from "./property-input-rows";

type Props = {
  workItemId: string;
  workItemTypeId: string | null;
  projectId: string;
  workspaceSlug: string;
  isEditable: boolean;
  isPeekView?: boolean;
};

/**
 * Custom-property rows for the work-item detail sidebar and the peek overview
 * (one CE stub feeds both consumers). Fills B2; the guest/read-only state flows
 * in via `isEditable`.
 */
export const CustomPropertiesSidebarSection = observer(function CustomPropertiesSidebarSection(props: Props) {
  const { workItemId, projectId, workspaceSlug, isEditable } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug || undefined, projectId || undefined);

  // Feature off (or nothing to show) ⇒ render nothing, so the sidebar/peek DOM
  // stays byte-identical to stock Plane. Every seam must gate on `enabled`.
  if (!enabled || !workItemId || !projectId || !workspaceSlug || properties.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <PropertyInputRows
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        workItemId={workItemId}
        disabled={!isEditable}
      />
    </div>
  );
});
