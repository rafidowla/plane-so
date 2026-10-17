/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: guest-access (PSR-119) — project Guests may raise work items and edit the ones they created.
// Pure rules (allowed fields, payload stripping, ownership) live in @plane/utils (permission/guest-access.ts);
// this folder holds the React-side hook. Call sites, all marked "guest-access PSR-119":
// - issues/issue-modal/{base,form,provider}.tsx, components/default-properties.tsx: guest create/edit modal
// - issues/header.tsx, empty states, workspace/sidebar/quick-actions.tsx, power-k creation command: entry points
// - issues/issue-detail/*, issues/peek-overview/*: own-item editing (title, description, priority, labels, attachments)
// - issues/issue-layouts/*: quick-add and bulk selection hidden for guests

export * from "./use-guest-work-item-access";
