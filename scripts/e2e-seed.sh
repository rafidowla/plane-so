#!/usr/bin/env bash
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
#
# FORK: e2e — idempotent seed for the Playwright suite. Creates (or refreshes)
# two QA users and a dedicated "e2e" workspace + time-tracking project in the
# LOCAL docker stack (docker-compose-local.yml). Never touches real data.
#
# Usage:  ./scripts/e2e-seed.sh
# Env:    E2E_PASSWORD (default QaTest#2026)

set -euo pipefail
cd "$(dirname "$0")/.."

E2E_PASSWORD="${E2E_PASSWORD:-QaTest#2026}"
export E2E_PASSWORD

docker compose -f docker-compose-local.yml exec -T -e E2E_PASSWORD api python manage.py shell <<'PY'
import os
from django.contrib.auth.hashers import make_password
from plane.db.models import (
    User, Workspace, WorkspaceMember, Project, ProjectMember,
    ProjectIdentifier, State, Profile,
)
from plane.properties.models import (
    IssueProperty, IssuePropertyOption, ProjectPropertiesFeature,
)

PASSWORD = make_password(os.environ["E2E_PASSWORD"])

def ensure_user(email):
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"username": email.split("@")[0], "first_name": "E2E"},
    )
    user.is_active = True
    user.password = PASSWORD
    user.save()
    Profile.objects.get_or_create(user=user)
    Profile.objects.filter(user=user).update(is_onboarded=True)
    return user

admin = ensure_user("qa-visual@plane.test")
outsider = ensure_user("uat-outsider@example.com")
# FORK: time-tracking-v2 — a plain workspace member who belongs to the E2E project, and a
# second member who belongs only to the SECRET project (live-dashboard redaction specs).
member = ensure_user("qa-member@plane.test")
other = ensure_user("qa-other@plane.test")
# FORK: time-tracking-v2 S6 — a project guest (no estimate controls)
guest = ensure_user("qa-guest@plane.test")

ws, _ = Workspace.objects.get_or_create(
    slug="e2e",
    defaults={"name": "E2E", "owner": admin},
)
WorkspaceMember.objects.get_or_create(workspace=ws, member=admin, defaults={"role": 20, "is_active": True})
# FORK: PSR-125 -- the e2e admin owns the workspace, so it is its Super Admin (Time settings are
# Super-Admin-only); the 0136 backfill ran before this seed on a fresh DB.
WorkspaceMember.objects.filter(workspace=ws, member=admin).update(is_super_admin=True)
WorkspaceMember.objects.get_or_create(workspace=ws, member=outsider, defaults={"role": 15, "is_active": True})
WorkspaceMember.objects.get_or_create(workspace=ws, member=member, defaults={"role": 15, "is_active": True})
WorkspaceMember.objects.get_or_create(workspace=ws, member=other, defaults={"role": 15, "is_active": True})
WorkspaceMember.objects.get_or_create(workspace=ws, member=guest, defaults={"role": 5, "is_active": True})

project, _ = Project.objects.get_or_create(
    workspace=ws,
    identifier="E2E",
    defaults={"name": "E2E Auto", "created_by": admin},
)
project.is_time_tracking_enabled = True
project.save()
ProjectIdentifier.objects.get_or_create(workspace=ws, project=project, name="E2E")
ProjectMember.objects.get_or_create(
    project=project, workspace=ws, member=admin,
    defaults={"role": 20, "is_active": True},
)
ProjectMember.objects.get_or_create(
    project=project, workspace=ws, member=member,
    defaults={"role": 15, "is_active": True},
)
# The outsider is deliberately NOT a project member.
State.objects.get_or_create(
    workspace=ws, project=project, name="Todo",
    defaults={"group": "unstarted", "default": True, "created_by": admin},
)
# FORK: time-tracking-v2 S6 — a started state (estimates lock when work starts) and the guest's membership
State.objects.get_or_create(
    workspace=ws, project=project, name="In Progress",
    defaults={"group": "started", "created_by": admin},
)
ProjectMember.objects.get_or_create(
    project=project, workspace=ws, member=guest,
    defaults={"role": 5, "is_active": True},
)

# FORK: time-tracking-v2 — a second tracked project that only qa-other belongs to.
secret, _ = Project.objects.get_or_create(
    workspace=ws,
    identifier="SEC",
    defaults={"name": "E2E Secret", "created_by": other},
)
secret.is_time_tracking_enabled = True
secret.save()
ProjectIdentifier.objects.get_or_create(workspace=ws, project=secret, name="SEC")
ProjectMember.objects.get_or_create(
    project=secret, workspace=ws, member=other,
    defaults={"role": 20, "is_active": True},
)
State.objects.get_or_create(
    workspace=ws, project=secret,
    defaults={"name": "Todo", "group": "unstarted", "default": True, "created_by": other},
)

# Custom properties on for this project + one OPTION property with a "Bug"
# option, so the PSR-60 chip-placeholder spec has real data to render.
feature, _ = ProjectPropertiesFeature.objects.get_or_create(
    project=project, defaults={"workspace": ws, "is_enabled": True},
)
feature.is_enabled = True
feature.save()
task_type, _ = IssueProperty.objects.get_or_create(
    project=project, name="task-type",
    defaults={
        "workspace": ws, "display_name": "Task Type", "description": "",
        "property_type": "OPTION", "is_required": False, "default_value": {},
        "settings": {}, "is_active": True, "is_multi": False,
        "validation_rules": {}, "logo_props": {}, "sort_order": 1,
    },
)
IssuePropertyOption.objects.get_or_create(
    property=task_type, name="Bug",
    defaults={
        "workspace": ws, "project": project, "description": "",
        "logo_props": {}, "sort_order": 1, "is_active": True, "is_default": False,
    },
)
IssuePropertyOption.objects.get_or_create(
    property=task_type, name="Chore",
    defaults={
        "workspace": ws, "project": project, "description": "",
        "logo_props": {}, "sort_order": 2, "is_active": True, "is_default": False,
    },
)

print("e2e seed ok: users + workspace 'e2e' + projects 'E2E', 'SEC' (time tracking on)")
PY
