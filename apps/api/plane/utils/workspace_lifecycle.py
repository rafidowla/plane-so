# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-125 -- workspace creation gate, delete re-confirmation, and restore.

* Creating a workspace is limited to instance admins and to Super Admins of at least one
  live workspace (the instance-wide DISABLE_WORKSPACE_CREATION switch still overrides).
* Deleting needs the workspace name typed plus an identity re-check: the account password,
  or -- for accounts without one (email-code / SSO sign-in) -- a fresh code sent by email.
* A deleted workspace is kept for WORKSPACE_HARD_DELETE_AFTER_DAYS and can be restored in
  that window. Restore walks the same relations the soft-delete cascade walked and revives
  only rows the cascade itself deleted (deleted_at >= the workspace's deleted_at); rows that
  were already deleted before keep their state.
"""

# Python imports
import hashlib
import hmac
import secrets
from datetime import timedelta

# Django imports
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

# Module imports
from plane.settings.redis import redis_instance

CREATION_FORBIDDEN_ERROR = "Ask a Super Admin to create a workspace."

DELETE_CODE_TTL_SECONDS = 600
DELETE_CODE_MAX_ISSUED = 3  # per DELETE_CODE_TTL_SECONDS window
DELETE_MAX_FAILED_CHECKS = 5
DELETE_FAILED_CHECK_WINDOW_SECONDS = 900

# Set while the soft-delete cascade for a workspace is still running; restore waits for it.
DELETION_RUNNING_TTL_SECONDS = 6 * 60 * 60

_RESTORE_CHUNK = 1000


def _code_key(workspace_id, user_id):
    return f"workspace_delete_code:{workspace_id}:{user_id}"


def _code_issued_key(workspace_id, user_id):
    return f"workspace_delete_code_issued:{workspace_id}:{user_id}"


def _failed_checks_key(workspace_id, user_id):
    return f"workspace_delete_failed:{workspace_id}:{user_id}"


def deletion_running_key(workspace_id):
    return f"workspace_deletion_running:{workspace_id}"


def _hash_code(code):
    return hashlib.sha256(str(code).encode()).hexdigest()


def _incr_with_ttl(ri, key, ttl):
    count = ri.incr(key)
    if count == 1:
        ri.expire(key, ttl)
    return int(count)


# --- creation gate -------------------------------------------------------------------------


def can_create_workspace(user):
    from plane.db.models import WorkspaceMember
    from plane.license.models import InstanceAdmin

    if InstanceAdmin.objects.filter(user=user).exists():
        return True
    return WorkspaceMember.objects.filter(
        member=user, is_super_admin=True, is_active=True, workspace__deleted_at__isnull=True
    ).exists()


# --- delete re-confirmation ----------------------------------------------------------------


def uses_password(user):
    """True when the account has a password the user chose (so it can be re-entered)."""
    return not user.is_password_autoset and user.has_usable_password()


def issue_delete_code(workspace_id, user_id):
    """Store a fresh 6-digit code (hashed) and return it, or None when too many were issued."""
    ri = redis_instance()
    if _incr_with_ttl(ri, _code_issued_key(workspace_id, user_id), DELETE_CODE_TTL_SECONDS) > DELETE_CODE_MAX_ISSUED:
        return None
    code = str(secrets.randbelow(900000) + 100000)
    ri.set(_code_key(workspace_id, user_id), _hash_code(code), ex=DELETE_CODE_TTL_SECONDS)
    return code


def too_many_failed_checks(workspace_id, user_id):
    raw = redis_instance().get(_failed_checks_key(workspace_id, user_id))
    return raw is not None and int(raw) >= DELETE_MAX_FAILED_CHECKS


def record_failed_check(workspace_id, user_id):
    _incr_with_ttl(redis_instance(), _failed_checks_key(workspace_id, user_id), DELETE_FAILED_CHECK_WINDOW_SECONDS)


def verify_delete_identity(user, workspace_id, password=None, code=None):
    """Check the password (password accounts) or the emailed code (everyone else).

    A matched code is consumed, so it works once.
    """
    if uses_password(user):
        return bool(password) and user.check_password(password)
    if not code:
        return False
    ri = redis_instance()
    key = _code_key(workspace_id, user.id)
    stored = ri.get(key)
    if stored is None:
        return False
    stored = stored.decode() if isinstance(stored, bytes) else stored
    if not hmac.compare_digest(stored, _hash_code(str(code).strip())):
        return False
    ri.delete(key)
    return True


def clear_delete_checks(workspace_id, user_id):
    redis_instance().delete(
        _code_key(workspace_id, user_id),
        _code_issued_key(workspace_id, user_id),
        _failed_checks_key(workspace_id, user_id),
    )


# --- deleted workspaces and restore --------------------------------------------------------


def purge_after_days():
    return settings.WORKSPACE_HARD_DELETE_AFTER_DAYS


def restorable_workspaces_for(user):
    """Deleted workspaces, still inside the keep window, that ``user`` was a Super Admin of."""
    from plane.db.models import Workspace, WorkspaceMember

    cutoff = timezone.now() - timedelta(days=purge_after_days())
    workspace_ids = WorkspaceMember.all_objects.filter(
        Q(deleted_at__isnull=True) | Q(deleted_at__gte=F("workspace__deleted_at")),
        member=user,
        is_super_admin=True,
        is_active=True,
        workspace__deleted_at__isnull=False,
        workspace__deleted_at__gte=cutoff,
    ).values("workspace_id")
    return Workspace.all_objects.filter(id__in=workspace_ids, deleted_at__isnull=False)


def original_slug(workspace):
    """Soft delete appends ``__<epoch>`` to the slug; strip it back off."""
    base, sep, suffix = workspace.slug.rpartition("__")
    return base if sep and suffix.isdigit() else workspace.slug


def purge_at(workspace):
    return workspace.deleted_at + timedelta(days=purge_after_days())


def deletion_still_running(workspace_id):
    return bool(redis_instance().exists(deletion_running_key(workspace_id)))


def _cascaded_relations(model):
    for relation in model._meta.get_fields():
        if not ((relation.one_to_many or relation.one_to_one) and relation.auto_created and not relation.concrete):
            continue
        on_delete = getattr(relation.on_delete, "__name__", "")
        if on_delete in ("DO_NOTHING", "SET_NULL"):
            continue
        related_model = relation.related_model
        if not hasattr(related_model, "all_objects") or not any(
            f.name == "deleted_at" for f in related_model._meta.concrete_fields
        ):
            continue
        yield related_model, relation.field.name


def restore_workspace_tree(workspace, since):
    """Undo the soft-delete cascade below ``workspace`` for rows deleted at or after ``since``.

    Mirrors ``soft_delete_related_objects``: same relations, same skips. A child the cascade
    skipped (already deleted earlier) has deleted_at < since, so it and everything under it
    stay deleted. Returns the number of rows revived.
    """
    revived = 0
    seen = set()
    queue = [(type(workspace), [workspace.pk])]
    while queue:
        model, pks = queue.pop()
        for related_model, fk_name in _cascaded_relations(model):
            for start in range(0, len(pks), _RESTORE_CHUNK):
                chunk = pks[start : start + _RESTORE_CHUNK]
                child_pks = [
                    pk
                    for pk in related_model.all_objects.filter(
                        **{f"{fk_name}__in": chunk, "deleted_at__gte": since}
                    ).values_list("pk", flat=True)
                    if (related_model, pk) not in seen
                ]
                if not child_pks:
                    continue
                seen.update((related_model, pk) for pk in child_pks)
                revived += related_model.all_objects.filter(pk__in=child_pks).update(deleted_at=None)
                queue.append((related_model, child_pks))
    return revived
