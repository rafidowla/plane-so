# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — live team dashboard: ordering, today's totals,
range scope (admin = everyone, member = self), HARD redaction of private
projects, guests 403, untracked members absent, constant query count."""

import json
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time
from rest_framework import status

from plane.db.models import IssueWorklog, WorklogSource, WorklogTimer
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

NOW = utc(2026, 6, 22, 8, 0)  # 14:00 Dhaka, 22 June


def _live(client, s, **params):
    return client.get(ws_url(s, "time/live/"), params)


def _log(user, issue, minutes, logged_date, ended_at):
    return IssueWorklog.objects.create(
        project=issue.project,
        issue=issue,
        logged_by=user,
        duration=minutes,
        logged_date=logged_date,
        started_at=ended_at,
        ended_at=ended_at,
        source=WorklogSource.TIMER,
    )


def _run(user, issue, started_at):
    return WorklogTimer.objects.create(project=issue.project, issue=issue, user=user, started_at=started_at)


@pytest.fixture
def team(s2):
    """admin (test user) + three members; a "Secret" project only ``carol`` is in."""
    ws, admin, project = s2["workspace"], s2["user"], s2["project"]
    alice, bob, carol = (make_user(ws, name=n) for n in ("Alice", "Bob", "Carol"))
    for u in (alice, bob, carol):
        join(project, u)
    secret, st = make_project(ws, admin, "Secret Launch", "SEC")
    join(secret, carol)
    secret_issue = make_issue(secret, st["todo"], "Acquire competitor", admin, assignees=[carol])
    return {**s2, "alice": alice, "bob": bob, "carol": carol, "secret": secret, "secret_issue": secret_issue}


def _rows(r):
    assert r.status_code == status.HTTP_200_OK, r.content
    return {row["user"]["id"]: row for row in r.json()["rows"]}


class TestLiveDashboard:
    def test_ordering_running_newest_first_then_latest_stop(self, team):
        t = team
        _run(t["alice"], t["issue"], utc(2026, 6, 22, 6, 0))
        _run(t["bob"], t["issue"], utc(2026, 6, 22, 7, 0))
        _log(t["carol"], t["issue"], 30, date(2026, 6, 22), utc(2026, 6, 22, 5, 0))
        _log(t["user"], t["issue"], 30, date(2026, 6, 22), utc(2026, 6, 22, 6, 30))
        with freeze_time(NOW):
            r = _live(client_for(t["user"]), t)
        order = [row["user"]["id"] for row in r.json()["rows"]]
        assert order == [str(u.id) for u in (t["bob"], t["alice"], t["user"], t["carol"])]
        rows = _rows(r)
        assert rows[str(t["bob"].id)]["status"] == "running"
        assert rows[str(t["user"].id)]["status"] == "stopped"
        assert rows[str(t["user"].id)]["last_stopped_at"].startswith("2026-06-22T06:30:00")

    def test_today_totals_use_the_workspace_day_and_include_the_running_part(self, team):
        t = team
        # 22 June locally starts at 21 June 18:00 UTC.
        _log(t["alice"], t["issue"], 45, date(2026, 6, 22), utc(2026, 6, 22, 2, 0))
        _log(t["alice"], t["issue"], 60, date(2026, 6, 21), utc(2026, 6, 21, 17, 0))  # yesterday
        _run(t["alice"], t["issue"], utc(2026, 6, 22, 7, 30))
        _run(t["bob"], t["issue"], utc(2026, 6, 21, 17, 0))  # started yesterday, local
        with freeze_time(NOW):
            rows = _rows(_live(client_for(t["user"]), t))
        a, b = rows[str(t["alice"].id)], rows[str(t["bob"].id)]
        assert (a["today_logged_minutes"], a["today_minutes"]) == (45, 75)
        # Only the part since local midnight counts toward today.
        assert (b["today_logged_minutes"], b["today_minutes"]) == (0, 14 * 60)

    def test_admin_gets_everyones_range_member_only_their_own(self, team):
        t = team
        _log(t["alice"], t["issue"], 60, date(2026, 6, 20), utc(2026, 6, 20, 5, 0))
        _log(t["bob"], t["issue"], 30, date(2026, 6, 21), utc(2026, 6, 21, 5, 0))
        params = {"start_date": "2026-06-15", "end_date": "2026-06-21"}
        with freeze_time(NOW):
            admin = _live(client_for(t["user"]), t, **params)
            member = _live(client_for(t["alice"]), t, **params)
        assert admin.json()["scope"] == "all"
        arows = _rows(admin)
        assert arows[str(t["alice"].id)]["range"]["total_minutes"] == 60
        assert arows[str(t["bob"].id)]["range"]["projects"][0]["minutes"] == 30
        assert member.json()["scope"] == "self"
        mrows = _rows(member)
        assert mrows[str(t["alice"].id)]["range"]["total_minutes"] == 60
        assert mrows[str(t["bob"].id)]["range"] is None
        # Others still show live status and today's total.
        assert mrows[str(t["bob"].id)]["status"] == "stopped"
        assert "today_minutes" in mrows[str(t["bob"].id)]

    def test_private_project_is_redacted_in_the_raw_json(self, team):
        t = team
        _run(t["carol"], t["secret_issue"], utc(2026, 6, 22, 7, 0))
        _log(t["carol"], t["secret_issue"], 50, date(2026, 6, 22), utc(2026, 6, 22, 6, 0))
        _log(t["carol"], t["issue"], 20, date(2026, 6, 22), utc(2026, 6, 22, 5, 0))
        # Even a WORKSPACE admin who is not in the project sees it as private.
        for viewer in (t["user"], t["alice"]):
            with freeze_time(NOW):
                r = _live(client_for(viewer), t)
            raw = r.content.decode()
            for leaked in (
                "Acquire competitor",
                "Secret Launch",
                "SEC-",
                '"SEC"',
                str(t["secret"].id),
                str(t["secret_issue"].id),
            ):
                assert leaked not in raw, leaked
            row = _rows(r)[str(t["carol"].id)]
            assert row["timer"] == {
                "started_at": row["timer"]["started_at"],
                "private": True,
                "label": "private project",
                "issue": None,
                "project": None,
            }
            assert row["today_minutes"] == 50 + 20 + 60
        rng = _rows(_live(client_for(t["user"]), t, start_date="2026-06-22", end_date="2026-06-22"))[
            str(t["carol"].id)
        ]["range"]
        assert rng["total_minutes"] == 70
        assert [p["identifier"] for p in rng["projects"]] == ["TTP"]
        assert rng["other_projects"] == {"label": "Other projects", "minutes": 50}

    def test_project_member_sees_the_details(self, team):
        t = team
        _run(t["carol"], t["secret_issue"], utc(2026, 6, 22, 7, 0))
        with freeze_time(NOW):
            row = _rows(_live(client_for(t["carol"]), t))[str(t["carol"].id)]
        assert row["timer"]["private"] is False
        assert row["timer"]["issue"]["ref"] == f"SEC-{t['secret_issue'].sequence_id}"
        assert row["timer"]["issue"]["name"] == "Acquire competitor"
        assert row["timer"]["project"]["name"] == "Secret Launch"

    def test_project_guest_counts_as_not_a_member(self, team):
        t = team
        join(t["secret"], t["alice"], role=5)
        _run(t["carol"], t["secret_issue"], utc(2026, 6, 22, 7, 0))
        with freeze_time(NOW):
            raw = _live(client_for(t["alice"]), t).content.decode()
        assert "Acquire competitor" not in raw and "Secret Launch" not in raw

    def test_workspace_guest_is_403(self, team):
        guest = make_user(team["workspace"], ws_role=5)
        assert _live(client_for(guest), team).status_code == status.HTTP_403_FORBIDDEN

    def test_untracked_members_and_guests_are_absent(self, team):
        t = team
        guest = make_user(t["workspace"], ws_role=5)
        with freeze_time(NOW):
            set_member_tracking(t["workspace"].id, t["bob"].id, False, date(2026, 6, 22), t["user"])
            rows = _rows(_live(client_for(t["user"]), t))
        assert str(t["bob"].id) not in rows
        assert str(guest.id) not in rows
        assert str(t["alice"].id) in rows

    def test_payload_is_an_allow_list(self, team):
        t = team
        _run(t["alice"], t["issue"], utc(2026, 6, 22, 7, 0))
        with freeze_time(NOW):
            d = _live(client_for(t["user"]), t).json()
        assert set(d) == {"server_now", "date", "timezone", "start_date", "end_date", "scope", "rows"}
        assert d["date"] == "2026-06-22" and d["timezone"] == "Asia/Dhaka"
        row = next(r for r in d["rows"] if r["user"]["id"] == str(t["alice"].id))
        assert set(row) == {
            "user",
            "is_me",
            "status",
            "timer",
            "last_stopped_at",
            "today_logged_minutes",
            "today_minutes",
            "range",
        }
        assert set(row["user"]) == {"id", "display_name", "first_name", "last_name", "avatar_url"}
        assert "email" not in json.dumps(d)

    def test_bad_range_is_400(self, team):
        c = client_for(team["user"])
        assert _live(c, team, start_date="nope").status_code == 400
        assert _live(c, team, start_date="2026-06-22", end_date="2026-06-01").status_code == 400
        assert _live(c, team, start_date="2025-01-01", end_date="2026-06-01").status_code == 400

    def test_query_count_does_not_grow_with_the_team(self, team):
        t = team

        def count():
            with freeze_time(NOW), CaptureQueriesContext(connection) as ctx:
                assert (
                    _live(client_for(t["user"]), t, start_date="2026-06-01", end_date="2026-06-22").status_code == 200
                )
            return len(ctx.captured_queries)

        _run(t["alice"], t["issue"], utc(2026, 6, 22, 7, 0))
        _log(t["bob"], t["issue"], 30, date(2026, 6, 22), utc(2026, 6, 22, 5, 0))
        before = count()
        for i in range(6):
            u = make_user(t["workspace"], name=f"Extra{i}")
            join(t["project"], u)
            issue = make_issue(t["project"], t["states"]["todo"], f"X{i}", t["user"], assignees=[u])
            _run(u, issue, utc(2026, 6, 22, 6, i))
            _log(u, issue, 10, date(2026, 6, 22), utc(2026, 6, 22, 5, i))
        assert count() == before
