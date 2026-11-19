# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from datetime import datetime

import jwt

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction  # FORK: PSR-124
from django.utils import timezone

# Third party modules
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

# Module imports
from plane.app.permissions import WorkSpaceAdminPermission
from plane.app.permissions import super_admin_forbidden  # FORK: PSR-125
from plane.utils import workspace_audit  # FORK: PSR-125
from plane.app.serializers import (
    WorkSpaceMemberInviteSerializer,
    WorkSpaceMemberInvitePublicSerializer,
    WorkSpaceMemberSerializer,
)
from plane.app.views.base import BaseAPIView
from plane.bgtasks.workspace_invitation_task import workspace_invitation
from plane.db.models import User, Workspace, WorkspaceMember, WorkspaceMemberInvite
from plane.utils.cache import invalidate_cache, invalidate_cache_directly
from plane.utils.host import base_host
from plane.utils.project_invite_spots import (  # FORK: PSR-124
    accept_project_spots,
    remove_project_spots,
)
from plane.utils.invite_settings import (  # FORK: PSR-124
    InviteExpiryCache,
    claim_resend_slot,
    is_pending,
    release_resend_slot,
    renew_invite,
)
from .. import BaseViewSet


class WorkspaceInvitationsViewset(BaseViewSet):
    """Endpoint for creating, listing and  deleting workspaces"""

    serializer_class = WorkSpaceMemberInviteSerializer
    model = WorkspaceMemberInvite

    permission_classes = [WorkSpaceAdminPermission]

    def get_queryset(self):
        return self.filter_queryset(
            super()
            .get_queryset()
            .filter(workspace__slug=self.kwargs.get("slug"))
            .select_related("workspace", "workspace__owner", "created_by")
        )

    def create(self, request, slug):
        emails = request.data.get("emails", [])
        # Check if email is provided
        if not emails:
            return Response({"error": "Emails are required"}, status=status.HTTP_400_BAD_REQUEST)

        # check for role level of the requesting user
        requesting_user = WorkspaceMember.objects.get(workspace__slug=slug, member=request.user, is_active=True)

        # Check if any invited user has an higher role
        if len([email for email in emails if int(email.get("role", 5)) > requesting_user.role]):
            return Response(
                {"error": "You cannot invite a user with higher role"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FORK: PSR-125 -- only a Super Admin can bring in a new Admin.
        if not requesting_user.is_super_admin and any(int(email.get("role", 5)) == 20 for email in emails):
            return super_admin_forbidden()

        # Get the workspace object
        workspace = Workspace.objects.get(slug=slug)

        # Check if user is already a member of workspace
        workspace_members = WorkspaceMember.objects.filter(
            workspace_id=workspace.id,
            member__email__in=[email.get("email") for email in emails],
            is_active=True,
        ).select_related("member", "member__avatar_asset")

        if workspace_members:
            return Response(
                {
                    "error": "Some users are already member of workspace",
                    "workspace_users": WorkSpaceMemberSerializer(workspace_members, many=True).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace_invitations = []
        for email in emails:
            try:
                validate_email(email.get("email"))
                workspace_invitations.append(
                    WorkspaceMemberInvite(
                        email=email.get("email").strip().lower(),
                        workspace_id=workspace.id,
                        token=jwt.encode(
                            {"email": email, "timestamp": datetime.now().timestamp()},
                            settings.SECRET_KEY,
                            algorithm="HS256",
                        ),
                        role=email.get("role", 5),
                        created_by=request.user,
                    )
                )
            except ValidationError:
                return Response(
                    {
                        "error": f"Invalid email - {email} provided a valid email address is required to send the invite"  # noqa: E501
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # FORK: PSR-124 -- the invites, the project-flow flag and the audit entries commit together.
        # Only rows that really exist (inserted or renewed) are audited and emailed, with the token stored.
        is_super_admin = requesting_user.is_super_admin
        invites_to_send = []
        claimed_slots = []
        skipped = []  # FORK: PSR-124 -- pending invites that were not resent, with the reason
        try:
            with transaction.atomic():
                requested_emails = [i.email for i in workspace_invitations]
                existing = {
                    i.email: i
                    for i in WorkspaceMemberInvite.objects.select_for_update()
                    # FORK: PSR-124 -- fixed lock order, so overlapping concurrent creates cannot deadlock
                    .filter(workspace_id=workspace.id, email__in=requested_emails)
                    .order_by("id")
                }
                new_invitations = []
                handled = set()  # an email repeated in one request is handled once
                for invitation in workspace_invitations:
                    current = existing.get(invitation.email)
                    if current is not None:
                        if current.id in handled:
                            continue
                        handled.add(current.id)
                    if current is None:
                        if invitation.email not in {n.email for n in new_invitations}:
                            new_invitations.append(invitation)
                        continue
                    # An existing PENDING invite is treated as a resend: renew it, keep its token and role.
                    if not is_pending(current):
                        continue  # declined: upstream behaviour (left as is, nothing sent)
                    # FORK: PSR-124 -- re-inviting from the workspace page makes it a workspace invite, even when
                    # the resend itself is skipped below (same transaction, under the row lock).
                    if current.created_from_project:
                        current.created_from_project = False
                        current.save(update_fields=["created_from_project", "updated_at"])
                    if current.role == 20 and not is_super_admin:
                        # PSR-125: only a Super Admin renews an Admin invitation
                        skipped.append({"email": current.email, "reason": "super_admin_required"})
                        continue
                    if not claim_resend_slot(current.id):
                        skipped.append({"email": current.email, "reason": "cooldown"})  # 60 s resend cooldown
                        continue
                    claimed_slots.append(current.id)
                    renew_invite(current)
                    workspace_audit.record_invite_resent(workspace.id, request.user, current)
                    invites_to_send.append(current)

                # Create workspace member invite
                if new_invitations:
                    WorkspaceMemberInvite.objects.bulk_create(new_invitations, batch_size=10, ignore_conflicts=True)
                    landed = {
                        i.email: i
                        for i in WorkspaceMemberInvite.objects.filter(
                            workspace_id=workspace.id,
                            email__in=[n.email for n in new_invitations],
                            token__in=[n.token for n in new_invitations],
                        )
                    }
                    for invitation in new_invitations:
                        saved = landed.get(invitation.email)
                        if saved is None:
                            continue  # a concurrent request created it first: nothing of ours exists
                        workspace_audit.record_invite_sent(workspace.id, request.user, saved)
                        invites_to_send.append(saved)
        except Exception:
            for invite_id in claimed_slots:
                release_resend_slot(invite_id)
            raise

        current_site = base_host(request=request, is_app=True)

        # Send invitations
        for invitation in invites_to_send:
            workspace_invitation.delay(
                invitation.email,
                workspace.id,
                invitation.token,
                current_site,
                request.user.email,
            )

        # FORK: PSR-124 -- `skipped` lists pending invites that were not resent (HTTP 200 and `message` kept)
        if skipped:
            return Response(
                {"message": "Some invitations were not resent", "skipped": skipped},
                status=status.HTTP_200_OK,
            )
        return Response({"message": "Emails sent successfully", "skipped": []}, status=status.HTTP_200_OK)

    def partial_update(self, request, slug, pk):
        # SECURITY: only ``role`` is writable, capped at the requester's own role as in create().
        if not isinstance(request.data, dict) or set(request.data) != {"role"}:
            return Response(
                {"error": "Only the role of an invitation can be updated"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace_member_invite = WorkspaceMemberInvite.objects.get(pk=pk, workspace__slug=slug)
        serializer = WorkSpaceMemberInviteSerializer(workspace_member_invite, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        requesting_user = WorkspaceMember.objects.get(workspace__slug=slug, member=request.user, is_active=True)
        if serializer.validated_data["role"] > requesting_user.role:
            return Response(
                {"error": "You cannot invite a user with higher role"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FORK: PSR-125 -- only a Super Admin can make (or unmake) an Admin invitation.
        roles = (serializer.validated_data["role"], workspace_member_invite.role)
        if not requesting_user.is_super_admin and 20 in roles:
            return super_admin_forbidden()

        before_role = workspace_member_invite.role
        invite = serializer.save()
        workspace_audit.record_invite_role_change(  # FORK: PSR-125
            invite.workspace_id, request.user, invite, before_role, invite.role
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, slug, pk):
        workspace_member_invite = WorkspaceMemberInvite.objects.get(pk=pk, workspace__slug=slug)
        with transaction.atomic():
            # FORK: PSR-124 -- cancelling the invitation drops its pending project spots too.
            remove_project_spots(workspace_member_invite)
            workspace_member_invite.delete()
            # FORK: PSR-124 -- audit; removing an answered (declined) invitation is clean-up, not a cancel
            if is_pending(workspace_member_invite):
                workspace_audit.record_invite_cancelled(
                    workspace_member_invite.workspace_id, request.user, workspace_member_invite
                )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceJoinEndpoint(BaseAPIView):
    permission_classes = [AllowAny]
    """Invitation response endpoint the user can respond to the invitation"""

    @invalidate_cache(path="/api/workspaces/", user=False)
    @invalidate_cache(path="/api/users/me/workspaces/", multiple=True)
    @invalidate_cache(
        path="/api/workspaces/:slug/members/",
        user=False,
        multiple=True,
        url_params=True,
    )
    @invalidate_cache(path="/api/users/me/settings/", multiple=True)
    def post(self, request, slug, pk):
        workspace_invite = WorkspaceMemberInvite.objects.get(pk=pk, workspace__slug=slug)

        token = request.data.get("token", "")

        # Validate the token to verify the user received the invitation email
        if not token or workspace_invite.token != token:
            return Response(
                {"error": "You do not have permission to join the workspace"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Require an authenticated session — the accepting user must be the
        # person who was invited.  Without this check an attacker who registers
        # with the invited address (email-squat) and obtains the token via the
        # GET endpoint can steal the workspace membership (GHSA-4vj8-p63v-8p24).
        if not request.user.is_authenticated:
            return Response(
                {"error": "Authentication required to accept workspace invitation"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if request.user.email.lower() != workspace_invite.email.lower():
            return Response(
                {"error": "You do not have permission to accept this invitation"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # FORK: PSR-124 -- the whole response (invite state, workspace join, project spots) is one unit,
        # and the invite row is locked so a double click cannot run it twice.
        with transaction.atomic():
            workspace_invite = (
                WorkspaceMemberInvite.objects.select_for_update(of=("self",))
                .filter(pk=pk, workspace__slug=slug)
                .first()
            )
            if workspace_invite is None or workspace_invite.responded_at is not None:
                return Response(
                    {"error": "You have already responded to the invitation request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            accepting = request.data.get("accepted", False)
            # FORK: PSR-124 -- an expired invitation can be declined but not accepted
            if accepting and InviteExpiryCache().is_expired(workspace_invite):
                return Response(
                    {
                        "error": "This invitation has expired. Ask a workspace admin to resend it.",
                        "error_code": "INVITATION_EXPIRED",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            workspace_invite.accepted = accepting
            workspace_invite.responded_at = timezone.now()
            workspace_invite.save()

            if workspace_invite.accepted:
                # Check if the user created account after invitation
                user = User.objects.filter(email__iexact=workspace_invite.email).first()

                # FORK: PSR-124 -- the first project the user was added to, for the web redirect.
                project_id = None

                # If the user is present then create the workspace member
                if user is not None:
                    # Check if the user was already a member of workspace then activate the user
                    workspace_member = WorkspaceMember.objects.filter(
                        workspace=workspace_invite.workspace, member=user
                    ).first()
                    if workspace_member is not None:
                        workspace_member.is_active = True
                        # FORK: PSR-125 -- a stale invite must not demote a current Super Admin.
                        if not workspace_member.is_super_admin:
                            workspace_member.role = workspace_invite.role
                        workspace_member.save()
                    else:
                        # Create a Workspace
                        workspace_member = WorkspaceMember.objects.create(
                            workspace=workspace_invite.workspace,
                            member=user,
                            role=workspace_invite.role,
                        )

                    # Set the user last_workspace_id to the accepted workspace
                    user.last_workspace_id = workspace_invite.workspace.id
                    user.save()

                    # FORK: PSR-124 -- turn the pending project spots into project memberships
                    project_ids = accept_project_spots(workspace_invite, user, workspace_member.role)
                    project_id = str(project_ids[0]) if project_ids else None
                    workspace_audit.record_invite_accepted(  # FORK: PSR-124
                        workspace_invite.workspace_id, request.user, workspace_invite
                    )

                    # Delete the invitation
                    workspace_invite.delete()

                return Response(
                    {"message": "Workspace Invitation Accepted", "project_id": project_id},  # FORK: PSR-124
                    status=status.HTTP_200_OK,
                )

            # Workspace invitation rejected
            workspace_audit.record_invite_declined(  # FORK: PSR-124
                workspace_invite.workspace_id, request.user, workspace_invite
            )
            return Response(
                {"message": "Workspace Invitation was not accepted"},
                status=status.HTTP_200_OK,
            )

    def get(self, request, slug, pk):
        workspace_invitation = WorkspaceMemberInvite.objects.get(workspace__slug=slug, pk=pk)
        # Use the public serializer that omits the token and invite_link fields so
        # that an unauthenticated caller cannot retrieve the acceptance token
        # (GHSA-86mg-259g-pwgg / GHSA-gf48-p6jp-cwc4).
        serializer = WorkSpaceMemberInvitePublicSerializer(workspace_invitation)
        return Response(serializer.data, status=status.HTTP_200_OK)


class UserWorkspaceInvitationsViewSet(BaseViewSet):
    serializer_class = WorkSpaceMemberInviteSerializer
    model = WorkspaceMemberInvite

    def get_queryset(self):
        return self.filter_queryset(
            super().get_queryset().filter(email=self.request.user.email).select_related("workspace")
        )

    def list(self, request):
        # Self-service route with no `slug`, so the role decorators do not
        # apply. Authorization is the `email=request.user.email` filter in
        # get_queryset() above, as for create() below: callers see only
        # invitations addressed to their own email.
        # FORK: PSR-124 -- answered (declined) invitations are not acceptable, so they are not listed
        queryset = self.get_queryset().filter(accepted=False, responded_at__isnull=True)
        return Response(self.get_serializer(queryset, many=True).data, status=status.HTTP_200_OK)

    @invalidate_cache(path="/api/workspaces/", user=False)
    @invalidate_cache(path="/api/users/me/workspaces/", multiple=True)
    @transaction.atomic  # FORK: PSR-124 -- workspace join + project spots land together
    def create(self, request):
        invitations = request.data.get("invitations", [])
        # FORK: PSR-124 -- only a PENDING invitation (not answered, not cancelled) can be joined. A declined
        # one must never be joinable, expired or not (decline-then-accept would dodge the expiry check).
        workspace_invitations = WorkspaceMemberInvite.objects.filter(
            pk__in=invitations,
            email=request.user.email,
            accepted=False,
            responded_at__isnull=True,
            deleted_at__isnull=True,
        ).order_by("-created_at")

        # FORK: PSR-124 -- materialise once so the spots can be processed before the invites are deleted
        workspace_invitations = list(workspace_invitations)

        # FORK: PSR-124 -- expired invitations are skipped: not joined, not deleted
        expiry = InviteExpiryCache()
        expired = [i for i in workspace_invitations if expiry.is_expired(i)]
        if expired:
            workspace_invitations = [i for i in workspace_invitations if i not in expired]
            if not workspace_invitations:
                return Response(
                    {
                        "error": "This invitation has expired. Ask a workspace admin to resend it.",
                        "error_code": "INVITATION_EXPIRED",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif not workspace_invitations:
            return Response(
                {"error": "None of these invitations can be accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # If the user is already a member of workspace and was deactivated then activate the user
        for invitation in workspace_invitations:
            invalidate_cache_directly(
                path=f"/api/workspaces/{invitation.workspace.slug}/members/",
                user=False,
                request=request,
                multiple=True,
            )
            # Update the WorkspaceMember for this specific invitation
            # FORK: PSR-125 -- a stale invite must not demote a current Super Admin.
            WorkspaceMember.objects.filter(workspace_id=invitation.workspace_id, member=request.user).exclude(
                is_super_admin=True
            ).update(is_active=True, role=invitation.role)

        # Bulk create the user for all the workspaces
        WorkspaceMember.objects.bulk_create(
            [
                WorkspaceMember(
                    workspace=invitation.workspace,
                    member=request.user,
                    role=invitation.role,
                    created_by=request.user,
                )
                for invitation in workspace_invitations
            ],
            ignore_conflicts=True,
        )

        # FORK: PSR-124 -- turn pending project spots into project memberships
        for invitation in workspace_invitations:
            workspace_role = (
                WorkspaceMember.objects.filter(
                    workspace_id=invitation.workspace_id, member=request.user, is_active=True
                )
                .values_list("role", flat=True)
                .first()
            )
            if workspace_role is not None:
                accept_project_spots(invitation, request.user, workspace_role)
                workspace_audit.record_invite_accepted(invitation.workspace_id, request.user, invitation)

        # Delete joined workspace invites
        WorkspaceMemberInvite.objects.filter(pk__in=[i.pk for i in workspace_invitations]).delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
