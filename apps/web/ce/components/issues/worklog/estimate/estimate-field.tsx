/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6 — the estimate on a work item: set / replace it before work starts,
// re-estimate (with an optional reason) after it locks, a history of every change, and "logged vs
// estimate" that turns amber once the logged time passes the current estimate. Rendered inside the
// work item's "Time tracking" property for project admins and members only; guests never mount it
// (the endpoint answers them 403). Nothing shows while the project's estimate mode is Off.
import { useCallback, useEffect, useRef, useState } from "react";
import { History, Lock, Pencil } from "lucide-react";
import useSWR, { mutate as globalMutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { Popover, PopoverContent, PopoverTrigger } from "@makeplane/propel/components/popover";
import { setToast } from "@plane/blocks/toast";
import { cn } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { formatLoggedMinutes } from "../timer-utils";
import type { TIssueTimeEstimate } from "../types";
import {
  describeEstimateError,
  estimatePercent,
  estimateToInput,
  formatRevisionTime,
  isOverEstimate,
  parseEstimateInput,
  REVISION_LABEL,
} from "./estimate-utils";
import { registerEstimateField, subscribeEstimateRequired } from "./estimate-required";

export const ISSUE_ESTIMATE_KEY = (slug: string, issueId: string) => `ISSUE_TIME_ESTIMATE_${slug}_${issueId}`;

/** Refresh the estimate card of one work item (after a timer start or a state move that may lock it). */
export const refreshIssueEstimate = (slug: string, issueId: string) => globalMutate(ISSUE_ESTIMATE_KEY(slug, issueId));

type TProps = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  /** minutes already logged on the item (finished entries) */
  loggedMinutes: number;
  /** the work item is read only here (archived, peek of a closed item) */
  disabled?: boolean;
  timeZone: string;
};

export function IssueEstimateField(props: TProps) {
  const { workspaceSlug, projectId, issueId, loggedMinutes, disabled, timeZone } = props;
  const { data, mutate } = useSWR<TIssueTimeEstimate>(
    ISSUE_ESTIMATE_KEY(workspaceSlug, issueId),
    () => timeTrackingService.getIssueEstimate(workspaceSlug, projectId, issueId),
    // the lock flips when a timer starts or the item moves, possibly from another tab or person
    { refreshInterval: 10_000, shouldRetryOnError: false }
  );

  const [editOpen, setEditOpen] = useState(false);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [attention, setAttention] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  // focus the input when the popover mounts it (stable callback so typing never re-steals focus)
  const focusInput = useCallback((el: HTMLInputElement | null) => el?.focus(), []);
  const attentionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const estimate = data?.estimate ?? null;
  const current = estimate?.current_minutes ?? null;
  const locked = Boolean(estimate?.locked);
  const canEdit = Boolean(data?.can_edit) && !disabled;

  const openEditor = () => {
    setText(estimateToInput(estimateRef.current?.current_minutes ?? null));
    setReason("");
    setError(null);
    setEditOpen(true);
  };

  // Work items that fail with "estimate required" ask this field to open itself. The listener lives for
  // the whole mount, so it reads the latest values through refs.
  const canEditRef = useRef(canEdit);
  canEditRef.current = canEdit;
  const estimateRef = useRef(estimate);
  estimateRef.current = estimate;
  const openEditorRef = useRef(openEditor);
  openEditorRef.current = openEditor;
  useEffect(() => {
    const unregister = registerEstimateField(issueId);
    const unsubscribe = subscribeEstimateRequired((detail) => {
      if (detail.issueId !== issueId) return;
      rootRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      setAttention(true);
      if (attentionTimer.current) clearTimeout(attentionTimer.current);
      attentionTimer.current = setTimeout(() => setAttention(false), 3000);
      if (canEditRef.current) openEditorRef.current();
      // the card may be stale (mode just turned on): refresh it
      void mutate();
    });
    return () => {
      unregister();
      unsubscribe();
      if (attentionTimer.current) clearTimeout(attentionTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId]);

  // Off, still loading, or the caller may not see estimates (403): nothing at all.
  if (!data || data.mode === "off") return null;

  const save = async (minutes: number | null) => {
    setSaving(true);
    setError(null);
    try {
      const next = await timeTrackingService.setIssueEstimate(workspaceSlug, projectId, issueId, {
        minutes,
        ...(locked && reason.trim() ? { reason: reason.trim() } : {}),
      });
      await mutate(next, { revalidate: false });
      setEditOpen(false);
      setToast({
        type: "success",
        title: minutes === null ? "Estimate cleared" : locked ? "Re-estimate saved" : "Estimate saved",
        message: minutes === null ? undefined : `${formatLoggedMinutes(minutes)} for this work item.`,
      });
    } catch (err) {
      setError(describeEstimateError(err));
      void mutate();
    } finally {
      setSaving(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseEstimateInput(text);
    if (!parsed.ok) {
      setError(parsed.message);
      return;
    }
    if (parsed.minutes === current) {
      setEditOpen(false);
      return;
    }
    void save(parsed.minutes);
  };

  const over = isOverEstimate(loggedMinutes, current);
  const percent = estimatePercent(loggedMinutes, current);
  const reestimated = Boolean(estimate && estimate.original_minutes !== current);
  const revisions = data.revisions ?? [];
  const whoSetOriginal = estimate?.original_by?.display_name;

  return (
    <div
      ref={rootRef}
      data-testid="estimate-field"
      data-mode={data.mode}
      data-locked={locked ? "true" : "false"}
      data-attention={attention ? "true" : undefined}
      className={cn("flex w-full min-w-0 flex-col gap-1 rounded-md break-words whitespace-normal", {
        "ring-amber-500 ring-2 ring-offset-2 ring-offset-transparent": attention,
      })}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-13">
        <span className="text-tertiary">Estimate</span>
        <span data-testid="estimate-value" className={cn("font-medium", { "text-tertiary": current === null })}>
          {current === null ? "Not set" : formatLoggedMinutes(current)}
        </span>
        {locked && (
          <span
            title="The original estimate locked when work started. Changes from now on are re-estimates."
            aria-label="Original estimate locked"
            data-testid="estimate-locked"
            className="inline-flex items-center text-tertiary"
          >
            <Lock className="size-3" />
          </span>
        )}
        {canEdit && (
          <Popover open={editOpen} onOpenChange={(open) => (open ? openEditor() : setEditOpen(false))}>
            <PopoverTrigger
              render={
                <button
                  type="button"
                  className="shrink-0 rounded-sm text-tertiary hover:text-primary"
                  aria-label={current === null ? "Set estimate" : locked ? "Re-estimate" : "Change estimate"}
                  data-testid="estimate-edit"
                >
                  <Pencil className="size-3" />
                </button>
              }
            />
            <PopoverContent variant="rich" side="bottom" align="start">
              <form onSubmit={submit} className="flex w-72 max-w-full flex-col gap-3" data-testid="estimate-form">
                <div>
                  <label htmlFor={`estimate-input-${issueId}`} className="text-sm mb-1 block font-medium text-primary">
                    {locked ? "Re-estimate" : "Estimate"} (hours)
                  </label>
                  <InputGroup size="lg">
                    <Input
                      id={`estimate-input-${issueId}`}
                      size="lg"
                      ref={focusInput}
                      autoComplete="off"
                      placeholder="2, 1.5, 2h 30m or 90m"
                      data-testid="estimate-input"
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                    />
                  </InputGroup>
                </div>
                {locked ? (
                  <>
                    <p className="text-xs text-tertiary" data-testid="estimate-locked-note">
                      Work has started, so the original estimate
                      {estimate?.original_minutes ? ` (${formatLoggedMinutes(estimate.original_minutes)}` : ""}
                      {whoSetOriginal ? ` by ${whoSetOriginal})` : estimate?.original_minutes ? ")" : ""} stays on
                      record. This change is saved as a re-estimate.
                    </p>
                    <div>
                      <label htmlFor={`estimate-reason-${issueId}`} className="text-sm mb-1 block text-tertiary">
                        Reason (optional)
                      </label>
                      <textarea
                        id={`estimate-reason-${issueId}`}
                        rows={2}
                        data-testid="estimate-reason"
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder="Why is the estimate changing?"
                        className="text-sm w-full resize-none rounded-md border border-subtle bg-transparent px-3 py-2 outline-none focus:border-accent-strong"
                      />
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-tertiary">
                    Until work starts, a new estimate replaces this one. It locks when a timer starts, time is logged or
                    the item moves to a started state.
                  </p>
                )}
                {error && (
                  <div role="alert" data-testid="estimate-error" className="text-sm text-danger-primary">
                    {error}
                  </div>
                )}
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {!locked && current !== null && (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      stretch="auto"
                      label="Clear"
                      disabled={saving}
                      data-testid="estimate-clear"
                      onClick={() => void save(null)}
                    />
                  )}
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    stretch="auto"
                    label="Cancel"
                    onClick={() => setEditOpen(false)}
                  />
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    stretch="auto"
                    label={saving ? "Saving" : "Save"}
                    loading={saving}
                    data-testid="estimate-save"
                  />
                </div>
              </form>
            </PopoverContent>
          </Popover>
        )}
        {revisions.length > 0 && (
          <Popover>
            <PopoverTrigger
              render={
                <button
                  type="button"
                  className="shrink-0 rounded-sm text-tertiary hover:text-primary"
                  aria-label="Estimate history"
                  data-testid="estimate-history-button"
                >
                  <History className="size-3" />
                </button>
              }
            />
            <PopoverContent variant="rich" side="bottom" align="start">
              <div className="flex w-72 max-w-full flex-col gap-2" data-testid="estimate-history">
                <div className="text-sm font-medium text-primary">Estimate history</div>
                <ol className="flex flex-col gap-2">
                  {revisions.map((r) => (
                    <li key={r.id} data-testid="estimate-revision" data-kind={r.kind} className="text-xs">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="font-medium text-primary">
                          {REVISION_LABEL[r.kind]}
                          {r.minutes !== null ? `: ${formatLoggedMinutes(r.minutes)}` : ""}
                        </span>
                      </div>
                      <div className="text-tertiary">
                        {r.set_by?.display_name ?? "Someone"} · {formatRevisionTime(r.created_at, timeZone)}
                      </div>
                      {r.reason && <div className="mt-0.5 text-secondary">“{r.reason}”</div>}
                    </li>
                  ))}
                </ol>
              </div>
            </PopoverContent>
          </Popover>
        )}
      </div>

      {current === null && data.required && !data.grandfathered && (
        <div data-testid="estimate-required-note" className="text-xs text-warning-primary">
          This project needs an estimate before work starts.
        </div>
      )}
      {estimate?.grandfathered && (
        <div className="text-xs text-tertiary">Started before estimates were required, so none is needed.</div>
      )}
      {reestimated && estimate && (
        <div className="text-xs text-tertiary" data-testid="estimate-original">
          Original {estimate.original_minutes ? formatLoggedMinutes(estimate.original_minutes) : "—"}
          {whoSetOriginal ? ` by ${whoSetOriginal}` : ""}
        </div>
      )}

      {current !== null && (
        <div
          data-testid="estimate-vs-logged"
          data-over={over ? "true" : "false"}
          className="text-xs flex min-w-0 flex-col gap-1"
        >
          <div
            className={cn("flex items-center justify-between gap-2", over ? "text-warning-primary" : "text-tertiary")}
          >
            <span>
              Logged {formatLoggedMinutes(loggedMinutes)} of {formatLoggedMinutes(current)}
            </span>
            <span className="tabular-nums">{percent}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-layer-3" aria-hidden>
            <div
              className={cn("h-full rounded-full", over ? "bg-amber-500" : "bg-accent-primary")}
              style={{ width: `${Math.min(100, percent)}%` }}
            />
          </div>
          {over && (
            <span className="text-warning-primary">
              Over the estimate by {formatLoggedMinutes(loggedMinutes - current)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
