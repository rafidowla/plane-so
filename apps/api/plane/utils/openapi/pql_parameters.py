# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""OpenAPI query parameters for PQL work item filtering (FORK: PSR-85)."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter

PQL_PARAMETER = OpenApiParameter(
    name="pql",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description=(
        "Plane Query Language expression to filter work items, e.g. "
        '`state.group = "started" AND assignee = currentUser()`. '
        'An invalid expression returns 400 with `{"pql": "<message>"}`.'
    ),
    required=False,
)

FILTERS_PARAMETER = OpenApiParameter(
    name="filters",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description=(
        'JSON object of structured lookups, e.g. `{"priority__in": ["high", "urgent"]}`. '
        'Combined with `pql` using AND. Invalid input returns 400 with `{"filters": "<message>"}`.'
    ),
    required=False,
)

GROUP_BY_PARAMETER = OpenApiParameter(
    name="group_by",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description=(
        "Group counts by one of: state_id, state__group, priority, project_id, labels__id, "
        "assignees__id, issue_module__module_id, cycle_id, created_by, target_date, start_date."
    ),
    required=False,
)

SUB_GROUP_BY_PARAMETER = OpenApiParameter(
    name="sub_group_by",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Second grouping dimension (requires group_by and must differ from it); same values as group_by.",
    required=False,
)
