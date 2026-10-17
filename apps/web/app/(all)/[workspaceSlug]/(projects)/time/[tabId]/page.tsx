/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: time-tracking-v2 — workspace Time area. Tabs come from one list so later slices (timesheets,
// approvals...) add a tab by adding an entry; no empty placeholder tabs.
import { observer } from "mobx-react";
import { useRouter } from "next/navigation";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Tab, Tabs, TabsList, TabsPanel } from "@makeplane/propel/components/tabs";
import { PageHead } from "@/components/core/page-title";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
import { useCanReviewTime, usePendingApprovalCount } from "@/plane-web/components/time/approvals/access";
import { getTimeTabs } from "@/plane-web/components/time/tabs";
import type { Route } from "./+types/page";

function TimePage({ params }: Route.ComponentProps) {
  const { tabId, workspaceSlug } = params;
  const router = useRouter();
  const { currentWorkspace } = useWorkspace();
  const { allowPermissions } = useUserPermissions();

  // Guests have no Time area (the nav item is hidden too); keep a typed-in URL from showing it.
  const isInternal = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );
  // FORK: time-tracking-v2 S3b — Approvals only for reviewers; an unauthorized tabId falls back to the first tab.
  const canReview = useCanReviewTime(workspaceSlug) && isInternal;
  const pendingCount = usePendingApprovalCount(workspaceSlug, canReview);
  // FORK: time-tracking-v2 S5 — Settings is for workspace admins only.
  const canAdminister = isInternal && allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const tabs = getTimeTabs({ canReview, pendingCount, canAdminister });
  const selected = tabs.find((tab) => tab.key === tabId) ?? tabs[0];

  const title = currentWorkspace?.name ? `${currentWorkspace.name} - Time` : undefined;

  if (!isInternal) {
    return (
      <>
        <PageHead title={title} />
        <div className="text-sm py-12 text-center text-tertiary">Time tracking isn&apos;t available for your role.</div>
      </>
    );
  }

  return (
    <>
      <PageHead title={title} />
      <div className="flex h-full overflow-hidden">
        <Tabs
          variant="contained"
          value={selected.key}
          onValueChange={(value) => router.push(`/${workspaceSlug}/time/${value}/`)}
        >
          <div className="flex h-full w-full flex-col">
            <div className="flex w-full items-center justify-between gap-4 overflow-hidden border-b border-subtle bg-surface-1 px-6 py-2">
              <TabsList>
                {tabs.map((tab) => (
                  <Tab key={tab.key} value={tab.key} label={tab.label} />
                ))}
              </TabsList>
            </div>
            <div className="grid min-h-0 w-full flex-1 grid-rows-1 overflow-x-hidden overflow-y-auto px-2">
              <TabsPanel key={selected.key} value={selected.key}>
                <selected.content />
              </TabsPanel>
            </div>
          </div>
        </Tabs>
      </div>
    </>
  );
}

export default observer(TimePage);
