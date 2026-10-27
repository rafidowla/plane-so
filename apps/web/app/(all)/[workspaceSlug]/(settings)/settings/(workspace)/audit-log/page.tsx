/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- workspace audit log. Super Admins only, read-only: no edit or delete controls.

import { useCallback, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import { cn, renderFormattedDate, renderFormattedTime } from "@plane/utils";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// services
import type { TWorkspaceAuditLogEntry, TWorkspaceAuditLogSource } from "@/services/workspace-audit-log.service";
import { WorkspaceAuditLogService } from "@/services/workspace-audit-log.service";
// local imports
import type { Route } from "./+types/page";
import { AuditLogWorkspaceSettingsHeader } from "./header";

const auditLogService = new WorkspaceAuditLogService();

const PAGE_SIZE = 50;

// Fields whose readable twin is shown instead (role -> role_name, owner_id -> owner).
const HIDDEN_WHEN_TWIN: Record<string, string> = { role: "role_name", owner_id: "owner" };

const formatValue = (value: unknown, yesNo: [string, string]): string => {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? yesNo[0] : yesNo[1];
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const changeLines = (entry: TWorkspaceAuditLogEntry, yesNo: [string, string]) => {
  const before = entry.before ?? {};
  const after = entry.after ?? {};
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  return (
    keys
      .filter((key) => !(HIDDEN_WHEN_TWIN[key] && keys.includes(HIDDEN_WHEN_TWIN[key])))
      // Some entries carry the full before/after state for context; only list what changed.
      .filter((key) => !(key in before && key in after && JSON.stringify(before[key]) === JSON.stringify(after[key])))
      .map((key) => ({
        key,
        before: key in before ? formatValue(before[key], yesNo) : null,
        after: key in after ? formatValue(after[key], yesNo) : null,
      }))
  );
};

function AuditLogRow({ entry }: { entry: TWorkspaceAuditLogEntry }) {
  const { t } = useTranslation();
  const lines = changeLines(entry, [t("common.yes"), t("common.no")]);
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-subtle px-1 py-3 md:grid-cols-[9rem_12rem_1fr]">
      <div className="text-caption-md-regular text-tertiary">
        <div>{renderFormattedDate(entry.created_at)}</div>
        <div>{renderFormattedTime(entry.created_at, "12-hour")}</div>
      </div>
      <div className="min-w-0 text-body-xs-regular">
        {entry.actor ? (
          <>
            <div className="truncate text-primary">{entry.actor.name || entry.actor.email}</div>
            {entry.actor.name && entry.actor.email && (
              <div className="truncate text-caption-md-regular text-tertiary">{entry.actor.email}</div>
            )}
          </>
        ) : (
          <div className="text-tertiary">{t("workspace_settings.settings.audit_log.unknown_actor")}</div>
        )}
      </div>
      <div className="min-w-0 text-body-xs-regular">
        <div className="text-primary">
          <span className="font-medium">{entry.action_label}</span>
          {entry.target_label && <span className="text-secondary"> · {entry.target_label}</span>}
        </div>
        {lines.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-caption-md-regular text-secondary">
            {lines.map((line) => (
              <li key={line.key} className="break-words">
                <span className="text-tertiary">{line.key}: </span>
                {line.before !== null && <span className="line-through decoration-1">{line.before}</span>}
                {line.before !== null && line.after !== null && <span className="text-tertiary"> → </span>}
                {line.after !== null && <span>{line.after}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function AuditLogPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  const { t } = useTranslation();
  const { workspaceUserInfo } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  // state
  const [source, setSource] = useState<TWorkspaceAuditLogSource | null>(null);
  const [extraPages, setExtraPages] = useState<TWorkspaceAuditLogEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState(false);
  // derived values
  const memberInfo = workspaceUserInfo[workspaceSlug];
  const isSuperAdmin = !!memberInfo?.is_super_admin;

  const { data: firstPage, error } = useSWR(
    isSuperAdmin ? `WORKSPACE_AUDIT_LOG_${workspaceSlug}_${source ?? "all"}` : null,
    async () => {
      const page = await auditLogService.list(workspaceSlug, { source, limit: PAGE_SIZE });
      setExtraPages([]);
      setNextCursor(page.next_cursor);
      return page;
    },
    { revalidateOnFocus: false }
  );

  const loadMore = useCallback(async () => {
    if (!nextCursor) return;
    setIsLoadingMore(true);
    setLoadError(false);
    try {
      const page = await auditLogService.list(workspaceSlug, { source, cursor: nextCursor, limit: PAGE_SIZE });
      setExtraPages((rows) => [...rows, ...page.results]);
      setNextCursor(page.next_cursor);
    } catch {
      setLoadError(true);
    } finally {
      setIsLoadingMore(false);
    }
  }, [nextCursor, source, workspaceSlug]);

  const changeSource = (value: TWorkspaceAuditLogSource | null) => {
    if (value === source) return;
    setExtraPages([]);
    setNextCursor(null);
    setSource(value);
  };

  if (memberInfo && !isSuperAdmin) return <NotAuthorizedView section="settings" className="h-auto" />;

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.audit_log.title")}`
    : undefined;
  const entries = [...(firstPage?.results ?? []), ...extraPages];
  const filters: { value: TWorkspaceAuditLogSource | null; label: string }[] = [
    { value: null, label: t("workspace_settings.settings.audit_log.filter_all") },
    { value: "workspace", label: t("workspace_settings.settings.audit_log.filter_workspace") },
    { value: "time_tracking", label: t("workspace_settings.settings.audit_log.filter_time_tracking") },
  ];

  return (
    <SettingsContentWrapper header={<AuditLogWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <SettingsHeading
          title={t("workspace_settings.settings.audit_log.title")}
          description={t("workspace_settings.settings.audit_log.description")}
        />
        <div className="mt-4 flex flex-wrap gap-1" role="group">
          {filters.map((filter) => (
            <button
              key={filter.value ?? "all"}
              type="button"
              aria-pressed={source === filter.value}
              onClick={() => changeSource(filter.value)}
              className={cn(
                "rounded-md border border-subtle px-2.5 py-1 text-caption-md-medium text-secondary hover:bg-layer-transparent-hover",
                { "bg-layer-transparent-selected text-primary": source === filter.value }
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="mt-3 border-t border-subtle">
          {error ? (
            <p className="py-6 text-body-xs-regular text-danger-primary">
              {t("workspace_settings.settings.audit_log.load_failed")}
            </p>
          ) : !firstPage ? (
            <div className="space-y-2 py-4">
              {[0, 1, 2].map((i) => (
                <span key={i} className="block h-10 w-full rounded-sm bg-layer-1" />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <p className="py-6 text-body-xs-regular text-tertiary">
              {t("workspace_settings.settings.audit_log.empty")}
            </p>
          ) : (
            entries.map((entry) => <AuditLogRow key={`${entry.source}-${entry.id}`} entry={entry} />)
          )}
        </div>
        {loadError && (
          <p className="mt-2 text-body-xs-regular text-danger-primary">
            {t("workspace_settings.settings.audit_log.load_failed")}
          </p>
        )}
        {nextCursor && (
          <div className="mt-4">
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={t("workspace_settings.settings.audit_log.load_more")}
              loading={isLoadingMore}
              disabled={isLoadingMore}
              onClick={() => void loadMore()}
            />
          </div>
        )}
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(AuditLogPage);
