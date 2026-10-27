/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — work week, entry rules, billing and weekly-approval switches. One Save
// sends only the fields that changed (PATCH time/settings/).
import { useState } from "react";
import { mutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { setToast } from "@plane/blocks/toast";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTimeSettings } from "../reports/types";
import { TIME_SETTINGS_KEY } from "../use-time-settings";
import { useTimeSettingsReadOnly } from "./read-only";
import {
  settingsErrorMessage,
  normalizeDays,
  settingsToForm,
  toPatch,
  validateSettingsForm,
  weeklyMinutesOf,
  WEEKDAYS,
  type TSettingsForm,
} from "./settings-utils";

type TProps = { workspaceSlug: string; settings: TTimeSettings };

type TNumberKey =
  | "minutes_per_day"
  | "remind_after_minutes"
  | "auto_stop_after_minutes"
  | "idle_check_min_minutes"
  | "idle_check_max_minutes";

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-medium text-primary">{label}</span>
      {children}
      {error ? (
        <span role="alert" className="text-xs text-danger-primary">
          {error}
        </span>
      ) : (
        hint && <span className="text-xs text-tertiary">{hint}</span>
      )}
    </label>
  );
}

function Toggle({
  id,
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <input
        id={`setting-${id}`}
        type="checkbox"
        role="switch"
        data-testid={`setting-${id}`}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 size-4"
      />
      <label htmlFor={`setting-${id}`} className="flex cursor-pointer flex-col">
        <span className="text-sm font-medium text-primary">{label}</span>
        <span className="text-xs text-tertiary">{hint}</span>
      </label>
    </div>
  );
}

export function RulesForm({ workspaceSlug, settings }: TProps) {
  const [form, setForm] = useState<TSettingsForm>(() => settingsToForm(settings));
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const readOnly = useTimeSettingsReadOnly(); // FORK: PSR-125

  const errors = validateSettingsForm(form);
  const patch = toPatch(settings, form);
  const dirty = Object.keys(patch).length > 0;
  const set = <K extends keyof TSettingsForm>(key: K, value: TSettingsForm[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setServerError(null);
  };
  const number = (key: TNumberKey) => (
    <input
      type="text"
      inputMode="numeric"
      data-testid={`setting-${key}`}
      value={form[key]}
      disabled={readOnly}
      onChange={(e) => set(key, e.target.value)}
      className={cn(
        "text-sm w-32 rounded border bg-transparent px-2 py-1",
        errors[key] ? "border-danger-subtle" : "border-subtle"
      )}
    />
  );

  const weekly = weeklyMinutesOf(form.work_days, Number(form.minutes_per_day) || 0);

  const save = async () => {
    if (readOnly || !dirty) return;
    setSaving(true);
    try {
      const updated = await timeTrackingService.updateTimeSettings(workspaceSlug, patch);
      await mutate(TIME_SETTINGS_KEY(workspaceSlug), updated, false);
      setForm(settingsToForm(updated));
      setToast({ type: "success", title: "Time settings saved" });
    } catch (err) {
      const message = settingsErrorMessage(err);
      setServerError(message);
      setToast({ type: "error", title: "Couldn't save", message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="flex flex-col gap-5" data-testid="settings-rules">
      <h2 className="text-16 font-semibold text-primary">Work week and rules</h2>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-primary">Work days</span>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Work days">
          {WEEKDAYS.map((day) => {
            const on = form.work_days.includes(day.iso);
            return (
              <button
                key={day.iso}
                type="button"
                aria-pressed={on}
                disabled={readOnly}
                data-testid={`workday-${day.iso}`}
                onClick={() =>
                  set(
                    "work_days",
                    on ? form.work_days.filter((d) => d !== day.iso) : normalizeDays([...form.work_days, day.iso])
                  )
                }
                className={cn(
                  "text-sm rounded border px-3 py-1",
                  on ? "border-accent-strong bg-accent-subtle text-accent-primary" : "border-subtle text-secondary"
                )}
              >
                {day.short}
              </button>
            );
          })}
        </div>
        {errors.work_days && (
          <span role="alert" className="text-xs text-danger-primary">
            {errors.work_days}
          </span>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field
          label="Minutes per work day"
          error={errors.minutes_per_day}
          hint={errors.minutes_per_day ? undefined : `Work week: ${(weekly / 60).toFixed(1)} h`}
        >
          {number("minutes_per_day")}
        </Field>
        <Field
          label="Remind after (minutes)"
          error={errors.remind_after_minutes}
          hint="Nudge when a timer runs this long"
        >
          {number("remind_after_minutes")}
        </Field>
        <Field
          label="Auto-stop after (minutes)"
          error={errors.auto_stop_after_minutes}
          hint="Stops a forgotten timer; must be after the reminder"
        >
          {number("auto_stop_after_minutes")}
        </Field>
        <Field
          label="Idle check, earliest (minutes)"
          error={errors.idle_check_min_minutes}
          hint="0 and 0 turns the check off"
        >
          {number("idle_check_min_minutes")}
        </Field>
        <Field label="Idle check, latest (minutes)" error={errors.idle_check_max_minutes} hint="Between 5 and 480">
          {number("idle_check_max_minutes")}
        </Field>
      </div>

      <div className="flex flex-col gap-3">
        <Toggle
          id="allow_member_manual_entry"
          label="Members can add time by hand"
          hint="When off, members can only use the timer; admins can still add entries."
          checked={form.allow_member_manual_entry}
          disabled={readOnly}
          onChange={(v) => set("allow_member_manual_entry", v)}
        />
        <Toggle
          id="weekly_approval_enabled"
          label="Weekly approval"
          hint="Members submit each week for review. Adds a Timesheets tab to Analytics."
          checked={form.weekly_approval_enabled}
          disabled={readOnly}
          onChange={(v) => set("weekly_approval_enabled", v)}
        />
        <Toggle
          id="billing_enabled"
          label="Billing"
          hint="Shows billable time and amounts in reports."
          checked={form.billing_enabled}
          disabled={readOnly}
          onChange={(v) => set("billing_enabled", v)}
        />
      </div>

      {serverError && (
        <div role="alert" className="text-sm text-danger-primary">
          {serverError}
        </div>
      )}
      {/* FORK: PSR-125 — no Save for a plain Admin */}
      {!readOnly && (
        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            size="sm"
            stretch="auto"
            label="Save settings"
            loading={saving}
            disabled={!dirty || Object.keys(errors).length > 0}
            onClick={save}
            data-testid="settings-save"
          />
          {dirty && Object.keys(errors).length === 0 && <span className="text-xs text-tertiary">Unsaved changes</span>}
        </div>
      )}
    </section>
  );
}
