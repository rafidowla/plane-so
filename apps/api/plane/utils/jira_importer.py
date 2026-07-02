# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared Jira -> Plane import engine.

Used by the `import_jira` management command, the synchronous preview API,
and the background import task. Fetches Jira issues/comments/worklogs over
REST and writes them through the ORM (so worklogs land in the time-tracking
tables). Idempotent via Issue.external_id.
"""

import math

import requests
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueWorklog,
    Label,
    Project,
    ProjectMember,
    ResourceCapacity,
    State,
    User,
    WorkspaceMember,
)

PRIORITY_MAP = {
    "highest": "urgent",
    "urgent": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "lowest": "low",
}

ISSUE_FIELDS = "summary,description,status,assignee,reporter,priority,labels,created,duedate,comment,worklog"


def norm(name):
    """Normalise a status name for fuzzy matching (lowercase, no spaces/hyphens)."""
    return "".join((name or "").lower().split()).replace("-", "").replace("_", "")


def adf_to_text(node):
    """Best-effort plain-text extraction from a Jira ADF document (or string)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [adf_to_text(c) for c in node.get("content", [])]
        sep = "\n" if node.get("type") in ("paragraph", "heading", "listItem") else ""
        return sep.join(p for p in parts if p)
    if isinstance(node, list):
        return "\n".join(adf_to_text(c) for c in node if c)
    return ""


class JiraConfigError(Exception):
    """Raised for missing/invalid Jira connection settings."""


def fetch_jira_issues(jira_url, jira_email, jira_token, jira_project=None, jql=None, limit=0):
    """Fetch issues (with comments + worklogs) from Jira Cloud REST API v3."""
    for key, val in (("jira_url", jira_url), ("jira_email", jira_email), ("jira_token", jira_token)):
        if not val:
            raise JiraConfigError(f"Missing Jira setting: {key}")
    query = jql or (f"project = {jira_project} ORDER BY created ASC" if jira_project else None)
    if not query:
        raise JiraConfigError("Provide a Jira project key or a JQL query")

    base = jira_url.rstrip("/")
    auth = (jira_email, jira_token)
    headers = {"Accept": "application/json"}

    def _raise_for_response(resp):
        # Surface auth/permission errors clearly instead of a generic failure.
        if resp.status_code in (401, 403):
            raise JiraConfigError(
                f"Jira rejected the credentials ({resp.status_code}). Check the email + API token "
                f"and that the token has permission to read this project."
            )

    # Jira Cloud (current): enhanced search with token-based pagination. The legacy
    # GET /rest/api/3/search was removed in 2025, so we use /search/jql and fall
    # back to the legacy endpoint only for older Jira Server/Data Center.
    issues, next_token, guard = [], None, 0
    use_legacy = False
    while True:
        params = {"jql": query, "maxResults": 100, "fields": ISSUE_FIELDS}
        if next_token:
            params["nextPageToken"] = next_token
        resp = requests.get(f"{base}/rest/api/3/search/jql", params=params, auth=auth, headers=headers, timeout=60)
        if resp.status_code in (404, 410):
            use_legacy = True
            break
        _raise_for_response(resp)
        resp.raise_for_status()
        data = resp.json()
        issues.extend(data.get("issues", []))
        next_token = data.get("nextPageToken")
        guard += 1
        if data.get("isLast") or not next_token or (limit and len(issues) >= limit) or guard > 10000:
            break

    if use_legacy:
        # Legacy offset pagination (Jira Server / Data Center).
        issues, start = [], 0
        while True:
            params = {"jql": query, "startAt": start, "maxResults": 100, "fields": ISSUE_FIELDS}
            resp = requests.get(f"{base}/rest/api/2/search", params=params, auth=auth, headers=headers, timeout=60)
            _raise_for_response(resp)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            start += len(batch)
            if not batch or start >= data.get("total", 0) or (limit and len(issues) >= limit):
                break

    return issues[:limit] if limit else issues


def build_maps(project):
    state_map, default_state = {}, None
    for st in State.objects.filter(project=project).exclude(group="triage"):
        state_map[norm(st.name)] = st
        if st.default:
            default_state = st
    member_map = {}
    for wm in WorkspaceMember.objects.filter(workspace=project.workspace, is_active=True).select_related("member"):
        if wm.member.email:
            member_map[wm.member.email.lower()] = wm.member
    return state_map, default_state, member_map


def resolve_rate(project, user_id):
    cap = ResourceCapacity.objects.filter(workspace=project.workspace, user_id=user_id).first()
    if cap and cap.billable_rate is not None:
        return cap.billable_rate, cap.currency
    if project.client_id and project.client and project.client.default_billable_rate is not None:
        return project.client.default_billable_rate, project.client.currency
    return None, "USD"


def is_project_admin(project, user):
    return ProjectMember.objects.filter(
        project=project, member=user, role=20, is_active=True
    ).exists()


def run_import(project, initiator, issues, with_worklogs=False, dry_run=True, progress=None, preview_limit=25):
    """Process Jira issues into Plane. Returns a result dict.

    `issues` is a list of Jira issue dicts (already fetched). When dry_run,
    nothing is written; a per-issue `preview` (capped at preview_limit) and the
    same aggregate counts are returned. `progress(processed, total)` is called
    after each issue for background-job status updates.
    """
    total = len(issues)
    created = skipped = comments_n = worklogs_n = 0
    unmapped_states, unmapped_users = set(), set()
    preview = []

    state_map, default_state, member_map = build_maps(project)

    for idx, ji in enumerate(issues):
        key = ji.get("key")
        f = ji.get("fields", {})
        summary = f.get("summary") or key

        if Issue.objects.filter(project=project, external_source="jira", external_id=key).exists():
            skipped += 1
            if progress:
                progress(idx + 1, total)
            continue

        status_name = (f.get("status") or {}).get("name", "")
        state = state_map.get(norm(status_name)) or default_state
        if norm(status_name) not in state_map:
            unmapped_states.add(status_name)
        pr = ((f.get("priority") or {}).get("name") or "").lower()
        priority = PRIORITY_MAP.get(pr, "none")

        assignee = f.get("assignee") or {}
        a_email = (assignee.get("emailAddress") or "").lower()
        assignee_user = member_map.get(a_email)
        if assignee and not assignee_user:
            unmapped_users.add(assignee.get("displayName") or a_email or "unknown")

        labels = f.get("labels") or []
        desc = adf_to_text(f.get("description"))
        jira_comments = (f.get("comment") or {}).get("comments", [])
        jira_worklogs = (f.get("worklog") or {}).get("worklogs", []) if with_worklogs else []
        comments_n += len(jira_comments)
        worklogs_n += len(jira_worklogs)
        created += 1

        if len(preview) < preview_limit:
            preview.append(
                {
                    "key": key,
                    "summary": summary[:80],
                    "state": state.name if state else None,
                    "priority": priority,
                    "assignee": assignee_user.display_name if assignee_user else None,
                    "labels": len(labels),
                    "comments": len(jira_comments),
                    "worklogs": len(jira_worklogs),
                }
            )

        if not dry_run:
            issue = Issue(
                project=project,
                workspace=project.workspace,
                name=summary[:254],
                description_html=f"<p>{desc}</p>" if desc else "<p></p>",
                priority=priority,
                state=state,
                external_source="jira",
                external_id=key,
                created_by_id=initiator.id,
            )
            if f.get("duedate"):
                issue.target_date = f["duedate"]
            issue.save(created_by_id=initiator.id)

            if assignee_user:
                IssueAssignee.objects.create(
                    issue=issue, assignee=assignee_user, project=project, created_by_id=initiator.id
                )
            for lname in labels:
                label, _ = Label.objects.get_or_create(
                    project=project, name=lname, defaults={"created_by_id": initiator.id}
                )
                IssueLabel.objects.create(issue=issue, label=label, project=project, created_by_id=initiator.id)
            for jc in jira_comments:
                body = adf_to_text(jc.get("body"))
                actor = member_map.get(((jc.get("author") or {}).get("emailAddress") or "").lower()) or initiator
                IssueComment(
                    issue=issue,
                    project=project,
                    workspace=project.workspace,
                    comment_html=f"<p>{body}</p>" if body else "<p></p>",
                    actor=actor,
                    external_source="jira",
                    external_id=str(jc.get("id")),
                    created_by_id=initiator.id,
                ).save(created_by_id=initiator.id)
            for jw in jira_worklogs:
                seconds = int(jw.get("timeSpentSeconds") or 0)
                if seconds <= 0:
                    continue
                author = member_map.get(((jw.get("author") or {}).get("emailAddress") or "").lower()) or initiator
                started = parse_datetime(jw.get("started")) if jw.get("started") else None
                rate, currency = resolve_rate(project, author.id)
                IssueWorklog(
                    project=project,
                    workspace=project.workspace,
                    issue=issue,
                    logged_by=author,
                    duration=max(1, math.floor(seconds / 60)),
                    description=adf_to_text(jw.get("comment"))[:500],
                    logged_date=(started or timezone.now()).date(),
                    source="manual",
                    is_billable=True,
                    billable_rate=rate,
                    currency=currency,
                ).save(created_by_id=initiator.id)

        if progress:
            progress(idx + 1, total)

    return {
        "dry_run": dry_run,
        "fetched": total,
        "created": created,
        "skipped": skipped,
        "comments": comments_n,
        "worklogs": worklogs_n,
        "unmapped_states": sorted(s for s in unmapped_states if s),
        "unmapped_users": sorted(unmapped_users),
        "preview": preview,
    }


def sample_issues():
    """Realistic Jira-shaped fixtures for offline self-test / demos."""
    return [
        {
            "key": "ENG-101",
            "fields": {
                "summary": "Login page throws 500 on empty password",
                "description": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Submit the login form with a blank password."}]}
                    ],
                },
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "Alice Anderson", "emailAddress": "alice@plane.test"},
                "reporter": {"displayName": "Admin User", "emailAddress": "admin@plane.test"},
                "labels": ["bug", "auth"],
                "duedate": "2026-07-15",
                "comment": {"comments": [
                    {"id": "9001", "author": {"emailAddress": "bob@plane.test"}, "body": "Reproduced on staging."},
                    {"id": "9002", "author": {"emailAddress": "alice@plane.test"}, "body": "Fix in review."},
                ]},
                "worklog": {"worklogs": [
                    {"author": {"emailAddress": "alice@plane.test"}, "timeSpentSeconds": 5400, "started": "2026-06-20T09:00:00.000+0000", "comment": "Investigation"},
                    {"author": {"emailAddress": "bob@plane.test"}, "timeSpentSeconds": 1800, "started": "2026-06-21T14:00:00.000+0000", "comment": "Pair debugging"},
                ]},
            },
        },
        {
            "key": "ENG-102",
            "fields": {
                "summary": "Add CSV export to reports",
                "description": "Users want to download the report as CSV.",
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "assignee": None,
                "reporter": {"displayName": "Admin User", "emailAddress": "admin@plane.test"},
                "labels": ["feature"],
                "comment": {"comments": []},
                "worklog": {"worklogs": []},
            },
        },
    ]
