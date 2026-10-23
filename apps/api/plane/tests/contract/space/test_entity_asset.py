# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the public deploy-board ``EntityAssetEndpoint`` GET.

Regression coverage for the SVG stored-XSS bypass: this endpoint is
``AllowAny`` (published project/issue boards need no login) and serves
``ISSUE_DESCRIPTION`` / ``COMMENT_DESCRIPTION`` assets, which the Jira
importer can populate with an unchecked ``mimeType``. It minted a bare
presigned URL with no disposition argument at all — defaulting to
``inline`` — so an SVG landing here via Jira import was served as an
active document to a fully unauthenticated visitor.
"""

from unittest import mock

import pytest
from rest_framework import status

from plane.db.models import DeployBoard, FileAsset, Project


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(name="Board Project", identifier="BRD", workspace=workspace, created_by=create_user)


@pytest.fixture
def published_board(db, workspace, project):
    """A published project deploy board, reachable with no auth via its anchor.

    Upstream INFRA-1146 scopes the asset lookup to ``deploy_board.project_id``, so the
    board and the asset must both belong to a project.
    """
    return DeployBoard.objects.create(
        workspace=workspace,
        project=project,
        entity_name="project",
        entity_identifier=project.id,
        anchor="test-anchor-12345",
    )


def _asset(workspace, user, *, name, content_type, project):
    return FileAsset.objects.create(
        attributes={"name": name, "type": content_type, "size": 1024},
        asset=f"{workspace.id}/{name}",
        size=1024,
        workspace=workspace,
        project=project,
        created_by=user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
        is_uploaded=True,
        storage_metadata={"size": 1024},
    )


def _url(anchor, asset_id):
    return f"/api/public/assets/v2/anchor/{anchor}/{asset_id}/"


@pytest.mark.contract
class TestEntityAssetDisposition:
    """An unauthenticated visitor to a published board must never receive an
    inline URL for an SVG-typed asset."""

    @staticmethod
    def _presign_params(api_client, url):
        """GET ``url`` and return the ``Params`` S3Storage handed to boto3's ``generate_presigned_url``.

        Upstream INFRA-1159 moved the inline/attachment decision into ``S3Storage.generate_presigned_url``
        (the view always asks for ``inline``), so the effective disposition is asserted on the boto3 call.
        """
        with mock.patch("plane.settings.storage.boto3.client") as mock_client:
            mock_client.return_value.generate_presigned_url.return_value = "https://signed.example/asset"
            response = api_client.get(url)
        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        _, kwargs = mock_client.return_value.generate_presigned_url.call_args
        return kwargs["Params"]

    @pytest.mark.django_db
    def test_svg_asset_is_forced_to_attachment(self, api_client, workspace, create_user, project, published_board):
        asset = _asset(workspace, create_user, name="evil.svg", content_type="image/svg+xml", project=project)

        params = self._presign_params(api_client, _url(published_board.anchor, asset.id))

        assert params["ResponseContentDisposition"].startswith("attachment;"), (
            f"SVG must be forced to download, got {params['ResponseContentDisposition']!r}"
        )
        assert "ResponseContentType" not in params

    @pytest.mark.django_db
    def test_png_asset_stays_inline(self, api_client, workspace, create_user, project, published_board):
        """Positive control: a legitimate inline image still previews inline, with its type pinned."""
        asset = _asset(workspace, create_user, name="shot.png", content_type="image/png", project=project)

        params = self._presign_params(api_client, _url(published_board.anchor, asset.id))

        assert params["ResponseContentDisposition"].startswith("inline;"), (
            f"PNG must stay inline, got {params['ResponseContentDisposition']!r}"
        )
        assert params["ResponseContentType"] == "image/png"
