/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import Link from "next/link";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Loader } from "@plane/ui";
import { generateWorkItemLink } from "@plane/utils";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { hrs } from "./root";

type Props = {
  workspaceSlug: string;
  userIds?: string[];
  startDate: string;
  endDate: string;
};

export const TaskBreakdown = observer(function TaskBreakdown({ workspaceSlug, userIds, startDate, endDate }: Props) {
  const params = {
    group_by: "issue",
    start_date: startDate,
    end_date: endDate,
    ...(userIds && userIds.length > 0 ? { user_ids: userIds.join(",") } : {}),
  };
  const { data, isLoading } = useSWR(
    workspaceSlug ? `TIME_REPORT_TASKS_${workspaceSlug}_${JSON.stringify(params)}` : null,
    workspaceSlug ? () => timeTrackingService.getTimeReport(workspaceSlug, params) : null
  );

  const groups = data?.groups ?? [];

  if (isLoading) {
    return (
      <Loader className="space-y-2 px-4 py-2">
        <Loader.Item height="24px" />
        <Loader.Item height="24px" />
      </Loader>
    );
  }

  if (groups.length === 0) {
    return <div className="text-sm px-4 py-3 text-tertiary">No task-level time logged in this range.</div>;
  }

  return (
    <div className="overflow-x-auto">
      {data?.truncated && (
        <div className="text-xs px-4 py-2 text-tertiary">
          Showing the top {data.limit} tasks by time logged. Export CSV for the full list.
        </div>
      )}
      <table className="text-sm w-full">
        <thead className="bg-surface-2 text-left text-tertiary">
          <tr>
            <th className="px-4 py-2 font-medium">Task</th>
            <th className="px-4 py-2 font-medium">Project</th>
            <th className="px-4 py-2 text-right font-medium">Total</th>
            <th className="px-4 py-2 text-right font-medium">Billable</th>
            <th className="px-4 py-2 text-right font-medium">Entries</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const workItemLink = generateWorkItemLink({
              workspaceSlug,
              projectId: g.project_id,
              issueId: g.issue_id,
              projectIdentifier: g.project_identifier,
              sequenceId: g.sequence_id,
            });
            return (
              <tr key={g.key ?? g.name} className="border-t border-subtle">
                <td className="px-4 py-2">
                  {g.issue_id ? (
                    <Link href={workItemLink} className="hover:underline">
                      {g.name}
                    </Link>
                  ) : (
                    g.name
                  )}
                </td>
                <td className="px-4 py-2 text-tertiary">{g.project_name ?? "—"}</td>
                <td className="px-4 py-2 text-right">{hrs(g.total_minutes)}</td>
                <td className="px-4 py-2 text-right">{hrs(g.billable_minutes)}</td>
                <td className="px-4 py-2 text-right">{g.entry_count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
});
