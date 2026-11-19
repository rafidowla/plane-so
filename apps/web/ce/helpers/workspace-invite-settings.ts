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

/** Exact match on the part after the last "@" (lowercase); an empty list allows everything. Mirrors the API. */
export const isEmailDomainAllowed = (email: string, domains: string[] | null | undefined): boolean => {
  if (!domains || domains.length === 0) return true;
  const at = email.lastIndexOf("@");
  if (at === -1) return false;
  const domain = email
    .slice(at + 1)
    .trim()
    .toLowerCase();
  return domains.some((allowed) => allowed.toLowerCase() === domain);
};

/**
 * Whether the Resend button shows on a project's pending list for `email`: workspace Admins always; Project
 * Admins only when the workspace lets them invite people AND the address's domain is allowed.
 */
export const canResendProjectInvite = (
  isWorkspaceAdmin: boolean,
  isProjectAdmin: boolean,
  settings: TWorkspaceInviteSettings | null | undefined,
  email?: string
): boolean => {
  if (isWorkspaceAdmin) return true;
  if (!isProjectAdmin || !settings?.project_admins_can_invite) return false;
  return email === undefined || isEmailDomainAllowed(email, settings.allowed_email_domains);
};

/**
 * i18n key suffix for a refused resend. Matches the server's `error_code` / `detail` codes first; the
 * Super Admin message text is only a fallback for responses that carry no `error_code`.
 */
export const resendDeniedReasonKey = (error: unknown): string | null => {
  const body = error as { detail?: string; error?: string; error_code?: string } | null | undefined;
  if (body?.detail === "workspace_admin_only") return "resend_denied_workspace_admin_only";
  if (body?.detail === "domain_not_allowed") return "resend_denied_domain_not_allowed";
  if (body?.error_code === "SUPER_ADMIN_REQUIRED") return "resend_denied_super_admin_only";
  if (body?.error_code === "RESEND_COOLDOWN") return "resend_denied_cooldown";
  if (typeof body?.error === "string" && body.error.includes("Super Admin")) return "resend_denied_super_admin_only";
  return null;
};

export type TSkippedInvite = { email: string; reason: "cooldown" | "super_admin_required" | string };

/** The `skipped` list of a Members-page invite response (invites that were not resent), or []. */
export const getSkippedInvites = (response: unknown): TSkippedInvite[] => {
  const skipped = (response as { skipped?: unknown } | null | undefined)?.skipped;
  if (!Array.isArray(skipped)) return [];
  return skipped.filter((item): item is TSkippedInvite => typeof (item as TSkippedInvite)?.email === "string");
};

/** i18n key suffix (under `invite_skipped_reason`) for a skipped invite's reason. */
export const skippedInviteReasonKey = (reason: string): string =>
  reason === "super_admin_required" ? "super_admin_required" : "cooldown";

/** The first field error of a `{ field: [message] }` 400 body, if any. */
export const fieldErrorText = (error: unknown, field: string): string | null => {
  const value = (error as Record<string, unknown> | null | undefined)?.[field];
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  if (typeof value === "string") return value;
  return null;
};
