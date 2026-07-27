/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// ui
import { useTranslation } from "@plane/i18n";
import { Tooltip } from "@plane/propel/tooltip";
// hooks
import { useInstance } from "@/hooks/store/use-instance";
import { usePlatformOS } from "@/hooks/use-platform-os";
import packageJson from "package.json";
// local components
import { Button } from "@plane/propel/button";
import { PaidPlanUpgradeModal } from "@/components/license/modal/upgrade-modal";

export const WorkspaceEditionBadge = observer(function WorkspaceEditionBadge() {
  // states
  const [isPaidPlanPurchaseModalOpen, setIsPaidPlanPurchaseModalOpen] = useState(false);
  // translation
  const { t } = useTranslation();
  // platform
  const { isMobile } = usePlatformOS();
  // instance
  const { config: instanceConfig } = useInstance();
  // FORK: self-hosted-chrome — self-hosted deployments have no cloud upgrade path to sell, so
  // the click no longer opens the paid-plan modal until a dedicated self-hosted community
  // feature exists. Left un-disabled on purpose (native `disabled` kills pointer events, which
  // would also silence the tooltip below — the only place the running version is shown).
  const isSelfManaged = instanceConfig?.is_self_managed;

  return (
    <>
      {!isSelfManaged && (
        <PaidPlanUpgradeModal
          isOpen={isPaidPlanPurchaseModalOpen}
          handleClose={() => setIsPaidPlanPurchaseModalOpen(false)}
        />
      )}
      <Tooltip tooltipContent={`Version: v${packageJson.version}`} isMobile={isMobile}>
        <Button
          variant="tertiary"
          size="lg"
          onClick={isSelfManaged ? undefined : () => setIsPaidPlanPurchaseModalOpen(true)}
          aria-haspopup={isSelfManaged ? undefined : "dialog"}
          aria-label={t("aria_labels.projects_sidebar.edition_badge")}
        >
          Community
        </Button>
      </Tooltip>
    </>
  );
});
