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
  isEmailDomainAllowed,
  parseDomains,
  parseExpiryDays,
  getSkippedInvites,
  resendDeniedReasonKey,
  selectInviteHint,
  skippedInviteReasonKey,
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

describe("canResendProjectInvite with the allowed domains list", () => {
  const on = settings({ project_admins_can_invite: true, allowed_email_domains: ["groundfloor.co"] });
  it("hides Resend from a project admin for a disallowed domain, shows it for an allowed one", () => {
    expect(canResendProjectInvite(false, true, on, "a@groundfloor.co")).toBe(true);
    expect(canResendProjectInvite(false, true, on, "a@Groundfloor.CO")).toBe(true);
    expect(canResendProjectInvite(false, true, on, "a@evil.com")).toBe(false);
    expect(canResendProjectInvite(false, true, on, "a@sub.groundfloor.co")).toBe(false);
  });
  it("never limits workspace admins by domain", () => {
    expect(canResendProjectInvite(true, true, on, "a@evil.com")).toBe(true);
  });
});

describe("isEmailDomainAllowed", () => {
  it("matches the part after the last @ exactly; an empty list allows all", () => {
    expect(isEmailDomainAllowed("x@any.com", [])).toBe(true);
    expect(isEmailDomainAllowed("a@b@groundfloor.co", ["groundfloor.co"])).toBe(true);
    expect(isEmailDomainAllowed("x@groundfloor.co.evil.com", ["groundfloor.co"])).toBe(false);
    expect(isEmailDomainAllowed("nodomain", ["groundfloor.co"])).toBe(false);
  });
});

describe("resendDeniedReasonKey", () => {
  it("maps server detail codes and the Super Admin message", () => {
    expect(resendDeniedReasonKey({ detail: "workspace_admin_only" })).toBe("resend_denied_workspace_admin_only");
    expect(resendDeniedReasonKey({ detail: "domain_not_allowed" })).toBe("resend_denied_domain_not_allowed");
    expect(resendDeniedReasonKey({ error: "Only a Super Admin of this workspace can do this." })).toBe(
      "resend_denied_super_admin_only"
    );
    expect(resendDeniedReasonKey({ error: "boom" })).toBeNull();
    expect(resendDeniedReasonKey(undefined)).toBeNull();
  });
});

describe("resendDeniedReasonKey error codes", () => {
  it("prefers error_code and keeps the text match as a fallback", () => {
    expect(resendDeniedReasonKey({ error: "any wording", error_code: "SUPER_ADMIN_REQUIRED" })).toBe(
      "resend_denied_super_admin_only"
    );
    expect(resendDeniedReasonKey({ error: "wait", error_code: "RESEND_COOLDOWN" })).toBe("resend_denied_cooldown");
    expect(resendDeniedReasonKey({ error: "x", error_code: "OTHER" })).toBeNull();
    expect(resendDeniedReasonKey({ error: "Only a Super Admin of this workspace can do this." })).toBe(
      "resend_denied_super_admin_only"
    );
  });
});

describe("skipped invites", () => {
  it("reads the skipped list defensively", () => {
    const skipped = [{ email: "a@b.co", reason: "cooldown" }];
    expect(getSkippedInvites({ message: "m", skipped })).toEqual(skipped);
    expect(getSkippedInvites({ skipped: [{ reason: "cooldown" }, null] })).toEqual([]);
    expect(getSkippedInvites({ message: "m" })).toEqual([]);
    expect(getSkippedInvites(undefined)).toEqual([]);
  });
  it("maps reasons to i18n keys", () => {
    expect(skippedInviteReasonKey("super_admin_required")).toBe("super_admin_required");
    expect(skippedInviteReasonKey("cooldown")).toBe("cooldown");
  });
});

describe("fieldErrorText", () => {
  it("reads the first message of a field", () => {
    expect(fieldErrorText({ allowed_email_domains: ["Bad domain: x"] }, "allowed_email_domains")).toBe("Bad domain: x");
    expect(fieldErrorText({ other: ["x"] }, "allowed_email_domains")).toBeNull();
    expect(fieldErrorText(undefined, "a")).toBeNull();
  });
});
