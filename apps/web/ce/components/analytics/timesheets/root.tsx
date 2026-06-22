/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import AnalyticsWrapper from "@/components/analytics/analytics-wrapper";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TTimesheetStatus } from "@/plane-web/components/issues/worklog/types";

const STATUS_STYLES: Record<TTimesheetStatus, string> = {
  open: "bg-surface-2 text-tertiary",
  submitted: "bg-amber-500/15 text-amber-600",
  approved: "bg-green-500/15 text-green-600",
  rejected: "bg-red-500/15 text-red-600",
};

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export const Timesheets = observer(function Timesheets() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { allowPermissions } = useUserPermissions();
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const [periodStart, setPeriodStart] = useState(daysAgo(6));
  const [periodEnd, setPeriodEnd] = useState(today());

  const { data, isLoading, mutate } = useSWR(
    workspaceSlug ? `TIMESHEETS_${workspaceSlug}` : null,
    workspaceSlug ? () => timeTrackingService.getTimesheets(workspaceSlug) : null
  );
  const timesheets = data ?? [];

  const handleSubmit = async () => {
    try {
      await timeTrackingService.submitTimesheet(workspaceSlug, periodStart, periodEnd);
      setToast({ type: TOAST_TYPE.SUCCESS, title: "Submitted", message: "Timesheet submitted for approval." });
      mutate();
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: err?.error ?? "Could not submit." });
    }
  };

  const handleReview = async (id: string, action: "approve" | "reject") => {
    try {
      await timeTrackingService.reviewTimesheet(workspaceSlug, id, action);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: action === "approve" ? "Approved" : "Rejected",
        message: `Timesheet ${action === "approve" ? "approved and locked" : "rejected"}.`,
      });
      mutate();
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Error", message: err?.error ?? "Could not update." });
    }
  };

  return (
    <AnalyticsWrapper i18nTitle="">
      <h1 className="mb-4 text-20 font-bold">Timesheets</h1>

      <div className="mb-5 flex flex-wrap items-end gap-3 rounded-md border border-subtle p-3">
        <div>
          <span className="text-xs mb-1 block text-tertiary">Week start</span>
          <input
            type="date"
            value={periodStart}
            onChange={(e) => setPeriodStart(e.target.value)}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
          />
        </div>
        <div>
          <span className="text-xs mb-1 block text-tertiary">Week end</span>
          <input
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
          />
        </div>
        <Button variant="primary" size="sm" onClick={handleSubmit}>
          Submit my timesheet
        </Button>
      </div>

      {isLoading ? (
        <Loader className="space-y-3">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : timesheets.length === 0 ? (
        <div className="text-sm py-12 text-center text-tertiary">No timesheets yet.</div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-subtle">
          <table className="text-sm w-full">
            <thead className="bg-surface-2 text-left text-tertiary">
              <tr>
                <th className="px-4 py-2 font-medium">Resource</th>
                <th className="px-4 py-2 font-medium">Period</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Reviewed by</th>
                {isAdmin && <th className="px-4 py-2 text-right font-medium">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {timesheets.map((ts) => (
                <tr key={ts.id} className="border-t border-subtle">
                  <td className="px-4 py-2">{ts.user_detail?.display_name ?? "—"}</td>
                  <td className="px-4 py-2">
                    {ts.period_start} → {ts.period_end}
                  </td>
                  <td className="px-4 py-2">
                    <span className={cn("text-xs rounded px-2 py-0.5 capitalize", STATUS_STYLES[ts.status])}>
                      {ts.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-tertiary">{ts.reviewed_by_detail?.display_name ?? "—"}</td>
                  {isAdmin && (
                    <td className="px-4 py-2 text-right">
                      {ts.status === "submitted" ? (
                        <div className="flex justify-end gap-2">
                          <Button variant="primary" size="sm" onClick={() => handleReview(ts.id, "approve")}>
                            Approve
                          </Button>
                          <Button variant="secondary" size="sm" onClick={() => handleReview(ts.id, "reject")}>
                            Reject
                          </Button>
                        </div>
                      ) : (
                        <span className="text-tertiary">—</span>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AnalyticsWrapper>
  );
});
