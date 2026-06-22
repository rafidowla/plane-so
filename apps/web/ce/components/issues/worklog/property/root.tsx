/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useEffect, useState } from "react";
import { Play, Square, Plus, Timer, X } from "lucide-react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { EUserProjectRoles } from "@plane/types";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
import { useMember } from "@/hooks/store/use-member";
import { useProject } from "@/hooks/store/use-project";
import { useUser } from "@/hooks/store/user";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { LogTimeModal } from "../log-time-modal";

type TIssueWorklogProperty = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled: boolean;
};

function fmtMinutes(total: number): string {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

function fmtElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export const IssueWorklogProperty = observer(function IssueWorklogProperty(props: TIssueWorklogProperty) {
  const { workspaceSlug, projectId, issueId, disabled } = props;
  // hooks must run unconditionally
  const { getProjectById } = useProject();
  const { data: currentUser } = useUser();
  const { project: projectMembers } = useMember();
  const [isLogOpen, setLogOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const project = getProjectById(projectId);
  const enabled = Boolean(project?.is_time_tracking_enabled);

  const { data: worklogs, mutate: mutateWorklogs } = useSWR(
    enabled ? `WORKLOGS_${workspaceSlug}_${issueId}` : null,
    enabled ? () => timeTrackingService.getWorklogs(workspaceSlug, projectId, issueId) : null
  );
  const { data: timer, mutate: mutateTimer } = useSWR(
    enabled ? `WORKLOG_TIMER_${workspaceSlug}_${issueId}` : null,
    enabled ? () => timeTrackingService.getTimer(workspaceSlug, projectId, issueId) : null
  );

  const runningHere = Boolean(timer && timer.id && timer.issue === issueId);
  useEffect(() => {
    if (!runningHere) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [runningHere]);

  if (!enabled) return null;

  const role = currentUser?.id ? projectMembers.getUserProjectRole(currentUser.id, projectId) : undefined;
  const isProjectAdmin = role === EUserProjectRoles.ADMIN;
  const list = worklogs ?? [];
  const total = list.reduce((sum, w) => sum + (w.duration || 0), 0);
  const elapsed =
    runningHere && timer?.started_at ? Math.max(0, Math.floor((now - new Date(timer.started_at).getTime()) / 1000)) : 0;

  const handleStart = async () => {
    try {
      await timeTrackingService.startTimer(workspaceSlug, projectId, issueId);
      mutateTimer();
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Cannot start timer", message: err?.error ?? "Try again." });
    }
  };

  const handleStop = async () => {
    try {
      await timeTrackingService.stopTimer(workspaceSlug, projectId, issueId);
      await Promise.all([mutateTimer(), mutateWorklogs()]);
      setToast({ type: TOAST_TYPE.SUCCESS, title: "Time logged", message: "Timer stopped and time recorded." });
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Cannot stop timer", message: err?.error ?? "Try again." });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await timeTrackingService.deleteWorklog(workspaceSlug, projectId, issueId, id);
      mutateWorklogs();
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Cannot delete", message: err?.error ?? "Try again." });
    }
  };

  return (
    <>
      <SidebarPropertyListItem icon={Timer} label="Time tracking" childrenClassName="flex-col items-start">
        <div className="flex w-full flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{fmtMinutes(total)} logged</span>
            {!disabled &&
              (runningHere ? (
                <Button variant="secondary" size="sm" onClick={handleStop}>
                  <Square className="mr-1 size-3" />
                  Stop {fmtElapsed(elapsed)}
                </Button>
              ) : (
                <Button variant="secondary" size="sm" onClick={handleStart}>
                  <Play className="mr-1 size-3" />
                  Start
                </Button>
              ))}
            {!disabled && (
              <Button variant="secondary" size="sm" onClick={() => setLogOpen(true)}>
                <Plus className="mr-1 size-3" />
                Log time
              </Button>
            )}
          </div>

          {list.length > 0 && (
            <div className="flex w-full flex-col gap-1">
              {list.slice(0, 6).map((w) => (
                <div key={w.id} className="text-xs flex items-center justify-between gap-2 text-tertiary">
                  <span className="truncate">
                    {fmtMinutes(w.duration)} · {w.logged_by_detail?.display_name ?? "—"} · {w.logged_date}
                    {w.is_billable ? "" : " · non-billable"}
                    {w.is_locked ? " · 🔒" : ""}
                  </span>
                  {!disabled && !w.is_locked && (
                    <button
                      type="button"
                      onClick={() => handleDelete(w.id)}
                      className="hover:text-danger shrink-0 text-tertiary"
                      aria-label="Delete time entry"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              ))}
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
        onSaved={() => mutateWorklogs()}
      />
    </>
  );
});
