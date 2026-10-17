# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-121 — the "required" estimate gate on the remaining ways into Started/Completed.

* draft -> work item (``POST /draft-to-issue/<id>/``) goes through ``IssueCreateSerializer``,
  so a draft posted straight into a Started/Completed state is refused like any other create
  (same project and a draft re-targeted into a required project);
* public API create with ``external_id``/``external_source`` (the importer-facing create) is
  gated like a plain create; the external-id upsert (``IssueDetailAPIEndpoint.put``) is not
  routed (``http_method_names`` has no ``put``), so it is not a reachable bypass;
* there is no bulk state endpoint in this fork (``bulk-operation-issues`` is a paid-plan
  upsell in the web app); the test pins that, so adding one later fails here and forces the
  gate onto it;
* the grandfather write fires on every path that turns the gate on — including the public API
  project PATCH (tracking switched on while the mode is already ``required``).
"""

from unittest import mock

import pytest

from plane.db.models import DraftIssue, Issue, IssueEstimateGrandfather

from .test_time_tracking_s6_estimates import (  # noqa: F401 (fixtures)
    _load_urlconf,
    _no_side_effects,
    api_key_client,
    assert_required,
    mode,
    put_est,
    s3,
    s6,
)
from .time_tracking_s3_support import client_for, make_issue, ws_url

pytestmark = [pytest.mark.contract, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def _draft_side_effects():
    with (
        mock.patch("plane.app.views.workspace.draft.issue_activity"),
        mock.patch("plane.app.views.workspace.draft.base_host", return_value="http://testserver"),
        mock.patch("plane.db.mixins.soft_delete_related_objects"),
    ):
        yield


def make_draft(s, state, project=None, owner=None):
    owner = owner or s["member"]
    draft = DraftIssue(name="a draft", workspace=s["p"].workspace, project=project or s["p"], state=state)
    draft.save(created_by_id=owner.id)
    return draft


def post_draft(s, draft, data, user=None):
    return client_for(user or s["member"]).post(ws_url(s, f"draft-to-issue/{draft.id}/"), data, format="json")


class TestDraftToWorkItem:
    @pytest.mark.parametrize("target", ["In Progress", "Done"])
    def test_draft_posted_into_a_started_state_is_refused(self, s6, target):
        state = s6["states"][target]
        draft = make_draft(s6, state)
        r = post_draft(s6, draft, {"name": "from draft", "state_id": str(state.id)})
        assert_required(r)
        assert not Issue.objects.filter(name="from draft").exists()
        assert DraftIssue.objects.filter(pk=draft.pk).exists()  # nothing consumed

    def test_draft_posted_into_a_not_started_state_is_allowed(self, s6):
        state = s6["states"]["Todo"]
        draft = make_draft(s6, state)
        r = post_draft(s6, draft, {"name": "from draft", "state_id": str(state.id)})
        assert r.status_code == 201, r.content

    def test_draft_without_a_state_lands_in_the_default_state_and_is_gated_by_it(self, s6):
        from plane.db.models import State

        State.objects.filter(project=s6["p"]).update(default=False)
        State.objects.filter(pk=s6["states"]["In Progress"].id).update(default=True)
        draft = make_draft(s6, None)
        assert_required(post_draft(s6, draft, {"name": "from draft"}))

    def test_draft_retargeted_into_a_required_project_is_gated(self, s6):
        # Draft started in Q (estimates off) in its Started state, posted into P (required).
        draft = make_draft(s6, s6["q_states"]["In Progress"], project=s6["q"])
        r = post_draft(
            s6,
            draft,
            {"name": "from draft", "project_id": str(s6["p"].id), "state_id": str(s6["q_states"]["In Progress"].id)},
        )
        assert_required(r)
        assert not Issue.objects.filter(name="from draft").exists()

    def test_draft_retargeted_without_a_state_never_lands_started_unestimated(self, s6):
        from plane.utils.time_tracking.estimate import WORK_GROUPS

        draft = make_draft(s6, s6["q_states"]["In Progress"], project=s6["q"])
        r = post_draft(s6, draft, {"name": "from draft", "project_id": str(s6["p"].id)})
        if r.status_code == 400:
            assert_required(r)
        else:
            assert r.status_code == 201, r.content
            created = Issue.objects.select_related("state").get(pk=r.json()["id"])
            assert created.project_id == s6["p"].id and created.state.group not in WORK_GROUPS, created.state.name

    def test_draft_into_a_started_state_is_open_when_not_required(self, s6):
        mode(s6["p"], "optional")
        state = s6["states"]["In Progress"]
        draft = make_draft(s6, state)
        r = post_draft(s6, draft, {"name": "from draft", "state_id": str(state.id)})
        assert r.status_code == 201, r.content


class TestPublicApiImportPaths:
    def url(self, s):
        return f"/api/v1/workspaces/{s['slug']}/projects/{s['p'].id}/issues/"

    def test_create_with_external_id_into_a_started_state_is_gated(self, s6):
        c = api_key_client(s6["member"])
        body = {
            "name": "imported started",
            "state": str(s6["states"]["In Progress"].id),
            "external_id": "EXT-1",
            "external_source": "csv",
        }
        assert_required(c.post(self.url(s6), body, format="json"))
        assert not Issue.objects.filter(external_id="EXT-1").exists()

    def test_create_with_external_id_into_todo_works(self, s6):
        c = api_key_client(s6["member"])
        body = {"name": "imported todo", "state": str(s6["states"]["Todo"].id), "external_id": "EXT-2"}
        body["external_source"] = "csv"
        r = c.post(self.url(s6), body, format="json")
        assert r.status_code == 201, r.content

    def test_external_id_upsert_put_is_not_routed(self, s6):
        c = api_key_client(s6["member"])
        r = c.put(
            self.url(s6),
            {"name": "x", "state": str(s6["states"]["In Progress"].id), "external_id": "E", "external_source": "s"},
            format="json",
        )
        assert r.status_code == 405, r.content


class TestNoBulkStateEndpoint:
    def test_bulk_operation_issues_is_not_served(self, s6):
        # The web app's bulk-operations bar is an upsell here; the API has no bulk state path.
        # If one is added, it must run gate_state_change per item and refuse atomically.
        r = client_for(s6["wsadmin"]).post(
            ws_url(s6, f"projects/{s6['p'].id}/bulk-operation-issues/"),
            {"issue_ids": [str(s6["todo"].id)], "properties": {"state_id": str(s6["states"]["In Progress"].id)}},
            format="json",
        )
        assert r.status_code == 404


class TestGrandfatherOnEveryGateStart:
    def test_v1_project_patch_turning_tracking_on_grandfathers(self, s6):
        q = s6["q"]
        q.is_time_tracking_enabled = False
        q.save()
        mode(q, "required")  # dormant: tracking is off, so the gate is not in force yet
        started = make_issue(q, s6["q_states"]["In Progress"], "Q started while dormant", s6["wsadmin"])
        IssueEstimateGrandfather.objects.filter(project=q).delete()
        with mock.patch("plane.api.views.project.model_activity"):
            r = api_key_client(s6["pa_q"]).patch(
                f"/api/v1/workspaces/{s6['slug']}/projects/{q.id}/", {"is_time_tracking_enabled": True}, format="json"
            )
        assert r.status_code == 200, r.content
        flagged = set(IssueEstimateGrandfather.objects.filter(project=q).values_list("issue_id", flat=True))
        assert {started.id, s6["issue_q"].id} <= flagged
