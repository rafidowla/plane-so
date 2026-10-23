/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — the "Still working?" chime. The scheduler is plain TypeScript with
// injectable timers so the 5 s x 5 min cadence is unit-testable; the sound itself is generated
// with the Web Audio API (no audio file).

/** How often the chime repeats while the "Still working?" prompt is open (PO decision, DESIGN §8.2). */
export const CHIME_INTERVAL_MS = 5_000;

type TSchedulerOptions = {
  /** Plays one chime. Return false when audio is blocked (autoplay policy). */
  play: () => boolean;
  /** Total time the chime keeps repeating (the prompt window: deadline - now). */
  durationMs: number;
  intervalMs?: number;
  /** Called once, the first time `play` reports that audio is blocked. */
  onBlocked?: () => void;
  now?: () => number;
  setIntervalFn?: (fn: () => void, ms: number) => unknown;
  clearIntervalFn?: (handle: unknown) => void;
};

export type TChimeScheduler = { start: () => void; stop: () => void; isRunning: () => boolean };

/** Chimes immediately, then every `intervalMs` until `durationMs` has elapsed or `stop()` is called. */
export function createChimeScheduler(options: TSchedulerOptions): TChimeScheduler {
  const {
    play,
    durationMs,
    intervalMs = CHIME_INTERVAL_MS,
    onBlocked,
    now = () => Date.now(),
    setIntervalFn = (fn, ms) => setInterval(fn, ms),
    clearIntervalFn = (h) => clearInterval(h as ReturnType<typeof setInterval>),
  } = options;
  let handle: unknown = null;
  let startedAt = 0;
  let blockedReported = false;

  const stop = () => {
    if (handle !== null) clearIntervalFn(handle);
    handle = null;
  };

  const tick = () => {
    if (now() - startedAt >= durationMs) {
      stop();
      return;
    }
    let ok = false;
    try {
      ok = play();
    } catch {
      ok = false;
    }
    if (!ok && !blockedReported) {
      blockedReported = true;
      onBlocked?.();
    }
  };

  return {
    start: () => {
      if (handle !== null) return;
      startedAt = now();
      blockedReported = false;
      handle = setIntervalFn(tick, intervalMs);
      tick();
    },
    stop,
    isRunning: () => handle !== null,
  };
}

type TAudioWindow = {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
};

let audioContext: AudioContext | null = null;
let primed = false;

function getAudioContext(): AudioContext | null {
  if (audioContext) return audioContext;
  if (typeof window === "undefined") return null;
  const Ctor =
    (window as unknown as TAudioWindow).AudioContext ?? (window as unknown as TAudioWindow).webkitAudioContext;
  if (!Ctor) return null;
  try {
    audioContext = new Ctor();
  } catch {
    audioContext = null;
  }
  return audioContext;
}

/**
 * Browsers only let a page make sound after the user has interacted with it. Call once at app
 * start: the first click/keypress in this tab creates and resumes the audio context, so a chime
 * later (with no gesture at that moment) can still play. Returns a cleanup function.
 */
export function primeAudioOnFirstGesture(): () => void {
  if (typeof window === "undefined" || primed) return () => {};
  const events = ["pointerdown", "keydown", "touchstart"] as const;
  const prime = () => {
    const ctx = getAudioContext();
    if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => {});
    primed = true;
    cleanup();
  };
  const cleanup = () => events.forEach((e) => window.removeEventListener(e, prime, true));
  events.forEach((e) => window.addEventListener(e, prime, { capture: true, passive: true }));
  return cleanup;
}

/**
 * Plays one short two-note chime. Returns false when the browser blocks audio (no prior gesture
 * in this tab, or the context is still suspended) — the caller then falls back to the title flash.
 */
export function playChime(): boolean {
  const ctx = getAudioContext();
  if (!ctx) return false;
  if (ctx.state !== "running") {
    // Try to wake it for next time; this tick counts as blocked.
    void ctx.resume().catch(() => {});
    return false;
  }
  const start = ctx.currentTime;
  [
    { freq: 880, at: 0 },
    { freq: 1318.5, at: 0.18 },
  ].forEach(({ freq, at }) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, start + at);
    gain.gain.exponentialRampToValueAtTime(0.25, start + at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + at + 0.5);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(start + at);
    osc.stop(start + at + 0.55);
  });
  return true;
}
