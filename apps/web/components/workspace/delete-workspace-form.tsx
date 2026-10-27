/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- deleting a workspace re-confirms who you are: type its name, then your password,
// or (accounts without a password) a code we email you. The workspace stays restorable until purge.

import { useState } from "react";
import { observer } from "mobx-react";
import { Controller, useForm } from "react-hook-form";
import { WarningTriangleOutline } from "@makeplane/propel/icons";
// Plane Imports
import { Field } from "@makeplane/propel/components/field";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/components/button";
import {
  DialogActions,
  DialogBody,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
import { setToast } from "@plane/blocks/toast";
import type { IWorkspace } from "@plane/types";

import { cn } from "@plane/utils";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUser, useUserSettings } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { WorkspaceService } from "@/services/workspace.service";

const workspaceService = new WorkspaceService();

type Props = {
  data: IWorkspace | null;
  onClose: () => void;
};

type TMode = "password" | "code";

const defaultValues = {
  workspaceName: "",
  secret: "",
};

const PREFIX = "workspace_settings.settings.general.delete_modal";

// API error codes -> i18n keys shown under the form.
const ERROR_KEYS: Record<string, string> = {
  WORKSPACE_NAME_MISMATCH: `${PREFIX}.errors.name_mismatch`,
  PASSWORD_INCORRECT: `${PREFIX}.errors.password_incorrect`,
  CODE_INVALID: `${PREFIX}.errors.code_invalid`,
  TOO_MANY_ATTEMPTS: `${PREFIX}.errors.too_many_attempts`,
  TOO_MANY_CODES: `${PREFIX}.errors.too_many_codes`,
};

type TApiError = { error?: string; error_code?: string } | undefined;

export const DeleteWorkspaceForm = observer(function DeleteWorkspaceForm(props: Props) {
  const { data, onClose } = props;
  // router
  const router = useAppRouter();
  // store hooks
  const { deleteWorkspace, getWorkspaceRedirectionUrl } = useWorkspace();
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  const { fetchCurrentUserSettings } = useUserSettings();
  // state
  const [mode, setMode] = useState<TMode>(currentUser?.is_password_autoset ? "code" : "password");
  const [codeSent, setCodeSent] = useState(false);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  // form info
  const {
    control,
    formState: { isSubmitting },
    handleSubmit,
    reset,
    watch,
  } = useForm({ defaultValues });

  const nameMatches = !!data && watch("workspaceName").trim() === data.name.trim();
  const canDelete = nameMatches && watch("secret").trim() !== "" && (mode === "password" || codeSent);

  const showError = (err: TApiError) => {
    const key = err?.error_code ? ERROR_KEYS[err.error_code] : undefined;
    setErrorText(key ? t(key) : (err?.error ?? t(`${PREFIX}.error_message`)));
  };

  const handleClose = () => {
    const timer = setTimeout(() => {
      reset(defaultValues);
      setCodeSent(false);
      setErrorText(null);
      clearTimeout(timer);
    }, 350);

    onClose();
  };

  const sendCode = async () => {
    if (!data) return;
    setIsSendingCode(true);
    setErrorText(null);
    try {
      await workspaceService.requestWorkspaceDeleteCode(data.slug);
      setCodeSent(true);
    } catch (err) {
      const apiError = err as TApiError;
      // The server knows best which check applies to this account.
      if (apiError?.error_code === "USE_PASSWORD") setMode("password");
      else showError(apiError);
    } finally {
      setIsSendingCode(false);
    }
  };

  const onSubmit = async (formData: typeof defaultValues) => {
    if (!data || !canDelete) return;
    setErrorText(null);
    const secret = formData.secret.trim();

    try {
      await deleteWorkspace(data.slug, {
        confirm_name: formData.workspaceName,
        ...(mode === "password" ? { password: formData.secret } : { code: secret }),
      });
      await fetchCurrentUserSettings();
      handleClose();
      router.push(getWorkspaceRedirectionUrl());
      setToast({
        type: "success",
        title: t(`${PREFIX}.success_title`),
        message: t(`${PREFIX}.success_message`),
      });
    } catch (err) {
      const apiError = err as TApiError;
      // A password check that the server answers with CODE_INVALID means this account has no
      // usable password; switch to the emailed code.
      if (mode === "password" && apiError?.error_code === "CODE_INVALID") {
        setMode("code");
        setCodeSent(false);
        setErrorText(t(`${PREFIX}.errors.use_code`));
        return;
      }
      showError(apiError);
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex min-h-0 flex-1 flex-col">
      <DialogMain>
        <DialogHeader>
          <div className="flex w-full items-center justify-start gap-4">
            <span
              className={cn(
                "grid size-12 shrink-0 place-items-center rounded-full bg-danger-subtle text-danger-primary sm:size-10"
              )}
            >
              <WarningTriangleOutline className="size-5 text-danger-primary" aria-hidden="true" />
            </span>
            <DialogHeading>
              <DialogTitle>{t(`${PREFIX}.title`)}</DialogTitle>
            </DialogHeading>
          </div>
        </DialogHeader>
        <DialogBody tabIndex={0}>
          <div className="space-y-4">
            <p className="text-body-xs-regular break-words text-secondary">
              {t(`${PREFIX}.body`, { name: data?.name ?? "" })}
            </p>
            <div className="text-secondary">
              <p className="text-body-xs-regular break-words">{t(`${PREFIX}.name_label`)}</p>
              <Controller
                control={control}
                name="workspaceName"
                render={({ field: { value, onChange, ref } }) => (
                  <Field name="workspaceName">
                    <InputGroup size="2xl">
                      <Input
                        size="2xl"
                        id="workspaceName"
                        name="workspaceName"
                        type="text"
                        value={value}
                        onChange={onChange}
                        ref={ref}
                        placeholder={data?.name}
                        autoComplete="off"
                        data-testid="delete-workspace-name"
                      />
                    </InputGroup>
                  </Field>
                )}
              />
            </div>
            <div className="text-secondary">
              <p className="text-body-xs-regular">
                {mode === "password" ? t(`${PREFIX}.password_label`) : t(`${PREFIX}.code_label`)}
              </p>
              {mode === "code" && (
                <div className="mt-1 mb-2 flex flex-wrap items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    stretch="auto"
                    label={codeSent ? t(`${PREFIX}.resend_code`) : t(`${PREFIX}.send_code`)}
                    loading={isSendingCode}
                    disabled={isSendingCode}
                    onClick={() => void sendCode()}
                    data-testid="delete-workspace-send-code"
                  />
                  {codeSent && (
                    <span className="text-caption-md-regular text-tertiary">{t(`${PREFIX}.code_sent`)}</span>
                  )}
                </div>
              )}
              <Controller
                control={control}
                name="secret"
                render={({ field: { value, onChange, ref } }) => (
                  <Field name="secret">
                    <InputGroup size="2xl">
                      <Input
                        size="2xl"
                        id="deleteWorkspaceSecret"
                        name="secret"
                        type={mode === "password" ? "password" : "text"}
                        inputMode={mode === "code" ? "numeric" : undefined}
                        value={value}
                        onChange={onChange}
                        ref={ref}
                        disabled={mode === "code" && !codeSent}
                        autoComplete={mode === "password" ? "current-password" : "one-time-code"}
                        data-testid="delete-workspace-secret"
                      />
                    </InputGroup>
                  </Field>
                )}
              />
            </div>
            {errorText && (
              <p role="alert" className="text-body-xs-regular text-danger-primary" data-testid="delete-workspace-error">
                {errorText}
              </p>
            )}
          </div>
        </DialogBody>
      </DialogMain>
      <DialogActions>
        <Button variant="secondary" size="md" stretch="auto" label={t("cancel")} onClick={handleClose} />
        <Button
          variant="danger"
          size="md"
          stretch="auto"
          type="submit"
          label={isSubmitting ? t("deleting") : t(`${PREFIX}.confirm`)}
          disabled={!canDelete || isSubmitting}
          loading={isSubmitting}
          data-testid="delete-workspace-confirm"
        />
      </DialogActions>
    </form>
  );
});
