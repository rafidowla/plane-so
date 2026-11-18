/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- one row of the Projects List view. Role checks, Join flow, favourite toggle and archive/restore
// mirror ProjectCard (card.tsx).

import { useState } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArchiveOutline,
  DeleteOutline,
  LinkOutline,
  LockOutline,
  MoreHorizontalOutline,
  NewTabOutline,
  RestoreOutline,
  SettingsOutline,
  UserPlusOutline,
} from "@makeplane/propel/icons";
import { Avatar } from "@makeplane/propel/components/avatar";
import { Button } from "@makeplane/propel/components/button";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@makeplane/propel/components/menu";
import { Logo } from "@plane/blocks/emoji-icon-picker";
import { FavoriteStar } from "@plane/blocks/common";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { IProject } from "@plane/types";
import { cn, getFileURL, getProjectLeadId } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useProjectCategory } from "@/hooks/store/use-project-category";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// local imports
import { ArchiveRestoreProjectModal } from "./archive-restore-modal";
import { DeleteProjectModal } from "./delete-project-modal";
import { JoinProjectModal } from "./join-project-modal";
import { useProjectSharedActions } from "./use-project-shared-actions";

type Props = {
  project: IProject;
};

type TMenuItem = { key: string; title: string; icon: React.FC<React.SVGProps<SVGSVGElement>>; action: () => void };

const CELL_CLASSNAME = "border-b border-subtle px-3 py-2";

export const ProjectListRow = observer(function ProjectListRow(props: Props) {
  const { project } = props;
  // states
  const [deleteProjectModalOpen, setDeleteProjectModal] = useState(false);
  const [joinProjectModalOpen, setJoinProjectModal] = useState(false);
  const [archiveModalOpen, setArchiveModal] = useState(false);
  const [restoreModalOpen, setRestoreModal] = useState(false);
  // router
  const router = useAppRouter();
  const { workspaceSlug: routeSlug } = useParams();
  const workspaceSlug = routeSlug?.toString();
  // i18n
  const { t } = useTranslation();
  // store hooks
  const { getUserDetails } = useMember();
  const { getCategoryById } = useProjectCategory();
  const { allowPermissions } = useUserPermissions();
  const { handleAddToFavorites, handleRemoveFromFavorites, handleCopyLink } = useProjectSharedActions(
    workspaceSlug,
    project.id
  );
  // derived values (same role decisions as ProjectCard)
  const shouldRenderFavorite = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );
  const isMemberOfProject = !!project.member_role;
  // workspace Guests cannot join projects (the API answers 403), so no Join actions
  const canJoinProject = !allowPermissions([EUserPermissions.GUEST], EUserPermissionsLevel.WORKSPACE);
  const hasAdminRole = project.member_role === EUserPermissions.ADMIN;
  const hasMemberRole = project.member_role === EUserPermissions.MEMBER;
  const isArchived = !!project.archived_at;
  const leadId = getProjectLeadId(project);
  const lead = leadId ? getUserDetails(leadId) : undefined;
  const categoryName = project.category_id ? getCategoryById(project.category_id)?.name : undefined;
  const projectPath = `/${workspaceSlug}/projects/${project.id}/issues`;

  const menuItems: TMenuItem[] = [];
  if (isMemberOfProject && !isArchived) {
    menuItems.push({
      key: "open",
      title: t("workspace_projects.list_view.open"),
      icon: NewTabOutline,
      action: () => router.push(projectPath),
    });
    if (hasAdminRole || hasMemberRole) {
      menuItems.push(
        {
          key: "settings",
          title: t("workspace_projects.list_view.settings"),
          icon: SettingsOutline,
          action: () => router.push(`/${workspaceSlug}/settings/projects/${project.id}`),
        },
        {
          key: "members",
          title: t("workspace_projects.list_view.members"),
          icon: UserPlusOutline,
          action: () => router.push(`/${workspaceSlug}/settings/projects/${project.id}/members`),
        }
      );
    }
    if (hasAdminRole)
      menuItems.push({
        key: "archive",
        title: t("workspace_projects.list_view.archive"),
        icon: ArchiveOutline,
        action: () => setArchiveModal(true),
      });
  }
  if (isArchived && hasAdminRole)
    menuItems.push(
      {
        key: "restore",
        title: t("workspace_projects.list_view.restore"),
        icon: RestoreOutline,
        action: () => setRestoreModal(true),
      },
      { key: "delete", title: t("common.delete"), icon: DeleteOutline, action: () => setDeleteProjectModal(true) }
    );

  const nameContent = (
    <>
      <span className="grid size-7 flex-shrink-0 place-items-center rounded-sm bg-layer-1">
        <Logo logo={project.logo_props} size={16} />
      </span>
      <span className="truncate font-medium text-primary">{project.name}</span>
      {project.network === 0 && <LockOutline className="size-3 flex-shrink-0 text-tertiary" />}
    </>
  );

  return (
    <>
      <DeleteProjectModal
        project={project}
        isOpen={deleteProjectModalOpen}
        onClose={() => setDeleteProjectModal(false)}
      />
      {workspaceSlug && (
        <>
          <JoinProjectModal
            workspaceSlug={workspaceSlug}
            project={project}
            isOpen={joinProjectModalOpen}
            handleClose={() => setJoinProjectModal(false)}
          />
          <ArchiveRestoreProjectModal
            workspaceSlug={workspaceSlug}
            projectId={project.id}
            isOpen={archiveModalOpen}
            onClose={() => setArchiveModal(false)}
            archive
          />
          <ArchiveRestoreProjectModal
            workspaceSlug={workspaceSlug}
            projectId={project.id}
            isOpen={restoreModalOpen}
            onClose={() => setRestoreModal(false)}
            archive={false}
          />
        </>
      )}
      <tr className={cn("hover:bg-layer-1-hover", { "opacity-90": isArchived })}>
        {/* favourite */}
        <td className={cn(CELL_CLASSNAME, "pl-5")}>
          {shouldRenderFavorite && !isArchived && (
            <FavoriteStar
              aria-label={
                project.is_favorite
                  ? t("workspace_projects.list_view.favorite_remove")
                  : t("workspace_projects.list_view.favorite_add")
              }
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (project.is_favorite) handleRemoveFromFavorites();
                else handleAddToFavorites();
              }}
              selected={!!project.is_favorite}
            />
          )}
        </td>
        {/* name: links like the card; non-members get the Join flow, archived projects do not navigate */}
        <td className={CELL_CLASSNAME}>
          {isMemberOfProject && !isArchived ? (
            <Link href={projectPath} className="flex min-w-0 items-center gap-2.5">
              {nameContent}
            </Link>
          ) : (
            <button
              type="button"
              className="flex min-w-0 items-center gap-2.5 text-left"
              disabled={isArchived || !canJoinProject}
              onClick={() => setJoinProjectModal(true)}
            >
              {nameContent}
              {isArchived && <span className="text-11 text-placeholder">Archived</span>}
            </button>
          )}
        </td>
        {/* key */}
        <td className={cn(CELL_CLASSNAME, "text-secondary")}>{project.identifier}</td>
        {/* lead */}
        <td className={CELL_CLASSNAME}>
          {lead ? (
            <span className="flex items-center gap-2 text-secondary">
              <Avatar
                alt={lead.display_name}
                fallback={lead.display_name?.[0]?.toUpperCase()}
                src={getFileURL(lead.avatar_url)}
                size="xs"
              />
              <span className="truncate">{lead.display_name}</span>
            </span>
          ) : (
            <span className="text-placeholder">—</span>
          )}
        </td>
        {/* category */}
        <td className={cn(CELL_CLASSNAME, "text-secondary")}>
          {categoryName ?? <span className="text-placeholder">—</span>}
        </td>
        {/* project url */}
        <td className={CELL_CLASSNAME}>
          {!isArchived && (
            <IconButton
              variant="ghost"
              size="sm"
              icon={<Icon icon={LinkOutline} />}
              aria-label={t("workspace_projects.list_view.copy_url")}
              onClick={() => void handleCopyLink()}
            />
          )}
        </td>
        {/* actions */}
        <td className={cn(CELL_CLASSNAME, "pr-5")}>
          {!isMemberOfProject && !isArchived && canJoinProject ? (
            <Button
              variant="ghost"
              size="sm"
              stretch="auto"
              label={t("workspace_projects.list_view.join")}
              onClick={() => setJoinProjectModal(true)}
            />
          ) : (
            menuItems.length > 0 && (
              <Menu>
                <MenuTrigger
                  render={
                    <IconButton
                      variant="ghost"
                      size="sm"
                      icon={<Icon icon={MoreHorizontalOutline} />}
                      aria-label={t("workspace_projects.list_view.col_actions")}
                    />
                  }
                />
                <MenuContent side="bottom" align="end">
                  {menuItems.map((item) => (
                    <MenuItem
                      key={item.key}
                      icon={<Icon icon={item.icon} />}
                      label={item.title}
                      onClick={item.action}
                    />
                  ))}
                </MenuContent>
              </Menu>
            )
          )}
        </td>
      </tr>
    </>
  );
});
