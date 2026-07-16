# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared Jira -> Plane import engine.

Used by the `import_jira` management command, the synchronous preview API,
and the background import task. Fetches Jira issues/comments/worklogs over
REST and writes them through the ORM (so worklogs land in the time-tracking
tables). Idempotent via Issue.external_id.
"""

import html as _html
import math
import re
from io import BytesIO
from urllib.parse import urlparse
from uuid import uuid4

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from plane.utils.jira_adf import adf_document_to_html

from plane.db.models import (
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


def fetch_jira_issues(jira_url, jira_email, jira_token, jira_project=None, jql=None, limit=0):
    """Fetch issues (with comments + worklogs) from Jira Cloud REST API v3."""
    for key, val in (("jira_url", jira_url), ("jira_email", jira_email), ("jira_token", jira_token)):
        if not val:
            raise JiraConfigError(f"Missing Jira setting: {key}")
    query = jql or (f"project = {jira_project} ORDER BY created ASC" if jira_project else None)
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
    return state_map, default_state, member_map


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


def migrate_attachments(issue, jira_attachments, project, initiator, jira_auth, member_map):
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
        try:
            resp = requests.get(content_url, auth=jira_auth, timeout=120)
            resp.raise_for_status()
        except requests.RequestException:
            counts["failed"] += 1
            continue
        content = resp.content
        if len(content) > settings.FILE_SIZE_LIMIT:
            counts["skipped_size"] += 1
            continue

        filename = sanitize_filename(att.get("filename") or "") or uuid4().hex
        mime = att.get("mimeType") or "application/octet-stream"
        asset_key = f"{project.workspace_id}/{uuid4().hex}-{filename}"
        if storage is None:
            storage = S3Storage()
        if not storage.upload_file(BytesIO(content), asset_key, content_type=mime):
            counts["failed"] += 1
            continue

        author_email = ((att.get("author") or {}).get("emailAddress") or "").lower()
        author = member_map.get(author_email) or initiator
        FileAsset.objects.create(
            attributes={"name": filename, "type": mime, "size": len(content)},
            asset=asset_key,
            size=len(content),
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


def _ordered_image_attachments(rendered_html, attachments):
    """Image attachments in document order (rendered <img> refs first, then any
    remaining image attachments in list order). Non-images are never returned."""
    by_id = {str(a.get("id")): a for a in (attachments or [])}
    image_ids = [str(a.get("id")) for a in (attachments or []) if str(a.get("mimeType") or "").startswith("image/")]
    image_id_set = set(image_ids)
    seen, ordered = set(), []
    for aid in _rendered_attachment_ids(rendered_html):
        if aid in image_id_set and aid not in seen:
            seen.add(aid)
            ordered.append(by_id[aid])
    for aid in image_ids:
        if aid not in seen:
            seen.add(aid)
            ordered.append(by_id[aid])
    return ordered


def _upload_body_image(att, entity_type, link_kwargs, project, initiator, jira_auth, member_map, cache):
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

    data = cache.get(att_id) if cache is not None else None
    if data is None:
        try:
            resp = requests.get(content_url, auth=jira_auth, timeout=120)
            resp.raise_for_status()
        except requests.RequestException:
            return None
        data = resp.content
        if cache is not None:
            cache[att_id] = data
    if len(data) > settings.FILE_SIZE_LIMIT:
        return None

    filename = sanitize_filename(att.get("filename") or "") or uuid4().hex
    mime = att.get("mimeType") or "application/octet-stream"
    asset_key = f"{project.workspace_id}/{uuid4().hex}-{filename}"
    try:
        uploaded = S3Storage().upload_file(BytesIO(data), asset_key, content_type=mime)
    except Exception:
        # A storage failure must degrade to a placeholder, never abort the import.
        return None
    if not uploaded:
        return None

    author = member_map.get(((att.get("author") or {}).get("emailAddress") or "").lower()) or initiator
    asset = FileAsset.objects.create(
        attributes={"name": filename, "type": mime, "size": len(data)},
        asset=asset_key,
        size=len(data),
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


def _make_media_resolver(ordered_attachments, upload_fn):
    """Build a `media_resolver(media_node) -> html` that walks the ordered image
    attachments, embedding each as an <image-component>; emits a visible
    placeholder when the image can't be resolved (never silently dropped)."""
    state = {"i": 0}

    def resolve(media_node):
        idx = state["i"]
        state["i"] += 1
        att = ordered_attachments[idx] if idx < len(ordered_attachments) else None
        if att is not None:
            asset_id = upload_fn(att)
            if asset_id:
                return f'<image-component src="{asset_id}"></image-component>'
        alt = (media_node.get("attrs") or {}).get("alt") or (att.get("filename") if att else None) or "attachment"
        return f"<p>[image: {_html.escape(str(alt), quote=False)}]</p>"

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
):
    """Process Jira issues into Plane. Returns a result dict.

    `issues` is a list of Jira issue dicts (already fetched). When dry_run,
    nothing is written; a per-issue `preview` (capped at preview_limit) and the
    same aggregate counts are returned. `progress(processed, total)` is called
    after each issue for background-job status updates.
    """
    total = len(issues)
    created = skipped = comments_n = worklogs_n = attachments_n = 0
    att_totals = {"created": 0, "skipped_size": 0, "skipped_existing": 0, "failed": 0}
    unmapped_states, unmapped_users = set(), set()
    preview = []

    state_map, default_state, member_map = build_maps(project)

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
        a_email = (assignee.get("emailAddress") or "").lower()
        assignee_user = member_map.get(a_email)
        if assignee and not assignee_user:
            unmapped_users.add(assignee.get("displayName") or a_email or "unknown")

        labels = f.get("labels") or []
        rendered = ji.get("renderedFields") or {}
        jira_comments = (f.get("comment") or {}).get("comments", [])
        jira_worklogs = (f.get("worklog") or {}).get("worklogs", []) if with_worklogs else []
        jira_attachments = (f.get("attachment") or []) if with_attachments else []
        comments_n += len(jira_comments)
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

            # Convert the ADF description to editor HTML (formatting preserved),
            # embedding inline images when attachment migration is enabled.
            desc_resolver = None
            if can_embed:
                desc_atts = _ordered_image_attachments(rendered.get("description") or "", jira_attachments)
                desc_resolver = _make_media_resolver(
                    desc_atts,
                    lambda att: _upload_body_image(
                        att,
                        FileAsset.EntityTypeContext.ISSUE_DESCRIPTION,
                        {"issue_id": issue.id},
                        project,
                        initiator,
                        jira_auth,
                        member_map,
                        dl_cache,
                    ),
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
            for ci, jc in enumerate(jira_comments):
                actor = member_map.get(((jc.get("author") or {}).get("emailAddress") or "").lower()) or initiator
                comment = IssueComment(
                    issue=issue,
                    project=project,
                    workspace=project.workspace,
                    comment_html="<p></p>",  # placeholder; inline images need the saved comment id
                    actor=actor,
                    external_source="jira",
                    external_id=str(jc.get("id")),
                    created_by_id=initiator.id,
                )
                comment.save(created_by_id=initiator.id)

                c_resolver = None
                if can_embed:
                    rc_html = rendered_comments[ci].get("body") if ci < len(rendered_comments) else ""
                    c_atts = _ordered_image_attachments(rc_html or "", jira_attachments)
                    c_resolver = _make_media_resolver(
                        c_atts,
                        lambda att, _cid=comment.id: _upload_body_image(
                            att,
                            FileAsset.EntityTypeContext.COMMENT_DESCRIPTION,
                            {"comment_id": _cid, "issue_id": issue.id},
                            project,
                            initiator,
                            jira_auth,
                            member_map,
                            dl_cache,
                        ),
                    )
                body_html = adf_document_to_html(jc.get("body"), c_resolver)
                if body_html and body_html != comment.comment_html:
                    comment.comment_html = body_html
                    comment.save(created_by_id=initiator.id)
            for jw in jira_worklogs:
                seconds = int(jw.get("timeSpentSeconds") or 0)
                if seconds <= 0:
                    continue
                author = member_map.get(((jw.get("author") or {}).get("emailAddress") or "").lower()) or initiator
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

            if with_attachments and jira_auth and jira_attachments:
                c = migrate_attachments(issue, jira_attachments, project, initiator, jira_auth, member_map)
                for k in att_totals:
                    att_totals[k] += c[k]

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
