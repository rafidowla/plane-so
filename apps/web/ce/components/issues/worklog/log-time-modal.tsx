/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Input, ModalCore } from "@plane/ui";
import { MemberDropdown } from "@/components/dropdowns/member/dropdown";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  isProjectAdmin: boolean;
  currentUserId: string;
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
  return new Date().toISOString().slice(0, 10);
}

export function LogTimeModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, projectId, issueId, isProjectAdmin, currentUserId, onSaved } = props;
  const [loggedBy, setLoggedBy] = useState<string | null>(currentUserId);

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<TForm>({
    defaultValues: { hours: 0, minutes: 0, logged_date: todayStr(), description: "", work_type: "", is_billable: true },
  });

  useEffect(() => {
    if (isOpen) {
      reset({ hours: 0, minutes: 0, logged_date: todayStr(), description: "", work_type: "", is_billable: true });
      setLoggedBy(currentUserId);
    }
  }, [isOpen, currentUserId, reset]);

  const onSubmit = async (data: TForm) => {
    const duration = Number(data.hours || 0) * 60 + Number(data.minutes || 0);
    if (duration <= 0) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Invalid duration", message: "Enter a duration greater than zero." });
      return;
    }
    try {
      await timeTrackingService.createWorklog(workspaceSlug, projectId, issueId, {
        duration,
        logged_date: data.logged_date,
        description: data.description,
        work_type: data.work_type || null,
        is_billable: data.is_billable,
        ...(isProjectAdmin && loggedBy && loggedBy !== currentUserId ? { logged_by: loggedBy } : {}),
      });
      setToast({ type: TOAST_TYPE.SUCCESS, title: "Time logged", message: "Your time entry was saved." });
      onSaved();
      handleClose();
    } catch (err: any) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: err?.error ?? "Could not save the time entry.",
      });
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose}>
      <form onSubmit={handleSubmit(onSubmit)}>
        <div className="space-y-4 p-5">
          <h3 className="text-lg font-medium">Log time</h3>

          {isProjectAdmin && (
            <div>
              <span className="text-sm mb-1 block text-tertiary">Resource</span>
              <MemberDropdown
                value={loggedBy}
                onChange={(val) => setLoggedBy(val)}
                projectId={projectId}
                multiple={false}
                placeholder="Select resource"
                buttonVariant="border-with-text"
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
                  <Input type="number" min={0} {...field} hasError={Boolean(errors.hours)} className="w-full" />
                )}
              />
            </div>
            <div className="w-24">
              <span className="text-sm mb-1 block text-tertiary">Minutes</span>
              <Controller
                control={control}
                name="minutes"
                render={({ field }) => <Input type="number" min={0} max={59} {...field} className="w-full" />}
              />
            </div>
            <div className="flex-1">
              <span className="text-sm mb-1 block text-tertiary">Date</span>
              <Controller
                control={control}
                name="logged_date"
                rules={{ required: "Date is required" }}
                render={({ field }) => (
                  <Input type="date" {...field} hasError={Boolean(errors.logged_date)} className="w-full" />
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
                <Input type="text" {...field} placeholder="What did you work on?" className="w-full" />
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
                  <select {...field} className="text-sm w-full rounded border border-subtle bg-transparent px-2 py-1.5">
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
            <span className="text-sm flex items-center gap-2 pt-5">
              <Controller
                control={control}
                name="is_billable"
                render={({ field }) => (
                  <input type="checkbox" checked={field.value} onChange={(e) => field.onChange(e.target.checked)} />
                )}
              />
              Billable
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-subtle px-5 py-4">
          <Button variant="secondary" size="sm" onClick={handleClose} type="button">
            Cancel
          </Button>
          <Button variant="primary" size="sm" type="submit" loading={isSubmitting}>
            {isSubmitting ? "Saving" : "Log time"}
          </Button>
        </div>
      </form>
    </ModalCore>
  );
}
