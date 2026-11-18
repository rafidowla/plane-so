/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- pure helpers for the "invite by email" section of the project invitation modal.

import type { TProjectInviteByEmailResult, TProjectInviteByEmailStatus } from "@plane/types";

/** Matches the API limit for one invite-by-email call. */
export const MAX_INVITE_EMAILS = 50;

// Deliberately simple (the server is the authority): something@something.tld, no spaces.
const EMAIL_REGEX = /^[^\s@,;<>()[\]]+@[^\s@,;<>()[\]]+\.[^\s@,;<>()[\]]{2,}$/;

const ANGLE_ADDRESS_REGEX = /(?<![^\s,;])(?:"[^"]*"\s*|(?:[^\s,;<>@"]+\s+)*)<([^<>\s]+)>/g;

export const isValidEmail = (value: string): boolean => EMAIL_REGEX.test(value);

/**
 * Decide whether the text typed in the email box should be turned into chips right now.
 * Commas, semicolons and newlines always commit. A space commits only when the text so far is exactly one
 * valid bare address (so "John Doe <j@x.com>" can still be typed by hand); everything else waits for
 * Enter, blur or paste.
 */
export const shouldCommitTypedEmails = (value: string): boolean => {
  if (/[,;\n\r]/.test(value)) return true;
  if (!/\s$/.test(value)) return false;
  if (/[<>"]/.test(value)) return false;
  return isValidEmail(value.trim());
};

export type TParsedEmails = {
  /** Valid, trimmed, lowercased, de-duplicated (also against `existing`). */
  valid: string[];
  invalid: string[];
};

/** Split pasted/typed text on commas, semicolons, whitespace/newlines. Strips "Name <a@b.c>" wrappers. */
export const parseEmails = (raw: string, existing: string[] = []): TParsedEmails => {
  const seen = new Set(existing.map((e) => e.trim().toLowerCase()));
  const valid: string[] = [];
  const invalid: string[] = [];
  const invalidSeen = new Set<string>();
  // "Name <a@b.c>" / "\"Name\" <a@b.c>" / "<a@b.c>" from copied address-book entries: keep only the address.
  // The name part may not contain "@", so plain "a@x.com b@x.com <c@x.com>" lists are left intact.
  const unwrapped = raw.replace(ANGLE_ADDRESS_REGEX, " $1 ");
  for (const part of unwrapped.split(/[\s,;]+/)) {
    let token = part.trim();
    if (!token) continue;
    token = token.toLowerCase();
    if (isValidEmail(token)) {
      if (seen.has(token)) continue;
      seen.add(token);
      valid.push(token);
    } else if (!invalidSeen.has(token)) {
      invalidSeen.add(token);
      invalid.push(token);
    }
  }
  return { valid, invalid };
};

export type TInviteSummary = {
  added: string[];
  invited: string[]; // new invitations (email sent)
  /** Already had a pending workspace invitation: added to this project too, no new email (so no note). */
  inviteUpdated: string[];
  alreadyMember: string[];
  notAllowed: string[];
  invalid: string[];
  /** Emails that did not succeed; the modal keeps these as chips for another try. */
  problemEmails: string[];
  hasProblems: boolean;
};

/**
 * The personal note only goes in NEW invitation emails. True when a note was typed and at least one person
 * (or picked member row) did not get one.
 */
export const isNoteDropped = (note: string, summary: TInviteSummary | null, hasMemberRows: boolean): boolean => {
  if (!note.trim()) return false;
  if (hasMemberRows) return true;
  if (!summary) return false;
  return summary.added.length + summary.alreadyMember.length + summary.inviteUpdated.length > 0;
};

const bucketFor: Record<TProjectInviteByEmailStatus, keyof Omit<TInviteSummary, "problemEmails" | "hasProblems">> = {
  added: "added",
  invited: "invited",
  invite_updated: "inviteUpdated",
  already_member: "alreadyMember",
  not_allowed: "notAllowed",
  invalid: "invalid",
};

/** Group per-email results. "Already a member" counts as a problem so the person sees it, not as a failure. */
export const summariseInviteResults = (results: TProjectInviteByEmailResult[]): TInviteSummary => {
  const summary: TInviteSummary = {
    added: [],
    invited: [],
    inviteUpdated: [],
    alreadyMember: [],
    notAllowed: [],
    invalid: [],
    problemEmails: [],
    hasProblems: false,
  };
  for (const result of results) {
    const bucket = bucketFor[result.status];
    if (!bucket) {
      // unknown status from a newer server: treat as a problem so it is not silently dropped
      summary.invalid.push(result.email);
      summary.problemEmails.push(result.email);
      continue;
    }
    summary[bucket].push(result.email);
    if (bucket === "alreadyMember" || bucket === "notAllowed" || bucket === "invalid") {
      summary.problemEmails.push(result.email);
    }
  }
  summary.hasProblems = summary.problemEmails.length > 0;
  return summary;
};
