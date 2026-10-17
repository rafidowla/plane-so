# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project guests raise and edit their own work items through the public ``/api/v1/`` API (FORK: PSR-119).

Rules under test (identical in the app API, see ``tests/contract/app/test_guest_work_item_write_app.py``):

* an active project Guest can create work items (no project toggle), sending only the allow-listed fields
  (name, description*, priority, labels); the state is always the project default;
* any other field with a non-empty value is a 400 naming the field(s); empty values are tolerated;
* a Guest can edit only work items they created, allowed fields only (not-own -> 403);
* a Guest can never delete (not even their own);
* ``created_by`` / ``created_at`` from the body are honoured only for a workspace Admin;
* the legacy ``/relations/`` GET only returns related work items the caller may see.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.issue_activities_task import issue_activity as real_issue_activity
from plane.db.models import (
    APIToken,
    Issue,
    IssueActivity,
    IssueRelation,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Keep celery tasks out of the way; the activity tests re-enable the real activity task explicitly."""
    for target in ("plane.api.views.issue.issue_activity", "plane.api.views.issue.model_activity"):
        monkeypatch.setattr(target, MagicMock())


def _user(name, workspace, ws_role):
    user = User.objects.create(email=f"{name}@plane.so", username=f"{name}-user")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role)
    return user


def _client(user):
    token = f"token-{user.username}"
    APIToken.objects.create(user=user, label=token, token=token)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token)
    return client


def _issue(name, project, creator):
    state = State.objects.filter(project=project, default=True).first()
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=creator)
    issue.refresh_from_db()
    return issue


@pytest.fixture
def world(db, workspace, create_user):
    """GA project (guest_view_all_features=False): admin (= workspace admin), member, guest, guest2.

    ``other`` is a second project the guest does not belong to (it holds a label the guest must not be able
    to use). States: ``backlog`` (default) and ``started``.
    """
    admin = create_user
    member = _user("member", workspace, 15)
    guest = _user("guest", workspace, 5)
    guest2 = _user("guest2", workspace, 5)

    def project(name, identifier, members):
        p = Project.objects.create(name=name, identifier=identifier, workspace=workspace, created_by=admin, network=0)
        for user, role in members:
            ProjectMember.objects.create(project=p, member=user, role=role, is_active=True)
        return p

    ga = project("GA", "GA", [(admin, 20), (member, 15), (guest, 5), (guest2, 5)])
    other = project("Other", "OT", [(admin, 20), (member, 15)])
    backlog = State.objects.create(name="Backlog", group="backlog", default=True, project=ga, workspace=workspace)
    started = State.objects.create(name="Doing", group="started", project=ga, workspace=workspace)
    label = Label.objects.create(name="bug", project=ga, workspace=workspace)
    other_label = Label.objects.create(name="foreign", project=other, workspace=workspace)

    class World:
        pass

    w = World()
    w.ws, w.ga, w.other = workspace, ga, other
    w.admin, w.member, w.guest, w.guest2 = admin, member, guest, guest2
    w.backlog, w.started, w.label, w.other_label = backlog, started, label, other_label
    w.i_admin = _issue("by admin", ga, admin)
    w.i_guest = _issue("by guest", ga, guest)
    w.i_guest2 = _issue("by guest2", ga, guest2)
    w.c = {
        name: _client(user)
        for name, user in (("admin", admin), ("member", member), ("guest", guest), ("guest2", guest2))
    }
    return w


def issues_url(w, issue=None, project=None):
    url = f"/api/v1/workspaces/{w.ws.slug}/projects/{(project or w.ga).id}/issues/"
    return f"{url}{issue.id}/" if issue else url


def items_url(w, issue):
    return f"/api/v1/workspaces/{w.ws.slug}/projects/{w.ga.id}/work-items/{issue.id}/"


# Non-empty values for every field a guest must NOT be able to send.
def forbidden_payloads(w):
    return {
        "state": str(w.started.id),
        "assignees": [str(w.member.id)],
        "parent": str(w.i_admin.id),
        "estimate_point": "1",
        "start_date": "2030-01-01",
        "target_date": "2030-02-01",
        "external_id": "ext-1",
        "external_source": "jira",
        "created_by": str(w.admin.id),
        "created_at": "2020-01-01T00:00:00Z",
        "archived_at": "2030-01-01T00:00:00Z",
        "is_draft": True,
        "type_id": str(uuid4()),
        "completed_at": "2030-01-01T00:00:00Z",
    }


FORBIDDEN_FIELDS = sorted(
    [
        "state",
        "assignees",
        "parent",
        "estimate_point",
        "start_date",
        "target_date",
        "external_id",
        "external_source",
        "created_by",
        "created_at",
        "archived_at",
        "is_draft",
        "type_id",
        "completed_at",
    ]
)


# --------------------------------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestCreate:
    def test_allowed_fields_create_the_item_in_the_default_state(self, world):
        r = world.c["guest"].post(
            issues_url(world),
            {
                "name": "Broken button",
                "description_html": "<p>It does not click</p>",
                "priority": "high",
                "labels": [str(world.label.id)],
            },
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.created_by_id == world.guest.id
        assert issue.state_id == world.backlog.id
        assert issue.priority == "high"
        assert "<p>It does not click</p>" in issue.description_html
        assert list(issue.labels.values_list("id", flat=True)) == [world.label.id]

    @pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
    def test_each_disallowed_field_is_a_400_naming_it(self, world, field):
        body = {"name": "x", field: forbidden_payloads(world)[field]}
        r = world.c["guest"].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert r.data["disallowed_fields"] == [field]
        assert field in r.data["error"]
        assert not Issue.objects.filter(name="x").exists()

    def test_several_disallowed_fields_are_all_named_sorted(self, world):
        body = {
            "name": "x",
            "state": str(world.started.id),
            "assignees": [str(world.member.id)],
            "start_date": "2030-01-01",
        }
        r = world.c["guest"].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert r.data["disallowed_fields"] == ["assignees", "start_date", "state"]

    def test_empty_values_for_team_only_fields_are_tolerated(self, world):
        body = {
            "name": "Form submit",
            "assignees": [],
            "state": None,
            "parent": None,
            "estimate_point": None,
            "start_date": None,
            "external_id": "",
            "is_draft": False,
            "labels": [],
        }
        r = world.c["guest"].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.state_id == world.backlog.id
        assert issue.parent_id is None and not issue.assignees.exists()

    def test_label_from_another_project_is_rejected(self, world):
        r = world.c["guest"].post(
            issues_url(world), {"name": "x", "labels": [str(world.other_label.id)]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "labels" in r.data
        assert not Issue.objects.filter(name="x").exists()

    def test_unknown_label_is_rejected(self, world):
        r = world.c["guest"].post(issues_url(world), {"name": "x", "labels": [str(uuid4())]}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_guest_cannot_create_labels(self, world):
        url = f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/labels/"
        r = world.c["guest"].post(url, {"name": "mine"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert not Label.objects.filter(name="mine").exists()

    def test_guest_cannot_create_in_a_project_they_are_not_in(self, world):
        r = world.c["guest"].post(issues_url(world, project=world.other), {"name": "x"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_create_works_whatever_guest_view_all_features_says(self, world):
        for value in (True, False):
            Project.objects.filter(id=world.ga.id).update(guest_view_all_features=value)
            r = world.c["guest"].post(issues_url(world), {"name": f"gv-{value}"}, format="json")
            assert r.status_code == status.HTTP_201_CREATED, r.data

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_member_and_admin_keep_full_field_access(self, world, who):
        body = {
            "name": f"by {who}",
            "state": str(world.started.id),
            "assignees": [str(world.member.id)],
            "start_date": "2030-01-01",
            "external_id": f"e-{who}",
            "external_source": "jira",
        }
        r = world.c[who].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.state_id == world.started.id
        assert list(issue.assignees.values_list("id", flat=True)) == [world.member.id]
        assert str(issue.start_date) == "2030-01-01"


# --------------------------------------------------------------------------------------------------
# created_by / created_at spoofing
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestAuthorshipSpoofing:
    def test_member_created_by_is_ignored_on_create(self, world):
        r = world.c["member"].post(
            issues_url(world),
            {"name": "spoof", "created_by": str(world.admin.id), "created_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.created_by_id == world.member.id
        assert issue.created_at.year != 2020

    def test_admin_created_by_is_honoured_on_create(self, world):
        r = world.c["admin"].post(
            issues_url(world),
            {"name": "import", "created_by": str(world.member.id), "created_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.created_by_id == world.member.id
        assert issue.created_at.year == 2020

    def test_member_cannot_reattribute_on_patch(self, world):
        r = world.c["member"].patch(
            issues_url(world, world.i_guest), {"created_by": str(world.member.id)}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.created_by_id == world.guest.id

    def test_admin_can_reattribute_on_patch(self, world):
        r = world.c["admin"].patch(
            issues_url(world, world.i_guest), {"created_by": str(world.member.id)}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.created_by_id == world.member.id

    def test_link_and_comment_authorship(self, world):
        base = f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/issues/{world.i_admin.id}"
        # who posts -> who is recorded as author: members are always themselves, only the admin may re-attribute
        for who, expected, actor in (
            ("member", world.member, world.member),
            ("admin", world.guest, world.admin),
        ):
            link = world.c[who].post(
                f"{base}/links/",
                {"url": f"https://example.com/{who}", "created_by": str(world.guest.id)},
                format="json",
            )
            assert link.status_code == status.HTTP_201_CREATED, link.data
            from plane.db.models import IssueComment, IssueLink

            assert IssueLink.objects.get(id=link.data["id"]).created_by_id == expected.id
            comment = world.c[who].post(
                f"{base}/comments/", {"comment_html": "<p>c</p>", "created_by": str(world.guest.id)}, format="json"
            )
            assert comment.status_code == status.HTTP_201_CREATED, comment.data
            row = IssueComment.objects.get(id=comment.data["id"])
            assert row.created_by_id == expected.id
            assert row.actor_id == actor.id


# --------------------------------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestUpdate:
    def test_guest_can_edit_allowed_fields_of_their_own_item(self, world):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {
                "name": "renamed",
                "priority": "urgent",
                "description_html": "<p>new</p>",
                "labels": [str(world.label.id)],
            },
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "renamed"
        assert world.i_guest.priority == "urgent"
        assert world.i_guest.state_id == world.backlog.id
        assert list(world.i_guest.labels.values_list("id", flat=True)) == [world.label.id]

    def test_work_items_alias_route_behaves_the_same(self, world):
        r = world.c["guest"].patch(items_url(world, world.i_guest), {"name": "via alias"}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        assert world.c["guest"].patch(items_url(world, world.i_admin), {"name": "no"}, format="json").status_code == 403

    @pytest.mark.parametrize("target", ["i_admin", "i_guest2"])
    def test_guest_cannot_edit_someone_elses_item(self, world, target):
        issue = getattr(world, target)
        r = world.c["guest"].patch(issues_url(world, issue), {"name": "hijack"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        issue.refresh_from_db()
        assert issue.name != "hijack"

    @pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
    def test_guest_disallowed_field_on_own_item_is_a_400(self, world, field):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {"name": "keep-me-out", field: forbidden_payloads(world)[field]},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert r.data["disallowed_fields"] == [field]
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "by guest"

    def test_guest_empty_team_fields_are_tolerated_on_update(self, world):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {"name": "ok", "assignees": [], "state": None, "parent": None},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data

    def test_label_from_another_project_is_rejected_on_update(self, world):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest), {"labels": [str(world.other_label.id)]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_is_not_routable(self, world):
        # PUT-create exists in the view but is not exposed by the URL conf; a guest never gets past permissions
        assert world.c["guest"].put(issues_url(world, world.i_guest), {"name": "x"}, format="json").status_code == 403
        assert world.c["member"].put(issues_url(world, world.i_guest), {"name": "x"}, format="json").status_code == 405
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "by guest"

    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_member_and_admin_can_edit_any_field_of_any_item(self, world, who):
        r = world.c[who].patch(
            issues_url(world, world.i_guest),
            {"state": str(world.started.id), "assignees": [str(world.member.id)], "target_date": "2030-05-05"},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.state_id == world.started.id
        assert str(world.i_guest.target_date) == "2030-05-05"


# --------------------------------------------------------------------------------------------------
# Removed guest (FORK: put-permission x guest-access) — v1 has no creator shortcut, but pin the same outcome
# as the app: a guest taken off the project cannot edit or delete the items they created
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestRemovedGuest:
    @pytest.fixture
    def removed(self, world):
        ProjectMember.objects.filter(project=world.ga, member=world.guest).update(is_active=False)
        return world

    @pytest.mark.parametrize("route", [issues_url, items_url])
    def test_removed_guest_cannot_edit_their_own_item(self, removed, route):
        r = removed.c["guest"].patch(route(removed, removed.i_guest), {"name": "after-removal"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        removed.i_guest.refresh_from_db()
        assert removed.i_guest.name == "by guest"

    def test_removed_guest_cannot_put_or_delete(self, removed):
        # PUT is not routed in v1 (405 for a member); permissions run first, so a non-writer gets 403
        assert (
            removed.c["guest"].put(issues_url(removed, removed.i_guest), {"name": "x"}, format="json").status_code
            == 403
        )
        assert removed.c["guest"].delete(issues_url(removed, removed.i_guest)).status_code == 403
        removed.i_guest.refresh_from_db()
        assert removed.i_guest.name == "by guest"

    def test_still_active_guest_keeps_editing_their_own_item(self, removed):
        r = removed.c["guest2"].patch(issues_url(removed, removed.i_guest2), {"name": "mine"}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        assert (
            removed.c["guest2"].patch(issues_url(removed, removed.i_admin), {"name": "no"}, format="json").status_code
            == 403
        )


# --------------------------------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestDelete:
    @pytest.mark.parametrize("target", ["i_guest", "i_admin"])
    def test_guest_can_never_delete(self, world, target):
        issue = getattr(world, target)
        r = world.c["guest"].delete(issues_url(world, issue))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.filter(id=issue.id).exists()

    def test_guest_cannot_delete_via_alias_route(self, world):
        assert world.c["guest"].delete(items_url(world, world.i_guest)).status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.filter(id=world.i_guest.id).exists()

    def test_admin_can_still_delete(self, world):
        assert world.c["admin"].delete(issues_url(world, world.i_guest)).status_code == status.HTTP_204_NO_CONTENT
        assert not Issue.objects.filter(id=world.i_guest.id).exists()

    def test_member_can_still_delete_their_own(self, world):
        own = _issue("member's", world.ga, world.member)
        assert world.c["member"].delete(issues_url(world, own)).status_code == status.HTTP_204_NO_CONTENT
        assert world.c["member"].delete(issues_url(world, world.i_admin)).status_code == status.HTTP_403_FORBIDDEN


# --------------------------------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestActivity:
    @pytest.fixture
    def real_activity(self, monkeypatch):
        """Run the real ``issue_activity`` task inline (no broker / redis / notifications)."""
        task = MagicMock()
        task.delay.side_effect = lambda **kw: real_issue_activity.run(**{**kw, "origin": None, "notification": False})
        monkeypatch.setattr("plane.api.views.issue.issue_activity", task)
        return task

    def test_guest_create_records_an_activity(self, world, real_activity):
        r = world.c["guest"].post(issues_url(world), {"name": "tracked"}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        rows = IssueActivity.objects.filter(issue_id=r.data["id"])
        assert rows.filter(verb="created", actor=world.guest).exists()

    def test_guest_edit_records_an_activity(self, world, real_activity):
        r = world.c["guest"].patch(issues_url(world, world.i_guest), {"name": "edited"}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        row = IssueActivity.objects.get(issue=world.i_guest, field="name", verb="updated")
        assert row.actor_id == world.guest.id
        assert row.old_value == "by guest" and row.new_value == "edited"


# --------------------------------------------------------------------------------------------------
# Bypass endpoints: everything that mutates a work item beyond create/edit stays closed to guests
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestBypassEndpointsDeniedToGuests:
    def test_denied(self, world):
        slug, pid = world.ws.slug, world.ga.id
        item = f"/api/v1/workspaces/{slug}/projects/{pid}/work-items/{world.i_guest.id}"
        cycle = f"/api/v1/workspaces/{slug}/projects/{pid}/cycles/{uuid4()}"
        module = f"/api/v1/workspaces/{slug}/projects/{pid}/modules/{uuid4()}"
        other = uuid4()
        attempts = [
            # relations (legacy + dependency / custom relation routes)
            ("post", f"{item}/relations/", {"relation_type": "blocked_by", "issues": [str(world.i_admin.id)]}),
            ("post", f"{item}/dependencies/", {"related_issue_id": str(world.i_admin.id)}),
            ("delete", f"{item}/dependencies/{world.i_admin.id}/", None),
            ("post", f"{item}/work-item-relations/", {"related_issue_id": str(world.i_admin.id)}),
            ("delete", f"{item}/work-item-relations/{world.i_admin.id}/", None),
            # move to another project
            ("post", f"{item}/move/", {"project_id": str(world.other.id)}),
            # cycles / modules: add, remove, transfer
            ("post", f"{cycle}/cycle-issues/", {"issues": [str(world.i_guest.id)]}),
            ("delete", f"{cycle}/cycle-issues/{world.i_guest.id}/", None),
            ("post", f"{cycle}/transfer-issues/", {"new_cycle_id": str(other)}),
            ("post", f"{module}/module-issues/", {"issues": [str(world.i_guest.id)]}),
            ("delete", f"{module}/module-issues/{world.i_guest.id}/", None),
            # states / labels (project settings)
            (
                "post",
                f"/api/v1/workspaces/{slug}/projects/{pid}/states/",
                {"name": "S", "color": "#fff", "group": "started"},
            ),
            ("patch", f"/api/v1/workspaces/{slug}/projects/{pid}/states/{world.backlog.id}/", {"name": "S"}),
            ("post", f"/api/v1/workspaces/{slug}/projects/{pid}/labels/", {"name": "L"}),
            ("patch", f"/api/v1/workspaces/{slug}/projects/{pid}/labels/{world.label.id}/", {"name": "L"}),
            ("delete", f"/api/v1/workspaces/{slug}/projects/{pid}/labels/{world.label.id}/", None),
        ]
        for method, url, body in attempts:
            kwargs = {"format": "json"} if body is not None else {}
            r = (
                getattr(world.c["guest"], method)(url, body, **kwargs)
                if body is not None
                else getattr(world.c["guest"], method)(url)
            )
            assert r.status_code == status.HTTP_403_FORBIDDEN, (method, url, r.status_code, getattr(r, "data", None))
        assert world.i_guest.parent_id is None
        assert Label.objects.filter(id=world.label.id).exists()
        assert IssueRelation.objects.count() == 0


# --------------------------------------------------------------------------------------------------
# Legacy relations GET no longer leaks hidden items
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestLegacyRelationsFiltered:
    def _relate(self, world):
        IssueRelation.objects.create(
            issue=world.i_guest,
            related_issue=world.i_admin,
            relation_type="relates_to",
            project=world.ga,
            workspace=world.ws,
        )
        IssueRelation.objects.create(
            issue=world.i_guest,
            related_issue=world.i_guest2,
            relation_type="relates_to",
            project=world.ga,
            workspace=world.ws,
        )

    def ids(self, response):
        return {entry["issue_id"] for entry in response.data["relates_to"]}

    def test_restricted_guest_only_sees_their_own_related_items(self, world):
        self._relate(world)
        r = world.c["guest"].get(items_url(world, world.i_guest) + "relations/")
        assert r.status_code == status.HTTP_200_OK
        assert self.ids(r) == set()  # neither related item belongs to the guest

    def test_guest_sees_related_item_they_authored(self, world):
        mine = _issue("also mine", world.ga, world.guest)
        IssueRelation.objects.create(
            issue=world.i_guest, related_issue=mine, relation_type="relates_to", project=world.ga, workspace=world.ws
        )
        self._relate(world)
        r = world.c["guest"].get(items_url(world, world.i_guest) + "relations/")
        assert self.ids(r) == {str(mine.id)}

    def test_member_sees_everything_and_guest_view_all_lifts_the_filter(self, world):
        self._relate(world)
        expected = {str(world.i_admin.id), str(world.i_guest2.id)}
        assert self.ids(world.c["member"].get(items_url(world, world.i_guest) + "relations/")) == expected
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        assert self.ids(world.c["guest"].get(items_url(world, world.i_guest) + "relations/")) == expected


# --------------------------------------------------------------------------------------------------
# sort_order: sent by drag-and-drop UIs (and by SDKs); ignored for guests, never written, never a 400
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestSortOrderIgnored:
    @pytest.mark.parametrize("value", [0, 0.0, 5, 123.5])
    def test_patch_ignores_sort_order(self, world, value):
        before = Issue.objects.get(id=world.i_guest.id).sort_order
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest), {"name": "dragged", "sort_order": value}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "dragged"
        assert world.i_guest.sort_order == before

    def test_patch_with_only_sort_order_zero_is_a_noop_200(self, world):
        before = Issue.objects.get(id=world.i_guest.id).sort_order
        r = world.c["guest"].patch(issues_url(world, world.i_guest), {"sort_order": 0}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.sort_order == before

    def test_create_ignores_sort_order(self, world):
        r = world.c["guest"].post(issues_url(world), {"name": "new", "sort_order": 0}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.sort_order != 0

    def test_member_can_still_set_sort_order(self, world):
        r = world.c["member"].patch(issues_url(world, world.i_guest), {"sort_order": 7}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.sort_order == 7
