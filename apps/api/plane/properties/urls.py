# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties URL routes.

Mounted under `/api/` from the root urlconf (one FORK-marked line, see A6 in
docs/custom-properties-design.md §6). Property/option/value routes are added in
Phase 1/2; Phase 0 exposes only the health probe.
"""

from django.urls import path

from plane.properties.views import (
    BulkIssuePropertyValuesEndpoint,
    CustomPropertiesHealthEndpoint,
    IssuePropertiesEndpoint,
    IssuePropertyDetailEndpoint,
    IssuePropertyOptionDetailEndpoint,
    IssuePropertyOptionsEndpoint,
    IssuePropertyValuesEndpoint,
    ProjectPropertiesFeatureEndpoint,
)

_PROJECT = "workspaces/<str:slug>/projects/<uuid:project_id>"

urlpatterns = [
    path(
        "custom-properties/health/",
        CustomPropertiesHealthEndpoint.as_view(),
        name="custom-properties-health",
    ),
    path(
        f"{_PROJECT}/properties-feature/",
        ProjectPropertiesFeatureEndpoint.as_view(),
        name="project-properties-feature",
    ),
    # Property definitions
    path(
        f"{_PROJECT}/work-item-properties/",
        IssuePropertiesEndpoint.as_view(),
        name="issue-properties",
    ),
    path(
        f"{_PROJECT}/work-item-properties/<uuid:property_id>/",
        IssuePropertyDetailEndpoint.as_view(),
        name="issue-property-detail",
    ),
    # Options
    path(
        f"{_PROJECT}/work-item-properties/<uuid:property_id>/options/",
        IssuePropertyOptionsEndpoint.as_view(),
        name="issue-property-options",
    ),
    path(
        f"{_PROJECT}/work-item-properties/<uuid:property_id>/options/<uuid:option_id>/",
        IssuePropertyOptionDetailEndpoint.as_view(),
        name="issue-property-option-detail",
    ),
    # Values (per issue + property, replace semantics)
    path(
        f"{_PROJECT}/work-items/<uuid:work_item_id>/work-item-properties/<uuid:property_id>/values/",
        IssuePropertyValuesEndpoint.as_view(),
        name="issue-property-values",
    ),
    # Bulk value read (board/spreadsheet hydration)
    path(
        f"{_PROJECT}/work-item-property-values/",
        BulkIssuePropertyValuesEndpoint.as_view(),
        name="bulk-issue-property-values",
    ),
]
