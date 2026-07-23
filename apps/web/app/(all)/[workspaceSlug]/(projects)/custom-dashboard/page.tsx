/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// components
import { PageHead } from "@/components/core/page-title";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
// plane web
import { DashboardRoot } from "@/plane-web/custom-dashboards/components";

function CustomDashboardPage() {
  const { workspaceSlug } = useParams();
  const { currentWorkspace } = useWorkspace();

  const pageTitle = currentWorkspace?.name ? `${currentWorkspace?.name} - Dashboard` : "Dashboard";

  if (!workspaceSlug) return null;

  return (
    <>
      <PageHead title={pageTitle} />
      <div className="h-full w-full overflow-hidden">
        <DashboardRoot workspaceSlug={workspaceSlug.toString()} />
      </div>
    </>
  );
}

export default observer(CustomDashboardPage);
