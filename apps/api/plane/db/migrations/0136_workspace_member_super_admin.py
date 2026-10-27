# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: PSR-125 -- workspace Super Admin flag.
#
# Backfill: each live workspace's owner becomes its Super Admin when they are still an active,
# non-bot Admin there; otherwise the longest-standing such Admin does, so no workspace is left
# without one. All other Admins stay plain Admins.

from django.db import migrations, models


def backfill_super_admins(apps, schema_editor):
    Workspace = apps.get_model("db", "Workspace")
    WorkspaceMember = apps.get_model("db", "WorkspaceMember")

    for workspace in Workspace.objects.filter(deleted_at__isnull=True).only("id", "owner_id").iterator():
        admins = WorkspaceMember.objects.filter(
            workspace_id=workspace.id,
            role=20,
            is_active=True,
            deleted_at__isnull=True,
            member__is_bot=False,
        )
        chosen = admins.filter(member_id=workspace.owner_id).first() or admins.order_by("created_at", "id").first()
        if chosen is not None:
            WorkspaceMember.objects.filter(pk=chosen.pk).update(is_super_admin=True)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0135_merge_upstream_2026_10_23"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspacemember",
            name="is_super_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_super_admins, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="workspacemember",
            constraint=models.CheckConstraint(
                condition=models.Q(is_super_admin=False) | models.Q(role=20, is_active=True),
                name="workspace_member_super_admin_is_active_admin",
            ),
        ),
    ]
