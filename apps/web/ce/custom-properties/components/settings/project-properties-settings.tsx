/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { ToggleSwitch } from "@plane/ui";
import { useUserPermissions } from "@/hooks/store/user";
import { useCustomProperties } from "@/plane-web/custom-properties/hooks/use-custom-properties";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

export const ProjectPropertiesSettings = observer(function ProjectPropertiesSettings({ workspaceSlug, projectId }: Props) {
  const { t } = useTranslation();
  const store = useCustomProperties();
  const { allowPermissions } = useUserPermissions();
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT, workspaceSlug, projectId);

  const { data } = useSWR(
    workspaceSlug && projectId ? `CUSTOM_PROPERTIES_FEATURE_${workspaceSlug}_${projectId}` : null,
    workspaceSlug && projectId ? () => store.fetchFeature(workspaceSlug, projectId) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  const instanceEnabled = data?.instance_enabled ?? false;
  const isEnabled = store.isFeatureEnabled(projectId);

  const handleToggle = async () => {
    try {
      await store.toggleFeature(workspaceSlug, projectId, !isEnabled);
    } catch (error: unknown) {
      const message = (error as { error?: string })?.error ?? "Could not update the setting.";
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message });
    }
  };

  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-subtle p-4">
      <div className="min-w-0">
        <h4 className="text-sm font-medium">{t("project_settings.features.custom_properties.toggle_title")}</h4>
        <p className="text-xs text-tertiary">{t("project_settings.features.custom_properties.toggle_description")}</p>
        {!instanceEnabled && (
          <p className="text-xs mt-1 text-amber-600">
            {t("project_settings.features.custom_properties.instance_disabled")}
          </p>
        )}
      </div>
      <ToggleSwitch value={isEnabled} onChange={handleToggle} disabled={!isAdmin || !instanceEnabled} />
    </div>
  );
});
