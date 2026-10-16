# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — shared fixtures/helpers for the S2 contract tests."""

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueAssignee,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    TimeTrackingSetting,
    User,
    WorkspaceMember,
)

DHAKA = "Asia/Dhaka"  # UTC+6, no DST


@pytest.fixture(autouse=True)
def _load_urlconf():
    """Import the URLconf before any freeze_time block (see the S1 tests)."""
    from django.urls import get_resolver

    get_resolver().url_patterns


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


def make_project(workspace, owner, name, identifier, tracked=True):
    project = Project.objects.create(
        workspace=workspace, name=name, identifier=identifier, created_by=owner, is_time_tracking_enabled=tracked
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name=identifier)
    states = {
        "backlog": State.objects.create(workspace=workspace, project=project, name="Backlog", group="backlog"),
        "todo": State.objects.create(
            workspace=workspace, project=project, name="Todo", group="unstarted", default=True
        ),
        "in_progress": State.objects.create(
            workspace=workspace, project=project, name="In Progress", group="started", sequence=10
        ),
        "review": State.objects.create(
            workspace=workspace, project=project, name="In Review", group="started", sequence=20
        ),
        "done": State.objects.create(workspace=workspace, project=project, name="Done", group="completed"),
        "cancelled": State.objects.create(workspace=workspace, project=project, name="Cancelled", group="cancelled"),
    }
    return project, states


def make_issue(project, state, name, creator, assignees=()):
    issue = Issue(workspace=project.workspace, project=project, name=name, state=state)
    issue.save(created_by_id=creator.id)
    for user in assignees:
        IssueAssignee.objects.create(workspace=project.workspace, project=project, issue=issue, assignee=user)
    return issue


def make_user(workspace, ws_role=15, name="Mem"):
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(
        email=f"s2-{uid}@plane.so", username=f"s2_{uid}", first_name=name, display_name=f"{name.lower()}_{uid}"
    )
    if ws_role is not None:
        WorkspaceMember.objects.create(workspace=workspace, member=u, role=ws_role, is_active=True)
    return u


def join(project, user, role=15):
    ProjectMember.objects.create(project=project, workspace=project.workspace, member=user, role=role, is_active=True)


def client_for(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def set_settings(workspace, **fields):
    # Call inside the freeze_time block that precedes the timer start: the row's
    # updated_at re-bases the "still working?" check (limits.idle_base).
    TimeTrackingSetting.objects.update_or_create(workspace=workspace, defaults=fields)


def issue_timer_url(s, issue=None, project=None):
    issue = issue or s["issue"]
    project = project or issue.project
    return f"/api/workspaces/{s['slug']}/projects/{project.id}/issues/{issue.id}/worklog-timer/"


def ws_url(s, path):
    return f"/api/workspaces/{s['slug']}/{path}"
