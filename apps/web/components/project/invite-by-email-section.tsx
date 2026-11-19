/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-124 -- "Invite by email" section of the project invitation modal.
// State lives in `useInviteByEmailForm` (owned by the modal's form) so ONE submit button can send both the
// picked workspace members and the typed emails, and so nothing typed is lost on partial failure. The modal
// mounts a fresh form for every open, so this state never needs resetting by hand.

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { CloseOutline, ChevronDownOutline } from "@makeplane/propel/icons";
import { Select } from "@plane/blocks/select";
import { EUserPermissions, EUserPermissionsLevel, ROLE } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUserPermissions } from "@/hooks/store/user";
// plane web imports
import {
  MAX_INVITE_EMAILS,
  apiErrorText,
  chipsAfterSend,
  isNoteDropped,
  parseEmails,
  shouldCommitPastedEmails,
  shouldCommitTypedEmails,
  summariseInviteResults,
} from "@/plane-web/helpers/project-invite-by-email";
import type { TInviteSummary } from "@/plane-web/helpers/project-invite-by-email";

type TRoleOption = { key: number; label: string };

const BASE = "project_settings.members.invite_members.by_email";
const list = (items: string[]) => items.join(", ");

/** Max personal-note length; matches the API limit. */
export const INVITE_NOTE_MAX_LENGTH = 1000;

export const useInviteByEmailForm = (workspaceSlug: string, projectId: string) => {
  const { t } = useTranslation();
  const { allowPermissions, getProjectRoleByWorkspaceSlugAndProjectId } = useUserPermissions();
  const {
    project: { inviteMembersByEmail },
  } = useMember();
  const [emails, setEmails] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [projectRole, setProjectRole] = useState<number>(EUserPermissions.MEMBER);
  const [workspaceRole, setWorkspaceRole] = useState<number>(EUserPermissions.MEMBER);
  const [message, setMessage] = useState("");
  const [summary, setSummary] = useState<TInviteSummary | null>(null);
  /** How many picked member rows were added in the last submit (shown next to the email results). */
  const [membersAdded, setMembersAdded] = useState(0);
  const [formError, setFormError] = useState<string | null>(null);
  /** Typed text that isn't a valid email. It stays in the input so it is never lost. */
  const [invalidTokens, setInvalidTokens] = useState<string[]>([]);

  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const currentProjectRole = getProjectRoleByWorkspaceSlugAndProjectId(workspaceSlug, projectId);
  const projectRoleOptions: TRoleOption[] = Object.entries(ROLE)
    .filter(([key]) => isWorkspaceAdmin || parseInt(key) <= (currentProjectRole ?? EUserPermissions.GUEST))
    .map(([key, label]) => ({ key: parseInt(key), label }));
  const workspaceRoleOptions: TRoleOption[] = [EUserPermissions.MEMBER, EUserPermissions.GUEST].map((key) => ({
    key,
    label: ROLE[key],
  }));

  /**
   * Valid addresses become chips; anything else stays in the input (comma separated) with an error.
   * With `keepIfInvalid` (blur) the text is only turned into chips when ALL of it is valid; otherwise it
   * is left exactly as typed, with the error, so a half-typed "Name <a@x" is never rewritten.
   */
  const commitDraft = (raw: string, options?: { keepIfInvalid?: boolean }) => {
    if (!raw.trim()) {
      setDraft("");
      setInvalidTokens([]);
      return;
    }
    const parsed = parseEmails(raw, emails);
    if (options?.keepIfInvalid && parsed.invalid.length) {
      setDraft(raw);
      setInvalidTokens(parsed.invalid);
      return;
    }
    if (parsed.valid.length) {
      setEmails((prev) => [...prev, ...parsed.valid.filter((email) => !prev.includes(email))]);
    }
    setInvalidTokens(parsed.invalid);
    setDraft(parsed.invalid.join(", "));
    setFormError(null);
  };

  /**
   * Gather the emails to send (chips + whatever is typed). Returns null when the typed text has invalid
   * entries: the text is left as typed, with an error, and nothing should be sent yet.
   */
  const collect = (): string[] | null => {
    const parsed = draft.trim() ? parseEmails(draft, emails) : { valid: [] as string[], invalid: [] as string[] };
    if (parsed.invalid.length) {
      setInvalidTokens(parsed.invalid);
      return null;
    }
    const all = [...emails, ...parsed.valid];
    if (all.length > MAX_INVITE_EMAILS) {
      setFormError(t(`${BASE}.too_many`, { count: MAX_INVITE_EMAILS }));
      return null;
    }
    if (draft.trim()) {
      // the typed addresses become chips, so a failed send keeps them visible
      setEmails(all);
      setDraft("");
      setInvalidTokens([]);
    }
    return all;
  };

  /** Call the API for the given emails. Updates chips/summary; on a failed request sets formError, returns null. */
  const send = async (toSend: string[]): Promise<TInviteSummary | null> => {
    try {
      const response = await inviteMembersByEmail(workspaceSlug, projectId, {
        emails: toSend.map((email) => ({ email, role: projectRole as 5 | 15 | 20 })),
        ...(isWorkspaceAdmin ? { workspace_role: workspaceRole as 5 | 15 } : {}),
        message: message.trim() || null,
      });
      const result = summariseInviteResults(response?.results ?? [], toSend);
      // successful emails are done; anything that needs attention (or was not part of this send) stays
      setEmails((prev) => chipsAfterSend(prev, toSend, result.problemEmails));
      setSummary(result);
      return result;
    } catch (error) {
      setFormError(apiErrorText(error) ?? t(`${BASE}.failed`));
      return null;
    }
  };

  /** A plain edit of the box: the invalid-email warning is re-checked on commit/blur/submit, so drop it now. */
  const editDraft = (value: string) => {
    setDraft(value);
    setInvalidTokens([]);
  };

  return {
    emails,
    setEmails,
    draft,
    editDraft,
    invalidTokens,
    projectRole,
    setProjectRole,
    workspaceRole,
    setWorkspaceRole,
    message,
    setMessage,
    summary,
    setSummary,
    membersAdded,
    setMembersAdded,
    formError,
    setFormError,
    isWorkspaceAdmin,
    projectRoleOptions,
    workspaceRoleOptions,
    commitDraft,
    collect,
    send,
    /** True when there is anything typed or chipped to send. */
    hasInput: emails.length > 0 || draft.trim().length > 0,
  };
};

export type TInviteByEmailForm = ReturnType<typeof useInviteByEmailForm>;

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

type Props = {
  form: TInviteByEmailForm;
};

export const InviteByEmailSection = observer(function InviteByEmailSection(props: Props) {
  const { form } = props;
  const { t } = useTranslation();
  const { summary } = form;
  const addedCount = (summary?.added.length ?? 0) + form.membersAdded;

  return (
    <div className="space-y-3 border-t border-subtle pt-4">
      <div>
        <h4 className="text-14 font-medium">{t(`${BASE}.title`)}</h4>
        <p className="text-13 text-secondary">{t(`${BASE}.description`)}</p>
      </div>
      <div className="space-y-1">
        <div className="flex flex-wrap gap-1.5">
          {form.emails.map((email) => (
            <span
              key={email}
              className="flex items-center gap-1 rounded-full border border-subtle bg-layer-1 px-2 py-0.5 text-12"
            >
              {email}
              <button
                type="button"
                aria-label={t(`${BASE}.remove_email`, { email })}
                onClick={() => form.setEmails((prev) => prev.filter((e) => e !== email))}
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
            value={form.draft}
            placeholder={t(`${BASE}.email_placeholder`)}
            aria-label={t(`${BASE}.email_label`)}
            aria-invalid={form.invalidTokens.length > 0}
            onChange={(e) => {
              const value = e.target.value;
              // A paste is never intercepted: the browser inserts it (replacing any selection, at the caret)
              // and the resulting value is parsed here, like typed text.
              const pasted = (e.nativeEvent as InputEvent | undefined)?.inputType === "insertFromPaste";
              // commas/semicolons, a space after one bare valid address, or a pasted list turn the text into
              // chips; anything else (e.g. "John Doe <j@x.com>" being typed) stays in the box until Enter/blur
              if (pasted ? shouldCommitPastedEmails(value) : shouldCommitTypedEmails(value)) form.commitDraft(value);
              else form.editDraft(value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                // never submit the surrounding member form from here
                e.preventDefault();
                form.commitDraft(e.currentTarget.value);
              }
            }}
            onBlur={(e) => form.commitDraft(e.currentTarget.value, { keepIfInvalid: true })}
          />
        </InputGroup>
        {form.invalidTokens.length > 0 && (
          <p className="text-13 text-danger-primary">
            {t(`${BASE}.invalid_emails`, { emails: list(form.invalidTokens) })}
          </p>
        )}
        {!form.isWorkspaceAdmin && <p className="text-13 text-secondary">{t(`${BASE}.non_admin_hint`)}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2 text-13">
          <span className="text-secondary">{t(`${BASE}.project_role`)}</span>
          <RolePicker
            value={form.projectRole}
            options={form.projectRoleOptions}
            onChange={form.setProjectRole}
            label={t(`${BASE}.project_role`)}
          />
        </div>
        {form.isWorkspaceAdmin && (
          <div className="flex items-center gap-2 text-13">
            <span className="text-secondary">{t(`${BASE}.workspace_role`)}</span>
            <RolePicker
              value={form.workspaceRole}
              options={form.workspaceRoleOptions}
              onChange={form.setWorkspaceRole}
              label={t(`${BASE}.workspace_role`)}
            />
          </div>
        )}
      </div>
      <div className="space-y-1">
        <label htmlFor="invite-message" className="text-13 text-secondary">
          {t(`${BASE}.message_label`)}
        </label>
        <textarea
          id="invite-message"
          name="invite-message"
          rows={2}
          maxLength={INVITE_NOTE_MAX_LENGTH}
          value={form.message}
          placeholder={t(`${BASE}.message_placeholder`)}
          onChange={(e) => form.setMessage(e.target.value)}
          className="w-full resize-none rounded-md border border-subtle bg-surface-1 p-2 text-13 placeholder:text-placeholder focus:outline-none"
        />
      </div>
      {form.formError && <p className="text-13 text-danger-primary">{form.formError}</p>}
      {/* The modal is still open after a submit only when part of it failed: say what DID happen too, so
          rows/emails that were dealt with (and so left the form) never vanish without a word. */}
      {(summary || form.membersAdded > 0) && (
        <div className="space-y-1 rounded-md border border-subtle bg-layer-1 p-3 text-13" role="status">
          <p className="font-medium">{t(summary?.hasProblems ? `${BASE}.summary_title` : `${BASE}.results_title`)}</p>
          {addedCount > 0 && <p>{t(`${BASE}.summary_added`, { count: addedCount })}</p>}
          {!!summary?.invited.length && <p>{t(`${BASE}.summary_invited`, { count: summary.invited.length })}</p>}
          {!!summary?.inviteUpdated.length && (
            <p>{t(`${BASE}.summary_invite_updated`, { emails: list(summary.inviteUpdated) })}</p>
          )}
          {isNoteDropped(form.message, summary, form.membersAdded > 0) && <p>{t(`${BASE}.note_not_sent`)}</p>}
          {!!summary?.alreadyMember.length && (
            <p>{t(`${BASE}.summary_already_member`, { emails: list(summary.alreadyMember) })}</p>
          )}
          {!!summary?.notAllowed.length && (
            <p className="text-danger-primary">
              {t(`${BASE}.summary_not_allowed`, { emails: list(summary.notAllowed) })}
            </p>
          )}
          {!!summary?.invalid.length && (
            <p className="text-danger-primary">{t(`${BASE}.summary_invalid`, { emails: list(summary.invalid) })}</p>
          )}
          {!!summary?.failed.length && (
            <p className="text-danger-primary">{t(`${BASE}.summary_failed`, { emails: list(summary.failed) })}</p>
          )}
        </div>
      )}
    </div>
  );
});
