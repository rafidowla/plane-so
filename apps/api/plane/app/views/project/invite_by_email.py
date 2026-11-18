# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- add people to a project by email (Part 1).

Existing workspace members are added straight away. For anyone else a workspace Admin creates (or
reuses) ONE pending workspace invitation per email and adds a pending project spot for this project;
the spot becomes a project membership when the invitation is accepted.
"""

# Python imports
from datetime import datetime

import jwt

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.cache import cache
from django.db import IntegrityError, transaction

# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.bgtasks.project_add_user_email_task import project_add_user_email
from plane.bgtasks.workspace_invitation_task import workspace_invitation
from plane.db.models import (
    Project,
    ProjectMember,
    User,
    WorkspaceMember,
    WorkspaceMemberInvite,
    WorkspaceMemberInviteProject,
)
from plane.utils.cache import invalidate_cache_directly
from plane.utils.host import base_host
from plane.utils.project_invite_spots import (
    cap_project_role,
    remove_project_spots,
    upsert_project_member,
)

MAX_EMAILS_PER_CALL = 50
MAX_MESSAGE_LENGTH = 1000
RESEND_COOLDOWN_SECONDS = 60
PROJECT_ROLES = (5, 15, 20)
WORKSPACE_ROLES_FOR_NEW_PEOPLE = (5, 15)
NOT_ALLOWED_DETAIL = "Only workspace admins can invite people who aren't in the workspace yet."


def _is_workspace_admin(user, slug):
    return WorkspaceMember.objects.filter(
        member=user, workspace__slug=slug, role=ROLE.ADMIN.value, is_active=True
    ).exists()


def _invite_link(request, invite, slug):
    return (
        f"{base_host(request=request, is_app=True)}/workspace-invitations/"
        f"?invitation_id={invite.id}&slug={slug}&token={invite.token}"
    )


def _spots_for_project(slug, project_id):
    """Pending spots of a project: invitation not yet answered (and not cancelled)."""
    return WorkspaceMemberInviteProject.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        invite__deleted_at__isnull=True,
        invite__accepted=False,
        invite__responded_at__isnull=True,
    ).select_related("invite", "created_by")


def _upsert_spot(invite, project, role):
    spot = WorkspaceMemberInviteProject.objects.filter(invite=invite, project=project).first()
    if spot is None:
        WorkspaceMemberInviteProject.objects.create(
            invite=invite, project=project, workspace_id=project.workspace_id, role=role
        )
    else:
        spot.role = role
        spot.save()


def _invite_and_spot(project, email, role, workspace_role, user):
    """Reuse (or create) the workspace invitation for ``email`` and add this project's spot."""
    invite = WorkspaceMemberInvite.objects.filter(workspace_id=project.workspace_id, email__iexact=email).first()
    if invite is not None and invite.responded_at is not None and not invite.accepted:
        # a declined invitation can never be accepted again: replace it
        remove_project_spots(invite)
        invite.delete()
        invite = None

    created = invite is None
    if created:
        invite = WorkspaceMemberInvite.objects.create(
            workspace_id=project.workspace_id,
            email=email,
            token=jwt.encode(
                {"email": email, "timestamp": datetime.now().timestamp()},
                settings.SECRET_KEY,
                algorithm="HS256",
            ),
            role=workspace_role,
            created_by=user,
            created_from_project=True,
        )
    _upsert_spot(invite, project, role)
    return invite, created


class ProjectInviteByEmailEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        data = request.data
        emails = data.get("emails")
        if not isinstance(emails, list) or not emails:
            return Response({"error": "At least one email is required"}, status=status.HTTP_400_BAD_REQUEST)
        if len(emails) > MAX_EMAILS_PER_CALL:
            return Response(
                {"error": f"You can invite at most {MAX_EMAILS_PER_CALL} people at once"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace_role = data.get("workspace_role", ROLE.MEMBER.value)
        if workspace_role not in WORKSPACE_ROLES_FOR_NEW_PEOPLE:
            return Response(
                {"error": "workspace_role must be 5 (Guest) or 15 (Member)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = data.get("message")
        if message is not None:
            if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
                return Response(
                    {"error": f"message must be text of at most {MAX_MESSAGE_LENGTH} characters"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            message = message.strip() or None

        project = Project.objects.get(pk=project_id, workspace__slug=slug)
        is_ws_admin = _is_workspace_admin(request.user, slug)
        host = base_host(request=request, is_app=True)

        results = []
        added_project_member_ids = []
        invites_to_send = []
        with transaction.atomic():
            for entry in emails:
                raw = entry.get("email") if isinstance(entry, dict) else None
                role = entry.get("role", ROLE.MEMBER.value) if isinstance(entry, dict) else None
                email = raw.strip().lower() if isinstance(raw, str) else ""
                try:
                    if not email or len(email) > 255:
                        raise ValidationError("invalid")
                    validate_email(email)
                except ValidationError:
                    results.append({"email": email or str(raw or ""), "status": "invalid", "detail": "Invalid email"})
                    continue
                if role not in PROJECT_ROLES:
                    results.append({"email": email, "status": "invalid", "detail": "Invalid project role"})
                    continue

                user = User.objects.filter(email__iexact=email, is_bot=False).first()
                ws_member = (
                    WorkspaceMember.objects.filter(workspace__slug=slug, member=user, is_active=True).first()
                    if user
                    else None
                )

                if user and ProjectMember.objects.filter(project=project, member=user, is_active=True).exists():
                    results.append({"email": email, "status": "already_member"})
                    continue

                if ws_member is not None:
                    # A workspace Admin is always a project Admin; a workspace Guest is capped to Guest.
                    final_role = cap_project_role(role, ws_member.role)
                    pm = upsert_project_member(project, user, final_role, created_by_id=request.user.id)
                    added_project_member_ids.append(pm.id)
                    results.append({"email": email, "status": "added"})
                    continue

                if not is_ws_admin:
                    results.append({"email": email, "status": "not_allowed", "detail": NOT_ALLOWED_DETAIL})
                    continue

                # One savepoint per email: a concurrent request creating the same invite/spot must not
                # fail (or roll back) the rest of the batch.
                try:
                    with transaction.atomic():
                        invite, created = _invite_and_spot(project, email, role, workspace_role, request.user)
                except IntegrityError:
                    created = False
                    # only a still-pending invitation may receive the spot (never a declined/accepted one)
                    invite = WorkspaceMemberInvite.objects.filter(
                        workspace_id=project.workspace_id,
                        email__iexact=email,
                        accepted=False,
                        responded_at__isnull=True,
                    ).first()
                    if invite is None:
                        results.append({"email": email, "status": "invalid", "detail": "Please try again."})
                        continue
                    try:
                        with transaction.atomic():
                            _upsert_spot(invite, project, role)
                    except IntegrityError:
                        pass  # the concurrent request already created the spot

                if created:
                    invites_to_send.append(invite)
                results.append({"email": email, "status": "invited" if created else "invite_updated"})

        # Side effects only once the data is committed
        for invite in invites_to_send:
            workspace_invitation.delay(
                invite.email, project.workspace_id, invite.token, host, request.user.email, message
            )
        for pm_id in added_project_member_ids:
            project_add_user_email.delay(host, pm_id, request.user.id)
        if added_project_member_ids:
            invalidate_cache_directly(
                path=f"/api/workspaces/{slug}/projects/{project_id}/members/",
                url_params=False,
                user=False,
                multiple=True,
            )

        return Response({"results": results}, status=status.HTTP_200_OK)


class ProjectPendingMembersEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        is_ws_admin = _is_workspace_admin(request.user, slug)
        payload = []
        for spot in _spots_for_project(slug, project_id).order_by("created_at"):
            item = {
                "id": spot.id,
                "invite_id": spot.invite_id,
                "email": spot.invite.email,
                "role": spot.role,
                # the project role the person will actually get on accept (Guest cap, Admin rule)
                "effective_role": cap_project_role(spot.role, spot.invite.role),
                "workspace_role": spot.invite.role,
                "created_at": spot.created_at,
                "invited_by": (
                    {"id": spot.created_by_id, "display_name": spot.created_by.display_name}
                    if spot.created_by_id
                    else None
                ),
            }
            if is_ws_admin:
                item["invite_link"] = _invite_link(request, spot.invite, slug)
            payload.append(item)
        return Response(payload, status=status.HTTP_200_OK)


class ProjectPendingMemberDetailEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, pk):
        spot = _spots_for_project(slug, project_id).filter(pk=pk).first()
        if spot is None:
            return Response({"error": "Pending invitation not found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            invite = spot.invite
            WorkspaceMemberInviteProject.objects.filter(pk=spot.pk).delete()  # soft delete, immediate
            remaining = WorkspaceMemberInviteProject.objects.filter(invite=invite).exists()
            # The workspace invitation only goes away when this flow created it and no spot is left.
            if invite.created_from_project and not remaining:
                invite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectPendingMemberResendEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id, pk):
        spot = _spots_for_project(slug, project_id).filter(pk=pk).first()
        if spot is None:
            return Response({"error": "Pending invitation not found"}, status=status.HTTP_404_NOT_FOUND)
        if not _is_workspace_admin(request.user, slug):
            return Response(
                {"error": "Only workspace admins can resend invitations."}, status=status.HTTP_403_FORBIDDEN
            )

        invite = spot.invite
        # Per-invite cooldown; cache.add is atomic, so a double click sends only once.
        if not cache.add(f"psr124:resend-invite:{invite.id}", 1, RESEND_COOLDOWN_SECONDS):
            return Response(
                {"error": "Please wait a minute before resending this invitation."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        workspace_invitation.delay(
            invite.email,
            invite.workspace_id,
            invite.token,
            base_host(request=request, is_app=True),
            request.user.email,
        )
        return Response({"message": "Invitation sent"}, status=status.HTTP_200_OK)
