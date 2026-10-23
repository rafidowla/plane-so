# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 — one-off recovery of worklogs lost to the old delete cascade.

Before time-tracking v2 (migration 0128), soft-deleting a work item also soft-deleted
its worklogs, so the time vanished from every report. v2 keeps worklogs when their
work item is deleted. This command brings the already-lost entries back, as long as
they are still soft-deleted (the 60-day hard delete has not removed them yet).
"""

# Python imports
from datetime import timedelta

# Django imports
from django.core.management import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Sum
from django.utils import timezone

# Module imports
from plane.db.models import (
    Issue,
    IssueWorklog,
    TimeTrackingAuditAction,
    TimeTrackingAuditTarget,
    Workspace,
    WorklogTimer,
)
from plane.utils.time_tracking import audit
from plane.utils.time_tracking.snapshot import issue_snapshot

AUDIT_REASON = "recover_cascaded_worklogs"
# The cascade never runs before the delete it follows. A couple of seconds of
# negative slack only absorbs clock skew between the API and worker containers.
CLOCK_SKEW_SECONDS = 2
DEFAULT_TOLERANCE_SECONDS = 300
BATCH_SIZE = 500


def _window(tolerance_seconds):
    """(lower, upper) offsets from the issue's ``deleted_at`` a cascade could land in."""
    return timedelta(seconds=-CLOCK_SKEW_SECONDS), timedelta(seconds=tolerance_seconds)


def cascaded_worklogs(tolerance_seconds=DEFAULT_TOLERANCE_SECONDS, workspace_slug=None, project_alive=True):
    """Soft-deleted worklogs deleted by their work item's deletion.

    A worklog qualifies when its work item is soft-deleted too and the worklog's
    ``deleted_at`` is within ``[issue.deleted_at - 2s, issue.deleted_at + tolerance]``.
    With ``project_alive`` the project and workspace must not be deleted: worklogs
    that went down with a deleted project are still removed by v2 (project deletion
    keeps cascading), so they are not recovered.
    """
    lower, upper = _window(tolerance_seconds)
    qs = IssueWorklog.all_objects.filter(
        deleted_at__isnull=False,
        deleted_at__gte=F("issue__deleted_at") + lower,
        deleted_at__lte=F("issue__deleted_at") + upper,
    )
    if project_alive:
        qs = qs.filter(project__deleted_at__isnull=True, workspace__deleted_at__isnull=True)
    else:
        qs = qs.exclude(project__deleted_at__isnull=True, workspace__deleted_at__isnull=True)
    if workspace_slug:
        qs = qs.filter(workspace__slug=workspace_slug)
    return qs


def cascaded_timers(tolerance_seconds=DEFAULT_TOLERANCE_SECONDS, workspace_slug=None):
    """Running timers that were soft-deleted with their work item (informational only)."""
    lower, upper = _window(tolerance_seconds)
    qs = WorklogTimer.all_objects.filter(
        deleted_at__isnull=False,
        deleted_at__gte=F("issue__deleted_at") + lower,
        deleted_at__lte=F("issue__deleted_at") + upper,
        project__deleted_at__isnull=True,
        workspace__deleted_at__isnull=True,
    )
    if workspace_slug:
        qs = qs.filter(workspace__slug=workspace_slug)
    return qs


def orphaned_worklogs(workspace_slug=None):
    """Soft-deleted worklogs whose work item row no longer exists (cannot be classified)."""
    qs = IssueWorklog.all_objects.filter(deleted_at__isnull=False).exclude(
        Exists(Issue.all_objects.filter(pk=OuterRef("issue_id")))
    )
    if workspace_slug:
        qs = qs.filter(workspace__slug=workspace_slug)
    return qs


def _hours(minutes):
    return f"{(minutes or 0) / 60:.2f}"


def restore_worklog(worklog):
    """Put one cascade-deleted worklog into the state v2 gives a surviving entry.

    ``deleted_at`` cleared; the work-item label snapshot (``issue_ref`` / ``issue_name``)
    filled if it is blank, exactly like ``IssueWorklog.save``. Written with
    ``update()`` so no user is stamped on the row and the entry's own data is untouched.
    Audited as a system ``update`` on the worklog.
    """
    before = {**audit.worklog_state(worklog), "deleted_at": worklog.deleted_at}
    fields = {"deleted_at": None, "updated_at": timezone.now()}
    if not worklog.issue_ref:
        fields["issue_ref"], fields["issue_name"] = issue_snapshot(worklog.issue_id)
    IssueWorklog.all_objects.filter(pk=worklog.pk).update(**fields)
    for name, value in fields.items():
        setattr(worklog, name, value)
    audit.record(
        worklog.workspace_id,
        None,
        TimeTrackingAuditTarget.WORKLOG,
        worklog.id,
        TimeTrackingAuditAction.UPDATE,
        before={**before, "reason": AUDIT_REASON},
        after={**audit.worklog_state(worklog), "deleted_at": None, "reason": AUDIT_REASON},
    )


class Command(BaseCommand):
    help = (
        "Recover time entries that were soft-deleted only because their work item was deleted "
        "(the pre-v2 delete cascade). DRY RUN by default: prints counts and changes nothing. "
        "Pass --apply to restore them.\n\n"
        "An entry counts as cascade-deleted when its work item is soft-deleted too and the entry's "
        "deleted_at is within a few seconds after the work item's deleted_at (--tolerance-seconds, "
        "default 300). Entries a user deleted on purpose (work item still alive, or deleted at a "
        "different time), entries of deleted projects/workspaces, and entries already hard-deleted "
        "are never restored. Running timers lost the same way are counted but not restored (their "
        "real end time is unknown).\n\n"
        "--apply restores in one transaction: clears deleted_at, fills the work-item label snapshot, "
        "and writes a time-tracking audit row (system actor) per entry. Safe to re-run: restored "
        "entries no longer match. Use --workspace <slug> to limit the run to one workspace."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="restore the entries (default: dry run, no writes)")
        parser.add_argument("--workspace", type=str, default=None, help="only this workspace (slug)")
        parser.add_argument(
            "--tolerance-seconds",
            type=int,
            default=DEFAULT_TOLERANCE_SECONDS,
            help=(
                "max seconds the worklog's deletion may trail its work item's deletion "
                f"(default {DEFAULT_TOLERANCE_SECONDS})"
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        slug = (options.get("workspace") or "").strip() or None
        tolerance = options["tolerance_seconds"]
        if tolerance < 0:
            raise CommandError("Error: --tolerance-seconds must be 0 or more")
        if slug and not Workspace.objects.filter(slug=slug).exists():
            raise CommandError(f"Error: Workspace with slug {slug} does not exist")

        qs = cascaded_worklogs(tolerance, slug)
        self._report(qs, tolerance, slug, apply)

        if not apply:
            self.stdout.write(self.style.WARNING("DRY RUN: nothing was changed. Re-run with --apply to restore."))
            return

        restored = 0
        with transaction.atomic():
            ids = list(qs.order_by("created_at").values_list("id", flat=True))
            for start in range(0, len(ids), BATCH_SIZE):
                rows = (
                    IssueWorklog.all_objects.select_for_update()
                    .filter(pk__in=ids[start : start + BATCH_SIZE], deleted_at__isnull=False)
                    .order_by("created_at")
                )
                for worklog in rows:
                    restore_worklog(worklog)
                    restored += 1
        self.stdout.write(self.style.SUCCESS(f"Restored {restored} time entries."))

    def _report(self, qs, tolerance, slug, apply):
        scope = f"workspace {slug}" if slug else "all workspaces"
        self.stdout.write(f"Cascade-deleted time entries ({scope}; deleted within {tolerance}s after their work item):")
        rows = (
            qs.values("workspace__slug", "project__identifier")
            .annotate(entries=Count("id"), minutes=Sum("duration"), first=Min("logged_date"), last=Max("logged_date"))
            .order_by("workspace__slug", "project__identifier")
        )
        current = None
        for row in rows:
            if row["workspace__slug"] != current:
                current = row["workspace__slug"]
                self.stdout.write(f"  workspace {current}")
            self.stdout.write(
                f"    project {row['project__identifier']}: {row['entries']} entries, "
                f"{_hours(row['minutes'])} h, {row['first']} .. {row['last']}"
            )
        total = qs.aggregate(
            entries=Count("id"), minutes=Sum("duration"), first=Min("logged_date"), last=Max("logged_date")
        )
        if total["entries"]:
            self.stdout.write(
                f"Total {'to restore' if apply else 'recoverable'}: {total['entries']} entries, "
                f"{_hours(total['minutes'])} h, logged dates {total['first']} .. {total['last']}"
            )
        else:
            self.stdout.write("Total recoverable: 0 entries")

        # Informational: things this command does not (or cannot) restore.
        gone = orphaned_worklogs(slug).count()
        project_gone = cascaded_worklogs(tolerance, slug, project_alive=False).count()
        timers = cascaded_timers(tolerance, slug).count()
        self.stdout.write("Not restored:")
        self.stdout.write(
            f"  {project_gone} entries deleted together with a deleted project/workspace (still removed by design)"
        )
        self.stdout.write(f"  {timers} running timers lost with their work item (end time unknown)")
        self.stdout.write(
            f"  {gone} soft-deleted entries whose work item row is already gone "
            "(cannot be told apart from deliberate deletions; entries removed by the 60-day hard delete "
            "no longer exist and are not detectable)"
        )
