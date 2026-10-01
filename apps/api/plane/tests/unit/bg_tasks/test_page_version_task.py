# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-86 — track_page_version must actually write PageVersion rows.

Upstream's task read ``page.description`` after the field was renamed to ``description_json``; the
AttributeError was swallowed by the task's catch-all, so the app recorded no page versions at all.
"""

import json

import pytest

from plane.bgtasks.page_version_task import track_page_version
from plane.db.models import Page, PageVersion


@pytest.mark.unit
class TestTrackPageVersion:
    @pytest.fixture
    def page(self, db, workspace, create_user):
        return Page.objects.create(
            name="Runbook",
            workspace=workspace,
            owned_by=create_user,
            description_html="<p>new</p>",
            description_json={"type": "doc", "content": []},
        )

    def test_creates_version_when_html_changed(self, page, create_user):
        track_page_version(page.id, json.dumps({"description_html": "<p>old</p>"}), str(create_user.id))

        version = PageVersion.objects.get(page=page)
        assert version.description_html == "<p>new</p>"
        assert version.description_json == {"type": "doc", "content": []}
        assert version.owned_by_id == create_user.id

    def test_reuses_recent_version_by_same_user(self, page, create_user):
        track_page_version(page.id, json.dumps({"description_html": "<p>old</p>"}), str(create_user.id))
        Page.objects.filter(id=page.id).update(description_html="<p>newer</p>", description_json={"v": 2})
        track_page_version(page.id, json.dumps({"description_html": "<p>new</p>"}), str(create_user.id))

        version = PageVersion.objects.get(page=page)
        assert version.description_html == "<p>newer</p>"
        assert version.description_json == {"v": 2}

    def test_no_version_when_html_unchanged(self, page, create_user):
        track_page_version(page.id, json.dumps({"description_html": "<p>new</p>"}), str(create_user.id))

        assert not PageVersion.objects.filter(page=page).exists()
