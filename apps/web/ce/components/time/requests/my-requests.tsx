/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S3b — "My requests": everything you asked your approver for, with Withdraw on
// pending ones and a way to start a missing-time request. (DESIGN §5 puts this in the My timesheet area;
// that tab does not exist yet, so it is its own Time tab until S4 adds one.)
import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Loader } from "@plane/blocks/skeleton";
import { setToast } from "@plane/blocks/toast";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import { MY_REQUESTS_KEY, refreshRequestData } from "./request-keys";
import { RequestModal } from "./request-modal";
import { RequestRow } from "./request-row";
import { describeRequestError, safeTimeZone } from "./request-utils";

const PAGE = 50;
const MAX = 200;

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
];

export const MyRequests = observer(function MyRequests() {
  const { currentWorkspace } = useWorkspace();
  const { allowPermissions } = useUserPermissions();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const timeZone = safeTimeZone(currentWorkspace?.timezone);
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [modalOpen, setModalOpen] = useState(false);
  const [withdrawing, setWithdrawing] = useState<string | null>(null);

  // Workspace admins change time directly, so "request" buttons are for everyone else.
  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const { data, isLoading } = useSWR(
    workspaceSlug ? `${MY_REQUESTS_KEY(workspaceSlug)}_${status}_${limit}` : null,
    () => timeTrackingService.getTimeRequests(workspaceSlug, { scope: "mine", status: status || undefined, limit }),
    { keepPreviousData: true }
  );
  const rows = data?.results ?? [];

  const withdraw = async (id: string) => {
    setWithdrawing(id);
    try {
      await timeTrackingService.withdrawTimeRequest(workspaceSlug, id);
      setToast({ type: "success", title: "Request withdrawn" });
    } catch (err) {
      const info = describeRequestError(err, timeZone, "Could not withdraw");
      setToast({ type: "error", title: info.title, message: info.message });
    } finally {
      setWithdrawing(null);
      await refreshRequestData(workspaceSlug);
    }
  };

  if (!workspaceSlug) return null;

  return (
    <div className="flex flex-col gap-3 px-4 py-4" data-testid="my-requests">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-16 font-semibold text-primary">My requests</h2>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <select
            aria-label="Status"
            data-testid="my-requests-status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setLimit(PAGE);
            }}
            className="text-sm rounded border border-subtle bg-transparent px-2 py-1.5"
          >
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          {!isWorkspaceAdmin && (
            <Button
              variant="primary"
              size="md"
              stretch="auto"
              label="Request missing time"
              data-testid="request-missing-time"
              onClick={() => setModalOpen(true)}
            />
          )}
        </div>
      </div>

      {isLoading && !data ? (
        <Loader className="space-y-3">
          <Loader.Item height="72px" />
          <Loader.Item height="72px" />
        </Loader>
      ) : rows.length === 0 ? (
        <div className="text-sm py-12 text-center text-tertiary" data-testid="my-requests-empty">
          {status ? "No requests with that status." : "You haven't made any time requests."}
        </div>
      ) : (
        <div className="rounded-lg border border-subtle bg-surface-1" data-testid="my-requests-list">
          {rows.map((request) => (
            <RequestRow
              key={request.id}
              request={request}
              workspaceSlug={workspaceSlug}
              timeZone={timeZone}
              actions={
                request.can_withdraw ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    stretch="auto"
                    label="Withdraw"
                    data-testid="request-withdraw"
                    loading={withdrawing === request.id}
                    onClick={() => void withdraw(request.id)}
                  />
                ) : undefined
              }
            />
          ))}
        </div>
      )}

      {data && data.count > rows.length && limit < MAX && (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            stretch="auto"
            label="Show more"
            onClick={() => setLimit((l) => Math.min(MAX, l + PAGE))}
          />
        </div>
      )}
      {data && data.count > MAX && rows.length >= MAX && (
        <p className="text-xs text-center text-tertiary">Showing your {MAX} newest requests.</p>
      )}

      <RequestModal
        isOpen={modalOpen}
        handleClose={() => setModalOpen(false)}
        workspaceSlug={workspaceSlug}
        timeZone={timeZone}
        kind="missing_time"
      />
    </div>
  );
});
