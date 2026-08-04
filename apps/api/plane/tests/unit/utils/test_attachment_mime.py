# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: attachment file types (#24).

Guards that uploads are accepted for every file kind the product promises
(PDF/Office/text/data/code/video/archives) even when the browser sends an
empty or generic MIME type, and that genuinely unsupported types are rejected.
"""

import pytest

from plane.utils.attachment_mime import EXTENSION_MIME_OVERRIDES, resolve_attachment_type


@pytest.mark.parametrize(
    "name,declared",
    [
        ("spec.pdf", "application/pdf"),
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("legacy.doc", "application/msword"),
        ("notes.txt", "text/plain"),
        ("data.csv", "text/csv"),
        ("book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("shot.png", "image/png"),
        ("clip.mp4", "video/mp4"),
        ("config.json", "application/json"),
        ("archive.zip", "application/zip"),
    ],
)
def test_declared_types_pass_through(name, declared):
    assert resolve_attachment_type(name, declared) == declared


@pytest.mark.parametrize(
    "name,declared,expected",
    [
        # Browsers leave these blank or report octet-stream on some platforms.
        ("server.log", "", "text/plain"),
        ("readme.md", "", "text/markdown"),
        ("config.yaml", "", "text/yaml"),
        ("config.yml", "", "text/yaml"),
        ("query.sql", "", "application/x-sql"),
        ("video.mov", "", "video/quicktime"),
        ("movie.mkv", "", "video/x-matroska"),
        ("files.rar", "", "application/vnd.rar"),
        # Declared type wins when it is already allowed.
        ("weird.bin", "image/png", "image/png"),
    ],
)
def test_extension_fallback(name, declared, expected):
    assert resolve_attachment_type(name, declared) == expected


def test_extension_overrides_are_all_allowed():
    from django.conf import settings

    for ext, mime in EXTENSION_MIME_OVERRIDES.items():
        assert mime in settings.ATTACHMENT_MIME_TYPES, f"{ext} override {mime} not in ATTACHMENT_MIME_TYPES"


def test_unsupported_type_rejected():
    assert resolve_attachment_type("evil.exe", "") is None
    assert resolve_attachment_type("evil.exe", "application/x-msdownload") is None


def test_script_capable_extension_does_not_sneak_in_via_guess():
    # .html guesses to text/html, which is NOT in the attachment allowlist.
    assert resolve_attachment_type("page.html", "") is None
