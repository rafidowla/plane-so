"""FORK: time-tracking-v2 secfix — stored "already in progress" flag for the estimate gate.

Before this, an item counted as grandfathered whenever it currently sat in a Started/Completed
state, so creating or moving an item straight into such a state skipped the "required" gate.
``IssueEstimateGrandfather`` records the items that were in progress when the gate came into
force (written by ``estimate.grandfather_project`` on a mode flip). The backfill below flags the
items of every project already in ``required`` mode, so nothing that was running is newly blocked.

Reverse: drops the table (the flags are derived data; the old behaviour needs none).
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

BACKFILL_SQL = """
INSERT INTO issue_estimate_grandfathers
    (id, created_at, updated_at, issue_id, project_id, workspace_id)
SELECT gen_random_uuid(), now(), now(), i.id, i.project_id, i.workspace_id
FROM issues i
JOIN projects p ON p.id = i.project_id
JOIN states s ON s.id = i.state_id
WHERE p.time_estimate_mode = 'required'
  AND p.deleted_at IS NULL
  AND i.deleted_at IS NULL
  AND s."group" IN ('started', 'completed')
  AND NOT EXISTS (
      SELECT 1 FROM issue_time_estimates e WHERE e.issue_id = i.id AND e.deleted_at IS NULL
  )
  AND NOT EXISTS (
      SELECT 1 FROM issue_estimate_grandfathers g WHERE g.issue_id = i.id AND g.deleted_at IS NULL
  )
"""


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0132_time_tracking_audit_append_only"),
    ]

    operations = [
        migrations.CreateModel(
            name="IssueEstimateGrandfather",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
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
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="estimate_grandfathers",
                        to="db.issue",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="project_%(class)s", to="db.project"
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workspace_%(class)s",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Issue Estimate Grandfather",
                "verbose_name_plural": "Issue Estimate Grandfathers",
                "db_table": "issue_estimate_grandfathers",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="issueestimategrandfather",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("issue",),
                name="issue_estimate_grandfather_one_per_issue",
            ),
        ),
        migrations.RunSQL(BACKFILL_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
