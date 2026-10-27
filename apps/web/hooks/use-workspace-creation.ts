/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- who may create a workspace. The instance switch still wins; otherwise only
// instance admins and Super Admins of a live workspace can (server-computed `can_create_workspace`).
// The API enforces the same rule, so this only decides what the UI offers.

import { useInstance } from "@/hooks/store/use-instance";
import { useUserSettings } from "@/hooks/store/user";

export type TWorkspaceCreationBlock = "instance" | "super_admin" | null;

export const useWorkspaceCreation = (): { canCreate: boolean; blockedBy: TWorkspaceCreationBlock } => {
  const { config } = useInstance();
  const { data: userSettings } = useUserSettings();

  if (config?.is_workspace_creation_disabled) return { canCreate: false, blockedBy: "instance" };
  if (userSettings?.can_create_workspace !== true) return { canCreate: false, blockedBy: "super_admin" };
  return { canCreate: true, blockedBy: null };
};
