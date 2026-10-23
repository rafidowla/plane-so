# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S5 — the HARD report scope fix (member = own only, project
admin = own projects' members + "Other projects", workspace admin = all, guest 403),
for Reports v2, its CSV and the legacy ``time-report/``."""

import uuid

import pytest
from freezegun import freeze_time

from .time_tracking_s2_support import _load_urlconf, client_for, join, make_issue, make_project, make_user, ws_url  # noqa: F401 (autouse fixture)
from .time_tracking_s5_support import AFTER_RANGE, OCT, csv_rows, log, member_row, report

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


@pytest.fixture
def org(s2):
    """P1 = TTP (pa administers it, m1 is a member); P2 = "Secret" (m1 and m2 only)."""
    ws = s2["workspace"]
    pa = make_user(ws, 15, "Pa")
    m1 = make_user(ws, 15, "Mone")
    m2 = make_user(ws, 15, "Mtwo")
    join(s2["project"], pa, role=20)
    join(s2["project"], m1, role=15)
    p2, states2 = make_project(ws, s2["user"], "Secret", "SEC")
    join(p2, m1, role=15)
    join(p2, m2, role=15)
    secret = make_issue(p2, states2["todo"], "Secret launch plan", s2["user"])
    log(s2, m1, OCT[1], 60, note="p1 work")
    log(s2, m1, OCT[2], 45, issue=secret, note="secret note")
    log(s2, m2, OCT[2], 30, issue=secret, note="m2 secret")
    log(s2, pa, OCT[1], 20)
    return {**s2, "pa": pa, "m1": m1, "m2": m2, "p2": p2, "secret": secret}


class TestMember:
    def test_member_sees_only_themselves(self, org):
        with freeze_time(AFTER_RANGE):
            data = report(org["m1"], org)
        assert data["scope"] == "self"
        assert data["user"]["user_id"] == str(org["m1"].id)
        assert data["tiles"]["logged_minutes"] == 105  # all of their own time, both projects
        assert [p["user_id"] for p in data["people"]] == [str(org["m1"].id)]
        # Their own time is all detail, including the project the viewer is in.
        assert data["projects"]["other_projects"]["logged_minutes"] == 0

    @pytest.mark.parametrize("target", ["team", "other", "random"])
    def test_member_cannot_ask_for_others(self, org, target):
        user = {"team": "team", "other": org["m2"].id, "random": uuid.uuid4()}[target]
        with freeze_time(AFTER_RANGE):
            body = report(org["m1"], org, user=user, expect=403)
        assert body["code"] == ("forbidden_scope" if target == "team" else "forbidden_user")
        assert "secret" not in str(body).lower()

    def test_member_csv_is_own_only(self, org):
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(org["m1"], org)
        assert {r[1] for r in rows[1:]} == {org["m1"].display_name}
        with freeze_time(AFTER_RANGE):
            path = f"time/report.csv?from=2026-10-01&to=2026-10-07&user={org['m2'].id}"
            r = client_for(org["m1"]).get(ws_url(org, path))
        assert r.status_code == 403

    def test_legacy_report_is_own_only_and_user_ids_idor_is_403(self, org):
        c = client_for(org["m2"])
        r = c.get(ws_url(org, "time-report/?group_by=resource"))
        assert r.status_code == 200
        assert [g["key"] for g in r.data["groups"]] == [str(org["m2"].id)]
        assert r.data["totals"]["total_minutes"] == 30
        r = c.get(ws_url(org, f"time-report/?group_by=resource&user_ids={org['m1'].id}"))
        assert r.status_code == 403
        assert r.json()["code"] == "forbidden_user"

    def test_guest_is_403_everywhere(self, org):
        guest = make_user(org["workspace"], 5, "Guest")
        c = client_for(guest)
        for path in ("time/report/", "time/report.csv", "time-report/?group_by=resource"):
            r = c.get(ws_url(org, path))
            assert r.status_code == 403, path
            assert "display_name" not in str(r.content)


class TestProjectAdmin:
    def test_team_is_their_projects_members_with_other_projects_bucket(self, org):
        with freeze_time(AFTER_RANGE):
            data = report(org["pa"], org)  # default: team
        assert data["scope"] == "projects"
        ids = {m["user_id"] for m in data["members"]}
        assert ids == {str(org["user"].id), str(org["pa"].id), str(org["m1"].id)}
        assert str(org["m2"].id) not in {p["user_id"] for p in data["people"]}
        # m1's Secret time is counted (Logged / Balance stay true) but nameless.
        assert member_row(data, org["m1"])["logged_minutes"] == 105
        assert data["tiles"]["logged_minutes"] == 125
        assert data["projects"]["other_projects"] == {"label": "Other projects", "logged_minutes": 45}
        assert {p["project_id"] for p in data["projects"]["results"]} == {str(org["project"].id)}
        assert data["tasks"]["other_projects_minutes"] == 45
        assert str(org["secret"].id) not in str(data)
        assert "Secret" not in str(data)

    def test_cannot_open_someone_outside_their_projects(self, org):
        with freeze_time(AFTER_RANGE):
            body = report(org["pa"], org, user=org["m2"].id, expect=403)
        assert body["code"] == "forbidden_user"

    def test_can_open_one_of_their_members(self, org):
        with freeze_time(AFTER_RANGE):
            data = report(org["pa"], org, user=org["m1"].id)
        assert data["tiles"]["logged_minutes"] == 105
        assert data["projects"]["other_projects"]["logged_minutes"] == 45

    def test_csv_folds_other_projects(self, org):
        with freeze_time(AFTER_RANGE):
            rows = csv_rows(org["pa"], org)
        body = [dict(zip(rows[0], r)) for r in rows[1:]]
        secret = [r for r in body if r["Project"] == "Other projects"]
        assert len(secret) == 1
        assert secret[0]["Member"] == org["m1"].display_name
        assert (secret[0]["Task ID"], secret[0]["Task title"], secret[0]["Note"]) == ("", "", "")
        assert secret[0]["Minutes"] == "45"
        assert "secret" not in str(rows).lower()
        assert org["m2"].display_name not in str(rows)

    def test_legacy_groupings_fold_other_projects(self, org):
        c = client_for(org["pa"])
        r = c.get(ws_url(org, "time-report/?group_by=project"))
        assert r.status_code == 200
        names = [g["name"] for g in r.data["groups"]]
        assert "Secret" not in names
        other = next(g for g in r.data["groups"] if g.get("is_other"))
        assert (other["name"], other["total_minutes"], other["key"]) == ("Other projects", 45, None)
        assert r.data["totals"]["total_minutes"] == 125
        r = c.get(ws_url(org, "time-report/?group_by=issue"))
        assert "Secret launch plan" not in str(r.data)
        # A project filter on someone else's project never reaches their rows.
        r = c.get(ws_url(org, f"time-report/?group_by=resource&project_ids={org['p2'].id}"))
        assert r.data["totals"]["total_minutes"] == 0
        r = c.get(ws_url(org, f"time-report/?group_by=resource&user_ids={org['m2'].id}"))
        assert r.status_code == 403

    def test_legacy_resource_csv_breakdown_folds_other_projects(self, org):
        r = client_for(org["pa"]).get(ws_url(org, "time-report/?group_by=resource&export=csv"))
        text = r.content.decode()
        assert "Secret launch plan" not in text and "Secret" not in text
        assert "Other projects" in text


class TestWorkspaceAdmin:
    def test_sees_everyone_and_every_project(self, org):
        with freeze_time(AFTER_RANGE):
            data = report(org["user"], org)
        assert data["scope"] == "all"
        assert {m["user_id"] for m in data["members"]} >= {str(org["m1"].id), str(org["m2"].id)}
        assert data["tiles"]["logged_minutes"] == 155
        assert data["projects"]["other_projects"]["logged_minutes"] == 0
        assert "Secret launch plan" in str(data["tasks"])

    def test_nonexistent_user_is_403_not_404(self, org):
        with freeze_time(AFTER_RANGE):
            body = report(org["user"], org, user=uuid.uuid4(), expect=403)
        assert body["code"] == "forbidden_user"
