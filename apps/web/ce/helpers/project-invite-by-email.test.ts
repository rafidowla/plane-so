/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-124
import { describe, expect, it } from "vitest";
import {
  apiErrorText,
  chipsAfterSend,
  isNoteDropped,
  isValidEmail,
  parseEmails,
  shouldCommitPastedEmails,
  shouldCommitTypedEmails,
  summariseInviteResults,
} from "./project-invite-by-email";

describe("isValidEmail", () => {
  it("accepts normal addresses and rejects malformed ones", () => {
    expect(isValidEmail("a@b.co")).toBe(true);
    expect(isValidEmail("first.last+tag@sub.example.com")).toBe(true);
    expect(isValidEmail("nope")).toBe(false);
    expect(isValidEmail("a@b")).toBe(false);
    expect(isValidEmail("a b@c.com")).toBe(false);
    expect(isValidEmail("@c.com")).toBe(false);
  });
});

describe("parseEmails", () => {
  it("splits on commas, semicolons, spaces and newlines", () => {
    const r = parseEmails("a@x.com, b@x.com;c@x.com\nd@x.com  e@x.com");
    expect(r.valid).toEqual(["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com"]);
    expect(r.invalid).toEqual([]);
  });
  it("lowercases, trims and de-duplicates, including against existing chips", () => {
    const r = parseEmails(" A@X.com a@x.com B@x.com ", ["b@x.com"]);
    expect(r.valid).toEqual(["a@x.com"]);
  });
  it("collects invalid tokens once", () => {
    const r = parseEmails("good@x.com bad bad also@bad");
    expect(r.valid).toEqual(["good@x.com"]);
    expect(r.invalid).toEqual(["bad", "also@bad"]);
  });
  it("keeps invalid text as typed (case kept), de-duplicated case-insensitively", () => {
    const r = parseEmails("Foo FOO Bar@baz");
    expect(r.invalid).toEqual(["Foo", "Bar@baz"]);
  });
  it("unwraps <address> tokens and handles empty input", () => {
    expect(parseEmails("<a@x.com>").valid).toEqual(["a@x.com"]);
    expect(parseEmails("<a@x.com>").invalid).toEqual([]);
    expect(parseEmails("  ,; \n").valid).toEqual([]);
  });
});

describe("parseEmails with display names", () => {
  it("extracts the address from 'Name <email>' and quoted-name forms", () => {
    const r = parseEmails('John Doe <John@X.com>, "Doe, Jane" <jane@x.com>; Bob <bob@x.com>');
    expect(r.valid).toEqual(["john@x.com", "jane@x.com", "bob@x.com"]);
    expect(r.invalid).toEqual([]);
  });
  it("keeps plain addresses that precede a 'Name <email>' entry", () => {
    const r = parseEmails("a@x.com b@x.com Carl <c@x.com>");
    expect(r.valid).toEqual(["a@x.com", "b@x.com", "c@x.com"]);
    expect(r.invalid).toEqual([]);
  });
  it("still reports invalid text so the caller can keep it visible", () => {
    const r = parseEmails("Name <not-an-email> ok@x.com");
    expect(r.valid).toEqual(["ok@x.com"]);
    expect(r.invalid).toEqual(["not-an-email"]);
  });
});

describe("summariseInviteResults", () => {
  it("groups statuses and flags problems", () => {
    const s = summariseInviteResults([
      { email: "a@x.com", status: "added" },
      { email: "b@x.com", status: "invited" },
      { email: "c@x.com", status: "invite_updated" },
      { email: "d@x.com", status: "already_member" },
      { email: "e@x.com", status: "not_allowed" },
      { email: "f@x.com", status: "invalid" },
    ]);
    expect(s.added).toEqual(["a@x.com"]);
    expect(s.invited).toEqual(["b@x.com"]);
    expect(s.inviteUpdated).toEqual(["c@x.com"]);
    expect(s.problemEmails).toEqual(["d@x.com", "e@x.com", "f@x.com"]);
    expect(s.hasProblems).toBe(true);
  });
  it("reports no problems on full success and treats unknown statuses as problems", () => {
    expect(summariseInviteResults([{ email: "a@x.com", status: "added" }]).hasProblems).toBe(false);
    const s = summariseInviteResults([{ email: "z@x.com", status: "weird" as never }]);
    expect(s.hasProblems).toBe(true);
    expect(s.failed).toEqual(["z@x.com"]);
  });
  it("keeps an email the server did not answer for as a problem", () => {
    const s = summariseInviteResults([{ email: "a@x.com", status: "added" }], ["a@x.com", "b@x.com"]);
    expect(s.failed).toEqual(["b@x.com"]);
    expect(s.problemEmails).toEqual(["b@x.com"]);
    expect(s.hasProblems).toBe(true);
  });
  it("shows the server's reason for an invalid result unless it is just 'Invalid email'", () => {
    const s = summariseInviteResults([
      { email: "a@x.com", status: "invalid", detail: "Please try again." },
      { email: "b@x", status: "invalid", detail: "Invalid email" },
    ]);
    expect(s.invalid).toEqual(["a@x.com (Please try again.)", "b@x"]);
    expect(s.problemEmails).toEqual(["a@x.com", "b@x"]);
  });
});

describe("shouldCommitTypedEmails", () => {
  it("commits on comma, semicolon and newline", () => {
    expect(shouldCommitTypedEmails("a@x.com,")).toBe(true);
    expect(shouldCommitTypedEmails("a@x.com;b")).toBe(true);
    expect(shouldCommitTypedEmails("a@x.com\nb@x.com")).toBe(true);
  });
  it("commits on a trailing space only after one valid bare address", () => {
    expect(shouldCommitTypedEmails("a@x.com ")).toBe(true);
    expect(shouldCommitTypedEmails("John ")).toBe(false);
    expect(shouldCommitTypedEmails("John Doe ")).toBe(false);
    expect(shouldCommitTypedEmails("John Doe <j@x.com> ")).toBe(false);
    expect(shouldCommitTypedEmails('"John" <j@x.com> ')).toBe(false);
    expect(shouldCommitTypedEmails("a@x.com b@x.com ")).toBe(false);
  });
  it("does not commit while typing", () => {
    expect(shouldCommitTypedEmails("a@x.com")).toBe(false);
  });
});

describe("shouldCommitPastedEmails", () => {
  it("commits a pasted list once the browser has inserted it", () => {
    expect(shouldCommitPastedEmails("a@x.com b@x.com")).toBe(true);
    expect(shouldCommitPastedEmails("John Doe <j@x.com>")).toBe(true);
    expect(shouldCommitPastedEmails("foo, a@x.com")).toBe(true);
    expect(shouldCommitPastedEmails("a@x.com ")).toBe(true);
  });
  it("leaves a single address, a bare name or an unclosed <...> for more typing", () => {
    expect(shouldCommitPastedEmails("a@x.com")).toBe(false);
    expect(shouldCommitPastedEmails("John Doe")).toBe(false);
    expect(shouldCommitPastedEmails("John Doe <j@x.com")).toBe(false);
  });
  it("judges the final value, so a paste that replaced a selection leaves no stale text", () => {
    // draft "foo" fully selected, "a@b.com c@d.com" pasted: the browser's value has no "foo" in it
    const value = "a@b.com c@d.com";
    expect(shouldCommitPastedEmails(value)).toBe(true);
    expect(parseEmails(value)).toEqual({ valid: ["a@b.com", "c@d.com"], invalid: [] });
  });
});

describe("chipsAfterSend", () => {
  it("drops sent emails that succeeded, keeps problems and chips that were not sent", () => {
    expect(chipsAfterSend(["a@x.com", "b@x.com", "c@x.com"], ["a@x.com", "b@x.com"], ["b@x.com"])).toEqual([
      "b@x.com",
      "c@x.com",
    ]);
  });
  it("adds problem emails that are not chips yet", () => {
    expect(chipsAfterSend([], ["a@x.com"], ["a@x.com"])).toEqual(["a@x.com"]);
  });
});

describe("apiErrorText", () => {
  it("reads error or detail, else null", () => {
    expect(apiErrorText({ error: "nope" })).toBe("nope");
    expect(apiErrorText({ detail: "denied" })).toBe("denied");
    expect(apiErrorText(undefined)).toBeNull();
    expect(apiErrorText(null)).toBeNull();
  });
});

describe("isNoteDropped", () => {
  const summary = (results: Parameters<typeof summariseInviteResults>[0]) => summariseInviteResults(results);
  it("is false without a note", () => {
    expect(isNoteDropped("  ", summary([{ email: "a@x.com", status: "added" }]), true)).toBe(false);
  });
  it("is true when any recipient got no new invitation email", () => {
    expect(isNoteDropped("hi", summary([{ email: "a@x.com", status: "added" }]), false)).toBe(true);
    expect(isNoteDropped("hi", summary([{ email: "a@x.com", status: "already_member" }]), false)).toBe(true);
    expect(isNoteDropped("hi", summary([{ email: "a@x.com", status: "invite_updated" }]), false)).toBe(true);
    expect(isNoteDropped("hi", null, true)).toBe(true);
  });
  it("is false when everyone got a new invitation email", () => {
    expect(isNoteDropped("hi", summary([{ email: "a@x.com", status: "invited" }]), false)).toBe(false);
  });
});

// FORK: PSR-124 Part 2
describe("summariseInviteResults not_allowed details", () => {
  it("separates domain rejections from admin-only ones and counts both as problems", () => {
    const s = summariseInviteResults([
      { email: "a@x.com", status: "not_allowed", detail: "workspace_admin_only" },
      { email: "b@x.com", status: "not_allowed", detail: "domain_not_allowed" },
      { email: "c@x.com", status: "not_allowed" },
    ]);
    expect(s.notAllowed).toEqual(["a@x.com", "c@x.com"]);
    expect(s.notAllowedDomain).toEqual(["b@x.com"]);
    expect(s.problemEmails).toEqual(["a@x.com", "b@x.com", "c@x.com"]);
    expect(s.hasProblems).toBe(true);
  });

  it("separates renewals of Admin invitations that need a Super Admin", () => {
    const s = summariseInviteResults([
      { email: "a@x.com", status: "not_allowed", detail: "admin_invite_super_admin_only" },
    ]);
    expect(s.notAllowed).toEqual([]);
    expect(s.notAllowedAdminInvite).toEqual(["a@x.com"]);
    expect(s.problemEmails).toEqual(["a@x.com"]);
    expect(s.hasProblems).toBe(true);
  });
});
