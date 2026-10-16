/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — one person's row on the live dashboard. Renders only what the payload
// says (see live-utils): a private timer shows "private project" and no work item detail at all.
import Link from "next/link";
import { Lock } from "lucide-react";
import { Avatar } from "@makeplane/propel/components/avatar";
import { cn, generateWorkItemLink, getFileURL } from "@plane/utils";
import { formatElapsed, formatLoggedMinutes } from "../../issues/worklog/timer-utils";
import type { TLiveRow } from "../../issues/worklog/types";
import { formatAgo, getLiveRowWork, getRangeSegments, getRowElapsedSeconds, getRowTodayMinutes } from "./live-utils";

type TProps = {
  row: TLiveRow;
  workspaceSlug: string;
  nowMs: number;
  serverOffsetMs: number;
  fetchedAtMs: number;
};

export function LiveRow({ row, workspaceSlug, nowMs, serverOffsetMs, fetchedAtMs }: TProps) {
  const running = row.status === "running" && row.timer !== null;
  const work = getLiveRowWork(row);
  const segments = row.range ? getRangeSegments(row.range) : [];

  let workCell: React.ReactNode = <span className="text-tertiary">Not tracking</span>;
  if (work.kind === "private") {
    workCell = (
      <span className="flex items-center gap-1.5 text-tertiary" data-testid="live-row-private">
        <Lock className="size-3.5 shrink-0" aria-hidden />
        {work.label}
      </span>
    );
  } else if (work.kind === "item") {
    const href = generateWorkItemLink({
      workspaceSlug,
      projectId: row.timer?.project?.id ?? "",
      issueId: work.issueId,
      projectIdentifier: work.projectIdentifier,
      sequenceId: Number(work.ref.split("-").pop()),
    });
    workCell = (
      <Link href={href} className="flex min-w-0 items-center gap-2 hover:underline">
        <span className="shrink-0 font-medium text-secondary">{work.ref}</span>
        <span className="min-w-0 truncate text-primary">{work.name}</span>
        <span className="text-xs shrink-0 text-tertiary">{work.projectName}</span>
      </Link>
    );
  } else if (row.last_stopped_at) {
    workCell = <span className="text-tertiary">Stopped {formatAgo(row.last_stopped_at, nowMs, serverOffsetMs)}</span>;
  }

  return (
    <div
      data-testid="live-row"
      data-status={row.status}
      className="flex flex-col gap-2 border-b border-subtle px-4 py-3 last:border-b-0"
    >
      <div className="flex items-center gap-3">
        <span
          aria-label={running ? "Running" : "Not running"}
          className={cn("size-2.5 shrink-0 rounded-full", running ? "animate-pulse bg-success-primary" : "bg-layer-3")}
        />
        <Avatar alt={row.user.display_name} src={getFileURL(row.user.avatar_url ?? "")} />
        <span className="text-sm w-40 shrink-0 truncate font-medium text-primary">
          {row.user.display_name}
          {row.is_me && <span className="font-normal ml-1 text-tertiary">(you)</span>}
        </span>
        <div className="text-sm min-w-0 flex-1">{workCell}</div>
        <span
          data-testid="live-row-elapsed"
          className="font-mono w-20 shrink-0 text-right text-14 text-primary tabular-nums"
        >
          {running ? formatElapsed(getRowElapsedSeconds(row, nowMs, serverOffsetMs)) : ""}
        </span>
        <span data-testid="live-row-today" className="text-sm w-24 shrink-0 text-right text-secondary">
          {formatLoggedMinutes(getRowTodayMinutes(row, nowMs, fetchedAtMs))} today
        </span>
      </div>
      {row.range && segments.length > 0 && (
        <div className="flex items-center gap-3 pl-6" data-testid="live-row-range">
          <div className="flex h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-layer-2">
            {segments.map((s) => (
              <div
                key={s.key}
                title={`${s.label}: ${formatLoggedMinutes(s.minutes)}`}
                style={{ width: `${s.percent}%`, backgroundColor: s.color }}
              />
            ))}
          </div>
          <span className="text-xs w-24 shrink-0 text-right text-tertiary">
            {formatLoggedMinutes(row.range.total_minutes)}
          </span>
        </div>
      )}
    </div>
  );
}
