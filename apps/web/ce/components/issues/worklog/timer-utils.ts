/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking — pure helpers shared by the worklog sidebar widget and the
// always-visible running-timer indicator (PSR-101 / PSR-105). Kept free of React so
// they can be unit tested.
import type { TStoppedTimer } from "./types";

/** 90 -> "1h 30m", 120 -> "2h", 25 -> "25m". */
export function formatLoggedMinutes(total: number): string {
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

/** 65 -> "01:05", 3725 -> "1:02:05". */
export function formatElapsed(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * Milliseconds to add to the browser clock to get server time. Lets elapsed time be
 * computed from the server's `started_at` without being skewed by a wrong client clock.
 * Returns 0 when the server time is missing or unparsable.
 */
export function getServerOffsetMs(serverNow: string | undefined, clientNowMs: number): number {
  if (!serverNow) return 0;
  const server = new Date(serverNow).getTime();
  return Number.isNaN(server) ? 0 : server - clientNowMs;
}

/** Whole seconds a timer has been running; never negative. */
export function getElapsedSeconds(startedAt: string | undefined, nowMs: number, serverOffsetMs = 0): number {
  if (!startedAt) return 0;
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started)) return 0;
  return Math.max(0, Math.floor((nowMs + serverOffsetMs - started) / 1000));
}

/** Toast shown when starting a timer stopped another one: "Stopped timer on PSR-12 (25m logged)". */
export function getAutoStopToastTitle(stopped: TStoppedTimer | undefined): string | null {
  if (!stopped) return null;
  const logged = `${formatLoggedMinutes(stopped.duration)} logged`;
  return stopped.issue ? `Stopped timer on ${stopped.issue.ref} (${logged})` : `Stopped the previous timer (${logged})`;
}
