/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { PropertyInputRows } from "./property-input-rows";

type Props = {
  isDraft?: boolean;
  projectId: string | null;
  workItemId: string | undefined;
  workspaceSlug: string;
};

/**
 * Custom-property inputs inside the create/edit work-item modal. Fills B1.
 *
 * v1 renders only in EDIT mode — the modal passes `workItemId` only once the
 * item exists (`data?.id`), so on create the section is absent and users set
 * custom properties immediately after (sidebar/spreadsheet). This avoids
 * coupling to the modal's submit pipeline (an EE-only provider hook), keeping
 * the seam a pure stub delegation. Create-time staging is a documented
 * follow-up.
 */
export const CustomPropertiesModalSection = observer(function CustomPropertiesModalSection(props: Props) {
  const { projectId, workItemId, workspaceSlug } = props;
  const { t } = useTranslation();
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId ?? undefined);

  // Create mode (no work-item id yet) or feature off ⇒ render nothing.
  if (!enabled || !workItemId || !projectId || properties.length === 0) return null;

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-subtle pt-3">
      <span className="text-xs font-medium text-secondary">{t("custom_properties.options")}</span>
      <PropertyInputRows
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        workItemId={workItemId}
        disabled={false}
      />
    </div>
  );
});
