# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — work-item label snapshot on worklogs.

Worklogs outlive their work item (``IssueWorklog.issue`` is DO_NOTHING, no DB
constraint). ``issue_ref`` ("PROJ-12") and ``issue_name`` are copied at write
time so reports can still label the time once the issue row is hard-deleted.
While the issue exists, reports use the live values.
"""


def issue_snapshot(issue_id):
    """(ref, name) for an issue, including soft-deleted ones; ("", "") if gone."""
    from plane.db.models import Issue

    row = Issue.all_objects.filter(pk=issue_id).values("name", "sequence_id", "project__identifier").first()
    if not row:
        return "", ""
    ref = f"{row['project__identifier']}-{row['sequence_id']}" if row["project__identifier"] else ""
    return ref[:64], (row["name"] or "")[:255]


def refresh_worklog_snapshots(issue_ids):
    """Re-copy the label onto every worklog of these issues (e.g. after a move)."""
    from plane.db.models import Issue, IssueWorklog

    for issue in Issue.all_objects.filter(pk__in=list(issue_ids)).select_related("project"):
        ref = f"{issue.project.identifier}-{issue.sequence_id}"[:64]
        IssueWorklog.all_objects.filter(issue_id=issue.id).update(issue_ref=ref, issue_name=(issue.name or "")[:255])
