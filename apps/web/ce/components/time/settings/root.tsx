/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — the Time area's Settings tab (workspace admins only; the tab list hides
// it from everyone else and the API refuses non-admins anyway).
import { observer } from "mobx-react";
import { Loader } from "@plane/blocks/skeleton";
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useTimeSettings } from "../use-time-settings";
import { HolidaysSettings } from "./holidays";
import { LeaveSettings } from "./leave";
import { MembersSettings } from "./members";
import { TimeSettingsReadOnlyContext } from "./read-only";
import { RulesForm } from "./rules-form";
import { settingsErrorMessage } from "./settings-utils";

export const SettingsTab = observer(function SettingsTab() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { data, error, isLoading } = useTimeSettings(workspaceSlug);
  // FORK: PSR-125 — only a Super Admin edits Time and Renewals; plain Admins get a read-only view
  const { workspaceUserInfo } = useUserPermissions();
  const readOnly = !workspaceUserInfo[workspaceSlug]?.is_super_admin;

  if (!workspaceSlug) return null;

  return (
    <TimeSettingsReadOnlyContext.Provider value={readOnly}>
      <div className="flex flex-col gap-8 px-4 py-4" data-testid="time-settings">
        {readOnly && (
          <div
            role="status"
            data-testid="time-settings-read-only"
            className="text-sm rounded border border-subtle bg-layer-1 px-3 py-2 text-tertiary"
          >
            Only a Super Admin can change these settings. You can view them.
          </div>
        )}
        {error && !data ? (
          <div role="alert" className="text-sm text-danger-primary">
            {settingsErrorMessage(error, "Couldn't load the time settings.")}
          </div>
        ) : isLoading || !data ? (
          <Loader className="space-y-3">
            <Loader.Item height="40px" />
            <Loader.Item height="240px" />
          </Loader>
        ) : (
          <RulesForm key={data.updated_at} workspaceSlug={workspaceSlug} settings={data} />
        )}
        <MembersSettings workspaceSlug={workspaceSlug} />
        {/* FORK: time-tracking-v2 holidays/leave (PSR-122) */}
        <HolidaysSettings workspaceSlug={workspaceSlug} />
        <LeaveSettings workspaceSlug={workspaceSlug} />
      </div>
    </TimeSettingsReadOnlyContext.Provider>
  );
});
