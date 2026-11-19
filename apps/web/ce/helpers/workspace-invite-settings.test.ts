/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-124 Part 2
import { describe, expect, it } from "vitest";
import {
  canResendProjectInvite,
  fieldErrorText,
  parseDomains,
  parseExpiryDays,
  selectInviteHint,
} from "./workspace-invite-settings";

const settings = (over: Partial<Parameters<typeof selectInviteHint>[0] & object> = {}) => ({
  invite_expiry_days: 7,
  allowed_email_domains: [] as string[],
  project_admins_can_invite: false,
  ...over,
});

describe("parseDomains", () => {
  it("lowercases, strips @, splits on commas/spaces and de-duplicates", () => {
    expect(parseDomains("@Acme.com, acme.com  Foo.IO;bar.co")).toEqual({
      valid: ["acme.com", "foo.io", "bar.co"],
      invalid: [],
    });
  });
  it("de-duplicates against existing entries", () => {
    expect(parseDomains("a.com b.com", ["a.com"]).valid).toEqual(["b.com"]);
  });
  it("reports entries that are not hostnames, as typed", () => {
    const r = parseDomains("good.com bad_domain nodot -x.com a@b.com");
    expect(r.valid).toEqual(["good.com"]);
    expect(r.invalid).toEqual(["bad_domain", "nodot", "-x.com", "a@b.com"]);
  });
  it("ignores empty input", () => {
    expect(parseDomains("  , ;")).toEqual({ valid: [], invalid: [] });
  });
});

describe("parseExpiryDays", () => {
  it("accepts whole numbers 1..90", () => {
    expect(parseExpiryDays("1")).toBe(1);
    expect(parseExpiryDays(" 90 ")).toBe(90);
  });
  it("rejects everything else", () => {
    for (const v of ["", "0", "91", "-1", "1.5", "abc", "7 days"]) expect(parseExpiryDays(v)).toBeNull();
  });
});

describe("selectInviteHint", () => {
  it("shows nothing to workspace admins", () => {
    expect(selectInviteHint(settings({ project_admins_can_invite: true }), true)).toBeNull();
  });
  it("uses the strict wording when the setting is off or not loaded", () => {
    expect(selectInviteHint(settings(), false)).toEqual({ key: "admin_only", domains: [] });
    expect(selectInviteHint(null, false)).toEqual({ key: "admin_only", domains: [] });
  });
  it("says any domain is fine when on with no list", () => {
    expect(selectInviteHint(settings({ project_admins_can_invite: true }), false)).toEqual({
      key: "any_domain",
      domains: [],
    });
  });
  it("lists the domains with a leading @ when on with a list", () => {
    expect(
      selectInviteHint(settings({ project_admins_can_invite: true, allowed_email_domains: ["a.com", "b.com"] }), false)
    ).toEqual({ key: "domains", domains: ["@a.com", "@b.com"] });
  });
});

describe("canResendProjectInvite", () => {
  it("always allows workspace admins", () => {
    expect(canResendProjectInvite(true, true, null)).toBe(true);
  });
  it("allows project admins only when the setting is on", () => {
    expect(canResendProjectInvite(false, true, settings())).toBe(false);
    expect(canResendProjectInvite(false, true, null)).toBe(false);
    expect(canResendProjectInvite(false, true, settings({ project_admins_can_invite: true }))).toBe(true);
    expect(canResendProjectInvite(false, false, settings({ project_admins_can_invite: true }))).toBe(false);
  });
});

describe("fieldErrorText", () => {
  it("reads the first message of a field", () => {
    expect(fieldErrorText({ allowed_email_domains: ["Bad domain: x"] }, "allowed_email_domains")).toBe("Bad domain: x");
    expect(fieldErrorText({ other: ["x"] }, "allowed_email_domains")).toBeNull();
    expect(fieldErrorText(undefined, "a")).toBeNull();
  });
});
