/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { ArrowNarrowLeftOutline } from "@makeplane/propel/icons";
import { observer } from "mobx-react";
// plane imports
import { ROLE_DETAILS } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Icon } from "@makeplane/propel/components/icon";
// components
import { WorkspaceLogo } from "@/components/workspace/logo";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
import { useWorkspace } from "@/hooks/store/use-workspace";

export const WorkspaceSettingsSidebarHeader = observer(function WorkspaceSettingsSidebarHeader() {
  // router
  const router = useAppRouter();
  // store hooks
  const { getWorkspaceRoleByWorkspaceSlug, workspaceUserInfo } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  // derived values
  const currentWorkspaceRole = currentWorkspace?.slug
    ? getWorkspaceRoleByWorkspaceSlug(currentWorkspace.slug)
    : undefined;
  // translation
  const { t } = useTranslation();
  // FORK: PSR-125 -- a Super Admin is an Admin with a flag; show the Super Admin title.
  const isSuperAdmin = !!(currentWorkspace?.slug && workspaceUserInfo[currentWorkspace.slug]?.is_super_admin);

  if (!currentWorkspaceRole) return null;

  return (
    <div className="shrink-0">
      <div className="flex items-center gap-1 py-3 pr-5 pl-4 text-body-md-medium">
        <IconButton
          variant="ghost"
          size="sm"
          icon={<Icon icon={ArrowNarrowLeftOutline} />}
          aria-label={t("back_to_workspace")}
          onClick={() => router.push(`/${currentWorkspace?.slug}/`)}
        />
        <p>Workspace settings</p>
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 px-5 py-0.5">
        <div className="flex items-center gap-2 truncate">
          <WorkspaceLogo
            logo={currentWorkspace?.logo_url}
            name={currentWorkspace?.name}
            classNames="shrink-0 size-8 border border-subtle"
          />
          <div className="truncate">
            <p className="truncate text-body-sm-medium">{currentWorkspace?.name}</p>
            <p className="truncate text-caption-md-regular">
              {isSuperAdmin
                ? t("workspace_settings.settings.members.super_admin")
                : t(ROLE_DETAILS[currentWorkspaceRole].i18n_title)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
});
