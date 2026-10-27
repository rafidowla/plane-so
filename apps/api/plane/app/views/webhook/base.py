# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import IntegrityError, transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import Webhook, WebhookLog, Workspace
from plane.db.models import WorkspaceAuditAction, WorkspaceAuditTarget  # FORK: PSR-125
from plane.utils import workspace_audit  # FORK: PSR-125
from plane.db.models.webhook import generate_token
from ..base import BaseAPIView
from plane.app.permissions import allow_permission, allow_workspace_super_admin, ROLE  # FORK: PSR-125
from plane.app.serializers import WebhookSerializer, WebhookLogSerializer


def _audit(request, webhook, action, before=None, after=None):
    """FORK: PSR-125 -- audit log entry for a webhook change. Never includes ``secret_key``."""
    workspace_audit.record(
        webhook.workspace_id,
        request.user,
        action,
        WorkspaceAuditTarget.WEBHOOK,
        target_id=webhook.id,
        target_label=workspace_audit.redact_url(webhook.url),
        before=before,
        after=after,
    )


class WebhookEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    @allow_workspace_super_admin  # FORK: PSR-125
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        try:
            serializer = WebhookSerializer(
                data=request.data,
                context={"request": request, "show_secret_key": True},
            )
            if serializer.is_valid():
                with transaction.atomic():
                    webhook = serializer.save(workspace_id=workspace.id)
                    after = workspace_audit.webhook_state(webhook)
                    _audit(request, webhook, WorkspaceAuditAction.WEBHOOK_CREATED, after=after)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            if "already exists" in str(e):
                return Response(
                    {"error": "URL already exists for the workspace"},
                    status=status.HTTP_409_CONFLICT,
                )
            raise IntegrityError

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, pk=None):
        if pk is None:
            webhooks = Webhook.objects.filter(workspace__slug=slug)
            serializer = WebhookSerializer(
                webhooks,
                fields=(
                    "id",
                    "url",
                    "is_active",
                    "created_at",
                    "updated_at",
                    "project",
                    "issue",
                    "cycle",
                    "module",
                    "issue_comment",
                ),
                many=True,
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            webhook = Webhook.objects.get(workspace__slug=slug, pk=pk)
            serializer = WebhookSerializer(
                webhook,
                fields=(
                    "id",
                    "url",
                    "is_active",
                    "created_at",
                    "updated_at",
                    "project",
                    "issue",
                    "cycle",
                    "module",
                    "issue_comment",
                ),
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    @allow_workspace_super_admin  # FORK: PSR-125
    def patch(self, request, slug, pk):
        webhook = Webhook.objects.get(workspace__slug=slug, pk=pk)
        serializer = WebhookSerializer(
            webhook,
            data=request.data,
            context={"request": request},
            partial=True,
            fields=(
                "id",
                "url",
                "is_active",
                "created_at",
                "updated_at",
                "project",
                "issue",
                "cycle",
                "module",
                "issue_comment",
            ),
        )
        if serializer.is_valid():
            before = workspace_audit.webhook_state(webhook)
            with transaction.atomic():
                webhook = serializer.save()
                changed_before, changed_after = workspace_audit.diff(before, workspace_audit.webhook_state(webhook))
                if changed_after:
                    _audit(request, webhook, WorkspaceAuditAction.WEBHOOK_UPDATED, changed_before, changed_after)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    @allow_workspace_super_admin  # FORK: PSR-125
    def delete(self, request, slug, pk):
        webhook = Webhook.objects.get(pk=pk, workspace__slug=slug)
        with transaction.atomic():
            before = workspace_audit.webhook_state(webhook)
            webhook.delete()
            _audit(request, webhook, WorkspaceAuditAction.WEBHOOK_DELETED, before=before)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookSecretRegenerateEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    @allow_workspace_super_admin  # FORK: PSR-125
    def post(self, request, slug, pk):
        webhook = Webhook.objects.get(workspace__slug=slug, pk=pk)
        webhook.secret_key = generate_token()
        with transaction.atomic():
            webhook.save()
            _audit(request, webhook, WorkspaceAuditAction.WEBHOOK_SECRET_REGENERATED)  # the secret is never logged
        serializer = WebhookSerializer(webhook, context={"show_secret_key": True})
        return Response(serializer.data, status=status.HTTP_200_OK)


class WebhookLogsEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, webhook_id):
        webhook_logs = WebhookLog.objects.filter(workspace__slug=slug, webhook=webhook_id)
        serializer = WebhookLogSerializer(webhook_logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
