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
 * default option. User edits are never overwritten: a property prefills only
 * once per project selection, and staged values (e.g. "create more") win over
 * prefills. Every change is also remembered as the new last-used value.
 */
export const CreatePropertyInputRows = observer(function CreatePropertyInputRows(props: Props) {
  const { workspaceSlug, projectId, disabled } = props;
  const { enabled, properties } = useProjectCustomProperties(workspaceSlug, projectId);
  const { issuePropertyValues, setIssuePropertyValues } = useIssueModal();
  const { data: currentUser } = useUser();
  // properties that already had their one-time prefill for this project selection
  const prefilledRef = useRef<Set<string>>(new Set());

  // Switching projects clears staging (property ids are project-scoped) and
  // re-arms the prefill for the new project's properties.
  useEffect(() => {
    prefilledRef.current = new Set();
    setIssuePropertyValues({});
  }, [projectId, setIssuePropertyValues]);

  // One-time prefill per property: last-used → default. Staged values win so a
  // "create more" round keeps what the user picked last time.
  useEffect(() => {
    if (!enabled || properties.length === 0) return;
    setIssuePropertyValues((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const property of properties) {
        if (prefilledRef.current.has(property.id)) continue;
        prefilledRef.current.add(property.id);
        if (next[property.id] !== undefined) continue;
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
