# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Guards for inline media resolution in migrated descriptions/comments.

Regression: attachments referenced inside a Jira comment rendered as a dead
"[image: attachment]" placeholder — the resolver only matched image/* files, so
PDFs/videos/docs were unresolvable, and the placeholder carried no file name or
link even though the file had migrated into the attachments list.
"""

from plane.utils.jira_importer import (
    _attachment_link_html,
    _make_media_resolver,
    _ordered_media_attachments,
)


def _att(aid, name, mime):
    return {"id": aid, "filename": name, "mimeType": mime}


PDF = _att("801", "spec.pdf", "application/pdf")
VIDEO = _att("802", "repro.mp4", "video/mp4")
IMAGE = _att("803", "shot.png", "image/png")


class _Asset:
    def __init__(self, url):
        self.asset_url = url


def _media(alt=None):
    attrs = {"id": "m", "type": "file"}
    if alt:
        attrs["alt"] = alt
    return {"type": "media", "attrs": attrs}


def test_ordered_media_includes_non_image_types():
    ordered = _ordered_media_attachments("", [PDF, VIDEO, IMAGE])
    assert [a["filename"] for a in ordered] == ["spec.pdf", "repro.mp4", "shot.png"]


def test_rendered_html_drives_document_order():
    rendered = '<img src="https://x/rest/api/3/attachment/content/803">'
    ordered = _ordered_media_attachments(rendered, [PDF, VIDEO, IMAGE])
    # the rendered-referenced attachment comes first, remainder keep list order
    assert ordered[0]["filename"] == "shot.png"
    assert {a["filename"] for a in ordered} == {"spec.pdf", "repro.mp4", "shot.png"}


def test_attachment_link_uses_real_filename_and_url():
    html = _attachment_link_html(PDF, _Asset("/api/assets/v2/.../attachments/abc/"))
    assert 'href="/api/assets/v2/.../attachments/abc/"' in html
    assert ">spec.pdf<" in html


def test_attachment_link_without_asset_still_names_the_file():
    html = _attachment_link_html(VIDEO, None)
    assert html == "<p>[attachment: repro.mp4]</p>"
    assert "[image:" not in html


def test_non_image_resolves_to_named_link_not_placeholder():
    resolve = _make_media_resolver(
        [PDF], set(), lambda att: "SHOULD-NOT-UPLOAD", {"801": _Asset("/api/assets/pdf/")}
    )
    html = resolve(_media())
    assert ">spec.pdf<" in html and "<a href=" in html
    assert "image-component" not in html


def test_image_resolves_to_inline_image_component():
    resolve = _make_media_resolver([IMAGE], set(), lambda att: "ASSET-1", {})
    assert resolve(_media()) == '<image-component src="ASSET-1"></image-component>'


def test_image_falls_back_to_link_when_upload_fails():
    resolve = _make_media_resolver([IMAGE], set(), lambda att: None, {"803": _Asset("/api/assets/img/")})
    html = resolve(_media())
    assert ">shot.png<" in html and "<a href=" in html


def test_consumed_is_shared_so_one_file_is_never_used_twice():
    consumed = set()
    ordered = [PDF, VIDEO]
    desc = _make_media_resolver(ordered, consumed, lambda a: None, {})
    comment = _make_media_resolver(ordered, consumed, lambda a: None, {})
    first = desc(_media())
    second = comment(_media())  # a *different* resolver, same issue
    assert "spec.pdf" in first
    assert "repro.mp4" in second


def test_unresolvable_media_names_what_it_can_and_is_not_labelled_image():
    resolve = _make_media_resolver([], set(), lambda a: None, {})
    assert resolve(_media(alt="diagram.vsd")) == "<p>[attachment: diagram.vsd]</p>"
    assert resolve(_media()) == "<p>[attachment: attachment]</p>"
