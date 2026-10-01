# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Change a page's body/title through the live (collaboration) server (FORK: PSR-86).

A page that has been opened in the editor has a Yjs document (``description_binary``) whose history the
browsers keep. Its content can only be changed safely as a transaction on that document, which is the
live server's job: ``POST <LIVE_URL>/page-content/replace/`` applies the change (on the document it has
open, if any, so connected editors see it) and returns the document in every stored format. The live
server writes nothing; the caller stores what comes back.
"""

import base64
import binascii
import logging

import requests
from django.conf import settings

from plane.utils.exception_logger import log_exception
from plane.utils.url import normalize_url_path

logger = logging.getLogger("plane.api")

LIVE_SECRET_HEADER = "live-server-secret-key"
LIVE_TIMEOUT_SECONDS = 10


class PageLiveSyncUnavailable(Exception):
    """The live server could not apply the change; nothing was changed."""


def replace_page_content(page_id, description_binary, description_html=None, name=None):
    """Apply a new body and/or title to a page's existing editor document.

    ``description_binary`` is the page's stored Yjs document (non-empty). Pass ``description_html``
    and/or ``name`` -- whichever is changing.

    Returns ``{"description_binary": bytes, "description_html": str, "description_json": dict,
    "loaded": bool}``: the resulting document, with html/json as the editor normalises them. Raises
    ``PageLiveSyncUnavailable`` when the live server is not configured, unreachable, slow, or answers
    with anything but a usable 200.
    """
    live_url = settings.LIVE_URL
    secret_key = settings.LIVE_SERVER_SECRET_KEY
    if not live_url or not secret_key:
        raise PageLiveSyncUnavailable("LIVE_BASE_URL or LIVE_SERVER_SECRET_KEY is not configured")

    payload = {
        "page_id": str(page_id),
        "description_binary": base64.b64encode(bytes(description_binary)).decode(),
    }
    if description_html is not None:
        payload["description_html"] = description_html
    if name is not None:
        payload["name"] = name

    url = normalize_url_path(f"{live_url}/page-content/replace/")
    try:
        response = requests.post(
            url,
            json=payload,
            headers={LIVE_SECRET_HEADER: secret_key},
            timeout=LIVE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        log_exception(e)
        raise PageLiveSyncUnavailable("The live server could not be reached") from e

    if response.status_code != 200:
        logger.warning("Live server answered %s for page-content/replace (page %s)", response.status_code, page_id)
        raise PageLiveSyncUnavailable(f"The live server answered {response.status_code}")

    try:
        data = response.json()
        binary = base64.b64decode(data["description_binary"], validate=True)
        html = data["description_html"]
        json_content = data["description_json"]
    except (ValueError, KeyError, TypeError, binascii.Error) as e:
        log_exception(e)
        raise PageLiveSyncUnavailable("The live server sent an unusable answer") from e
    if not binary or not isinstance(html, str) or not isinstance(json_content, dict):
        raise PageLiveSyncUnavailable("The live server sent an unusable answer")

    return {
        "description_binary": binary,
        "description_html": html,
        "description_json": json_content,
        "loaded": bool(data.get("loaded")),
    }
