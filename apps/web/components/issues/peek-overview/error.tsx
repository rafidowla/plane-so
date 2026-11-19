/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { ArrowNarrowRightOutline } from "@makeplane/propel/icons";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { useTranslation } from "@plane/i18n";
// assets
import emptyIssue from "@/app/assets/empty-state/issue.svg?url";
// components
import { EmptyState } from "@/components/common/empty-state";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";

type TIssuePeekOverviewError = {
  removeRoutePeekId: () => void;
  // FORK: PSR-59 — 403 means the user isn't a project member; show an access
  // message instead of claiming the work item doesn't exist.
  accessDenied?: boolean;
};

export function IssuePeekOverviewError(props: TIssuePeekOverviewError) {
  const { removeRoutePeekId, accessDenied = false } = props;
  const { t } = useTranslation();
  // hooks
  const { isMobile } = usePlatformOS();

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <div className="flex flex-shrink-0 justify-start">
        <div className="m-5">
          <IconButton
            type="button"
            variant="ghost"
            size="sm"
            aria-label={t("common.close_peek_view")}
            showTooltip={!isMobile}
            onClick={removeRoutePeekId}
            icon={<Icon icon={ArrowNarrowRightOutline} />}
          />
        </div>
      </div>

      <div className="h-full w-full">
        {accessDenied ? (
          <EmptyState
            image={emptyIssue ?? undefined}
            title="You don't have access to this work item"
            description="You're not a member of this project, so you can't view this work item. Ask a project admin to add you."
          />
        ) : (
          <EmptyState
            image={emptyIssue ?? undefined}
            title="Work item does not exist"
            description="The work item you are looking for does not exist, has been archived, or has been deleted."
          />
        )}
      </div>
    </div>
  );
}
