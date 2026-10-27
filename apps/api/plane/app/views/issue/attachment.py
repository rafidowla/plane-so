# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
import uuid

# Django imports
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from django.http import HttpResponseRedirect

# Third Party imports
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser

# Module imports
from .. import BaseAPIView
from plane.app.serializers import IssueAttachmentSerializer
from plane.db.models import FileAsset, Issue, Workspace
from plane.bgtasks.issue_activities_task import issue_activity
from plane.app.permissions import allow_permission, issue_hidden_from_guest, ROLE
from plane.settings.storage import S3Storage
from plane.utils.path_validator import sanitize_filename
from plane.utils.attachment_mime import (
    SUPPORTED_ATTACHMENT_EXTENSIONS,
    resolve_attachment_type,
)
from plane.bgtasks.storage_metadata_task import get_asset_object_metadata
from plane.utils.host import base_host


class IssueAttachmentEndpoint(BaseAPIView):
    serializer_class = IssueAttachmentSerializer
    model = FileAsset
    parser_classes = (MultiPartParser, FormParser)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def post(self, request, slug, project_id, issue_id):
        # A restricted guest may only add attachments to issues they created,
        # mirroring the issue-detail visibility rule.
        if issue_hidden_from_guest(request, slug, project_id, issue_id):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = IssueAttachmentSerializer(data=request.data)
        workspace = Workspace.objects.get(slug=slug)
        # The URL issue must belong to the URL workspace + project (404 otherwise).
        Issue.objects.get(pk=issue_id, workspace=workspace, project_id=project_id)
        if serializer.is_valid():
            serializer.save(
                project_id=project_id,
                issue_id=issue_id,
                workspace_id=workspace.id,
                entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            )
            issue_activity.delay(
                type="attachment.activity.created",
                requested_data=None,
                actor_id=str(self.request.user.id),
                issue_id=str(self.kwargs.get("issue_id", None)),
                project_id=str(self.kwargs.get("project_id", None)),
                current_instance=json.dumps(serializer.data, cls=DjangoJSONEncoder),
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=base_host(request=request, is_app=True),
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], creator=True, model=FileAsset)
    def delete(self, request, slug, project_id, issue_id, pk):
        issue_attachment = FileAsset.objects.filter(
            pk=pk, workspace__slug=slug, project_id=project_id, issue_id=issue_id
        ).first()
        if not issue_attachment:
            return Response(
                {"error": "Issue attachment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        issue_attachment.asset.delete(save=False)
        issue_attachment.delete()
        issue_activity.delay(
            type="attachment.activity.deleted",
            requested_data=None,
            actor_id=str(self.request.user.id),
            issue_id=str(self.kwargs.get("issue_id", None)),
            project_id=str(self.kwargs.get("project_id", None)),
            current_instance=None,
            epoch=int(timezone.now().timestamp()),
            notification=True,
            origin=base_host(request=request, is_app=True),
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id):
        # A restricted guest may only see attachments of issues they created,
        # mirroring the issue-detail visibility rule.
        if issue_hidden_from_guest(request, slug, project_id, issue_id):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )
        issue_attachments = FileAsset.objects.filter(
            issue_id=issue_id,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            workspace__slug=slug,
            project_id=project_id,
        )
        serializer = IssueAttachmentSerializer(issue_attachments, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class IssueAttachmentV2Endpoint(BaseAPIView):
    serializer_class = IssueAttachmentSerializer
    model = FileAsset

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def post(self, request, slug, project_id, issue_id):
        # A restricted guest may only add attachments to issues they created,
        # mirroring the issue-detail visibility rule.
        if issue_hidden_from_guest(request, slug, project_id, issue_id):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )
        name = sanitize_filename(request.data.get("name")) or "unnamed"
        # FORK: attachment file types (#24) — fall back to the file name's
        # extension when the browser sends an empty/generic MIME type, and
        # name the supported extensions in the error.
        type = resolve_attachment_type(name, request.data.get("type") or "")
        size = int(request.data.get("size", settings.FILE_SIZE_LIMIT))

        if not type:
            return Response(
                {
                    "error": f"Unsupported file type. Supported files: {SUPPORTED_ATTACHMENT_EXTENSIONS}",
                    "status": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the workspace
        workspace = Workspace.objects.get(slug=slug)

        # The URL issue must belong to the URL workspace + project (404 otherwise).
        Issue.objects.get(pk=issue_id, workspace=workspace, project_id=project_id)

        # asset key
        asset_key = f"{workspace.id}/{uuid.uuid4().hex}-{name}"

        # Get the size limit
        size_limit = min(size, settings.FILE_SIZE_LIMIT)

        # Create a File Asset
        asset = FileAsset.objects.create(
            attributes={"name": name, "type": type, "size": size_limit},
            asset=asset_key,
            size=size_limit,
            workspace_id=workspace.id,
            created_by=request.user,
            issue_id=issue_id,
            project_id=project_id,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        )

        # Get the presigned URL
        storage = S3Storage(request=request)

        # Generate a presigned URL to share an S3 object
        presigned_url = storage.generate_presigned_post(object_name=asset_key, file_type=type, file_size=size_limit)

        # Return the presigned URL
        return Response(
            {
                "upload_data": presigned_url,
                "asset_id": str(asset.id),
                "attachment": IssueAttachmentSerializer(asset).data,
                "asset_url": asset.asset_url,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], creator=True, model=FileAsset)
    def delete(self, request, slug, project_id, issue_id, pk):
        issue_attachment = FileAsset.objects.get(
            pk=pk, workspace__slug=slug, project_id=project_id, issue_id=issue_id
        )
        issue_attachment.is_deleted = True
        issue_attachment.deleted_at = timezone.now()
        issue_attachment.save()

        issue_activity.delay(
            type="attachment.activity.deleted",
            requested_data=None,
            actor_id=str(self.request.user.id),
            issue_id=str(issue_id),
            project_id=str(project_id),
            current_instance=None,
            epoch=int(timezone.now().timestamp()),
            notification=True,
            origin=base_host(request=request, is_app=True),
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id, pk=None):
        # A restricted guest may only see attachments of issues they created,
        # mirroring the issue-detail visibility rule.
        if issue_hidden_from_guest(request, slug, project_id, issue_id):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if pk:
            # Get the asset
            asset = FileAsset.objects.get(id=pk, workspace__slug=slug, project_id=project_id, issue_id=issue_id)

            # Check if the asset is uploaded
            if not asset.is_uploaded:
                return Response(
                    {"error": "The asset is not uploaded.", "status": False},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            storage = S3Storage(request=request)
            # FORK: attachment-preview — `?disposition=inline` lets the in-app viewer
            # render the file (img/iframe) instead of forcing a download.
            disposition = "inline" if request.GET.get("disposition") == "inline" else "attachment"
            # SECURITY: script-capable types (SVG, JS, HTML, XML) served inline on the
            # app's own origin (default self-hosted USE_MINIO deployment) execute as a
            # same-origin document, enabling stored XSS. Force such types to always
            # download, regardless of the requested disposition. This is the
            # load-bearing enforcement — the frontend cannot opt back into inline.
            asset_mime_type = (asset.attributes.get("type") or "").split(";")[0].strip().lower()
            if asset_mime_type in settings.SCRIPT_CAPABLE_MIME_TYPES:
                disposition = "attachment"
            presigned_url = storage.generate_presigned_url(
                object_name=asset.asset.name,
                disposition=disposition,
                filename=asset.attributes.get("name"),
                # FORK: attachment-preview — upstream INFRA-1159: inline only for an allow-listed type, pinned as
                # the response Content-Type (the fork viewer's wider list: pdf/text/video/audio)
                content_type=asset_mime_type,
                inline_mime_types=settings.ATTACHMENT_PREVIEW_INLINE_MIME_TYPES,
            )
            return HttpResponseRedirect(presigned_url)

        # Get all the attachments
        issue_attachments = FileAsset.objects.filter(
            issue_id=issue_id,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            workspace__slug=slug,
            project_id=project_id,
            is_uploaded=True,
        )
        # Serialize the attachments
        serializer = IssueAttachmentSerializer(issue_attachments, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def patch(self, request, slug, project_id, issue_id, pk):
        # A restricted guest may only touch attachments of issues they created,
        # mirroring the issue-detail visibility rule.
        if issue_hidden_from_guest(request, slug, project_id, issue_id):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )
        issue_attachment = FileAsset.objects.get(
            pk=pk, workspace__slug=slug, project_id=project_id, issue_id=issue_id
        )
        serializer = IssueAttachmentSerializer(issue_attachment)

        # Send this activity only if the attachment is not uploaded before
        if not issue_attachment.is_uploaded:
            issue_activity.delay(
                type="attachment.activity.created",
                requested_data=None,
                actor_id=str(self.request.user.id),
                issue_id=str(self.kwargs.get("issue_id", None)),
                project_id=str(self.kwargs.get("project_id", None)),
                current_instance=json.dumps(serializer.data, cls=DjangoJSONEncoder),
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=base_host(request=request, is_app=True),
            )

            # Update the attachment — do NOT overwrite created_by; it is set at
            # creation time and must not be reassigned.
            issue_attachment.is_uploaded = True

        # Get the storage metadata
        if not issue_attachment.storage_metadata:
            get_asset_object_metadata.delay(str(issue_attachment.id))
        issue_attachment.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
