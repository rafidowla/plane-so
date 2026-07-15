# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties URL routes.

Mounted under `/api/` from the root urlconf (one FORK-marked line, see A6 in
docs/custom-properties-design.md §6). Property/option/value routes are added in
Phase 1/2; Phase 0 exposes only the health probe.
"""

from django.urls import path

from plane.properties.views import CustomPropertiesHealthEndpoint

urlpatterns = [
    path(
        "custom-properties/health/",
        CustomPropertiesHealthEndpoint.as_view(),
        name="custom-properties-health",
    ),
]
