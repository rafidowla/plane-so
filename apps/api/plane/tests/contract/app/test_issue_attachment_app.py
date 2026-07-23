# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the issue-attachment download endpoint's
``Content-Disposition`` handling.

Regression coverage for the stored-XSS hole in the fork's inline attachment
preview: an SVG served with ``Content-Disposition: inline`` from the app's own
origin (the default self-hosted ``USE_MINIO`` deployment) is rendered as an
active same-origin document and executes any embedded ``<script>``. The
download endpoint must therefore force SVG to ``attachment`` disposition even
when ``?disposition=inline`` is requested, while still honouring inline for
legitimate previewable images such as PNG.
"""

from unittest import mock

import pytest
from rest_framework import status

from plane.db.models import (
    FileAsset,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    Issue,
)


@pytest.fixture
def attachment_ctx(db, create_user, workspace):
    """A project (test user as admin) with one issue, ready to attach files to."""
    project = Project.objects.create(
        workspace=workspace,
        name="Attachment Project",
        identifier="ATT",
        created_by=create_user,
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="ATT")
    ProjectMember.objects.create(project=project, workspace=workspace, member=create_user, role=20, is_active=True)
    state = State.objects.create(
        workspace=workspace, project=project, name="Todo", group="unstarted", default=True
    )
    issue = Issue(workspace=workspace, project=project, name="Issue 1", state=state)
    issue.save(created_by_id=create_user.id)
    return {"project": project, "issue": issue, "user": create_user, "workspace": workspace}


def _asset(ctx, *, name, content_type):
    """Create an uploaded issue-attachment FileAsset of the given MIME type."""
    return FileAsset.objects.create(
        attributes={"name": name, "type": content_type, "size": 1024},
        asset=f"{ctx['workspace'].id}/{name}",
        size=1024,
        workspace=ctx["workspace"],
        project=ctx["project"],
        issue=ctx["issue"],
        created_by=ctx["user"],
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=True,
        storage_metadata={"size": 1024},
    )


def _download_url(ctx, asset_id):
    return (
        f"/api/assets/v2/workspaces/{ctx['workspace'].slug}"
        f"/projects/{ctx['project'].id}/issues/{ctx['issue'].id}/attachments/{asset_id}/"
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestIssueAttachmentDisposition:
    """The download endpoint must never mint an inline URL for an SVG."""

    def test_svg_inline_request_is_forced_to_attachment(self, session_client, attachment_ctx):
        """?disposition=inline on an SVG must be overridden to attachment."""
        asset = _asset(attachment_ctx, name="evil.svg", content_type="image/svg+xml")
        url = _download_url(attachment_ctx, asset.id) + "?disposition=inline"

        with mock.patch("plane.app.views.issue.attachment.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/evil.svg"
            response = session_client.get(url)

        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        mock_storage.return_value.generate_presigned_url.assert_called_once()
        _, kwargs = mock_storage.return_value.generate_presigned_url.call_args
        assert kwargs["disposition"] == "attachment", (
            f"SVG must be forced to download, got disposition={kwargs['disposition']!r}"
        )

    def test_png_inline_request_stays_inline(self, session_client, attachment_ctx):
        """Regression guard: a legitimate PNG must still preview inline."""
        asset = _asset(attachment_ctx, name="shot.png", content_type="image/png")
        url = _download_url(attachment_ctx, asset.id) + "?disposition=inline"

        with mock.patch("plane.app.views.issue.attachment.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/shot.png"
            response = session_client.get(url)

        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        mock_storage.return_value.generate_presigned_url.assert_called_once()
        _, kwargs = mock_storage.return_value.generate_presigned_url.call_args
        assert kwargs["disposition"] == "inline", (
            f"PNG inline preview must be preserved, got disposition={kwargs['disposition']!r}"
        )

    def test_svg_without_disposition_param_still_downloads(self, session_client, attachment_ctx):
        """Default (no param) already yields attachment; the override keeps it so."""
        asset = _asset(attachment_ctx, name="plain.svg", content_type="image/svg+xml")
        url = _download_url(attachment_ctx, asset.id)

        with mock.patch("plane.app.views.issue.attachment.S3Storage") as mock_storage:
            mock_storage.return_value.generate_presigned_url.return_value = "https://signed.example/plain.svg"
            response = session_client.get(url)

        assert response.status_code == status.HTTP_302_FOUND, f"Got {response.status_code}: {response.data!r}"
        _, kwargs = mock_storage.return_value.generate_presigned_url.call_args
        assert kwargs["disposition"] == "attachment"
