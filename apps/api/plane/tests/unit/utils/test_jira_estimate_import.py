# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-121 — the Jira importers vs. the time-estimate gate.

* Jira's original estimate (seconds) is imported as the item's original estimate (minutes),
  whatever the project's estimate mode (REST ``timeoriginalestimate`` with a
  ``timetracking.originalEstimateSeconds`` fallback; CSV "Original Estimate" / "Original estimate").
* An imported item in a Started/Completed state with no usable estimate in a "required" project
  is grandfathered (``IssueEstimateGrandfather``), never refused; a not-started item is not.
* The importers write through the ORM, which the serializer-level gate never sees.
"""

import io

import pytest

from plane.db.models import (
    Issue,
    IssueEstimateGrandfather,
    IssueTimeEstimate,
    IssueTimeEstimateRevision,
    Project,
    State,
    Workspace,
)
from plane.utils.jira_csv_importer import parse_jira_csv, run_csv_import
from plane.utils.jira_importer import (
    ISSUE_FIELDS,
    original_estimate_seconds,
    run_import,
    sample_issues,
)
from plane.utils.time_tracking.estimate import check_can_log, seconds_to_minutes

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def workspace(db, create_user):
    return Workspace.objects.create(name="Jira Est WS", slug="jira-est-ws", owner=create_user)


def make_project(workspace, user, mode="required", tracking=True, identifier="JE"):
    project = Project.objects.create(
        name=f"Jira Est {identifier}",
        identifier=identifier,
        workspace=workspace,
        created_by=user,
        is_time_tracking_enabled=tracking,
        time_estimate_mode=mode,
    )
    State.objects.create(name="Todo", project=project, group="backlog", default=True)
    State.objects.create(name="In Progress", project=project, group="started")
    State.objects.create(name="Done", project=project, group="completed")
    return project


@pytest.fixture
def project(workspace, create_user):
    return make_project(workspace, create_user)


def jira_issue(key, status="In Progress", **fields):
    return {"key": key, "fields": {"summary": f"Issue {key}", "status": {"name": status}, **fields}}


def do_import(project, user, issues, **kw):
    return run_import(project=project, initiator=user, issues=issues, dry_run=False, **kw)


def issue_of(key):
    return Issue.objects.get(external_source="jira", external_id=key)


def estimate_of(key):
    return IssueTimeEstimate.objects.filter(issue=issue_of(key)).first()


def grandfathered(key):
    return IssueEstimateGrandfather.objects.filter(issue=issue_of(key)).exists()


class TestSecondsToMinutes:
    @pytest.mark.parametrize(
        "seconds, minutes",
        [
            (3600, 60),
            ("7200", 120),
            (7200.0, 120),
            (90, 2),  # half a minute rounds up
            (89, 1),
            (1, 1),  # a positive value never becomes 0
            (10**9, 60000),  # clamped to MAX_MINUTES
        ],
    )
    def test_converts(self, seconds, minutes):
        assert seconds_to_minutes(seconds) == minutes

    @pytest.mark.parametrize("seconds", [None, "", "  ", 0, -60, "abc", True, float("nan"), float("inf")])
    def test_no_usable_estimate(self, seconds):
        assert seconds_to_minutes(seconds) is None


class TestRestEstimate:
    def test_fields_requested_from_jira(self):
        assert "timeoriginalestimate" in ISSUE_FIELDS.split(",")
        assert "timetracking" in ISSUE_FIELDS.split(",")

    def test_original_estimate_imported_as_minutes(self, project, create_user):
        res = do_import(project, create_user, [jira_issue("JE-1", timeoriginalestimate=7200)])
        est = estimate_of("JE-1")
        assert (est.original_minutes, est.current_minutes) == (120, 120)
        assert est.original_by == create_user
        assert est.locked_at is not None  # already in a started state
        assert not grandfathered("JE-1")
        rev = IssueTimeEstimateRevision.objects.get(estimate=est)
        assert (rev.kind, rev.minutes, rev.set_by) == ("original_set", 120, create_user)
        assert (res["estimates"], res["grandfathered"]) == (1, 0)

    def test_estimate_on_a_not_started_item_stays_unlocked(self, project, create_user):
        do_import(project, create_user, [jira_issue("JE-1", status="Todo", timeoriginalestimate=3600)])
        est = estimate_of("JE-1")
        assert est.original_minutes == 60
        assert est.locked_at is None

    def test_timetracking_fallback(self, project, create_user):
        issue = jira_issue("JE-1", timetracking={"originalEstimateSeconds": 1800, "originalEstimate": "30m"})
        do_import(project, create_user, [issue])
        assert estimate_of("JE-1").original_minutes == 30

    def test_timeoriginalestimate_wins_over_timetracking(self):
        fields = {"timeoriginalestimate": 600, "timetracking": {"originalEstimateSeconds": 1800}}
        assert original_estimate_seconds(fields) == 600
        assert original_estimate_seconds({"timeoriginalestimate": None, "timetracking": {}}) is None
        assert original_estimate_seconds({}) is None

    def test_zero_estimate_is_no_estimate(self, project, create_user):
        res = do_import(project, create_user, [jira_issue("JE-1", timeoriginalestimate=0)])
        assert estimate_of("JE-1") is None
        assert grandfathered("JE-1")
        assert (res["estimates"], res["grandfathered"]) == (0, 1)

    def test_sample_fixture_carries_an_estimate(self, project, create_user):
        assert sample_issues()[0]["fields"]["timeoriginalestimate"] == 28800
        do_import(project, create_user, sample_issues())
        assert estimate_of("ENG-101").original_minutes == 480
        assert estimate_of("ENG-102") is None


class TestRequiredModeGrandfathering:
    @pytest.mark.parametrize("status", ["In Progress", "Done"])
    def test_started_or_completed_without_estimate_is_grandfathered(self, project, create_user, status):
        res = do_import(project, create_user, [jira_issue("JE-1", status=status)])
        assert estimate_of("JE-1") is None
        assert grandfathered("JE-1")
        assert res["created"] == 1  # nothing refused
        assert (res["estimates"], res["grandfathered"]) == (0, 1)
        row = IssueEstimateGrandfather.objects.get(issue=issue_of("JE-1"))
        assert (row.project_id, row.workspace_id) == (project.id, project.workspace_id)

    def test_grandfathered_item_is_never_blocked_afterwards(self, project, create_user):
        do_import(project, create_user, [jira_issue("JE-1")])
        check_can_log(issue_of("JE-1"))  # would raise estimate_required if it were not flagged

    def test_not_started_without_estimate_is_not_grandfathered(self, project, create_user):
        res = do_import(project, create_user, [jira_issue("JE-1", status="Todo")])
        assert estimate_of("JE-1") is None
        assert not grandfathered("JE-1")
        assert res["grandfathered"] == 0

    def test_unmapped_status_lands_in_default_state_and_is_not_grandfathered(self, project, create_user):
        do_import(project, create_user, [jira_issue("JE-1", status="Nonexistent")])
        assert not grandfathered("JE-1")

    def test_started_with_estimate_is_not_grandfathered(self, project, create_user):
        do_import(project, create_user, [jira_issue("JE-1", timeoriginalestimate=3600)])
        assert estimate_of("JE-1") is not None
        assert not grandfathered("JE-1")
        assert not estimate_of("JE-1").grandfathered

    def test_mixed_batch(self, project, create_user):
        issues = [
            jira_issue("JE-1", timeoriginalestimate=3600),
            jira_issue("JE-2"),
            jira_issue("JE-3", status="Todo"),
        ]
        res = do_import(project, create_user, issues)
        assert (res["created"], res["estimates"], res["grandfathered"]) == (3, 1, 1)
        assert IssueEstimateGrandfather.objects.count() == 1
        assert IssueTimeEstimate.objects.count() == 1

    def test_rerun_is_idempotent(self, project, create_user):
        issues = [jira_issue("JE-1"), jira_issue("JE-2", timeoriginalestimate=60)]
        do_import(project, create_user, issues)
        res = do_import(project, create_user, issues)
        assert res["skipped"] == 2
        assert IssueEstimateGrandfather.objects.count() == 1
        assert IssueTimeEstimate.objects.count() == 1


class TestOtherModes:
    @pytest.mark.parametrize("mode", ["off", "optional"])
    def test_estimate_stored_and_no_grandfather_row(self, workspace, create_user, mode):
        project = make_project(workspace, create_user, mode=mode, identifier="JO")
        issues = [jira_issue("JE-1", timeoriginalestimate=5400), jira_issue("JE-2")]
        res = do_import(project, create_user, issues)
        assert estimate_of("JE-1").original_minutes == 90
        assert IssueEstimateGrandfather.objects.count() == 0
        assert (res["estimates"], res["grandfathered"]) == (1, 0)

    def test_estimate_stored_when_time_tracking_is_off_and_nothing_is_grandfathered(self, workspace, create_user):
        project = make_project(workspace, create_user, mode="required", tracking=False, identifier="JT")
        do_import(project, create_user, [jira_issue("JE-1", timeoriginalestimate=600), jira_issue("JE-2")])
        assert estimate_of("JE-1").original_minutes == 10
        assert IssueEstimateGrandfather.objects.count() == 0


class TestDryRun:
    def test_counts_without_writing(self, project, create_user):
        issues = [
            jira_issue("JE-1", timeoriginalestimate=3600),
            jira_issue("JE-2"),
            jira_issue("JE-3", status="Todo"),
        ]
        res = run_import(project=project, initiator=create_user, issues=issues, dry_run=True)
        assert (res["created"], res["estimates"], res["grandfathered"]) == (3, 1, 1)
        by_key = {row["key"]: row for row in res["preview"]}
        assert by_key["JE-1"]["estimate_minutes"] == 60 and not by_key["JE-1"]["grandfathered"]
        assert by_key["JE-2"]["estimate_minutes"] is None and by_key["JE-2"]["grandfathered"]
        assert not by_key["JE-3"]["grandfathered"]
        assert Issue.objects.filter(external_source="jira").count() == 0
        assert IssueTimeEstimate.objects.count() == 0
        assert IssueEstimateGrandfather.objects.count() == 0


CSV_HEADER = ["Issue key", "Summary", "Status", "Priority", "Assignee", "Description"]


def csv_file(extra_header, rows):
    out = io.StringIO()
    import csv

    w = csv.writer(out)
    w.writerow(CSV_HEADER + extra_header)
    for row in rows:
        w.writerow(row)
    out.seek(0)
    return out


def csv_row(key, status, *extra):
    return [key, f"Issue {key}", status, "", "", "", *extra]


def do_csv(project, user, fp, **kw):
    issues, names = parse_jira_csv(fp)
    return issues, run_csv_import(project, user, issues, names, dry_run=False, **kw)


class TestCsvEstimate:
    @pytest.mark.parametrize("header", ["Original Estimate", "Original estimate"])
    def test_both_header_spellings(self, project, create_user, header):
        issues, res = do_csv(project, create_user, csv_file([header], [csv_row("JE-1", "In Progress", "7200")]))
        assert issues[0]["original_estimate_seconds"] == 7200
        assert estimate_of("JE-1").original_minutes == 120
        assert not grandfathered("JE-1")
        assert (res["estimates"], res["grandfathered"]) == (1, 0)

    @pytest.mark.parametrize("cell", ["", "   ", "0", "abc"])
    def test_blank_or_unusable_cell_is_no_estimate(self, project, create_user, cell):
        issues, res = do_csv(project, create_user, csv_file(["Original Estimate"], [csv_row("JE-1", "In Progress", cell)]))
        assert issues[0]["original_estimate_seconds"] is None
        assert estimate_of("JE-1") is None
        assert grandfathered("JE-1")  # required + started + no estimate
        assert (res["estimates"], res["grandfathered"]) == (0, 1)

    def test_no_estimate_column_at_all(self, project, create_user):
        _, res = do_csv(project, create_user, csv_file([], [csv_row("JE-1", "In Progress"), csv_row("JE-2", "Todo")]))
        assert grandfathered("JE-1")
        assert not grandfathered("JE-2")
        assert res["grandfathered"] == 1

    def test_estimate_stored_in_optional_mode_without_grandfather_row(self, workspace, create_user):
        project = make_project(workspace, create_user, mode="optional", identifier="JC")
        do_csv(
            project,
            create_user,
            csv_file(["Original Estimate"], [csv_row("JE-1", "In Progress", "600"), csv_row("JE-2", "In Progress", "")]),
        )
        assert estimate_of("JE-1").original_minutes == 10
        assert IssueEstimateGrandfather.objects.count() == 0

    def test_dry_run_counts_without_writing(self, project, create_user):
        issues, names = parse_jira_csv(
            csv_file(["Original Estimate"], [csv_row("JE-1", "In Progress", "3600"), csv_row("JE-2", "Done", "")])
        )
        res = run_csv_import(project, create_user, issues, names, dry_run=True)
        assert (res["estimates"], res["grandfathered"]) == (1, 1)
        assert IssueTimeEstimate.objects.count() == 0
        assert IssueEstimateGrandfather.objects.count() == 0
