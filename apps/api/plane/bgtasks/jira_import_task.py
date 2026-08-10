# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import JiraImportJob, JiraImportStatus
from plane.utils.jira_importer import fetch_jira_issues, run_import


@shared_task
def run_jira_import_task(
    job_id, jira_url, jira_email, jira_token, jira_project, jql, with_worklogs, with_attachments=False, statuses=None
):
    """Run a self-service Jira import in the background and track progress.

    The Jira token is received as an argument only and is never persisted.
    """
    job = JiraImportJob.objects.filter(id=job_id).first()
    if not job:
        return

    JiraImportJob.objects.filter(id=job_id).update(status=JiraImportStatus.PROCESSING)
    try:
        issues = fetch_jira_issues(
            jira_url=jira_url,
            jira_email=jira_email,
            jira_token=jira_token,
            jira_project=jira_project,
            jql=jql,
            statuses=statuses,
        )
        JiraImportJob.objects.filter(id=job_id).update(total=len(issues))

        def progress(processed, total):
            JiraImportJob.objects.filter(id=job_id).update(processed=processed, total=total)

        result = run_import(
            project=job.project,
            initiator=job.initiated_by,
            issues=issues,
            with_worklogs=with_worklogs,
            dry_run=False,
            progress=progress,
            preview_limit=0,
            with_attachments=with_attachments,
            jira_auth=(jira_email, jira_token) if with_attachments else None,
            jira_url=jira_url if with_attachments else None,
        )
        JiraImportJob.objects.filter(id=job_id).update(
            status=JiraImportStatus.COMPLETED,
            result=result,
            total=result["fetched"],
            processed=result["fetched"],
        )
    except Exception as e:
        JiraImportJob.objects.filter(id=job_id).update(status=JiraImportStatus.FAILED, error=str(e)[:1000])
