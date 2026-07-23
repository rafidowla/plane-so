/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// store
import { CoreRootStore } from "@/store/root.store";
// FORK: custom-properties
import type { ICustomPropertiesStore, IPropertyValuesStore } from "./custom-properties";
import { CustomPropertiesStore, PropertyValuesStore } from "./custom-properties";
// FORK: custom-dashboards
import type { ICustomDashboardsStore } from "./custom-dashboards";
import { CustomDashboardsStore } from "./custom-dashboards";
import type { ITimelineStore } from "./timeline";
import { TimeLineStore } from "./timeline";

export class RootStore extends CoreRootStore {
  customProperties: ICustomPropertiesStore; // FORK: custom-properties
  propertyValues: IPropertyValuesStore; // FORK: custom-properties
  customDashboards: ICustomDashboardsStore; // FORK: custom-dashboards
  timelineStore: ITimelineStore;

  constructor() {
    super();

    this.customProperties = new CustomPropertiesStore(this); // FORK: custom-properties
    this.propertyValues = new PropertyValuesStore(this); // FORK: custom-properties
    this.customDashboards = new CustomDashboardsStore(this); // FORK: custom-dashboards
    this.timelineStore = new TimeLineStore(this);
  }
}
