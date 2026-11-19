/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- "Invitation settings" on the workspace Members settings page. Workspace Admins only
// (the server enforces it too): expiry in days, allowed email domains, and whether Project Admins can invite.

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { Switch } from "@makeplane/propel/components/switch";
import { CloseOutline } from "@makeplane/propel/icons";
import { setToast } from "@plane/blocks/toast";
import { useTranslation } from "@plane/i18n";
import type { TWorkspaceInviteSettings } from "@plane/types";
// hooks
import { useMember } from "@/hooks/store/use-member";
// plane web imports
import {
  MAX_ALLOWED_DOMAINS,
  MAX_INVITE_EXPIRY_DAYS,
  MIN_INVITE_EXPIRY_DAYS,
  fieldErrorText,
  parseDomains,
  parseExpiryDays,
} from "@/plane-web/helpers/workspace-invite-settings";

const BASE = "workspace_settings.settings.members.invite_settings";

type TFormProps = {
  workspaceSlug: string;
  settings: TWorkspaceInviteSettings;
};

const InviteSettingsForm = observer(function InviteSettingsForm(props: TFormProps) {
  const { workspaceSlug, settings } = props;
  const { t } = useTranslation();
  const {
    workspace: { updateInviteSettings },
  } = useMember();
  const [days, setDays] = useState(String(settings.invite_expiry_days));
  const [domains, setDomains] = useState<string[]>(settings.allowed_email_domains);
  const [draft, setDraft] = useState("");
  const [projectAdminsCanInvite, setProjectAdminsCanInvite] = useState(settings.project_admins_can_invite);
  const [daysError, setDaysError] = useState<string | null>(null);
  const [domainsError, setDomainsError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  /**
   * Valid domains become chips; anything else stays in the box (as typed) with an error. Returns the domain
   * list after the commit and whether everything typed was valid.
   */
  const commitDraft = (raw: string): { next: string[]; ok: boolean } => {
    if (!raw.trim()) {
      setDraft("");
      return { next: domains, ok: true };
    }
    const parsed = parseDomains(raw, domains);
    const next = [...domains, ...parsed.valid];
    setDomains(next);
    setDraft(parsed.invalid.join(", "));
    setDomainsError(
      parsed.invalid.length ? t(`${BASE}.domains_invalid`, { domains: parsed.invalid.join(", ") }) : null
    );
    return { next, ok: parsed.invalid.length === 0 };
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    const parsedDays = parseExpiryDays(days);
    setDaysError(parsedDays === null ? t(`${BASE}.expiry_error`) : null);
    const { next, ok } = commitDraft(draft);
    if (parsedDays === null || !ok) return;
    if (next.length > MAX_ALLOWED_DOMAINS) {
      setDomainsError(t(`${BASE}.domains_too_many`, { count: MAX_ALLOWED_DOMAINS }));
      return;
    }
    setIsSaving(true);
    try {
      const saved = await updateInviteSettings(workspaceSlug, {
        invite_expiry_days: parsedDays,
        allowed_email_domains: next,
        project_admins_can_invite: projectAdminsCanInvite,
      });
      // show what the server stored (it normalises the domains)
      setDays(String(saved.invite_expiry_days));
      setDomains(saved.allowed_email_domains);
      setDomainsError(null);
      setToast({ type: "success", title: t("success"), message: t(`${BASE}.saved`) });
    } catch (error) {
      const daysMessage = fieldErrorText(error, "invite_expiry_days");
      const domainsMessage = fieldErrorText(error, "allowed_email_domains");
      if (daysMessage) setDaysError(daysMessage);
      if (domainsMessage) setDomainsError(domainsMessage);
      if (!daysMessage && !domainsMessage) {
        const body = error as { error?: string; detail?: string } | undefined;
        setToast({
          type: "error",
          title: t("error"),
          message: body?.error ?? body?.detail ?? t(`${BASE}.save_failed`),
        });
      }
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form onSubmit={(e) => void handleSave(e)} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <label htmlFor="invite-expiry-days" className="text-body-xs-medium">
          {t(`${BASE}.expiry_label`)}
        </label>
        <div className="flex items-center gap-2">
          <div className="w-24">
            <InputGroup size="lg">
              <Input
                size="lg"
                id="invite-expiry-days"
                name="invite-expiry-days"
                type="number"
                min={MIN_INVITE_EXPIRY_DAYS}
                max={MAX_INVITE_EXPIRY_DAYS}
                value={days}
                aria-invalid={!!daysError}
                onChange={(e) => {
                  setDays(e.target.value);
                  setDaysError(null);
                }}
              />
            </InputGroup>
          </div>
          <span className="text-body-xs-regular text-secondary">{t(`${BASE}.expiry_unit`)}</span>
        </div>
        {daysError && (
          <p role="alert" className="text-caption-md-regular text-danger-primary">
            {daysError}
          </p>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="invite-allowed-domains" className="text-body-xs-medium">
          {t(`${BASE}.domains_label`)}
        </label>
        {domains.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {domains.map((domain) => (
              <span
                key={domain}
                className="flex items-center gap-1 rounded-full border border-subtle bg-layer-1 px-2 py-0.5 text-12"
              >
                @{domain}
                <button
                  type="button"
                  aria-label={t(`${BASE}.domains_remove`, { domain })}
                  onClick={() => {
                    setDomains((prev) => prev.filter((d) => d !== domain));
                    setDomainsError(null);
                  }}
                >
                  <CloseOutline className="h-3 w-3 text-secondary" />
                </button>
              </span>
            ))}
          </div>
        )}
        <InputGroup size="lg">
          <Input
            size="lg"
            id="invite-allowed-domains"
            name="invite-allowed-domains"
            type="text"
            value={draft}
            placeholder={t(`${BASE}.domains_placeholder`)}
            aria-invalid={!!domainsError}
            onChange={(e) => {
              const value = e.target.value;
              // a comma or semicolon ends an entry, like Enter does
              if (/[,;]/.test(value)) commitDraft(value);
              else {
                setDraft(value);
                setDomainsError(null);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                // never submit the form from the chip box
                e.preventDefault();
                commitDraft(e.currentTarget.value);
              }
            }}
            onBlur={(e) => commitDraft(e.currentTarget.value)}
          />
        </InputGroup>
        <p className="text-caption-md-regular text-secondary">{t(`${BASE}.domains_help`)}</p>
        {domainsError && (
          <p role="alert" className="text-caption-md-regular text-danger-primary">
            {domainsError}
          </p>
        )}
      </div>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <span id="invite-project-admins-label" className="text-body-xs-medium">
            {t(`${BASE}.project_admins_label`)}
          </span>
          <span className="text-caption-md-regular text-secondary">{t(`${BASE}.project_admins_help`)}</span>
        </div>
        <Switch
          size="sm"
          checked={projectAdminsCanInvite}
          onCheckedChange={setProjectAdminsCanInvite}
          aria-labelledby="invite-project-admins-label"
        />
      </div>
      <div>
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          type="submit"
          label={t(`${BASE}.save`)}
          loading={isSaving}
          disabled={isSaving}
        />
      </div>
    </form>
  );
});

type TProps = { workspaceSlug: string };

export const WorkspaceInviteSettingsSection = observer(function WorkspaceInviteSettingsSection(props: TProps) {
  const { workspaceSlug } = props;
  const { t } = useTranslation();
  const {
    workspace: { getInviteSettings, fetchInviteSettings },
  } = useMember();
  const { error } = useSWR(
    workspaceSlug ? `WORKSPACE_INVITE_SETTINGS_${workspaceSlug}` : null,
    () => fetchInviteSettings(workspaceSlug),
    { revalidateOnFocus: false }
  );
  const settings = getInviteSettings(workspaceSlug);

  return (
    <section className="mb-6 flex flex-col gap-3 rounded-md border border-subtle p-4">
      <div>
        <h4 className="text-h4-medium">{t(`${BASE}.title`)}</h4>
        <p className="text-body-xs-regular text-secondary">{t(`${BASE}.description`)}</p>
      </div>
      {settings ? (
        <InviteSettingsForm key={JSON.stringify(settings)} workspaceSlug={workspaceSlug} settings={settings} />
      ) : error ? (
        <p role="alert" className="text-caption-md-regular text-danger-primary">
          {t(`${BASE}.load_failed`)}
        </p>
      ) : null}
    </section>
  );
});
