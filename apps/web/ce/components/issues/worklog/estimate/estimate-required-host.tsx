/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S6 — mounted once per workspace page. Shows "Add an estimate first" whenever any
// call (state move, timer start, manual entry, missing-time request) is refused with `estimate_required`.
// When the work item's estimate field is on screen it opens itself; otherwise (board, list, timer bar) the
// toast links to the work item so the estimate can be added there.
import { useEffect, useRef } from "react";
import { setToast } from "@plane/blocks/toast";
import { generateWorkItemLink } from "@plane/utils";
import { useAppRouter } from "@/hooks/use-app-router";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import {
  ESTIMATE_REQUIRED_MESSAGE,
  ESTIMATE_REQUIRED_TITLE,
  isEstimateFieldMounted,
  subscribeEstimateRequired,
} from "./estimate-required";

export function EstimateRequiredHost() {
  const router = useAppRouter();
  const { getProjectById } = useProject();
  const {
    issue: { getIssueById },
  } = useIssueDetail();

  // The subscription lives for the whole mount; read the stores through a ref so it never goes stale.
  const latest = useRef({ router, getProjectById, getIssueById });
  latest.current = { router, getProjectById, getIssueById };

  useEffect(
    () =>
      subscribeEstimateRequired((detail) => {
        const { router: r, getProjectById: projectById, getIssueById: issueById } = latest.current;
        const issueId = detail.issueId ?? null;
        const onScreen = isEstimateFieldMounted(issueId);
        const issue = issueId ? issueById(issueId) : undefined;
        const project = issue?.project_id ? projectById(issue.project_id) : undefined;
        const href =
          !onScreen && issue && project && detail.workspaceSlug
            ? generateWorkItemLink({
                workspaceSlug: detail.workspaceSlug,
                projectId: project.id,
                issueId: issue.id,
                projectIdentifier: project.identifier,
                sequenceId: issue.sequence_id,
              })
            : null;
        setToast({
          id: `estimate-required-${issueId ?? "x"}`,
          type: "warning",
          title: ESTIMATE_REQUIRED_TITLE,
          message: onScreen
            ? "Set the estimate in the Time tracking section, then try again."
            : ESTIMATE_REQUIRED_MESSAGE,
          ...(href ? { actionItems: [{ label: "Open work item", primary: true, onClick: () => r.push(href) }] } : {}),
        });
      }),
    []
  );

  return null;
}
