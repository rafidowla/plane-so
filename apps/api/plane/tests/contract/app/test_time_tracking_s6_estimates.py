# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S6 — per-work-item time estimates (DESIGN §2 items 33-35).

* ``GET/PUT projects/<p>/issues/<i>/time-estimate/`` — set, replace before work starts,
  re-estimate after (original kept), history, permissions (guests 403).
* Lock: on move into Started/Completed, timer start, first time entry.
* "Required" gate: app PATCH/PUT and public API PATCH of the state, creation straight into a
  Started/Completed state (app and v1), intake accept, a move into a required project, timer start
  (both endpoints), manual entry, missing-time submit/approve — 400 ``estimate_required``;
  never into Cancelled, never for grandfathered items (the stored flag written when the mode
  became required — not the item's current state).
* Guests can't move an item into Started at all (state is not a guest field).
* Moving a work item to another project carries its estimate.
"""

from unittest import mock

import pytest
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Issue,
    IssueTimeEstimate,
    IssueTimeEstimateRevision,
    IssueWorklog,
    TimeTrackingAuditLog,
    WorklogTimer,
)
from plane.utils.time_tracking import entry_requests

from . import time_tracking_s3_support as support
from .time_tracking_s3_support import (  # noqa: F401 (_load_urlconf is an autouse fixture)
    NOW,
    _load_urlconf,
    act,
    client_for,
    iso,
    make_issue,
    missing,
    set_settings,
    utc,
    ws_url,
)

s3 = support.s3

pytestmark = [pytest.mark.contract, pytest.mark.django_db]

APP_VIEWS = "plane.app.views.issue.base"
API_VIEWS = "plane.api.views.issue"


@pytest.fixture(autouse=True)
def _no_side_effects():
    patches = [
        mock.patch(f"{APP_VIEWS}.issue_activity"),
        mock.patch(f"{APP_VIEWS}.model_activity"),
        mock.patch(f"{APP_VIEWS}.issue_description_version_task"),
        mock.patch(f"{APP_VIEWS}.recent_visited_task"),
        mock.patch(f"{API_VIEWS}.issue_activity"),
        mock.patch(f"{API_VIEWS}.model_activity"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture
def s6(s3):
    """S3's world plus states by name and a not-started item ``todo`` in P (estimates required)."""
    from plane.db.models import State

    p = s3["p"]
    s3["states"] = {s.name: s for s in State.objects.filter(project=p)}
    s3["q_states"] = {s.name: s for s in State.objects.filter(project=s3["q"])}
    s3["todo"] = make_issue(p, s3["states"]["Todo"], "Todo item", s3["wsadmin"], assignees=[s3["member"]])
    mode(p, "required")
    return s3


def mode(project, value):
    project.time_estimate_mode = value
    project.save(update_fields=["time_estimate_mode"])


def est_url(s, issue):
    return ws_url(s, f"projects/{issue.project_id}/issues/{issue.id}/time-estimate/")


def put_est(user, s, issue, minutes, **extra):
    return client_for(user).put(est_url(s, issue), {"minutes": minutes, **extra}, format="json")


def app_issue_url(s, issue):
    return ws_url(s, f"projects/{issue.project_id}/issues/{issue.id}/")


def move_state(user, s, issue, state, method="patch"):
    return getattr(client_for(user), method)(app_issue_url(s, issue), {"state_id": str(state.id)}, format="json")


def api_key_client(user):
    token = f"token-{user.username}"
    APIToken.objects.create(user=user, label=token, token=token)
    c = APIClient()
    c.credentials(HTTP_X_API_KEY=token)
    return c


def v1_issue_url(s, issue):
    return f"/api/v1/workspaces/{s['slug']}/projects/{issue.project_id}/issues/{issue.id}/"


def start_timer(user, s, issue, via="issue"):
    c = client_for(user)
    if via == "issue":
        return c.post(ws_url(s, f"projects/{issue.project_id}/issues/{issue.id}/worklog-timer/"), {}, format="json")
    return c.post(ws_url(s, "time/timer/start/"), {"issue_id": str(issue.id)}, format="json")


def estimate(issue):
    return IssueTimeEstimate.objects.get(issue=issue)


def assert_required(resp):
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.content
    body = resp.json()
    assert body["code"] == "estimate_required", body
    assert body["error"] == "Add an estimate first."


# ---------------------------------------------------------------------------
# GET / PUT
# ---------------------------------------------------------------------------


class TestEstimateEndpoint:
    def test_get_without_estimate(self, s6):
        r = client_for(s6["member"]).get(est_url(s6, s6["todo"]))
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["mode"] == "required" and body["required"] is True
        assert body["estimate"] is None and body["revisions"] == []
        assert body["work_started"] is False and body["grandfathered"] is False and body["can_edit"] is True

    def test_set_records_the_setter_and_history(self, s6):
        r = put_est(s6["member"], s6, s6["todo"], 120, reason="first guess")
        assert r.status_code == 200, r.content
        e = r.json()["estimate"]
        assert e["original_minutes"] == e["current_minutes"] == 120
        assert e["original_by"]["id"] == str(s6["member"].id) and e["current_by"]["id"] == str(s6["member"].id)
        assert e["locked"] is False and e["grandfathered"] is False
        [rev] = r.json()["revisions"]
        assert rev["kind"] == "original_set" and rev["minutes"] == 120 and rev["reason"] == "first guess"
        assert TimeTrackingAuditLog.objects.filter(
            target_type="estimate", target_id=estimate(s6["todo"]).id, action="create", actor=s6["member"]
        ).exists()

    def test_before_work_starts_the_original_is_replaced(self, s6):
        put_est(s6["member"], s6, s6["todo"], 120)
        r = put_est(s6["pa"], s6, s6["todo"], 90)
        e = r.json()["estimate"]
        assert e["original_minutes"] == e["current_minutes"] == 90
        assert e["original_by"]["id"] == str(s6["pa"].id)
        assert [x["kind"] for x in r.json()["revisions"]] == ["original_set", "original_replaced"]
        assert IssueTimeEstimate.objects.filter(issue=s6["todo"]).count() == 1

    def test_same_value_is_a_no_op(self, s6):
        put_est(s6["member"], s6, s6["todo"], 120)
        r = put_est(s6["member"], s6, s6["todo"], 120)
        assert r.status_code == 200 and len(r.json()["revisions"]) == 1

    def test_after_lock_a_new_value_is_a_re_estimate_and_original_stays(self, s6):
        put_est(s6["member"], s6, s6["todo"], 120)
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"]).status_code == 204
        r = put_est(s6["pa"], s6, s6["todo"], 200, reason="bigger than thought")
        assert r.status_code == 200, r.content
        e = r.json()["estimate"]
        assert e["locked"] is True
        assert e["original_minutes"] == 120 and e["original_by"]["id"] == str(s6["member"].id)
        assert e["current_minutes"] == 200 and e["current_by"]["id"] == str(s6["pa"].id)
        revs = r.json()["revisions"]
        assert [x["kind"] for x in revs] == ["original_set", "re_estimate"]
        assert revs[-1]["reason"] == "bigger than thought"
        # A second re-estimate appends again; the original still never moves.
        r = put_est(s6["member"], s6, s6["todo"], 180)
        assert r.json()["estimate"]["original_minutes"] == 120
        assert [x["kind"] for x in r.json()["revisions"]] == ["original_set", "re_estimate", "re_estimate"]

    def test_clear_before_lock_and_refuse_after(self, s6):
        put_est(s6["member"], s6, s6["todo"], 120)
        r = put_est(s6["member"], s6, s6["todo"], None)
        assert r.status_code == 200 and r.json()["estimate"] is None
        assert [x["kind"] for x in r.json()["revisions"]] == ["original_set", "cleared"]
        put_est(s6["member"], s6, s6["todo"], 60)
        move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"])
        r = put_est(s6["member"], s6, s6["todo"], None)
        assert r.status_code == 400 and r.json()["code"] == "estimate_locked"
        assert estimate(s6["todo"]).current_minutes == 60

    @pytest.mark.parametrize("bad", [0, -5, 60001, "abc", "1.5", 1.5, True, ""])
    def test_invalid_minutes(self, s6, bad):
        r = put_est(s6["member"], s6, s6["todo"], bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_minutes", r.content

    def test_missing_minutes_is_invalid(self, s6):
        r = client_for(s6["member"]).put(est_url(s6, s6["todo"]), {"reason": "x"}, format="json")
        assert r.status_code == 400 and r.json()["code"] == "invalid_minutes"

    def test_mode_off_refuses_writes_but_reads(self, s6):
        mode(s6["p"], "off")
        r = put_est(s6["member"], s6, s6["todo"], 60)
        assert r.status_code == 400 and r.json()["code"] == "estimates_off"
        g = client_for(s6["member"]).get(est_url(s6, s6["todo"])).json()
        assert g["mode"] == "off" and g["can_edit"] is False

    def test_tracking_disabled_counts_as_off(self, s6):
        s6["p"].is_time_tracking_enabled = False
        s6["p"].save(update_fields=["is_time_tracking_enabled"])
        assert client_for(s6["member"]).get(est_url(s6, s6["todo"])).json()["mode"] == "off"
        assert put_est(s6["member"], s6, s6["todo"], 60).json()["code"] == "estimates_off"

    def test_optional_mode_allows_estimates(self, s6):
        mode(s6["p"], "optional")
        r = put_est(s6["member"], s6, s6["todo"], 30)
        assert r.status_code == 200 and r.json()["required"] is False

    def test_guest_cannot_read_or_write(self, s6):
        c = client_for(s6["guest"])
        assert c.get(est_url(s6, s6["todo"])).status_code == status.HTTP_403_FORBIDDEN
        assert c.put(est_url(s6, s6["todo"]), {"minutes": 30}, format="json").status_code == 403
        assert not IssueTimeEstimate.objects.filter(issue=s6["todo"]).exists()

    def test_non_member_is_refused(self, s6):
        # pa_q is in Q only.
        c = client_for(s6["pa_q"])
        assert c.get(est_url(s6, s6["todo"])).status_code == status.HTTP_403_FORBIDDEN

    def test_item_of_another_project_is_404(self, s6):
        url = ws_url(s6, f"projects/{s6['p'].id}/issues/{s6['issue_q'].id}/time-estimate/")
        assert client_for(s6["member"]).get(url).status_code == 404
        assert client_for(s6["member"]).put(url, {"minutes": 5}, format="json").status_code == 404


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


class TestLock:
    def test_moving_to_started_locks(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        assert estimate(s6["todo"]).locked_at is None
        move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"])
        assert estimate(s6["todo"]).locked_at is not None

    def test_moving_to_completed_locks(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["Done"]).status_code == 204
        assert estimate(s6["todo"]).locked_at is not None

    def test_backlog_or_cancelled_does_not_lock(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        move_state(s6["member"], s6, s6["todo"], s6["states"]["Backlog"])
        move_state(s6["member"], s6, s6["todo"], s6["states"]["Cancelled"])
        assert estimate(s6["todo"]).locked_at is None

    def test_timer_start_locks(self, s6):
        set_settings(s6["workspace"], idle_check_min_minutes=0, idle_check_max_minutes=0)
        put_est(s6["member"], s6, s6["todo"], 60)
        r = start_timer(s6["member"], s6, s6["todo"])
        assert r.status_code in (200, 201), r.content
        assert estimate(s6["todo"]).locked_at is not None

    def test_first_entry_locks(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        support.entry(s6, s6["member"], utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0), issue=s6["todo"])
        assert estimate(s6["todo"]).locked_at is not None

    def test_estimate_set_after_work_started_is_locked_at_once(self, s6):
        mode(s6["p"], "optional")
        support.entry(s6, s6["member"], utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0), issue=s6["todo"])
        r = put_est(s6["member"], s6, s6["todo"], 60)
        assert r.json()["estimate"]["locked"] is True and r.json()["work_started"] is True
        assert r.json()["estimate"]["grandfathered"] is False  # optional mode: nothing to grandfather

    def test_bulk_state_change_is_caught_at_next_write(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        # e.g. a bulk_update path that sends no post_save
        Issue.objects.filter(pk=s6["todo"].pk).update(state=s6["states"]["In Progress"])
        r = put_est(s6["member"], s6, s6["todo"], 90)
        e = r.json()["estimate"]
        assert e["locked"] is True and e["original_minutes"] == 60 and e["current_minutes"] == 90
        assert r.json()["revisions"][-1]["kind"] == "re_estimate"


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class TestGateStateChange:
    @pytest.mark.parametrize("target", ["In Progress", "Done"])
    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_app_move_into_work_needs_an_estimate(self, s6, target, method):
        r = move_state(s6["member"], s6, s6["todo"], s6["states"][target], method=method)
        assert_required(r)
        assert r.json()["issue_id"] == str(s6["todo"].id)
        s6["todo"].refresh_from_db()
        assert s6["todo"].state_id == s6["states"]["Todo"].id

    def test_app_move_allowed_with_estimate(self, s6):
        put_est(s6["member"], s6, s6["todo"], 60)
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"]).status_code == 204

    def test_admin_is_gated_too(self, s6):
        assert_required(move_state(s6["wsadmin"], s6, s6["todo"], s6["states"]["In Progress"]))

    def test_cancelled_and_backlog_are_allowed(self, s6):
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["Backlog"]).status_code == 204
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["Cancelled"]).status_code == 204

    def test_other_field_edits_are_not_gated(self, s6):
        r = client_for(s6["member"]).patch(app_issue_url(s6, s6["todo"]), {"name": "renamed"}, format="json")
        assert r.status_code == 204, r.content

    def test_grandfathered_started_item_moves_freely(self, s6):
        # s3["issue"] sits in In Progress with no estimate (started before "required").
        issue = s6["issue"]
        assert move_state(s6["member"], s6, issue, s6["states"]["In Review"]).status_code == 204
        assert move_state(s6["member"], s6, issue, s6["states"]["Done"]).status_code == 204
        g = client_for(s6["member"]).get(est_url(s6, issue)).json()
        assert g["grandfathered"] is True and g["estimate"] is None

    def test_grandfathered_flag_is_stored_with_a_late_estimate(self, s6):
        r = put_est(s6["member"], s6, s6["issue"], 45)
        assert r.json()["estimate"]["grandfathered"] is True and r.json()["estimate"]["locked"] is True

    @pytest.mark.parametrize("value", ["off", "optional"])
    def test_off_and_optional_are_not_gated(self, s6, value):
        mode(s6["p"], value)
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"]).status_code == 204

    @pytest.mark.parametrize("target", ["In Progress", "Done"])
    def test_creation_in_a_started_state_is_gated(self, s6, target):
        r = client_for(s6["member"]).post(
            ws_url(s6, f"projects/{s6['p'].id}/issues/"),
            {"name": "born started", "state_id": str(s6["states"][target].id)},
            format="json",
        )
        assert_required(r)
        assert not Issue.objects.filter(name="born started").exists()

    def test_creation_in_a_not_started_state_is_allowed(self, s6):
        for target in ("Todo", "Backlog", "Cancelled"):
            r = client_for(s6["member"]).post(
                ws_url(s6, f"projects/{s6['p'].id}/issues/"),
                {"name": f"born {target}", "state_id": str(s6["states"][target].id)},
                format="json",
            )
            assert r.status_code == 201, r.content

    def test_creation_without_a_state_uses_the_default_state(self, s6):
        # The default state counts: a project whose default state is Started cannot be used to dodge the gate.
        from plane.db.models import State

        State.objects.filter(project=s6["p"]).update(default=False)
        State.objects.filter(pk=s6["states"]["In Progress"].id).update(default=True)
        r = client_for(s6["member"]).post(
            ws_url(s6, f"projects/{s6['p'].id}/issues/"), {"name": "born default"}, format="json"
        )
        assert_required(r)

    def test_v1_creation_in_a_started_state_is_gated(self, s6):
        url = f"/api/v1/workspaces/{s6['slug']}/projects/{s6['p'].id}/issues/"
        c = api_key_client(s6["member"])
        assert_required(
            c.post(url, {"name": "v1 started", "state": str(s6["states"]["In Progress"].id)}, format="json")
        )
        assert not Issue.objects.filter(name="v1 started").exists()
        r = c.post(url, {"name": "v1 todo", "state": str(s6["states"]["Todo"].id)}, format="json")
        assert r.status_code == 201, r.content

    @pytest.mark.parametrize("value", ["off", "optional"])
    def test_creation_in_a_started_state_is_open_when_not_required(self, s6, value):
        mode(s6["p"], value)
        r = client_for(s6["member"]).post(
            ws_url(s6, f"projects/{s6['p'].id}/issues/"),
            {"name": "born started", "state_id": str(s6["states"]["In Progress"].id)},
            format="json",
        )
        assert r.status_code == 201, r.content

    def test_v1_patch_is_gated(self, s6):
        c = api_key_client(s6["member"])
        r = c.patch(v1_issue_url(s6, s6["todo"]), {"state": str(s6["states"]["In Progress"].id)}, format="json")
        assert_required(r)
        s6["todo"].refresh_from_db()
        assert s6["todo"].state_id == s6["states"]["Todo"].id
        put_est(s6["member"], s6, s6["todo"], 60)
        r = c.patch(v1_issue_url(s6, s6["todo"]), {"state": str(s6["states"]["In Progress"].id)}, format="json")
        assert r.status_code == 200, r.content
        assert estimate(s6["todo"]).locked_at is not None

    def test_v1_cancel_is_allowed(self, s6):
        c = api_key_client(s6["member"])
        r = c.patch(v1_issue_url(s6, s6["todo"]), {"state": str(s6["states"]["Cancelled"].id)}, format="json")
        assert r.status_code == 200, r.content


class TestGuestPath:
    def test_guest_cannot_move_into_started(self, s6):
        """Guests may only edit name/description/priority/labels: a state change is refused
        before the serializer runs, so the item never reaches Started without an estimate."""
        own = Issue(workspace=s6["workspace"], project=s6["p"], name="guest item", state=s6["states"]["Todo"])
        own.save(created_by_id=s6["guest"].id)
        r = move_state(s6["guest"], s6, own, s6["states"]["In Progress"])
        assert r.status_code == 400
        assert r.json()["disallowed_fields"] == ["state_id"], r.content
        own.refresh_from_db()
        assert own.state_id == s6["states"]["Todo"].id

    def test_guest_v1_cannot_move_into_started(self, s6):
        own = Issue(workspace=s6["workspace"], project=s6["p"], name="guest item", state=s6["states"]["Todo"])
        own.save(created_by_id=s6["guest"].id)
        r = api_key_client(s6["guest"]).patch(
            v1_issue_url(s6, own), {"state": str(s6["states"]["In Progress"].id)}, format="json"
        )
        assert r.status_code in (400, 403), r.content
        own.refresh_from_db()
        assert own.state_id == s6["states"]["Todo"].id


class TestGateLogging:
    @pytest.fixture(autouse=True)
    def _idle_off(self, s6):
        set_settings(s6["workspace"], idle_check_min_minutes=0, idle_check_max_minutes=0)

    @pytest.mark.parametrize("via", ["issue", "workspace"])
    def test_timer_start_needs_an_estimate(self, s6, via):
        assert_required(start_timer(s6["member"], s6, s6["todo"], via=via))
        assert not WorklogTimer.objects.filter(user=s6["member"]).exists()
        s6["todo"].refresh_from_db()
        assert s6["todo"].state_id == s6["states"]["Todo"].id  # not auto-moved to Started
        put_est(s6["member"], s6, s6["todo"], 60)
        assert start_timer(s6["member"], s6, s6["todo"], via=via).status_code in (200, 201)

    def test_timer_on_grandfathered_item_is_allowed(self, s6):
        assert start_timer(s6["member"], s6, s6["issue"]).status_code in (200, 201)

    def test_timer_not_gated_when_optional(self, s6):
        mode(s6["p"], "optional")
        assert start_timer(s6["member"], s6, s6["todo"]).status_code in (200, 201)

    def test_manual_entry_needs_an_estimate(self, s6):
        data = {
            "issue_id": str(s6["todo"].id),
            "started_at": iso(utc(2026, 6, 22, 3, 0)),
            "ended_at": iso(utc(2026, 6, 22, 4, 0)),
        }
        with freeze_time(NOW):
            # the workspace admin adds time directly (no request needed)
            assert_required(client_for(s6["wsadmin"]).post(ws_url(s6, "time/entries/"), data, format="json"))
            put_est(s6["pa"], s6, s6["todo"], 60)
            r = client_for(s6["wsadmin"]).post(ws_url(s6, "time/entries/"), data, format="json")
        assert r.status_code == 201, r.content
        assert IssueWorklog.objects.filter(issue=s6["todo"]).exists()
        assert estimate(s6["todo"]).locked_at is not None

    def test_missing_time_request_needs_an_estimate(self, s6):
        start, end = utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)
        with freeze_time(NOW):
            assert_required(missing(client_for(s6["member"]), s6, start, end, issue=s6["todo"]))
            put_est(s6["member"], s6, s6["todo"], 60)
            r = missing(client_for(s6["member"]), s6, start, end, issue=s6["todo"])
        assert r.status_code == 201, r.content

    def test_missing_time_approval_needs_an_estimate(self, s6):
        start, end = utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)
        mode(s6["p"], "optional")
        with freeze_time(NOW):
            r = missing(client_for(s6["member"]), s6, start, end, issue=s6["todo"])
            assert r.status_code == 201, r.content
            rid = r.json()["id"]
            mode(s6["p"], "required")
            assert_required(act(client_for(s6["pa"]), s6, rid, "approve"))
            assert support.req(rid).status == "pending"
            put_est(s6["member"], s6, s6["todo"], 60)
            assert act(client_for(s6["pa"]), s6, rid, "approve").status_code == 200
        assert IssueWorklog.objects.filter(issue=s6["todo"]).exists()

    def test_service_level_approval_raises(self, s6):
        from plane.utils.time_tracking.timer import TimeTrackingError

        start, end = utc(2026, 6, 22, 3, 0), utc(2026, 6, 22, 4, 0)
        mode(s6["p"], "optional")
        with freeze_time(NOW):
            rid = missing(client_for(s6["member"]), s6, start, end, issue=s6["todo"]).json()["id"]
            mode(s6["p"], "required")
            with pytest.raises(TimeTrackingError) as exc:
                entry_requests.approve(s6["workspace"].id, s6["pa"], rid)
        assert exc.value.payload["code"] == "estimate_required"


# ---------------------------------------------------------------------------
# Move to another project
# ---------------------------------------------------------------------------


class TestMove:
    def test_estimate_and_history_follow_the_item(self, s6):
        from plane.utils.work_item_move import move_work_item

        put_est(s6["member"], s6, s6["todo"], 60)
        put_est(s6["member"], s6, s6["todo"], 75)
        mode(s6["q"], "optional")
        support.join(s6["q"], s6["wsadmin"], 20)
        move_work_item(Issue.objects.get(pk=s6["todo"].pk), s6["q"], s6["wsadmin"], notify=False)
        moved = Issue.objects.get(pk=s6["todo"].pk)
        assert moved.project_id == s6["q"].id
        e = IssueTimeEstimate.objects.get(issue=moved)
        assert e.project_id == s6["q"].id and e.original_minutes == 75
        assert IssueTimeEstimateRevision.objects.filter(issue=moved).count() == 2
        r = client_for(s6["member"]).get(est_url(s6, moved))
        assert r.status_code == 200 and r.json()["estimate"]["current_minutes"] == 75
        assert len(r.json()["revisions"]) == 2


class TestProjectSetting:
    def test_project_admin_sets_the_mode_through_the_project_update(self, s6):
        with mock.patch("plane.app.views.project.base.model_activity"):
            r = client_for(s6["wsadmin"]).patch(
                ws_url(s6, f"projects/{s6['q'].id}/"), {"time_estimate_mode": "required"}, format="json"
            )
        assert r.status_code == 200, r.content
        assert r.json()["time_estimate_mode"] == "required"
        s6["q"].refresh_from_db()
        assert s6["q"].time_estimate_mode == "required"


# ---------------------------------------------------------------------------
# Secfix 5 — the gate cannot be dodged: stored grandfather flag, intake accept, moves
# ---------------------------------------------------------------------------


def started_flags(project):
    from plane.db.models import IssueEstimateGrandfather

    return set(IssueEstimateGrandfather.objects.filter(project=project).values_list("issue_id", flat=True))


class TestGrandfatherFlag:
    def test_flip_to_required_flags_items_already_in_progress(self, s6):
        # s6 flipped P to required: the In Progress item was flagged, the Todo item was not.
        assert started_flags(s6["p"]) == {s6["issue"].id}

    def test_state_alone_does_not_grandfather(self, s6):
        # An item pushed into a Started state behind the gate's back (bulk update) is NOT grandfathered.
        Issue.objects.filter(pk=s6["todo"].pk).update(state=s6["states"]["In Progress"])
        assert_required(start_timer(s6["member"], s6, s6["todo"]))
        g = client_for(s6["member"]).get(est_url(s6, s6["todo"])).json()
        assert g["grandfathered"] is False
        # ...and the stored flag still covers the item that was running when the mode flipped.
        assert start_timer(s6["member"], s6, s6["issue"]).status_code in (200, 201)

    def test_item_started_in_optional_mode_is_flagged_on_the_later_flip(self, s6):
        mode(s6["p"], "optional")
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["In Progress"]).status_code == 204
        mode(s6["p"], "required")
        assert s6["todo"].id in started_flags(s6["p"])
        assert move_state(s6["member"], s6, s6["todo"], s6["states"]["Done"]).status_code == 204

    def test_reflip_flags_only_whats_in_progress_at_that_moment(self, s6):
        mode(s6["p"], "optional")
        later = make_issue(s6["p"], s6["states"]["In Progress"], "started while optional", s6["wsadmin"])
        mode(s6["p"], "required")
        assert later.id in started_flags(s6["p"]) and s6["todo"].id not in started_flags(s6["p"])

    def test_saving_a_required_project_again_does_not_reflag(self, s6):
        from plane.db.models import IssueEstimateGrandfather

        Issue.objects.filter(pk=s6["todo"].pk).update(state=s6["states"]["In Progress"])
        s6["p"].name = "Renamed"
        s6["p"].save()
        assert s6["todo"].id not in started_flags(s6["p"])
        assert IssueEstimateGrandfather.objects.filter(project=s6["p"]).count() == 1

    def test_turning_tracking_on_with_required_mode_flags_too(self, s6):
        s6["p"].is_time_tracking_enabled = False
        s6["p"].save()
        started = make_issue(s6["p"], s6["states"]["In Progress"], "started while dormant", s6["wsadmin"])
        s6["p"].is_time_tracking_enabled = True
        s6["p"].save()
        assert started.id in started_flags(s6["p"])

    def test_flip_through_the_project_update_endpoint(self, s6):
        q = s6["q"]
        started = make_issue(q, s6["q_states"]["In Progress"], "Q started", s6["wsadmin"])
        with mock.patch("plane.app.views.project.base.model_activity"):
            r = client_for(s6["wsadmin"]).patch(
                ws_url(s6, f"projects/{q.id}/"), {"time_estimate_mode": "required"}, format="json"
            )
        assert r.status_code == 200, r.content
        assert started.id in started_flags(q) and s6["issue_q"].id in started_flags(q)

    def test_items_with_an_estimate_are_not_flagged(self, s6):
        mode(s6["p"], "optional")
        put_est(s6["member"], s6, s6["issue"], 30)
        from plane.db.models import IssueEstimateGrandfather

        IssueEstimateGrandfather.objects.all().delete()
        mode(s6["p"], "required")
        assert started_flags(s6["p"]) == set()


class TestBackfillMigration:
    def test_backfill_flags_started_items_of_required_projects_only(self, s6):
        import importlib

        from django.db import connection

        from plane.db.models import IssueEstimateGrandfather

        mig = importlib.import_module("plane.db.migrations.0133_time_estimate_grandfather")
        q_started = make_issue(s6["q"], s6["q_states"]["In Progress"], "Q started", s6["wsadmin"])  # Q: mode off
        done = make_issue(s6["p"], s6["states"]["Done"], "P done", s6["wsadmin"])
        estimated = make_issue(s6["p"], s6["states"]["In Progress"], "P estimated", s6["wsadmin"])
        mode(s6["p"], "optional")
        put_est(s6["member"], s6, estimated, 30)
        IssueEstimateGrandfather.objects.all().delete()
        Issue.objects.filter(pk=done.pk)  # done is in the completed group
        mode_rows = Issue.objects.filter(project=s6["p"]).count()
        assert mode_rows >= 3
        # Set the mode without the receiver (what the table looked like before the migration).
        type(s6["p"]).objects.filter(pk=s6["p"].pk).update(time_estimate_mode="required")
        with connection.cursor() as cursor:
            cursor.execute(mig.BACKFILL_SQL)
            cursor.execute(mig.BACKFILL_SQL)  # idempotent
        assert started_flags(s6["p"]) == {s6["issue"].id, done.id}  # not the estimated one, not the Todo one
        assert started_flags(s6["q"]) == set() and q_started.id not in started_flags(s6["q"])


class TestIntakeAccept:
    @pytest.fixture
    def intake_item(self, s6):
        from plane.db.models import Intake, IntakeIssue, State

        p = s6["p"]
        p.intake_view = True
        p.save(update_fields=["intake_view"])
        triage = State.objects.create(
            workspace=s6["workspace"], project=p, name="Triage", group="triage", default=False
        )
        intake = Intake.objects.create(name="Intake", project=p, workspace=s6["workspace"], is_default=True)
        issue = Issue(workspace=s6["workspace"], project=p, name="from intake", state=triage)
        issue.save(created_by_id=s6["wsadmin"].id)
        IntakeIssue.objects.create(intake=intake, issue=issue, project=p, workspace=s6["workspace"], status=-2)
        # Accepting moves the item into the project's default state: make that a Started one.
        State.objects.filter(project=p).update(default=False)
        State.objects.filter(pk=s6["states"]["In Progress"].id).update(default=True)
        return issue, triage

    def test_app_accept_into_a_started_default_state_needs_an_estimate(self, s6, intake_item):
        issue, triage = intake_item
        url = ws_url(s6, f"projects/{s6['p'].id}/intake-issues/{issue.id}/")
        r = client_for(s6["wsadmin"]).patch(url, {"status": 1}, format="json")
        assert_required(r)
        assert r.json()["issue_id"] == str(issue.id)
        issue.refresh_from_db()
        assert issue.state_id == triage.id
        from plane.db.models import IntakeIssue

        assert IntakeIssue.objects.get(issue=issue).status == -2

    def test_app_accept_with_an_estimate_works(self, s6, intake_item):
        issue, _ = intake_item
        assert put_est(s6["wsadmin"], s6, issue, 60).status_code == 200
        url = ws_url(s6, f"projects/{s6['p'].id}/intake-issues/{issue.id}/")
        r = client_for(s6["wsadmin"]).patch(url, {"status": 1}, format="json")
        assert r.status_code == 200, r.content
        issue.refresh_from_db()
        assert issue.state_id == s6["states"]["In Progress"].id

    def test_app_accept_into_a_not_started_default_state_is_open(self, s6, intake_item):
        from plane.db.models import State

        issue, _ = intake_item
        State.objects.filter(project=s6["p"]).update(default=False)
        State.objects.filter(pk=s6["states"]["Todo"].id).update(default=True)
        url = ws_url(s6, f"projects/{s6['p'].id}/intake-issues/{issue.id}/")
        assert client_for(s6["wsadmin"]).patch(url, {"status": 1}, format="json").status_code == 200

    def test_app_accept_is_open_when_not_required(self, s6, intake_item):
        issue, _ = intake_item
        mode(s6["p"], "optional")
        url = ws_url(s6, f"projects/{s6['p'].id}/intake-issues/{issue.id}/")
        assert client_for(s6["wsadmin"]).patch(url, {"status": 1}, format="json").status_code == 200

    def test_v1_accept_into_a_started_default_state_needs_an_estimate(self, s6, intake_item):
        issue, triage = intake_item
        url = f"/api/v1/workspaces/{s6['slug']}/projects/{s6['p'].id}/intake-issues/{issue.id}/"
        c = api_key_client(s6["wsadmin"])
        assert_required(c.patch(url, {"status": 1}, format="json"))
        issue.refresh_from_db()
        assert issue.state_id == triage.id
        put_est(s6["wsadmin"], s6, issue, 60)
        assert c.patch(url, {"status": 1}, format="json").status_code == 200


class TestMoveIntoRequiredProject:
    @pytest.fixture
    def move_world(self, s6):
        support.join(s6["q"], s6["wsadmin"], 20)
        mode(s6["q"], "required")
        return s6

    def _move(self, s, issue, state_map=None):
        from plane.utils.work_item_move import move_work_item

        return move_work_item(Issue.objects.get(pk=issue.pk), s["q"], s["wsadmin"], state_map=state_map, notify=False)

    def test_started_item_without_an_estimate_cannot_enter_a_required_project(self, move_world):
        from rest_framework.exceptions import APIException

        s = move_world
        # The source flag (P's grandfathering) does not travel: the item is refused.
        assert s["issue"].id in started_flags(s["p"])
        with pytest.raises(APIException) as exc:
            self._move(s, s["issue"])
        assert exc.value.detail["code"] == "estimate_required" and exc.value.detail["issue_id"] == str(s["issue"].id)
        assert Issue.objects.get(pk=s["issue"].pk).project_id == s["p"].id
        assert s["issue"].id in started_flags(s["p"])  # nothing was written

    def test_started_item_with_an_estimate_moves(self, move_world):
        s = move_world
        put_est(s["member"], s, s["issue"], 90)
        self._move(s, s["issue"])
        assert Issue.objects.get(pk=s["issue"].pk).project_id == s["q"].id

    def test_unstarted_item_moves_and_a_started_override_is_refused(self, move_world):
        from rest_framework.exceptions import APIException

        s = move_world
        with pytest.raises(APIException):
            self._move(s, s["todo"], state_map={str(s["states"]["Todo"].id): str(s["q_states"]["In Progress"].id)})
        self._move(s, s["todo"])
        moved = Issue.objects.get(pk=s["todo"].pk)
        assert moved.project_id == s["q"].id and moved.state.group == "unstarted"

    def test_cancelled_item_moves(self, move_world):
        s = move_world
        s["todo"].state = s["states"]["Cancelled"]
        s["todo"].save()
        self._move(s, s["todo"])
        assert Issue.objects.get(pk=s["todo"].pk).project_id == s["q"].id

    def test_move_to_a_project_that_does_not_require_estimates_is_open_and_drops_the_flag(self, move_world):
        s = move_world
        mode(s["q"], "optional")
        self._move(s, s["issue"])
        assert Issue.objects.get(pk=s["issue"].pk).project_id == s["q"].id
        assert s["issue"].id not in started_flags(s["p"]) and s["issue"].id not in started_flags(s["q"])

    def test_move_endpoint_returns_the_estimate_required_payload(self, move_world):
        s = move_world
        r = client_for(s["wsadmin"]).post(
            ws_url(s, f"projects/{s['p'].id}/work-items/{s['issue'].id}/move/"),
            {"target_project_id": str(s["q"].id)},
            format="json",
        )
        assert_required(r)
