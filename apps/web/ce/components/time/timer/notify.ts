/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 — browser Notification for the "Still working?" check. Permission is
// asked once, at timer start (a user gesture, as browsers require), and never again if refused.
const ASKED_KEY = "plane-time-notify-asked";
const NOTIFICATION_TAG = "plane-time-still-working";

const supported = () => typeof window !== "undefined" && "Notification" in window;

export function requestNotificationPermissionOnce(): void {
  if (!supported() || Notification.permission !== "default") return;
  try {
    if (window.localStorage.getItem(ASKED_KEY)) return;
    window.localStorage.setItem(ASKED_KEY, "1");
  } catch {
    // storage unavailable: still ask once per page load via the permission state itself
  }
  try {
    void Notification.requestPermission();
  } catch {
    // older browsers use a callback form; nothing to do
  }
}

let current: Notification | null = null;

export function showIdleNotification(body: string, onClick: () => void): void {
  if (!supported() || Notification.permission !== "granted") return;
  closeIdleNotification();
  try {
    // Fixed tag: the same prompt in several tabs collapses into one notification.
    current = new Notification("Still working?", { body, tag: NOTIFICATION_TAG, requireInteraction: true });
    current.addEventListener("click", () => {
      window.focus();
      onClick();
    });
  } catch {
    current = null;
  }
}

export function closeIdleNotification(): void {
  try {
    current?.close();
  } catch {
    // already closed
  }
  current = null;
}
