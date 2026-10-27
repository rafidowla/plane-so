/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — holiday lists (one per country/office) and the
// holidays inside the selected list. Workspace admins only (the Settings tab is admin-only and the API
// refuses everyone else). A holiday only lowers Expected hours; it never blocks logging time.
import { useState } from "react";
import { mutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { setToast } from "@plane/blocks/toast";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import {
  ErrorLine,
  DeleteButton,
  PortionSelect,
  SectionHeader,
  TD_CLASS,
  TH_CLASS,
  inputClass,
} from "./holiday-controls";
import { HOLIDAYS_KEY, HOLIDAY_CALENDARS_KEY, useHolidayCalendars, useHolidays } from "./holiday-data";
import {
  defaultHolidayDate,
  holidayErrorMessage,
  portionLabel,
  todayIso,
  validateCalendarName,
  validateHolidayForm,
  yearOptions,
  type THolidayForm,
} from "./holidays-utils";
import { MEMBERS_KEY } from "./members";
import { useTimeSettingsReadOnly } from "./read-only";
import type { THoliday, THolidayCalendar } from "./types";

// ---- Holiday lists ----

type TCalendarRowProps = {
  workspaceSlug: string;
  calendar: THolidayCalendar;
  calendars: THolidayCalendar[];
  selected: boolean;
  onSelect: () => void;
};

function CalendarRow({ workspaceSlug, calendar, calendars, selected, onSelect }: TCalendarRowProps) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(calendar.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  // member_count and every member's assignment may have changed
  const refresh = async () => {
    await Promise.all([mutate(HOLIDAY_CALENDARS_KEY(workspaceSlug)), mutate(MEMBERS_KEY(workspaceSlug))]);
  };

  const rename = async () => {
    const problem = validateCalendarName(name, calendars, calendar.id);
    if (problem) return setError(problem);
    if (name.trim() === calendar.name) return setEditing(false);
    setBusy(true);
    try {
      await timeTrackingService.updateHolidayCalendar(workspaceSlug, calendar.id, { name: name.trim() });
      await refresh();
      setEditing(false);
      setError(null);
    } catch (err) {
      setError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const makeDefault = async () => {
    setBusy(true);
    try {
      await timeTrackingService.updateHolidayCalendar(workspaceSlug, calendar.id, { is_default: true });
      await refresh();
      setError(null);
      setToast({ type: "success", title: "Default holiday list changed", message: calendar.name });
    } catch (err) {
      setError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    try {
      await timeTrackingService.deleteHolidayCalendar(workspaceSlug, calendar.id);
      await refresh();
      setError(null);
      setToast({ type: "success", title: "Holiday list deleted", message: calendar.name });
    } catch (err) {
      // the server explains why the default list can't go yet: show its own sentence
      setError(holidayErrorMessage(err, "Couldn't delete the list.", true));
    }
  };

  return (
    <li
      className={cn("flex flex-col gap-1 border-t border-subtle px-3 py-2 first:border-t-0", selected && "bg-layer-1")}
      data-testid="holiday-calendar-row"
      data-calendar-id={calendar.id}
      data-selected={selected}
    >
      <div className="flex flex-wrap items-center gap-3">
        {editing && !readOnly ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void rename();
            }}
          >
            <input
              type="text"
              aria-label={`Name of ${calendar.name}`}
              data-testid="holiday-calendar-rename-input"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              className={inputClass(Boolean(error))}
            />
            <Button
              variant="secondary"
              size="sm"
              stretch="auto"
              type="submit"
              label="Save"
              loading={busy}
              data-testid="holiday-calendar-rename-save"
            />
            <button
              type="button"
              className="text-xs text-tertiary hover:underline"
              onClick={() => {
                setEditing(false);
                setName(calendar.name);
                setError(null);
              }}
            >
              Cancel
            </button>
          </form>
        ) : (
          <button
            type="button"
            data-testid="holiday-calendar-select"
            aria-pressed={selected}
            onClick={onSelect}
            className="text-sm text-left font-medium text-primary hover:underline"
          >
            {calendar.name}
          </button>
        )}
        {calendar.is_default && (
          <span
            data-testid="holiday-calendar-default"
            className="text-xs rounded bg-accent-subtle px-2 py-0.5 text-accent-primary"
          >
            Default
          </span>
        )}
        <span className="text-xs text-tertiary" data-testid="holiday-calendar-members">
          {calendar.member_count === 1 ? "1 person" : `${calendar.member_count} people`}
        </span>
        {!editing && !readOnly && (
          <span className="ml-auto flex items-center gap-3">
            <button
              type="button"
              className="text-xs text-accent-primary hover:underline disabled:opacity-50"
              disabled={busy}
              data-testid="holiday-calendar-rename"
              onClick={() => setEditing(true)}
            >
              Rename
            </button>
            {!calendar.is_default && (
              <button
                type="button"
                className="text-xs text-accent-primary hover:underline disabled:opacity-50"
                disabled={busy}
                data-testid="holiday-calendar-make-default"
                onClick={makeDefault}
              >
                Make default
              </button>
            )}
            <DeleteButton
              label={`Delete ${calendar.name} holiday list`}
              testId="holiday-calendar-delete"
              disabled={busy}
              onConfirm={remove}
            />
          </span>
        )}
      </div>
      <ErrorLine message={error} testId="holiday-calendar-error" />
    </li>
  );
}

function NewCalendarForm({
  workspaceSlug,
  calendars,
  onCreated,
}: {
  workspaceSlug: string;
  calendars: THolidayCalendar[];
  onCreated: (calendar: THolidayCalendar) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const problem = validateCalendarName(name, calendars);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      const created = await timeTrackingService.createHolidayCalendar(workspaceSlug, { name: name.trim() });
      await mutate(HOLIDAY_CALENDARS_KEY(workspaceSlug));
      setName("");
      setError(null);
      onCreated(created);
    } catch (err) {
      setError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-1" data-testid="holiday-calendar-new">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          aria-label="New holiday list name"
          data-testid="holiday-calendar-new-name"
          placeholder={calendars.length === 0 ? "e.g. Canada" : "New list, e.g. India office"}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
          className={cn(inputClass(Boolean(error)), "w-64")}
        />
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          type="submit"
          label="Add list"
          loading={busy}
          disabled={busy || !name.trim()}
          data-testid="holiday-calendar-new-submit"
        />
      </div>
      <ErrorLine message={error} testId="holiday-calendar-new-error" />
    </form>
  );
}

// ---- Holidays in the selected list ----

type TFormProps = {
  initial: THolidayForm;
  submitLabel: string;
  testId: string;
  busy: boolean;
  serverError: string | null;
  onSubmit: (form: THolidayForm) => void;
  onCancel?: () => void;
};

function HolidayForm({ initial, submitLabel, testId, busy, serverError, onSubmit, onCancel }: TFormProps) {
  const [form, setForm] = useState<THolidayForm>(initial);
  const [touched, setTouched] = useState(false);
  const errors = validateHolidayForm(form);
  const set = <K extends keyof THolidayForm>(key: K, value: THolidayForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <form
      className="flex flex-col gap-1"
      data-testid={testId}
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (Object.keys(errors).length === 0) onSubmit({ ...form, name: form.name.trim() });
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="date"
          aria-label="Holiday date"
          data-testid={`${testId}-date`}
          value={form.date}
          onChange={(e) => set("date", e.target.value)}
          className={inputClass(touched && Boolean(errors.date))}
        />
        <input
          type="text"
          aria-label="Holiday name"
          data-testid={`${testId}-name`}
          placeholder="Holiday name"
          value={form.name}
          onChange={(e) => set("name", e.target.value)}
          className={cn(inputClass(touched && Boolean(errors.name)), "w-56")}
        />
        <PortionSelect
          value={form.portion}
          onChange={(v) => set("portion", v)}
          label="Full or half day"
          testId={`${testId}-portion`}
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
      <ErrorLine message={serverError ?? (touched ? (errors.date ?? errors.name) : null)} testId={`${testId}-error`} />
    </form>
  );
}

type THolidayRowProps = { workspaceSlug: string; calendarId: string; year: number; holiday: THoliday };

function HolidayRow({ workspaceSlug, calendarId, year, holiday }: THolidayRowProps) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125
  const key = HOLIDAYS_KEY(workspaceSlug, calendarId, year);

  const save = async (form: THolidayForm) => {
    setBusy(true);
    try {
      await timeTrackingService.updateHoliday(workspaceSlug, calendarId, holiday.id, form);
      // a date moved into another year drops out of this year's list: refetch every year of this list
      await mutate((k) => Array.isArray(k) && k[0] === "time-holidays" && k[2] === calendarId);
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
      await timeTrackingService.deleteHoliday(workspaceSlug, calendarId, holiday.id);
      await mutate(key);
    } catch (err) {
      setToast({ type: "error", title: "Couldn't delete the holiday", message: holidayErrorMessage(err) });
    }
  };

  if (editing && !readOnly) {
    return (
      <tr className="border-t border-subtle" data-testid="holiday-row" data-editing>
        <td colSpan={4} className="px-3 py-2">
          <HolidayForm
            initial={{ date: holiday.date, name: holiday.name, portion: holiday.portion }}
            submitLabel="Save"
            testId="holiday-edit"
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
    <tr className="border-t border-subtle" data-testid="holiday-row" data-date={holiday.date}>
      <td className={cn(TD_CLASS, "tabular-nums")}>{holiday.date}</td>
      <td className={TD_CLASS}>{holiday.name}</td>
      <td className={cn(TD_CLASS, holiday.portion === "half" ? "text-secondary" : "")}>
        {portionLabel(holiday.portion)}
      </td>
      <td className="px-3 py-2">
        {!readOnly && (
          <span className="flex items-center justify-end gap-3">
            <button
              type="button"
              className="text-xs text-accent-primary hover:underline"
              data-testid="holiday-edit"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
            <DeleteButton label={`Delete ${holiday.name}`} testId="holiday-delete" onConfirm={remove} />
          </span>
        )}
      </td>
    </tr>
  );
}

function HolidayPanel({ workspaceSlug, calendar }: { workspaceSlug: string; calendar: THolidayCalendar }) {
  const today = todayIso();
  const currentYear = Number(today.slice(0, 4));
  const [year, setYear] = useState(currentYear);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125
  const { data, error: loadError, isLoading } = useHolidays(workspaceSlug, calendar.id, year);

  const add = async (form: THolidayForm) => {
    setBusy(true);
    try {
      await timeTrackingService.createHoliday(workspaceSlug, calendar.id, form);
      const addedYear = Number(form.date.slice(0, 4));
      // show the year the holiday landed in, so the person sees it appear
      if (addedYear !== year) setYear(addedYear);
      await mutate((k) => Array.isArray(k) && k[0] === "time-holidays" && k[2] === calendar.id);
      setError(null);
    } catch (err) {
      setError(holidayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3" data-testid="holiday-panel" data-calendar-id={calendar.id}>
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-primary">Holidays in {calendar.name}</h3>
        <select
          aria-label="Year"
          data-testid="holiday-year"
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className={inputClass()}
        >
          {yearOptions(currentYear, year).map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>

      {/* key: a fresh form when the list or year changes, so the date box starts in the year on show */}
      {!readOnly && (
        <HolidayForm
          key={`${calendar.id}-${year}`}
          initial={{ date: defaultHolidayDate(year, today), name: "", portion: "full" }}
          submitLabel="Add holiday"
          testId="holiday-new"
          busy={busy}
          serverError={error}
          onSubmit={add}
        />
      )}

      {loadError && !data ? (
        <ErrorLine message={holidayErrorMessage(loadError, "Couldn't load the holidays.")} />
      ) : isLoading || !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="40px" />
          <Loader.Item height="40px" />
        </Loader>
      ) : data.length === 0 ? (
        <p className="text-sm text-tertiary" data-testid="holiday-empty">
          No holidays in {year} yet.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-subtle bg-surface-1">
          <table className="w-full" data-testid="holiday-table">
            <thead>
              <tr>
                <th className={TH_CLASS}>Date</th>
                <th className={TH_CLASS}>Holiday</th>
                <th className={TH_CLASS}>Length</th>
                <th className={TH_CLASS} />
              </tr>
            </thead>
            <tbody>
              {data.map((h) => (
                <HolidayRow key={h.id} workspaceSlug={workspaceSlug} calendarId={calendar.id} year={year} holiday={h} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function HolidaysSettings({ workspaceSlug }: { workspaceSlug: string }) {
  const { data: calendars, error, isLoading } = useHolidayCalendars(workspaceSlug);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  const selected =
    calendars?.find((c) => c.id === selectedId) ?? calendars?.find((c) => c.is_default) ?? calendars?.[0];

  return (
    <section className="flex flex-col gap-4" data-testid="settings-holidays">
      <SectionHeader
        title="Holidays"
        hint="One holiday list per country or office. People use the list they are assigned to (see People), or the default. A holiday lowers the expected hours for the day; time can still be logged on it."
      />
      {error && !calendars ? (
        <ErrorLine message={holidayErrorMessage(error, "Couldn't load the holiday lists.")} />
      ) : isLoading || !calendars ? (
        <Loader className="space-y-3">
          <Loader.Item height="48px" />
          <Loader.Item height="48px" />
        </Loader>
      ) : (
        <>
          {calendars.length > 0 && (
            <ul className="rounded-lg border border-subtle bg-surface-1" data-testid="holiday-calendars">
              {calendars.map((c) => (
                <CalendarRow
                  key={c.id}
                  workspaceSlug={workspaceSlug}
                  calendar={c}
                  calendars={calendars}
                  selected={c.id === selected?.id}
                  onSelect={() => setSelectedId(c.id)}
                />
              ))}
            </ul>
          )}
          {calendars.length === 0 && (
            <p className="text-sm text-tertiary" data-testid="holiday-calendars-empty">
              {readOnly
                ? "No holiday lists yet."
                : "No holiday lists yet. The first list you add becomes the default for everyone."}
            </p>
          )}
          {!readOnly && (
            <NewCalendarForm
              workspaceSlug={workspaceSlug}
              calendars={calendars}
              onCreated={(c) => setSelectedId(c.id)}
            />
          )}
          {selected && <HolidayPanel key={selected.id} workspaceSlug={workspaceSlug} calendar={selected} />}
        </>
      )}
    </section>
  );
}
