# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Guards for status-based ticket selection during Jira -> Plane migration.

`build_jql` narrows the fetch to user-picked Jira statuses; `fetch_jira_statuses`
lists a project's workflow statuses for the picker UI (Cloud v3 with a v2
fallback for Server/DC).
"""

import pytest

from plane.utils import jira_importer
from plane.utils.jira_importer import JiraConfigError, build_jql, fetch_jira_statuses


@pytest.mark.unit
class TestBuildJql:
    def test_no_statuses_keeps_default_query(self):
        assert build_jql(jira_project="ENG") == "project = ENG ORDER BY created ASC"

    def test_empty_status_list_means_no_filter(self):
        assert build_jql(jira_project="ENG", statuses=[]) == "project = ENG ORDER BY created ASC"
        assert build_jql(jira_project="ENG", statuses=None) == "project = ENG ORDER BY created ASC"

    def test_blank_status_entries_are_dropped(self):
        assert build_jql(jira_project="ENG", statuses=["  ", ""]) == "project = ENG ORDER BY created ASC"

    def test_status_filter_inserts_before_order_by(self):
        assert build_jql(jira_project="ENG", statuses=["To Do", "In Progress"]) == (
            'project = ENG AND status IN ("To Do", "In Progress") ORDER BY created ASC'
        )

    def test_custom_jql_gets_parenthesised_and_clause(self):
        assert build_jql(jql='project = ENG AND assignee = "a@x.com"', statuses=["Done"]) == (
            '(project = ENG AND assignee = "a@x.com") AND status IN ("Done")'
        )

    def test_custom_jql_without_statuses_unchanged(self):
        assert build_jql(jql="project = ENG") == "project = ENG"

    def test_status_names_are_quoted_and_escaped(self):
        assert build_jql(jira_project="ENG", statuses=['Weird "Quoted" Name']) == (
            'project = ENG AND status IN ("Weird \\"Quoted\\" Name") ORDER BY created ASC'
        )

    def test_no_project_and_no_jql_returns_none(self):
        assert build_jql() is None
        assert build_jql(statuses=["To Do"]) is None


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.url = "https://acme.atlassian.net/rest/api/3/project/ENG/statuses"

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"{self.status_code}")


CREDS = {
    "jira_url": "https://acme.atlassian.net",
    "jira_email": "you@acme.com",
    "jira_token": "tok",
    "jira_project": "ENG",
}

# Jira returns one entry per issue type; most share the same workflow.
STATUSES_PAYLOAD = [
    {"name": "Bug", "statuses": [{"name": "To Do"}, {"name": "In Progress"}, {"name": "Done"}]},
    {"name": "Story", "statuses": [{"name": "To Do"}, {"name": "In Review"}, {"name": "Done"}]},
]


@pytest.mark.unit
class TestFetchJiraStatuses:
    def test_dedupes_names_across_issue_types(self, monkeypatch):
        monkeypatch.setattr(jira_importer.requests, "get", lambda *a, **k: _FakeResp(STATUSES_PAYLOAD))
        assert fetch_jira_statuses(**CREDS) == ["Done", "In Progress", "In Review", "To Do"]

    def test_legacy_fallback_on_404(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if "/rest/api/3/" in url:
                return _FakeResp({}, status_code=404)
            return _FakeResp(STATUSES_PAYLOAD)

        monkeypatch.setattr(jira_importer.requests, "get", fake_get)
        assert fetch_jira_statuses(**CREDS) == ["Done", "In Progress", "In Review", "To Do"]
        assert any("/rest/api/2/" in u for u in calls)

    def test_auth_failure_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(jira_importer.requests, "get", lambda *a, **k: _FakeResp({}, status_code=401))
        with pytest.raises(JiraConfigError) as exc:
            fetch_jira_statuses(**CREDS)
        assert "401" in str(exc.value)

    def test_non_json_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(
            jira_importer.requests, "get", lambda *a, **k: _FakeResp(ValueError("Expecting value"))
        )
        with pytest.raises(JiraConfigError) as exc:
            fetch_jira_statuses(**CREDS)
        assert "non-JSON" in str(exc.value)

    @pytest.mark.parametrize("missing", ["jira_url", "jira_email", "jira_token", "jira_project"])
    def test_missing_settings_raise(self, missing):
        with pytest.raises(JiraConfigError) as exc:
            fetch_jira_statuses(**{**CREDS, missing: None})
        assert missing in str(exc.value)
