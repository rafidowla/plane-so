# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Import a Jira CSV export (the wide "all fields" format) into a Plane project.

Jira's CSV export repeats column headers for multi-value fields (Log Work,
Comment, Labels, Watchers, Attachment). Users appear as a display name plus an
opaque account id, but never an email — so members are matched by display name,
and worklog authors (which are account ids) are resolved via a name table
harvested from the Assignee/Reporter/Creator columns.

Reuses state/priority mapping and rate resolution from jira_importer.
"""

import csv
import html
import math
from datetime import datetime

from django.utils import timezone

from plane.db.models import (
    Description,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueWorklog,
    Label,
    WorkspaceMember,
)
from plane.utils.jira_importer import PRIORITY_MAP, norm, resolve_rate

_DATE_FORMATS = ("%d/%b/%y %I:%M %p", "%d/%b/%Y %I:%M %p", "%d/%b/%y", "%d/%b/%Y", "%Y-%m-%d")


def _parse_date(value):
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value):
    """FORK: jira-comment-provenance (#15) — full timestamp variant of _parse_date.
    Jira CSV comment cells carry no timezone, so make parsed values aware."""
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
        except ValueError:
            continue
    return None


def _to_html(text):
    text = (text or "").strip()
    if not text:
        return "<p></p>"
    return "<p>" + html.escape(text).replace("\n", "<br/>") + "</p>"


def parse_jira_csv(fp):
    """Parse an open text file object; returns (issues, accountid_to_name)."""
    reader = csv.reader(fp)
    header = next(reader)
    rows = list(reader)

    def cols(name):
        return [i for i, c in enumerate(header) if c == name]

    def one(row, name):
        for i in cols(name):
            if i < len(row) and row[i].strip():
                return row[i].strip()
        return ""

    def many(row, name):
        return [row[i].strip() for i in cols(name) if i < len(row) and row[i].strip()]

    # Harvest account-id -> display-name from the identity columns.
    accountid_to_name = {}
    for row in rows:
        for name_col, id_col in (("Assignee", "Assignee Id"), ("Reporter", "Reporter Id"), ("Creator", "Creator Id")):
            nm, aid = one(row, name_col), one(row, id_col)
            if aid and nm:
                accountid_to_name.setdefault(aid, nm)

    issues = []
    for row in rows:
        key = one(row, "Issue key")
        if not key:
            continue
        worklogs = []
        for cell in many(row, "Log Work"):
            # format: comment;started;authorAccountId;timeSpentSeconds
            parts = cell.rsplit(";", 3)
            if len(parts) == 4:
                comment, started, author_id, seconds = parts
            else:
                continue
            try:
                seconds = int(seconds)
            except ValueError:
                continue
            worklogs.append(
                {"seconds": seconds, "started": _parse_date(started), "author_id": author_id.strip(), "comment": comment}
            )
        comments = []
        for cell in many(row, "Comment"):
            # format: created;author;body  (body may contain ';')
            parts = cell.split(";", 2)
            if len(parts) == 3:
                comments.append({"created": parts[0], "author": parts[1].strip(), "body": parts[2]})
            elif len(parts) == 2:
                comments.append({"created": parts[0], "author": "", "body": parts[1]})
            else:
                comments.append({"created": "", "author": "", "body": cell})

        issues.append(
            {
                "key": key,
                "summary": one(row, "Summary"),
                "status": one(row, "Status"),
                "priority": one(row, "Priority"),
                "assignee_name": one(row, "Assignee"),
                "labels": many(row, "Labels"),
                "description": one(row, "Description"),
                "due_date": _parse_date(one(row, "Due date")),
                # Jira's "Parent" column is the numeric internal id; "Parent key" is the
                # human-readable key (e.g. UG-1) that matches our stored external_id.
                "parent_key": one(row, "Parent key") or one(row, "Parent"),
                "comments": comments,
                "worklogs": worklogs,
            }
        )
    return issues, accountid_to_name


def _build_maps(project):
    from plane.db.models import State

    state_map, default_state = {}, None
    for st in State.objects.filter(project=project).exclude(group="triage"):
        state_map[norm(st.name)] = st
        if st.default:
            default_state = st

    member_by_name = {}
    for wm in WorkspaceMember.objects.filter(workspace=project.workspace, is_active=True).select_related("member"):
        m = wm.member
        keys = {(m.display_name or "").lower(), f"{m.first_name} {m.last_name}".strip().lower()}
        for k in keys:
            if k:
                member_by_name[k] = m
    return state_map, default_state, member_by_name


def run_csv_import(
    project, initiator, issues, accountid_to_name, with_worklogs=False, dry_run=True, progress=None, preview_limit=25
):
    """Create Plane records from parsed Jira CSV issues. Returns a result dict."""
    total = len(issues)
    created = skipped = comments_n = worklogs_n = 0
    unmapped_states, unmapped_users = set(), set()
    preview = []

    state_map, default_state, member_by_name = _build_maps(project)

    def resolve_member(name):
        return member_by_name.get((name or "").strip().lower())

    created_by_key = {}  # jira key -> plane issue (for parent linking)

    for idx, ji in enumerate(issues):
        key = ji["key"]
        summary = ji["summary"] or key

        if Issue.objects.filter(project=project, external_source="jira", external_id=key).exists():
            skipped += 1
            if progress:
                progress(idx + 1, total)
            continue

        state = state_map.get(norm(ji["status"])) or default_state
        if ji["status"] and norm(ji["status"]) not in state_map:
            unmapped_states.add(ji["status"])
        priority = PRIORITY_MAP.get((ji["priority"] or "").lower(), "none")
        assignee_user = resolve_member(ji["assignee_name"])
        if ji["assignee_name"] and not assignee_user:
            unmapped_users.add(ji["assignee_name"])

        wl = ji["worklogs"] if with_worklogs else []
        comments_n += len(ji["comments"])
        worklogs_n += len(wl)
        created += 1

        if len(preview) < preview_limit:
            preview.append(
                {
                    "key": key,
                    "summary": summary[:80],
                    "state": state.name if state else None,
                    "priority": priority,
                    "assignee": assignee_user.display_name if assignee_user else None,
                    "labels": len(ji["labels"]),
                    "comments": len(ji["comments"]),
                    "worklogs": len(wl),
                }
            )

        if dry_run:
            if progress:
                progress(idx + 1, total)
            continue

        issue = Issue(
            project=project,
            workspace=project.workspace,
            name=summary[:254],
            description_html=_to_html(ji["description"]),
            priority=priority,
            state=state,
            external_source="jira",
            external_id=key,
            created_by_id=initiator.id,
        )
        if ji["due_date"]:
            issue.target_date = ji["due_date"]
        issue.save(created_by_id=initiator.id)
        created_by_key[key] = issue

        if assignee_user:
            IssueAssignee.objects.create(issue=issue, assignee=assignee_user, project=project, created_by_id=initiator.id)
        for lname in ji["labels"]:
            label, _ = Label.objects.get_or_create(project=project, name=lname, defaults={"created_by_id": initiator.id})
            IssueLabel.objects.create(issue=issue, label=label, project=project, created_by_id=initiator.id)
        for c in ji["comments"]:
            matched = resolve_member(c["author"])
            actor = matched or initiator
            # FORK: jira-comment-provenance (#15) — resolved author as creator,
            # Jira author name kept when unmapped, original timestamp backfilled.
            comment = IssueComment(
                issue=issue,
                project=project,
                workspace=project.workspace,
                comment_html=_to_html(c["body"]),
                actor=actor,
                external_source="jira",
                external_actor_display=None if matched else (c["author"] or None),
                created_by_id=actor.id,
            )
            comment.save(created_by_id=actor.id)
            c_created = _parse_datetime(c["created"])
            if c_created:
                IssueComment.objects.filter(id=comment.id).update(created_at=c_created)
                if comment.description_id:
                    Description.objects.filter(id=comment.description_id).update(created_at=c_created)
        for w in wl:
            if w["seconds"] <= 0:
                continue
            author = resolve_member(accountid_to_name.get(w["author_id"], "")) or assignee_user or initiator
            rate, currency = resolve_rate(project, author.id)
            IssueWorklog(
                project=project,
                workspace=project.workspace,
                issue=issue,
                logged_by=author,
                duration=max(1, math.floor(w["seconds"] / 60)),
                description=(w["comment"] or "")[:500],
                logged_date=w["started"] or timezone.now().date(),
                source="manual",
                is_billable=True,
                billable_rate=rate,
                currency=currency,
            ).save(created_by_id=initiator.id)

        if progress:
            progress(idx + 1, total)

    # Second pass: link parents. Resolve both child and parent from this run or the
    # DB so re-running the import backfills links for issues created earlier.
    parents_linked = 0
    if not dry_run:
        for ji in issues:
            parent_key = ji.get("parent_key")
            if not parent_key:
                continue
            child = created_by_key.get(ji["key"]) or Issue.objects.filter(
                project=project, external_source="jira", external_id=ji["key"]
            ).first()
            if not child:
                continue
            parent = created_by_key.get(parent_key) or Issue.objects.filter(
                project=project, external_source="jira", external_id=parent_key
            ).first()
            if parent and child.parent_id != parent.id:
                child.parent_id = parent.id
                child.save(update_fields=["parent_id"])
                parents_linked += 1

    return {
        "dry_run": dry_run,
        "fetched": total,
        "created": created,
        "skipped": skipped,
        "comments": comments_n,
        "worklogs": worklogs_n,
        "parents_linked": parents_linked,
        "unmapped_states": sorted(s for s in unmapped_states if s),
        "unmapped_users": sorted(unmapped_users),
        "preview": preview,
    }
