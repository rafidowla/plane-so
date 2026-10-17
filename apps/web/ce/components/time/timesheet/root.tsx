/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S4 — the "My timesheet" tab: the timer bar (today only), a Monday-to-Sunday week
// grid, the week's entries by date, "Add time" when allowed, past-day requests, and My requests below.
// Project / workspace admins can open someone else's week (read-only) from the person select.
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { observer } from "mobx-react";
import useSWR, { mutate as globalMutate } from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { asApiError } from "@/plane-web/components/analytics/api-error";
import { useRunningTimer } from "@/plane-web/hooks/use-running-timer";
import { timeTimesheetService } from "@/plane-web/services/time-timesheet.service";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { LogTimeModal } from "../../issues/worklog/log-time-modal";
import type { TWorklog } from "../../issues/worklog/types";
import { refreshRequestData } from "../requests/request-keys";
import { MyRequests } from "../requests/my-requests";
import { RequestModal, type TRequestModalKind } from "../requests/request-modal";
import { safeTimeZone } from "../requests/request-utils";
import { TimerBar } from "../timer/timer-bar";
import { refreshTimeData, timesheetKeyPrefix } from "../timer/timer-actions";
import { TimesheetEntries } from "./timesheet-entries";
import { TimesheetGrid } from "./timesheet-grid";
import { formatWeekRange, mondayOf, shiftWeek } from "./timesheet-utils";
import type { TTimesheetPeople, TTimesheetWeek } from "./types";

const REFRESH_MS = 15_000;
const ME = "";

type TModal = { kind: TRequestModalKind; entry?: TWorklog | null; direct?: boolean; defaultDate?: string } | null;

const APPROVAL_LABEL: Record<TTimesheetWeek["approval"]["status"], string> = {
  open: "Not submitted",
  submitted: "Submitted, waiting for approval",
  approved: "Approved",
  rejected: "Rejected",
};

export const TimesheetTab = observer(function TimesheetTab() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const [weekStart, setWeekStart] = useState<string | null>(null); // null = the current week
  const [userId, setUserId] = useState(ME);
  const [modal, setModal] = useState<TModal>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<TWorklog | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const key = workspaceSlug ? `${timesheetKeyPrefix(workspaceSlug)}_${userId || "me"}_${weekStart ?? "current"}` : null;
  const {
    data: week,
    error,
    mutate,
  } = useSWR<TTimesheetWeek>(
    key,
    () =>
      timeTimesheetService.getWeek(workspaceSlug, {
        weekStart: weekStart ?? undefined,
        userId: userId || undefined,
      }),
    {
      keepPreviousData: true,
      refreshInterval: () => (typeof document !== "undefined" && document.hidden ? 0 : REFRESH_MS),
      revalidateOnFocus: true,
    }
  );
  const { data: people } = useSWR<TTimesheetPeople>(
    workspaceSlug ? `TIME_TIMESHEET_PEOPLE_${workspaceSlug}` : null,
    () => timeTimesheetService.getPeople(workspaceSlug)
  );

  // A timer stopped from anywhere (bar, work item, auto-stop, another tab) adds an entry: pull it in.
  const { timer } = useRunningTimer(workspaceSlug);
  const lastTimerId = useRef<string | null>(null);
  useEffect(() => {
    const id = timer?.id ?? null;
    if (lastTimerId.current && !id) void mutate();
    lastTimerId.current = id;
  }, [timer?.id, mutate]);

  const refresh = useCallback(async () => {
    await Promise.all([mutate(), refreshRequestData(workspaceSlug), refreshTimeData(workspaceSlug)]);
  }, [mutate, workspaceSlug]);

  const timeZone = safeTimeZone(week?.timezone ?? currentWorkspace?.timezone);

  if (!workspaceSlug) return null;

  const backToMe = () => {
    setUserId(ME);
    setWeekStart(null);
  };

  if (!week && error) {
    return (
      <div className="flex flex-col items-center gap-3 py-12" data-testid="timesheet-error">
        <p className="text-sm text-tertiary">{asApiError(error).error ?? "Could not load this timesheet."}</p>
        {userId && (
          <Button variant="secondary" size="md" stretch="auto" label="Back to my timesheet" onClick={backToMe} />
        )}
      </div>
    );
  }
  if (!week) {
    return (
      <div className="px-4 py-4" data-testid="timesheet-loading">
        <Loader className="space-y-3">
          <Loader.Item height="48px" />
          <Loader.Item height="220px" />
        </Loader>
      </div>
    );
  }

  const isCurrentWeek = week.week_start === mondayOf(week.today);
  const goWeek = (weeks: number) => setWeekStart(shiftWeek(week.week_start, weeks));
  const canRequestDay = week.is_self && !week.viewer.is_workspace_admin;
  const approval = week.approval;
  const canSubmit = week.is_self && approval.enabled && (approval.status === "open" || approval.status === "rejected");

  const submitWeek = async () => {
    setSubmitting(true);
    try {
      await timeTrackingService.submitTimesheet(workspaceSlug, week.week_start, week.week_end);
      setToast({ type: "success", title: "Week submitted", message: "Your approver will review it." });
      await refresh();
    } catch (err) {
      setToast({ type: "error", title: "Could not submit", message: asApiError(err).error ?? "Try again." });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDirectDelete = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await timeTimesheetService.deleteEntry(workspaceSlug, confirmDelete.id);
      setToast({ type: "success", title: "Time entry deleted" });
      setConfirmDelete(null);
      await refresh();
    } catch (err) {
      setToast({ type: "error", title: "Could not delete", message: asApiError(err).error ?? "Try again." });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 px-4 py-4" data-testid="timesheet-tab" data-week-start={week.week_start}>
      {week.is_self && <TimerBar workspaceSlug={workspaceSlug} />}

      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-16 font-semibold text-primary" data-testid="timesheet-title">
          {week.is_self ? "My timesheet" : `${week.user.display_name}'s timesheet`}
        </h2>
        <div className="flex items-center gap-1" data-testid="timesheet-week-nav">
          <button
            type="button"
            aria-label="Previous week"
            data-testid="timesheet-prev"
            className="rounded p-1 text-tertiary hover:bg-layer-1 hover:text-primary"
            onClick={() => goWeek(-1)}
          >
            <ChevronLeft className="size-4" />
          </button>
          <span className="text-sm min-w-44 text-center text-primary" data-testid="timesheet-range">
            {formatWeekRange(week.week_start, week.week_end)}
          </span>
          <button
            type="button"
            aria-label="Next week"
            data-testid="timesheet-next"
            className="rounded p-1 text-tertiary hover:bg-layer-1 hover:text-primary"
            onClick={() => goWeek(1)}
          >
            <ChevronRight className="size-4" />
          </button>
          {!isCurrentWeek && (
            <Button
              variant="secondary"
              size="sm"
              stretch="auto"
              label="This week"
              data-testid="timesheet-today"
              onClick={() => setWeekStart(null)}
            />
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          {people && people.results.length > 0 && (
            <select
              aria-label="Person"
              data-testid="timesheet-person"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="text-sm max-w-56 rounded border border-subtle bg-transparent px-2 py-1.5"
            >
              <option value={ME}>Me</option>
              {people.results.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          )}
          {canSubmit && (
            <Button
              variant="secondary"
              size="md"
              stretch="auto"
              label="Submit week"
              data-testid="timesheet-submit"
              loading={submitting}
              onClick={() => void submitWeek()}
            />
          )}
          {week.viewer.can_add_time && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              icon={<Plus className="size-4" />}
              label="Add time"
              data-testid="timesheet-add-time"
              onClick={() => setAddOpen(true)}
            />
          )}
        </div>
      </div>

      {!week.is_self && (
        <div
          className="text-sm rounded-md border border-subtle bg-layer-1 px-3 py-2 text-secondary"
          data-testid="timesheet-readonly"
        >
          You are viewing {week.user.display_name}&apos;s week. It is read-only.
          {week.scoped && " Only time on projects you administer is itemised."}{" "}
          <button type="button" className="text-accent-primary hover:underline" onClick={backToMe}>
            Back to my timesheet
          </button>
        </div>
      )}

      {approval.enabled && (
        <div className="text-sm flex flex-wrap items-center gap-2 text-tertiary" data-testid="timesheet-approval">
          <span>Week status:</span>
          <span className="font-medium text-primary" data-status={approval.status}>
            {APPROVAL_LABEL[approval.status]}
          </span>
          {approval.status === "rejected" && approval.review_note && <span>({approval.review_note})</span>}
        </div>
      )}

      {week.is_self && week.pending_requests.length > 0 && (
        <div
          className="text-sm rounded-md border border-subtle bg-layer-1 px-3 py-2 text-secondary"
          data-testid="timesheet-pending-requests"
        >
          {week.pending_requests.length} time {week.pending_requests.length === 1 ? "request is" : "requests are"}{" "}
          waiting for approval for this week.
        </div>
      )}

      <TimesheetGrid
        week={week}
        canRequestDay={canRequestDay}
        onRequestDay={(date) => setModal({ kind: "missing_time", defaultDate: date })}
      />

      <TimesheetEntries
        week={week}
        workspaceSlug={workspaceSlug}
        onEdit={(entry, direct) => setModal({ kind: "time_change", entry, direct })}
        onDelete={(entry, direct) => (direct ? setConfirmDelete(entry) : setModal({ kind: "deletion", entry }))}
      />

      {week.is_self && <MyRequests />}

      {modal && (
        <RequestModal
          isOpen
          handleClose={() => setModal(null)}
          workspaceSlug={workspaceSlug}
          timeZone={timeZone}
          kind={modal.kind}
          entry={modal.entry}
          direct={modal.direct}
          defaultDate={modal.defaultDate}
          onDone={() => void refresh()}
        />
      )}
      <LogTimeModal
        isOpen={addOpen}
        handleClose={() => setAddOpen(false)}
        workspaceSlug={workspaceSlug}
        defaultDate={week.today}
        onSaved={() => void refresh()}
      />
      <ConfirmDialog
        isOpen={confirmDelete !== null}
        handleClose={() => setConfirmDelete(null)}
        handleSubmit={confirmDirectDelete}
        isSubmitting={deleting}
        title="Delete time entry"
        content="Are you sure you want to delete this time entry? The logged time will be permanently removed and cannot be recovered."
      />
    </div>
  );
});

// Re-exported for tests that need to refresh the same keys.
export const refreshTimesheet = (slug: string) =>
  globalMutate((k) => typeof k === "string" && k.startsWith(timesheetKeyPrefix(slug)));
