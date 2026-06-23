# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .base import BaseModel


class JiraImportStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class JiraImportJob(BaseModel):
    """Tracks a self-service Jira import run (status + progress + result).

    The Jira API token is never persisted here — it is passed transiently to
    the background task only.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="jira_import_jobs")
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="jira_import_jobs")
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jira_import_jobs"
    )
    status = models.CharField(max_length=20, choices=JiraImportStatus.choices, default=JiraImportStatus.QUEUED)
    with_worklogs = models.BooleanField(default=False)
    # Non-secret connection info for display/audit (url, project key, jql) — never the token.
    config = models.JSONField(default=dict)
    total = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Jira Import Job"
        verbose_name_plural = "Jira Import Jobs"
        db_table = "jira_import_jobs"
        ordering = ("-created_at",)

    def __str__(self):
        return f"jira-import {self.id} [{self.status}]"
