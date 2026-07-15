# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Convert Jira's Atlassian Document Format (ADF) to Plane editor HTML.

The Plane rich-text editor (TipTap/ProseMirror) round-trips through HTML: a
work item's ``description_html`` / a comment's ``comment_html`` is parsed back
into the editor document on load. So preserving Jira formatting is a matter of
mapping each ADF node to the tag the editor understands
(``h1``-``h6``/``p``/``ul``/``ol``/``li``/``table``/``strong``/``em``/``a``/…),
rather than flattening to plain text.

Images are emitted as ``<image-component src="<asset-id>">`` — the editor's
custom image node (parseHTML tag ``image-component``) whose ``src`` is a Plane
FileAsset id. Because uploading an image needs a saved parent (issue/comment)
and network I/O, resolution is injected via ``media_resolver`` so this module
stays pure and unit-testable; callers pass a resolver that downloads the Jira
media and returns the ``<image-component>`` markup (or a placeholder).

Reference: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
"""

import html as _html

# Inline text marks -> (open, close). Order-independent; applied outermost-last.
_SIMPLE_MARKS = {
    "strong": ("<strong>", "</strong>"),
    "em": ("<em>", "</em>"),
    "code": ("<code>", "</code>"),
    "strike": ("<s>", "</s>"),
    "underline": ("<u>", "</u>"),
}


def _esc(text):
    return _html.escape(text or "", quote=False)


def _esc_attr(text):
    return _html.escape(text or "", quote=True)


def _apply_marks(text, marks):
    """Wrap already-escaped text in its ADF inline marks."""
    for mark in marks or []:
        mtype = mark.get("type")
        if mtype in _SIMPLE_MARKS:
            open_t, close_t = _SIMPLE_MARKS[mtype]
            text = f"{open_t}{text}{close_t}"
        elif mtype == "link":
            href = _esc_attr((mark.get("attrs") or {}).get("href", ""))
            text = f'<a href="{href}" target="_blank" rel="noopener noreferrer">{text}</a>'
        elif mtype == "subsup":
            tag = "sub" if (mark.get("attrs") or {}).get("type") == "sub" else "sup"
            text = f"<{tag}>{text}</{tag}>"
        # textColor / underline-variants / unknown marks: leave the text unwrapped.
    return text


def _children(node, media_resolver):
    return "".join(adf_to_html(c, media_resolver) for c in (node.get("content") or []) if c is not None)


def _raw_text(node):
    """Concatenate descendant text nodes verbatim (for code blocks)."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(_raw_text(c) for c in (node.get("content") or []))
    if isinstance(node, list):
        return "".join(_raw_text(c) for c in node)
    return ""


def adf_to_html(node, media_resolver=None):
    """Convert an ADF node (dict), a list of nodes, or a plain string to HTML.

    ``media_resolver`` (optional): ``(media_node) -> html`` for ADF ``media``
    nodes. Defaults to a visible ``[image: …]`` placeholder so images are never
    silently dropped.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        # Some (older) Jira issues store description as a plain wiki/markup string.
        return "".join(f"<p>{_esc(line)}</p>" for line in node.split("\n")) if node.strip() else ""
    if isinstance(node, list):
        return "".join(adf_to_html(c, media_resolver) for c in node if c is not None)
    if not isinstance(node, dict):
        return ""

    t = node.get("type")

    if t == "text":
        return _apply_marks(_esc(node.get("text", "")), node.get("marks"))
    if t in ("doc", "bodiedExtension"):
        return _children(node, media_resolver)
    if t == "paragraph":
        return f"<p>{_children(node, media_resolver)}</p>"
    if t == "heading":
        level = (node.get("attrs") or {}).get("level", 1)
        try:
            level = min(max(int(level), 1), 6)
        except (TypeError, ValueError):
            level = 1
        return f"<h{level}>{_children(node, media_resolver)}</h{level}>"
    if t == "hardBreak":
        return "<br/>"
    if t == "bulletList":
        return f"<ul>{_children(node, media_resolver)}</ul>"
    if t == "orderedList":
        return f"<ol>{_children(node, media_resolver)}</ol>"
    if t == "listItem":
        return f"<li>{_children(node, media_resolver)}</li>"
    if t in ("blockquote", "panel", "expand", "nestedExpand"):
        return f"<blockquote>{_children(node, media_resolver)}</blockquote>"
    if t == "codeBlock":
        return f"<pre><code>{_esc(_raw_text(node))}</code></pre>"
    if t == "rule":
        return "<hr/>"
    if t == "table":
        return f"<table><tbody>{_children(node, media_resolver)}</tbody></table>"
    if t == "tableRow":
        return f"<tr>{_children(node, media_resolver)}</tr>"
    if t == "tableCell":
        return f"<td>{_children(node, media_resolver)}</td>"
    if t == "tableHeader":
        return f"<th>{_children(node, media_resolver)}</th>"
    if t in ("mediaSingle", "mediaGroup", "mediaInline"):
        return _children(node, media_resolver)
    if t == "media":
        if media_resolver is not None:
            return media_resolver(node) or ""
        attrs = node.get("attrs") or {}
        return f"<p>[image: {_esc(attrs.get('alt') or attrs.get('id') or 'attachment')}]</p>"
    if t == "mention":
        return _esc((node.get("attrs") or {}).get("text") or "")
    if t == "emoji":
        attrs = node.get("attrs") or {}
        return _esc(attrs.get("text") or attrs.get("shortName") or "")
    if t == "status":
        return _esc((node.get("attrs") or {}).get("text") or "")
    if t == "date":
        return _esc(str((node.get("attrs") or {}).get("timestamp") or ""))
    if t in ("inlineCard", "blockCard", "embedCard"):
        url = (node.get("attrs") or {}).get("url") or ""
        if not url:
            return _children(node, media_resolver)
        return f'<a href="{_esc_attr(url)}" target="_blank" rel="noopener noreferrer">{_esc(url)}</a>'

    # Unknown/unsupported node: recurse so no text is lost.
    return _children(node, media_resolver)


def adf_document_to_html(node, media_resolver=None):
    """Top-level convenience wrapper: always returns editor-safe HTML.

    Guarantees a non-empty body (``<p></p>`` when the source is empty) and wraps
    bare inline output in a paragraph so the editor gets a valid block document.
    """
    html = adf_to_html(node, media_resolver).strip()
    if not html:
        return "<p></p>"
    # If the top level produced only inline content (e.g. a plain string that
    # somehow bypassed wrapping), wrap it so the editor has a block node.
    if not html.startswith(("<p", "<h", "<ul", "<ol", "<table", "<pre", "<blockquote", "<hr", "<image-component")):
        return f"<p>{html}</p>"
    return html
