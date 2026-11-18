/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- pending (not yet accepted) email invitations in the project Members list.

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { EUserPermissions, EUserPermissionsLevel, ROLE } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { TProjectPendingMember } from "@plane/types";
import { copyTextToClipboard } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** Filters the rows by the members search box. */
  searchQuery?: string;
};

export const ProjectPendingMemberList = observer(function ProjectPendingMemberList(props: Props) {
  const { workspaceSlug, projectId, searchQuery = "" } = props;
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();
  const {
    project: { getPendingMembers, fetchPendingMembers, cancelPendingMember, resendPendingMember },
  } = useMember();
  const [toCancel, setToCancel] = useState<TProjectPendingMember | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const isProjectAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const canManage = isProjectAdmin || isWorkspaceAdmin;

  useSWR(
    canManage && workspaceSlug && projectId ? `PROJECT_PENDING_MEMBERS_${workspaceSlug}_${projectId}` : null,
    () => fetchPendingMembers(workspaceSlug, projectId),
    { revalidateOnFocus: false }
  );

  const pending = (getPendingMembers(projectId) ?? []).filter((spot) =>
    spot.email.toLowerCase().includes(searchQuery.trim().toLowerCase())
  );
  if (!canManage || pending.length === 0) return null;

  const base = "project_settings.members.pending";

  const handleResend = async (spot: TProjectPendingMember) => {
    setBusyId(spot.id);
    try {
      await resendPendingMember(workspaceSlug, projectId, spot.id);
      setToast({ type: "success", title: t("success"), message: t(`${base}.resend_success`) });
    } catch (error) {
      setToast({
        type: "error",
        title: t("error"),
        message: (error as { error?: string } | undefined)?.error ?? t(`${base}.resend_error`),
      });
    } finally {
      setBusyId(null);
    }
  };

  const handleCopy = async (spot: TProjectPendingMember) => {
    if (!spot.invite_link) return;
    try {
      await copyTextToClipboard(spot.invite_link);
      setToast({ type: "success", title: t("success"), message: t(`${base}.copy_link_success`) });
    } catch {
      setToast({ type: "error", title: t("error"), message: t(`${base}.copy_link_error`) });
    }
  };

  const handleConfirmCancel = async () => {
    if (!toCancel) return;
    setIsCancelling(true);
    try {
      await cancelPendingMember(workspaceSlug, projectId, toCancel.id);
      setToast({ type: "success", title: t("success"), message: t(`${base}.cancel_success`) });
      setToCancel(null);
    } catch (error) {
      setToast({
        type: "error",
        title: t("error"),
        message: (error as { error?: string } | undefined)?.error ?? t(`${base}.cancel_error`),
      });
    } finally {
      setIsCancelling(false);
    }
  };

  return (
    <>
      <ul className="divide-y divide-subtle">
        {pending.map((spot) => (
          <li key={spot.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-13 font-medium">{spot.email}</span>
                <span className="rounded-full bg-layer-1 px-2 py-0.5 text-11 text-secondary">{t(`${base}.tag`)}</span>
              </div>
              <div className="text-12 text-secondary">
                {/* effective_role = project role they will actually get; older servers only send `role` */}
                <span className="capitalize">{ROLE[(spot.effective_role ?? spot.role) as keyof typeof ROLE]}</span>
                {spot.invited_by?.display_name &&
                  ` · ${t(`${base}.invited_by`, { name: spot.invited_by.display_name })}`}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {isWorkspaceAdmin && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  label={t(`${base}.resend`)}
                  loading={busyId === spot.id}
                  onClick={() => handleResend(spot)}
                />
              )}
              {isWorkspaceAdmin && spot.invite_link && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  label={t(`${base}.copy_link`)}
                  onClick={() => handleCopy(spot)}
                />
              )}
              <Button
                variant="secondary"
                size="sm"
                stretch="auto"
                label={t(`${base}.cancel`)}
                onClick={() => setToCancel(spot)}
              />
            </div>
          </li>
        ))}
      </ul>
      <ConfirmDialog
        isOpen={!!toCancel}
        handleClose={() => setToCancel(null)}
        handleSubmit={handleConfirmCancel}
        isSubmitting={isCancelling}
        title={t(`${base}.cancel_title`)}
        content={t(`${base}.cancel_message`, { email: toCancel?.email ?? "" })}
      />
    </>
  );
});
