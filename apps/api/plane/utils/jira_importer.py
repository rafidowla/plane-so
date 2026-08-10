# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared Jira -> Plane import engine.

Used by the `import_jira` management command, the synchronous preview API,
and the background import task. Fetches Jira issues/comments/worklogs over
REST and writes them through the ORM (so worklogs land in the time-tracking
tables). Idempotent via Issue.external_id.
"""

import base64
import html as _html
import math
import os
import re
from tempfile import SpooledTemporaryFile
from urllib.parse import urlparse
from uuid import uuid4

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plane.utils.jira_adf import adf_document_to_html

from plane.db.models import (
    Description,
    FileAsset,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueWorklog,
    Label,
    Project,
    ProjectMember,
    ResourceCapacity,
    State,
    User,
    WorkspaceMember,
)
from plane.settings.storage import S3Storage
from plane.utils.path_validator import sanitize_filename
from plane.utils.url_security import pinned_fetch_following_redirects

PRIORITY_MAP = {
    "highest": "urgent",
    "urgent": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "lowest": "low",
}

ISSUE_FIELDS = "summary,description,status,assignee,reporter,priority,labels,created,duedate,comment,worklog,attachment"


def norm(name):
    """Normalise a status name for fuzzy matching (lowercase, no spaces/hyphens)."""
    return "".join((name or "").lower().split()).replace("-", "").replace("_", "")


def adf_to_text(node):
    """Best-effort plain-text extraction from a Jira ADF document (or string)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [adf_to_text(c) for c in node.get("content", [])]
        sep = "\n" if node.get("type") in ("paragraph", "heading", "listItem") else ""
        return sep.join(p for p in parts if p)
    if isinstance(node, list):
        return "\n".join(adf_to_text(c) for c in node if c)
    return ""


class JiraConfigError(Exception):
    """Raised for missing/invalid Jira connection settings."""


def normalize_jira_base(jira_url):
    """Return the base the REST API lives at.

    Atlassian Cloud's API is always at the site origin, so a pasted path such as
    ``/jira`` or ``/wiki`` (or a full board URL) would otherwise produce
    ``.../jira/rest/api/3/...`` and a non-JSON page. For Cloud we drop the path;
    for Server/Data Center we keep any context path and only trim trailing slashes.
    """
    raw = (jira_url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if (parsed.netloc or "").lower().endswith(".atlassian.net"):
        return f"{parsed.scheme or 'https'}://{parsed.netloc}"
    return raw.rstrip("/")


def _jira_json(resp):
    """resp.json() with a clear, actionable error when Jira returns non-JSON
    (usually a wrong base URL serving an HTML page instead of the REST API)."""
    try:
        return resp.json()
    except ValueError:
        url = (resp.url or "").split("?")[0]
        raise JiraConfigError(
            f"Jira returned a non-JSON response (HTTP {resp.status_code}) from {url}. "
            "Check the Jira URL — it should be your site root, e.g. "
            "https://yourcompany.atlassian.net (no /jira or /wiki path)."
        )


# FORK: jira-comment-structure (#21) — Jira's threaded comments surface the
# reply's parent in different shapes depending on API version/deployment, so
# every known variant is probed. Returns the parent's Jira comment id or None.
def _jira_comment_parent_id(jc):
    parent_id = jc.get("parentCommentId") or jc.get("parentId") or (jc.get("parent") or {}).get("id")
    return str(parent_id) if parent_id else None


def _flatten_jira_comments(jira_comments):
    """Flatten Jira threaded comments into import order.

    Returns a list of (comment, parent_jira_id, rendered_index) tuples.
    Top-level comments keep their index into renderedFields.comment.comments
    (used for inline-image placement); nested replies have no rendered entry.
    Reply nesting deeper than one level is flattened onto the top-level parent,
    matching Jira's single-level reply model.
    """
    flat = []
    for ci, jc in enumerate(jira_comments):
        jc_id = str(jc.get("id"))
        flat.append((jc, _jira_comment_parent_id(jc), ci))
        for reply in jc.get("replies") or jc.get("children") or []:
            flat.append((reply, jc_id, None))
    return flat


def _jql_quote(value):
    """Quote a JQL string literal, escaping backslashes and double quotes."""
    return '"{}"'.format(str(value).replace("\\", "\\\\").replace('"', '\\"'))


def build_jql(jira_project=None, jql=None, statuses=None):
    """Build the JQL query for an import.

    `statuses` (list of Jira status names) narrows the fetch to tickets in
    those statuses only. An empty/None list means no filtering — the import
    behaves exactly as before. With a custom `jql`, the status clause is
    AND-ed onto it (parenthesised so an ORDER BY in the custom query isn't
    trapped inside the AND).
    """
    base = jql or (f"project = {jira_project} ORDER BY created ASC" if jira_project else None)
    if not base:
        return None
    statuses = [s.strip() for s in (statuses or []) if s and s.strip()]
    if not statuses:
        return base
    status_clause = "status IN ({})".format(", ".join(_jql_quote(s) for s in statuses))
    if jql:
        return f"({base}) AND {status_clause}"
    # Insert before ORDER BY so the default query stays valid.
    order_by = " ORDER BY created ASC"
    if base.endswith(order_by):
        return f"{base[: -len(order_by)]} AND {status_clause}{order_by}"
    return f"{base} AND {status_clause}"


def fetch_jira_statuses(jira_url, jira_email, jira_token, jira_project):
    """List the workflow statuses used by a Jira project (for the picker UI).

    Uses the per-project statuses endpoint (Cloud v3, falling back to v2 for
    Server/DC) and dedupes names across issue types — Jira returns one entry
    per issue type, and most share the same workflow.
    """
    for key, val in (
        ("jira_url", jira_url),
        ("jira_email", jira_email),
        ("jira_token", jira_token),
        ("jira_project", jira_project),
    ):
        if not val:
            raise JiraConfigError(f"Missing Jira setting: {key}")

    base = normalize_jira_base(jira_url)
    auth = (jira_email, jira_token)
    headers = {"Accept": "application/json"}

    resp = requests.get(
        f"{base}/rest/api/3/project/{jira_project}/statuses", auth=auth, headers=headers, timeout=60
    )
    if resp.status_code in (404, 410):
        resp = requests.get(
            f"{base}/rest/api/2/project/{jira_project}/statuses", auth=auth, headers=headers, timeout=60
        )
    if resp.status_code in (401, 403):
        raise JiraConfigError(
            f"Jira rejected the credentials ({resp.status_code}). Check the email + API token "
            f"and that the token has permission to read this project."
        )
    resp.raise_for_status()
    data = _jira_json(resp)

    names = set()
    for issue_type in data if isinstance(data, list) else []:
        for st in issue_type.get("statuses") or []:
            name = (st.get("name") or "").strip()
            if name:
                names.add(name)
    return sorted(names, key=str.lower)


def fetch_jira_issues(jira_url, jira_email, jira_token, jira_project=None, jql=None, limit=0, statuses=None):
    """Fetch issues (with comments + worklogs) from Jira Cloud REST API v3."""
    for key, val in (("jira_url", jira_url), ("jira_email", jira_email), ("jira_token", jira_token)):
        if not val:
            raise JiraConfigError(f"Missing Jira setting: {key}")
    query = build_jql(jira_project=jira_project, jql=jql, statuses=statuses)
    if not query:
        raise JiraConfigError("Provide a Jira project key or a JQL query")

    base = normalize_jira_base(jira_url)
    auth = (jira_email, jira_token)
    headers = {"Accept": "application/json"}

    def _raise_for_response(resp):
        # Surface auth/permission errors clearly instead of a generic failure.
        if resp.status_code in (401, 403):
            raise JiraConfigError(
                f"Jira rejected the credentials ({resp.status_code}). Check the email + API token "
                f"and that the token has permission to read this project."
            )

    # Jira Cloud (current): enhanced search with token-based pagination. The legacy
    # GET /rest/api/3/search was removed in 2025, so we use /search/jql and fall
    # back to the legacy endpoint only for older Jira Server/Data Center.
    issues, next_token, guard = [], None, 0
    use_legacy = False
    while True:
        # renderedFields gives Jira's server-rendered HTML, whose <img> tags carry
        # the real attachment ids in document order — used to place inline images.
        params = {"jql": query, "maxResults": 100, "fields": ISSUE_FIELDS, "expand": "renderedFields"}
        if next_token:
            params["nextPageToken"] = next_token
        resp = requests.get(f"{base}/rest/api/3/search/jql", params=params, auth=auth, headers=headers, timeout=60)
        if resp.status_code in (404, 410):
            use_legacy = True
            break
        _raise_for_response(resp)
        resp.raise_for_status()
        data = _jira_json(resp)
        issues.extend(data.get("issues", []))
        next_token = data.get("nextPageToken")
        guard += 1
        if data.get("isLast") or not next_token or (limit and len(issues) >= limit) or guard > 10000:
            break

    if use_legacy:
        # Legacy offset pagination (Jira Server / Data Center).
        issues, start = [], 0
        while True:
            params = {
                "jql": query,
                "startAt": start,
                "maxResults": 100,
                "fields": ISSUE_FIELDS,
                "expand": "renderedFields",
            }
            resp = requests.get(f"{base}/rest/api/2/search", params=params, auth=auth, headers=headers, timeout=60)
            _raise_for_response(resp)
            resp.raise_for_status()
            data = _jira_json(resp)
            batch = data.get("issues", [])
            issues.extend(batch)
            start += len(batch)
            if not batch or start >= data.get("total", 0) or (limit and len(issues) >= limit):
                break

    return issues[:limit] if limit else issues


def build_maps(project):
    state_map, default_state = {}, None
    for st in State.objects.filter(project=project).exclude(group="triage"):
        state_map[norm(st.name)] = st
        if st.default:
            default_state = st
    member_map = {}
    for wm in WorkspaceMember.objects.filter(workspace=project.workspace, is_active=True).select_related("member"):
        if wm.member.email:
            member_map[wm.member.email.lower()] = wm.member

    # Display-name fallback for when Jira doesn't return an emailAddress (Jira
    # Cloud hides it from the REST API unless the requester is an org admin or
    # the target user opted into public visibility — the common case, not the
    # exception). Same matching keys the CSV importer already uses
    # (jira_csv_importer._build_maps): exact, case-insensitive display_name or
    # "first_name last_name".
    #
    # Deliberately scoped to this PROJECT's members, not the whole workspace
    # (unlike the email map above, which is a precise match and predates this
    # fallback). Display names are far easier for the project admin running
    # the import to already know or guess than someone else's email, and an
    # import only requires project-admin trust, not workspace-owner trust —
    # so a workspace-wide name match would let a project admin attribute
    # fabricated comments/worklogs to any workspace member, including ones
    # who have nothing to do with this project.
    # Two project members can share a display name (two "John Smith"s isn't
    # exotic) — silently keeping whichever one the loop happened to see last
    # would misattribute that person's Jira history (and worklogs, which feed
    # the billing report) to a real but wrong person, with nothing surfaced
    # anywhere. Track any name that resolves to more than one distinct member
    # and drop it from the map entirely rather than guessing — resolve_member
    # then treats it as no match, same as any other unmapped name.
    member_by_name = {}
    ambiguous_names = set()
    for pm in ProjectMember.objects.filter(project=project, is_active=True).select_related("member"):
        m = pm.member
        for k in {(m.display_name or "").lower(), f"{m.first_name} {m.last_name}".strip().lower()}:
            if not k:
                continue
            if k in member_by_name and member_by_name[k].id != m.id:
                ambiguous_names.add(k)
            else:
                member_by_name[k] = m
    for k in ambiguous_names:
        member_by_name.pop(k, None)
    return state_map, default_state, member_map, member_by_name


def resolve_member(member_map, member_by_name, jira_user):
    """Resolve a Jira user object (assignee/reporter/author) to a Plane member.

    Email first (most precise, avoids collisions between similarly-named
    people) — but Jira frequently omits it, so fall back to an exact
    display-name match rather than leaving the field unmapped. Returns None
    if neither matches so callers can still distinguish "no match" from "a
    match that happens to be the initiator."
    """
    jira_user = jira_user or {}
    email = (jira_user.get("emailAddress") or "").lower()
    if email and email in member_map:
        return member_map[email]
    display_name = (jira_user.get("displayName") or "").strip().lower()
    if display_name and display_name in member_by_name:
        return member_by_name[display_name]
    return None


def resolve_rate(project, user_id):
    cap = ResourceCapacity.objects.filter(workspace=project.workspace, user_id=user_id).first()
    if cap and cap.billable_rate is not None:
        return cap.billable_rate, cap.currency
    if project.client_id and project.client and project.client.default_billable_rate is not None:
        return project.client.default_billable_rate, project.client.currency
    return None, "USD"


def is_project_admin(project, user):
    return ProjectMember.objects.filter(
        project=project, member=user, role=20, is_active=True
    ).exists()


def _basic_auth_header(jira_auth):
    """Build a Basic-Auth header from the (email, token) tuple used elsewhere
    as `requests`' `auth=`. pinned_fetch's own `auth` kwarg is reserved for
    credentials embedded in the URL itself, so it can't be reused here."""
    if not jira_auth:
        return {}
    email, token = jira_auth
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _download_attachment(content_url, jira_auth, max_bytes, allowed_origin):
    """Stream a Jira attachment to a temp file (spilling to disk past 8 MB).

    Large files (e.g. screen recordings) must not be buffered whole in memory —
    the previous `resp.content` held the entire file in the worker's RAM. The
    read timeout also scales with the file so a big download isn't cut off
    mid-transfer on a slow link.

    `content_url` comes straight out of a Jira API response - a Jira instance
    that's compromised, misconfigured, or simply malicious could point it
    anywhere, including at internal-network addresses (SSRF). Restrict it to
    the exact (scheme, host) of the configured Jira instance (`allowed_origin`)
    - the scheme check matters too: without it a same-host `http://` URL would
    still be "the right host" but would send the Basic-Auth header in
    cleartext - and fetch through `pinned_fetch_following_redirects` so a
    same-host redirect can't be re-pointed at an internal IP either.

    `allowed_hosts=[host]` lets that one exact, admin-configured hostname skip
    the private-IP block: a self-hosted Jira instance living on the same
    internal network as Plane is a legitimate, common deployment, and this
    function already restricts the request to that single hostname above (an
    admin-trusted value, not attacker-controlled data) - the connection is
    still pinned to whatever IP that hostname resolves to, so DNS rebinding to
    a *different* internal target is still blocked.

    Returns (fileobj, size) on success, ("too_large", size) if it exceeds
    max_bytes, or (None, 0) on a blocked/mismatched origin, network, or HTTP
    failure.
    """
    if not allowed_origin:
        return None, 0
    allowed_scheme, allowed_host = allowed_origin
    parsed = urlparse(content_url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    scheme = (parsed.scheme or "").lower()
    if hostname != allowed_host or scheme != allowed_scheme:
        return None, 0
    # 60s to connect; read timeout is per-chunk, so a slow-but-alive transfer
    # keeps going. Tunable for very slow Jira instances.
    read_timeout = int(os.environ.get("JIRA_ATTACHMENT_READ_TIMEOUT", 300))
    try:
        resp, _ = pinned_fetch_following_redirects(
            "GET",
            content_url,
            headers=_basic_auth_header(jira_auth),
            timeout=(60, read_timeout),
            stream=True,
            allowed_hosts=[allowed_host],
        )
        with resp:
            resp.raise_for_status()
            tmp = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            size = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    tmp.close()
                    return "too_large", size
                tmp.write(chunk)
            tmp.seek(0)
            return tmp, size
    except (requests.RequestException, ValueError):
        return None, 0


def migrate_attachments(
    issue, jira_attachments, project, initiator, jira_auth, member_map, member_by_name, allowed_origin
):
    """Download each Jira attachment and store it as a Plane FileAsset.

    Bytes are streamed from Jira's authenticated `content` URL straight into the
    configured object store (S3/MinIO) via Plane's storage helper, then a
    FileAsset row links them to the issue. Idempotent on
    (external_source='jira', external_id=<jira attachment id>). Files over
    settings.FILE_SIZE_LIMIT are skipped (counted, not fatal).
    """
    counts = {"created": 0, "skipped_size": 0, "skipped_existing": 0, "failed": 0}
    storage = None
    for att in jira_attachments or []:
        att_id = str(att.get("id") or "")
        content_url = att.get("content")
        if not att_id or not content_url:
            counts["failed"] += 1
            continue
        if FileAsset.objects.filter(issue_id=issue.id, external_source="jira", external_id=att_id).exists():
            counts["skipped_existing"] += 1
            continue
        # Skip early on the size Jira reports, before spending a download.
        if int(att.get("size") or 0) > settings.FILE_SIZE_LIMIT:
            counts["skipped_size"] += 1
            continue
        fileobj, size = _download_attachment(content_url, jira_auth, settings.FILE_SIZE_LIMIT, allowed_origin)
        if fileobj == "too_large":
            counts["skipped_size"] += 1
            continue
        if fileobj is None:
            counts["failed"] += 1
            continue

        filename = sanitize_filename(att.get("filename") or "") or uuid4().hex
        mime = att.get("mimeType") or "application/octet-stream"
        asset_key = f"{project.workspace_id}/{uuid4().hex}-{filename}"
        if storage is None:
            storage = S3Storage()
        try:
            uploaded = storage.upload_file(fileobj, asset_key, content_type=mime)
        except Exception:
            uploaded = False
        finally:
            fileobj.close()
        if not uploaded:
            counts["failed"] += 1
            continue

        author = resolve_member(member_map, member_by_name, att.get("author")) or initiator
        FileAsset.objects.create(
            attributes={"name": filename, "type": mime, "size": size},
            asset=asset_key,
            size=size,
            workspace_id=project.workspace_id,
            project_id=project.id,
            issue_id=issue.id,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_uploaded=True,
            external_id=att_id,
            external_source="jira",
            created_by=author,
        )
        counts["created"] += 1
    return counts


# --- Inline image (rich body) support ---------------------------------------
# Jira ADF `media` nodes reference an image by an opaque media id, not the REST
# attachment id. Jira's server-rendered HTML (expand=renderedFields), however,
# emits <img> tags in document order whose src carries the attachment id — so we
# read the order from there and resolve each `media` node to an attachment,
# falling back to image-attachment list order when rendered HTML is unavailable.

_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.IGNORECASE)
_ATTACHMENT_ID_RE = re.compile(r"/attachment/(?:content|thumbnail)/(\d+)")


def _rendered_attachment_ids(rendered_html):
    """Attachment ids referenced by <img> tags in Jira rendered HTML, in order."""
    ids = []
    for src in _IMG_SRC_RE.findall(rendered_html or ""):
        m = _ATTACHMENT_ID_RE.search(src)
        if m:
            ids.append(m.group(1))
    return ids


def _ordered_media_attachments(rendered_html, attachments):
    """Attachments in document order (those referenced by rendered <img> tags
    first, then the rest in list order).

    Deliberately includes EVERY attachment type, not just images: an ADF `media`
    node can reference a PDF, video or document, and filtering those out left
    them unresolvable (they rendered as a dead "[image: attachment]" placeholder
    even though the file had migrated fine into the attachments list).
    """
    by_id = {str(a.get("id")): a for a in (attachments or [])}
    all_ids = [str(a.get("id")) for a in (attachments or [])]
    seen, ordered = set(), []
    for aid in _rendered_attachment_ids(rendered_html):
        if aid in by_id and aid not in seen:
            seen.add(aid)
            ordered.append(by_id[aid])
    for aid in all_ids:
        if aid not in seen:
            seen.add(aid)
            ordered.append(by_id[aid])
    return ordered


def _attachment_link_html(att, asset):
    """Anchor showing the original Jira file name, pointing at the migrated
    attachment so it can be opened straight from the body/comment. Falls back to
    a named placeholder when the file didn't migrate (never a generic label)."""
    name = _html.escape(str(att.get("filename") or "attachment"), quote=False)
    url = getattr(asset, "asset_url", None) if asset is not None else None
    if not url:
        return f"<p>[attachment: {name}]</p>"
    return f'<p><a href="{_html.escape(str(url), quote=True)}" target="_blank" rel="noopener noreferrer">{name}</a></p>'


def _upload_body_image(
    att, entity_type, link_kwargs, project, initiator, jira_auth, member_map, member_by_name, cache, allowed_origin
):
    """Download a Jira image attachment and store it as an ISSUE_DESCRIPTION /
    COMMENT_DESCRIPTION FileAsset. Returns the new asset id (str) or None.
    Idempotent per (entity_type, attachment id) so re-imports don't duplicate."""
    att_id = str(att.get("id") or "")
    content_url = att.get("content")
    if not att_id or not content_url:
        return None
    ext_id = f"jira-{entity_type.lower()}-{att_id}"
    existing = FileAsset.objects.filter(external_source="jira", external_id=ext_id, **link_kwargs).first()
    if existing:
        return str(existing.id)
    if int(att.get("size") or 0) > settings.FILE_SIZE_LIMIT:
        return None
    # FORK: svg-xss-hardening — Jira's reported mimeType is attacker-controlled
    # (an imported source project can claim anything). Reject denylisted types
    # here rather than trusting the download-side disposition guard alone: this
    # asset is created directly, bypassing every upload-path MIME allowlist.
    reported_mime_type = (att.get("mimeType") or "").split(";")[0].strip().lower()
    if reported_mime_type in settings.SCRIPT_CAPABLE_MIME_TYPES:
        return None

    # Stream the download through the same size-capped, spill-to-disk path used
    # for real attachments (`_download_attachment`) instead of buffering the
    # whole body into memory. A mis-reported Jira `size` (e.g. 0 for a multi-GB
    # body) can no longer drive an uncapped allocation on the import worker: the
    # cap is enforced DURING streaming, aborting early with a "too_large"
    # sentinel. The per-issue cache holds the resulting file-like object (a
    # SpooledTemporaryFile) so an image referenced from both the description and
    # a comment is fetched only once; every read seeks back to 0 first.
    cached = cache.get(att_id) if cache is not None else None
    if cached is not None:
        fileobj, size = cached
    else:
        fileobj, size = _download_attachment(content_url, jira_auth, settings.FILE_SIZE_LIMIT, allowed_origin)
        if fileobj == "too_large" or fileobj is None:
            return None
        if cache is not None:
            cache[att_id] = (fileobj, size)

    filename = sanitize_filename(att.get("filename") or "") or uuid4().hex
    mime = att.get("mimeType") or "application/octet-stream"
    asset_key = f"{project.workspace_id}/{uuid4().hex}-{filename}"
    try:
        fileobj.seek(0)
        uploaded = S3Storage().upload_file(fileobj, asset_key, content_type=mime)
    except Exception:
        # A storage failure must degrade to a placeholder, never abort the import.
        return None
    finally:
        # A cached object is reused for the next entity in this issue, so only
        # close it here when nothing is holding it for reuse; the cache dict
        # (and its temp files) is dropped when the issue finishes importing.
        if cache is None:
            fileobj.close()
    if not uploaded:
        return None

    author = resolve_member(member_map, member_by_name, att.get("author")) or initiator
    asset = FileAsset.objects.create(
        attributes={"name": filename, "type": mime, "size": size},
        asset=asset_key,
        size=size,
        workspace_id=project.workspace_id,
        project_id=project.id,
        entity_type=entity_type,
        is_uploaded=True,
        external_id=ext_id,
        external_source="jira",
        created_by=author,
        **link_kwargs,
    )
    return str(asset.id)


def _make_media_resolver(ordered_attachments, consumed, upload_fn, asset_lookup):
    """Build a `media_resolver(media_node) -> html`.

    Images are embedded inline as <image-component>; every other file type is
    rendered as a named link to its migrated attachment, so a PDF/video/doc in a
    Jira comment stays identifiable and openable. `consumed` is shared across an
    issue's description and all of its comments so one attachment is never
    assigned to two places.
    """

    def resolve(media_node):
        att = None
        for candidate in ordered_attachments:
            if str(candidate.get("id")) not in consumed:
                att = candidate
                consumed.add(str(candidate.get("id")))
                break
        if att is None:
            alt = (media_node.get("attrs") or {}).get("alt") or "attachment"
            return f"<p>[attachment: {_html.escape(str(alt), quote=False)}]</p>"

        if str(att.get("mimeType") or "").startswith("image/"):
            asset_id = upload_fn(att)
            if asset_id:
                return f'<image-component src="{asset_id}"></image-component>'
        # Non-image (or an image whose inline upload failed): link to the file.
        return _attachment_link_html(att, asset_lookup.get(str(att.get("id"))))

    return resolve


def run_import(
    project,
    initiator,
    issues,
    with_worklogs=False,
    dry_run=True,
    progress=None,
    preview_limit=25,
    with_attachments=False,
    jira_auth=None,
    jira_url=None,
):
    """Process Jira issues into Plane. Returns a result dict.

    `issues` is a list of Jira issue dicts (already fetched). When dry_run,
    nothing is written; a per-issue `preview` (capped at preview_limit) and the
    same aggregate counts are returned. `progress(processed, total)` is called
    after each issue for background-job status updates.

    `jira_url` (required whenever `with_attachments` is set) pins attachment/
    inline-image downloads to that instance's own host - see
    `_download_attachment` for why.
    """
    total = len(issues)
    created = skipped = comments_n = worklogs_n = attachments_n = 0
    att_totals = {"created": 0, "skipped_size": 0, "skipped_existing": 0, "failed": 0}
    unmapped_states, unmapped_users = set(), set()
    preview = []

    # (scheme, host) of the configured Jira instance - attachment downloads are
    # restricted to this exact origin (see _download_attachment). None when
    # jira_url isn't set (previews/samples, which don't download anything).
    _jira_base = urlparse(normalize_jira_base(jira_url)) if jira_url else None
    allowed_origin = (
        (_jira_base.scheme.lower(), _jira_base.hostname.rstrip(".").lower())
        if _jira_base and _jira_base.scheme and _jira_base.hostname
        else None
    )

    state_map, default_state, member_map, member_by_name = build_maps(project)

    for idx, ji in enumerate(issues):
        key = ji.get("key")
        f = ji.get("fields", {})
        summary = f.get("summary") or key

        if Issue.objects.filter(project=project, external_source="jira", external_id=key).exists():
            skipped += 1
            if progress:
                progress(idx + 1, total)
            continue

        status_name = (f.get("status") or {}).get("name", "")
        state = state_map.get(norm(status_name)) or default_state
        if norm(status_name) not in state_map:
            unmapped_states.add(status_name)
        pr = ((f.get("priority") or {}).get("name") or "").lower()
        priority = PRIORITY_MAP.get(pr, "none")

        assignee = f.get("assignee") or {}
        assignee_user = resolve_member(member_map, member_by_name, assignee)
        if assignee and not assignee_user:
            unmapped_users.add(assignee.get("displayName") or assignee.get("emailAddress") or "unknown")

        labels = f.get("labels") or []
        rendered = ji.get("renderedFields") or {}
        jira_comments = (f.get("comment") or {}).get("comments", [])
        jira_worklogs = (f.get("worklog") or {}).get("worklogs", []) if with_worklogs else []
        jira_attachments = (f.get("attachment") or []) if with_attachments else []
        comments_n += len(_flatten_jira_comments(jira_comments))
        worklogs_n += len(jira_worklogs)
        attachments_n += len(jira_attachments)
        created += 1

        if len(preview) < preview_limit:
            preview.append(
                {
                    "key": key,
                    "summary": summary[:80],
                    "state": state.name if state else None,
                    "priority": priority,
                    "assignee": assignee_user.display_name if assignee_user else None,
                    "labels": len(labels),
                    "comments": len(jira_comments),
                    "worklogs": len(jira_worklogs),
                    "attachments": len(jira_attachments),
                }
            )

        if not dry_run:
            # Per-issue download cache so an image used both inline and as an
            # attachment is fetched from Jira only once.
            dl_cache = {}
            can_embed = bool(with_attachments and jira_auth)

            issue = Issue(
                project=project,
                workspace=project.workspace,
                name=summary[:254],
                # Placeholder: inline images need the saved issue's id first.
                description_html="<p></p>",
                priority=priority,
                state=state,
                external_source="jira",
                external_id=key,
                created_by_id=initiator.id,
            )
            if f.get("duedate"):
                issue.target_date = f["duedate"]
            issue.save(created_by_id=initiator.id)

            # Attachments are migrated BEFORE the body is converted so inline
            # references (especially non-image files) can link to the migrated
            # file rather than degrading to a placeholder.
            attachment_assets = {}
            if can_embed and jira_attachments:
                c = migrate_attachments(
                    issue, jira_attachments, project, initiator, jira_auth, member_map, member_by_name, allowed_origin
                )
                for k in att_totals:
                    att_totals[k] += c[k]
                attachment_assets = {
                    str(a.external_id): a
                    for a in FileAsset.objects.filter(
                        issue_id=issue.id,
                        external_source="jira",
                        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
                    )
                }
            # One attachment is never assigned to two places across this issue.
            consumed_media = set()

            # Convert the ADF description to editor HTML (formatting preserved),
            # embedding inline images when attachment migration is enabled.
            desc_resolver = None
            if can_embed:
                desc_atts = _ordered_media_attachments(rendered.get("description") or "", jira_attachments)
                desc_resolver = _make_media_resolver(
                    desc_atts,
                    consumed_media,
                    lambda att: _upload_body_image(
                        att,
                        FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
                        {"issue_id": issue.id},
                        project,
                        initiator,
                        jira_auth,
                        member_map,
                        member_by_name,
                        dl_cache,
                        allowed_origin,
                    ),
                    attachment_assets,
                )
            desc_html = adf_document_to_html(f.get("description"), desc_resolver)
            if desc_html and desc_html != issue.description_html:
                issue.description_html = desc_html
                issue.save(created_by_id=initiator.id)

            if assignee_user:
                IssueAssignee.objects.create(
                    issue=issue, assignee=assignee_user, project=project, created_by_id=initiator.id
                )
            for lname in labels:
                label, _ = Label.objects.get_or_create(
                    project=project, name=lname, defaults={"created_by_id": initiator.id}
                )
                IssueLabel.objects.create(issue=issue, label=label, project=project, created_by_id=initiator.id)
            rendered_comments = (rendered.get("comment") or {}).get("comments", [])
            # FORK: jira-comment-structure (#21) — import replies too and remember
            # each comment's Jira parent id so threading can be restored below.
            flat_comments = _flatten_jira_comments(jira_comments)
            comments_by_external = {}  # jira comment id -> IssueComment
            comment_parent_links = {}  # IssueComment id -> parent jira comment id
            for ci, (jc, parent_jira_id, rendered_idx) in enumerate(flat_comments):
                jc_author = jc.get("author") or {}
                matched_actor = resolve_member(member_map, member_by_name, jc_author)
                actor = matched_actor or initiator
                if jc_author and not matched_actor:
                    unmapped_users.add(jc_author.get("displayName") or jc_author.get("emailAddress") or "unknown")
                comment = IssueComment(
                    issue=issue,
                    project=project,
                    workspace=project.workspace,
                    comment_html="<p></p>",  # placeholder; inline images need the saved comment id
                    actor=actor,
                    external_source="jira",
                    external_id=str(jc.get("id")),
                    # FORK: jira-comment-provenance (#15) — attribute the comment
                    # to the resolved author (not the import initiator) and keep
                    # the Jira display name when the author couldn't be mapped.
                    external_actor_display=(
                        None if matched_actor else (jc_author.get("displayName") or jc_author.get("emailAddress"))
                    ),
                    created_by_id=actor.id,
                )
                comment.save(created_by_id=actor.id)
                comments_by_external[str(jc.get("id"))] = comment
                if parent_jira_id:
                    comment_parent_links[comment.id] = parent_jira_id

                c_resolver = None
                if can_embed:
                    rc_html = (
                        rendered_comments[rendered_idx].get("body")
                        if rendered_idx is not None and rendered_idx < len(rendered_comments)
                        else ""
                    )
                    c_atts = _ordered_media_attachments(rc_html or "", jira_attachments)
                    c_resolver = _make_media_resolver(
                        c_atts,
                        consumed_media,
                        lambda att, _cid=comment.id: _upload_body_image(
                            att,
                            FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
                            {"comment_id": _cid, "issue_id": issue.id},
                            project,
                            initiator,
                            jira_auth,
                            member_map,
                            member_by_name,
                            dl_cache,
                            allowed_origin,
                        ),
                        attachment_assets,
                    )
                body_html = adf_document_to_html(jc.get("body"), c_resolver)
                if body_html and body_html != comment.comment_html:
                    comment.comment_html = body_html
                    comment.save(created_by_id=actor.id)
                # FORK: jira-comment-provenance (#15) — backfill the original
                # Jira timestamps after the final save. A queryset update()
                # bypasses auto_now_add/auto_now; assigning on the instance
                # before save would be silently overwritten.
                jc_created = parse_datetime(jc.get("created")) if jc.get("created") else None
                jc_updated = parse_datetime(jc.get("updated")) if jc.get("updated") else None
                if jc_created:
                    timestamp_fields = {"created_at": jc_created}
                    if jc_updated and jc_updated > jc_created:
                        timestamp_fields["edited_at"] = jc_updated
                    IssueComment.objects.filter(id=comment.id).update(**timestamp_fields)
                    if comment.description_id:
                        Description.objects.filter(id=comment.description_id).update(created_at=jc_created)
            # FORK: jira-comment-structure (#21) — restore Jira's reply threading.
            # Resolve parents from this issue's just-imported comments first, then
            # the DB (covers parents imported in an earlier run). A queryset
            # update() keeps the backfilled timestamps untouched.
            for child_id, parent_jira_id in comment_parent_links.items():
                parent_comment = comments_by_external.get(parent_jira_id) or IssueComment.objects.filter(
                    issue=issue, external_source="jira", external_id=parent_jira_id
                ).first()
                if parent_comment and parent_comment.id != child_id:
                    IssueComment.objects.filter(id=child_id, parent__isnull=True).update(parent_id=parent_comment.id)
            for jw in jira_worklogs:
                seconds = int(jw.get("timeSpentSeconds") or 0)
                if seconds <= 0:
                    continue
                jw_author = jw.get("author") or {}
                matched_author = resolve_member(member_map, member_by_name, jw_author)
                author = matched_author or initiator
                if jw_author and not matched_author:
                    unmapped_users.add(jw_author.get("displayName") or jw_author.get("emailAddress") or "unknown")
                started = parse_datetime(jw.get("started")) if jw.get("started") else None
                rate, currency = resolve_rate(project, author.id)
                IssueWorklog(
                    project=project,
                    workspace=project.workspace,
                    issue=issue,
                    logged_by=author,
                    duration=max(1, math.floor(seconds / 60)),
                    description=adf_to_text(jw.get("comment"))[:500],
                    logged_date=(started or timezone.now()).date(),
                    source="manual",
                    is_billable=True,
                    billable_rate=rate,
                    currency=currency,
                ).save(created_by_id=initiator.id)

            # (attachments were migrated earlier, before body conversion, so the
            # description/comments could link to the migrated files)

        if progress:
            progress(idx + 1, total)

    return {
        "dry_run": dry_run,
        "fetched": total,
        "created": created,
        "skipped": skipped,
        "comments": comments_n,
        "worklogs": worklogs_n,
        "attachments": attachments_n,
        "attachments_created": att_totals["created"],
        "attachments_skipped_size": att_totals["skipped_size"],
        "attachments_failed": att_totals["failed"],
        "unmapped_states": sorted(s for s in unmapped_states if s),
        "unmapped_users": sorted(unmapped_users),
        "preview": preview,
    }


def sample_issues():
    """Realistic Jira-shaped fixtures for offline self-test / demos."""
    return [
        {
            "key": "ENG-101",
            "fields": {
                "summary": "Login page throws 500 on empty password",
                "description": {
                    "type": "doc",
                    "content": [
                        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Steps to Reproduce"}]},
                        {"type": "paragraph", "content": [
                            {"type": "text", "text": "Submit the login form with a "},
                            {"type": "text", "text": "blank", "marks": [{"type": "strong"}]},
                            {"type": "text", "text": " password. See "},
                            {"type": "text", "text": "the auth docs", "marks": [{"type": "link", "attrs": {"href": "https://example.com/auth"}}]},
                            {"type": "text", "text": "."},
                        ]},
                        {"type": "bulletList", "content": [
                            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Open /login"}]}]},
                            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Leave password empty"}]}]},
                        ]},
                        {"type": "table", "content": [
                            {"type": "tableRow", "content": [
                                {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Field"}]}]},
                                {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Expected"}]}]},
                                {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Actual"}]}]},
                            ]},
                            {"type": "tableRow", "content": [
                                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Status"}]}]},
                                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "400"}]}]},
                                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "500"}]}]},
                            ]},
                        ]},
                    ],
                },
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "Alice Anderson", "emailAddress": "alice@plane.test"},
                "reporter": {"displayName": "Admin User", "emailAddress": "admin@plane.test"},
                "labels": ["bug", "auth"],
                "duedate": "2026-07-15",
                "comment": {"comments": [
                    {"id": "9001", "author": {"emailAddress": "bob@plane.test"}, "body": {
                        "type": "doc", "content": [
                            {"type": "paragraph", "content": [
                                {"type": "text", "text": "Reproduced on "},
                                {"type": "text", "text": "staging", "marks": [{"type": "strong"}]},
                                {"type": "text", "text": "."},
                            ]},
                        ],
                    }},
                    {"id": "9002", "author": {"emailAddress": "alice@plane.test"}, "body": "Fix in review."},
                ]},
                "worklog": {"worklogs": [
                    {"author": {"emailAddress": "alice@plane.test"}, "timeSpentSeconds": 5400, "started": "2026-06-20T09:00:00.000+0000", "comment": "Investigation"},
                    {"author": {"emailAddress": "bob@plane.test"}, "timeSpentSeconds": 1800, "started": "2026-06-21T14:00:00.000+0000", "comment": "Pair debugging"},
                ]},
            },
        },
        {
            "key": "ENG-102",
            "fields": {
                "summary": "Add CSV export to reports",
                "description": "Users want to download the report as CSV.",
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "assignee": None,
                "reporter": {"displayName": "Admin User", "emailAddress": "admin@plane.test"},
                "labels": ["feature"],
                "comment": {"comments": []},
                "worklog": {"worklogs": []},
            },
        },
    ]
