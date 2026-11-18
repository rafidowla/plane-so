/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- "Invite by email" section of the project invitation modal.

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { Button } from "@makeplane/propel/components/button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { CloseOutline, ChevronDownOutline } from "@makeplane/propel/icons";
import { Select } from "@plane/blocks/select";
import { setToast } from "@plane/blocks/toast";
import { EUserPermissions, EUserPermissionsLevel, ROLE } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUserPermissions } from "@/hooks/store/user";
// plane web imports
import { MAX_INVITE_EMAILS, parseEmails, summariseInviteResults } from "@/plane-web/helpers/project-invite-by-email";
import type { TInviteSummary } from "@/plane-web/helpers/project-invite-by-email";

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** Called after at least one person was added directly (so the parent can refresh/close on full success). */
  onSuccess?: () => void;
  onDone: () => void;
};

type TRoleOption = { key: number; label: string };

function RolePicker(props: { value: number; options: TRoleOption[]; onChange: (role: number) => void; label: string }) {
  const { value, options, onChange, label } = props;
  return (
    <div className="w-32">
      <Select<TRoleOption>
        getValues={() => options}
        value={options.find((role) => role.key === value) ?? null}
        onChange={(key) => onChange(parseInt(key))}
        getOptionValue={(role) => String(role.key)}
        getOptionLabel={(role) => role.label}
        placeholder={label}
        showSearch={false}
        pinSelected={false}
      >
        <Select.Trigger<TRoleOption>
          variant="pill-md"
          appendIcon={<ChevronDownOutline />}
          tooltip={{ emptyContent: label }}
        >
          <span className="min-w-0 grow truncate text-left capitalize">
            {ROLE[value as keyof typeof ROLE] ?? label}
          </span>
        </Select.Trigger>
      </Select>
    </div>
  );
}

const list = (items: string[]) => items.join(", ");

export const InviteByEmailSection = observer(function InviteByEmailSection(props: Props) {
  const { workspaceSlug, projectId, onSuccess, onDone } = props;
  const { t } = useTranslation();
  const { allowPermissions, getProjectRoleByWorkspaceSlugAndProjectId } = useUserPermissions();
  const {
    project: { inviteMembersByEmail },
  } = useMember();
  // state
  const [emails, setEmails] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [invalidTokens, setInvalidTokens] = useState<string[]>([]);
  const [projectRole, setProjectRole] = useState<number>(EUserPermissions.MEMBER);
  const [workspaceRole, setWorkspaceRole] = useState<number>(EUserPermissions.MEMBER);
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [summary, setSummary] = useState<TInviteSummary | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // derived values
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const currentProjectRole = getProjectRoleByWorkspaceSlugAndProjectId(workspaceSlug, projectId);
  const projectRoleOptions: TRoleOption[] = Object.entries(ROLE)
    .filter(([key]) => isWorkspaceAdmin || parseInt(key) <= (currentProjectRole ?? EUserPermissions.GUEST))
    .map(([key, label]) => ({ key: parseInt(key), label }));
  const workspaceRoleOptions: TRoleOption[] = [EUserPermissions.MEMBER, EUserPermissions.GUEST].map((key) => ({
    key,
    label: ROLE[key],
  }));

  const commitDraft = (raw: string) => {
    if (!raw.trim()) return;
    const parsed = parseEmails(raw, emails);
    setEmails((prev) => [...prev, ...parsed.valid]);
    setInvalidTokens(parsed.invalid);
    setDraft("");
    setFormError(null);
  };

  const handleSubmit = async () => {
    // pick up whatever is still typed in the box
    const pending = draft.trim() ? parseEmails(draft, emails) : { valid: [] as string[], invalid: [] as string[] };
    const all = [...emails, ...pending.valid];
    if (pending.valid.length || pending.invalid.length) {
      setEmails(all);
      setInvalidTokens(pending.invalid);
      setDraft("");
    }
    if (pending.invalid.length) return;
    if (all.length === 0) {
      setFormError(t("project_settings.members.invite_members.by_email.nothing_to_send"));
      return;
    }
    if (all.length > MAX_INVITE_EMAILS) {
      setFormError(t("project_settings.members.invite_members.by_email.too_many", { count: MAX_INVITE_EMAILS }));
      return;
    }
    setIsSubmitting(true);
    setFormError(null);
    try {
      const response = await inviteMembersByEmail(workspaceSlug, projectId, {
        emails: all.map((email) => ({ email, role: projectRole as 5 | 15 | 20 })),
        ...(isWorkspaceAdmin ? { workspace_role: workspaceRole as 5 | 15 } : {}),
        message: message.trim() || null,
      });
      const result = summariseInviteResults(response?.results ?? []);
      if (result.added.length > 0) onSuccess?.();
      if (!result.hasProblems) {
        setToast({
          type: "success",
          title: t("project_settings.members.invite_members.by_email.success_title"),
          message: [
            result.added.length
              ? t("project_settings.members.invite_members.by_email.summary_added", { count: result.added.length })
              : null,
            result.invited.length
              ? t("project_settings.members.invite_members.by_email.summary_invited", { count: result.invited.length })
              : null,
          ]
            .filter(Boolean)
            .join(". "),
        });
        onDone();
        return;
      }
      // keep the modal open: only the emails that still need attention stay as chips
      setSummary(result);
      setEmails(result.problemEmails);
    } catch (error) {
      const detail = (error as { error?: unknown; detail?: unknown } | undefined) ?? {};
      setFormError(
        typeof detail.error === "string"
          ? detail.error
          : typeof detail.detail === "string"
            ? detail.detail
            : t("project_settings.members.invite_members.by_email.failed")
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const base = "project_settings.members.invite_members.by_email";

  return (
    <div className="space-y-3 border-t border-subtle pt-4">
      <div>
        <h4 className="text-14 font-medium">{t(`${base}.title`)}</h4>
        <p className="text-13 text-secondary">{t(`${base}.description`)}</p>
      </div>
      <div className="space-y-1">
        <div className="flex flex-wrap gap-1.5">
          {emails.map((email) => (
            <span
              key={email}
              className="flex items-center gap-1 rounded-full border border-subtle bg-layer-1 px-2 py-0.5 text-12"
            >
              {email}
              <button
                type="button"
                aria-label={t(`${base}.remove_email`, { email })}
                onClick={() => setEmails((prev) => prev.filter((e) => e !== email))}
              >
                <CloseOutline className="h-3 w-3 text-secondary" />
              </button>
            </span>
          ))}
        </div>
        <InputGroup size="lg">
          <Input
            size="lg"
            name="invite-emails"
            type="text"
            value={draft}
            placeholder={t(`${base}.email_placeholder`)}
            aria-label={t(`${base}.email_label`)}
            onChange={(e) => {
              const value = e.target.value;
              // a separator typed or pasted turns the text before it into chips
              if (/[\s,;]/.test(value)) commitDraft(value);
              else setDraft(value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                // never submit the surrounding member form from here
                e.preventDefault();
                commitDraft(draft);
              }
            }}
            onBlur={() => commitDraft(draft)}
          />
        </InputGroup>
        {invalidTokens.length > 0 && (
          <p className="text-13 text-danger-primary">{t(`${base}.invalid_emails`, { emails: list(invalidTokens) })}</p>
        )}
        {!isWorkspaceAdmin && <p className="text-13 text-secondary">{t(`${base}.non_admin_hint`)}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2 text-13">
          <span className="text-secondary">{t(`${base}.project_role`)}</span>
          <RolePicker
            value={projectRole}
            options={projectRoleOptions}
            onChange={setProjectRole}
            label={t(`${base}.project_role`)}
          />
        </div>
        {isWorkspaceAdmin && (
          <div className="flex items-center gap-2 text-13">
            <span className="text-secondary">{t(`${base}.workspace_role`)}</span>
            <RolePicker
              value={workspaceRole}
              options={workspaceRoleOptions}
              onChange={setWorkspaceRole}
              label={t(`${base}.workspace_role`)}
            />
          </div>
        )}
      </div>
      <div className="space-y-1">
        <label htmlFor="invite-message" className="text-13 text-secondary">
          {t(`${base}.message_label`)}
        </label>
        <textarea
          id="invite-message"
          name="invite-message"
          rows={2}
          maxLength={500}
          value={message}
          placeholder={t(`${base}.message_placeholder`)}
          onChange={(e) => setMessage(e.target.value)}
          className="w-full resize-none rounded-md border border-subtle bg-surface-1 p-2 text-13 placeholder:text-placeholder focus:outline-none"
        />
      </div>
      {formError && <p className="text-13 text-danger-primary">{formError}</p>}
      {summary?.hasProblems && (
        <div className="space-y-1 rounded-md border border-subtle bg-layer-1 p-3 text-13" role="status">
          <p className="font-medium">{t(`${base}.summary_title`)}</p>
          {summary.added.length > 0 && <p>{t(`${base}.summary_added`, { count: summary.added.length })}</p>}
          {summary.invited.length > 0 && <p>{t(`${base}.summary_invited`, { count: summary.invited.length })}</p>}
          {summary.alreadyMember.length > 0 && (
            <p>{t(`${base}.summary_already_member`, { emails: list(summary.alreadyMember) })}</p>
          )}
          {summary.notAllowed.length > 0 && (
            <p className="text-danger-primary">
              {t(`${base}.summary_not_allowed`, { emails: list(summary.notAllowed) })}
            </p>
          )}
          {summary.invalid.length > 0 && (
            <p className="text-danger-primary">{t(`${base}.summary_invalid`, { emails: list(summary.invalid) })}</p>
          )}
        </div>
      )}
      <div className="flex justify-end">
        <Button
          variant="primary"
          size="md"
          stretch="auto"
          type="button"
          onClick={handleSubmit}
          loading={isSubmitting}
          label={isSubmitting ? t(`${base}.submitting`) : t(`${base}.submit`)}
        />
      </div>
    </div>
  );
});
