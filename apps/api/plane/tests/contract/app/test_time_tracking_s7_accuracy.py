# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S7 — estimate accuracy (DESIGN §2 item 36, §6 S7).

Population (completed only, cancelled excluded), per-task / project / person metrics credited
to the original estimator, the ±20 % and 80 %-solo boundaries, trend bucketing in the
workspace timezone with the n < 5 flag, and the report scope matrix.
"""

import pytest
from freezegun import freeze_time

from plane.db.models import Issue

from . import time_tracking_s3_support as support
from .time_tracking_s2_support import client_for, utc
from .time_tracking_s3_support import NOW, _load_urlconf  # noqa: F401 (autouse fixture)
from .time_tracking_s7_support import accuracy_url, get, names, task

s3 = support.s3

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


class TestPopulation:
    def test_completed_only_cancelled_and_open_excluded(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 60, w, name="done")
        task(s3, s3["p"], 60, 60, w, group="cancelled", name="cancelled")
        task(s3, s3["p"], 60, 60, w, group="started", name="started")
        task(s3, s3["p"], 60, 60, w, group="unstarted", name="todo")
        data = get(w, s3)
        assert names(data) == {"done"}
        assert data["summary"]["n"] == 1

    def test_item_without_estimate_not_included(self, s3):
        from .time_tracking_s7_support import log, state
        from plane.db.models import Issue

        with freeze_time(NOW):
            issue = Issue(workspace=s3["workspace"], project=s3["p"], name="no est", state=state(s3["p"], "completed"))
            issue.save(created_by_id=s3["wsadmin"].id)
            log(s3, s3["p"], issue, s3["wsadmin"], 30, NOW)
        assert get(s3["wsadmin"], s3)["summary"]["n"] == 0

    def test_zero_time_excluded_and_counted(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 0, w, name="untracked")
        task(s3, s3["p"], 60, 60, w, name="tracked")
        data = get(w, s3)
        assert names(data) == {"tracked"}
        assert data["excluded"]["no_time"] == 1

    def test_grandfathered_excluded_by_default_and_opt_in(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 60, w, name="late", grandfathered=True)
        task(s3, s3["p"], 60, 60, w, name="normal")
        data = get(w, s3)
        assert names(data) == {"normal"}
        assert data["excluded"]["grandfathered"] == 1
        data = get(w, s3, include_grandfathered=1)
        assert names(data) == {"late", "normal"}
        assert data["excluded"]["grandfathered"] == 0
        assert {t["name"]: t["grandfathered"] for t in data["tasks"]["results"]} == {"late": True, "normal": False}

    def test_reopened_item_leaves_the_population(self, s3):
        w = s3["wsadmin"]
        issue = task(s3, s3["p"], 60, 60, w, name="reopened")
        from .time_tracking_s7_support import state

        issue.state = state(s3["p"], "started")
        issue.save()
        assert get(w, s3)["summary"]["n"] == 0

    def test_archived_and_deleted_items_are_kept(self, s3):
        w = s3["wsadmin"]
        archived = task(s3, s3["p"], 60, 60, w, name="archived")
        deleted = task(s3, s3["p"], 60, 90, w, name="deleted")
        with freeze_time(NOW):
            Issue.all_objects.filter(pk=archived.pk).update(archived_at=NOW)
            Issue.all_objects.filter(pk=deleted.pk).update(deleted_at=NOW)
        data = get(w, s3)
        assert names(data) == {"archived", "deleted"}
        assert {t["name"]: t["is_deleted"] for t in data["tasks"]["results"]} == {"archived": False, "deleted": True}

    def test_actual_counts_everyones_time(self, s3):
        t = task(s3, s3["p"], 100, 150, s3["member"], worker=s3["member2"], estimator_minutes=50)
        row = get(s3["wsadmin"], s3)["tasks"]["results"][0]
        assert (row["actual_minutes"], row["estimator_minutes"]) == (150, 50)
        assert row["issue_id"] == str(t.id)


class TestMetrics:
    def test_task_ratio_error_and_vs_current(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 100, 150, w, current=120)
        row = get(w, s3)["tasks"]["results"][0]
        assert row["original_minutes"] == 100 and row["current_minutes"] == 120
        assert row["ratio"] == 1.5 and row["error_pct"] == 50.0
        assert row["ratio_current"] == 1.25 and row["error_pct_current"] == 25.0
        assert row["reestimated"] is True
        assert row["within"] is False and row["within_current"] is False

    def test_original_is_the_baseline_for_project_metrics(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 100, 100, w, current=50)  # re-estimated down; original was spot on
        data = get(w, s3)
        assert data["summary"]["ratio"] == 1.0 and data["summary"]["ratio_current"] == 2.0
        assert data["summary"]["median_abs_error_pct"] == 0.0

    def test_project_sums_median_and_within_share(self, s3):
        w = s3["wsadmin"]
        # errors: -50 %, 0 %, +30 %, +100 %  (originals 100,100,100,100; actuals 50,100,130,200)
        for actual in (50, 100, 130, 200):
            task(s3, s3["p"], 100, actual, w)
        task(s3, s3["q"], 60, 60, w)
        data = get(w, s3)
        p = next(r for r in data["projects"] if r["project_id"] == str(s3["p"].id))
        assert p["n"] == 4 and p["original_minutes"] == 400 and p["actual_minutes"] == 480
        assert p["ratio"] == 1.2
        assert p["median_abs_error_pct"] == 40.0  # median of 50, 0, 30, 100 -> (30 + 50) / 2
        assert p["within_count"] == 1 and p["within_share"] == 0.25
        assert p["low_sample"] is True
        q = next(r for r in data["projects"] if r["project_id"] == str(s3["q"].id))
        assert q["n"] == 1 and q["ratio"] == 1.0

    def test_within_band_boundary_is_inclusive(self, s3):
        w = s3["wsadmin"]
        for actual in (80, 120, 79, 121):
            task(s3, s3["p"], 100, actual, w, name=f"a{actual}")
        within = {t["name"]: t["within"] for t in get(w, s3)["tasks"]["results"]}
        assert within == {"a80": True, "a120": True, "a79": False, "a121": False}

    def test_people_credited_to_original_estimator(self, s3):
        w = s3["wsadmin"]
        # member estimated both; member2 and wsadmin did the work.
        task(s3, s3["p"], 100, 100, s3["member"], worker=s3["member2"])
        task(s3, s3["p"], 100, 200, s3["member"], worker=w)
        task(s3, s3["p"], 100, 100, s3["member2"])
        people = {p["user"]["id"]: p for p in get(w, s3)["people"]}
        assert set(people) == {str(s3["member"].id), str(s3["member2"].id)}
        assert people[str(s3["member"].id)]["n"] == 2
        assert people[str(s3["member"].id)]["ratio"] == 1.5
        assert people[str(s3["member2"].id)]["n"] == 1

    def test_estimate_without_estimator_is_a_null_person(self, s3):
        w = s3["wsadmin"]
        t = task(s3, s3["p"], 100, 100, s3["member"])
        from plane.db.models import IssueTimeEstimate

        IssueTimeEstimate.objects.filter(issue=t).update(original_by=None)
        people = get(w, s3)["people"]
        assert [p["user"] for p in people] == [None]

    def test_empty_population_has_null_ratios(self, s3):
        data = get(s3["wsadmin"], s3)
        assert data["summary"]["n"] == 0 and data["summary"]["ratio"] is None
        assert data["tasks"]["results"] == [] and data["trend"] == []


class TestSolo:
    @pytest.mark.parametrize("estimator_minutes,solo", [(80, True), (79, False), (100, True), (50, False)])
    def test_boundary_79_80_percent(self, s3, estimator_minutes, solo):
        w = s3["wsadmin"]
        task(s3, s3["p"], 100, 100, s3["member"], worker=s3["member2"], estimator_minutes=estimator_minutes)
        row = get(w, s3)["tasks"]["results"][0]
        assert row["solo"] is solo
        assert row["estimator_share"] == estimator_minutes / 100
        filtered = get(w, s3, solo=1)
        assert filtered["summary"]["n"] == (1 if solo else 0)
        assert filtered["excluded"]["not_solo"] == (0 if solo else 1)

    def test_solo_filter_applies_to_every_table_and_the_trend(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 100, 100, s3["member"], name="solo")
        task(s3, s3["p"], 100, 100, s3["member"], worker=s3["member2"], estimator_minutes=10, name="team")
        data = get(w, s3, solo=1)
        assert names(data) == {"solo"}
        assert data["solo"] is True
        assert data["projects"][0]["n"] == 1 and data["people"][0]["n"] == 1
        assert sum(b["n"] for b in data["trend"]) == 1
        assert get(w, s3)["summary"]["solo_count"] == 1


class TestTrend:
    def test_week_buckets_use_workspace_timezone(self, s3):
        w = s3["wsadmin"]
        # Dhaka is UTC+6: Sun 21 Jun 23:59 Dhaka = 17:59 UTC; Mon 22 Jun 00:00 Dhaka = 18:00 UTC.
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 21, 17, 59), name="sun")
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 21, 18, 0), name="mon")
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 28, 17, 59), name="sun2")
        data = get(w, s3)
        assert data["timezone"] == "Asia/Dhaka"
        assert {str(b["bucket"]): b["n"] for b in data["trend"]} == {"2026-06-15": 1, "2026-06-22": 2}

    def test_month_buckets_use_workspace_timezone(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 5, 31, 17, 59))  # 31 May 23:59 Dhaka
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 5, 31, 18, 0))  # 1 Jun 00:00 Dhaka
        data = get(w, s3, frm="2026-05-01", to="2026-06-30", granularity="month")
        assert {str(b["bucket"]): b["n"] for b in data["trend"]} == {"2026-05-01": 1, "2026-06-01": 1}

    def test_range_is_by_local_completion_date(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 14, 17, 59), name="before")  # 14 Jun Dhaka
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 14, 18, 0), name="in")  # 15 Jun Dhaka
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 15, 17, 59), name="in2")  # 15 Jun 23:59 Dhaka
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 15, 18, 0), name="after")  # 16 Jun Dhaka
        assert names(get(w, s3, "2026-06-15", "2026-06-15")) == {"in", "in2"}

    def test_low_sample_flag_below_five(self, s3):
        w = s3["wsadmin"]
        for i in range(4):  # week of 15 Jun: 4 tasks
            task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 16 + i, 6, 0))
        for i in range(5):  # week of 22 Jun: 5 tasks
            task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 22 + i, 6, 0))
        data = get(w, s3)
        trend = {str(b["bucket"]): b for b in data["trend"]}
        assert trend["2026-06-15"]["n"] == 4 and trend["2026-06-15"]["low_sample"] is True
        assert trend["2026-06-22"]["n"] == 5 and trend["2026-06-22"]["low_sample"] is False
        assert data["thresholds"] == {"within_pct": 20, "solo_pct": 80, "min_sample": 5}

    def test_trend_bucket_metrics(self, s3):
        w = s3["wsadmin"]
        for actual in (60, 90):
            task(s3, s3["p"], 60, actual, w, done_at=utc(2026, 6, 23, 6, 0))
        bucket = get(w, s3)["trend"][0]
        assert bucket["ratio"] == 1.25 and bucket["within_share"] == 0.5 and bucket["median_abs_error_pct"] == 25.0

    def test_default_range_is_the_last_90_days(self, s3):
        w = s3["wsadmin"]
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 3, 27, 6, 0), name="in")  # 89 days before 24 Jun
        task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 3, 25, 6, 0), name="old")
        with freeze_time(NOW):
            r = client_for(w).get(f"/api/workspaces/{s3['slug']}/time/estimate-accuracy/")
        assert r.status_code == 200
        assert {t["name"] for t in r.data["tasks"]["results"]} == {"in"}
        assert str(r.data["from"]) == "2026-03-27" and str(r.data["to"]) == "2026-06-24"


class TestScope:
    @pytest.fixture
    def world(self, s3):
        p, q = s3["p"], s3["q"]
        return {
            "t1": task(s3, p, 60, 60, s3["member"], name="t1"),
            "t2": task(s3, p, 60, 60, s3["member2"], name="t2"),
            "t3": task(s3, q, 60, 60, s3["member"], name="t3"),
            "t4": task(s3, q, 60, 60, s3["pa_q"], name="t4"),
            "t5": task(s3, p, 60, 60, s3["pa"], name="t5"),
            "t6": task(s3, q, 60, 60, s3["pa"], name="t6"),
        }

    @pytest.mark.parametrize(
        "who,scope,expected",
        [
            ("wsadmin", "all", {"t1", "t2", "t3", "t4", "t5", "t6"}),
            ("pa", "projects", {"t1", "t2", "t5", "t6"}),  # admin of P + own estimate in Q
            ("pa_q", "projects", {"t3", "t4", "t6"}),
            ("member", "self", {"t1", "t3"}),
            ("member2", "self", {"t2"}),
        ],
    )
    def test_scope_matrix(self, s3, world, who, scope, expected):
        data = get(s3[who], s3)
        assert data["scope"] == scope
        assert names(data) == expected
        # every table is built from the visible rows only
        assert data["summary"]["n"] == len(expected)
        assert sum(r["n"] for r in data["projects"]) == len(expected)
        assert sum(r["n"] for r in data["people"]) == len(expected)
        assert sum(b["n"] for b in data["trend"]) == len(expected)

    def test_member_people_table_is_only_themselves(self, s3, world):
        people = get(s3["member"], s3)["people"]
        assert [p["user"]["id"] for p in people] == [str(s3["member"].id)]

    def test_guest_forbidden(self, s3, world):
        body = get(s3["guest"], s3, expect=403)
        assert "tasks" not in body and "summary" not in body

    def test_anonymous_rejected(self, s3, world):
        from rest_framework.test import APIClient

        assert APIClient().get(accuracy_url(s3)).status_code in (401, 403)

    def test_user_param_idor(self, s3, world):
        assert get(s3["member"], s3, user=s3["member2"].id, expect=403)["code"] == "forbidden_user"
        assert (
            get(s3["member"], s3, user="00000000-0000-0000-0000-000000000000", expect=403)["code"] == "forbidden_user"
        )
        assert get(s3["member"], s3, user="nope", expect=400)["code"] == "invalid_user"
        # a project admin may pick people in their projects, not people outside them
        assert names(get(s3["pa"], s3, user=s3["member2"].id)) == {"t2"}
        assert get(s3["pa"], s3, user=s3["pa_q"].id, expect=403)["code"] == "forbidden_user"
        assert names(get(s3["wsadmin"], s3, user=s3["member"].id)) == {"t1", "t3"}
        assert get(s3["wsadmin"], s3, user=s3["guest"].id)["summary"]["n"] == 0

    def test_project_param_idor(self, s3, world):
        # member2 is not in Q: Q is forbidden (exists or not), P works
        assert get(s3["member2"], s3, project=s3["q"].id, expect=403)["code"] == "forbidden_project"
        assert get(s3["member2"], s3, project="00000000-0000-0000-0000-000000000000", expect=403)["code"] == (
            "forbidden_project"
        )
        assert get(s3["member2"], s3, project="nope", expect=400)["code"] == "invalid_project"
        assert names(get(s3["member2"], s3, project=s3["p"].id)) == {"t2"}
        # a member of Q sees only their own estimates in it; pa (not in Q) is forbidden
        assert names(get(s3["member"], s3, project=s3["q"].id)) == {"t3"}
        assert get(s3["pa"], s3, project=s3["q"].id, expect=403)["code"] == "forbidden_project"
        assert names(get(s3["wsadmin"], s3, project=s3["q"].id)) == {"t3", "t4", "t6"}

    def test_other_workspace_project_is_forbidden(self, s3, world):
        from plane.db.models import Project, Workspace, User

        owner = User.objects.create(email="o@x.io", username="o_x")
        other_ws = Workspace.objects.create(name="Other", slug="other-ws", owner=owner)
        other = Project.objects.create(workspace=other_ws, name="O", identifier="OO", created_by=owner)
        assert get(s3["wsadmin"], s3, project=other.id, expect=403)["code"] == "forbidden_project"


class TestParams:
    def test_invalid_params(self, s3):
        w = s3["wsadmin"]
        assert get(w, s3, frm="x", expect=400)["code"] == "invalid_range"
        assert get(w, s3, frm="2026-06-30", to="2026-06-01", expect=400)["code"] == "invalid_range"
        assert get(w, s3, frm="2025-01-01", to="2026-06-01", expect=400)["code"] == "invalid_range"
        assert get(w, s3, granularity="day", expect=400)["code"] == "invalid_granularity"
        assert get(w, s3, task_limit=0, expect=400)["code"] == "invalid_limit"
        assert get(w, s3, task_limit=501, expect=400)["code"] == "invalid_limit"
        assert get(w, s3, task_limit="x", expect=400)["code"] == "invalid_limit"

    def test_366_day_range_ok(self, s3):
        assert get(s3["wsadmin"], s3, frm="2025-06-01", to="2026-05-31")["summary"]["n"] == 0

    def test_task_limit_truncates_but_totals_cover_everything(self, s3):
        w = s3["wsadmin"]
        for i in range(3):
            task(s3, s3["p"], 60, 60, w, done_at=utc(2026, 6, 10 + i, 6, 0), name=f"t{i}")
        data = get(w, s3, task_limit=2)
        assert [t["name"] for t in data["tasks"]["results"]] == ["t2", "t1"]  # newest first
        assert data["tasks"]["truncated"] is True and data["tasks"]["total"] == 3
        assert data["summary"]["n"] == 3

    def test_population_cap(self, s3, monkeypatch):
        from plane.app.views.time_tracking import estimate_accuracy

        monkeypatch.setattr(estimate_accuracy, "MAX_POPULATION", 2)
        for i in range(3):
            task(s3, s3["p"], 60, 60, s3["wsadmin"])
        assert get(s3["wsadmin"], s3, expect=400)["code"] == "too_many_tasks"
