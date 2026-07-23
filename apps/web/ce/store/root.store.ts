/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// store
import { CoreRootStore } from "@/store/root.store";
// FORK: custom-dashboards
import type { ICustomDashboardsStore } from "./custom-dashboards";
import { CustomDashboardsStore } from "./custom-dashboards";
import type { ITimelineStore } from "./timeline";
import { TimeLineStore } from "./timeline";

export class RootStore extends CoreRootStore {
  customDashboards: ICustomDashboardsStore; // FORK: custom-dashboards
  timelineStore: ITimelineStore;

  constructor() {
    super();

    this.customDashboards = new CustomDashboardsStore(this); // FORK: custom-dashboards
    this.timelineStore = new TimeLineStore(this);
  }
}
