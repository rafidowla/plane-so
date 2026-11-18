/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-124
import { describe, expect, it } from "vitest";
import { isValidEmail, parseEmails, summariseInviteResults } from "./project-invite-by-email";

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
  });
});
