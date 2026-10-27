/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import Link from "next/link";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { ROLE, EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { DeactivatedUserOutline, DeleteOutline, MoreHorizontalOutline } from "@makeplane/propel/icons";
import { Badge } from "@makeplane/propel/components/badge";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@makeplane/propel/components/menu";
import { setToast } from "@plane/blocks/toast";
import type { IUser, IWorkspaceMember } from "@plane/types";
// plane ui
import { Select } from "@plane/blocks/select";
// helpers
import { getFileURL } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUser, useUserPermissions } from "@/hooks/store/user";
import type { TWorkspaceMemberUpdate } from "@/store/member/workspace/workspace-member.store";

export interface RowData {
  member: IWorkspaceMember;
  role: EUserPermissions;
  is_active: boolean;
  is_super_admin?: boolean; // FORK: PSR-125
}

type NameProps = {
  rowData: RowData;
  workspaceSlug: string;
  isAdmin: boolean;
  isSuperAdmin?: boolean; // FORK: PSR-125
  currentUser: IUser | undefined;
  setRemoveMemberModal: (rowData: RowData) => void;
};

// FORK: PSR-125 -- "super_admin" is Admin + the Super Admin flag; the API stores it that way.
type TRoleKey = EUserPermissions | "super_admin";
type TRoleOption = { key: TRoleKey; label: string };

const SUPER_ADMIN_KEY = "super_admin" as const;

const roleKeyOf = (row: { role: EUserPermissions; is_super_admin?: boolean }): TRoleKey =>
  row.is_super_admin && row.role === EUserPermissions.ADMIN ? SUPER_ADMIN_KEY : row.role;

// The PATCH body for moving a member to a role key. Leaving Super Admin is sent explicitly.
const updateFor = (key: TRoleKey, wasSuperAdmin: boolean): TWorkspaceMemberUpdate => {
  if (key === SUPER_ADMIN_KEY) return { role: EUserPermissions.ADMIN, is_super_admin: true };
  return wasSuperAdmin ? { role: key, is_super_admin: false } : { role: key };
};

type AccountTypeProps = {
  rowData: RowData;
  workspaceSlug: string;
};

export function NameColumn(props: NameProps) {
  const { rowData, workspaceSlug, isAdmin, isSuperAdmin = false, currentUser, setRemoveMemberModal } = props;
  // plane hooks
  const { t } = useTranslation();
  // derived values
  const { avatar_url, display_name, email, first_name, id, last_name } = rowData.member;
  const isSuspended = rowData.is_active === false;
  // FORK: PSR-125 -- only a Super Admin can remove an Admin; the API refuses it otherwise.
  const canRemove = isAdmin && (isSuperAdmin || rowData.role !== EUserPermissions.ADMIN);

  return (
    <div className="group relative">
      <div className="flex w-72 items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-1 items-center gap-x-2 gap-y-2">
          {isSuspended ? (
            <div className="rounded-full bg-layer-1">
              <DeactivatedUserOutline className="size-6 text-placeholder" />
            </div>
          ) : avatar_url && avatar_url.trim() !== "" ? (
            <Link href={`/${workspaceSlug}/profile/${id}`}>
              <span className="relative flex size-6 items-center justify-center rounded-full text-on-color capitalize">
                <img
                  src={getFileURL(avatar_url)}
                  className="absolute top-0 left-0 h-full w-full rounded-full object-cover"
                  alt={display_name || email}
                />
              </span>
            </Link>
          ) : (
            <Link href={`/${workspaceSlug}/profile/${id}`}>
              <span className="relative flex size-6 items-center justify-center rounded-full bg-layer-3 text-11 text-tertiary capitalize">
                {(email ?? display_name ?? "?")[0]}
              </span>
            </Link>
          )}
          <span className={isSuspended ? "text-placeholder" : ""}>
            {first_name} {last_name}
          </span>
        </div>

        {!isSuspended && (canRemove || id === currentUser?.id) && (
          <div className="opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 has-[[data-popup-open]]:opacity-100">
            <Menu>
              <MenuTrigger
                render={
                  <IconButton
                    variant="ghost"
                    size="sm"
                    aria-label={t("aria_labels.common.more_actions")}
                    icon={<Icon icon={MoreHorizontalOutline} />}
                  />
                }
              />
              <MenuContent side="bottom" align="end">
                <MenuItem
                  icon={<Icon icon={DeleteOutline} />}
                  label={id === currentUser?.id ? t("leave") : t("remove")}
                  onClick={() => setRemoveMemberModal(rowData)}
                />
              </MenuContent>
            </Menu>
          </div>
        )}
      </div>
    </div>
  );
}

export const AccountTypeColumn = observer(function AccountTypeColumn(props: AccountTypeProps) {
  const { rowData, workspaceSlug } = props;
  // form info
  const { control } = useForm();
  // store hooks
  const { t } = useTranslation();
  const { allowPermissions, workspaceUserInfo } = useUserPermissions();

  const {
    workspace: { updateMember },
  } = useMember();
  const { data: currentUser } = useUser();

  // derived values
  const isCurrentUser = currentUser?.id === rowData.member.id;
  const isAdminRole = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  // FORK: PSR-125 -- a plain Admin can only move Members and Guests between those two roles;
  // a Super Admin can set any role, including Super Admin.
  const viewerIsSuperAdmin = !!workspaceUserInfo[workspaceSlug]?.is_super_admin;
  const rowKey = roleKeyOf(rowData);
  const rowIsAdmin = rowData.role === EUserPermissions.ADMIN;
  const isRoleNonEditable = isCurrentUser || !isAdminRole || (rowIsAdmin && !viewerIsSuperAdmin);
  const isSuspended = rowData.is_active === false;
  const superAdminLabel = t("workspace_settings.settings.members.super_admin");
  const labelOf = (key: TRoleKey) => (key === SUPER_ADMIN_KEY ? superAdminLabel : ROLE[key]);
  const keys: TRoleKey[] = viewerIsSuperAdmin
    ? [SUPER_ADMIN_KEY, EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST]
    : [EUserPermissions.MEMBER, EUserPermissions.GUEST];
  const options: TRoleOption[] = keys.map((key) => ({ key, label: labelOf(key) }));

  return (
    <>
      {isSuspended ? (
        <div className="flex w-32">
          <Badge variant="neutral" size="sm" label="Suspended" />
        </div>
      ) : isRoleNonEditable ? (
        <div className="flex w-32">
          <span>{labelOf(rowKey)}</span>
        </div>
      ) : (
        <Controller
          name="role"
          control={control}
          rules={{ required: "Role is required." }}
          render={() => (
            <div className="w-32">
              <Select<TRoleOption>
                value={options.find((role) => role.key === rowKey) ?? null}
                onChange={(val) => {
                  if (!workspaceSlug) return;
                  const key: TRoleKey =
                    String(val) === SUPER_ADMIN_KEY ? SUPER_ADMIN_KEY : (Number(val) as EUserPermissions);
                  if (key === rowKey) return;
                  updateMember(
                    workspaceSlug.toString(),
                    rowData.member.id,
                    updateFor(key, rowKey === SUPER_ADMIN_KEY)
                  ).catch((err: unknown) => {
                    const error = err as { error?: string | string[] };
                    const errorString = Array.isArray(error?.error) ? error.error[0] : error?.error;

                    setToast({
                      type: "error",
                      title: "Error!",
                      message: errorString ?? "An error occurred while updating member role. Please try again.",
                    });
                  });
                }}
                getValues={() => options}
                getOptionValue={(role) => String(role.key)}
                getOptionLabel={(role) => role.label}
                showSearch={false}
                pinSelected={false}
              >
                <Select.Trigger<TRoleOption> variant="select-ghost-md">
                  <span className="min-w-0 flex-1 truncate text-left">{labelOf(rowKey)}</span>
                </Select.Trigger>
              </Select>
            </div>
          )}
        />
      )}
    </>
  );
});
