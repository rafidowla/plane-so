# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties data model (docs/custom-properties-design.md §3).

All models live in the fork-owned ``plane.properties`` app so their migration
chain never collides with upstream's ``plane.db`` chain. Upstream models are
referenced by string label (``"db.Workspace"`` etc.), the same pattern
``plane.db.models.issue_type`` uses. Table names mirror upstream Plane's
work-item-properties schema (``issue_properties`` / ``issue_property_options`` /
``issue_property_values``) so an upstream implementation can be adopted later by
fake-migrating onto identical tables rather than rewriting data (Risk R2).
"""

from django.db import models

from plane.db.models.base import BaseModel


class PropertyTypeEnum(models.TextChoices):
    TEXT = "TEXT"
    DATETIME = "DATETIME"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    OPTION = "OPTION"
    RELATION = "RELATION"
    URL = "URL"
    EMAIL = "EMAIL"
    FILE = "FILE"
    FORMULA = "FORMULA"  # contract parity only — rejected by validation in v1


class RelationTypeEnum(models.TextChoices):
    ISSUE = "ISSUE"
    USER = "USER"


class IssueProperty(BaseModel):
    """A typed column definition. ``issue_type`` is nullable: NULL means the
    property applies to every work item in the project (Monday-style)."""

    workspace = models.ForeignKey(
        "db.Workspace", related_name="issue_properties", on_delete=models.CASCADE
    )
    project = models.ForeignKey(
        "db.Project", related_name="issue_properties", on_delete=models.CASCADE
    )
    issue_type = models.ForeignKey(
        "db.IssueType",
        related_name="properties",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)  # machine name (slugified display_name)
    display_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    property_type = models.CharField(max_length=255, choices=PropertyTypeEnum.choices)
    relation_type = models.CharField(
        max_length=255, choices=RelationTypeEnum.choices, null=True, blank=True
    )
    is_required = models.BooleanField(default=False)
    default_value = models.JSONField(default=list)  # array — matches API contract
    settings = models.JSONField(default=dict)  # per-type settings (e.g. TEXT display_format)
    is_active = models.BooleanField(default=True)
    is_multi = models.BooleanField(default=False)
    validation_rules = models.JSONField(default=dict)
    logo_props = models.JSONField(default=dict)
    sort_order = models.FloatField(default=65535)  # column order, left→right
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_properties"  # mirror upstream (Risk R2)
        verbose_name = "Issue Property"
        verbose_name_plural = "Issue Properties"
        ordering = ("sort_order",)

    def __str__(self):
        return f"{self.display_name} <{self.project_id}>"


class IssuePropertyOption(BaseModel):
    """One selectable option for an OPTION-type property. Color lives in
    ``logo_props`` (no dedicated column) — see design §3.3."""

    workspace = models.ForeignKey(
        "db.Workspace", related_name="issue_property_options", on_delete=models.CASCADE
    )
    project = models.ForeignKey(
        "db.Project", related_name="issue_property_options", on_delete=models.CASCADE
    )
    property = models.ForeignKey(
        IssueProperty, related_name="options", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    logo_props = models.JSONField(default=dict)  # color lives here — see §3.3
    sort_order = models.FloatField(default=65535)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    parent = models.ForeignKey(
        "self", related_name="children", null=True, blank=True, on_delete=models.CASCADE
    )
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_property_options"  # mirror upstream (Risk R2)
        verbose_name = "Issue Property Option"
        verbose_name_plural = "Issue Property Options"
        ordering = ("sort_order",)

    def __str__(self):
        return f"{self.name} <{self.property_id}>"


class IssuePropertyValue(BaseModel):
    """A value of a property for one issue. Exactly one typed column is
    populated per row, keyed off the property's ``property_type``. Multi-value
    properties store multiple rows per (issue, property)."""

    workspace = models.ForeignKey(
        "db.Workspace", related_name="issue_property_values", on_delete=models.CASCADE
    )
    project = models.ForeignKey(
        "db.Project", related_name="issue_property_values", on_delete=models.CASCADE
    )
    issue = models.ForeignKey(
        "db.Issue", related_name="property_values", on_delete=models.CASCADE
    )
    property = models.ForeignKey(
        IssueProperty, related_name="property_values", on_delete=models.CASCADE
    )
    # typed value columns — exactly one is populated per row, per property_type
    value_text = models.TextField(blank=True)
    value_boolean = models.BooleanField(default=False)
    value_decimal = models.FloatField(default=0)
    value_datetime = models.DateTimeField(null=True, blank=True)
    value_uuid = models.UUIDField(null=True, blank=True)  # RELATION targets
    value_option = models.ForeignKey(
        IssuePropertyOption,
        related_name="values",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "issue_property_values"  # mirror upstream (Risk R2)
        verbose_name = "Issue Property Value"
        verbose_name_plural = "Issue Property Values"
        indexes = [
            models.Index(fields=["issue"]),
            models.Index(fields=["property"]),
            models.Index(fields=["project", "property"]),
        ]

    def __str__(self):
        return f"value(issue={self.issue_id}, property={self.property_id})"


class ProjectPropertiesFeature(BaseModel):
    """Per-project on/off toggle for custom properties. Fork-only (no upstream
    mirror), so the table is ``fork_``-prefixed and safe forever. No new column
    on ``db.Project`` — keeps the db-app migration chain untouched (design §8)."""

    project = models.OneToOneField(
        "db.Project", related_name="properties_feature", on_delete=models.CASCADE
    )
    workspace = models.ForeignKey(
        "db.Workspace", related_name="properties_features", on_delete=models.CASCADE
    )
    is_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "fork_project_properties_feature"
        verbose_name = "Project Properties Feature"
        verbose_name_plural = "Project Properties Features"

    def __str__(self):
        return f"properties_feature(project={self.project_id}, enabled={self.is_enabled})"
