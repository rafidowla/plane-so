/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking — fills the worklog property stub
import { useEffect, useState } from "react";
import { Pencil, Play, Square, Plus, Timer, X } from "lucide-react";
import { observer } from "mobx-react";
import useSWR, { mutate as globalMutate } from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Button } from "@makeplane/propel/components/button";
import { Icon } from "@makeplane/propel/components/icon";
import { ConfirmDialog } from "@plane/blocks/dialog";
import { setToast } from "@plane/blocks/toast";
import { SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUser, useUserPermissions } from "@/hooks/store/user";
import { asApiError } from "@/plane-web/components/analytics/api-error";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { revalidateRunningTimer } from "@/plane-web/hooks/use-running-timer";
import { LogTimeModal } from "../log-time-modal";
// FORK: time-tracking-v2
import { describeTimerError } from "@/plane-web/components/time/timer/errors";
import { requestNotificationPermissionOnce } from "@/plane-web/components/time/timer/notify";
import { getStateChangeToastTitle } from "@/plane-web/components/time/timer/timer-actions";
import { useCanReviewTime } from "@/plane-web/components/time/approvals/access";
import { ISSUE_PENDING_KEY } from "@/plane-web/components/time/requests/request-keys";
import { RequestModal, type TRequestModalKind } from "@/plane-web/components/time/requests/request-modal";
import { safeTimeZone } from "@/plane-web/components/time/requests/request-utils";
import type { TTimeRequest, TTimerStartResponse, TWorklog } from "../types";
import { formatElapsed, formatLoggedMinutes, getAutoStopToastTitle } from "../timer-utils";
// FORK: time-tracking-v2 S6 — work item estimate
import { IssueEstimateField, refreshIssueEstimate } from "../estimate/estimate-field";
import { handleEstimateRequired } from "../estimate/estimate-required";

// FORK: time-tracking-v2 S6 — an open work item re-reads its time every 10 s (DESIGN §5)
const ITEM_REFRESH_MS = 10_000;

type TIssueWorklogProperty = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
};

export const IssueWorklogProperty = observer(function IssueWorklogProperty(props: TIssueWorklogProperty) {
  const { workspaceSlug, projectId, issueId, disabled } = props;
  // hooks must run unconditionally
  const { getProjectById } = useProject();
  const { data: currentUser } = useUser();
  const { allowPermissions } = useUserPermissions();
  const [isLogOpen, setLogOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  // FORK: PSR-37 — confirm before deleting a time-log entry
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  // FORK: time-tracking-v2 S3b — the request modal: missing time for this item, or a change / deletion /
  // direct edit of one entry
  const [requestModal, setRequestModal] = useState<{
    kind: TRequestModalKind;
    entry?: TWorklog;
    direct?: boolean;
  } | null>(null);
  const { currentWorkspace } = useWorkspace();
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const canReview = useCanReviewTime(workspaceSlug);
  const timeZone = safeTimeZone(currentWorkspace?.timezone);

  // is_time_tracking_enabled is returned by the project API but not on the TProject type.
  const project = getProjectById(projectId) as ({ is_time_tracking_enabled?: boolean } & object) | undefined;
  const enabled = Boolean(project?.is_time_tracking_enabled);

  const { data: worklogs, mutate: mutateWorklogs } = useSWR(
    enabled ? `WORKLOGS_${workspaceSlug}_${issueId}` : null,
    enabled ? () => timeTrackingService.getWorklogs(workspaceSlug, projectId, issueId) : null,
    // FORK: time-tracking-v2 S6 — keep an open item current (other people's entries, a timer started elsewhere)
    { refreshInterval: ITEM_REFRESH_MS, refreshWhenHidden: false }
  );
  const { data: timer, mutate: mutateTimer } = useSWR(
    enabled ? `WORKLOG_TIMER_${workspaceSlug}_${issueId}` : null,
    enabled ? () => timeTrackingService.getTimer(workspaceSlug, projectId, issueId) : null,
    { refreshInterval: ITEM_REFRESH_MS, refreshWhenHidden: false }
  );
  // FORK: time-tracking-v2 — whether the caller is a tracked user at all (guests never are). Shares the
  // task picker's cache key, so it costs one request per page.
  const { data: myTasks } = useSWR(
    enabled ? `WORKSPACE_MY_TASKS_${workspaceSlug}_` : null,
    enabled ? () => timeTrackingService.getMyTasks(workspaceSlug, "", 15) : null
  );
  const isTracked = myTasks?.tracked === true;
  // FORK: time-tracking-v2 S3b — pending requests aimed at this item's entries (own, plus those the viewer
  // can review) drive the "Change pending" badge. One request per entry can be pending at a time.
  const { data: pendingRequests } = useSWR(
    enabled ? ISSUE_PENDING_KEY(workspaceSlug, issueId) : null,
    enabled
      ? async () => {
          const params = { status: "pending", project_id: projectId, limit: 200 };
          const [mine, review] = await Promise.all([
            timeTrackingService.getTimeRequests(workspaceSlug, { scope: "mine", ...params }),
            canReview
              ? timeTrackingService.getTimeRequests(workspaceSlug, { scope: "review", ...params })
              : Promise.resolve({ count: 0, results: [] as TTimeRequest[] }),
          ]);
          return [...mine.results, ...review.results];
        }
      : null
  );

  const runningHere = Boolean(timer && timer.id && timer.issue === issueId);
  useEffect(() => {
    if (!runningHere) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [runningHere]);

  if (!enabled) return null;

  const isProjectAdmin = allowPermissions(
    [EUserPermissions.ADMIN],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  // Guests (clients) see the reported total only — never the per-entry breakdown,
  // who logged it, or the self/PM source. Internal roles (admin/member) see detail.
  const canSeeDetails = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );
  const currentUserId = currentUser?.id ?? "";
  // FORK: time-tracking-v2 S3b — workspace admins write time directly; everyone else asks their approver.
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const pendingByEntry = new Map<string, TTimeRequest>();
  (pendingRequests ?? []).forEach((r) => {
    if (r.worklog_id && r.issue_id === issueId) pendingByEntry.set(r.worklog_id, r);
  });
  const pickedIssue = (() => {
    const issue = getIssueById(issueId);
    const identifier = getProjectById(projectId)?.identifier;
    return {
      id: issueId,
      ref: identifier && issue?.sequence_id ? `${identifier}-${issue.sequence_id}` : "",
      name: issue?.name ?? "",
    };
  })();
  // FORK: time-tracking-v2 — guests get only `{ total_minutes }`; internal roles get the entry list.
  const list = Array.isArray(worklogs) ? worklogs : [];
  const total = Array.isArray(worklogs)
    ? list.reduce((sum, w) => sum + (w.duration || 0), 0)
    : ((worklogs as { total_minutes?: number } | undefined)?.total_minutes ?? 0);
  const elapsed =
    runningHere && timer?.started_at ? Math.max(0, Math.floor((now - new Date(timer.started_at).getTime()) / 1000)) : 0;

  const handleStart = async () => {
    // FORK: time-tracking-v2 — ask for notification permission on this click (needed for "Still working?")
    requestNotificationPermissionOnce();
    try {
      const started = await timeTrackingService.startTimer(workspaceSlug, projectId, issueId);
      // FORK: PSR-101 — starting here stops a timer running elsewhere (server-side, atomically)
      // and logs its time; tell the user, and refresh that work item's widget if it is cached.
      const stoppedTitle = getAutoStopToastTitle(started.stopped);
      if (stoppedTitle) {
        setToast({ type: "info", title: stoppedTitle });
        const stoppedIssueId = started.stopped?.issue?.id;
        if (stoppedIssueId) {
          void globalMutate(`WORKLOG_TIMER_${workspaceSlug}_${stoppedIssueId}`);
          void globalMutate(`WORKLOGS_${workspaceSlug}_${stoppedIssueId}`);
        }
      }
      // FORK: time-tracking-v2 — "Moved to In Progress" when starting advanced the item's state
      const movedTitle = getStateChangeToastTitle((started as TTimerStartResponse).state_change);
      if (movedTitle) setToast({ type: "success", title: movedTitle });
      await Promise.all([
        mutateTimer(),
        mutateWorklogs(),
        revalidateRunningTimer(workspaceSlug),
        // FORK: time-tracking-v2 S6 — starting work locks the original estimate
        refreshIssueEstimate(workspaceSlug, issueId),
      ]);
    } catch (err) {
      // FORK: time-tracking-v2 S6 — the project needs an estimate first: the estimate field opens
      if (handleEstimateRequired(err, { issueId, workspaceSlug, projectId })) return;
      const info = describeTimerError(err);
      setToast({ type: info.alreadyRunning ? "info" : "error", title: info.title, message: info.message });
      if (info.alreadyRunning) await Promise.all([mutateTimer(), revalidateRunningTimer(workspaceSlug)]);
    }
  };

  const handleStop = async () => {
    try {
      await timeTrackingService.stopTimer(workspaceSlug, projectId, issueId);
      await Promise.all([mutateTimer(), mutateWorklogs(), revalidateRunningTimer(workspaceSlug)]);
      setToast({ type: "success", title: "Time logged", message: "Timer stopped and time recorded." });
    } catch (err) {
      setToast({ type: "error", title: "Cannot stop timer", message: asApiError(err).error ?? "Try again." });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await timeTrackingService.deleteWorklog(workspaceSlug, projectId, issueId, id);
      mutateWorklogs();
    } catch (err) {
      setToast({ type: "error", title: "Cannot delete", message: asApiError(err).error ?? "Try again." });
    }
  };

  // FORK: PSR-37 — the X only opens the confirmation; nothing is deleted until confirmed
  const handleDeleteConfirm = async () => {
    if (!pendingDeleteId) return;
    setIsDeleting(true);
    await handleDelete(pendingDeleteId);
    setIsDeleting(false);
    setPendingDeleteId(null);
  };

  return (
    <>
      {/* FORK: PSR-36 — min-w-0 is essential: the full-screen sidebar clips its property list
          (`truncate` = overflow-hidden). Without it this flex column grows to the entry text's
          width, pushing the delete (x) button past the clip edge so it was invisible there but
          visible in the wider peek panel. */}
      <SidebarPropertyListItem icon={Timer} label="Time tracking" childrenClassName="min-w-0 flex-col items-start">
        <div className="flex w-full min-w-0 flex-col gap-2">
          <span className="text-sm font-medium">{formatLoggedMinutes(total)} logged</span>
          {/* FORK: time-tracking-v2 S6 — estimate, history and logged-vs-estimate; admins and members only (guests: total only) */}
          {canSeeDetails && (
            <IssueEstimateField
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              issueId={issueId}
              loggedMinutes={total}
              disabled={disabled}
              timeZone={timeZone}
            />
          )}
          {canSeeDetails && !disabled && (
            <div className="flex flex-wrap items-center gap-2">
              {/* FORK: time-tracking-v2 — the timer button is for tracked users only (guests/untracked never see it) */}
              {isTracked && runningHere && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  icon={<Icon icon={Square} />}
                  label={`Stop ${formatElapsed(elapsed)}`}
                  onClick={handleStop}
                />
              )}
              {isTracked && !runningHere && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  icon={<Icon icon={Play} />}
                  label="Start"
                  onClick={handleStart}
                />
              )}
              {/* FORK: time-tracking-v2 S3b — members ask for time they could not track; workspace admins log directly */}
              {isTracked && !isWorkspaceAdmin && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  icon={<Icon icon={Plus} />}
                  label="Request missing time"
                  data-testid="worklog-request-missing"
                  onClick={() => setRequestModal({ kind: "missing_time" })}
                />
              )}
              {/* Manual entry is a PM/admin function; members self-track with the timer. */}
              {isProjectAdmin && (
                <Button
                  variant="secondary"
                  size="sm"
                  stretch="auto"
                  icon={<Icon icon={Plus} />}
                  label="Log time"
                  onClick={() => setLogOpen(true)}
                />
              )}
            </div>
          )}

          {canSeeDetails && list.length > 0 && (
            <div className="flex w-full min-w-0 flex-col gap-1">
              {list.slice(0, 6).map((w) => {
                // Only PMs/admins, the resource, or whoever entered it may remove an entry.
                const canModify = isProjectAdmin || w.logged_by === currentUserId || w.created_by === currentUserId;
                // FORK: time-tracking-v2 S3b — own entries (unless workspace admin) change only by request;
                // a project admin's edit of someone else's entry, and any workspace admin edit, is direct.
                const isOwn = w.logged_by === currentUserId;
                const needsRequest = isOwn && !isWorkspaceAdmin && isTracked;
                const canEditDirect = isWorkspaceAdmin || (isProjectAdmin && !isOwn);
                const pending = pendingByEntry.get(w.id);
                const isSelfTracked = w.source === "timer";
                return (
                  <div key={w.id} className="text-xs flex min-w-0 items-center justify-between gap-2 text-tertiary">
                    <span className="min-w-0 truncate">
                      {formatLoggedMinutes(w.duration)} · {w.logged_by_detail?.display_name ?? "—"} ·{" "}
                      <span title={isSelfTracked ? "Self-tracked with the timer" : "Entered manually by a PM/admin"}>
                        {isSelfTracked ? "self" : "PM"}
                      </span>{" "}
                      · {w.logged_date}
                      {w.is_billable ? "" : " · non-billable"}
                      {w.is_locked ? " · 🔒" : ""}
                    </span>
                    {pending && (
                      <span
                        className="text-xs shrink-0 rounded bg-warning-subtle px-1.5 py-0.5 text-warning-primary"
                        title="A request to change this entry is waiting for review"
                        data-testid="change-pending-badge"
                      >
                        Change pending
                      </span>
                    )}
                    {/* FORK: time-tracking-v2 secfix — locked entries stay editable for workspace admins only (audited override). */}
                    {!disabled && (!w.is_locked || isWorkspaceAdmin) && !pending && (needsRequest || canEditDirect) && (
                      <button
                        type="button"
                        onClick={() =>
                          setRequestModal({ kind: "time_change", entry: w, direct: !needsRequest && canEditDirect })
                        }
                        className="shrink-0 text-tertiary hover:text-primary"
                        aria-label={needsRequest ? "Request a time change" : "Edit time"}
                        data-testid="worklog-edit-time"
                      >
                        <Pencil className="size-3" />
                      </button>
                    )}
                    {!disabled && (!w.is_locked || isWorkspaceAdmin) && !pending && canModify && (
                      <button
                        type="button"
                        onClick={() =>
                          needsRequest ? setRequestModal({ kind: "deletion", entry: w }) : setPendingDeleteId(w.id)
                        }
                        className="hover:text-danger shrink-0 text-tertiary"
                        aria-label={needsRequest ? "Request deletion" : "Delete time entry"}
                        data-testid="worklog-delete"
                      >
                        <X className="size-3" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </SidebarPropertyListItem>

      <LogTimeModal
        isOpen={isLogOpen}
        handleClose={() => setLogOpen(false)}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        isProjectAdmin={isProjectAdmin}
        currentUserId={currentUser?.id ?? ""}
        onSaved={() => {
          void mutateWorklogs();
          // FORK: time-tracking-v2 S6 — the first entry locks the original estimate
          void refreshIssueEstimate(workspaceSlug, issueId);
        }}
      />

      {/* FORK: time-tracking-v2 S3b */}
      {requestModal && (
        <RequestModal
          isOpen
          handleClose={() => setRequestModal(null)}
          workspaceSlug={workspaceSlug}
          timeZone={timeZone}
          kind={requestModal.kind}
          issue={requestModal.kind === "missing_time" ? pickedIssue : undefined}
          entry={requestModal.entry}
          direct={requestModal.direct}
          onDone={() => void mutateWorklogs()}
        />
      )}

      {/* FORK: PSR-37 — confirm before a time-log entry is permanently deleted */}
      <ConfirmDialog
        isOpen={pendingDeleteId !== null}
        handleClose={() => setPendingDeleteId(null)}
        handleSubmit={handleDeleteConfirm}
        isSubmitting={isDeleting}
        title="Delete time entry"
        content="Are you sure you want to delete this time entry? The logged time will be permanently removed and cannot be recovered."
      />
    </>
  );
});
