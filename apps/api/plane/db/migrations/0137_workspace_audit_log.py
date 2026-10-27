# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- ``workspace_audit_logs``, append-only in the database.

Same shape as 0132 (time-tracking audit): ``WORKSPACE_AUDIT_GUARD_SQL`` installs a row trigger
that refuses every UPDATE and DELETE except the two Postgres runs from its own referential
actions (``pg_trigger_depth() > 1``): the ``ON DELETE CASCADE`` of a workspace hard delete and
the ``ON DELETE SET NULL`` of a user hard delete (which may only null ``actor_id``). The FKs
are created in SQL; the Django fields are DO_NOTHING / ``db_constraint=False``. Tests run with
``--nomigrations`` and install the same SQL from this module.
"""

import uuid

import django.core.serializers.json
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


WORKSPACE_AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION workspace_audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        IF NEW.actor_id IS NULL AND (to_jsonb(NEW) - 'actor_id') = (to_jsonb(OLD) - 'actor_id') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'workspace_audit_logs is append-only (% refused)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workspace_audit_logs_append_only ON workspace_audit_logs;
CREATE TRIGGER workspace_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON workspace_audit_logs
    FOR EACH ROW EXECUTE FUNCTION workspace_audit_logs_append_only();

ALTER TABLE workspace_audit_logs DROP CONSTRAINT IF EXISTS workspace_audit_logs_workspace_fk;
ALTER TABLE workspace_audit_logs
    ADD CONSTRAINT workspace_audit_logs_workspace_fk
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE;
ALTER TABLE workspace_audit_logs DROP CONSTRAINT IF EXISTS workspace_audit_logs_actor_fk;
ALTER TABLE workspace_audit_logs
    ADD CONSTRAINT workspace_audit_logs_actor_fk
    FOREIGN KEY (actor_id) REFERENCES users (id) ON DELETE SET NULL;
"""

WORKSPACE_AUDIT_GUARD_REVERSE_SQL = """
ALTER TABLE workspace_audit_logs DROP CONSTRAINT IF EXISTS workspace_audit_logs_actor_fk;
ALTER TABLE workspace_audit_logs DROP CONSTRAINT IF EXISTS workspace_audit_logs_workspace_fk;
DROP TRIGGER IF EXISTS workspace_audit_logs_append_only ON workspace_audit_logs;
DROP FUNCTION IF EXISTS workspace_audit_logs_append_only();
"""

ACTION_CHOICES = [
    ("workspace.created", "Workspace created"),
    ("workspace.updated", "Workspace details changed"),
    ("workspace.deleted", "Workspace deleted"),
    ("workspace.restored", "Workspace restored"),
    ("workspace.ownership_transferred", "Ownership transferred"),
    ("member.role_changed", "Member role changed"),
    ("member.removed", "Member removed"),
    ("member.left", "Admin left"),
    ("member.admin_invited", "Admin invited"),
    ("member.invite_role_changed", "Invitation role changed"),
    ("webhook.created", "Webhook created"),
    ("webhook.updated", "Webhook changed"),
    ("webhook.deleted", "Webhook deleted"),
    ("webhook.secret_regenerated", "Webhook secret regenerated"),
]

TARGET_CHOICES = [
    ("workspace", "Workspace"),
    ("member", "Member"),
    ("invite", "Invitation"),
    ("webhook", "Webhook"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0136_workspace_member_super_admin"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceAuditLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("actor_name", models.CharField(blank=True, default="", max_length=255)),
                ("actor_email", models.CharField(blank=True, default="", max_length=255)),
                ("action", models.CharField(choices=ACTION_CHOICES, max_length=48)),
                ("target_type", models.CharField(choices=TARGET_CHOICES, max_length=20)),
                ("target_id", models.UUIDField(blank=True, null=True)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                (
                    "before",
                    models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True),
                ),
                (
                    "after",
                    models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="workspace_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="audit_logs",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace Audit Log",
                "verbose_name_plural": "Workspace Audit Logs",
                "db_table": "workspace_audit_logs",
                "ordering": ("-created_at",),
                "indexes": [models.Index(fields=["workspace", "-created_at"], name="ws_audit_ws_created_idx")],
            },
        ),
        migrations.RunSQL(WORKSPACE_AUDIT_GUARD_SQL, reverse_sql=WORKSPACE_AUDIT_GUARD_REVERSE_SQL),
    ]
