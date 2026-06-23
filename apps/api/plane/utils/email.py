# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
#
# Clean-room AGPL replacement for the previous proprietary email helper.
# Provides the same `generate_plain_text_from_html(html_content) -> str`
# contract used by the email background tasks.

# Python imports
import html as _html
import re

# Django imports
from django.utils.html import strip_tags


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

    # Drop <style> and <script> blocks together with their contents.
    without_blocks = re.sub(
        r"<(style|script)\b[^>]*>.*?</\1>",
        "",
        html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove the remaining tags and decode entities (e.g. &amp; -> &).
    text = _html.unescape(strip_tags(without_blocks))

    # Trim trailing spaces per line, then collapse runs of blank lines.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)

    # Pad with a leading and trailing blank line.
    return "\n\n" + text.strip() + "\n\n"
