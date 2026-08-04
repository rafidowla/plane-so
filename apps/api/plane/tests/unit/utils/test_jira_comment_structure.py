# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: jira-comment-structure (#21).

Guards that Jira's threaded comments (replies) survive migration: replies are
imported as comments in their own right and linked to their parent via
IssueComment.parent, whichever shape the Jira API exposes the threading in
(explicit parent ids or nested replies/children arrays).
"""

import pytest

from plane.db.models import IssueComment, Project, State, Workspace
from plane.utils.jira_importer import _flatten_jira_comments, run_import


@pytest.fixture
def workspace(db, create_user):
    return Workspace.objects.create(name="Jira WS", slug="jira-ws-21", owner=create_user)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Jira Project", identifier="J2", workspace=workspace, created_by=create_user)
    State.objects.create(name="Todo", project=project, group="backlog", default=True)
    return project


def _comment(cid, body="", **extra):
    return {"id": cid, "author": {"displayName": "Jane Doe"}, "body": body, "created": "", "updated": "", **extra}


def _issue_payload(comments):
    return [
        {
            "key": "J2-1",
            "fields": {
                "summary": "Threaded issue",
                "status": {"name": "Todo"},
                "comment": {"comments": comments},
            },
            "renderedFields": {},
        }
    ]


def test_flatten_keeps_top_level_order_and_rendered_index():
    flat = _flatten_jira_comments([_comment("1"), _comment("2")])
    assert [(jc["id"], parent, idx) for jc, parent, idx in flat] == [("1", None, 0), ("2", None, 1)]


def test_flatten_reads_explicit_parent_id_variants():
    assert _flatten_jira_comments([_comment("1", parentCommentId=9)])[0][1] == "9"
    assert _flatten_jira_comments([_comment("1", parentId=8)])[0][1] == "8"
    assert _flatten_jira_comments([_comment("1", parent={"id": 7})])[0][1] == "7"


def test_flatten_pulls_nested_replies_under_parent():
    flat = _flatten_jira_comments([_comment("1", replies=[_comment("2"), _comment("3")])])
    assert [(jc["id"], parent, idx) for jc, parent, idx in flat] == [
        ("1", None, 0),
        ("2", "1", None),
        ("3", "1", None),
    ]


@pytest.mark.django_db
def test_import_links_reply_to_parent(project, create_user):
    run_import(
        project,
        create_user,
        _issue_payload([_comment("100"), _comment("101", parentCommentId=100)]),
        dry_run=False,
    )

    parent = IssueComment.objects.get(external_id="100")
    reply = IssueComment.objects.get(external_id="101")
    assert parent.parent_id is None
    assert reply.parent_id == parent.id


@pytest.mark.django_db
def test_import_links_nested_replies(project, create_user):
    run_import(
        project,
        create_user,
        _issue_payload([_comment("100", replies=[_comment("101")])]),
        dry_run=False,
    )

    reply = IssueComment.objects.get(external_id="101")
    assert reply.parent_id == IssueComment.objects.get(external_id="100").id


@pytest.mark.django_db
def test_import_counts_replies_and_ignores_unknown_parents(project, create_user):
    result = run_import(
        project,
        create_user,
        _issue_payload([_comment("100"), _comment("101", parentCommentId=999)]),
        dry_run=False,
    )

    assert result["comments"] == 2
    # parent id that doesn't exist in Jira -> reply stays a top-level comment
    assert IssueComment.objects.get(external_id="101").parent_id is None
