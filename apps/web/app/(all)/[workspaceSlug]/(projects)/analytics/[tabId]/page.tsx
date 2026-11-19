/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane package imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/blocks/empty-state";
import { Tabs, TabsList, TabsPanel } from "@makeplane/propel/components/tabs";
// components
import AnalyticsFilterActions from "@/components/analytics/analytics-filter-actions";
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { UnderlineTabLink } from "@/components/navigation/underline-tab-link";
// hooks
import { useCommandPalette } from "@/hooks/store/use-command-palette";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
import { useAnalyticsTabs } from "@/components/analytics/use-analytics-tabs";
// FORK: time-tracking-v2 S5
import { useTimeSettings } from "@/plane-web/components/time/use-time-settings";
import type { Route } from "./+types/page";

function AnalyticsPage({ params }: Route.ComponentProps) {
  const { tabId } = params;

  // plane imports
  const { t } = useTranslation();

  // store hooks
  const { toggleCreateProjectModal } = useCommandPalette();
  const { workspaceProjectIds, loader } = useProject();
  const { currentWorkspace } = useWorkspace();
  const { allowPermissions } = useUserPermissions();

  const pageTitle = currentWorkspace?.name
    ? t(`workspace_analytics.page_label`, { workspace: currentWorkspace?.name })
    : undefined;

  // permissions
  const canPerformEmptyStateActions = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  const workspaceSlug = params.workspaceSlug;
  // FORK: time-tracking-v2 S5 — Timesheets shows only while weekly approval is on; the old Time tab
  // now lives in the Time area (Reports), so a stale /analytics/time link is sent there.
  const { data: timeSettings, error: timeSettingsError } = useTimeSettings(
    workspaceSlug.toString(),
    canPerformEmptyStateActions
  );
  const ANALYTICS_TABS = useAnalyticsTabs(workspaceSlug.toString(), timeSettings?.weekly_approval_enabled === true);
  const timeSettingsKnown = !!timeSettings || !!timeSettingsError;
  useEffect(() => {
    if (tabId === "time") router.replace(`/${workspaceSlug}/time/reports/`);
    else if (
      tabId === "timesheets" &&
      timeSettingsKnown &&
      !ANALYTICS_TABS.some((tab) => (tab.key as string) === "timesheets")
    )
      router.replace(`/${workspaceSlug}/analytics/overview/`);
  }, [tabId, timeSettingsKnown, ANALYTICS_TABS, router, workspaceSlug]);

  // the route owns the selected tab; each tab is a link to its route
  const selectedTab = tabId || ANALYTICS_TABS[0]?.key;

  // FORK: guest-access PSR-119 — analytics are hidden from workspace guests (the API answers 403 as well)
  if (!canPerformEmptyStateActions) return <NotAuthorizedView section="general" className="h-auto" />;

  return (
    <>
      <PageHead title={pageTitle} />
      {workspaceProjectIds && (
        <>
          {workspaceProjectIds.length > 0 || loader === "init-loader" ? (
            <div className="flex h-full overflow-hidden">
              <Tabs variant="underline" layout="fill" value={selectedTab}>
                <div className="flex h-full w-full flex-col">
                  <div className="flex w-full items-center justify-between gap-4 overflow-hidden border-b border-subtle-1 bg-surface-1 px-page-x py-2">
                    {/* pulls the underline onto the header's bottom border */}
                    <div className="-mb-2">
                      <TabsList>
                        {ANALYTICS_TABS.map((tab) => (
                          <UnderlineTabLink
                            key={tab.key}
                            value={tab.key}
                            disabled={tab.isDisabled}
                            label={tab.label}
                            to={`/${workspaceSlug}/analytics/${tab.key}`}
                          />
                        ))}
                      </TabsList>
                    </div>

                    <div className="shrink-0">
                      <AnalyticsFilterActions />
                    </div>
                  </div>
                  {/* Grid wrapper: Propel's TabsPanel omits className, so the single mounted panel
                      gets its fill height from a one-row grid instead. */}
                  <div className="grid min-h-0 w-full flex-1 grid-rows-1 overflow-x-hidden overflow-y-auto">
                    {ANALYTICS_TABS.map((tab) => (
                      <TabsPanel key={tab.key} value={tab.key}>
                        <tab.content />
                      </TabsPanel>
                    ))}
                  </div>
                </div>
              </Tabs>
            </div>
          ) : (
            <EmptyStateDetailed
              assetKey="project"
              title={t("workspace_projects.empty_state.no_projects.title")}
              description={t("workspace_projects.empty_state.no_projects.description")}
              actions={[
                {
                  label: t("create_project"),
                  onClick: () => {
                    toggleCreateProjectModal(true);
                  },
                  disabled: !canPerformEmptyStateActions,
                },
              ]}
            />
          )}
        </>
      )}
    </>
  );
}

export default observer(AnalyticsPage);
