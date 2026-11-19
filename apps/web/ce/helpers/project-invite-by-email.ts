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

/**
 * Same decision for the box's value right AFTER a native paste (the browser has already inserted the text,
 * replacing any selection). A pasted list ("a@x.com b@x.com", "Name <a@x.com>, ...") commits; text with no
 * "@" at all (e.g. a pasted name) or an unclosed "<" is left alone so the person can keep typing.
 */
export const shouldCommitPastedEmails = (value: string): boolean => {
  if (shouldCommitTypedEmails(value)) return true;
  const trimmed = value.trim();
  if (!trimmed.includes("@") || !/[\s,;]/.test(trimmed)) return false;
  return (trimmed.match(/</g)?.length ?? 0) <= (trimmed.match(/>/g)?.length ?? 0);
};

export type TParsedEmails = {
  /** Valid, trimmed, lowercased, de-duplicated (also against `existing`). */
  valid: string[];
  /** Everything else, as typed (case kept), de-duplicated case-insensitively. */
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
    const token = part.trim();
    if (!token) continue;
    const lower = token.toLowerCase();
    if (isValidEmail(lower)) {
      if (seen.has(lower)) continue;
      seen.add(lower);
      valid.push(lower);
    } else if (!invalidSeen.has(lower)) {
      // kept as typed: it goes back into the input for the person to fix
      invalidSeen.add(lower);
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
  /** Rejected by the server; the server's reason is appended when it says more than "Invalid email". */
  invalid: string[];
  /** Sent but missing from the server's answer: nothing is known about them, so they are kept for a retry. */
  failed: string[];
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

type TResultBucket = "added" | "invited" | "inviteUpdated" | "alreadyMember" | "notAllowed" | "invalid";

const bucketFor: Record<TProjectInviteByEmailStatus, TResultBucket> = {
  added: "added",
  invited: "invited",
  invite_updated: "inviteUpdated",
  already_member: "alreadyMember",
  not_allowed: "notAllowed",
  invalid: "invalid",
};

/**
 * Group per-email results. "Already a member" counts as a problem so the person sees it, not as a failure.
 * `sent` (the emails in the request) lets an email the server did not answer for be kept, never dropped.
 */
export const summariseInviteResults = (results: TProjectInviteByEmailResult[], sent: string[] = []): TInviteSummary => {
  const summary: TInviteSummary = {
    added: [],
    invited: [],
    inviteUpdated: [],
    alreadyMember: [],
    notAllowed: [],
    invalid: [],
    failed: [],
    problemEmails: [],
    hasProblems: false,
  };
  const answered = new Set<string>();
  for (const result of results) {
    answered.add(result.email);
    const bucket = bucketFor[result.status] as TResultBucket | undefined;
    if (!bucket) {
      // unknown status from a newer server: treat as a problem so it is not silently dropped
      summary.failed.push(result.email);
      summary.problemEmails.push(result.email);
      continue;
    }
    if (bucket === "invalid") {
      // e.g. "Please try again." after a concurrent invite: don't present that as a bad address
      const reason = result.detail && result.detail !== "Invalid email" ? ` (${result.detail})` : "";
      summary.invalid.push(`${result.email}${reason}`);
    } else {
      summary[bucket].push(result.email);
    }
    if (bucket === "alreadyMember" || bucket === "notAllowed" || bucket === "invalid") {
      summary.problemEmails.push(result.email);
    }
  }
  for (const email of sent) {
    if (answered.has(email)) continue;
    summary.failed.push(email);
    summary.problemEmails.push(email);
  }
  summary.hasProblems = summary.problemEmails.length > 0;
  return summary;
};

/**
 * The chips left after a send: emails that were not part of it stay, sent ones stay only if they need
 * attention, and problem emails the server spelled differently are added. Order is kept.
 */
export const chipsAfterSend = (current: string[], sent: string[], problemEmails: string[]): string[] => {
  const sentSet = new Set(sent);
  const problems = new Set(problemEmails);
  const kept = current.filter((email) => !sentSet.has(email) || problems.has(email));
  const keptSet = new Set(kept);
  return [...kept, ...problemEmails.filter((email) => !keptSet.has(email))];
};

/** The message of an API error body (`{ error }` or `{ detail }`), if it has one. */
export const apiErrorText = (error: unknown): string | null => {
  const body = (error as { error?: unknown; detail?: unknown } | null | undefined) ?? {};
  if (typeof body.error === "string") return body.error;
  if (typeof body.detail === "string") return body.detail;
  return null;
};
