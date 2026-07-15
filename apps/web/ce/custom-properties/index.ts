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
