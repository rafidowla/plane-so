# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class DashboardsConfig(AppConfig):
    # Fork-owned app for custom dashboards. Kept as a separate Django app so its
    # migration chain never collides with upstream's plane.db chain. See
    # docs/custom-dashboards-design.md.
    name = "plane.dashboards"
