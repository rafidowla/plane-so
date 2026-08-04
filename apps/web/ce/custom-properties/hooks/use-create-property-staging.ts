/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — create-time staging with default/last-used prefill (improvements #19, #20)
import { useEffect, useRef } from "react";
// hooks
import { useIssueModal } from "@/hooks/context/use-issue-modal";
import { useUser } from "@/hooks/store/user/user-user";
// local imports
import type { TIssueProperty } from "../types";
import { getPrefillPropertyValue, setLastUsedPropertyValue } from "../utils/default-values";

/**
 * Create-mode staging for a given set of custom properties: there is no work
 * item yet, so values are staged on the IssueModalContext and saved by the
 * provider's `handleCreateUpdatePropertyValues` once the item exists.
 *
 * Extracted from CreatePropertyInputRows so the promoted Task Type header
 * chip (improvement #20) and the Options rows share the exact same prefill
 * and touched-tracking behavior. Each instance manages a disjoint set of
 * properties, so running two instances (header + Options) is safe.
 *
 * Each property prefills from the user's last-used value, else the configured
 * default option. User edits are never overwritten: manual changes mark the
 * property as touched, and staged values (e.g. "create more") win over
 * prefills. Every change is also remembered as the new last-used value.
 */
export const useCreatePropertyStaging = (projectId: string, properties: TIssueProperty[]) => {
  const { issuePropertyValues, setIssuePropertyValues } = useIssueModal();
  const { data: currentUser } = useUser();
  // last project the staging was cleared for, and properties the user edited
  // by hand (manual edits are never overwritten by a prefill)
  const clearedForProjectRef = useRef<string | null>(null);
  const userTouchedRef = useRef<Set<string>>(new Set());

  // Switching projects clears staging (property ids are project-scoped). The
  // ref guard makes repeat invocations (re-mounts, effect re-runs) no-ops so a
  // later stray run can't wipe an already-applied prefill.
  useEffect(() => {
    if (clearedForProjectRef.current === projectId) return;
    clearedForProjectRef.current = projectId;
    userTouchedRef.current = new Set();
    setIssuePropertyValues({});
  }, [projectId, setIssuePropertyValues]);

  // Prefill every untouched, still-empty property: last-used → default. Not
  // one-shot: if staging is reset underneath us (modal lifecycle), the next
  // run re-applies the prefill. Staged values win so a "create more" round
  // keeps what the user picked last time.
  useEffect(() => {
    if (properties.length === 0) return;
    setIssuePropertyValues((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const property of properties) {
        if (userTouchedRef.current.has(property.id)) continue;
        const stagedValue = next[property.id];
        if (Array.isArray(stagedValue) && stagedValue.length > 0) continue;
        const prefill = getPrefillPropertyValue(currentUser?.id, projectId, property);
        if (prefill.length > 0) {
          next[property.id] = prefill;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [properties, projectId, currentUser?.id, setIssuePropertyValues]);

  const getStagedValues = (propertyId: string): string[] => {
    const staged = issuePropertyValues[propertyId];
    return Array.isArray(staged) ? (staged as string[]) : [];
  };

  const handleChange = (propertyId: string, next: string[]) => {
    userTouchedRef.current.add(propertyId);
    setIssuePropertyValues((prev) => ({ ...prev, [propertyId]: next }));
    setLastUsedPropertyValue(currentUser?.id, projectId, propertyId, next);
  };

  return { getStagedValues, handleChange };
};
