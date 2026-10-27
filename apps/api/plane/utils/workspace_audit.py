# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- write rows to the workspace audit log.

Call ``record`` inside the same ``transaction.atomic()`` as the change it describes, so an
entry exists exactly when the change committed. ``before`` / ``after`` hold only the fields
that changed (``diff``); secrets (webhook ``secret_key``) are never part of any state dict.
"""

import hashlib
from urllib.parse import urlsplit

WORKSPACE_FIELDS = ("name", "slug", "logo", "logo_asset_id", "organization_size", "timezone", "background_color")

# secret_key is deliberately absent.
WEBHOOK_FIELDS = (
    "url",
    "is_active",
    "project",
    "issue",
    "module",
    "cycle",
    "issue_comment",
    "is_internal",
    "version",
)

ROLE_NAMES = {20: "Admin", 15: "Member", 5: "Guest"}


def state(instance, fields):
    return {f: getattr(instance, f, None) for f in fields}


def workspace_state(workspace):
    data = state(workspace, WORKSPACE_FIELDS)
    if data.get("logo_asset_id") is not None:
        data["logo_asset_id"] = str(data["logo_asset_id"])
    return data


def redact_url(url):
    """``scheme://host/…`` plus a short fingerprint.

    Webhook URLs often carry a secret in the path or query (e.g. Slack incoming webhooks), and
    the log outlives the webhook, so only the host is kept. The fingerprint still shows that a
    URL changed when the host stayed the same.
    """
    if not url:
        return url
    parts = urlsplit(str(url))
    fingerprint = hashlib.sha256(str(url).encode()).hexdigest()[:6]
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}/… (#{fingerprint})" if host else f"… (#{fingerprint})"


def webhook_state(webhook):
    data = state(webhook, WEBHOOK_FIELDS)
    data["url"] = redact_url(data.get("url"))
    return data


def member_state(role, is_super_admin):
    return {"role": role, "role_name": ROLE_NAMES.get(role, str(role)), "is_super_admin": bool(is_super_admin)}


def diff(before, after):
    """Keep only the keys whose value changed. Returns (before, after) or (None, None)."""
    keys = [k for k in after if before.get(k) != after.get(k)]
    if not keys:
        return None, None
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def user_label(user):
    if user is None:
        return ""
    name = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
    return name or getattr(user, "display_name", "") or getattr(user, "email", "") or ""


def record_admin_invite(workspace_id, actor, email, invite_id=None):
    from plane.db.models import WorkspaceAuditAction, WorkspaceAuditTarget

    return record(
        workspace_id,
        actor,
        WorkspaceAuditAction.ADMIN_INVITED,
        WorkspaceAuditTarget.INVITE,
        target_id=invite_id,
        target_label=email,
        after={"email": email, "role": 20, "role_name": ROLE_NAMES[20]},
    )


def record_invite_role_change(workspace_id, actor, invite, before_role, after_role):
    """Only changes that make or unmake an Admin invitation are logged."""
    from plane.db.models import WorkspaceAuditAction, WorkspaceAuditTarget

    if before_role == after_role or 20 not in (before_role, after_role):
        return None
    return record(
        workspace_id,
        actor,
        WorkspaceAuditAction.INVITE_ROLE_CHANGED,
        WorkspaceAuditTarget.INVITE,
        target_id=invite.id,
        target_label=invite.email,
        before={"role": before_role, "role_name": ROLE_NAMES.get(before_role, str(before_role))},
        after={"role": after_role, "role_name": ROLE_NAMES.get(after_role, str(after_role))},
    )


def record(workspace_id, actor, action, target_type, target_id=None, target_label="", before=None, after=None):
    """Write one entry. ``actor`` None / anonymous = the system."""
    from plane.db.models import WorkspaceAuditLog

    if actor is not None and getattr(actor, "is_anonymous", False):
        actor = None
    return WorkspaceAuditLog.objects.create(
        workspace_id=workspace_id,
        actor_id=getattr(actor, "pk", None),
        actor_name=user_label(actor)[:255],
        actor_email=(getattr(actor, "email", "") or "")[:255],
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=(target_label or "")[:255],
        before=before,
        after=after,
    )
