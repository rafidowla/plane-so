# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-124 -- hourly sweep that writes the "invitation expired" audit entry.

Expiry itself needs no job (it is computed from ``last_sent_at`` and the workspace's setting); this
only records each expiry once per send. ``expiry_recorded_at`` marks it done and is cleared when the
invitation is resent. Invitations are never deleted here: an expired one can still be resent or cancelled.
"""

# Python imports
from datetime import timedelta

# Third party imports
from celery import shared_task

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import WorkspaceMemberInvite
from plane.utils import workspace_audit
from plane.utils.exception_logger import log_exception
from plane.utils.invite_settings import MIN_EXPIRY_DAYS, InviteExpiryCache, invite_expires_at

BATCH_SIZE = 500


def _pending_unrecorded():
    return WorkspaceMemberInvite.objects.filter(
        accepted=False, responded_at__isnull=True, expiry_recorded_at__isnull=True
    )


@shared_task
def record_expired_invitations():
    try:
        now = timezone.now()
        expiry = InviteExpiryCache()
        workspace_ids = list(
            _pending_unrecorded()
            .filter(last_sent_at__lt=now - timedelta(days=MIN_EXPIRY_DAYS))
            .values_list("workspace_id", flat=True)
            .distinct()
        )
        recorded = 0
        for workspace_id in workspace_ids:
            cutoff = now - timedelta(days=expiry.days(workspace_id))
            while True:
                ids = list(
                    _pending_unrecorded()
                    .filter(workspace_id=workspace_id, last_sent_at__lt=cutoff)
                    .values_list("id", flat=True)[:BATCH_SIZE]
                )
                if not ids:
                    break
                with transaction.atomic():
                    # skip rows another request (resend, accept, cancel) is holding; re-check the state under lock
                    rows = list(
                        _pending_unrecorded()
                        .select_for_update(skip_locked=True, of=("self",))
                        .filter(id__in=ids, last_sent_at__lt=cutoff)
                    )
                    for invite in rows:
                        workspace_audit.record_invite_expired(
                            workspace_id, invite, invite_expires_at(invite, expiry.days(workspace_id))
                        )
                    # queryset update: no user / updated_at side effects on the invitation itself
                    WorkspaceMemberInvite.objects.filter(id__in=[i.id for i in rows]).update(expiry_recorded_at=now)
                recorded += len(rows)
                if not rows:
                    break  # everything left is locked by someone else; the next run picks it up
        return recorded
    except Exception as e:
        log_exception(e)
        return 0
