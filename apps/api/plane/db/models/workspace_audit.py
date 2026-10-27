# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- workspace audit log of Super Admin actions."""

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .time_tracking import AppendOnlyModel


class WorkspaceAuditAction(models.TextChoices):
    WORKSPACE_CREATED = "workspace.created", "Workspace created"
    WORKSPACE_UPDATED = "workspace.updated", "Workspace details changed"
    WORKSPACE_DELETED = "workspace.deleted", "Workspace deleted"
    WORKSPACE_RESTORED = "workspace.restored", "Workspace restored"
    OWNERSHIP_TRANSFERRED = "workspace.ownership_transferred", "Ownership transferred"
    MEMBER_ROLE_CHANGED = "member.role_changed", "Member role changed"
    MEMBER_REMOVED = "member.removed", "Member removed"
    MEMBER_LEFT = "member.left", "Admin left"
    ADMIN_INVITED = "member.admin_invited", "Admin invited"
    INVITE_ROLE_CHANGED = "member.invite_role_changed", "Invitation role changed"
    WEBHOOK_CREATED = "webhook.created", "Webhook created"
    WEBHOOK_UPDATED = "webhook.updated", "Webhook changed"
    WEBHOOK_DELETED = "webhook.deleted", "Webhook deleted"
    WEBHOOK_SECRET_REGENERATED = "webhook.secret_regenerated", "Webhook secret regenerated"


class WorkspaceAuditTarget(models.TextChoices):
    WORKSPACE = "workspace", "Workspace"
    MEMBER = "member", "Member"
    INVITE = "invite", "Invitation"
    WEBHOOK = "webhook", "Webhook"


class WorkspaceAuditLog(AppendOnlyModel):
    """Who did what to the workspace, with before/after values of what changed.

    Append-only in the database too (migration 0137): a trigger refuses UPDATE / DELETE except
    the cascade of a workspace hard delete and the ``actor`` -> NULL of a user hard delete, so
    the log lives exactly as long as the workspace. Both FKs are DO_NOTHING here with the real
    ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` constraints created in SQL (same shape as
    ``TimeTrackingAuditLog``). It has no ``deleted_at``, so a workspace soft delete leaves it alone.

    ``actor_name`` / ``actor_email`` / ``target_label`` are snapshots taken at write time, so an
    entry still reads correctly after the person or object is renamed or erased.
    """

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.DO_NOTHING, db_constraint=False, related_name="audit_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="workspace_audit_logs",
    )
    actor_name = models.CharField(max_length=255, blank=True, default="")
    actor_email = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=48, choices=WorkspaceAuditAction.choices)
    target_type = models.CharField(max_length=20, choices=WorkspaceAuditTarget.choices)
    target_id = models.UUIDField(null=True, blank=True)
    target_label = models.CharField(max_length=255, blank=True, default="")
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "Workspace Audit Log"
        verbose_name_plural = "Workspace Audit Logs"
        db_table = "workspace_audit_logs"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["workspace", "-created_at"], name="ws_audit_ws_created_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.target_type}:{self.target_id}"
