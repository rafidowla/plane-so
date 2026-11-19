/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/blocks/empty-state";
// components
import { LogoSpinner } from "@/components/common/logo-spinner";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { IssueService } from "@/services/issue/issue.service";
// types
import type { Route } from "./+types/page";

const issueService = new IssueService();

export async function clientLoader({ params }: Route.ClientLoaderArgs) {
  const { workspaceSlug, projectId, issueId } = params;

  try {
    const data = await issueService.getIssueMetaFromURL(workspaceSlug, projectId, issueId);

    if (data) {
      throw redirect(`/${workspaceSlug}/browse/${data.project_identifier}-${data.sequence_id}`);
    }

    return { error: true, accessDenied: false, workspaceSlug };
  } catch (error) {
    // If it's a redirect, rethrow it
    if (error instanceof Response) {
      throw error;
    }
    // FORK: PSR-59 — a 403 means the item exists but the user isn't a project
    // member; say so instead of claiming it doesn't exist.
    const accessDenied = (error as { status?: number } | null)?.status === 403;
    return { error: true, accessDenied, workspaceSlug };
  }
}

export default function IssueDetailsPage({ loaderData }: Route.ComponentProps) {
  const router = useAppRouter();
  const { t } = useTranslation();

  if (loaderData.error) {
    return (
      <div className="flex size-full items-center justify-center">
        {/* FORK: PSR-59 — access-denied copy when the API said 403 */}
        {loaderData.accessDenied ? (
          <EmptyStateDetailed
            align="center"
            assetKey="no-access"
            assetClassName="size-40"
            title="You don't have access to this work item"
            description="You're not a member of this project, so you can't view this work item. Ask a project admin to add you, or go back to your work items."
            actions={[
              {
                label: "Go to my work items",
                variant: "primary",
                onClick: () => router.push(`/${loaderData.workspaceSlug}/workspace-views/all-issues/`),
              },
            ]}
          />
        ) : (
          <EmptyStateDetailed
            align="center"
            assetKey="work-item"
            assetClassName="size-40"
            title={t("issue.empty_state.issue_detail.title")}
            description={t("issue.empty_state.issue_detail.description")}
            actions={[
              {
                label: t("issue.empty_state.issue_detail.primary_button.text"),
                variant: "primary",
                onClick: () => router.push(`/${loaderData.workspaceSlug}/workspace-views/all-issues/`),
              },
            ]}
          />
        )}
      </div>
    );
  }

  return (
    <div className="flex size-full items-center justify-center">
      <LogoSpinner />
    </div>
  );
}
