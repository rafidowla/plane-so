/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { replaceUnderscoreIfSnakeCase } from "@plane/utils";
import type { TNotificationContentMap } from "@/components/workspace-notifications/sidebar/notification-card/content";

// Additional notification content map for CE (empty - EE extends this)
export const ADDITIONAL_NOTIFICATION_CONTENT_MAP: TNotificationContentMap = {
  // FORK: time-tracking-v2 S3b — time-entry request notifications; `verb` is the notification kind
  // (request_submitted | request_approved | request_rejected). Card text only; the click goes to the
  // Approvals / My requests tab (see sidebar/notification-card/item.tsx).
  time_entry_request: ({ verb }) => ({
    action:
      verb === "request_submitted"
        ? "submitted a"
        : verb === "request_approved"
          ? "approved your"
          : verb === "request_rejected"
            ? "rejected your"
            : "updated a",
    value: "time request",
    showConnector: false,
  }),
  // FORK: move-work-item (PSR-91) — "project" activity: old_value / new_value are the old / new identifiers
  project: ({ newValue }) => ({
    action: "moved this work item to",
    value: newValue,
    showConnector: false,
  }),
};

// Fallback action renderer for fields not in the map
export const renderAdditionalAction = (notificationField: string, verb: string | undefined) => {
  const baseAction = !["comment", "archived_at"].includes(notificationField) ? verb : "";
  return `${baseAction} ${replaceUnderscoreIfSnakeCase(notificationField)}`;
};

// Fallback value renderer for fields not in the map
export const renderAdditionalValue = (
  _notificationField: string | undefined,
  newValue: string | undefined,
  _oldValue: string | undefined
) => newValue;

export const shouldShowConnector = (notificationField: string | undefined) =>
  !["comment", "archived_at", "None", "assignees", "labels", "start_date", "target_date", "parent"].includes(
    notificationField || ""
  );

export const shouldRender = (notificationField: string | undefined, verb: string | undefined) => verb !== "deleted";
