/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { useForm, Controller, useFieldArray } from "react-hook-form";
// plane imports
import { ROLE, EUserPermissions } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import { AddOutline, ChevronDownOutline, CloseOutline } from "@makeplane/propel/icons";
import { setToast } from "@plane/blocks/toast";
import { Select } from "@plane/blocks/select";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
// components
import { MemberSelect } from "@/components/dropdowns/member/member-select";
// FORK: PSR-124
import { InviteByEmailSection, useInviteByEmailForm } from "./invite-by-email-section";
import { apiErrorText, isNoteDropped } from "@/plane-web/helpers/project-invite-by-email";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  projectId: string;
  workspaceSlug: string;
};

type member = {
  role: EUserPermissions;
  member_id: string;
};

type FormValues = {
  members: member[];
};

type TRoleOption = {
  key: EUserPermissions;
  label: string;
};

const defaultValues = (): FormValues => ({
  members: [
    {
      role: 5,
      member_id: "",
    },
  ],
});

export const SendProjectInvitationModal = observer(function SendProjectInvitationModal(props: Props) {
  const { isOpen, onClose, onSuccess, projectId, workspaceSlug } = props;
  // FORK: PSR-124 -- every open gets a brand-new form (new key), so nothing from an earlier invitation -- note,
  // chips, results, picked rows -- can show, not even for one frame. The key is derived DURING render (React's
  // "adjust state when a prop changes" pattern), not in an effect, which would paint the old state first; no
  // timers are needed either, because the closing dialog keeps showing the old form until it unmounts.
  const [session, setSession] = useState(0);
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) setSession((value) => value + 1);
  }
  // FORK: PSR-124 -- while a submit is in flight the dialog cannot be dismissed, so its outcome is always seen
  const busyRef = useRef(false);

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open && !busyRef.current) onClose();
      }}
    >
      <DialogContent size="md">
        <ProjectInvitationForm
          key={session}
          onClose={onClose}
          onSuccess={onSuccess}
          onBusyChange={(busy) => {
            busyRef.current = busy;
          }}
          projectId={projectId}
          workspaceSlug={workspaceSlug}
        />
      </DialogContent>
    </Dialog>
  );
});

type FormProps = Omit<Props, "isOpen"> & {
  onBusyChange: (busy: boolean) => void;
};

const ProjectInvitationForm = observer(function ProjectInvitationForm(props: FormProps) {
  const { onClose, onSuccess, onBusyChange, projectId, workspaceSlug } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectRoleByWorkspaceSlugAndProjectId } = useUserPermissions();
  const {
    project: { getProjectMemberDetails, bulkAddMembersToProject },
    workspace: { workspaceMemberIds, getWorkspaceMemberDetails },
  } = useMember();
  // FORK: PSR-124 -- state of the "invite by email" section; one submit sends both it and the member rows
  const emailForm = useInviteByEmailForm(workspaceSlug, projectId);
  const submittingRef = useRef(false);
  // form info
  const {
    formState: { errors, isSubmitting },
    watch,
    setValue,
    reset,
    handleSubmit,
    control,
  } = useForm<FormValues>({ defaultValues: defaultValues() });
  const { fields, append, remove } = useFieldArray({
    control,
    name: "members",
  });
  // derived values
  const currentProjectRole = getProjectRoleByWorkspaceSlugAndProjectId(workspaceSlug, projectId);
  const uninvitedPeople =
    workspaceMemberIds?.filter((userId) => {
      if (!getWorkspaceMemberDetails(userId)?.member) return false;
      const projectMemberDetails = getProjectMemberDetails(userId, projectId);
      const isInvited = projectMemberDetails?.member.id && projectMemberDetails?.original_role;
      return !isInvited;
    }) ?? [];

  // FORK: PSR-124 -- ONE submit: picked member rows go through bulk add, typed emails through invite-by-email.
  // Close only if everything succeeded; otherwise keep failed emails / unsent rows in place.
  // FORK: PSR-124 -- submit is enabled only when there is something to send
  const hasPickedRows = (watch("members") ?? []).some((row) => row?.member_id);
  const submitLabel = (() => {
    const ellipsis = isSubmitting ? "..." : "";
    if (emailForm.hasInput) {
      const by = "project_settings.members.invite_members.by_email";
      return hasPickedRows ? `${t(`${by}.submit_both`)}${ellipsis}` : `${t(`${by}.submit`)}${ellipsis}`;
    }
    return `${fields && fields.length > 1 ? t("add_members") : t("add_member")}${ellipsis}`;
  })();

  const onSubmit = async (formData: FormValues) => {
    // a ref, not `isSubmitting`: the latter is a stale render closure, so it cannot stop a quick double click
    if (!workspaceSlug || !projectId || submittingRef.current) return;
    submittingRef.current = true;
    onBusyChange(true);
    try {
      await submit(formData);
    } finally {
      submittingRef.current = false;
      onBusyChange(false);
    }
  };

  const submit = async (formData: FormValues) => {
    const rows = formData.members.filter((row) => row.member_id);
    const emails = emailForm.collect();
    // invalid typed text (or too many emails): shown in place, nothing is sent
    if (emails === null) return;
    if (rows.length === 0 && emails.length === 0) {
      emailForm.setFormError(t("project_settings.members.invite_members.by_email.nothing_to_send"));
      return;
    }
    emailForm.setFormError(null);
    emailForm.setSummary(null);
    emailForm.setMembersAdded(0);

    const [rowsResult, emailsResult] = await Promise.allSettled([
      rows.length > 0
        ? bulkAddMembersToProject(workspaceSlug.toString(), projectId.toString(), { members: rows })
        : Promise.resolve(null),
      emails.length > 0 ? emailForm.send(emails) : Promise.resolve(null),
    ]);

    const rowsOk = rows.length > 0 && rowsResult.status === "fulfilled";
    const rowsFailed = rows.length > 0 && rowsResult.status === "rejected";
    const emailSummary = emailsResult.status === "fulfilled" ? emailsResult.value : null;
    // `send` returns null (and sets its own error) when the request itself failed
    const emailsFailed = emails.length > 0 && !emailSummary;

    if (rowsOk) {
      emailForm.setMembersAdded(rows.length);
      // these people are members now: drop them from the form so they are never re-sent
      reset(defaultValues());
      onSuccess?.();
    }
    if (rowsFailed) {
      console.error(rowsResult.reason);
      emailForm.setFormError(
        apiErrorText(rowsResult.reason) ?? t("project_settings.members.invite_members.by_email.members_failed")
      );
    }

    if (rowsFailed || emailsFailed || emailSummary?.hasProblems) return;

    // everything succeeded
    const by = "project_settings.members.invite_members.by_email";
    const addedCount = (rowsOk ? rows.length : 0) + (emailSummary?.added.length ?? 0);
    const messageParts = [
      addedCount ? t(`${by}.summary_added`, { count: addedCount }) : null,
      emailSummary?.invited.length ? t(`${by}.summary_invited`, { count: emailSummary.invited.length }) : null,
      emailSummary?.inviteUpdated.length
        ? t(`${by}.summary_invite_updated`, { emails: emailSummary.inviteUpdated.join(", ") })
        : null,
      isNoteDropped(emailForm.message, emailSummary, rowsOk) ? t(`${by}.note_not_sent`) : null,
    ].filter(Boolean);
    setToast({ title: t(`${by}.success_title`), type: "success", message: messageParts.join(". ") });
    // the busy flag is still set here (cleared in onSubmit's finally), so close directly
    onClose();
  };

  const appendField = () => {
    append({
      role: 5,
      member_id: "",
    });
  };

  useEffect(() => {
    if (fields.length === 0) {
      append([
        {
          role: 5,
          member_id: "",
        },
      ]);
    }
  }, [fields, append]);

  const checkCurrentOptionWorkspaceRole = (value: string) => {
    const currentMemberWorkspaceRole = getWorkspaceMemberDetails(value)?.role;
    if (!value || !currentMemberWorkspaceRole) return ROLE;

    const isGuestOROwner = [EUserPermissions.ADMIN, EUserPermissions.GUEST].includes(
      currentMemberWorkspaceRole as EUserPermissions
    );

    return Object.fromEntries(
      Object.entries(ROLE).filter(([key]) => !isGuestOROwner || [currentMemberWorkspaceRole].includes(parseInt(key)))
    );
  };

  const getRoleOptions = (memberId: string): TRoleOption[] =>
    Object.entries(checkCurrentOptionWorkspaceRole(memberId))
      .filter(([key]) => parseInt(key) <= (currentProjectRole ?? EUserPermissions.GUEST))
      .map(([key, label]) => ({ key: parseInt(key) as EUserPermissions, label }));

  const handleMemberChange = (index: number, memberId: string, onChange: (val: string) => void) => {
    onChange(memberId);
    // Update the role to the workspace role when member ID changes
    const workspaceMemberDetails = getWorkspaceMemberDetails(memberId);
    const workspaceRole = workspaceMemberDetails?.role ?? 5;
    const newValue = ROLE[workspaceRole].toUpperCase();
    setValue(`members.${index}.role`, EUserPermissions[newValue as keyof typeof EUserPermissions]);
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex min-h-0 flex-1 flex-col">
      <DialogMain>
        <DialogHeader>
          <DialogHeading>
            <DialogTitle>{t("project_settings.members.invite_members.title")}</DialogTitle>
            <DialogDescription>{t("project_settings.members.invite_members.sub_heading")}</DialogDescription>
          </DialogHeading>
        </DialogHeader>
        <DialogBody>
          {/* FORK: PSR-124 -- nothing can be edited while a submit is in flight, so its result can't
                  overwrite something typed or picked meanwhile */}
          <fieldset disabled={isSubmitting} className="m-0 min-w-0 border-0 p-0">
            <div className="space-y-4">
              {fields.map((field, index) => (
                <div key={field.id} className="group mb-1 flex w-full items-start justify-between gap-x-4 text-body-xs-regular">
                  <div className="flex w-full grow flex-col gap-1">
                    <Controller
                      control={control}
                      name={`members.${index}.member_id`}
                      render={({ field: { value, onChange } }) => (
                        <MemberSelect
                          value={value || null}
                          onChange={(val: string) => handleMemberChange(index, val, onChange)}
                          variant="select-lg"
                          memberIds={uninvitedPeople}
                          placeholder="Select co-worker"
                        />
                      )}
                    />
                    {errors.members && errors.members[index]?.member_id && (
                      <span className="px-1 text-body-xs-regular text-danger-primary">
                        {errors.members[index]?.member_id?.message}
                      </span>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center justify-between gap-2">
                    <div className="flex flex-col gap-1">
                      <Controller
                        name={`members.${index}.role`}
                        control={control}
                        rules={{ required: "Select Role" }}
                        render={({ field: { value, onChange } }) => {
                          const roleOptions = getRoleOptions(watch(`members.${index}.member_id`));
                          return (
                            <div className="w-32">
                              <Select<TRoleOption>
                                getValues={() => roleOptions}
                                value={roleOptions.find((role) => role.key === value) ?? null}
                                onChange={(key) => onChange(parseInt(key))}
                                getOptionValue={(role) => String(role.key)}
                                getOptionLabel={(role) => role.label}
                                placeholder="Select role"
                                showSearch={false}
                                pinSelected={false}
                              >
                                <Select.Trigger<TRoleOption>
                                  variant="pill-md"
                                  appendIcon={<ChevronDownOutline />}
                                  tooltip={{ emptyContent: "Select role" }}
                                >
                                  <span className="min-w-0 grow truncate text-left capitalize">
                                    {value ? ROLE[value] : "Select role"}
                                  </span>
                                </Select.Trigger>
                              </Select>
                            </div>
                          );
                        }}
                      />
                      {errors.members && errors.members[index]?.role && (
                        <span className="px-1 text-body-xs-regular text-danger-primary">{errors.members[index]?.role?.message}</span>
                      )}
                    </div>

                    {fields.length > 1 && (
                      <div className="flex-item flex w-6">
                        <button
                          type="button"
                          aria-label={t("remove")}
                          className="place-items-center self-center rounded-sm"
                          onClick={() => remove(index)}
                        >
                          <CloseOutline className="h-4 w-4 text-secondary" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {/* FORK: PSR-124 -- invite people who aren't in the workspace yet, by email */}
            <div className="mt-4">
              <InviteByEmailSection form={emailForm} />
            </div>
          </fieldset>
        </DialogBody>
      </DialogMain>
      <DialogActions>
        <div className="flex w-full items-center justify-between gap-2">
          <button
            type="button"
            className="flex items-center gap-2 bg-transparent py-2 pr-3 text-body-xs-medium text-accent-primary outline-accent-strong"
            onClick={appendField}
            disabled={isSubmitting}
          >
            <AddOutline className="h-4 w-4" />
            {t("common.add_more")}
          </button>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={t("cancel")}
              onClick={onClose}
              disabled={isSubmitting}
            />
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              type="submit"
              disabled={!hasPickedRows && !emailForm.hasInput}
              label={submitLabel}
              loading={isSubmitting}
            />
          </div>
        </div>
      </DialogActions>
    </form>
  );
});
