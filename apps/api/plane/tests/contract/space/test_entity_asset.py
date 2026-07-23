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

from plane.db.models import DeployBoard, FileAsset, Workspace, WorkspaceMember


@pytest.fixture
def published_board(db, workspace, create_user):
    """A published project deploy board, reachable with no auth via its anchor."""
    return DeployBoard.objects.create(
        workspace=workspace,
        entity_name="project",
        anchor="test-anchor-12345",
    )


def _asset(workspace, user, *, name, content_type):
    return FileAsset.objects.create(
        attributes={"name": name, "type": content_type, "size": 1024},
        asset=f"{workspace.id}/{name}",
        size=1024,
        workspace=workspace,
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

    @pytest.mark.django_db
    def test_svg_asset_is_forced_to_attachment(self, api_client, workspace, create_user, published_board):
        asset = _asset(workspace, create_user, name="evil.svg", content_type="image/svg+xml")
        url = _url(published_board.anchor, asset.id)

        with mock.patch("plane.space.views.asset.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/evil.svg"
            response = api_client.get(url)

        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        _, kwargs = mock_storage.return_value.generate_presigned_url.call_args
        assert kwargs["disposition"] == "attachment", (
            f"SVG must be forced to download, got disposition={kwargs['disposition']!r}"
        )

    @pytest.mark.django_db
    def test_png_asset_stays_inline(self, api_client, workspace, create_user, published_board):
        """Positive control: a legitimate inline image still previews inline."""
        asset = _asset(workspace, create_user, name="shot.png", content_type="image/png")
        url = _url(published_board.anchor, asset.id)

        with mock.patch("plane.space.views.asset.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/shot.png"
            response = api_client.get(url)

        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        _, kwargs = mock_storage.return_value.generate_presigned_url.call_args
        assert kwargs["disposition"] == "inline", (
            f"PNG must stay inline, got disposition={kwargs['disposition']!r}"
        )
