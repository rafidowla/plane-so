/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — approve / reject one request. Approve takes an optional note; reject needs one.
// Auto-stopped timers are "confirmed" as they are, or confirmed with the real end time (inside the session).
import { useEffect, useState } from "react";
import { mutate as globalMutate } from "swr";
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
import type { TTimeRequest } from "../../issues/worklog/types";
import { refreshRequestData } from "../requests/request-keys";
import { describeChange, reviewTitle, type TReviewMode } from "../requests/request-utils";
import {
  describeRequestError,
  formatZonedDateTime,
  isInsideSession,
  isoToZonedInput,
  shouldRefreshAfter,
  zonedInputToIso,
  type TRequestErrorInfo,
} from "../requests/request-utils";

type TProps = {
  request: TTimeRequest | null;
  mode: TReviewMode;
  workspaceSlug: string;
  timeZone: string;
  onClose: () => void;
  /** the dialog wants the reject form instead (after a stale approval) */
  onSwitchMode: (mode: TReviewMode) => void;
};

const isAutoStop = (r: TTimeRequest | null) => r?.type === "auto_stop_review";

export function ReviewDialog({ request, mode, workspaceSlug, timeZone, onClose, onSwitchMode }: TProps) {
  const [note, setNote] = useState("");
  const [endInput, setEndInput] = useState("");
  const [error, setError] = useState<TRequestErrorInfo | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setNote("");
    setEndInput("");
    setError(null);
    setSubmitting(false);
  }, [request?.id, mode]);

  const fail = (message: string, code: TRequestErrorInfo["code"] = null) =>
    setError({ title: "Check the form", message, code, details: [] });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!request || submitting) return;
    setError(null);
    const trimmed = note.trim();
    if (mode === "reject" && !trimmed) return fail("Say why the request is rejected.", "note_required");
    let newEndedAt: string | undefined;
    if (mode === "confirm_end") {
      const iso = zonedInputToIso(endInput, timeZone);
      if (!isInsideSession(iso, request.old_started_at, request.old_ended_at)) {
        return fail("The end time must fall inside the session, after it started and before it was stopped.");
      }
      newEndedAt = iso as string;
    }
    setSubmitting(true);
    try {
      if (mode === "reject") {
        await timeTrackingService.rejectTimeRequest(workspaceSlug, request.id, trimmed);
        setToast({ type: "success", title: "Request rejected" });
      } else {
        await timeTrackingService.approveTimeRequest(workspaceSlug, request.id, {
          ...(trimmed ? { note: trimmed } : {}),
          ...(newEndedAt ? { new_ended_at: newEndedAt } : {}),
        });
        setToast({ type: "success", title: isAutoStop(request) ? "Time confirmed" : "Request approved" });
      }
      void globalMutate(`WORKLOGS_${workspaceSlug}_${request.issue_id}`);
      await refreshRequestData(workspaceSlug);
      onClose();
    } catch (err) {
      const info = describeRequestError(err, timeZone, "Could not save");
      if (info.code === "not_pending") {
        setToast({ type: "info", title: "Already reviewed", message: info.message });
        await refreshRequestData(workspaceSlug);
        onClose();
      } else {
        setError(info);
        // A stale or vanished request will never apply: reload the list so it doesn't look actionable.
        if (shouldRefreshAfter(info.code)) await refreshRequestData(workspaceSlug);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const stale = error?.code === "stale";
  const label =
    mode === "reject"
      ? "Reject"
      : mode === "confirm_end"
        ? "Confirm with end time"
        : isAutoStop(request)
          ? "Confirm"
          : "Approve";

  return (
    <Dialog
      open={request !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="sm" data-prevent-outside-click>
        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col" data-testid="review-dialog" data-mode={mode}>
          <DialogMain>
            <DialogHeader>
              <DialogHeading>
                <DialogTitle>{reviewTitle(request, mode)}</DialogTitle>
              </DialogHeading>
            </DialogHeader>
            <DialogBody tabIndex={0}>
              {request && (
                <div className="space-y-4">
                  <div className="text-sm rounded-md border border-subtle bg-layer-1 px-3 py-2">
                    <div className="text-primary">
                      {request.requester?.display_name ?? "Someone"} · {request.issue?.name ?? ""}
                    </div>
                    <div className="text-secondary">{describeChange(request, timeZone)}</div>
                    {request.reason && <div className="text-xs mt-1 text-tertiary">&ldquo;{request.reason}&rdquo;</div>}
                  </div>

                  {mode === "confirm_end" && (
                    <div>
                      <span className="text-sm mb-1 block text-tertiary">Real end time</span>
                      <InputGroup size="lg">
                        <Input
                          size="lg"
                          type="datetime-local"
                          aria-label="End time"
                          data-testid="review-end"
                          value={endInput}
                          min={isoToZonedInput(request.old_started_at, timeZone)}
                          max={isoToZonedInput(request.old_ended_at, timeZone)}
                          onChange={(e) => setEndInput(e.target.value)}
                        />
                      </InputGroup>
                      <p className="text-xs mt-1 text-tertiary">
                        Between {formatZonedDateTime(request.old_started_at, timeZone)} and{" "}
                        {formatZonedDateTime(request.old_ended_at, timeZone)}. Time after it is removed.
                      </p>
                    </div>
                  )}

                  <div>
                    <span className="text-sm mb-1 block text-tertiary">
                      {mode === "reject" ? "Reason (required)" : "Note (optional)"}
                    </span>
                    <textarea
                      data-testid="review-note"
                      aria-label={mode === "reject" ? "Reason" : "Note"}
                      rows={3}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      className="text-sm w-full resize-none rounded-md border border-subtle bg-transparent px-3 py-2 outline-none focus:border-accent-strong"
                    />
                  </div>

                  {error && (
                    <div
                      role="alert"
                      data-testid="review-error"
                      data-code={error.code ?? ""}
                      className="text-sm rounded-md border border-danger-subtle bg-danger-subtle px-3 py-2 text-danger-primary"
                    >
                      <div>{error.message}</div>
                      {stale && mode !== "reject" && (
                        <button
                          type="button"
                          data-testid="review-switch-reject"
                          className="mt-1 underline"
                          onClick={() => onSwitchMode("reject")}
                        >
                          Reject it instead
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </DialogBody>
          </DialogMain>
          <DialogActions>
            <Button variant="secondary" size="md" stretch="auto" label="Cancel" onClick={onClose} type="button" />
            <Button
              variant={mode === "reject" ? "danger" : "primary"}
              size="md"
              stretch="auto"
              type="submit"
              loading={submitting}
              disabled={stale && mode !== "reject"}
              data-testid="review-submit"
              label={submitting ? "Saving" : label}
            />
          </DialogActions>
        </form>
      </DialogContent>
    </Dialog>
  );
}
