# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for ``ProjectBulkAssetEndpoint`` (FORK: PSR-104).

The work item create modal uploads editor images before the work item exists and
links them with the bulk endpoint after create. The editor binds its upload handler
to the project selected when it mounted, so after the user switches project the image
is uploaded into the *original* project; the link call then targets the new project.
That used to 404 ("The requested asset could not be found") and the image was lost.

Unattached description images uploaded by the caller may be re-homed into the target
project; everything else keeps the SECUR-242 scoping.
"""

from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import FileAsset, Issue, Project, ProjectMember, State, User


def _make_project(workspace, user, identifier):
    project = Project.objects.create(
        name=f"Project {identifier}", identifier=identifier, workspace=workspace, created_by=user
    )
    ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=20)
    return project


@pytest.fixture
def project_a(db, workspace, create_user):
    return _make_project(workspace, create_user, "PAA")


@pytest.fixture
def project_b(db, workspace, create_user):
    return _make_project(workspace, create_user, "PBB")


def _make_issue(workspace, project, user, name="Issue"):
    state = State.objects.create(name="Todo", project=project, workspace=workspace, group="unstarted")
    return Issue.objects.create(name=name, project=project, workspace=workspace, state=state, created_by=user)


def _make_asset(workspace, project, user, **kwargs):
    # BaseModel.save() stamps created_by from the request user (None outside a request),
    # so pass it explicitly.
    asset = FileAsset(
        attributes={"name": "pic.png", "type": "image/png", "size": 10},
        asset=f"{workspace.id}/{uuid4().hex}-pic.png",
        size=10,
        workspace=workspace,
        project=project,
        entity_type=kwargs.pop("entity_type", FileAsset.EntityTypeContext.ISSUE_DESCRIPTION),
        is_uploaded=True,
        **kwargs,
    )
    asset.save(created_by_id=user.id)
    return asset


def bulk_url(slug, project_id, entity_id):
    return f"/api/assets/v2/workspaces/{slug}/projects/{project_id}/{entity_id}/bulk/"


@pytest.mark.contract
class TestBulkAssetRehome:
    @pytest.mark.django_db
    def test_same_project_description_asset_is_linked(self, session_client, workspace, project_a, create_user):
        issue = _make_issue(workspace, project_a, create_user)
        asset = _make_asset(workspace, project_a, create_user)

        response = session_client.post(
            bulk_url(workspace.slug, project_a.id, issue.id), {"asset_ids": [str(asset.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        asset.refresh_from_db()
        assert asset.issue_id == issue.id
        assert asset.project_id == project_a.id

    @pytest.mark.django_db
    def test_unattached_description_asset_from_other_project_is_rehomed(
        self, session_client, workspace, project_a, project_b, create_user
    ):
        """Image uploaded while project A was selected, work item saved in project B."""
        issue = _make_issue(workspace, project_b, create_user)
        asset = _make_asset(workspace, project_a, create_user)

        response = session_client.post(
            bulk_url(workspace.slug, project_b.id, issue.id), {"asset_ids": [str(asset.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
        asset.refresh_from_db()
        assert asset.issue_id == issue.id
        assert asset.project_id == project_b.id

    @pytest.mark.django_db
    def test_attached_asset_from_other_project_is_not_moved(
        self, session_client, workspace, project_a, project_b, create_user
    ):
        """An image already attached to a project A work item must stay there."""
        issue_a = _make_issue(workspace, project_a, create_user, name="A")
        issue_b = _make_issue(workspace, project_b, create_user, name="B")
        asset = _make_asset(workspace, project_a, create_user, issue=issue_a)

        response = session_client.post(
            bulk_url(workspace.slug, project_b.id, issue_b.id), {"asset_ids": [str(asset.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        asset.refresh_from_db()
        assert asset.issue_id == issue_a.id
        assert asset.project_id == project_a.id

    @pytest.mark.django_db
    def test_other_users_asset_is_not_linked(self, session_client, workspace, project_a, project_b, create_user):
        other = User.objects.create(email=f"o-{uuid4().hex[:6]}@plane.so", username=f"o_{uuid4().hex[:6]}")
        issue = _make_issue(workspace, project_b, create_user)
        asset = _make_asset(workspace, project_a, other)

        response = session_client.post(
            bulk_url(workspace.slug, project_b.id, issue.id), {"asset_ids": [str(asset.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        asset.refresh_from_db()
        assert asset.issue_id is None
        assert asset.project_id == project_a.id

    @pytest.mark.django_db
    def test_non_description_asset_from_other_project_is_not_rehomed(
        self, session_client, workspace, project_a, project_b, create_user
    ):
        issue = _make_issue(workspace, project_b, create_user)
        asset = _make_asset(workspace, project_a, create_user, entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT)

        response = session_client.post(
            bulk_url(workspace.slug, project_b.id, issue.id), {"asset_ids": [str(asset.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        asset.refresh_from_db()
        assert asset.project_id == project_a.id
