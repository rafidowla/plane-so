/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-125 -- deleting a workspace re-confirms who you are: type its name, then your password,
// or (accounts without a password) a code we email you. The workspace stays restorable until purge.

import { useState } from "react";
import { observer } from "mobx-react";
// Plane Imports
import {
  AlertDialog,
  AlertDialogActions,
  AlertDialogBody,
  AlertDialogClose,
  AlertDialogCloseGroup,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogIcon,
  AlertDialogIntro,
  AlertDialogTitle,
} from "@makeplane/propel/components/alert-dialog";
import { Button } from "@makeplane/propel/components/button";
import { Field } from "@makeplane/propel/components/field";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { CloseOutline } from "@makeplane/propel/icons";
import { useTranslation } from "@plane/i18n";
import { setToast } from "@plane/blocks/toast";
import type { IWorkspace } from "@plane/types";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUser, useUserSettings } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { WorkspaceService } from "@/services/workspace.service";

const workspaceService = new WorkspaceService();

type Props = {
  data: IWorkspace | null;
  isOpen: boolean;
  onClose: () => void;
};

type TMode = "password" | "code";

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
  const { data, isOpen, onClose } = props;
  // router
  const router = useAppRouter();
  // store hooks
  const { deleteWorkspace, getWorkspaceRedirectionUrl } = useWorkspace();
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  const { fetchCurrentUserSettings } = useUserSettings();
  // state
  const [mode, setMode] = useState<TMode>(currentUser?.is_password_autoset ? "code" : "password");
  const [workspaceName, setWorkspaceName] = useState("");
  const [secret, setSecret] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const nameMatches = !!data && workspaceName.trim() === data.name.trim();
  const canDelete = nameMatches && secret.trim() !== "" && (mode === "password" || codeSent);

  const showError = (err: TApiError) => {
    const key = err?.error_code ? ERROR_KEYS[err.error_code] : undefined;
    setErrorText(key ? t(key) : (err?.error ?? t(`${PREFIX}.error_message`)));
  };

  const resetForm = () => {
    setWorkspaceName("");
    setSecret("");
    setCodeSent(false);
    setErrorText(null);
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

  const onSubmit = async () => {
    if (!data || !canDelete || isSubmitting) return;
    setErrorText(null);
    setIsSubmitting(true);
    const trimmedSecret = secret.trim();

    try {
      await deleteWorkspace(data.slug, {
        confirm_name: workspaceName,
        ...(mode === "password" ? { password: secret } : { code: trimmedSecret }),
      });
      await fetchCurrentUserSettings();
      onClose();
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
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={isOpen}
      onOpenChange={(open) => {
        if (open || isSubmitting) return;
        onClose();
      }}
      // Clear once the close animation has finished so typed values don't vanish mid-fade.
      onOpenChangeComplete={(open) => {
        if (!open) resetForm();
      }}
    >
      <AlertDialogContent data-prevent-outside-click>
        <AlertDialogCloseGroup>
          <IconButton
            variant="ghost"
            size="xs"
            aria-label={t("close")}
            icon={<Icon icon={CloseOutline} />}
            disabled={isSubmitting}
            render={<AlertDialogClose />}
          />
        </AlertDialogCloseGroup>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            void onSubmit();
          }}
        >
          <AlertDialogBody>
            <AlertDialogHeader>
              <AlertDialogIcon variant="danger" />
              <AlertDialogIntro>
                <AlertDialogTitle>{t(`${PREFIX}.title`)}</AlertDialogTitle>
                <AlertDialogDescription>{t(`${PREFIX}.body`, { name: data?.name ?? "" })}</AlertDialogDescription>
              </AlertDialogIntro>
            </AlertDialogHeader>
            <div className="flex flex-col gap-4">
              <div className="text-secondary">
                <p className="text-body-xs-regular break-words">{t(`${PREFIX}.name_label`)}</p>
                <Field name="workspaceName">
                  <InputGroup size="2xl">
                    <Input
                      size="2xl"
                      id="workspaceName"
                      name="workspaceName"
                      type="text"
                      value={workspaceName}
                      onChange={(e) => setWorkspaceName(e.target.value)}
                      placeholder={data?.name}
                      autoComplete="off"
                      data-testid="delete-workspace-name"
                    />
                  </InputGroup>
                </Field>
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
                      type="button"
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
                <Field name="secret">
                  <InputGroup size="2xl">
                    <Input
                      size="2xl"
                      id="deleteWorkspaceSecret"
                      name="secret"
                      type={mode === "password" ? "password" : "text"}
                      inputMode={mode === "code" ? "numeric" : undefined}
                      value={secret}
                      onChange={(e) => setSecret(e.target.value)}
                      disabled={mode === "code" && !codeSent}
                      autoComplete={mode === "password" ? "current-password" : "one-time-code"}
                      data-testid="delete-workspace-secret"
                    />
                  </InputGroup>
                </Field>
              </div>
              {errorText && (
                <p
                  role="alert"
                  className="text-body-xs-regular text-danger-primary"
                  data-testid="delete-workspace-error"
                >
                  {errorText}
                </p>
              )}
            </div>
          </AlertDialogBody>
          <AlertDialogActions>
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label={t("cancel")}
              disabled={isSubmitting}
              render={<AlertDialogClose />}
            />
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
          </AlertDialogActions>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
});
