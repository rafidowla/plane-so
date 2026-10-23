# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
#
# FORK: clean-room AGPL replacement for the previous proprietary email helper.
# Provides the same `generate_plain_text_from_html(html_content) -> str`
# contract used by the email background tasks.

# Python imports
import html as _html
import re

# Django imports
from django.utils.html import strip_tags

_STYLE_OPEN_RE = re.compile(r"<style", re.IGNORECASE)
_STYLE_CLOSE_RE = re.compile(r"</style>", re.IGNORECASE)
# FORK: the fork's plain-text helper also drops <script> blocks; same linear scan as upstream's style removal.
# The open pattern requires a tag-name boundary so ``<scripts>`` is not treated as ``<script>``.
_SCRIPT_OPEN_RE = re.compile(r"<script\b", re.IGNORECASE)
_SCRIPT_CLOSE_RE = re.compile(r"</script>", re.IGNORECASE)


def _remove_blocks(html_content, open_re, close_re):
    """Remove ``open_re ...> ... close_re`` blocks in a single forward scan (linear time)."""
    parts = []
    pos = 0
    while start := open_re.search(html_content, pos):
        tag_end = html_content.find(">", start.end())
        if tag_end == -1:
            break
        close = close_re.search(html_content, tag_end + 1)
        if close is None:
            break
        parts.append(html_content[pos : start.start()])
        pos = close.end()
    parts.append(html_content[pos:])
    return "".join(parts)


def _remove_style_blocks(html_content):
    """
    Remove `<style ...>...</style>` blocks (case-insensitive) in a single
    forward scan, leaving the surrounding content unchanged.
    """
    return _remove_blocks(html_content, _STYLE_OPEN_RE, _STYLE_CLOSE_RE)


def generate_plain_text_from_html(html_content):
    """Convert an HTML email body into clean plain text.

    Drops <style>/<script> blocks and their contents, removes the remaining
    HTML tags, decodes HTML entities, normalises whitespace, and pads the
    result with a leading and trailing blank line.

    Args:
        html_content (str): the HTML email body.

    Returns:
        str: the plain-text rendering of the email body.
    """
    if not html_content:
        return "\n\n\n\n"

    # Drop <style> and <script> blocks together with their contents (linear-time scans; a backtracking
    # ``<(style|script)...>.*?</\1>`` regex was quadratic on many unclosed opening tags).
    without_blocks = _remove_blocks(_remove_style_blocks(html_content), _SCRIPT_OPEN_RE, _SCRIPT_CLOSE_RE)

    # Remove the remaining tags and decode entities (e.g. &amp; -> &).
    text = _html.unescape(strip_tags(without_blocks))

    # Trim trailing spaces per line, then collapse runs of blank lines.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)

    # Pad with a leading and trailing blank line.
    return "\n\n" + text.strip() + "\n\n"
