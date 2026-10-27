/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- workspaces the user deleted (or was a Super Admin of) that can still be
// restored, until they are removed for good. A taken web address asks for a new one.

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { setToast } from "@plane/blocks/toast";
import { renderFormattedDate } from "@plane/utils";
// components
import { ProfileSettingsHeading } from "@/components/settings/profile/heading";
import { WorkspaceLogo } from "@/components/workspace/logo";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserSettings } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import type { TDeletedWorkspace } from "@/services/workspace.service";
import { WorkspaceService } from "@/services/workspace.service";

const workspaceService = new WorkspaceService();

export const DELETED_WORKSPACES_KEY = "USER_DELETED_WORKSPACES";

const PREFIX = "account_settings.deleted_workspaces";

const ERROR_KEYS: Record<string, string> = {
  DELETE_IN_PROGRESS: `${PREFIX}.errors.delete_in_progress`,
  SLUG_INVALID: `${PREFIX}.errors.slug_invalid`,
  SLUG_TAKEN: `${PREFIX}.errors.slug_taken`,
  RESTORE_CONFLICT: `${PREFIX}.errors.restore_conflict`,
};

type TApiError = { error?: string; error_code?: string } | undefined;

const DeletedWorkspaceRow = observer(function DeletedWorkspaceRow(props: {
  workspace: TDeletedWorkspace;
  onRestored: (slug: string) => Promise<void>;
}) {
  const { workspace, onRestored } = props;
  const { t } = useTranslation();
  // state
  const [needsNewSlug, setNeedsNewSlug] = useState(!workspace.is_slug_available);
  const [slug, setSlug] = useState(workspace.slug);
  const [isRestoring, setIsRestoring] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const restore = async () => {
    setIsRestoring(true);
    setErrorText(null);
    try {
      const restored = await workspaceService.restoreWorkspace(workspace.id, needsNewSlug ? slug.trim() : undefined);
      await onRestored(restored.slug);
    } catch (err) {
      const apiError = err as TApiError;
      if (apiError?.error_code === "SLUG_TAKEN" || apiError?.error_code === "SLUG_INVALID") setNeedsNewSlug(true);
      const key = apiError?.error_code ? ERROR_KEYS[apiError.error_code] : undefined;
      setErrorText(key ? t(key) : (apiError?.error ?? t(`${PREFIX}.errors.generic`)));
    } finally {
      setIsRestoring(false);
    }
  };

  return (
    <div className="border-b border-subtle py-4" data-testid={`deleted-workspace-${workspace.id}`}>
      <div className="flex flex-wrap items-center gap-3">
        <WorkspaceLogo logo={workspace.logo_url} name={workspace.name} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-body-sm-medium text-primary">{workspace.name}</div>
          <div className="text-caption-md-regular text-tertiary">
            {t(`${PREFIX}.dates`, {
              deleted: renderFormattedDate(workspace.deleted_at) ?? "",
              purge: renderFormattedDate(workspace.purge_at) ?? "",
            })}
          </div>
        </div>
        <Button
          variant="secondary"
          size="md"
          stretch="auto"
          label={t(`${PREFIX}.restore`)}
          loading={isRestoring}
          disabled={isRestoring || (needsNewSlug && slug.trim() === "")}
          onClick={() => void restore()}
          data-testid="deleted-workspace-restore"
        />
      </div>
      {needsNewSlug && (
        <div className="mt-3 max-w-sm space-y-1">
          <p className="text-caption-md-regular text-secondary">{t(`${PREFIX}.new_slug_label`)}</p>
          <InputGroup size="md">
            <Input
              size="md"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
              maxLength={48}
              aria-label={t(`${PREFIX}.new_slug_label`)}
              data-testid="deleted-workspace-slug"
            />
          </InputGroup>
        </div>
      )}
      {errorText && (
        <p role="alert" className="mt-2 text-body-xs-regular text-danger-primary">
          {errorText}
        </p>
      )}
    </div>
  );
});

export const DeletedWorkspacesProfileSettings = observer(function DeletedWorkspacesProfileSettings() {
  const { t } = useTranslation();
  const router = useAppRouter();
  const { fetchWorkspaces } = useWorkspace();
  const { fetchCurrentUserSettings } = useUserSettings();
  const {
    data: workspaces,
    error,
    mutate,
  } = useSWR(DELETED_WORKSPACES_KEY, () => workspaceService.fetchDeletedWorkspaces());

  const handleRestored = async (slug: string) => {
    await Promise.all([fetchWorkspaces(), fetchCurrentUserSettings(true), mutate()]);
    setToast({
      type: "success",
      title: t(`${PREFIX}.restored_title`),
      message: t(`${PREFIX}.restored_message`),
    });
    router.push(`/${slug}`);
  };

  return (
    <div className="size-full">
      <ProfileSettingsHeading title={t(`${PREFIX}.title`)} description={t(`${PREFIX}.description`)} />
      <div className="mt-7">
        {error ? (
          <p className="text-body-xs-regular text-danger-primary">{t(`${PREFIX}.errors.load_failed`)}</p>
        ) : !workspaces ? (
          <div className="space-y-2">
            {[0, 1].map((i) => (
              <span key={i} className="block h-12 w-full rounded-sm bg-layer-1" />
            ))}
          </div>
        ) : workspaces.length === 0 ? (
          <p className="text-body-xs-regular text-tertiary">{t(`${PREFIX}.empty`)}</p>
        ) : (
          <div className="border-t border-subtle">
            {workspaces.map((workspace) => (
              <DeletedWorkspaceRow key={workspace.id} workspace={workspace} onRestored={handleRestored} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
});
