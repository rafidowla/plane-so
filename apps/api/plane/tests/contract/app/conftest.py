# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — the ``s2`` fixture for the S2 time-tracking contract tests
(helpers live in ``time_tracking_s2_support``). Not autouse: other tests are unaffected."""

import pytest

from .time_tracking_s2_support import DHAKA, join, make_issue, make_project


@pytest.fixture
def s2(db, create_user, workspace):
    """Dhaka workspace; tracked project "TTP" with the full state set; the test user is
    workspace + project admin; one Todo issue assigned to them."""
    workspace.timezone = DHAKA
    workspace.save()
    project, states = make_project(workspace, create_user, "TT Project", "TTP")
    join(project, create_user, role=20)
    issue = make_issue(project, states["todo"], "Issue 1", create_user, assignees=[create_user])
    return {
        "workspace": workspace,
        "slug": workspace.slug,
        "project": project,
        "states": states,
        "issue": issue,
        "user": create_user,
    }
