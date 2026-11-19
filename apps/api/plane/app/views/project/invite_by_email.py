# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- add people to a project by email (Part 1, Part 2).

Existing workspace members are added straight away. For anyone else a workspace Admin creates (or
reuses) ONE pending workspace invitation per email and adds a pending project spot for this project;
the spot becomes a project membership when the invitation is accepted.

Part 2: a Project Admin who is not a workspace Admin may also invite new emails when the workspace
setting allows it and the domain is allowed; invitations expire; every step is written to the audit log.
"""

# Python imports
from datetime import datetime

import jwt

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

# Third Party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission, is_workspace_super_admin, super_admin_forbidden
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
from plane.utils import workspace_audit
from plane.utils.cache import invalidate_cache_directly
from plane.utils.host import base_host
from plane.utils.invite_settings import (
    InviteExpiryCache,
    claim_resend_slot,
    get_invite_settings,
    invite_not_allowed_detail,
    is_invite_expired,
    is_pending,
    release_resend_slot,
    renew_invite,
)
from plane.utils.project_invite_spots import (
    cap_project_role,
    remove_project_spots,
    upsert_project_member,
)

MAX_EMAILS_PER_CALL = 50
MAX_MESSAGE_LENGTH = 1000
PROJECT_ROLES = (5, 15, 20)
WORKSPACE_ROLES_FOR_NEW_PEOPLE = (5, 15)
# FORK: PSR-124 -- ``detail`` of a ``not_allowed`` result is a code the web turns into a message:
# "workspace_admin_only" (setting off / not a project admin) or "domain_not_allowed".


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
    ).select_related("invite", "created_by", "project")


def _upsert_spot(invite, project, role):
    """Create or update the spot; returns True when a new spot was created."""
    spot = WorkspaceMemberInviteProject.objects.filter(invite=invite, project=project).first()
    if spot is None:
        WorkspaceMemberInviteProject.objects.create(
            invite=invite, project=project, workspace_id=project.workspace_id, role=role
        )
        return True
    spot.role = role
    spot.save()
    return False


def _sort_key(entry):
    raw = entry.get("email") if isinstance(entry, dict) else None
    return raw.strip().lower() if isinstance(raw, str) else ""


class AdminInviteSuperAdminOnly(Exception):
    """FORK: PSR-124 -- renewing an Admin-role invitation needs a Super Admin."""


def _invite_and_spot(project, email, role, workspace_role, user, expiry_days, is_super_admin=False):
    """Reuse (or create) the workspace invitation for ``email`` and add this project's spot.

    Returns ``(invite, created, renewed)``. ``renewed`` is True when an EXPIRED pending invitation was
    given a fresh window (the caller sends the email again). Audit entries are written here, inside the
    caller's transaction. Raises ``AdminInviteSuperAdminOnly`` when the invitation is expired, carries an
    Admin role and the requester is not a Super Admin (PSR-125: only a Super Admin brings in an Admin).
    """
    # Lock the invitation (same lock the cancel endpoint takes) so a concurrent cancel of its last spot
    # cannot delete it while this request attaches a new spot to it.
    invite = (
        WorkspaceMemberInvite.objects.select_for_update()
        .filter(workspace_id=project.workspace_id, email__iexact=email)
        .first()
    )
    if invite is not None and invite.responded_at is not None and not invite.accepted:
        # a declined invitation can never be accepted again: replace it
        remove_project_spots(invite)
        invite.delete()
        invite = None

    created = invite is None
    renewed = False
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
    elif is_pending(invite) and is_invite_expired(invite, expiry_days):
        # FORK: PSR-124 -- an expired invitation is renewed and sent again
        # FORK: PSR-124 / PSR-125 -- renewing an Admin-role invitation is for a Super Admin only
        if invite.role >= ROLE.ADMIN.value and not is_super_admin:
            raise AdminInviteSuperAdminOnly()
        renew_invite(invite)
        renewed = True

    spot_created = _upsert_spot(invite, project, role)
    if renewed:
        workspace_audit.record_invite_resent(project.workspace_id, user, invite, project, role)
    elif created or spot_created:
        workspace_audit.record_invite_sent(project.workspace_id, user, invite, project, role)
    return invite, created, renewed


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
        # FORK: PSR-124 -- Part 2: workspace settings decide what a Project Admin may do. The decorator
        # already guarantees a requester who is not a workspace Admin is a Project Admin.
        invite_settings = get_invite_settings(project.workspace_id)
        is_super_admin = is_workspace_super_admin(request.user, workspace_id=project.workspace_id)
        if not is_ws_admin:
            # PO-1: a new person invited by a Project Admin is a workspace Member
            workspace_role = ROLE.MEMBER.value

        results = []
        added_project_member_ids = []
        invites_to_send = []  # (invite, personal message)
        # Invitation rows are locked until the batch commits, so take them in a fixed (email) order to
        # avoid deadlocks between two overlapping batches; results go back in request order below.
        order = sorted(range(len(emails)), key=lambda i: _sort_key(emails[i]))
        with transaction.atomic():
            for entry in (emails[i] for i in order):
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

                denied = invite_not_allowed_detail(email, invite_settings, is_ws_admin, True)
                if denied is not None:
                    results.append({"email": email, "status": "not_allowed", "detail": denied})
                    continue

                # One savepoint per email: a concurrent request creating the same invite/spot must not
                # fail (or roll back) the rest of the batch.
                try:
                    with transaction.atomic():
                        invite, created, renewed = _invite_and_spot(
                            project,
                            email,
                            role,
                            workspace_role,
                            request.user,
                            invite_settings.invite_expiry_days,
                            is_super_admin,
                        )
                except AdminInviteSuperAdminOnly:
                    # FORK: PSR-124 / PSR-125 -- not renewed; the web shows a readable reason
                    results.append(
                        {"email": email, "status": "not_allowed", "detail": "admin_invite_super_admin_only"}
                    )
                    continue
                except IntegrityError:
                    created = False
                    renewed = False
                    # only a still-pending invitation may receive the spot (never a declined/accepted one)
                    invite = (
                        WorkspaceMemberInvite.objects.select_for_update()
                        .filter(
                            workspace_id=project.workspace_id,
                            email__iexact=email,
                            accepted=False,
                            responded_at__isnull=True,
                        )
                        .first()
                    )
                    if invite is None:
                        results.append({"email": email, "status": "invalid", "detail": "Please try again."})
                        continue
                    try:
                        with transaction.atomic():
                            # FORK: PSR-124 -- same renew path as above (an expired one is renewed and re-sent)
                            if is_invite_expired(invite, invite_settings.invite_expiry_days):
                                if invite.role >= ROLE.ADMIN.value and not is_super_admin:
                                    raise AdminInviteSuperAdminOnly()
                                renew_invite(invite)
                                renewed = True
                                _upsert_spot(invite, project, role)
                                workspace_audit.record_invite_resent(
                                    project.workspace_id, request.user, invite, project, role
                                )
                            elif _upsert_spot(invite, project, role):
                                workspace_audit.record_invite_sent(
                                    project.workspace_id, request.user, invite, project, role
                                )
                    except AdminInviteSuperAdminOnly:
                        results.append(
                            {"email": email, "status": "not_allowed", "detail": "admin_invite_super_admin_only"}
                        )
                        continue
                    except IntegrityError:
                        renewed = False
                        pass  # the concurrent request already created the spot

                if created:
                    invites_to_send.append((invite, message))
                elif renewed:
                    # a re-send: the personal note only ever goes in the first email
                    invites_to_send.append((invite, None))
                results.append({"email": email, "status": "invited" if (created or renewed) else "invite_updated"})

        # exactly one result per entry, appended in processing order -> restore request order
        results = [result for _, result in sorted(zip(order, results), key=lambda pair: pair[0])]

        # Side effects only once the data is committed
        for invite, note in invites_to_send:
            workspace_invitation.delay(invite.email, project.workspace_id, invite.token, host, request.user.email, note)
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
        expiry = InviteExpiryCache()  # FORK: PSR-124 -- one settings lookup per workspace
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
                "expires_at": expiry.expires_at(spot.invite),  # FORK: PSR-124
                "is_expired": expiry.is_expired(spot.invite),  # FORK: PSR-124
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
            # Lock the invitation first so two cancels of its last two spots run one after the other;
            # otherwise each still sees the other's spot and neither removes the invitation.
            invite = WorkspaceMemberInvite.objects.select_for_update().filter(pk=spot.invite_id).first()
            if invite is None:
                return Response({"error": "Pending invitation not found"}, status=status.HTTP_404_NOT_FOUND)
            WorkspaceMemberInviteProject.objects.filter(pk=spot.pk).delete()  # soft delete, immediate
            # FORK: PSR-124 -- one "cancelled" entry naming the project, whether or not the invitation goes too
            workspace_audit.record_invite_cancelled(
                invite.workspace_id, request.user, invite, spot.project, spot.role
            )
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

        # FORK: PSR-124 (PO-4) -- a workspace Admin always; a Project Admin only when the workspace setting
        # is on and the domain is still allowed.
        invite_settings = get_invite_settings(spot.invite.workspace_id)
        denied = invite_not_allowed_detail(
            spot.invite.email, invite_settings, _is_workspace_admin(request.user, slug), True
        )
        if denied is not None:
            return Response(
                {"error": "You are not allowed to resend this invitation.", "detail": denied},
                status=status.HTTP_403_FORBIDDEN,
            )

        claimed = False
        try:
            with transaction.atomic():
                invite = WorkspaceMemberInvite.objects.select_for_update().filter(pk=spot.invite_id).first()
                if invite is None or not is_pending(invite):
                    return Response({"error": "Pending invitation not found"}, status=status.HTTP_404_NOT_FOUND)
                # FORK: PSR-124 / PSR-125 -- renewing an Admin-role invitation is for a Super Admin only
                if invite.role >= ROLE.ADMIN.value and not is_workspace_super_admin(
                    request.user, workspace_id=invite.workspace_id
                ):
                    return super_admin_forbidden()
                # Per-invite cooldown; cache.add is atomic, so a double click sends only once.
                if not claim_resend_slot(invite.id):
                    return Response(
                        {"error": "Please wait a minute before resending this invitation."},
                        status=status.HTTP_429_TOO_MANY_REQUESTS,
                    )
                claimed = True
                # FORK: PSR-124 -- a resend starts a fresh expiry window
                renew_invite(invite)
                workspace_audit.record_invite_resent(
                    invite.workspace_id, request.user, invite, spot.project, spot.role
                )
        except Exception:
            # FORK: PSR-124 -- rolled back, nothing was sent: do not burn the cooldown
            if claimed:
                release_resend_slot(invite.id)
            raise
        workspace_invitation.delay(
            invite.email,
            invite.workspace_id,
            invite.token,
            base_host(request=request, is_app=True),
            request.user.email,
        )
        return Response({"message": "Invitation sent"}, status=status.HTTP_200_OK)
