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
import { splitTaskTypeProperty } from "../utils/task-type";
import { CreatePropertyInputRows } from "./create-property-input-rows";
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
 * EDIT mode (workItemId present) binds inputs to the property-values store.
 * CREATE mode stages values on the IssueModalContext — prefilled from the
 * user's last-used value or the configured default (improvement #19) — and the
 * modal provider saves them once the work item exists.
 */
export const CustomPropertiesModalSection = observer(function CustomPropertiesModalSection(props: Props) {
  const { projectId, workItemId, workspaceSlug } = props;
  const { t } = useTranslation();
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId ?? undefined);

  // Improvement #20: in create mode the Task Type property moves to the modal
  // header; when it is the only property, the Options section renders nothing
  // (no leftover whitespace).
  const { otherProperties } = splitTaskTypeProperty(properties);
  const hasCreateRows = !!workItemId || otherProperties.length > 0;

  // Feature off ⇒ render nothing (create mode is handled below).
  if (!enabled || !projectId || properties.length === 0 || !hasCreateRows) return null;

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-subtle pt-3">
      <span className="text-xs font-medium text-secondary">{t("custom_properties.options")}</span>
      {workItemId ? (
        <PropertyInputRows
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          workItemId={workItemId}
          disabled={false}
        />
      ) : (
        <CreatePropertyInputRows workspaceSlug={workspaceSlug} projectId={projectId} disabled={false} />
      )}
    </div>
  );
});
