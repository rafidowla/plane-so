/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/blocks/empty-state";

type TProps = {
  isWorkspaceAdmin: boolean;
  /** FORK: guest-access PSR-119 — a workspace Guest cannot join projects (the API answers 403), so no Join button */
  isWorkspaceGuest?: boolean;
  handleJoinProject: () => void;
  isJoinButtonDisabled: boolean;
  errorStatusCode: number | undefined;
};

export const ProjectAccessRestriction = observer(function ProjectAccessRestriction(props: TProps) {
  const {
    isWorkspaceAdmin,
    isWorkspaceGuest = false,
    handleJoinProject,
    isJoinButtonDisabled,
    errorStatusCode,
  } = props;
  // plane hooks
  const { t } = useTranslation();

  // Show join project screen if:
  // - User lacks project membership (409 Conflict)
  // - User lacks permission to access the private project (403 Forbidden) but is a workspace admin (can join any project)
  // FORK: guest-access PSR-119 — never for a workspace Guest: they fall through to the "no access" screen below
  if (!isWorkspaceGuest && (errorStatusCode === 409 || (errorStatusCode === 403 && isWorkspaceAdmin)))
    return (
      <div className="grid h-full w-full place-items-center bg-surface-1">
        <EmptyStateDetailed
          title={t("project_empty_state.no_access.title")}
          description={t("project_empty_state.no_access.join_description")}
          assetKey="no-access"
          assetClassName="size-40"
          actions={[
            {
              label: isJoinButtonDisabled
                ? t("project_empty_state.no_access.cta_loading")
                : t("project_empty_state.no_access.cta_primary"),
              onClick: handleJoinProject,
              disabled: isJoinButtonDisabled,
            },
          ]}
        />
      </div>
    );

  // Show no access screen if:
  // - User lacks permission to access the private project (403 Forbidden)
  // - FORK: guest-access PSR-119 — a workspace Guest is not a member of the project (409 Conflict) and cannot join
  if (errorStatusCode === 403 || (isWorkspaceGuest && errorStatusCode === 409)) {
    return (
      <div className="grid h-full w-full place-items-center bg-surface-1">
        <EmptyStateDetailed
          title={t("project_empty_state.no_access.title")}
          description={t("project_empty_state.no_access.restricted_description")}
          assetKey="no-access"
          assetClassName="size-40"
        />
      </div>
    );
  }

  // Show empty state screen if:
  // - Project not found (404 Not Found)
  // - Any other error status code
  return (
    <div className="grid h-full w-full place-items-center bg-surface-1">
      <EmptyStateDetailed
        title={t("project_empty_state.invalid_project.title")}
        description={t("project_empty_state.invalid_project.description")}
        assetKey="project"
        assetClassName="size-40"
      />
    </div>
  );
});
