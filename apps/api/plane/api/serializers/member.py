# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ProjectMember, WorkspaceMember
from .base import BaseSerializer
from plane.db.models import User
from plane.utils.permissions import ROLE


class ProjectMemberSerializer(BaseSerializer):
    """
    Serializer for project members.
    """

    member = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=True,
    )

    def validate_member(self, value):
        slug = self.context.get("slug")
        if not slug:
            raise serializers.ValidationError("Slug is required", code="INVALID_SLUG")
        if not value:
            raise serializers.ValidationError("Member is required", code="INVALID_MEMBER")
        if not WorkspaceMember.objects.filter(workspace__slug=slug, member=value, is_active=True).exists():
            raise serializers.ValidationError("Member not found in workspace", code="INVALID_MEMBER")
        return value

    def validate_role(self, value):
        if value not in [ROLE.ADMIN.value, ROLE.MEMBER.value, ROLE.GUEST.value]:
            raise serializers.ValidationError("Invalid role", code="INVALID_ROLE")
        return value

    def validate(self, attrs):
        # Mirrors ProjectMemberViewSet.partial_update: a project role cannot exceed the target's
        # workspace role (workspace guests stay guests). Upstream added the same cap; the fork's version
        # below is kept because it is stricter (immutable member, admin floor on create).
        # FORK: guest-access — cap the project role by the user's workspace role, as the app does
        # (app/views/project/member.py): a workspace Guest cannot be a project Member/Admin, and on
        # create a workspace Admin cannot be added below Admin.
        # The member of an existing row is immutable: re-pointing a row at another user would skip the role cap.
        if self.instance is not None and "member" in attrs and attrs["member"].pk != self.instance.member_id:
            raise serializers.ValidationError(
                {"member": "The member of a project membership cannot be changed"}, code="INVALID_MEMBER"
            )
        # An update that does not set ``role`` changes nothing the cap governs (the member is immutable, the role
        # stays as stored), so it is a no-op 200 even on a legacy row that already breaks the cap (e.g. a workspace
        # Guest left at project Member): rejecting it would report a request error for data the client did not send.
        # Any update that does set ``role`` is still capped below, so the row can only be corrected, never raised.
        if self.instance is not None and "role" not in attrs:
            return attrs
        member = attrs.get("member") or (self.instance.member if self.instance else None)
        # On create, evaluate the cap also when ``role`` is omitted: the row gets the model default.
        role = attrs.get("role", self.instance.role if self.instance else ProjectMember._meta.get_field("role").default)
        workspace_role = (
            WorkspaceMember.objects.filter(workspace__slug=self.context.get("slug"), member=member, is_active=True)
            .values_list("role", flat=True)
            .first()
        )
        if workspace_role is None:
            raise serializers.ValidationError({"member": "Member not found in workspace"}, code="INVALID_MEMBER")
        if workspace_role == ROLE.GUEST.value and role in [ROLE.MEMBER.value, ROLE.ADMIN.value]:
            raise serializers.ValidationError(
                "You cannot add a user with role higher than the workspace role", code="INVALID_ROLE"
            )
        if (
            self.instance is None
            and workspace_role == ROLE.ADMIN.value
            and role in [ROLE.MEMBER.value, ROLE.GUEST.value]
        ):
            raise serializers.ValidationError(
                "You cannot add a user with role lower than the workspace role", code="INVALID_ROLE"
            )
        return attrs

    class Meta:
        model = ProjectMember
        fields = ["id", "member", "role"]
        read_only_fields = ["id"]


class BaseMemberLiteAPISerializer(BaseSerializer):
    """Common flattened member representation for paginated member pickers/directories."""

    id = serializers.UUIDField(source="member.id", read_only=True)
    first_name = serializers.CharField(source="member.first_name", read_only=True)
    last_name = serializers.CharField(source="member.last_name", read_only=True)
    email = serializers.EmailField(source="member.email", read_only=True)
    avatar = serializers.CharField(source="member.avatar", read_only=True, allow_null=True)
    avatar_url = serializers.CharField(source="member.avatar_url", read_only=True, allow_null=True)
    display_name = serializers.CharField(source="member.display_name", read_only=True)
    is_bot = serializers.BooleanField(source="member.is_bot", read_only=True)

    class Meta:
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "avatar",
            "avatar_url",
            "display_name",
            "role",
            "is_active",
            "is_bot",
        ]
        read_only_fields = fields


class WorkspaceMemberLiteAPISerializer(BaseMemberLiteAPISerializer):
    """Minimal WorkspaceMember representation for paginated member pickers/directories."""

    class Meta(BaseMemberLiteAPISerializer.Meta):
        model = WorkspaceMember


class ProjectMemberLiteAPISerializer(BaseMemberLiteAPISerializer):
    """Minimal ProjectMember representation for paginated member pickers/directories."""

    class Meta(BaseMemberLiteAPISerializer.Meta):
        model = ProjectMember
