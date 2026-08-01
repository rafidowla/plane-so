# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: jira-comment-provenance (#15) — hand-written; regenerate via
# makemigrations if upstream adds migrations first (numbers may collide).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0124_jiraimportjob_with_attachments"),
    ]

    operations = [
        migrations.AddField(
            model_name="issuecomment",
            name="external_actor_display",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
