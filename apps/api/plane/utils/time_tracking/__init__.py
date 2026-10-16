# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — shared time-tracking service layer.

Every entry point (per-issue endpoints, workspace endpoints, celery sweep) goes
through these modules so they behave identically:

- ``tz``        workspace-local dates and the midnight split
- ``snapshot``  work-item label snapshot kept on worklogs
- ``audit``     append-only audit rows
- ``tracking``  workspace settings + per-member tracking status
- ``timer``     timer start/stop (stop-previous, split, system stops)
"""
