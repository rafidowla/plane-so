# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models

# Module imports
from .base import BaseModel


class Client(BaseModel):
    """A customer/client a workspace does project work for.

    Sits between Workspace and Project so time can be reported and billed
    per client: Workspace -> Client -> Project -> Issue -> Worklog.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="clients")
    name = models.CharField(max_length=255)
    identifier = models.CharField(max_length=12, blank=True, default="")
    description = models.TextField(blank=True, default="")
    email = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    website = models.URLField(null=True, blank=True)
    logo_props = models.JSONField(default=dict)
    # Default rate applied to billable worklogs on this client's projects
    # when neither the resource nor the worklog overrides it.
    default_billable_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="USD")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="client_unique_workspace_name_when_deleted_at_null",
            )
        ]
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        db_table = "clients"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.name} <{self.workspace.name}>"
