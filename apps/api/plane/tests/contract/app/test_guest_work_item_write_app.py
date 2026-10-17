# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project guests raise and edit their own work items through the app API (FORK: PSR-119).

Mirror of ``tests/contract/api/test_guest_work_item_write.py`` (the public ``/api/v1/`` API), which pins the
same behaviour: an active project Guest can create work items and edit their own, sending only name / description*
/ priority / labels (``label_ids``); any other field with a value is a 400 naming it (empty values are ignored);
a Guest can never delete; every other mutation route stays closed to guests.
"""

from unittest import mock
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.issue_activities_task import issue_activity as real_issue_activity
from plane.db.models import (
    Intake,
    IntakeIssue,
    Issue,
    IssueActivity,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)

VIEWS = "plane.app.views.issue.base"
INTAKE_VIEWS = "plane.app.views.intake.base"


@pytest.fixture(autouse=True)
def _no_side_effects():
    """Celery tasks are mocked; the activity tests swap in the real activity task explicitly."""
    patches = [
        mock.patch(f"{VIEWS}.issue_activity"),
        mock.patch(f"{VIEWS}.model_activity"),
        mock.patch(f"{VIEWS}.issue_description_version_task"),
        mock.patch(f"{VIEWS}.recent_visited_task"),
        mock.patch(f"{INTAKE_VIEWS}.issue_activity"),
        mock.patch(f"{INTAKE_VIEWS}.issue_description_version_task"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def _user(name, workspace, ws_role):
    user = User.objects.create(email=f"{name}@plane.so", username=f"{name}-user")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=ws_role)
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _issue(name, project, creator):
    state = State.objects.filter(project=project, default=True).first()
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=creator)
    issue.refresh_from_db()
    return issue


@pytest.fixture
def world(db, workspace, create_user):
    admin = create_user
    member = _user("member", workspace, 15)
    guest = _user("guest", workspace, 5)
    guest2 = _user("guest2", workspace, 5)
    outsider = _user("outsider", workspace, 15)

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
    w.admin, w.member, w.guest, w.guest2, w.outsider = admin, member, guest, guest2, outsider
    w.backlog, w.started, w.label, w.other_label = backlog, started, label, other_label
    w.i_admin = _issue("by admin", ga, admin)
    w.i_guest = _issue("by guest", ga, guest)
    w.i_guest2 = _issue("by guest2", ga, guest2)
    w.c = {
        name: _client(user)
        for name, user in (
            ("admin", admin),
            ("member", member),
            ("guest", guest),
            ("guest2", guest2),
            ("outsider", outsider),
        )
    }
    return w


def project_url(w, project=None):
    return f"/api/workspaces/{w.ws.slug}/projects/{(project or w.ga).id}"


def issues_url(w, issue=None):
    return f"{project_url(w)}/issues/{issue.id}/" if issue else f"{project_url(w)}/issues/"


# Non-empty values for every team-only field a guest must NOT be able to send (app field names).
def forbidden_payloads(w):
    return {
        "state_id": str(w.started.id),
        "assignee_ids": [str(w.member.id)],
        "parent_id": str(w.i_admin.id),
        "estimate_point": "1",
        "start_date": "2030-01-01",
        "target_date": "2030-02-01",
        "external_id": "ext-1",
        "external_source": "jira",
        "created_by": str(w.admin.id),
        "archived_at": "2030-01-01T00:00:00Z",
        "is_draft": True,
        "type_id": str(uuid4()),
        "completed_at": "2030-01-01T00:00:00Z",
    }


FORBIDDEN_FIELDS = sorted(
    [
        "state_id",
        "assignee_ids",
        "parent_id",
        "estimate_point",
        "start_date",
        "target_date",
        "external_id",
        "external_source",
        "created_by",
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
                "label_ids": [str(world.label.id)],
            },
            format="json",
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.created_by_id == world.guest.id
        assert issue.state_id == world.backlog.id
        assert issue.priority == "high"
        assert list(issue.labels.values_list("id", flat=True)) == [world.label.id]

    @pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
    def test_each_disallowed_field_is_a_400_naming_it(self, world, field):
        r = world.c["guest"].post(
            issues_url(world), {"name": "x", field: forbidden_payloads(world)[field]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert r.data["disallowed_fields"] == [field]
        assert field in r.data["error"]
        assert not Issue.objects.filter(name="x").exists()

    def test_empty_values_for_team_only_fields_are_tolerated(self, world):
        body = {
            "name": "Form submit",
            "assignee_ids": [],
            "state_id": None,
            "parent_id": None,
            "estimate_point": None,
            "start_date": None,
            "external_id": "",
            "is_draft": False,
            "label_ids": [],
        }
        r = world.c["guest"].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.state_id == world.backlog.id
        assert issue.parent_id is None and not issue.assignees.exists()

    def test_label_from_another_project_is_rejected(self, world):
        r = world.c["guest"].post(
            issues_url(world), {"name": "x", "label_ids": [str(world.other_label.id)]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "label_ids" in r.data
        assert not Issue.objects.filter(name="x").exists()

    def test_guest_cannot_create_in_a_project_they_are_not_in(self, world):
        r = world.c["guest"].post(f"{project_url(world, world.other)}/issues/", {"name": "x"}, format="json")
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
            "state_id": str(world.started.id),
            "assignee_ids": [str(world.member.id)],
            "start_date": "2030-01-01",
        }
        r = world.c[who].post(issues_url(world), body, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        issue = Issue.objects.get(id=r.data["id"])
        assert issue.state_id == world.started.id
        assert list(issue.assignees.values_list("id", flat=True)) == [world.member.id]

    def test_workspace_member_outside_the_project_is_still_denied(self, world):
        r = world.c["outsider"].post(issues_url(world), {"name": "x"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN


# --------------------------------------------------------------------------------------------------
# Update (PATCH, and PUT which is routed through the same checks)
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestUpdate:
    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_guest_can_edit_allowed_fields_of_their_own_item(self, world, method):
        r = getattr(world.c["guest"], method)(
            issues_url(world, world.i_guest),
            {
                "name": "renamed",
                "priority": "urgent",
                "description_html": "<p>new</p>",
                "label_ids": [str(world.label.id)],
            },
            format="json",
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "renamed"
        assert world.i_guest.priority == "urgent"
        assert world.i_guest.state_id == world.backlog.id
        assert list(world.i_guest.labels.values_list("id", flat=True)) == [world.label.id]

    @pytest.mark.parametrize("method", ["patch", "put"])
    @pytest.mark.parametrize("target", ["i_admin", "i_guest2"])
    def test_guest_cannot_edit_someone_elses_item(self, world, method, target):
        issue = getattr(world, target)
        r = getattr(world.c["guest"], method)(issues_url(world, issue), {"name": "hijack"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        issue.refresh_from_db()
        assert issue.name != "hijack"

    @pytest.mark.parametrize("method", ["patch", "put"])
    @pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
    def test_guest_disallowed_field_on_own_item_is_a_400(self, world, method, field):
        r = getattr(world.c["guest"], method)(
            issues_url(world, world.i_guest),
            {"name": "keep-me-out", field: forbidden_payloads(world)[field]},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert r.data["disallowed_fields"] == [field]
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "by guest"

    def test_url_identity_keys_from_the_web_store_are_ignored(self, world):
        # the web stores send id / project_id in the body; the URL is authoritative
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {"name": "via-store", "id": str(world.i_admin.id), "project_id": str(uuid4())},
            format="json",
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        world.i_guest.refresh_from_db()
        world.i_admin.refresh_from_db()
        assert world.i_guest.name == "via-store"
        assert world.i_admin.name != "via-store"

    def test_guest_empty_team_fields_are_tolerated_on_update(self, world):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {"name": "ok", "assignee_ids": [], "state_id": None, "parent_id": None, "skip_activity": False},
            format="json",
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)

    def test_label_from_another_project_is_rejected_on_update(self, world):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest), {"label_ids": [str(world.other_label.id)]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_no_longer_bypasses_permissions(self, world):
        # the default ModelViewSet PUT was reachable by any authenticated user, whatever their project role
        r = world.c["outsider"].put(issues_url(world, world.i_guest), {"name": "pwn"}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "by guest"

    @pytest.mark.parametrize("method", ["patch", "put"])
    @pytest.mark.parametrize("who", ["member", "admin"])
    def test_member_and_admin_can_edit_any_field_of_any_item(self, world, who, method):
        r = getattr(world.c[who], method)(
            issues_url(world, world.i_guest),
            {"state_id": str(world.started.id), "assignee_ids": [str(world.member.id)], "target_date": "2030-05-05"},
            format="json",
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        world.i_guest.refresh_from_db()
        assert world.i_guest.state_id == world.started.id
        assert str(world.i_guest.target_date) == "2030-05-05"


# --------------------------------------------------------------------------------------------------
# Removed guest (FORK: put-permission x guest-access) — the creator shortcut in ``allow_permission`` now needs
# an active ProjectMember, so a guest taken off the project loses edit access to the items they created
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestRemovedGuest:
    @pytest.fixture
    def removed(self, world):
        ProjectMember.objects.filter(project=world.ga, member=world.guest).update(is_active=False)
        return world

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_removed_guest_cannot_edit_their_own_item(self, removed, method):
        r = getattr(removed.c["guest"], method)(
            issues_url(removed, removed.i_guest), {"name": "after-removal"}, format="json"
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN
        removed.i_guest.refresh_from_db()
        assert removed.i_guest.name == "by guest"

    def test_removed_guest_cannot_delete_their_own_item(self, removed):
        r = removed.c["guest"].delete(issues_url(removed, removed.i_guest))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.filter(id=removed.i_guest.id).exists()

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_still_active_guest_keeps_editing_their_own_item(self, removed, method):
        # guest2 is still active: the shortcut fix must not take away a guest's own-item edits
        r = getattr(removed.c["guest2"], method)(issues_url(removed, removed.i_guest2), {"name": "mine"}, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        removed.i_guest2.refresh_from_db()
        assert removed.i_guest2.name == "mine"

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_active_guest_with_disallowed_field_on_own_item_still_400(self, removed, method):
        r = getattr(removed.c["guest2"], method)(
            issues_url(removed, removed.i_guest2), {"state_id": str(removed.started.id)}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        removed.i_guest2.refresh_from_db()
        assert removed.i_guest2.state_id == removed.backlog.id


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

    def test_admin_can_still_delete(self, world):
        assert world.c["admin"].delete(issues_url(world, world.i_guest)).status_code == status.HTTP_204_NO_CONTENT
        assert not Issue.objects.filter(id=world.i_guest.id).exists()

    def test_member_can_still_delete_their_own_only(self, world):
        own = _issue("member's", world.ga, world.member)
        assert world.c["member"].delete(issues_url(world, own)).status_code == status.HTTP_204_NO_CONTENT
        assert world.c["member"].delete(issues_url(world, world.i_admin)).status_code == status.HTTP_403_FORBIDDEN


# --------------------------------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestActivity:
    @pytest.fixture
    def real_activity(self):
        """Run the real ``issue_activity`` task inline (no broker / redis / notifications)."""
        task = mock.MagicMock()
        task.delay.side_effect = lambda **kw: real_issue_activity.run(**{**kw, "origin": None, "notification": False})
        with mock.patch(f"{VIEWS}.issue_activity", task):
            yield task

    def test_guest_create_records_an_activity(self, world, real_activity):
        r = world.c["guest"].post(issues_url(world), {"name": "tracked"}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert IssueActivity.objects.filter(issue_id=r.data["id"], verb="created", actor=world.guest).exists()

    def test_guest_edit_records_an_activity(self, world, real_activity):
        r = world.c["guest"].patch(issues_url(world, world.i_guest), {"name": "edited"}, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        row = IssueActivity.objects.get(issue=world.i_guest, field="name", verb="updated")
        assert row.actor_id == world.guest.id
        assert row.old_value == "by guest" and row.new_value == "edited"

    def test_guest_skip_activity_is_ignored_and_history_is_kept(self, world, real_activity):
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest),
            {"description_html": "<p>quiet</p>", "skip_activity": True},
            format="json",
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        assert IssueActivity.objects.filter(issue=world.i_guest, field="description", actor=world.guest).exists()

    def test_guest_create_with_web_store_identity_keys(self, world):
        r = world.c["guest"].post(
            issues_url(world), {"name": "from-modal", "project_id": str(world.ga.id)}, format="json"
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data


# --------------------------------------------------------------------------------------------------
# Bypass endpoints: everything that mutates a work item beyond create/edit stays closed to guests
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestBypassEndpointsDeniedToGuests:
    def test_denied(self, world):
        base = project_url(world)
        gi = world.i_guest.id
        attempts = [
            ("delete", f"{base}/bulk-delete-issues/", {"issue_ids": [str(gi)]}),
            ("post", f"{base}/bulk-archive-issues/", {"issue_ids": [str(gi)]}),
            ("post", f"{base}/issues/{gi}/archive/", {}),
            ("delete", f"{base}/issues/{gi}/archive/", None),
            ("post", f"{base}/issues/{gi}/sub-issues/", {"sub_issue_ids": [str(world.i_admin.id)]}),
            (
                "post",
                f"{base}/issues/{gi}/issue-relation/",
                {"relation_type": "blocked_by", "issues": [str(world.i_admin.id)]},
            ),
            ("post", f"{base}/issues/{gi}/remove-relation/", {"related_issue": str(world.i_admin.id)}),
            ("post", f"{base}/cycles/{uuid4()}/cycle-issues/", {"issues": [str(gi)]}),
            ("delete", f"{base}/cycles/{uuid4()}/cycle-issues/{gi}/", None),
            ("post", f"{base}/modules/{uuid4()}/issues/", {"issues": [str(gi)]}),
            ("post", f"{base}/issues/{gi}/modules/", {"modules": [str(uuid4())]}),
            ("delete", f"{base}/modules/{uuid4()}/issues/{gi}/", None),
            ("post", f"{base}/issue-dates/", {"updates": []}),
            ("post", f"{base}/work-items/{gi}/move/", {"project_id": str(world.other.id)}),
            ("post", f"{base}/issue-labels/", {"name": "L"}),
            ("post", f"{base}/bulk-create-labels/", {"label_data": [{"name": "L"}]}),
            ("post", f"{base}/states/", {"name": "S", "color": "#fff", "group": "started"}),
            ("post", f"/api/workspaces/{world.ws.slug}/draft-to-issue/{uuid4()}/", {}),
        ]
        wrong = []
        for method, url, body in attempts:
            client = world.c["guest"]
            r = getattr(client, method)(url, body, format="json") if body is not None else getattr(client, method)(url)
            if r.status_code != status.HTTP_403_FORBIDDEN:
                wrong.append((method, url, r.status_code, getattr(r, "data", None)))
        assert not wrong
        world.i_guest.refresh_from_db()
        assert world.i_guest.parent_id is None and world.i_guest.archived_at is None
        assert not Label.objects.filter(name="L").exists()


# --------------------------------------------------------------------------------------------------
# Intake: the app's intake create accepted arbitrary work item fields from guests, and its destroy
# let the guest creator delete the underlying work item
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestIntake:
    @pytest.fixture
    def intake(self, world):
        return Intake.objects.create(name="Intake", project=world.ga, workspace=world.ws, is_default=True)

    def url(self, world, issue=None):
        base = f"{project_url(world)}/intake-issues/"
        return f"{base}{issue.id}/" if issue else base

    def test_guest_intake_submission_is_limited_to_allowed_fields(self, world, intake):
        r = world.c["guest"].post(
            self.url(world),
            {"issue": {"name": "Please look", "priority": "low", "assignee_ids": [str(world.member.id)]}},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert r.data["disallowed_fields"] == ["assignee_ids"]
        assert not Issue.objects.filter(name="Please look").exists()

    def test_guest_intake_submission_ok_and_empty_fields_tolerated(self, world, intake):
        r = world.c["guest"].post(
            self.url(world),
            {"issue": {"name": "Please look", "priority": "low", "assignee_ids": [], "parent_id": None}},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        issue = Issue.objects.get(name="Please look")
        assert issue.created_by_id == world.guest.id
        assert issue.state.group == "triage"

    def test_member_intake_submission_is_unchanged(self, world, intake):
        r = world.c["member"].post(
            self.url(world),
            {"issue": {"name": "From member", "assignee_ids": [str(world.member.id)]}},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data

    def test_guest_cannot_delete_their_own_intake_item(self, world, intake):
        issue = _issue("in intake", world.ga, world.guest)
        IntakeIssue.objects.create(intake=intake, project=world.ga, workspace=world.ws, issue=issue, status=-2)
        r = world.c["guest"].delete(self.url(world, issue))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert Issue.objects.filter(id=issue.id).exists()
        assert IntakeIssue.objects.filter(issue=issue).exists()

    def test_skip_activity_is_not_honoured_for_guest_intake_edits(self, world, intake):
        issue = _issue("in intake", world.ga, world.guest)
        ii = IntakeIssue.objects.create(intake=intake, project=world.ga, workspace=world.ws, issue=issue, status=-2)
        IntakeIssue.objects.filter(id=ii.id).update(created_by=world.guest)
        with mock.patch(f"{INTAKE_VIEWS}.issue_activity") as activity:
            r = world.c["guest"].patch(
                self.url(world, issue),
                {"issue": {"name": "renamed"}, "description_html": "<p>x</p>", "skip_activity": True},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, getattr(r, "data", None)
        issue.refresh_from_db()
        assert issue.name == "renamed"
        assert [c.kwargs["type"] for c in activity.delay.call_args_list] == ["issue.activity.updated"]

    def test_skip_activity_is_still_honoured_for_team_description_migrations(self, world, intake):
        issue = _issue("in intake", world.ga, world.admin)
        IntakeIssue.objects.create(intake=intake, project=world.ga, workspace=world.ws, issue=issue, status=-2)
        with mock.patch(f"{INTAKE_VIEWS}.issue_activity") as activity:
            r = world.c["admin"].patch(
                self.url(world, issue),
                {"issue": {"name": "migrated"}, "description_html": "<p>x</p>", "skip_activity": True},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK, getattr(r, "data", None)
        assert "issue.activity.updated" not in [c.kwargs["type"] for c in activity.delay.call_args_list]


# --------------------------------------------------------------------------------------------------
# sort_order: sent by drag-and-drop UIs; ignored for guests, never written, never a 400
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestGuestSortOrderIgnored:
    @pytest.mark.parametrize("value", [0, 0.0, 5, 123.5])
    def test_patch_ignores_sort_order(self, world, value):
        before = Issue.objects.get(id=world.i_guest.id).sort_order
        r = world.c["guest"].patch(
            issues_url(world, world.i_guest), {"name": "dragged", "sort_order": value}, format="json"
        )
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "dragged"
        assert world.i_guest.sort_order == before

    def test_patch_with_only_sort_order_zero_is_accepted_and_not_written(self, world):
        before = Issue.objects.get(id=world.i_guest.id).sort_order
        r = world.c["guest"].patch(issues_url(world, world.i_guest), {"sort_order": 0}, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT, getattr(r, "data", None)
        world.i_guest.refresh_from_db()
        assert world.i_guest.sort_order == before
