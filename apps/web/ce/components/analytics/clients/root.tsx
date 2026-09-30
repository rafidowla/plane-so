/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { Button } from "@makeplane/propel/components/button";
import { Icon } from "@makeplane/propel/components/icon";
import { Input, InputGroup } from "@makeplane/propel/components/input";
import { Loader } from "@plane/blocks/skeleton";
import { setToast } from "@plane/blocks/toast";
import AnalyticsWrapper from "@/components/analytics/analytics-wrapper";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { asApiError } from "@/plane-web/components/analytics/api-error";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";

export const Clients = observer(function Clients() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { workspaceProjectIds, getProjectById } = useProject();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [rate, setRate] = useState("");

  const { data, isLoading, mutate } = useSWR(
    workspaceSlug ? `CLIENTS_${workspaceSlug}` : null,
    workspaceSlug ? () => timeTrackingService.getClients(workspaceSlug) : null
  );
  const clients = data ?? [];

  // Map each project to its current client (from the authoritative clients payload).
  const projectToClient: Record<string, string> = {};
  clients.forEach((c) => (c.project_ids ?? []).forEach((pid) => (projectToClient[pid] = c.id)));

  const handleCreate = async () => {
    if (!name.trim()) {
      setToast({ type: "error", title: "Name required", message: "Enter a client name." });
      return;
    }
    try {
      await timeTrackingService.createClient(workspaceSlug, {
        name: name.trim(),
        identifier: identifier.trim(),
        ...(rate ? { default_billable_rate: rate } : {}),
      });
      setToast({ type: "success", title: "Client created", message: `${name} added.` });
      setName("");
      setIdentifier("");
      setRate("");
      setShowForm(false);
      mutate();
    } catch (err) {
      const apiError = asApiError(err);
      setToast({
        type: "error",
        title: "Error",
        message: apiError.error ?? apiError.name?.[0] ?? "Could not create.",
      });
    }
  };

  const handleDelete = async (id: string, clientName: string) => {
    try {
      await timeTrackingService.deleteClient(workspaceSlug, id);
      setToast({ type: "success", title: "Deleted", message: `${clientName} removed.` });
      mutate();
    } catch (err) {
      const apiError = asApiError(err);
      setToast({ type: "error", title: "Error", message: apiError.error ?? "Could not delete." });
    }
  };

  const handleAssign = async (projectId: string, clientId: string) => {
    try {
      await timeTrackingService.setProjectClient(workspaceSlug, projectId, clientId || null);
      setToast({ type: "success", title: "Updated", message: "Project assignment saved." });
      mutate();
    } catch (err) {
      const apiError = asApiError(err);
      setToast({ type: "error", title: "Error", message: apiError.error ?? "Could not assign." });
    }
  };

  return (
    <AnalyticsWrapper i18nTitle="">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-20 font-bold">Clients</h1>
        <Button
          variant="primary"
          size="sm"
          stretch="auto"
          icon={<Icon icon={Plus} />}
          label="Add client"
          onClick={() => setShowForm((s) => !s)}
        />
      </div>

      {showForm && (
        <div className="mb-5 flex flex-wrap items-end gap-3 rounded-md border border-subtle p-3">
          <div>
            <span className="text-xs mb-1 block text-tertiary">Name</span>
            <div className="w-48">
              <InputGroup size="md">
                <Input size="md" value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Corp" />
              </InputGroup>
            </div>
          </div>
          <div>
            <span className="text-xs mb-1 block text-tertiary">Identifier</span>
            <div className="w-32">
              <InputGroup size="md">
                <Input
                  size="md"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  placeholder="ACME"
                />
              </InputGroup>
            </div>
          </div>
          <div>
            <span className="text-xs mb-1 block text-tertiary">Default rate</span>
            <div className="w-28">
              <InputGroup size="md">
                <Input
                  size="md"
                  type="number"
                  value={rate}
                  onChange={(e) => setRate(e.target.value)}
                  placeholder="150"
                />
              </InputGroup>
            </div>
          </div>
          <Button variant="primary" size="sm" stretch="auto" label="Create" onClick={handleCreate} />
        </div>
      )}

      {isLoading ? (
        <Loader className="space-y-3">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : clients.length === 0 ? (
        <div className="text-sm py-8 text-center text-tertiary">No clients yet. Add one to get started.</div>
      ) : (
        <div className="mb-8 overflow-x-auto rounded-md border border-subtle">
          <table className="text-sm w-full">
            <thead className="bg-surface-2 text-left text-tertiary">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Identifier</th>
                <th className="px-4 py-2 text-right font-medium">Default rate</th>
                <th className="px-4 py-2 text-right font-medium">Projects</th>
                <th className="px-4 py-2 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr key={c.id} className="border-t border-subtle">
                  <td className="px-4 py-2">{c.name}</td>
                  <td className="px-4 py-2 text-tertiary">{c.identifier || "—"}</td>
                  <td className="px-4 py-2 text-right">
                    {c.default_billable_rate ? `$${c.default_billable_rate} ${c.currency}` : "—"}
                  </td>
                  <td className="px-4 py-2 text-right">{c.project_count ?? 0}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => handleDelete(c.id, c.name)}
                      className="hover:text-danger text-tertiary"
                      aria-label="Delete client"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="text-base mb-3 font-semibold">Project assignments</h2>
      <div className="overflow-x-auto rounded-md border border-subtle">
        <table className="text-sm w-full">
          <thead className="bg-surface-2 text-left text-tertiary">
            <tr>
              <th className="px-4 py-2 font-medium">Project</th>
              <th className="px-4 py-2 font-medium">Client</th>
            </tr>
          </thead>
          <tbody>
            {(workspaceProjectIds ?? []).map((pid) => {
              const project = getProjectById(pid);
              if (!project) return null;
              return (
                <tr key={pid} className="border-t border-subtle">
                  <td className="px-4 py-2">{project.name}</td>
                  <td className="px-4 py-2">
                    <select
                      value={projectToClient[pid] ?? ""}
                      onChange={(e) => handleAssign(pid, e.target.value)}
                      className="text-sm rounded border border-subtle bg-transparent px-2 py-1"
                    >
                      <option value="">— Unassigned —</option>
                      {clients.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </AnalyticsWrapper>
  );
});
