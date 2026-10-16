# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — the timer bar's "my tasks" picker."""

from datetime import date, timedelta

import pytest
from django.utils import timezone
from freezegun import freeze_time
from rest_framework import status

from plane.db.models import Issue
from plane.utils.time_tracking.tracking import set_member_tracking

from .time_tracking_s2_support import (  # noqa: F401 (fixtures)
    _load_urlconf,
    client_for,
    join,
    make_issue,
    make_project,
    make_user,
    utc,
    ws_url,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


def _get(client, s, **params):
    return client.get(ws_url(s, "time/my-tasks/"), params)


def _names(r):
    assert r.status_code == status.HTTP_200_OK, r.json()
    return [row["name"] for row in r.json()["results"]]


class TestMyTasks:
    def test_assigned_items_only_done_in_cancelled_out(self, s2):
        me, st, project = s2["user"], s2["states"], s2["project"]
        other = make_user(s2["workspace"])
        join(project, other)
        make_issue(project, st["done"], "Finished thing", me, assignees=[me])
        make_issue(project, st["cancelled"], "Dropped thing", me, assignees=[me])
        make_issue(project, st["todo"], "Someone else's", me, assignees=[other])
        make_issue(project, st["todo"], "Unassigned", me)
        names = _names(_get(client_for(me), s2, limit=50))
        assert sorted(names) == ["Finished thing", "Issue 1"]

    def test_search_by_identifier_and_title(self, s2):
        me = s2["user"]
        done = make_issue(s2["project"], s2["states"]["done"], "Write the report", me, assignees=[me])
        c = client_for(me)
        assert _names(_get(c, s2, q="report")) == ["Write the report"]
        assert _names(_get(c, s2, q=f"TTP-{done.sequence_id}")) == ["Write the report"]
        assert _names(_get(c, s2, q=f"ttp{done.sequence_id}")) == ["Write the report"]
        assert _names(_get(c, s2, q=str(done.sequence_id))) == ["Write the report"]
        assert _names(_get(c, s2, q="nothing like this")) == []
        row = _get(c, s2, q="report").json()["results"][0]
        assert set(row) == {
            "id",
            "name",
            "sequence_id",
            "ref",
            "project_id",
            "project_identifier",
            "state_id",
            "state_name",
            "state_group",
            "updated_at",
        }
        assert row["ref"] == f"TTP-{done.sequence_id}"
        assert row["state_group"] == "completed"

    def test_five_most_recent_without_a_query(self, s2):
        me = s2["user"]
        base = timezone.now() - timedelta(days=1)
        for i in range(7):
            issue = make_issue(s2["project"], s2["states"]["todo"], f"T{i}", me, assignees=[me])
            Issue.objects.filter(pk=issue.pk).update(updated_at=base + timedelta(minutes=i))
        Issue.objects.filter(pk=s2["issue"].pk).update(updated_at=base - timedelta(days=1))
        assert _names(_get(client_for(me), s2)) == ["T6", "T5", "T4", "T3", "T2"]

    def test_untracked_projects_and_guest_projects_are_left_out(self, s2):
        me = s2["user"]
        off, st_off = make_project(s2["workspace"], me, "Off", "OFF", tracked=False)
        join(off, me, role=20)
        make_issue(off, st_off["todo"], "Not tracked", me, assignees=[me])
        guest_in, st_g = make_project(s2["workspace"], me, "Client", "CLI")
        join(guest_in, me, role=5)
        make_issue(guest_in, st_g["todo"], "As guest", me, assignees=[me])
        assert _names(_get(client_for(me), s2, limit=50)) == ["Issue 1"]

    def test_untracked_member_gets_an_empty_list(self, s2):
        with freeze_time(utc(2026, 6, 22, 4, 0)):
            set_member_tracking(s2["workspace"].id, s2["user"].id, False, date(2026, 6, 22), s2["user"])
            r = _get(client_for(s2["user"]), s2)
        assert r.json() == {"tracked": False, "results": []}

    def test_workspace_guest_is_403(self, s2):
        guest = make_user(s2["workspace"], ws_role=5)
        assert _get(client_for(guest), s2).status_code == status.HTTP_403_FORBIDDEN

    def test_bad_limit_is_400(self, s2):
        assert _get(client_for(s2["user"]), s2, limit="x").status_code == status.HTTP_400_BAD_REQUEST
