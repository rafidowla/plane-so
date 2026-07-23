/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Admin-only overflow-menu action handlers shared by `WidgetCard` and every
 * widget renderer that forwards them (kept in its own module so both the card
 * and the per-type widgets can import the type without a circular import).
 */
export type TWidgetCardAdminActionsProp = {
  onEdit: () => void;
  onToggleEnabled: () => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
};
