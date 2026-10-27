# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: ``attachment_count`` must only count finished uploads.

An attachment whose presigned upload never completed (``is_uploaded=False``) is
hidden from the attachment list, so it must not inflate the count badge on the
work item list / detail either.
"""

import pytest
from rest_framework import status

from plane.db.models import FileAsset, Issue, Project, ProjectIdentifier, ProjectMember, State


@pytest.fixture
def issue_with_assets(db, create_user, workspace):
    project = Project.objects.create(workspace=workspace, name="Count", identifier="CNT", created_by=create_user)
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="CNT")
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20, is_active=True)
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    issue = Issue(workspace=workspace, project=project, name="Issue", state=state)
    issue.save(created_by_id=create_user.id)
    for name, uploaded in (("done.png", True), ("failed.png", False)):
        FileAsset.objects.create(
            attributes={"name": name, "type": "image/png", "size": 1024},
            asset=f"{workspace.id}/{name}",
            size=1024,
            workspace=workspace,
            project=project,
            issue=issue,
            created_by=create_user,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_uploaded=uploaded,
        )
    return issue


@pytest.mark.contract
@pytest.mark.django_db
def test_issue_detail_attachment_count_ignores_unfinished_uploads(session_client, workspace, issue_with_assets):
    issue = issue_with_assets
    response = session_client.get(f"/api/workspaces/{workspace.slug}/projects/{issue.project_id}/issues/{issue.id}/")
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["attachment_count"] == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_issue_list_attachment_count_ignores_unfinished_uploads(session_client, workspace, issue_with_assets):
    issue = issue_with_assets
    response = session_client.get(f"/api/workspaces/{workspace.slug}/projects/{issue.project_id}/issues/")
    assert response.status_code == status.HTTP_200_OK, response.data
    rows = response.data["results"] if isinstance(response.data, dict) else response.data
    row = next(r for r in rows if str(r["id"]) == str(issue.id))
    assert row["attachment_count"] == 1
