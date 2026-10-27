# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: the ``issue_attachments`` expand must hide failed/abandoned uploads.

The attachment list endpoint filters ``is_uploaded=True``, but the
DynamicBaseSerializer ``issue_attachments`` block did not, so an upload whose
presigned PUT never completed still showed up (broken) on the work item.
"""

import pytest

from plane.app.serializers import IssueSerializer
from plane.db.models import FileAsset, Issue, Project, ProjectIdentifier, State


def _asset(issue, user, *, name, is_uploaded):
    return FileAsset.objects.create(
        attributes={"name": name, "type": "image/png", "size": 1024},
        asset=f"{issue.workspace_id}/{name}",
        size=1024,
        workspace=issue.workspace,
        project=issue.project,
        issue=issue,
        created_by=user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=is_uploaded,
    )


@pytest.mark.unit
@pytest.mark.django_db
def test_issue_attachments_expand_excludes_unfinished_uploads(create_user, workspace):
    project = Project.objects.create(workspace=workspace, name="Expand", identifier="EXP", created_by=create_user)
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="EXP")
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    issue = Issue(workspace=workspace, project=project, name="Issue", state=state)
    issue.save(created_by_id=create_user.id)

    done = _asset(issue, create_user, name="done.png", is_uploaded=True)
    _asset(issue, create_user, name="failed.png", is_uploaded=False)

    data = IssueSerializer(issue, expand=["issue_attachments"]).data

    assert [str(a["id"]) for a in data["issue_attachments"]] == [str(done.id)]
