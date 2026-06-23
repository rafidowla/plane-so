# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Import issues, comments and worklogs from Jira into a Plane project.

Self-hosted friendly: talks to Jira over REST and writes straight through the
ORM (so it can populate the time-tracking tables too). Defaults to a dry-run.
The actual work lives in plane.utils.jira_importer (shared with the API).

Examples:
  # Dry-run against a real Jira project (no writes):
  python manage.py import_jira --slug demo --project DEMO \
      --initiator admin@plane.test \
      --jira-url https://acme.atlassian.net --jira-email you@acme.com \
      --jira-token <token> --jira-project ENG --with-worklogs

  # Execute the import:
  python manage.py import_jira ... --execute

  # Offline self-test with bundled sample data (no Jira account needed):
  python manage.py import_jira --slug demo --project DEMO \
      --initiator admin@plane.test --sample --with-worklogs --execute
"""

import os

from django.core.management.base import BaseCommand, CommandError

from plane.db.models import User, Project, ProjectMember
from plane.utils.jira_importer import fetch_jira_issues, run_import, sample_issues


def _opt(options, key, env):
    return options.get(key) or os.environ.get(env)


class Command(BaseCommand):
    help = "Import issues (and optionally worklogs) from Jira into a Plane project"

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="Plane workspace slug")
        parser.add_argument("--project", required=True, help="Plane project id or identifier")
        parser.add_argument("--initiator", required=True, help="email of the Plane user to own imported records")
        parser.add_argument("--jira-url", default=os.environ.get("JIRA_BASE_URL"))
        parser.add_argument("--jira-email", default=os.environ.get("JIRA_EMAIL"))
        parser.add_argument("--jira-token", default=os.environ.get("JIRA_API_TOKEN"))
        parser.add_argument("--jira-project", default=os.environ.get("JIRA_PROJECT_KEY"))
        parser.add_argument("--jql", default=None)
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--with-worklogs", action="store_true", default=False)
        parser.add_argument("--sample", action="store_true", default=False, help="use bundled sample data, skip Jira")
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

        if options["sample"]:
            issues = sample_issues()
        else:
            issues = fetch_jira_issues(
                jira_url=_opt(options, "jira_url", "JIRA_BASE_URL"),
                jira_email=_opt(options, "jira_email", "JIRA_EMAIL"),
                jira_token=_opt(options, "jira_token", "JIRA_API_TOKEN"),
                jira_project=_opt(options, "jira_project", "JIRA_PROJECT_KEY"),
                jql=options["jql"],
                limit=options["limit"],
            )

        self.stdout.write(self.style.MIGRATE_HEADING(f"{'DRY-RUN' if dry else 'IMPORT'}: Jira -> {project.name}"))
        self.stdout.write(f"Fetched {len(issues)} Jira issue(s)\n")
        result = run_import(
            project=project,
            initiator=initiator,
            issues=issues,
            with_worklogs=options["with_worklogs"],
            dry_run=dry,
            preview_limit=10**6,
        )
        for row in result["preview"]:
            self.stdout.write(
                f"  + {row['key']} | {row['summary'][:48]!r} | state={row['state'] or '—'} | "
                f"priority={row['priority']} | assignee={row['assignee'] or '—'} | "
                f"labels={row['labels']} | comments={row['comments']} | worklogs={row['worklogs']}"
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{'Would create' if dry else 'Created'}: {result['created']} issue(s)"))
        self.stdout.write(f"Skipped (already imported): {result['skipped']}")
        self.stdout.write(f"Comments: {result['comments']} | Worklogs: {result['worklogs']}")
        if result["unmapped_states"]:
            self.stdout.write(self.style.WARNING(f"Unmapped statuses (used default): {result['unmapped_states']}"))
        if result["unmapped_users"]:
            self.stdout.write(self.style.WARNING(f"Unmapped users (assigned to initiator): {result['unmapped_users']}"))
        if dry:
            self.stdout.write(self.style.NOTICE("\nDry-run only. Re-run with --execute to write these records."))
