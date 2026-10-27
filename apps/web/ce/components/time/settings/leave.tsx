/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — per-person leave entered by workspace admins (no
// approval flow). Leave lowers Expected hours for the days it covers; it never blocks logging time.
import { useMemo, useState } from "react";
import { mutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { setToast } from "@plane/blocks/toast";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import {
  DeleteButton,
  ErrorLine,
  PortionSelect,
  SectionHeader,
  TD_CLASS,
  TH_CLASS,
  inputClass,
} from "./holiday-controls";
import { LEAVE_KEY, useLeave } from "./holiday-data";
import {
  coercePortion,
  halfDayAllowed,
  holidayErrorMessage,
  leaveSpan,
  portionLabel,
  todayIso,
  validateLeaveForm,
  type TLeaveForm,
} from "./holidays-utils";
import { useTrackingMembers } from "./members";
import { useTimeSettingsReadOnly } from "./read-only";
import type { TLeave } from "./types";

type TPerson = { user_id: string; display_name: string; tracked: boolean };

/** Admins and members (role 15+) can take leave; guests can't. Tracked people first, then by name. */
function usePeople(workspaceSlug: string): TPerson[] {
  const { data } = useTrackingMembers(workspaceSlug);
  return useMemo(
    () =>
      (data?.members ?? [])
        .filter((m) => m.role >= 15)
        .map((m) => ({ user_id: m.user_id, display_name: m.display_name, tracked: m.is_tracked }))
        .sort((a, b) => Number(b.tracked) - Number(a.tracked) || a.display_name.localeCompare(b.display_name)),
    [data]
  );
}

type TFormProps = {
  initial: TLeaveForm;
  people: TPerson[];
  submitLabel: string;
  testId: string;
  busy: boolean;
  serverError: string | null;
  onSubmit: (form: TLeaveForm) => void;
  onCancel?: () => void;
};

function LeaveForm({ initial, people, submitLabel, testId, busy, serverError, onSubmit, onCancel }: TFormProps) {
  const [form, setForm] = useState<TLeaveForm>(initial);
  const [touched, setTouched] = useState(false);
  const errors = validateLeaveForm(form);
  const halfOk = halfDayAllowed(form.start_date, form.end_date);

  const setDates = (start: string, end: string) =>
    setForm((f) => ({ ...f, start_date: start, end_date: end, portion: coercePortion(f.portion, start, end) }));
  const firstError = errors.user_id ?? errors.start_date ?? errors.end_date ?? errors.portion;

  return (
    <form
      className="flex flex-col gap-1"
      data-testid={testId}
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (Object.keys(errors).length === 0) onSubmit({ ...form, note: form.note.trim() });
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Person"
          data-testid={`${testId}-person`}
          value={form.user_id}
          onChange={(e) => setForm((f) => ({ ...f, user_id: e.target.value }))}
          className={inputClass(touched && Boolean(errors.user_id))}
        >
          <option value="">Pick a person</option>
          {people.map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.display_name}
              {p.tracked ? "" : " (not tracked)"}
            </option>
          ))}
        </select>
        <input
          type="date"
          aria-label="First day"
          data-testid={`${testId}-start`}
          value={form.start_date}
          onChange={(e) => {
            const start = e.target.value;
            // keep a single day by default: move the end along unless it is already after the new start
            setDates(start, form.end_date && form.end_date >= start ? form.end_date : start);
          }}
          className={inputClass(touched && Boolean(errors.start_date))}
        />
        <span className="text-xs text-tertiary">to</span>
        <input
          type="date"
          aria-label="Last day"
          data-testid={`${testId}-end`}
          value={form.end_date}
          min={form.start_date || undefined}
          onChange={(e) => setDates(form.start_date, e.target.value)}
          className={inputClass(touched && Boolean(errors.end_date))}
        />
        <PortionSelect
          value={form.portion}
          onChange={(v) => setForm((f) => ({ ...f, portion: v }))}
          label="Full or half day"
          testId={`${testId}-portion`}
          halfDisabled={!halfOk}
        />
        <input
          type="text"
          aria-label="Note"
          data-testid={`${testId}-note`}
          placeholder="Note (optional)"
          maxLength={255}
          value={form.note}
          onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
          className={cn(inputClass(), "w-48")}
        />
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          type="submit"
          label={submitLabel}
          loading={busy}
          disabled={busy}
          data-testid={`${testId}-submit`}
        />
        {onCancel && (
          <button type="button" className="text-xs text-tertiary hover:underline" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {!halfOk && <span className="text-xs text-tertiary">Half days are for a single day.</span>}
      <ErrorLine message={serverError ?? (touched ? firstError : null)} testId={`${testId}-error`} />
    </form>
  );
}

const toForm = (leave: TLeave): TLeaveForm => ({
  user_id: leave.user_id,
  start_date: leave.start_date,
  end_date: leave.end_date,
  portion: leave.portion,
  note: leave.note,
});

function LeaveRow({ workspaceSlug, leave, people }: { workspaceSlug: string; leave: TLeave; people: TPerson[] }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  const save = async (form: TLeaveForm) => {
    setBusy(true);
    try {
      await timeTrackingService.updateLeave(workspaceSlug, leave.id, form);
      await mutate(LEAVE_KEY(workspaceSlug));
      setEditing(false);
      setError(null);
    } catch (err) {
      setError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await timeTrackingService.deleteLeave(workspaceSlug, leave.id);
      await mutate(LEAVE_KEY(workspaceSlug));
    } catch (err) {
      setToast({ type: "error", title: "Couldn't delete the leave", message: holidayErrorMessage(err) });
    }
  };

  if (editing && !readOnly) {
    return (
      <tr className="border-t border-subtle" data-testid="leave-row" data-editing>
        <td colSpan={5} className="px-3 py-2">
          <LeaveForm
            initial={toForm(leave)}
            people={people}
            submitLabel="Save"
            testId="leave-edit"
            busy={busy}
            serverError={error}
            onSubmit={save}
            onCancel={() => {
              setEditing(false);
              setError(null);
            }}
          />
        </td>
      </tr>
    );
  }
  return (
    <tr className="border-t border-subtle" data-testid="leave-row" data-user-id={leave.user_id}>
      <td className={TD_CLASS}>{leave.display_name}</td>
      <td className={cn(TD_CLASS, "tabular-nums")} title={`${leave.start_date} to ${leave.end_date}`}>
        {leaveSpan(leave.start_date, leave.end_date)}
      </td>
      <td className={TD_CLASS}>{portionLabel(leave.portion)}</td>
      <td className={cn(TD_CLASS, "text-secondary")}>{leave.note || "–"}</td>
      <td className="px-3 py-2">
        {!readOnly && (
          <span className="flex items-center justify-end gap-3">
            <button
              type="button"
              className="text-xs text-accent-primary hover:underline"
              data-testid="leave-edit-open"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
            <DeleteButton label={`Delete leave for ${leave.display_name}`} testId="leave-delete" onConfirm={remove} />
          </span>
        )}
      </td>
    </tr>
  );
}

export function LeaveSettings({ workspaceSlug }: { workspaceSlug: string }) {
  const { data, error, isLoading } = useLeave(workspaceSlug);
  const people = usePeople(workspaceSlug);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // bump to reset the add form after a save
  const [formKey, setFormKey] = useState(0);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  const add = async (form: TLeaveForm) => {
    setBusy(true);
    try {
      await timeTrackingService.createLeave(workspaceSlug, form);
      await mutate(LEAVE_KEY(workspaceSlug));
      setFormError(null);
      setFormKey((k) => k + 1);
      setToast({ type: "success", title: "Leave added" });
    } catch (err) {
      setFormError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-4" data-testid="settings-leave">
      <SectionHeader
        title="Leave"
        hint="Time off for one person, entered by an admin. It lowers that person's expected hours on working days; a day that is also a holiday still counts once."
      />
      {!readOnly && (
        <LeaveForm
          key={formKey}
          initial={{ user_id: "", start_date: todayIso(), end_date: todayIso(), portion: "full", note: "" }}
          people={people}
          submitLabel="Add leave"
          testId="leave-new"
          busy={busy}
          serverError={formError}
          onSubmit={add}
        />
      )}
      {error && !data ? (
        <ErrorLine message={holidayErrorMessage(error, "Couldn't load leave.")} />
      ) : isLoading || !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="40px" />
          <Loader.Item height="40px" />
        </Loader>
      ) : data.length === 0 ? (
        <p className="text-sm text-tertiary" data-testid="leave-empty">
          No leave entered yet.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-subtle bg-surface-1">
          <table className="w-full" data-testid="leave-table">
            <thead>
              <tr>
                <th className={TH_CLASS}>Person</th>
                <th className={TH_CLASS}>Dates</th>
                <th className={TH_CLASS}>Length</th>
                <th className={TH_CLASS}>Note</th>
                <th className={TH_CLASS} />
              </tr>
            </thead>
            <tbody>
              {data.map((l) => (
                <LeaveRow key={l.id} workspaceSlug={workspaceSlug} leave={l} people={people} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
