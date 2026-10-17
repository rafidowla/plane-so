/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — "Request missing time" / "Request change" / "Request deletion" (members),
// and the direct "Edit time" for workspace admins. Times are typed in the workspace time zone.
import { useEffect, useState } from "react";
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
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { setToast } from "@plane/blocks/toast";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TWorklog } from "../../issues/worklog/types";
import { IssuePicker, type TPickedIssue } from "./issue-picker";
import { refreshRequestData } from "./request-keys";
import {
  checkWindow,
  describeRequestError,
  formatWindow,
  isoToZonedInput,
  type TRequestErrorInfo,
} from "./request-utils";

export type TRequestModalKind = "missing_time" | "time_change" | "deletion";

type TProps = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  /** workspace time zone: the start/end the person types are read in it */
  timeZone: string;
  kind: TRequestModalKind;
  /** missing_time: work item already chosen (opened from a work item) */
  issue?: TPickedIssue | null;
  /** time_change / deletion: the entry concerned */
  entry?: TWorklog | null;
  /** workspace admins change time directly: time_change saves at once, no reason needed */
  direct?: boolean;
  // FORK: time-tracking-v2 S4 — missing_time opened from a past day of the timesheet: prefill 09:00–10:00 on it.
  defaultDate?: string;
  onDone?: () => void;
};

const TITLE: Record<TRequestModalKind, string> = {
  missing_time: "Request missing time",
  time_change: "Request a time change",
  deletion: "Request deletion",
};

const SUBMIT_LABEL: Record<TRequestModalKind, string> = {
  missing_time: "Send request",
  time_change: "Send request",
  deletion: "Request deletion",
};

const HELP: Record<TRequestModalKind, string> = {
  missing_time: "Your approver reviews this before the time is added to your timesheet.",
  time_change: "Your approver reviews this before your entry changes.",
  deletion: "Your approver reviews this before your entry is removed.",
};

/** The start/end to show for an entry: its interval, or the day at 09:00 for a duration-only entry. */
export function entryInterval(entry: TWorklog | null | undefined, timeZone: string): { start: string; end: string } {
  if (!entry) return { start: "", end: "" };
  if (entry.started_at && entry.ended_at) {
    return { start: isoToZonedInput(entry.started_at, timeZone), end: isoToZonedInput(entry.ended_at, timeZone) };
  }
  const start = new Date(`${entry.logged_date}T09:00:00Z`);
  const end = new Date(start.getTime() + (entry.duration || 0) * 60_000);
  return {
    start: `${entry.logged_date}T09:00`,
    end: Number.isNaN(end.getTime()) ? "" : end.toISOString().slice(0, 16),
  };
}

export function RequestModal(props: TProps) {
  const { isOpen, handleClose, workspaceSlug, timeZone, kind, entry, direct = false, onDone } = props;
  const [issue, setIssue] = useState<TPickedIssue | null>(props.issue ?? null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<TRequestErrorInfo | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    const interval =
      kind === "time_change"
        ? entryInterval(entry, timeZone)
        : kind === "missing_time" && props.defaultDate
          ? { start: `${props.defaultDate}T09:00`, end: `${props.defaultDate}T10:00` }
          : { start: "", end: "" };
    setIssue(props.issue ?? null);
    setStart(interval.start);
    setEnd(interval.end);
    setReason("");
    setError(null);
    setSubmitting(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, kind, entry?.id, props.issue?.id, props.defaultDate]);

  const needsWindow = kind !== "deletion";
  const reasonRequired = !(direct && kind === "time_change");

  const fail = (message: string) => setError({ title: "Check the form", message, code: null, details: [] });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);

    if (kind === "missing_time" && !issue) return fail("Choose the work item the time was spent on.");
    if ((kind === "time_change" || kind === "deletion") && !entry) return fail("This entry is no longer available.");
    let win: ReturnType<typeof checkWindow> | null = null;
    if (needsWindow) {
      win = checkWindow(start, end, timeZone);
      if (!win.ok) return fail(win.message);
    }
    const trimmed = reason.trim();
    if (reasonRequired && !trimmed) return fail("Tell your approver why you need this.");

    setSubmitting(true);
    try {
      if (direct && kind === "time_change" && entry && win?.ok) {
        await timeTrackingService.updateTimeEntry(workspaceSlug, entry.id, {
          started_at: win.startIso,
          ended_at: win.endIso,
        });
        setToast({ type: "success", title: "Time updated", message: "The entry now has the new times." });
      } else if (kind === "missing_time" && issue && win?.ok) {
        await timeTrackingService.submitTimeRequest(workspaceSlug, {
          type: "missing_time",
          issue_id: issue.id,
          started_at: win.startIso,
          ended_at: win.endIso,
          reason: trimmed,
        });
        setToast({ type: "success", title: "Request sent", message: "Your approver will review the missing time." });
      } else if (kind === "time_change" && entry && win?.ok) {
        await timeTrackingService.submitTimeRequest(workspaceSlug, {
          type: "time_change",
          worklog_id: entry.id,
          started_at: win.startIso,
          ended_at: win.endIso,
          reason: trimmed,
        });
        setToast({ type: "success", title: "Request sent", message: "Your approver will review the change." });
      } else if (kind === "deletion" && entry) {
        await timeTrackingService.submitTimeRequest(workspaceSlug, {
          type: "deletion",
          worklog_id: entry.id,
          reason: trimmed,
        });
        setToast({ type: "success", title: "Request sent", message: "Your approver will review the deletion." });
      }
      await refreshRequestData(workspaceSlug);
      onDone?.();
      handleClose();
    } catch (err) {
      setError(describeRequestError(err, timeZone));
    } finally {
      setSubmitting(false);
    }
  };

  const title = direct && kind === "time_change" ? "Edit time" : TITLE[kind];
  const submitLabel = direct && kind === "time_change" ? "Save" : SUBMIT_LABEL[kind];

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      {/* data-prevent-outside-click: keeps the issue peek overview open (see LogTimeModal). */}
      <DialogContent size="sm" data-prevent-outside-click>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col" data-testid="request-modal" data-kind={kind}>
          <DialogMain>
            <DialogHeader>
              <DialogHeading>
                <DialogTitle>{title}</DialogTitle>
              </DialogHeading>
            </DialogHeader>
            <DialogBody tabIndex={0}>
              <div className="space-y-4">
                {!direct && <p className="text-sm text-tertiary">{HELP[kind]}</p>}

                {kind === "missing_time" && (
                  <div>
                    <span className="text-sm mb-1 block text-tertiary">Work item</span>
                    <IssuePicker
                      workspaceSlug={workspaceSlug}
                      value={issue}
                      onChange={setIssue}
                      disabled={Boolean(props.issue)}
                    />
                  </div>
                )}

                {kind !== "missing_time" && entry && (
                  <div
                    className="text-sm rounded-md border border-subtle bg-layer-1 px-3 py-2"
                    data-testid="request-entry"
                  >
                    <span className="text-tertiary">Current entry</span>
                    <div className="text-primary">
                      {formatWindow(entry.started_at, entry.ended_at, timeZone, entry.duration)}
                    </div>
                    {entry.description && (
                      <div className="text-xs mt-0.5 truncate text-tertiary">{entry.description}</div>
                    )}
                  </div>
                )}

                {needsWindow && (
                  <div className="flex flex-wrap gap-3">
                    <div className="min-w-44 flex-1">
                      <span className="text-sm mb-1 block text-tertiary">
                        {kind === "time_change" ? "New start" : "Start"}
                      </span>
                      <InputGroup size="lg">
                        <Input
                          size="lg"
                          type="datetime-local"
                          aria-label="Start"
                          data-testid="request-start"
                          value={start}
                          onChange={(e) => setStart(e.target.value)}
                        />
                      </InputGroup>
                    </div>
                    <div className="min-w-44 flex-1">
                      <span className="text-sm mb-1 block text-tertiary">
                        {kind === "time_change" ? "New end" : "End"}
                      </span>
                      <InputGroup size="lg">
                        <Input
                          size="lg"
                          type="datetime-local"
                          aria-label="End"
                          data-testid="request-end"
                          value={end}
                          onChange={(e) => setEnd(e.target.value)}
                        />
                      </InputGroup>
                    </div>
                    <p className="text-xs w-full text-tertiary">Times are in the workspace time zone ({timeZone}).</p>
                  </div>
                )}

                {reasonRequired && (
                  <div>
                    <span className="text-sm mb-1 block text-tertiary">Reason</span>
                    <textarea
                      data-testid="request-reason"
                      aria-label="Reason"
                      rows={3}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="Why does this need to change?"
                      className="text-sm w-full resize-none rounded-md border border-subtle bg-transparent px-3 py-2 outline-none focus:border-accent-strong"
                    />
                  </div>
                )}

                {error && (
                  <div
                    role="alert"
                    data-testid="request-error"
                    data-code={error.code ?? ""}
                    className="text-sm rounded-md border border-danger-subtle bg-danger-subtle px-3 py-2 text-danger-primary"
                  >
                    <div>{error.message}</div>
                    {error.details.length > 0 && (
                      <ul className="text-xs mt-1 list-disc pl-4">
                        {error.details.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
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
              loading={submitting}
              data-testid="request-submit"
              label={submitting ? "Sending" : submitLabel}
            />
          </DialogActions>
        </form>
      </DialogContent>
    </Dialog>
  );
}
