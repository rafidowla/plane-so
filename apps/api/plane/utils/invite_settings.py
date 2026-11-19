# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- workspace invitation settings (expiry, allowed domains) and pure helpers.

A workspace without a ``WorkspaceInviteSettings`` row behaves as the defaults; reading never creates a row.
"""

import re
from datetime import timedelta

from django.utils import timezone

DEFAULT_EXPIRY_DAYS = 7
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 90
MAX_DOMAINS = 50
MAX_DOMAIN_LENGTH = 253

# one DNS label: letters/digits/hyphen, not starting or ending with a hyphen; at least two labels
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")


def get_invite_settings(workspace_id):
    """The workspace's settings row, or an UNSAVED default instance (no row is created)."""
    from plane.db.models import WorkspaceInviteSettings

    row = WorkspaceInviteSettings.objects.filter(workspace_id=workspace_id).first()
    if row is not None:
        return row
    return WorkspaceInviteSettings(
        workspace_id=workspace_id,
        invite_expiry_days=DEFAULT_EXPIRY_DAYS,
        allowed_email_domains=[],
        project_admins_can_invite=False,
    )


def settings_payload(settings_obj):
    return {
        "invite_expiry_days": settings_obj.invite_expiry_days,
        "allowed_email_domains": list(settings_obj.allowed_email_domains or []),
        "project_admins_can_invite": bool(settings_obj.project_admins_can_invite),
    }


def normalize_domains(domains):
    """Strip, lowercase, drop a leading "@", dedupe (keep order). ValueError on anything invalid."""
    if not isinstance(domains, (list, tuple)):
        raise ValueError("Allowed domains must be a list")
    result = []
    for raw in domains:
        if not isinstance(raw, str):
            raise ValueError("Each domain must be text")
        domain = raw.strip().lower().lstrip("@")
        if not domain or len(domain) > MAX_DOMAIN_LENGTH or not _DOMAIN_RE.match(domain):
            raise ValueError(f"'{raw.strip()[:60]}' is not a valid domain")
        if domain not in result:
            result.append(domain)
    if len(result) > MAX_DOMAINS:
        raise ValueError(f"You can list at most {MAX_DOMAINS} domains")
    return result


def email_domain_allowed(email, domains):
    """Exact match on the part after the last "@". Empty list = no restriction. Subdomains do not match."""
    if not domains:
        return True
    if not isinstance(email, str) or "@" not in email:
        return False
    return email.rsplit("@", 1)[1].strip().lower() in {d.lower() for d in domains}


def invite_expires_at(invite, days):
    return invite.last_sent_at + timedelta(days=days)


def is_invite_expired(invite, days, now=None):
    return invite_expires_at(invite, days) < (now or timezone.now())


class InviteExpiryCache:
    """Per-workspace expiry days, looked up once each (avoids N+1 in serializers and sweeps)."""

    def __init__(self):
        self._days = {}

    def days(self, workspace_id):
        if workspace_id not in self._days:
            self._days[workspace_id] = get_invite_settings(workspace_id).invite_expiry_days
        return self._days[workspace_id]

    def is_expired(self, invite, now=None):
        return is_invite_expired(invite, self.days(invite.workspace_id), now)

    def expires_at(self, invite):
        return invite_expires_at(invite, self.days(invite.workspace_id))


# ---- renewal / resend helpers -------------------------------------------------------------------

RESEND_COOLDOWN_SECONDS = 60


def is_pending(invite):
    return not invite.accepted and invite.responded_at is None


def renew_invite(invite):
    """Start a fresh expiry window (call on a locked row, inside a transaction)."""
    invite.last_sent_at = timezone.now()
    invite.expiry_recorded_at = None
    invite.save(update_fields=["last_sent_at", "expiry_recorded_at", "updated_at"])
    return invite


def claim_resend_slot(invite_id):
    """Per-invite cooldown. ``cache.add`` is atomic, so a double click sends only once."""
    from django.core.cache import cache

    return cache.add(f"psr124:resend-invite:{invite_id}", 1, RESEND_COOLDOWN_SECONDS)


def invite_not_allowed_detail(email, settings_obj, requester_is_workspace_admin, requester_is_project_admin):
    """None when the requester may invite ``email``; else ``workspace_admin_only`` / ``domain_not_allowed``."""
    if requester_is_workspace_admin:
        return None
    if not (requester_is_project_admin and settings_obj.project_admins_can_invite):
        return "workspace_admin_only"
    if not email_domain_allowed(email, settings_obj.allowed_email_domains):
        return "domain_not_allowed"
    return None
