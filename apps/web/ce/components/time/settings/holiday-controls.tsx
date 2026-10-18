/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 holidays/leave (PSR-122) — small controls shared by the holiday and leave
// sections: a two-step delete, the full/half select, and the input look.
import { useState } from "react";
import { Button } from "@makeplane/propel/components/button";
import { cn } from "@plane/utils";
import { PORTION_OPTIONS } from "./holidays-utils";
import type { TDayPortion } from "./types";

export const INPUT_CLASS = "text-sm rounded border bg-transparent px-2 py-1";
export const inputClass = (invalid?: boolean) => cn(INPUT_CLASS, invalid ? "border-danger-subtle" : "border-subtle");
export const TH_CLASS = "text-xs px-3 py-2 text-left font-medium text-tertiary";
export const TD_CLASS = "text-sm px-3 py-2 text-primary";

export function SectionHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div>
      <h2 className="text-16 font-semibold text-primary">{title}</h2>
      {hint && <p className="text-xs text-tertiary">{hint}</p>}
    </div>
  );
}

export function ErrorLine({ message, testId }: { message: string | null | undefined; testId?: string }) {
  if (!message) return null;
  return (
    <div role="alert" data-testid={testId} className="text-xs text-danger-primary">
      {message}
    </div>
  );
}

type TPortionSelectProps = {
  value: TDayPortion;
  onChange: (value: TDayPortion) => void;
  label: string;
  testId: string;
  /** half days are only allowed on a single day: the "Half day" option is disabled otherwise */
  halfDisabled?: boolean;
  disabled?: boolean;
};

export function PortionSelect({ value, onChange, label, testId, halfDisabled, disabled }: TPortionSelectProps) {
  return (
    <select
      aria-label={label}
      data-testid={testId}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as TDayPortion)}
      className={inputClass()}
    >
      {PORTION_OPTIONS.map((o) => (
        <option key={o.value} value={o.value} disabled={o.value === "half" && halfDisabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

type TDeleteButtonProps = {
  /** what is being deleted, for the accessible name: "Delete UK holiday list" */
  label: string;
  testId: string;
  disabled?: boolean;
  onConfirm: () => Promise<void> | void;
};

/** "Delete" turns into "Delete? Yes / No" so a stray click never removes anything. */
export function DeleteButton({ label, testId, disabled, onConfirm }: TDeleteButtonProps) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!confirming) {
    return (
      <button
        type="button"
        aria-label={label}
        data-testid={testId}
        disabled={disabled}
        className="text-xs text-danger-primary hover:underline disabled:opacity-50"
        onClick={() => setConfirming(true)}
      >
        Delete
      </button>
    );
  }
  return (
    <span className="flex items-center gap-2">
      <span className="text-xs text-secondary">Delete?</span>
      <Button
        variant="danger"
        size="sm"
        stretch="auto"
        label="Yes"
        loading={busy}
        disabled={busy}
        data-testid={`${testId}-confirm`}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            setConfirming(false);
          }
        }}
      />
      <Button
        variant="secondary"
        size="sm"
        stretch="auto"
        label="No"
        disabled={busy}
        data-testid={`${testId}-cancel`}
        onClick={() => setConfirming(false)}
      />
    </span>
  );
}
