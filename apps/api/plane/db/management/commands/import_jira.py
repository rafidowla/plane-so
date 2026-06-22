# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Import issues, comments and worklogs from Jira into a Plane project.

Self-hosted friendly: talks to Jira over REST and writes straight through the
ORM (so it can populate the time-tracking tables too). Defaults to a dry-run.

Examples:
  # Dry-run against a real Jira project (no writes):
  python manage.py import_jira --slug demo --project DEMO \
      --initiator admin@plane.test \
      --jira-url https://acme.atlassian.net --jira-email you@acme.com \
      --jira-token <token> --jira-project ENG --with-worklogs

  # Execute the import:
  python manage.py import_jira ... --execute

  # Offline self-test with bundled sample data (no Jira account needed):
  python manage.py import_jira --slug demo --project DEMO \
      --initiator admin@plane.test --sample --with-worklogs --execute
"""

import os
from datetime import datetime, timezone as dt_timezone

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from plane.db.models import (
    User,
    Project,
    State,
    Issue,
    IssueComment,
    IssueAssignee,
    IssueLabel,
    Label,
    WorkspaceMember,
    ProjectMember,
    ResourceCapacity,
    IssueWorklog,
)

PRIORITY_MAP = {
    "highest": "urgent",
    "urgent": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "lowest": "low",
}


def _norm(name):
    """Normalise a status name for fuzzy matching (lowercase, no spaces/hyphens)."""
    return "".join((name or "").lower().split()).replace("-", "").replace("_", "")


def _adf_to_text(node):
    """Best-effort plain-text extraction from a Jira ADF document (or string)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [_adf_to_text(c) for c in node.get("content", [])]
        sep = "\n" if node.get("type") in ("paragraph", "heading", "listItem") else ""
        return sep.join(p for p in parts if p)
    if isinstance(node, list):
        return "\n".join(_adf_to_text(c) for c in node if c)
    return ""


class Command(BaseCommand):
    help = "Import issues (and optionally worklogs) from Jira into a Plane project"

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="Plane workspace slug")
        parser.add_argument("--project", required=True, help="Plane project id or identifier")
        parser.add_argument("--initiator", required=True, help="email of the Plane user to own imported records")
        parser.add_argument("--jira-url", default=os.environ.get("JIRA_BASE_URL"))
        parser.add_argument("--jira-email", default=os.environ.get("JIRA_EMAIL"))
        parser.add_argument("--jira-token", default=os.environ.get("JIRA_API_TOKEN"))
        parser.add_argument("--jira-project", default=os.environ.get("JIRA_PROJECT_KEY"))
        parser.add_argument("--jql", default=None, help="override JQL (default: project = <KEY> ORDER BY created ASC)")
        parser.add_argument("--limit", type=int, default=0, help="max issues (0 = all)")
        parser.add_argument("--with-worklogs", action="store_true", default=False)
        parser.add_argument("--sample", action="store_true", default=False, help="use bundled sample data, skip Jira")
        parser.add_argument("--execute", action="store_true", default=False, help="write to DB (default is dry-run)")

    # ------------------------------------------------------------------ Jira IO
    def _jira_get(self, opts, path, params=None):
        url = f"{opts['jira_url'].rstrip('/')}{path}"
        resp = requests.get(
            url,
            params=params or {},
            auth=(opts["jira_email"], opts["jira_token"]),
            headers={"Accept": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def _fetch_issues(self, opts):
        if opts["sample"]:
            return _sample_issues()
        jql = opts["jql"] or f"project = {opts['jira_project']} ORDER BY created ASC"
        fields = "summary,description,status,assignee,reporter,priority,labels,created,duedate,comment,worklog"
        issues, start, page = [], 0, 100
        while True:
            data = self._jira_get(
                opts, "/rest/api/3/search", {"jql": jql, "startAt": start, "maxResults": page, "fields": fields}
            )
            batch = data.get("issues", [])
            issues.extend(batch)
            start += len(batch)
            if not batch or start >= data.get("total", 0):
                break
            if opts["limit"] and len(issues) >= opts["limit"]:
                break
        if opts["limit"]:
            issues = issues[: opts["limit"]]
        return issues

    # --------------------------------------------------------------- mapping
    def _build_maps(self, project):
        state_map = {}
        default_state = None
        for st in State.objects.filter(project=project).exclude(group="triage"):
            state_map[_norm(st.name)] = st
            if st.default:
                default_state = st
        member_map = {}
        for wm in WorkspaceMember.objects.filter(workspace=project.workspace, is_active=True).select_related("member"):
            if wm.member.email:
                member_map[wm.member.email.lower()] = wm.member
        return state_map, default_state, member_map

    def _resolve_rate(self, project, user_id):
        cap = ResourceCapacity.objects.filter(workspace=project.workspace, user_id=user_id).first()
        if cap and cap.billable_rate is not None:
            return cap.billable_rate, cap.currency
        if project.client_id and project.client and project.client.default_billable_rate is not None:
            return project.client.default_billable_rate, project.client.currency
        return None, "USD"

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        execute = opts["execute"]
        dry = not execute

        if not opts["sample"]:
            missing = [k for k in ("jira_url", "jira_email", "jira_token", "jira_project") if not opts.get(k)]
            if missing:
                raise CommandError(f"Missing Jira settings: {', '.join(missing)} (pass flags or set JIRA_* env vars)")

        # Resolve Plane project + initiator
        project = (
            Project.objects.filter(workspace__slug=opts["slug"], identifier=opts["project"]).first()
            or Project.objects.filter(workspace__slug=opts["slug"], pk=opts["project"]).first()
        )
        if not project:
            raise CommandError(f"Project '{opts['project']}' not found in workspace '{opts['slug']}'")
        initiator = User.objects.filter(email=opts["initiator"]).first()
        if not initiator:
            raise CommandError(f"Initiator user '{opts['initiator']}' not found")
        if not ProjectMember.objects.filter(project=project, member=initiator, is_active=True).exists():
            raise CommandError("Initiator must be a member of the project")

        state_map, default_state, member_map = self._build_maps(project)

        self.stdout.write(self.style.MIGRATE_HEADING(f"{'DRY-RUN' if dry else 'IMPORT'}: Jira -> {project.name}"))
        issues = self._fetch_issues(opts)
        self.stdout.write(f"Fetched {len(issues)} Jira issue(s)\n")

        created = skipped = comments_n = worklogs_n = 0
        unmapped_states, unmapped_users = set(), set()

        for ji in issues:
            key = ji.get("key")
            f = ji.get("fields", {})
            summary = f.get("summary") or key
            existing = Issue.objects.filter(project=project, external_source="jira", external_id=key).first()
            if existing:
                skipped += 1
                self.stdout.write(f"  = {key}: already imported (skip)")
                continue

            status_name = (f.get("status") or {}).get("name", "")
            state = state_map.get(_norm(status_name)) or default_state
            if _norm(status_name) not in state_map:
                unmapped_states.add(status_name)
            pr = ((f.get("priority") or {}).get("name") or "").lower()
            priority = PRIORITY_MAP.get(pr, "none")

            assignee = f.get("assignee") or {}
            a_email = (assignee.get("emailAddress") or "").lower()
            assignee_user = member_map.get(a_email)
            if assignee and not assignee_user:
                unmapped_users.add(assignee.get("displayName") or a_email or "unknown")

            labels = f.get("labels") or []
            desc = _adf_to_text(f.get("description"))
            jira_comments = (f.get("comment") or {}).get("comments", [])
            jira_worklogs = (f.get("worklog") or {}).get("worklogs", []) if opts["with_worklogs"] else []

            self.stdout.write(
                f"  + {key} | {summary[:48]!r} | state={state.name if state else '—'} | "
                f"priority={priority} | assignee={assignee_user.display_name if assignee_user else '—'} | "
                f"labels={len(labels)} | comments={len(jira_comments)} | worklogs={len(jira_worklogs)}"
            )

            comments_n += len(jira_comments)
            worklogs_n += len(jira_worklogs)
            if dry:
                created += 1
                continue

            # ---- write ----
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
            created += 1

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
                body = _adf_to_text(jc.get("body"))
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
                logged_date = (started or datetime.now(dt_timezone.utc)).date()
                rate, currency = self._resolve_rate(project, author.id)
                IssueWorklog(
                    project=project,
                    workspace=project.workspace,
                    issue=issue,
                    logged_by=author,
                    duration=max(1, round(seconds / 60)),
                    description=_adf_to_text(jw.get("comment"))[:500],
                    logged_date=logged_date,
                    source="manual",
                    is_billable=True,
                    billable_rate=rate,
                    currency=currency,
                    created_by_id=initiator.id,
                ).save(created_by_id=initiator.id)

        # ---- summary ----
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{'Would create' if dry else 'Created'}: {created} issue(s)"))
        self.stdout.write(f"Skipped (already imported): {skipped}")
        self.stdout.write(f"Comments: {comments_n} | Worklogs: {worklogs_n}")
        if unmapped_states:
            self.stdout.write(self.style.WARNING(f"Unmapped statuses (used default): {sorted(unmapped_states)}"))
        if unmapped_users:
            self.stdout.write(self.style.WARNING(f"Unmapped users (assigned to initiator): {sorted(unmapped_users)}"))
        if dry:
            self.stdout.write(self.style.NOTICE("\nDry-run only. Re-run with --execute to write these records."))


def _sample_issues():
    """Realistic Jira-shaped fixtures for offline self-test."""
    return [
        {
            "key": "ENG-101",
            "fields": {
                "summary": "Login page throws 500 on empty password",
                "description": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Steps to reproduce: submit the login form with a blank password."}]}
                    ],
                },
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "Alice Anderson", "emailAddress": "alice@plane.test"},
                "reporter": {"displayName": "Admin User", "emailAddress": "admin@plane.test"},
                "labels": ["bug", "auth"],
                "duedate": "2026-07-15",
                "comment": {
                    "comments": [
                        {"id": "9001", "author": {"emailAddress": "bob@plane.test"}, "body": "Reproduced on staging."},
                        {"id": "9002", "author": {"emailAddress": "alice@plane.test"}, "body": "Fix in review."},
                    ]
                },
                "worklog": {
                    "worklogs": [
                        {"author": {"emailAddress": "alice@plane.test"}, "timeSpentSeconds": 5400, "started": "2026-06-20T09:00:00.000+0000", "comment": "Investigation"},
                        {"author": {"emailAddress": "bob@plane.test"}, "timeSpentSeconds": 1800, "started": "2026-06-21T14:00:00.000+0000", "comment": "Pair debugging"},
                    ]
                },
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
