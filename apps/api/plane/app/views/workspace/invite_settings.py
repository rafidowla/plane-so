# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- workspace invitation settings and workspace-level resend (Part 2)."""

# Python imports
from datetime import timedelta

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.bgtasks.workspace_invitation_task import workspace_invitation
from plane.db.models import Workspace, WorkspaceInviteSettings, WorkspaceMemberInvite
from plane.utils import workspace_audit
from plane.utils.host import base_host
from plane.utils.invite_settings import (
    MAX_EXPIRY_DAYS,
    MIN_EXPIRY_DAYS,
    claim_resend_slot,
    get_invite_settings,
    is_pending,
    normalize_domains,
    renew_invite,
    settings_payload,
)

SETTING_FIELDS = ("invite_expiry_days", "allowed_email_domains", "project_admins_can_invite")


class WorkspaceInviteSettingsEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        return Response(settings_payload(get_invite_settings(workspace.id)), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug):
        data = request.data
        if not isinstance(data, dict):
            return Response({"error": "Invalid request"}, status=status.HTTP_400_BAD_REQUEST)

        errors = {}
        values = {}
        if "invite_expiry_days" in data:
            days = data["invite_expiry_days"]
            # bool is an int subclass: reject it explicitly
            if isinstance(days, bool) or not isinstance(days, int) or not MIN_EXPIRY_DAYS <= days <= MAX_EXPIRY_DAYS:
                errors["invite_expiry_days"] = [f"Enter a whole number of days from {MIN_EXPIRY_DAYS} to {MAX_EXPIRY_DAYS}."]
            else:
                values["invite_expiry_days"] = days
        if "allowed_email_domains" in data:
            try:
                values["allowed_email_domains"] = normalize_domains(data["allowed_email_domains"])
            except ValueError as e:
                errors["allowed_email_domains"] = [str(e)]
        if "project_admins_can_invite" in data:
            if not isinstance(data["project_admins_can_invite"], bool):
                errors["project_admins_can_invite"] = ["Must be true or false."]
            else:
                values["project_admins_can_invite"] = data["project_admins_can_invite"]
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        workspace = Workspace.objects.get(slug=slug)
        with transaction.atomic():
            row, _ = WorkspaceInviteSettings.objects.select_for_update().get_or_create(workspace=workspace)
            before = workspace_audit.invite_settings_state(row)
            for field, value in values.items():
                setattr(row, field, value)
            row.save()
            if "invite_expiry_days" in values:
                # A longer window can bring an already-logged expiry back to life; let the sweep log it
                # again if it expires a second time.
                WorkspaceMemberInvite.objects.filter(
                    workspace=workspace,
                    accepted=False,
                    responded_at__isnull=True,
                    expiry_recorded_at__isnull=False,
                    last_sent_at__gt=timezone.now() - timedelta(days=row.invite_expiry_days),
                ).update(expiry_recorded_at=None)
            workspace_audit.record_invite_settings_changed(
                workspace.id, request.user, before, workspace_audit.invite_settings_state(row)
            )
        return Response(settings_payload(row), status=status.HTTP_200_OK)


class WorkspaceInvitationResendEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, pk):
        with transaction.atomic():
            invite = (
                WorkspaceMemberInvite.objects.select_for_update()
                .filter(pk=pk, workspace__slug=slug)
                .first()
            )
            if invite is None:
                return Response({"error": "Invitation not found"}, status=status.HTTP_404_NOT_FOUND)
            if not is_pending(invite):
                return Response(
                    {"error": "This invitation has already been answered and can't be resent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not claim_resend_slot(invite.id):
                return Response(
                    {"error": "Please wait a minute before resending this invitation."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            renew_invite(invite)
            workspace_audit.record_invite_resent(invite.workspace_id, request.user, invite)

        workspace_invitation.delay(
            invite.email,
            invite.workspace_id,
            invite.token,
            base_host(request=request, is_app=True),
            request.user.email,
        )
        return Response({"message": "Invitation sent"}, status=status.HTTP_200_OK)
