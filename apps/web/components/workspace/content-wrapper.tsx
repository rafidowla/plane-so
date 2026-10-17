/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { observer } from "mobx-react";
// plane imports
import { cn } from "@plane/utils";
import { AppRailRoot } from "@/components/navigation";
import { useAppRailVisibility } from "@/lib/app-rail";
import { TopNavigationRoot } from "@/components/navigation/top-navigation-root";
// FORK: time-tracking-v2 — "Still working?" check, hosted on every workspace page
import { IdleCheckHost } from "@/plane-web/components/time/timer/idle-check-host";
// FORK: time-tracking-v2 S6 — "Add an estimate first" toast for refused moves / timer starts
import { EstimateRequiredHost } from "@/plane-web/components/issues/worklog/estimate/estimate-required-host";

export const WorkspaceContentWrapper = observer(function WorkspaceContentWrapper({
  children,
}: {
  children: React.ReactNode;
}) {
  // Use the context to determine if app rail should render
  const { shouldRenderAppRail } = useAppRailVisibility();

  return (
    <div className="relative flex size-full flex-col overflow-hidden bg-canvas transition-all duration-300 ease-in-out">
      <TopNavigationRoot />
      <IdleCheckHost />
      <EstimateRequiredHost />
      <div className="relative flex size-full overflow-hidden">
        {/* Conditionally render AppRailRoot based on context */}
        {shouldRenderAppRail && <AppRailRoot />}
        <div
          className={cn(
            "relative size-full flex-grow overflow-hidden pr-2 pb-2 pl-2 transition-all duration-300 ease-in-out",
            {
              "pl-0!": shouldRenderAppRail,
            }
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
});
