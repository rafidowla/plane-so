# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 S2 — who may see which project's content in time views.

The content-leak rule (DESIGN §2 item 27): a work item's identifier, title and
project name are shown only to a viewer who is an ACTIVE Admin/Member (role >= 15)
of that item's project. Workspace role does not widen this — a workspace admin
who is not in a project sees that project's time as "private project" /
"Other projects" like anyone else. Guests (role 5) never count.
"""

# Django imports
from django.db.models import Exists, OuterRef

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import ProjectMember

PRIVATE_LABEL = "private project"
OTHER_PROJECTS_LABEL = "Other projects"


def content_member_projects(user_id):
    """Queryset of project ids whose content ``user_id`` may see."""
    return ProjectMember.objects.filter(member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value).values(
        "project_id"
    )


def can_see_project(user_id, project_ref="project_id"):
    """An ``Exists`` annotation: the viewer may see the content of ``OuterRef(project_ref)``."""
    return Exists(
        ProjectMember.objects.filter(
            project_id=OuterRef(project_ref), member_id=user_id, is_active=True, role__gte=ROLE.MEMBER.value
        )
    )
