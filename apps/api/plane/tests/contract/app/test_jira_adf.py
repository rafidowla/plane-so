# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the Jira ADF -> Plane editor HTML converter.

These guard the fix for "formatting lost during Jira -> Plane migration": ADF
rich content must map to editor HTML (headings/bold/lists/tables/links/images),
not be flattened to a single plain-text paragraph.
"""

from plane.utils.jira_adf import adf_document_to_html


def _doc(*content):
    return {"type": "doc", "content": list(content)}


def _p(*content):
    return {"type": "paragraph", "content": list(content)}


def _t(text, marks=None):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def test_heading_and_paragraph_preserved():
    html = adf_document_to_html(_doc({"type": "heading", "attrs": {"level": 2}, "content": [_t("Title")]}, _p(_t("Body"))))
    assert html == "<h2>Title</h2><p>Body</p>"


def test_inline_marks():
    html = adf_document_to_html(_doc(_p(_t("a "), _t("b", [{"type": "strong"}]), _t(" "), _t("c", [{"type": "em"}]))))
    assert html == "<p>a <strong>b</strong> <em>c</em></p>"


def test_link_mark():
    html = adf_document_to_html(_doc(_p(_t("x", [{"type": "link", "attrs": {"href": "https://e.com"}}]))))
    assert '<a href="https://e.com" target="_blank" rel="noopener noreferrer">x</a>' in html


def test_bullet_and_ordered_lists():
    li = lambda s: {"type": "listItem", "content": [_p(_t(s))]}
    html = adf_document_to_html(_doc({"type": "bulletList", "content": [li("one"), li("two")]}))
    assert html == "<ul><li><p>one</p></li><li><p>two</p></li></ul>"


def test_table_with_header_and_cells():
    def cell(kind, s):
        return {"type": kind, "content": [_p(_t(s))]}

    table = {
        "type": "table",
        "content": [
            {"type": "tableRow", "content": [cell("tableHeader", "Field"), cell("tableHeader", "Value")]},
            {"type": "tableRow", "content": [cell("tableCell", "Ratio"), cell("tableCell", "10")]},
        ],
    }
    html = adf_document_to_html(_doc(table))
    assert html == (
        "<table><tbody>"
        "<tr><th><p>Field</p></th><th><p>Value</p></th></tr>"
        "<tr><td><p>Ratio</p></td><td><p>10</p></td></tr>"
        "</tbody></table>"
    )


def test_code_block_is_not_mark_processed_and_is_escaped():
    cb = {"type": "codeBlock", "content": [_t("if a < b & c: pass")]}
    html = adf_document_to_html(_doc(cb))
    assert html == "<pre><code>if a &lt; b &amp; c: pass</code></pre>"


def test_html_is_escaped_no_injection():
    html = adf_document_to_html(_doc(_p(_t("<script>alert(1)</script> & <b>x</b>"))))
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;x&lt;/b&gt;" in html


def test_hard_break_and_rule():
    html = adf_document_to_html(_doc(_p(_t("a"), {"type": "hardBreak"}, _t("b")), {"type": "rule"}))
    assert html == "<p>a<br/>b</p><hr/>"


def test_media_uses_resolver_when_provided():
    doc = _doc({"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "m1", "alt": "shot.png"}}]})
    html = adf_document_to_html(doc, lambda node: '<image-component src="ASSET1"></image-component>')
    assert html == '<image-component src="ASSET1"></image-component>'


def test_media_falls_back_to_visible_placeholder():
    doc = _doc({"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "m1", "alt": "shot.png"}}]})
    html = adf_document_to_html(doc)
    assert html == "<p>[image: shot.png]</p>"


def test_plain_string_description():
    assert adf_document_to_html("line one\nline two") == "<p>line one</p><p>line two</p>"


def test_empty_yields_editor_safe_default():
    assert adf_document_to_html(None) == "<p></p>"
    assert adf_document_to_html({"type": "doc", "content": []}) == "<p></p>"


def test_unknown_node_recurses_so_text_is_not_lost():
    doc = _doc({"type": "someFutureNode", "content": [_p(_t("kept"))]})
    assert adf_document_to_html(doc) == "<p>kept</p>"
