/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- pure helpers for the workspace invitation settings (Members settings page + invite modal).

import type { TWorkspaceInviteSettings } from "@plane/types";

export const MIN_INVITE_EXPIRY_DAYS = 1;
export const MAX_INVITE_EXPIRY_DAYS = 90;
/** Matches the API limit. */
export const MAX_ALLOWED_DOMAINS = 50;

// A hostname: dot-separated labels of letters, digits and hyphens (no leading/trailing hyphen), 2+ labels.
const DOMAIN_REGEX = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

export type TParsedDomains = {
  /** Lowercased, "@" stripped, de-duplicated (also against `existing`), order kept. */
  valid: string[];
  /** Entries that are not a hostname, as typed. */
  invalid: string[];
};

/** Split typed/pasted text on commas, semicolons and whitespace and clean each entry into a bare domain. */
export const parseDomains = (raw: string, existing: string[] = []): TParsedDomains => {
  const seen = new Set(existing.map((domain) => domain.toLowerCase()));
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const part of raw.split(/[\s,;]+/)) {
    const token = part.trim();
    if (!token) continue;
    const domain = token.replace(/^@+/, "").toLowerCase();
    if (!DOMAIN_REGEX.test(domain)) {
      if (!invalid.includes(token)) invalid.push(token);
      continue;
    }
    if (seen.has(domain)) continue;
    seen.add(domain);
    valid.push(domain);
  }
  return { valid, invalid };
};

/** The number of days if `value` is a whole number in 1..90, otherwise null. */
export const parseExpiryDays = (value: string): number | null => {
  if (!/^\d+$/.test(value.trim())) return null;
  const days = Number(value);
  return days >= MIN_INVITE_EXPIRY_DAYS && days <= MAX_INVITE_EXPIRY_DAYS ? days : null;
};

export type TInviteHint = { key: "admin_only" | "any_domain" | "domains"; domains: string[] };

/** Which hint to show under the email box. Workspace admins get none (they can invite any address). */
export const selectInviteHint = (
  settings: TWorkspaceInviteSettings | null | undefined,
  isWorkspaceAdmin: boolean
): TInviteHint | null => {
  if (isWorkspaceAdmin) return null;
  // Until the settings load (or if they fail to), fall back to the strict Part 1 wording.
  if (!settings?.project_admins_can_invite) return { key: "admin_only", domains: [] };
  if (settings.allowed_email_domains.length === 0) return { key: "any_domain", domains: [] };
  return { key: "domains", domains: settings.allowed_email_domains.map((domain) => `@${domain}`) };
};

/**
 * Whether the Resend button shows on a project's pending list: workspace Admins always; Project Admins only
 * when the workspace lets them invite people.
 */
export const canResendProjectInvite = (
  isWorkspaceAdmin: boolean,
  isProjectAdmin: boolean,
  settings: TWorkspaceInviteSettings | null | undefined
): boolean => isWorkspaceAdmin || (isProjectAdmin && !!settings?.project_admins_can_invite);

/** The first field error of a `{ field: [message] }` 400 body, if any. */
export const fieldErrorText = (error: unknown, field: string): string | null => {
  const value = (error as Record<string, unknown> | null | undefined)?.[field];
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  if (typeof value === "string") return value;
  return null;
};
