/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Barrel for the fork-owned custom-properties module, reached via the
 * `@/plane-web/custom-properties` alias (apps/web/tsconfig.json). Later phases
 * add the seam components (CustomPropertyHeaderCells, CustomPropertyValueCells,
 * CustomProperties*Section, …) here. See docs/custom-properties-design.md §5.
 */

export * from "./types";
export * from "./constants";
// services
export * from "./issue-properties.service";
// hooks
export * from "./hooks/use-custom-properties";
export * from "./hooks/use-property-values";
export * from "./hooks/use-project-custom-properties";
// components
export * from "./components/settings/project-properties-settings";
