/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — the "Still working?" check, hosted once per workspace page so it runs
// in every open tab. Behaviour (PO decision, DESIGN §8.2): the server picks a random check time
// (20-80 min by default) and never announces it; once `timer.check` is set a prompt appears and
// a chime repeats every 5 s until the deadline or an answer; a browser notification fires too; if
// audio is blocked the tab title flashes instead. "I'm still here" confirms; no answer by the
// deadline and the server stops the timer, and a card offers "Start again".
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useParams } from "next/navigation";
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
import { setToast } from "@plane/blocks/toast";
import { useRunningTimer } from "@/plane-web/hooks/use-running-timer";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { formatElapsed } from "../../issues/worklog/timer-utils";
import { createChimeScheduler, playChime, primeAudioOnFirstGesture } from "./chime";
import { describeTimerError, isStoppedByServer } from "./errors";
import { getPromptRemainingMs, initialIdleState, reduceIdle } from "./idle-check";
import { closeIdleNotification, showIdleNotification } from "./notify";
import { refreshTimeData, startTimerOnItem, stopRunningTimer } from "./timer-actions";
import { startTitleFlash } from "./title-flash";

type TChannelMessage = { type: "answered"; checkAt: string } | { type: "stopped" };

export function IdleCheckHost() {
  const { workspaceSlug } = useParams();
  const slug = workspaceSlug?.toString();
  const { timer, serverOffsetMs, mutate } = useRunningTimer(slug);
  const [state, dispatch] = useReducer(reduceIdle, initialIdleState);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const channelRef = useRef<BroadcastChannel | null>(null);

  // Latest values for the 1 s ticker without restarting it on every poll.
  const latest = useRef({ timer, serverOffsetMs });
  latest.current = { timer, serverOffsetMs };

  useEffect(() => primeAudioOnFirstGesture(), []);

  // Cross-tab: an answer or a stop in one tab closes the prompt (and chime) in the others.
  useEffect(() => {
    if (!slug || typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(`plane-time-idle:${slug}`);
    channelRef.current = channel;
    channel.addEventListener("message", (event: MessageEvent<TChannelMessage>) => {
      const msg = event.data;
      if (msg?.type === "answered") dispatch({ type: "answered", checkAt: msg.checkAt });
      else if (msg?.type === "stopped") dispatch({ type: "cleared" });
      void mutate();
    });
    return () => {
      channel.close();
      channelRef.current = null;
    };
  }, [slug, mutate]);

  const post = (msg: TChannelMessage) => {
    try {
      channelRef.current?.postMessage(msg);
    } catch {
      // other tabs fall back to their next poll
    }
  };

  // Drive the state machine from the server clock, once a second while a timer or prompt exists.
  useEffect(() => {
    const tick = () => {
      const { timer: t, serverOffsetMs: off } = latest.current;
      const now = Date.now();
      if (state.kind === "prompt") setNowMs(now);
      dispatch({ type: "tick", timer: t, serverNowMs: now + off });
    };
    tick();
    if (!timer?.id && state.kind === "none") return;
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timer, serverOffsetMs, state.kind]);

  const promptCheckAt = state.kind === "prompt" ? state.checkAt : null;
  const promptRef = state.kind === "prompt" ? state.item.ref?.ref : undefined;
  const promptName = state.kind === "prompt" ? state.item.ref?.name : undefined;
  const promptDeadline = state.kind === "prompt" ? state.deadlineAt : null;

  // While the prompt is open: chime every 5 s, notify, and flash the title if audio is blocked.
  useEffect(() => {
    if (!promptCheckAt || !promptDeadline) return;
    const durationMs = Math.max(0, new Date(promptDeadline).getTime() - (Date.now() + latest.current.serverOffsetMs));
    let stopFlash: (() => void) | null = null;
    const chime = createChimeScheduler({
      play: playChime,
      durationMs,
      onBlocked: () => {
        stopFlash = startTitleFlash();
      },
    });
    chime.start();
    showIdleNotification(
      promptRef ? `${promptRef} ${promptName ?? ""}`.trim() : "Your timer is still running",
      () => {}
    );
    return () => {
      chime.stop();
      stopFlash?.();
      closeIdleNotification();
    };
  }, [promptCheckAt, promptDeadline, promptRef, promptName]);

  const handleConfirm = useCallback(async () => {
    if (!slug || state.kind !== "prompt" || busy) return;
    const { checkAt } = state;
    setBusy(true);
    try {
      await timeTrackingService.confirmTimer(slug);
      dispatch({ type: "answered", checkAt });
      post({ type: "answered", checkAt });
      await refreshTimeData(slug);
    } catch (err) {
      if (isStoppedByServer(err)) {
        // Answered after the deadline: the server already stopped the timer.
        dispatch({ type: "expired" });
        post({ type: "stopped" });
        setToast({ type: "info", title: "Timer stopped", message: "You didn't confirm in time." });
      } else {
        const info = describeTimerError(err, "Cannot confirm");
        setToast({ type: "error", title: info.title, message: info.message });
      }
      await refreshTimeData(slug);
    } finally {
      setBusy(false);
    }
  }, [slug, state, busy]);

  const handleStop = useCallback(async () => {
    if (!slug || state.kind !== "prompt" || busy) return;
    setBusy(true);
    const ok = await stopRunningTimer(slug, state.item.ref?.ref);
    if (ok) {
      dispatch({ type: "cleared" });
      post({ type: "stopped" });
    }
    setBusy(false);
  }, [slug, state, busy]);

  const handleResume = useCallback(async () => {
    if (!slug || state.kind !== "stopped" || busy) return;
    const { issueId } = state.item;
    setBusy(true);
    // The timer may still be on the server for a moment (sweep runs each minute): stop it first —
    // the server clamps the cutoff to the prompt time — then start fresh. A 404 just means it is gone.
    try {
      await timeTrackingService.stopWorkspaceTimer(slug);
    } catch {
      // already stopped
    }
    const started = await startTimerOnItem(slug, issueId);
    if (started) {
      dispatch({ type: "cleared" });
      post({ type: "stopped" });
    }
    setBusy(false);
  }, [slug, state, busy]);

  if (!slug) return null;

  if (state.kind === "prompt") {
    const remaining = Math.ceil(getPromptRemainingMs(state, nowMs + serverOffsetMs) / 1000);
    return (
      <Dialog open onOpenChange={() => {}}>
        {/* Esc / outside click must not silently stop the timer: only the two buttons answer. */}
        <DialogContent size="sm" data-prevent-outside-click data-testid="idle-check-dialog">
          <DialogMain>
            <DialogHeader>
              <DialogHeading>
                <DialogTitle>Still working?</DialogTitle>
              </DialogHeading>
            </DialogHeader>
            <DialogBody tabIndex={0}>
              <p className="text-14 text-secondary">
                Your timer is running on{" "}
                <span className="font-medium text-primary">
                  {state.item.ref ? `${state.item.ref.ref} ${state.item.ref.name}` : "a work item"}
                </span>
                . Confirm you're still on it, or the timer stops.
              </p>
              <p className="mt-2 text-13 text-tertiary" data-testid="idle-check-countdown">
                Stops automatically in {formatElapsed(remaining)} if there is no answer.
              </p>
            </DialogBody>
          </DialogMain>
          <DialogActions>
            <Button variant="secondary" size="md" stretch="auto" label="Stop" onClick={handleStop} disabled={busy} />
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label="I'm still here"
              onClick={handleConfirm}
              loading={busy}
              data-testid="idle-check-yes"
            />
          </DialogActions>
        </DialogContent>
      </Dialog>
    );
  }

  if (state.kind === "stopped") {
    return (
      <div
        role="alert"
        data-testid="idle-check-stopped"
        className="shadow-lg fixed right-4 bottom-4 z-50 flex max-w-sm flex-col gap-3 rounded-lg border border-subtle bg-surface-1 p-4"
      >
        <div className="text-14">
          <p className="font-medium text-primary">Timer stopped — you didn't confirm.</p>
          {state.item.ref && (
            <p className="mt-1 text-13 text-tertiary">
              {state.item.ref.ref} {state.item.ref.name}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label="Dismiss"
            onClick={() => dispatch({ type: "cleared" })}
            disabled={busy}
          />
          <Button
            variant="primary"
            size="sm"
            stretch="auto"
            label="Start again"
            onClick={handleResume}
            loading={busy}
            data-testid="idle-check-resume"
          />
        </div>
      </div>
    );
  }

  return null;
}
