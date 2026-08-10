/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Input } from "@plane/ui";
import AnalyticsWrapper from "@/components/analytics/analytics-wrapper";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
import { timeTrackingService } from "@/plane-web/services/time-tracking.service";
import type { TJiraImportJob, TJiraPreview } from "@/plane-web/components/issues/worklog/types";

const inputCls = "text-sm w-full rounded border border-subtle bg-transparent px-2 py-1.5";

export const Imports = observer(function Imports() {
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug ?? "";
  const { workspaceProjectIds, getProjectById } = useProject();
  const { allowPermissions } = useUserPermissions();
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const [projectId, setProjectId] = useState("");
  const [jiraUrl, setJiraUrl] = useState("");
  const [jiraEmail, setJiraEmail] = useState("");
  const [jiraToken, setJiraToken] = useState("");
  const [jiraProject, setJiraProject] = useState("");
  const [withWorklogs, setWithWorklogs] = useState(true);
  const [withAttachments, setWithAttachments] = useState(false);
  const [useSample, setUseSample] = useState(false);

  // Status filter: null = never loaded (import everything, the old behavior).
  const [jiraStatuses, setJiraStatuses] = useState<string[] | null>(null);
  const [checkedStatuses, setCheckedStatuses] = useState<string[]>([]);
  const [loadingStatuses, setLoadingStatuses] = useState(false);

  const [preview, setPreview] = useState<TJiraPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [job, setJob] = useState<TJiraImportJob | null>(null);
  const [importing, setImporting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cfg = () => ({
    jira_url: jiraUrl,
    jira_email: jiraEmail,
    jira_token: jiraToken,
    jira_project: jiraProject,
    with_worklogs: withWorklogs,
    with_attachments: withAttachments,
    sample: useSample,
    // Only send a status filter once the user has loaded and picked statuses.
    ...(jiraStatuses !== null ? { statuses: checkedStatuses } : {}),
  });
  const canLoadStatuses = Boolean(projectId && jiraUrl && jiraEmail && jiraToken && jiraProject) && !useSample;
  const canRun =
    Boolean(projectId) &&
    (useSample || (jiraUrl && jiraEmail && jiraToken)) &&
    // If the list was loaded, at least one status must stay checked.
    (jiraStatuses === null || checkedStatuses.length > 0);

  // Changing the Jira connection makes a previously loaded status list stale.
  const handleConnChange = (setter: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setter(e.target.value);
    setJiraStatuses(null);
  };

  const handleLoadStatuses = async () => {
    setLoadingStatuses(true);
    try {
      const list = await timeTrackingService.getJiraStatuses(workspaceSlug, projectId, cfg());
      setJiraStatuses(list);
      setCheckedStatuses(list); // everything checked by default
    } catch (err: any) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Could not load statuses",
        message: err?.error ?? "Check your Jira details.",
      });
    } finally {
      setLoadingStatuses(false);
    }
  };

  const toggleStatus = (name: string) =>
    setCheckedStatuses((prev) => (prev.includes(name) ? prev.filter((s) => s !== name) : [...prev, name]));

  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    []
  );

  const handlePreview = async () => {
    setPreviewing(true);
    setPreview(null);
    try {
      const res = await timeTrackingService.previewJiraImport(workspaceSlug, projectId, cfg());
      setPreview(res);
    } catch (err: any) {
      setToast({ type: TOAST_TYPE.ERROR, title: "Preview failed", message: err?.error ?? "Check your Jira details." });
    } finally {
      setPreviewing(false);
    }
  };

  const pollJob = (jobId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await timeTrackingService.getJiraImportJob(workspaceSlug, projectId, jobId);
        setJob(j);
        if (j.status === "completed" || j.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setImporting(false);
          if (j.status === "completed")
            setToast({
              type: TOAST_TYPE.SUCCESS,
              title: "Import complete",
              message: `${j.result?.created ?? 0} work items imported.`,
            });
          else setToast({ type: TOAST_TYPE.ERROR, title: "Import failed", message: j.error || "See details." });
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
  };

  const handleStart = async () => {
    setImporting(true);
    setJob(null);
    try {
      const j = await timeTrackingService.startJiraImport(workspaceSlug, projectId, cfg());
      setJob(j);
      if (j.status === "completed" || j.status === "failed") setImporting(false);
      else pollJob(j.id);
    } catch (err: any) {
      setImporting(false);
      setToast({ type: TOAST_TYPE.ERROR, title: "Could not start import", message: err?.error ?? "Try again." });
    }
  };

  if (!isAdmin) {
    return (
      <AnalyticsWrapper i18nTitle="">
        <h1 className="mb-4 text-20 font-bold">Imports</h1>
        <p className="text-sm text-tertiary">Only workspace admins can run imports.</p>
      </AnalyticsWrapper>
    );
  }

  const result = job?.result as TJiraPreview | undefined;

  return (
    <AnalyticsWrapper i18nTitle="">
      <h1 className="mb-1 text-20 font-bold">Import from Jira</h1>
      <p className="text-sm mb-5 text-tertiary">
        Bring Jira work items, comments and logged time into a Plane project. Preview first — nothing is written until
        you start the import.
      </p>

      <div className="grid max-w-2xl gap-4 rounded-md border border-subtle p-4">
        <div>
          <span className="text-xs mb-1 block text-tertiary">Target Plane project</span>
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className={inputCls}>
            <option value="">— Select a project —</option>
            {(workspaceProjectIds ?? []).map((pid) => {
              const p = getProjectById(pid);
              return (
                <option key={pid} value={pid}>
                  {p?.name}
                </option>
              );
            })}
          </select>
        </div>

        <label className="text-sm flex items-center gap-2">
          <input type="checkbox" checked={useSample} onChange={(e) => setUseSample(e.target.checked)} />
          Use sample data (try it without a Jira account)
        </label>

        {!useSample && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <span className="text-xs mb-1 block text-tertiary">Jira URL</span>
              <Input
                value={jiraUrl}
                onChange={handleConnChange(setJiraUrl)}
                placeholder="https://acme.atlassian.net"
                className="w-full"
              />
            </div>
            <div>
              <span className="text-xs mb-1 block text-tertiary">Jira email</span>
              <Input
                value={jiraEmail}
                onChange={handleConnChange(setJiraEmail)}
                placeholder="you@acme.com"
                className="w-full"
              />
            </div>
            <div>
              <span className="text-xs mb-1 block text-tertiary">API token</span>
              <Input
                type="password"
                value={jiraToken}
                onChange={handleConnChange(setJiraToken)}
                placeholder="••••••••"
                className="w-full"
              />
            </div>
            <div>
              <span className="text-xs mb-1 block text-tertiary">Jira project key</span>
              <Input
                value={jiraProject}
                onChange={handleConnChange(setJiraProject)}
                placeholder="ENG"
                className="w-full"
              />
            </div>
          </div>
        )}

        {!useSample && (
          <div className="grid gap-2">
            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleLoadStatuses}
                disabled={!canLoadStatuses || loadingStatuses}
              >
                {loadingStatuses ? "Loading…" : jiraStatuses ? "Reload statuses" : "Load statuses"}
              </Button>
              <span className="text-xs text-tertiary">
                Optional — pick which Jira statuses to import (e.g. skip Done/Closed to leave old tickets behind).
              </span>
            </div>
            {jiraStatuses !== null && (
              <div className="grid gap-1.5 rounded border border-subtle p-3">
                <label className="text-sm flex items-center gap-2 font-medium">
                  <input
                    type="checkbox"
                    checked={jiraStatuses.length > 0 && checkedStatuses.length === jiraStatuses.length}
                    onChange={() =>
                      setCheckedStatuses(checkedStatuses.length === jiraStatuses.length ? [] : jiraStatuses)
                    }
                  />
                  Select all ({checkedStatuses.length}/{jiraStatuses.length})
                </label>
                {jiraStatuses.map((s) => (
                  <label key={s} className="text-sm flex items-center gap-2 pl-5">
                    <input type="checkbox" checked={checkedStatuses.includes(s)} onChange={() => toggleStatus(s)} />
                    {s}
                  </label>
                ))}
                {jiraStatuses.length === 0 && (
                  <p className="text-xs text-amber-600">Jira returned no statuses for this project key.</p>
                )}
                <p className="text-xs text-tertiary">Only tickets in the selected statuses will be imported.</p>
              </div>
            )}
          </div>
        )}

        <label className="text-sm flex items-center gap-2">
          <input type="checkbox" checked={withWorklogs} onChange={(e) => setWithWorklogs(e.target.checked)} />
          Import logged time (Jira worklogs → time tracking)
        </label>

        <label className="text-sm flex items-center gap-2">
          <input
            type="checkbox"
            checked={withAttachments}
            disabled={useSample}
            onChange={(e) => setWithAttachments(e.target.checked)}
          />
          Migrate attachments (downloads files from Jira — needs your Jira login above)
        </label>

        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={handlePreview} disabled={!canRun || previewing}>
            {previewing ? "Previewing…" : "Preview"}
          </Button>
          <Button variant="primary" size="sm" onClick={handleStart} disabled={!canRun || importing}>
            {importing ? "Importing…" : "Start import"}
          </Button>
        </div>
      </div>

      {/* Preview result */}
      {preview && (
        <div className="mt-5 max-w-2xl rounded-md border border-subtle p-4">
          <div className="text-sm mb-2 font-medium">Preview (dry run — nothing written)</div>
          <div className="text-sm mb-3 flex flex-wrap gap-x-6 gap-y-1 text-tertiary">
            <span>
              Would create: <span className="text-primary">{preview.created}</span>
            </span>
            <span>Skipped (already imported): {preview.skipped}</span>
            <span>Comments: {preview.comments}</span>
            <span>Worklogs: {preview.worklogs}</span>
            {withAttachments && <span>Attachments: {preview.attachments ?? 0}</span>}
          </div>
          {preview.unmapped_states.length > 0 && (
            <p className="text-xs text-amber-600 mb-1">
              Unmapped statuses (will use default): {preview.unmapped_states.join(", ")}
            </p>
          )}
          {preview.unmapped_users.length > 0 && (
            <p className="text-xs text-amber-600 mb-2">
              Unmapped users (assigned to you): {preview.unmapped_users.join(", ")}
            </p>
          )}
          <div className="overflow-x-auto rounded border border-subtle">
            <table className="text-sm w-full">
              <thead className="bg-surface-2 text-left text-tertiary">
                <tr>
                  <th className="px-3 py-1.5 font-medium">Key</th>
                  <th className="px-3 py-1.5 font-medium">Summary</th>
                  <th className="px-3 py-1.5 font-medium">State</th>
                  <th className="px-3 py-1.5 font-medium">Assignee</th>
                </tr>
              </thead>
              <tbody>
                {preview.preview.map((r) => (
                  <tr key={r.key} className="border-t border-subtle">
                    <td className="px-3 py-1.5">{r.key}</td>
                    <td className="truncate px-3 py-1.5">{r.summary}</td>
                    <td className="px-3 py-1.5">{r.state ?? "—"}</td>
                    <td className="px-3 py-1.5">{r.assignee ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Import job status */}
      {job && (
        <div className="mt-5 max-w-2xl rounded-md border border-subtle p-4">
          <div className="text-sm mb-2 flex items-center justify-between">
            <span className="font-medium">
              Import {job.status === "completed" ? "complete" : job.status === "failed" ? "failed" : "running…"}
            </span>
            <span className="text-tertiary capitalize">{job.status}</span>
          </div>
          {(job.status === "queued" || job.status === "processing") && (
            <div className="h-2 w-full overflow-hidden rounded bg-surface-2">
              <div
                className="bg-primary h-full transition-all"
                style={{ width: job.total ? `${Math.round((job.processed / job.total) * 100)}%` : "10%" }}
              />
            </div>
          )}
          {job.status === "processing" && (
            <p className="text-xs mt-1 text-tertiary">
              {job.processed} / {job.total} processed
            </p>
          )}
          {job.status === "completed" && result && (
            <div className="text-sm flex flex-wrap gap-x-6 gap-y-1 text-tertiary">
              <span>
                Created: <span className="text-primary">{result.created}</span>
              </span>
              <span>Skipped: {result.skipped}</span>
              <span>Comments: {result.comments}</span>
              <span>Worklogs: {result.worklogs}</span>
            </div>
          )}
          {job.status === "failed" && <p className="text-sm text-red-600">{job.error}</p>}
        </div>
      )}
    </AnalyticsWrapper>
  );
});
