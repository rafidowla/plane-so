# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: jira-comment-provenance (#15).

Guards that migrated Jira comments keep their original author and timestamp:
the resolved workspace member becomes the creator, the original Jira date is
backfilled into created_at (bypassing auto_now_add), and unmapped authors fall
back to the initiator while keeping their Jira display name.
"""

import pytest
from datetime import datetime, timezone as dt_timezone

from plane.db.models import Issue, IssueComment, Project, State, Workspace, WorkspaceMember
from plane.utils.jira_csv_importer import _parse_datetime, run_csv_import


@pytest.fixture
def workspace(db, create_user):
    return Workspace.objects.create(name="Jira WS", slug="jira-ws", owner=create_user)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Jira Project", identifier="JP", workspace=workspace, created_by=create_user)
    State.objects.create(name="Todo", project=project, group="backlog", default=True)
    return project


@pytest.fixture
def jira_author(db, workspace):
    from plane.db.models import User

    user = User.objects.create(email="jane@plane.so", first_name="Jane", last_name="Doe", display_name="Jane Doe")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
    return user


def _issues_payload(comments):
    return [
        {
            "key": "JP-1",
            "summary": "Migrated issue",
            "status": "Todo",
            "priority": "",
            "assignee_name": "",
            "labels": [],
            "description": "",
            "due_date": None,
            "parent_key": None,
            "comments": comments,
            "worklogs": [],
        }
    ]


@pytest.mark.django_db
def test_parse_datetime_makes_aware():
    parsed = _parse_datetime("31/Jul/26 3:47 PM")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 7, 31, 15)
    assert _parse_datetime("not a date") is None
    assert _parse_datetime("") is None


@pytest.mark.django_db
def test_mapped_comment_keeps_author_and_timestamp(project, create_user, jira_author):
    run_csv_import(
        project,
        create_user,
        _issues_payload([{"created": "20/Jun/25 9:05 AM", "author": "Jane Doe", "body": "original note"}]),
        {},
        dry_run=False,
    )

    comment = IssueComment.objects.get(issue__external_id="JP-1")
    assert comment.actor == jira_author
    assert comment.created_by == jira_author
    assert comment.external_actor_display is None
    expected = datetime(2025, 6, 20, 9, 5, tzinfo=dt_timezone.utc)
    assert comment.created_at == expected
    # the linked description row gets the same historical timestamp
    assert comment.description.created_at == expected


@pytest.mark.django_db
def test_unmapped_comment_falls_back_but_keeps_display_name(project, create_user):
    run_csv_import(
        project,
        create_user,
        _issues_payload([{"created": "01/Jul/25 12:00 PM", "author": "External Person", "body": "who dis"}]),
        {},
        dry_run=False,
    )

    comment = IssueComment.objects.get(issue__external_id="JP-1")
    assert comment.actor == create_user
    assert comment.created_by == create_user
    assert comment.external_actor_display == "External Person"
    assert comment.created_at == datetime(2025, 7, 1, 12, 0, tzinfo=dt_timezone.utc)


@pytest.mark.django_db
def test_comment_without_date_uses_import_time(project, create_user):
    run_csv_import(
        project,
        create_user,
        _issues_payload([{"created": "", "author": "", "body": "dateless"}]),
        {},
        dry_run=False,
    )

    comment = IssueComment.objects.get(issue__external_id="JP-1")
    # no Jira date → auto_now_add stands (recent, not a backfilled historical date)
    assert comment.created_at.year >= 2026
