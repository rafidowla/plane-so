# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import JiraImportJobSerializer
from plane.bgtasks.jira_import_task import run_jira_import_task
from plane.db.models import Project, Workspace, JiraImportJob
from plane.utils.jira_importer import (
    JiraConfigError,
    fetch_jira_issues,
    fetch_jira_statuses,
    run_import,
    sample_issues,
)

PREVIEW_LIMIT = 50


def _jira_cfg(data):
    statuses = data.get("statuses")
    return {
        "jira_url": data.get("jira_url"),
        "jira_email": data.get("jira_email"),
        "jira_token": data.get("jira_token"),
        "jira_project": data.get("jira_project"),
        "jql": data.get("jql") or None,
        # Only non-empty strings; empty/missing list means "no status filter".
        "statuses": [s.strip() for s in statuses if isinstance(s, str) and s.strip()]
        if isinstance(statuses, list)
        else [],
    }


class JiraImportStatusesEndpoint(BaseAPIView):
    """List the Jira project's workflow statuses so the user can pick which to import."""

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        cfg = _jira_cfg(request.data)
        if not (cfg["jira_url"] and cfg["jira_email"] and cfg["jira_token"] and cfg["jira_project"]):
            return Response(
                {"error": "jira_url, jira_email, jira_token and jira_project are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            statuses = fetch_jira_statuses(
                jira_url=cfg["jira_url"],
                jira_email=cfg["jira_email"],
                jira_token=cfg["jira_token"],
                jira_project=cfg["jira_project"],
            )
        except JiraConfigError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Could not reach Jira: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"statuses": statuses}, status=status.HTTP_200_OK)


class JiraImportPreviewEndpoint(BaseAPIView):
    """Synchronous dry-run preview: fetch a sample, map, and report — no writes."""

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        project = Project.objects.get(pk=project_id)
        with_worklogs = bool(request.data.get("with_worklogs"))
        with_attachments = bool(request.data.get("with_attachments")) and not request.data.get("sample")
        try:
            if request.data.get("sample"):
                issues = sample_issues()
            else:
                cfg = _jira_cfg(request.data)
                issues = fetch_jira_issues(
                    jira_url=cfg["jira_url"],
                    jira_email=cfg["jira_email"],
                    jira_token=cfg["jira_token"],
                    jira_project=cfg["jira_project"],
                    jql=cfg["jql"],
                    limit=PREVIEW_LIMIT,
                    statuses=cfg["statuses"],
                )
        except JiraConfigError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Could not reach Jira: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        # Dry-run only counts attachments (no download), so jira_auth isn't needed here.
        result = run_import(
            project=project,
            initiator=request.user,
            issues=issues,
            with_worklogs=with_worklogs,
            dry_run=True,
            with_attachments=with_attachments,
        )
        return Response(result, status=status.HTTP_200_OK)


class JiraImportEndpoint(BaseAPIView):
    """Start a background import (POST) and list past jobs (GET)."""

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        jobs = JiraImportJob.objects.filter(workspace__slug=slug, project_id=project_id)
        return Response(JiraImportJobSerializer(jobs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        cfg = _jira_cfg(request.data)
        is_sample = bool(request.data.get("sample"))
        if not is_sample and not (cfg["jira_url"] and cfg["jira_email"] and cfg["jira_token"]):
            return Response(
                {"error": "jira_url, jira_email and jira_token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = Workspace.objects.get(slug=slug)
        with_worklogs = bool(request.data.get("with_worklogs"))
        with_attachments = bool(request.data.get("with_attachments")) and not is_sample

        job = JiraImportJob.objects.create(
            workspace=workspace,
            project_id=project_id,
            initiated_by=request.user,
            with_worklogs=with_worklogs,
            with_attachments=with_attachments,
            # config is non-secret display info only — never store the token.
            config={
                "jira_url": cfg["jira_url"],
                "jira_project": cfg["jira_project"],
                "jql": cfg["jql"],
                "statuses": cfg["statuses"],
                "sample": is_sample,
            },
        )

        if is_sample:
            # Run inline for the offline self-test path.
            from plane.utils.jira_importer import run_import as _run
            from plane.db.models import JiraImportStatus

            issues = sample_issues()
            result = _run(project=job.project, initiator=request.user, issues=issues, with_worklogs=with_worklogs, dry_run=False, preview_limit=0)
            job.status = JiraImportStatus.COMPLETED
            job.result = result
            job.total = result["fetched"]
            job.processed = result["fetched"]
            job.save()
        else:
            run_jira_import_task.delay(
                str(job.id),
                cfg["jira_url"],
                cfg["jira_email"],
                cfg["jira_token"],
                cfg["jira_project"],
                cfg["jql"],
                with_worklogs,
                with_attachments,
                cfg["statuses"],
            )

        return Response(JiraImportJobSerializer(job).data, status=status.HTTP_201_CREATED)


class JiraImportJobDetailEndpoint(BaseAPIView):
    """Poll a single import job's status/progress/result."""

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id, pk):
        job = JiraImportJob.objects.filter(workspace__slug=slug, project_id=project_id, pk=pk).first()
        if not job:
            return Response({"error": "Import job not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(JiraImportJobSerializer(job).data, status=status.HTTP_200_OK)
