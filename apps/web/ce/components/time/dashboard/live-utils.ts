/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — pure view-model helpers for the live dashboard. Everything the
// row shows is derived from the payload alone: the server already redacted other people's
// work (`timer.private`), so nothing here may look a work item up client-side.
import { getElapsedSeconds } from "../../issues/worklog/timer-utils";
import type { TLiveRangeProject, TLiveRow } from "../../issues/worklog/types";

export const PRIVATE_PROJECT_LABEL = "private project";

export type TLiveRowWork =
  | { kind: "idle" }
  | { kind: "private"; label: string }
  | { kind: "item"; ref: string; name: string; projectName: string; projectIdentifier: string; issueId: string };

/**
 * What to show in the "working on" cell. A private timer yields ONLY the label — no ref,
 * name or project — even if a future payload happened to carry more.
 */
export function getLiveRowWork(row: TLiveRow): TLiveRowWork {
  const timer = row.status === "running" ? row.timer : null;
  if (!timer) return { kind: "idle" };
  if (timer.private || !timer.issue || !timer.project) {
    return { kind: "private", label: timer.label || PRIVATE_PROJECT_LABEL };
  }
  return {
    kind: "item",
    ref: timer.issue.ref,
    name: timer.issue.name,
    projectName: timer.project.name,
    projectIdentifier: timer.project.identifier,
    issueId: timer.issue.id,
  };
}

/** Whole seconds a row's timer has been running; 0 for a stopped row. */
export function getRowElapsedSeconds(row: TLiveRow, nowMs: number, serverOffsetMs: number): number {
  if (row.status !== "running" || !row.timer) return 0;
  return getElapsedSeconds(row.timer.started_at, nowMs, serverOffsetMs);
}

/**
 * Today's total for a row. The payload's `today_minutes` already includes the running part as
 * of the response; a running row keeps counting from there (monotonic client time since the
 * response arrived, so a skewed client clock cannot distort it).
 */
export function getRowTodayMinutes(row: TLiveRow, nowMs: number, fetchedAtMs: number): number {
  if (row.status !== "running") return row.today_minutes;
  const sinceFetchMin = Math.max(0, Math.floor((nowMs - fetchedAtMs) / 60_000));
  return row.today_minutes + sinceFetchMin;
}

/** "5m ago", "2h ago", "3d ago"; empty for a missing timestamp. */
export function formatAgo(iso: string | null, nowMs: number, serverOffsetMs: number): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((nowMs + serverOffsetMs - t) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const PALETTE = ["#3f76ff", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#db2777", "#65a30d", "#ea580c"];

/** The project's own icon colour when it has one, else a stable colour derived from its id. */
export function getProjectColor(project: Pick<TLiveRangeProject, "id" | "logo_props">): string {
  const icon = (project.logo_props as { icon?: { color?: string } } | null | undefined)?.icon;
  if (icon?.color && /^#[0-9a-fA-F]{3,8}$/.test(icon.color)) return icon.color;
  let hash = 0;
  for (let i = 0; i < project.id.length; i += 1) hash = (hash * 31 + project.id.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

export type TRangeSegment = { key: string; label: string; minutes: number; percent: number; color: string };

/** Stacked-bar segments for a row's range; empty when there is nothing to draw. */
export function getRangeSegments(range: NonNullable<TLiveRow["range"]>): TRangeSegment[] {
  const total = range.total_minutes;
  if (total <= 0) return [];
  const segments: TRangeSegment[] = range.projects
    .filter((p) => p.minutes > 0)
    .map((p) => ({
      key: p.id,
      label: p.name,
      minutes: p.minutes,
      percent: (p.minutes / total) * 100,
      color: getProjectColor(p),
    }));
  if (range.other_projects.minutes > 0) {
    segments.push({
      key: "other",
      label: range.other_projects.label,
      minutes: range.other_projects.minutes,
      percent: (range.other_projects.minutes / total) * 100,
      color: "#9ca3af",
    });
  }
  return segments;
}
