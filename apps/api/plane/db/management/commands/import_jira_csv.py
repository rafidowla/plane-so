# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Import a Jira CSV export into a Plane project.

Example (dry-run):
  python manage.py import_jira_csv --slug demo --project DEMO \
      --initiator admin@plane.test --file /tmp/export.csv --with-worklogs

  # Execute:
  python manage.py import_jira_csv ... --execute
"""

from django.core.management.base import BaseCommand, CommandError

from plane.db.models import Project, ProjectMember, User
from plane.utils.jira_csv_importer import parse_jira_csv, run_csv_import


class Command(BaseCommand):
    help = "Import issues (and worklogs) from a Jira CSV export into a Plane project"

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--project", required=True, help="Plane project id or identifier")
        parser.add_argument("--initiator", required=True, help="email of the Plane user to own imported records")
        parser.add_argument("--file", required=True, help="path to the Jira CSV export")
        parser.add_argument("--with-worklogs", action="store_true", default=False)
        parser.add_argument("--execute", action="store_true", default=False, help="write to DB (default is dry-run)")

    def handle(self, *args, **options):
        dry = not options["execute"]
        project = (
            Project.objects.filter(workspace__slug=options["slug"], identifier=options["project"]).first()
            or Project.objects.filter(workspace__slug=options["slug"], pk=options["project"]).first()
        )
        if not project:
            raise CommandError(f"Project '{options['project']}' not found in workspace '{options['slug']}'")
        initiator = User.objects.filter(email=options["initiator"]).first()
        if not initiator:
            raise CommandError(f"Initiator user '{options['initiator']}' not found")
        if not ProjectMember.objects.filter(project=project, member=initiator, is_active=True).exists():
            raise CommandError("Initiator must be a member of the project")

        try:
            with open(options["file"], newline="", encoding="utf-8-sig") as fp:
                issues, accountid_to_name = parse_jira_csv(fp)
        except FileNotFoundError:
            raise CommandError(f"CSV file not found: {options['file']}")

        self.stdout.write(self.style.MIGRATE_HEADING(f"{'DRY-RUN' if dry else 'IMPORT'}: Jira CSV -> {project.name}"))
        self.stdout.write(f"Parsed {len(issues)} issue(s) from CSV ({len(accountid_to_name)} distinct Jira users)\n")

        result = run_csv_import(
            project=project,
            initiator=initiator,
            issues=issues,
            accountid_to_name=accountid_to_name,
            with_worklogs=options["with_worklogs"],
            dry_run=dry,
            preview_limit=10**6,
        )
        for row in result["preview"][:40]:
            self.stdout.write(
                f"  + {row['key']} | {row['summary'][:44]!r} | state={row['state'] or '—'} | "
                f"prio={row['priority']} | assignee={row['assignee'] or '—'} | "
                f"labels={row['labels']} comments={row['comments']} worklogs={row['worklogs']}"
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{'Would create' if dry else 'Created'}: {result['created']} issue(s)"))
        self.stdout.write(f"Skipped (already imported): {result['skipped']}")
        self.stdout.write(f"Comments: {result['comments']} | Worklogs: {result['worklogs']} | Parents linked: {result['parents_linked']}")
        if result["unmapped_states"]:
            self.stdout.write(self.style.WARNING(f"Unmapped statuses (used default): {result['unmapped_states']}"))
        if result["unmapped_users"]:
            self.stdout.write(
                self.style.WARNING(f"Unmapped users ({len(result['unmapped_users'])}) - not matched by name to a member")
            )
        if dry:
            self.stdout.write(self.style.NOTICE("\nDry-run only. Re-run with --execute to write these records."))
