# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fixtures for the PQL engine tests (FORK: PSR-85).

`world` builds two workspaces. The main one ("pql-ws") has projects WEB and API
with issues A-F wired to states, assignees, labels, cycles, modules, mentions,
subscribers, intake and relations, including soft-deleted through-rows that must
never match. The other one ("pql-other") holds look-alike data used to prove
there is no cross-workspace leakage.
"""

from datetime import datetime, time, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.utils import timezone

from plane.db.models import (
    Cycle,
    CycleIssue,
    Intake,
    IntakeIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueMention,
    IssueRelation,
    IssueSubscriber,
    Label,
    Module,
    ModuleIssue,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)


def _user(handle: str) -> User:
    return User.objects.create(email=f"{handle}@pql.test", username=f"pql_{handle}", first_name=handle)


def _noon(d) -> datetime:
    return datetime.combine(d, time(12), tzinfo=dt_timezone.utc)


@pytest.fixture
def world(db):
    today = timezone.now().astimezone(dt_timezone.utc).date()
    owner, u2, u3, stranger = _user("owner"), _user("u2"), _user("u3"), _user("stranger")

    ws = Workspace.objects.create(name="PQL", slug="pql-ws", owner=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=20)
    WorkspaceMember.objects.create(workspace=ws, member=u2, role=15)
    WorkspaceMember.objects.create(workspace=ws, member=u3, role=15, is_active=False)

    web = Project.objects.create(name="Web", identifier="WEB", workspace=ws, created_by=owner)
    api = Project.objects.create(name="Api", identifier="API", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=web, member=owner, role=20)
    ProjectMember.objects.create(project=web, member=u2, role=15, is_active=False)

    backlog = State.objects.create(name="Backlog", project=web, group="backlog", default=True)
    started = State.objects.create(name="Doing", project=web, group="started")
    done = State.objects.create(name="Done", project=web, group="completed")
    api_backlog = State.objects.create(name="Backlog", project=api, group="backlog", default=True)

    def issue(name, project, state, **kwargs):
        return Issue.objects.create(name=name, project=project, workspace=ws, state=state, created_by=owner, **kwargs)

    a = issue(
        "Alpha capex launch",
        web,
        started,
        priority="urgent",
        target_date=today - timedelta(days=3),
        start_date=today - timedelta(days=10),
        description_html="<p>budget spreadsheet</p>",
    )
    b = issue("Beta report", web, backlog, priority="high", target_date=today + timedelta(days=5), parent=a)
    c = issue("Gamma cleanup", web, done, priority="low", target_date=today - timedelta(days=3))
    d = issue("Delta draft", web, backlog, priority="none", is_draft=True)
    e = issue("Epsilon archived", web, done, priority="medium", archived_at=today)
    f = issue("Zeta api", api, api_backlog, priority="medium")
    # BaseModel.save() takes created_by from the request user (none in tests), so set it here.
    Issue.objects.filter(workspace=ws).update(created_by=owner)
    Issue.objects.filter(pk=b.pk).update(created_by=u2)
    Issue.objects.filter(pk=a.pk).update(created_at=_noon(today - timedelta(days=10)))

    def soft_delete(obj):
        type(obj).all_objects.filter(pk=obj.pk).update(deleted_at=timezone.now())

    # Assignees: A owner+u2, B u2, C u2 (soft-deleted), D u3 (inactive member), F owner.
    for iss, user in ((a, owner), (a, u2), (b, u2), (d, u3), (f, owner)):
        IssueAssignee.objects.create(issue=iss, assignee=user, project=iss.project)
    soft_delete(IssueAssignee.objects.create(issue=c, assignee=u2, project=web))

    bug = Label.objects.create(name="bug", workspace=ws, project=web)
    feature = Label.objects.create(name="feature", workspace=ws, project=web)
    IssueLabel.objects.create(issue=a, label=bug, project=web)
    IssueLabel.objects.create(issue=b, label=feature, project=web)
    soft_delete(IssueLabel.objects.create(issue=e, label=bug, project=web))

    now = timezone.now()
    active = Cycle.objects.create(
        name="Active", project=web, owned_by=owner, start_date=now - timedelta(days=2), end_date=now + timedelta(days=2)
    )
    past = Cycle.objects.create(
        name="Past", project=web, owned_by=owner, start_date=now - timedelta(days=20), end_date=now - timedelta(days=10)
    )
    future = Cycle.objects.create(
        name="Future", project=web, owned_by=owner, start_date=now + timedelta(days=5), end_date=now + timedelta(days=9)
    )
    CycleIssue.objects.create(cycle=active, issue=a, project=web)
    CycleIssue.objects.create(cycle=past, issue=b, project=web)
    CycleIssue.objects.create(cycle=future, issue=c, project=web)

    m1 = Module.objects.create(name="M1", project=web)
    ModuleIssue.objects.create(module=m1, issue=a, project=web)

    IssueMention.objects.create(issue=a, mention=u2, project=web)
    IssueSubscriber.objects.create(issue=b, subscriber=owner, project=web)

    intake = Intake.objects.create(name="Intake", project=web)
    IntakeIssue.objects.create(intake=intake, issue=c, project=web)

    # B is blocked by A; A relates to C; D duplicates C; F->A blocked_by is soft-deleted.
    IssueRelation.objects.create(issue=b, related_issue=a, relation_type="blocked_by", project=web)
    IssueRelation.objects.create(issue=a, related_issue=c, relation_type="relates_to", project=web)
    IssueRelation.objects.create(issue=d, related_issue=c, relation_type="duplicate", project=web)
    soft_delete(IssueRelation.objects.create(issue=f, related_issue=a, relation_type="blocked_by", project=api))

    # Other workspace with look-alike data.
    ows = Workspace.objects.create(name="Other", slug="pql-other", owner=stranger)
    WorkspaceMember.objects.create(workspace=ows, member=stranger, role=20)
    oth = Project.objects.create(name="Other", identifier="OTH", workspace=ows, created_by=stranger)
    oth_state = State.objects.create(name="Doing", project=oth, group="started", default=True)
    x = Issue.objects.create(
        name="Other secret capex",
        project=oth,
        workspace=ows,
        state=oth_state,
        priority="urgent",
        created_by=stranger,
        target_date=today - timedelta(days=3),
    )
    x_child = Issue.objects.create(
        name="Other child", project=oth, workspace=ows, state=oth_state, created_by=stranger, parent=x
    )
    oth_label = Label.objects.create(name="bug", workspace=ows, project=oth)
    IssueLabel.objects.create(issue=x, label=oth_label, project=oth)
    IssueAssignee.objects.create(issue=x, assignee=stranger, project=oth)
    oth_cycle = Cycle.objects.create(
        name="Active", project=oth, owned_by=stranger, start_date=now - timedelta(days=1), end_date=now + timedelta(1)
    )
    CycleIssue.objects.create(cycle=oth_cycle, issue=x, project=oth)
    IssueRelation.objects.create(issue=x_child, related_issue=x, relation_type="blocked_by", project=oth)

    for obj in (a, b, c, d, e, f, x):
        obj.refresh_from_db()

    return SimpleNamespace(
        today=today,
        owner=owner,
        u2=u2,
        u3=u3,
        stranger=stranger,
        ws=ws,
        web=web,
        api=api,
        backlog=backlog,
        started=started,
        done=done,
        bug=bug,
        feature=feature,
        active=active,
        past=past,
        future=future,
        m1=m1,
        a=a,
        b=b,
        c=c,
        d=d,
        e=e,
        f=f,
        ows=ows,
        oth=oth,
        oth_state=oth_state,
        oth_label=oth_label,
        oth_cycle=oth_cycle,
        x=x,
    )
