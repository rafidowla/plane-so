/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: custom-properties — create-time staging with default/last-used prefill (improvement #19)
import { useEffect, useRef } from "react";
import { observer } from "mobx-react";
// hooks
import { useIssueModal } from "@/hooks/context/use-issue-modal";
import { useUser } from "@/hooks/store/user/user-user";
// local imports
import { useProjectCustomProperties } from "@/plane-web/custom-properties";
import { getPrefillPropertyValue, setLastUsedPropertyValue } from "../utils/default-values";
import { PROPERTY_INPUT_REGISTRY } from "./cells/registry";

type Props = {
  workspaceSlug: string;
  projectId: string;
  disabled: boolean;
};

/**
 * Create-mode counterpart to PropertyInputRows: there is no work item yet, so
 * values are staged on the IssueModalContext and saved by the provider's
 * `handleCreateUpdatePropertyValues` once the item exists.
 *
 * Each property prefills from the user's last-used value, else the configured
 * default option. User edits are never overwritten: manual changes mark the
 * property as touched, and staged values (e.g. "create more") win over
 * prefills. Every change is also remembered as the new last-used value.
 */
export const CreatePropertyInputRows = observer(function CreatePropertyInputRows(props: Props) {
  const { workspaceSlug, projectId, disabled } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId);
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
    if (!enabled || properties.length === 0) return;
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
  }, [enabled, properties, projectId, currentUser?.id, setIssuePropertyValues]);

  if (!enabled || properties.length === 0) return null;

  return (
    <>
      {properties.map((property) => {
        const Input = PROPERTY_INPUT_REGISTRY[property.property_type];
        if (!Input) return null;
        const staged = issuePropertyValues[property.id];
        const values = Array.isArray(staged) ? (staged as string[]) : [];
        return (
          <div key={property.id} className="flex min-h-8 items-center gap-2">
            <span className="text-sm w-2/5 flex-shrink-0 truncate text-tertiary">
              {property.display_name || property.name}
            </span>
            <div className="w-3/5">
              <Input
                property={property}
                values={values}
                disabled={disabled}
                onChange={async (next) => {
                  userTouchedRef.current.add(property.id);
                  setIssuePropertyValues((prev) => ({ ...prev, [property.id]: next }));
                  setLastUsedPropertyValue(currentUser?.id, projectId, property.id, next);
                }}
              />
            </div>
          </div>
        );
      })}
    </>
  );
});
