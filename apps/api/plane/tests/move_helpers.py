# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared builders for the move-work-item (PSR-91) / draft-project-change (PSR-92) tests.

Plain functions (not fixtures) so contract, API-key and unit tests can share
them. Every builder passes ``created_by_id`` explicitly because
``BaseModel.save`` otherwise nulls the audit users when no request is active.
"""

import uuid
from types import SimpleNamespace

from rest_framework.test import APIClient

from plane.db.models import (
    Estimate,
    EstimatePoint,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueSubscriber,
    Label,
    Project,
    ProjectIdentifier,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)

ADMIN, MEMBER, GUEST = 20, 15, 5


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_user(prefix="user"):
    uid = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{uid}@plane.so",
        username=f"{prefix}_{uid}",
        first_name=prefix.title(),
        last_name="Tester",
    )
    return user


def add_workspace_member(workspace, user, role=MEMBER):
    return WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=True)


def add_project_member(project, user, role=MEMBER, is_active=True):
    return ProjectMember.objects.create(
        project=project, workspace=project.workspace, member=user, role=role, is_active=is_active
    )


def make_project(workspace, identifier, creator, **fields):
    project = Project(workspace=workspace, name=f"Project {identifier}", identifier=identifier, **fields)
    project.save(created_by_id=creator.id)
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name=project.identifier)
    return project


def make_state(project, name, group, default=False, **fields):
    return State.objects.create(
        project=project, workspace=project.workspace, name=name, group=group, default=default, color="#888888", **fields
    )


def make_label(project, name):
    return Label.objects.create(project=project, workspace=project.workspace, name=name, color="#123456")


def make_issue(project, name, state=None, parent=None, creator=None, **fields):
    issue = Issue(workspace=project.workspace, project=project, name=name, state=state, parent=parent, **fields)
    issue.save(created_by_id=creator.id if creator else None)
    return issue


def assign(issue, user):
    return IssueAssignee.objects.create(issue=issue, assignee=user, project=issue.project)


def label_issue(issue, label):
    return IssueLabel.objects.create(issue=issue, label=label, project=issue.project)


def subscribe(issue, user):
    return IssueSubscriber.objects.create(issue=issue, subscriber=user, project=issue.project)


def make_estimate(project, est_type, values, name="Estimate"):
    """Create an estimate with the given point values and make it the project's active one."""
    estimate = Estimate.objects.create(project=project, workspace=project.workspace, name=name, type=est_type)
    points = {
        value: EstimatePoint.objects.create(
            estimate=estimate, project=project, workspace=project.workspace, key=i, value=value
        )
        for i, value in enumerate(values)
    }
    Project.objects.filter(pk=project.pk).update(estimate=estimate)
    project.refresh_from_db()
    return estimate, points


def build_world(workspace, admin):
    """Two projects (SRC, TGT) in ``workspace`` with deliberately different configs.

    State mapping (source -> target):
        Backlog   -> BACKLOG      (name, case-insensitive)
        Todo      -> Ready        (group: unstarted)
        Doing     -> In Progress  (group: started; a triage state named "Doing" in the target is ignored)
        Review    -> In Progress  (group: started)
        Done      -> Complete     (group: completed)
        Cancelled -> Ready        (no cancelled group in target -> target default)

    ``mover`` is a plain workspace member (role 15) and project MEMBER in both,
    so it gets none of the workspace-admin bypass that ``admin`` does.
    """
    src = make_project(workspace, "SRC", admin)
    tgt = make_project(workspace, "TGT", admin)
    add_project_member(src, admin, ADMIN)
    add_project_member(tgt, admin, ADMIN)

    mover = make_user("mover")
    add_workspace_member(workspace, mover, MEMBER)
    add_project_member(src, mover, MEMBER)
    add_project_member(tgt, mover, MEMBER)

    s = SimpleNamespace(
        backlog=make_state(src, "Backlog", "backlog", default=True),
        todo=make_state(src, "Todo", "unstarted"),
        doing=make_state(src, "Doing", "started"),
        review=make_state(src, "Review", "started"),
        done=make_state(src, "Done", "completed"),
        cancelled=make_state(src, "Cancelled", "cancelled"),
    )
    t = SimpleNamespace(
        backlog=make_state(tgt, "BACKLOG", "backlog"),
        ready=make_state(tgt, "Ready", "unstarted", default=True),
        progress=make_state(tgt, "In Progress", "started"),
        complete=make_state(tgt, "Complete", "completed"),
        triage=make_state(tgt, "Doing", "triage", is_triage=True),
    )

    src_labels = SimpleNamespace(
        bug=make_label(src, "bug"),
        bug_upper=make_label(src, "Bug"),
        feature=make_label(src, "feature"),
        docs=make_label(src, "docs"),
    )
    tgt_labels = SimpleNamespace(bug=make_label(tgt, "BUG"), docs=make_label(tgt, "Docs"))

    def member_of(prefix, src_role, tgt_role):
        user = make_user(prefix)
        add_workspace_member(workspace, user, MEMBER)
        if src_role:
            add_project_member(src, user, src_role)
        if tgt_role:
            add_project_member(tgt, user, tgt_role)
        return user

    users = SimpleNamespace(
        mover=mover,
        admin=admin,
        tgt_member=member_of("tgtmember", MEMBER, MEMBER),
        tgt_admin=member_of("tgtadmin", MEMBER, ADMIN),
        tgt_guest=member_of("tgtguest", MEMBER, GUEST),
        not_in_tgt=member_of("nottgt", MEMBER, None),
        tgt_only=member_of("tgtonly", None, MEMBER),
    )
    inactive = make_user("inactive")
    add_workspace_member(workspace, inactive, MEMBER)
    add_project_member(src, inactive, MEMBER)
    add_project_member(tgt, inactive, MEMBER, is_active=False)
    users.tgt_inactive = inactive

    return SimpleNamespace(
        workspace=workspace,
        slug=workspace.slug,
        src=src,
        tgt=tgt,
        s=s,
        t=t,
        src_labels=src_labels,
        tgt_labels=tgt_labels,
        users=users,
        mover=mover,
        admin=admin,
    )


def move_url(world_or_slug, project_id, issue_id):
    slug = getattr(world_or_slug, "slug", world_or_slug)
    return f"/api/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/move/"


def preview_url(world_or_slug, project_id, issue_id):
    return move_url(world_or_slug, project_id, issue_id) + "preview/"
