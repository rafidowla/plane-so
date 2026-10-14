/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-105 — tells the top-bar timer indicator whether the sidebar footer
// indicator is currently mounted, so exactly one of the two is shown (the footer
// when the sidebar is open, the top bar when the sidebar is collapsed or absent,
// e.g. on settings and notifications pages).
import { useEffect, useSyncExternalStore } from "react";

let mountedCount = 0;
const listeners = new Set<() => void>();

const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Call from the sidebar footer indicator while it is mounted. */
export function useRegisterTimerFooter() {
  useEffect(() => {
    mountedCount += 1;
    emit();
    return () => {
      mountedCount -= 1;
      emit();
    };
  }, []);
}

/** True while at least one sidebar footer indicator is mounted. */
export function useIsTimerFooterMounted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => mountedCount > 0,
    () => false
  );
}
