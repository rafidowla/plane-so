# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Attachment upload type resolution (FORK: attachment file types, #24).

Browsers frequently report an empty or generic ``file.type`` for extensions
like .log, .sql, .yaml or .mov, which made those uploads fail validation even
though the file kind is supported. `resolve_attachment_type` falls back to the
file name's extension (and finally to `mimetypes`) before giving up.
"""

import mimetypes

from django.conf import settings

# Extensions that browsers or `mimetypes` commonly leave blank or map
# inconsistently across platforms. Values must be in ATTACHMENT_MIME_TYPES.
EXTENSION_MIME_OVERRIDES = {
    "log": "text/plain",
    "md": "text/markdown",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "sql": "application/x-sql",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "wmv": "video/x-ms-wmv",
    "rar": "application/vnd.rar",
}

# Human-readable list for validation error messages (FORK #24).
SUPPORTED_ATTACHMENT_EXTENSIONS = (
    ".pdf, .doc, .docx, .txt, .rtf, .csv, .xls, .xlsx, .ppt, .pptx, "
    ".png, .jpg, .jpeg, .gif, .svg, .webp, "
    ".mp4, .mov, .mkv, .webm, .wmv, "
    ".json, .xml, .yaml, .yml, .sql, .log, .md, "
    ".zip, .rar, .7z, .tar, .gz"
)


def resolve_attachment_type(name, declared_type):
    """Return an allowed MIME type for an upload, or None when unsupported.

    Checks, in order: the browser-declared type, a per-extension override, and
    a `mimetypes` guess from the file name. The first candidate present in
    settings.ATTACHMENT_MIME_TYPES wins.

    A *guessed* application/octet-stream is never accepted — `mimetypes`
    returns it for arbitrary binary extensions (.exe, .dll), which would
    otherwise slip past the allowlist. An explicitly declared octet-stream
    keeps the historical behavior.
    """
    candidates = []
    if declared_type:
        candidates.append(declared_type)
    ext = name.rsplit(".", 1)[-1].lower() if name and "." in name else ""
    if ext in EXTENSION_MIME_OVERRIDES:
        candidates.append(EXTENSION_MIME_OVERRIDES[ext])
    guessed = mimetypes.guess_type(name)[0] if name else None
    if guessed and guessed != "application/octet-stream":
        candidates.append(guessed)
    for candidate in candidates:
        if candidate in settings.ATTACHMENT_MIME_TYPES:
            return candidate
    return None
