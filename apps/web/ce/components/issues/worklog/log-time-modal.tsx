/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { format } from "date-fns"; // local calendar date — toISOString() is UTC and rolls to tomorrow in the evening
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Button } from "@makeplane/propel/components/button";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
import { Field } from "@makeplane/propel/components/field";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { setToast } from "@plane/blocks/toast";
import { MemberSelect } from "@/components/dropdowns/member/member-select";
import { asApiError } from "@/plane-web/components/analytics/api-error";
// FORK: time-tracking-v2 S6
import { handleEstimateRequired } from "./estimate/estimate-required";
import { IssuePicker, type TPickedIssue } from "@/plane-web/components/time/requests/issue-picker";
import { timeTimesheetService } from "@/plane-web/services/time-timesheet.service";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  // FORK: time-tracking-v2 S4 — without projectId/issueId this is the timesheet's "Add time": it asks which
  // work item, posts to `time/entries/`, and leaves out the resource select and billing (the timesheet has neither).
  projectId?: string;
  issueId?: string;
  isProjectAdmin?: boolean;
  currentUserId?: string;
  /** timesheet mode: the workspace's today, as YYYY-MM-DD (the browser's day may differ) */
  defaultDate?: string;
  onSaved: () => void;
};

type TForm = {
  hours: number;
  minutes: number;
  logged_date: string;
  description: string;
  work_type: string;
  is_billable: boolean;
};

const WORK_TYPES = ["development", "qa", "design", "meeting", "review", "other"];

function todayStr(): string {
  return format(new Date(), "yyyy-MM-dd");
}

export function LogTimeModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, projectId, issueId, isProjectAdmin = false, onSaved } = props;
  const currentUserId = props.currentUserId ?? "";
  const standalone = !projectId || !issueId;
  const startDate = props.defaultDate || todayStr();
  const [loggedBy, setLoggedBy] = useState<string | null>(currentUserId);
  const [pickedIssue, setPickedIssue] = useState<TPickedIssue | null>(null);

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<TForm>({
    defaultValues: { hours: 0, minutes: 0, logged_date: startDate, description: "", work_type: "", is_billable: true },
  });

  useEffect(() => {
    if (isOpen) {
      reset({ hours: 0, minutes: 0, logged_date: startDate, description: "", work_type: "", is_billable: true });
      setLoggedBy(currentUserId);
      setPickedIssue(null);
    }
  }, [isOpen, currentUserId, startDate, reset]);

  const onSubmit = async (data: TForm) => {
    const duration = Number(data.hours || 0) * 60 + Number(data.minutes || 0);
    if (duration <= 0) {
      setToast({ type: "error", title: "Invalid duration", message: "Enter a duration greater than zero." });
      return;
    }
    if (standalone && !pickedIssue) {
      setToast({ type: "error", title: "Choose a work item", message: "Pick the work item the time was spent on." });
      return;
    }
    try {
      if (standalone) {
        await timeTimesheetService.createEntry(workspaceSlug, {
          issue_id: pickedIssue!.id,
          logged_date: data.logged_date,
          duration,
          description: data.description,
          work_type: data.work_type || null,
        });
      } else {
        await timeTrackingService.createWorklog(workspaceSlug, projectId, issueId, {
          duration,
          logged_date: data.logged_date,
          description: data.description,
          work_type: data.work_type || null,
          is_billable: data.is_billable,
          ...(isProjectAdmin && loggedBy && loggedBy !== currentUserId ? { logged_by: loggedBy } : {}),
        });
      }
      setToast({ type: "success", title: "Time logged", message: "Your time entry was saved." });
      onSaved();
      handleClose();
    } catch (err) {
      // FORK: time-tracking-v2 S6 — no estimate on a project that needs one: close and open the estimate field.
      // From the timesheet (S4) the item comes from the picker; the error payload carries its project.
      if (handleEstimateRequired(err, { issueId: issueId ?? pickedIssue?.id, workspaceSlug, projectId })) {
        handleClose();
        return;
      }
      setToast({
        type: "error",
        title: "Error",
        message: asApiError(err).error ?? "Could not save the time entry.",
      });
    }
  };

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      {/*
        data-prevent-outside-click keeps the issue peek overview open while this
        modal is being used. The modal is portaled to <body> (outside the peek
        panel), so without this marker a click into any field reads as an
        "outside click" and collapses the peek, taking the modal with it.
      */}
      <DialogContent size="sm" data-prevent-outside-click>
        <form
          onSubmit={handleSubmit(onSubmit)}
          className="flex min-h-0 flex-1 flex-col"
          data-testid={standalone ? "add-time-modal" : undefined}
        >
          <DialogMain>
            <DialogHeader>
              <DialogHeading>
                <DialogTitle>{standalone ? "Add time" : "Log time"}</DialogTitle>
              </DialogHeading>
            </DialogHeader>
            <DialogBody tabIndex={0}>
              <div className="space-y-4">
                {standalone && (
                  <div>
                    <span className="text-sm mb-1 block text-tertiary">Work item</span>
                    <IssuePicker workspaceSlug={workspaceSlug} value={pickedIssue} onChange={setPickedIssue} />
                  </div>
                )}
                {!standalone && isProjectAdmin && (
                  <div>
                    <span className="text-sm mb-1 block text-tertiary">Resource</span>
                    <MemberSelect
                      value={loggedBy}
                      onChange={(val) => setLoggedBy(val)}
                      projectId={projectId!}
                      multiple={false}
                      placeholder="Select resource"
                      variant="select-md"
                    />
                    <p className="text-xs mt-1 text-tertiary">Admins/PMs can log time on behalf of another resource.</p>
                  </div>
                )}

                <div className="flex items-end gap-3">
                  <div className="w-24">
                    <span className="text-sm mb-1 block text-tertiary">Hours</span>
                    <Controller
                      control={control}
                      name="hours"
                      render={({ field }) => (
                        <Field name={field.name} invalid={Boolean(errors.hours)}>
                          <InputGroup size="lg">
                            <Input size="lg" type="number" min={0} {...field} />
                          </InputGroup>
                        </Field>
                      )}
                    />
                  </div>
                  <div className="w-24">
                    <span className="text-sm mb-1 block text-tertiary">Minutes</span>
                    <Controller
                      control={control}
                      name="minutes"
                      render={({ field }) => (
                        <InputGroup size="lg">
                          <Input size="lg" type="number" min={0} max={59} {...field} />
                        </InputGroup>
                      )}
                    />
                  </div>
                  <div className="flex-1">
                    <span className="text-sm mb-1 block text-tertiary">Date</span>
                    <Controller
                      control={control}
                      name="logged_date"
                      rules={{ required: "Date is required" }}
                      render={({ field }) => (
                        <Field name={field.name} invalid={Boolean(errors.logged_date)}>
                          <InputGroup size="lg">
                            <Input size="lg" type="date" {...field} />
                          </InputGroup>
                        </Field>
                      )}
                    />
                  </div>
                </div>

                <div>
                  <span className="text-sm mb-1 block text-tertiary">Description</span>
                  <Controller
                    control={control}
                    name="description"
                    render={({ field }) => (
                      <InputGroup size="lg">
                        <Input size="lg" type="text" {...field} placeholder="What did you work on?" />
                      </InputGroup>
                    )}
                  />
                </div>

                <div className="flex items-center gap-4">
                  <div className="flex-1">
                    <span className="text-sm mb-1 block text-tertiary">Work type</span>
                    <Controller
                      control={control}
                      name="work_type"
                      render={({ field }) => (
                        <select
                          {...field}
                          className="text-sm w-full rounded border border-subtle bg-transparent px-2 py-1.5"
                        >
                          <option value="">—</option>
                          {WORK_TYPES.map((w) => (
                            <option key={w} value={w}>
                              {w.charAt(0).toUpperCase() + w.slice(1)}
                            </option>
                          ))}
                        </select>
                      )}
                    />
                  </div>
                  {!standalone && (
                    <span className="text-sm flex items-center gap-2 pt-5">
                      <Controller
                        control={control}
                        name="is_billable"
                        render={({ field }) => (
                          <input
                            type="checkbox"
                            checked={field.value}
                            onChange={(e) => field.onChange(e.target.checked)}
                          />
                        )}
                      />
                      Billable
                    </span>
                  )}
                </div>
              </div>
            </DialogBody>
          </DialogMain>
          <DialogActions>
            <Button variant="secondary" size="md" stretch="auto" label="Cancel" onClick={handleClose} type="button" />
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              type="submit"
              loading={isSubmitting}
              label={isSubmitting ? "Saving" : standalone ? "Add time" : "Log time"}
            />
          </DialogActions>
        </form>
      </DialogContent>
    </Dialog>
  );
}
