# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S4 — who may open whose timesheet (IDOR matrix) + the people list.

Projects: P (admin ``pa``, members ``member``/``member2``), Q (admin ``pa_q``, member ``member``).
"""

import uuid
from datetime import timedelta

import pytest
from freezegun import freeze_time

from plane.db.models import WorkspaceMember

from .time_tracking_s2_support import client_for, make_user, ws_url
from .time_tracking_s3_support import MON, NOW, _load_urlconf  # noqa: F401 (_load_urlconf is autouse)
from .test_time_tracking_s4_timesheet import log, week
from .test_time_tracking_s4_timesheet import s3  # noqa: F401 (fixture)

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


@pytest.fixture
def busy(s3):  # noqa: F811
    """member: 60 in P, 30 in Q.  member2: 45 in P.  pa_q: 15 in Q."""
    log(s3, s3["member"], MON, 60)
    log(s3, s3["member"], MON, 30, issue=s3["issue_q"], hh=13)
    log(s3, s3["member2"], MON, 45)
    log(s3, s3["pa_q"], MON, 15, issue=s3["issue_q"])
    return s3


def people(viewer, s, expect=200, q=""):
    r = client_for(viewer).get(ws_url(s, f"time/timesheet/people/?q={q}"))
    assert r.status_code == expect, r.content
    return r.data if expect == 200 else r.json()


class TestViewing:
    def test_member_sees_only_themselves(self, busy):
        with freeze_time(NOW):
            own = week(busy["member"], busy, user_id=busy["member"].id)  # explicit self is fine
            assert own["is_self"] is True and own["totals"]["logged"] == 90
            for other in ("member2", "pa", "wsadmin", "pa_q"):
                body = week(busy["member"], busy, expect=403, user_id=busy[other].id)
                assert body["code"] == "not_allowed"
                assert "Mem2" not in str(body)

    def test_project_admin_sees_members_of_their_projects_only(self, busy):
        with freeze_time(NOW):
            data = week(busy["pa"], busy, user_id=busy["member2"].id)
            assert data["is_self"] is False and data["scoped"] is True
            assert data["totals"]["logged"] == 45 and data["totals"]["other_projects"] == 0
            assert data["viewer"]["can_add_time"] is False
            # pa_q is not in project P.
            assert week(busy["pa"], busy, expect=403, user_id=busy["pa_q"].id)["code"] == "not_allowed"

    def test_project_admin_gets_other_project_time_as_a_count_only(self, busy):
        with freeze_time(NOW):
            data = week(busy["pa"], busy, user_id=busy["member"].id)
        assert data["totals"]["logged"] == 60  # P only
        assert data["totals"]["other_projects"] == 30
        assert data["days"][0]["logged_minutes"] == 60 and data["days"][0]["other_projects_minutes"] == 30
        assert [p["project"]["identifier"] for p in data["projects"]] == ["PP"]
        text = str(data)
        assert str(busy["q"].id) not in text and "Q issue" not in text and "QQ" not in text
        assert str(busy["issue_q"].id) not in text
        groups = [g for d in data["entries_by_date"] for g in d["groups"]]
        assert len(groups) == 1 and groups[0]["issue"]["id"] == str(busy["issue"].id)

    def test_project_admin_pending_requests_are_scoped_too(self, busy):
        from .time_tracking_s3_support import submit

        wl = log(busy, busy["member"], MON + timedelta(days=1), 30, issue=busy["issue_q"])
        with freeze_time(NOW):
            r = submit(client_for(busy["member"]), busy, type="deletion", worklog_id=str(wl.id), reason="x")
            assert r.status_code == 201, r.content
            as_pa = week(busy["pa"], busy, user_id=busy["member"].id)
            as_pa_q = week(busy["pa_q"], busy, user_id=busy["member"].id)
        assert as_pa["pending_requests"] == []
        assert len(as_pa_q["pending_requests"]) == 1

    def test_project_admin_views_their_own_week_unscoped(self, busy):
        with freeze_time(NOW):
            data = week(busy["pa_q"], busy)
        assert data["scoped"] is False and data["totals"]["logged"] == 15

    def test_workspace_admin_sees_everyone_fully(self, busy):
        with freeze_time(NOW):
            data = week(busy["wsadmin"], busy, user_id=busy["member"].id)
        assert data["scoped"] is False and data["is_self"] is False
        assert data["totals"]["logged"] == 90 and data["totals"]["other_projects"] == 0
        assert {p["project"]["identifier"] for p in data["projects"]} == {"PP", "QQ"}
        assert data["user"]["id"] == str(busy["member"].id)

    def test_guest_is_refused_everywhere(self, busy):
        with freeze_time(NOW):
            assert client_for(busy["guest"]).get(ws_url(busy, "time/timesheet/")).status_code == 403
            assert (
                client_for(busy["guest"]).get(ws_url(busy, f"time/timesheet/?user_id={busy['member'].id}")).status_code
                == 403
            )
            assert client_for(busy["guest"]).get(ws_url(busy, "time/timesheet/people/")).status_code == 403

    def test_anonymous_and_outsider_are_refused(self, busy):
        from rest_framework.test import APIClient

        assert APIClient().get(ws_url(busy, "time/timesheet/")).status_code in (401, 403)
        outsider = make_user(busy["workspace"], 15, "Outsider")
        WorkspaceMember.objects.filter(member=outsider).delete()
        assert client_for(outsider).get(ws_url(busy, "time/timesheet/")).status_code == 403

    def test_bad_and_unknown_user_ids(self, busy):
        with freeze_time(NOW):
            assert week(busy["wsadmin"], busy, expect=400, user_id="not-a-uuid")["code"] == "invalid_filter"
            assert week(busy["wsadmin"], busy, expect=404, user_id=uuid.uuid4())["code"] == "not_found"
            # A project admin cannot probe for existence: unknown ids look like out-of-scope ones.
            assert week(busy["pa"], busy, expect=403, user_id=uuid.uuid4())["code"] == "not_allowed"

    def test_guests_cannot_be_opened_by_a_workspace_admin(self, busy):
        with freeze_time(NOW):
            assert week(busy["wsadmin"], busy, expect=404, user_id=busy["guest"].id)["code"] == "not_found"

    def test_other_workspace_member_is_not_reachable(self, busy, db):
        from plane.db.models import Workspace

        from .time_tracking_s2_support import make_user as mk

        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=busy["wsadmin"])
        stranger = mk(other_ws, 15, "Stranger")
        with freeze_time(NOW):
            assert week(busy["wsadmin"], busy, expect=404, user_id=stranger.id)["code"] == "not_found"


class TestPeople:
    def test_member_gets_nobody_else(self, busy):
        data = people(busy["member"], busy)
        assert data["results"] == [] and data["viewer_id"] == str(busy["member"].id)

    def test_project_admin_gets_their_projects_people(self, busy):
        ids = {p["id"] for p in people(busy["pa"], busy)["results"]}
        assert ids == {str(busy[k].id) for k in ("wsadmin", "member", "member2")}

    def test_workspace_admin_gets_all_internal_members_and_no_guests(self, busy):
        ids = {p["id"] for p in people(busy["wsadmin"], busy)["results"]}
        assert ids == {str(busy[k].id) for k in ("pa", "pa_q", "member", "member2")}

    def test_query_filters_by_name_and_is_bounded(self, busy):
        busy["member2"].display_name = "Zelda Quux"
        busy["member2"].save()
        found = people(busy["wsadmin"], busy, q="quux")["results"]
        assert [p["id"] for p in found] == [str(busy["member2"].id)]
        assert people(busy["wsadmin"], busy, q="x" * 500)["results"] == []
