/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Barrel for the fork-owned custom-dashboards module, reached via the
 * `@/plane-web/custom-dashboards` alias (apps/web/tsconfig.json). Phase 6 adds
 * the widget components (config forms, chart renderers, view-list table, …)
 * here. See docs/custom-dashboards-design.md.
 */

export * from "./types";
export * from "./constants";
// services
export * from "./custom-dashboards.service";
// hooks
export * from "./hooks/use-custom-dashboard";
// NOTE: Phase 6 components are intentionally NOT re-exported here. The store
// (@/plane-web/store/custom-dashboards) imports this barrel for the service +
// types, and the components import the store — re-exporting them would form a
// runtime import cycle through this file. Import components directly from
// `@/plane-web/custom-dashboards/components` instead.
