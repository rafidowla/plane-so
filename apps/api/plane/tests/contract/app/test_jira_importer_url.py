# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Guards for Jira base-URL normalization + non-JSON error handling.

Regression: a Jira URL with a path (e.g. ``https://site.atlassian.net/jira/``)
produced ``.../jira/rest/api/3/...``, an HTML page, and a cryptic
``Expecting value: line 1 column 1 (char 0)`` import failure.
"""

import pytest

from plane.utils.jira_importer import JiraConfigError, _jira_json, normalize_jira_base


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://acme.atlassian.net/jira/", "https://acme.atlassian.net"),
        ("https://acme.atlassian.net/jira", "https://acme.atlassian.net"),
        ("https://acme.atlassian.net/", "https://acme.atlassian.net"),
        ("https://acme.atlassian.net", "https://acme.atlassian.net"),
        ("acme.atlassian.net/jira", "https://acme.atlassian.net"),
        ("https://acme.atlassian.net/jira/software/projects/UG/boards/9", "https://acme.atlassian.net"),
        ("http://acme.atlassian.net/wiki", "http://acme.atlassian.net"),
        # Server / Data Center context paths are preserved (only trailing slash trimmed).
        ("https://jira.mycompany.com/jira", "https://jira.mycompany.com/jira"),
        ("https://jira.mycompany.com/", "https://jira.mycompany.com"),
    ],
)
def test_normalize_jira_base(raw, expected):
    assert normalize_jira_base(raw) == expected


class _FakeResp:
    def __init__(self, ok):
        self.ok = ok
        self.status_code = 200
        self.url = "https://acme.atlassian.net/jira/rest/api/3/search/jql?jql=x"

    def json(self):
        if self.ok:
            return {"issues": []}
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_jira_json_ok():
    assert _jira_json(_FakeResp(ok=True)) == {"issues": []}


def test_jira_json_non_json_raises_clear_error():
    with pytest.raises(JiraConfigError) as exc:
        _jira_json(_FakeResp(ok=False))
    msg = str(exc.value)
    # actionable, not the cryptic JSON decode error
    assert "non-JSON" in msg
    assert "site root" in msg
    assert "Expecting value" not in msg
