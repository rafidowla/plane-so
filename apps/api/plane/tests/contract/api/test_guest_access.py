# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The public ``/api/v1/`` API enforces the same guest visibility / ownership rules as the web app (FORK: guest-access).

Covers: work item retrieve, comments (scope + author-or-admin edit/delete), attachments (membership, scope,
issue binding, uploader-or-admin delete), project visibility for workspace guests, the project role ceiling and
the smaller read paths (relations, activity, links, search).

Since the 2026-10-23 upstream sync (WEB-9423 / INFRA-1180) a work item a restricted guest cannot see is a 404 on
v1 (upstream's ``guest_cannot_view_issue``), not the fork's former 403.
"""

from unittest.mock import MagicMock

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Cycle,
    CycleIssue,
    FileAsset,
    Intake,
    IntakeIssue,
    Issue,
    IssueActivity,
    IssueComment,
    IssueLink,
    Module,
    ModuleIssue,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Keep celery tasks, S3 and link crawling out of the way; these tests only look at authorization."""
    for target in (
        "plane.api.views.issue.issue_activity",
        "plane.api.views.issue.model_activity",
        "plane.api.views.issue.get_asset_object_metadata",
        "plane.api.views.issue.crawl_work_item_link_title",
        "plane.api.views.issue.S3Storage",
    ):
        monkeypatch.setattr(target, MagicMock())
    storage = MagicMock()
    storage.return_value.generate_presigned_post.return_value = {}
    monkeypatch.setattr("plane.api.views.issue.S3Storage", storage)


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
    state = State.objects.filter(project=project).first()
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=creator)
    issue.refresh_from_db()
    return issue


def _comment(issue, author):
    comment = IssueComment.objects.create(
        issue=issue, project=issue.project, workspace=issue.workspace, actor=author, comment_html="<p>hi</p>"
    )
    IssueComment.objects.filter(id=comment.id).update(created_by=author)
    return comment


def _attachment(issue, uploader, name="a.png"):
    asset = FileAsset.objects.create(
        attributes={"name": name, "type": "image/png", "size": 1},
        asset=f"ws/{name}",
        size=1,
        workspace=issue.workspace,
        project=issue.project,
        issue=issue,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        is_uploaded=True,
    )
    FileAsset.objects.filter(id=asset.id).update(created_by=uploader)
    return asset


@pytest.fixture
def world(db, workspace, create_user):
    """test-workspace.

    GA project (secret, guest_view_all_features=False): ``admin`` (project admin, = create_user), ``member`` (15),
    ``guest`` (5). ``outsider`` is a workspace member (15) who is NOT in GA. ``guest2`` is a project guest
    who only owns ``i_guest2``.
      i_admin   created by admin   (hidden from the guests)
      i_guest   created by guest   (guest's own)
      i_guest2  created by guest2
    PUB project (network=2): admin + member only; the guests are not members.
    """
    admin = create_user
    member = _user("member", workspace, 15)
    guest = _user("guest", workspace, 5)
    guest2 = _user("guest2", workspace, 5)
    outsider = _user("outsider", workspace, 15)

    def project(name, identifier, members, **extra):
        p = Project.objects.create(name=name, identifier=identifier, workspace=workspace, created_by=admin, **extra)
        State.objects.create(name="Backlog", group="backlog", default=True, project=p, workspace=workspace)
        for user, role in members:
            ProjectMember.objects.create(project=p, member=user, role=role, is_active=True)
        return p

    ga = project("GA", "GA", [(admin, 20), (member, 15), (guest, 5), (guest2, 5)], network=0)
    pub = project("Public", "PUB", [(admin, 20), (member, 15)], network=2)

    i_admin = _issue("by admin", ga, admin)
    i_guest = _issue("by guest", ga, guest)
    i_guest2 = _issue("by guest2", ga, guest2)

    class World:
        pass

    w = World()
    w.ws, w.ga, w.pub = workspace, ga, pub
    w.admin, w.member, w.guest, w.guest2, w.outsider = admin, member, guest, guest2, outsider
    w.i_admin, w.i_guest, w.i_guest2 = i_admin, i_guest, i_guest2
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


def base(w, issue=None, project=None):
    project = project or w.ga
    url = f"/api/v1/workspaces/{w.ws.slug}/projects/{project.id}"
    return f"{url}/issues/{issue.id}" if issue else url


# --------------------------------------------------------------------------------------------------
# 1. Work item single retrieve
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestWorkItemRetrieve:
    def test_restricted_guest_denied_on_other_users_item(self, world):
        r = world.c["guest"].get(base(world, world.i_admin) + "/")
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_guest_can_open_own_item(self, world):
        r = world.c["guest"].get(base(world, world.i_guest) + "/")
        assert r.status_code == status.HTTP_200_OK, r.data
        assert str(r.data["id"]) == str(world.i_guest.id)

    def test_guest_view_all_features_allows_guest(self, world):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        r = world.c["guest"].get(base(world, world.i_admin) + "/")
        assert r.status_code == status.HTTP_200_OK, r.data

    @pytest.mark.parametrize("who", ["admin", "member"])
    def test_member_and_admin_unaffected(self, world, who):
        for issue in (world.i_admin, world.i_guest):
            r = world.c[who].get(base(world, issue) + "/")
            assert r.status_code == status.HTTP_200_OK, r.data

    def test_identifier_lookup_is_scoped_too(self, world):
        slug = world.ws.slug
        hidden = world.c["guest"].get(f"/api/v1/workspaces/{slug}/issues/GA-{world.i_admin.sequence_id}/")
        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        own = world.c["guest"].get(f"/api/v1/workspaces/{slug}/issues/GA-{world.i_guest.sequence_id}/")
        assert own.status_code == status.HTTP_200_OK, own.data
        member = world.c["member"].get(f"/api/v1/workspaces/{slug}/issues/GA-{world.i_admin.sequence_id}/")
        assert member.status_code == status.HTTP_200_OK, member.data

    def test_work_items_alias_route_is_scoped(self, world):
        url = f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/work-items/{world.i_admin.id}/"
        assert world.c["guest"].get(url).status_code == status.HTTP_404_NOT_FOUND
        assert world.c["member"].get(url).status_code == status.HTTP_200_OK

    def test_search_only_finds_visible_items(self, world):
        url = f"/api/v1/workspaces/{world.ws.slug}/issues/search/"
        guest = world.c["guest"].get(url, {"search": "by", "workspace_search": "true"})
        assert {i["name"] for i in guest.data["issues"]} == {"by guest"}
        member = world.c["member"].get(url, {"search": "by", "workspace_search": "true"})
        assert {i["name"] for i in member.data["issues"]} == {"by admin", "by guest", "by guest2"}


# --------------------------------------------------------------------------------------------------
# 2. Comments
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestComments:
    def url(self, w, issue, comment=None):
        url = base(w, issue) + "/comments/"
        return url + f"{comment.id}/" if comment else url

    def test_list_scoped_to_guests_view_of_item(self, world):
        _comment(world.i_admin, world.admin)
        _comment(world.i_guest, world.guest)
        assert world.c["guest"].get(self.url(world, world.i_admin)).status_code == status.HTTP_404_NOT_FOUND
        own = world.c["guest"].get(self.url(world, world.i_guest))
        assert own.status_code == status.HTTP_200_OK
        assert own.data["total_count"] == 1
        assert world.c["member"].get(self.url(world, world.i_admin)).status_code == status.HTTP_200_OK

    def test_retrieve_scoped_to_guests_view_of_item(self, world):
        hidden = _comment(world.i_admin, world.admin)
        r = world.c["guest"].get(self.url(world, world.i_admin, hidden))
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert world.c["member"].get(self.url(world, world.i_admin, hidden)).status_code == status.HTTP_200_OK

    def test_create_refused_on_hidden_item_allowed_on_own(self, world):
        # upstream WEB-9423: a hidden work item is a 404 on v1 (was the app's 400)
        denied = world.c["guest"].post(self.url(world, world.i_admin), {"comment_html": "<p>x</p>"}, format="json")
        assert denied.status_code == status.HTTP_404_NOT_FOUND
        assert not IssueComment.objects.filter(issue=world.i_admin).exists()
        ok = world.c["guest"].post(self.url(world, world.i_guest), {"comment_html": "<p>x</p>"}, format="json")
        assert ok.status_code == status.HTTP_201_CREATED, ok.data
        member = world.c["member"].post(self.url(world, world.i_admin), {"comment_html": "<p>x</p>"}, format="json")
        assert member.status_code == status.HTTP_201_CREATED, member.data

    def test_create_requires_item_in_url_project(self, world):
        other = _issue("elsewhere", world.pub, world.admin)
        r = world.c["member"].post(
            f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/issues/{other.id}/comments/",
            {"comment_html": "<p>x</p>"},
            format="json",
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_non_author_member_cannot_edit_or_delete(self, world):
        comment = _comment(world.i_guest, world.guest)
        r = world.c["member"].patch(
            self.url(world, world.i_guest, comment), {"comment_html": "<p>hijack</p>"}, format="json"
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN
        r = world.c["member"].delete(self.url(world, world.i_guest, comment))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        comment.refresh_from_db()
        assert comment.comment_html == "<p>hi</p>"
        assert IssueComment.objects.filter(id=comment.id).exists()

    def test_guest_cannot_touch_other_peoples_comments(self, world):
        # guest_view_all_features lets guest2 see the item; that still does not make the comment theirs
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        comment = _comment(world.i_guest, world.guest)
        mine = world.c["guest2"]
        assert (
            mine.patch(self.url(world, world.i_guest, comment), {"comment_html": "x"}, format="json").status_code == 403
        )
        assert mine.delete(self.url(world, world.i_guest, comment)).status_code == status.HTTP_403_FORBIDDEN
        assert IssueComment.objects.filter(id=comment.id).exists()

    def test_author_can_edit_and_delete_own(self, world):
        for who, author in (("member", world.member), ("guest", world.guest)):
            comment = _comment(world.i_guest, author)
            r = world.c[who].patch(
                self.url(world, world.i_guest, comment), {"comment_html": "<p>mine</p>"}, format="json"
            )
            assert r.status_code == status.HTTP_200_OK, r.data
            r = world.c[who].delete(self.url(world, world.i_guest, comment))
            assert r.status_code == status.HTTP_204_NO_CONTENT
            assert not IssueComment.objects.filter(id=comment.id).exists()

    def test_project_admin_can_edit_and_delete_anyones(self, world):
        comment = _comment(world.i_guest, world.guest)
        r = world.c["admin"].patch(
            self.url(world, world.i_guest, comment), {"comment_html": "<p>moderated</p>"}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        assert world.c["admin"].delete(self.url(world, world.i_guest, comment)).status_code == 204


# --------------------------------------------------------------------------------------------------
# 3. Attachments
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestAttachments:
    def url(self, w, issue, attachment=None):
        url = base(w, issue) + "/issue-attachments/"
        return url + f"{attachment.id}/" if attachment else url

    def test_non_member_cannot_list_or_read_or_delete(self, world):
        att = _attachment(world.i_admin, world.admin)
        outsider = world.c["outsider"]
        assert outsider.get(self.url(world, world.i_admin)).status_code == status.HTTP_403_FORBIDDEN
        assert outsider.get(self.url(world, world.i_admin, att)).status_code == status.HTTP_403_FORBIDDEN
        assert outsider.delete(self.url(world, world.i_admin, att)).status_code == status.HTTP_403_FORBIDDEN
        assert outsider.patch(self.url(world, world.i_admin, att), {}, format="json").status_code == 403
        assert outsider.post(self.url(world, world.i_admin), {}, format="json").status_code == 403
        att.refresh_from_db()
        assert att.is_deleted is False

    def test_list_scoped_to_guests_view_of_item(self, world):
        _attachment(world.i_admin, world.admin)
        _attachment(world.i_guest, world.guest)
        assert world.c["guest"].get(self.url(world, world.i_admin)).status_code == status.HTTP_404_NOT_FOUND
        own = world.c["guest"].get(self.url(world, world.i_guest))
        assert own.status_code == status.HTTP_200_OK
        assert len(own.data) == 1
        assert world.c["member"].get(self.url(world, world.i_admin)).status_code == status.HTTP_200_OK

    def test_download_scoped_to_guests_view_of_item(self, world):
        att = _attachment(world.i_admin, world.admin)
        r = world.c["guest"].get(self.url(world, world.i_admin, att))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_upload_and_confirm_refused_on_hidden_item(self, world):
        payload = {"name": "a.png", "type": "image/png", "size": 10}
        r = world.c["guest"].post(self.url(world, world.i_admin), payload, format="json")
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert not FileAsset.objects.filter(issue=world.i_admin).exists()
        att = _attachment(world.i_admin, world.admin)
        FileAsset.objects.filter(id=att.id).update(is_uploaded=False)
        r = world.c["guest"].patch(self.url(world, world.i_admin, att), {"is_uploaded": True}, format="json")
        assert r.status_code == status.HTTP_404_NOT_FOUND
        att.refresh_from_db()
        assert att.is_uploaded is False

    def test_upload_allowed_for_member_on_any_item(self, world):
        payload = {"name": "a.png", "type": "image/png", "size": 10}
        r = world.c["member"].post(self.url(world, world.i_admin), payload, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data

    def test_non_uploader_member_cannot_delete(self, world):
        att = _attachment(world.i_guest, world.guest)
        r = world.c["member"].delete(self.url(world, world.i_guest, att))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        att.refresh_from_db()
        assert att.is_deleted is False

    def test_guest_cannot_delete_someone_elses_attachment(self, world):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        att = _attachment(world.i_guest, world.guest)
        r = world.c["guest2"].delete(self.url(world, world.i_guest, att))
        assert r.status_code == status.HTTP_403_FORBIDDEN
        att.refresh_from_db()
        assert att.is_deleted is False

    def test_uploader_and_admin_can_delete(self, world):
        mine = _attachment(world.i_guest, world.guest)
        assert world.c["guest"].delete(self.url(world, world.i_guest, mine)).status_code == 204
        theirs = _attachment(world.i_guest, world.guest, name="b.png")
        assert world.c["admin"].delete(self.url(world, world.i_guest, theirs)).status_code == 204
        member_own = _attachment(world.i_admin, world.member, name="c.png")
        assert world.c["member"].delete(self.url(world, world.i_admin, member_own)).status_code == 204

    def test_attachment_must_belong_to_url_issue(self, world):
        att = _attachment(world.i_guest, world.guest)
        wrong = self.url(world, world.i_admin, att)  # attachment of i_guest addressed via i_admin
        assert world.c["admin"].delete(wrong).status_code == status.HTTP_404_NOT_FOUND
        assert world.c["admin"].get(wrong).status_code == status.HTTP_404_NOT_FOUND
        assert world.c["admin"].patch(wrong, {}, format="json").status_code == status.HTTP_404_NOT_FOUND
        att.refresh_from_db()
        assert att.is_deleted is False

    def test_confirming_upload_does_not_reassign_owner(self, world):
        att = _attachment(world.i_guest, world.guest)
        FileAsset.objects.filter(id=att.id).update(is_uploaded=False)
        r = world.c["member"].patch(self.url(world, world.i_guest, att), {"is_uploaded": True}, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT
        att.refresh_from_db()
        assert att.is_uploaded is True
        assert att.created_by_id == world.guest.id


# --------------------------------------------------------------------------------------------------
# 4. Public projects are not visible to workspace guests
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestProjectVisibility:
    def ids(self, r):
        return {str(p["id"]) for p in r.data["results"]}

    def test_workspace_guest_only_sees_member_projects(self, world):
        slug = world.ws.slug
        for path in ("projects/", "projects-lite/"):
            r = world.c["guest"].get(f"/api/v1/workspaces/{slug}/{path}")
            assert r.status_code == status.HTTP_200_OK, r.data
            assert self.ids(r) == {str(world.ga.id)}, path

    def test_workspace_guest_cannot_retrieve_public_project(self, world):
        slug = world.ws.slug
        r = world.c["guest"].get(f"/api/v1/workspaces/{slug}/projects/{world.pub.id}/")
        assert r.status_code == status.HTTP_404_NOT_FOUND
        r = world.c["guest"].get(f"/api/v1/workspaces/{slug}/projects/{world.ga.id}/")
        assert r.status_code == status.HTTP_200_OK, r.data

    def test_guest_sees_public_project_once_a_member(self, world):
        ProjectMember.objects.create(project=world.pub, member=world.guest, role=5, is_active=True)
        r = world.c["guest"].get(f"/api/v1/workspaces/{world.ws.slug}/projects/")
        assert self.ids(r) == {str(world.ga.id), str(world.pub.id)}

    def test_workspace_member_still_sees_public_projects(self, world):
        slug = world.ws.slug
        r = world.c["outsider"].get(f"/api/v1/workspaces/{slug}/projects/")
        assert self.ids(r) == {str(world.pub.id)}
        r = world.c["outsider"].get(f"/api/v1/workspaces/{slug}/projects-lite/")
        assert self.ids(r) == {str(world.pub.id)}
        r = world.c["outsider"].get(f"/api/v1/workspaces/{slug}/projects/{world.pub.id}/")
        assert r.status_code == status.HTTP_200_OK, r.data


# --------------------------------------------------------------------------------------------------
# 5. Project role ceiling
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestProjectRoleCeiling:
    def members_url(self, w, pk=None):
        url = f"/api/v1/workspaces/{w.ws.slug}/projects/{w.pub.id}/members/"
        return url + f"{pk}/" if pk else url

    @pytest.mark.parametrize("role", [15, 20])
    def test_workspace_guest_cannot_be_added_above_guest(self, world, role):
        r = world.c["admin"].post(self.members_url(world), {"member": str(world.guest.id), "role": role}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert not ProjectMember.objects.filter(project=world.pub, member=world.guest).exists()

    def test_workspace_guest_can_be_added_as_guest(self, world):
        r = world.c["admin"].post(self.members_url(world), {"member": str(world.guest.id), "role": 5}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data

    def test_workspace_member_can_be_added_as_member_or_admin(self, world):
        r = world.c["admin"].post(
            self.members_url(world), {"member": str(world.outsider.id), "role": 20}, format="json"
        )
        assert r.status_code == status.HTTP_201_CREATED, r.data

    def test_workspace_admin_cannot_be_added_below_admin(self, world):
        wsa = _user("wsadmin", world.ws, 20)
        r = world.c["admin"].post(self.members_url(world), {"member": str(wsa.id), "role": 15}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        r = world.c["admin"].post(self.members_url(world), {"member": str(wsa.id), "role": 20}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data

    def test_patch_cannot_raise_workspace_guest(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.guest)
        url = f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/members/{pm.id}/"
        r = world.c["admin"].patch(url, {"role": 15}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        pm.refresh_from_db()
        assert pm.role == 5

    def test_patch_can_promote_workspace_member(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.member)
        url = f"/api/v1/workspaces/{world.ws.slug}/projects/{world.ga.id}/members/{pm.id}/"
        r = world.c["admin"].patch(url, {"role": 20}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        pm.refresh_from_db()
        assert pm.role == 20


# --------------------------------------------------------------------------------------------------
# 6. Smaller read paths: relations, activity, links
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestParentScopedReads:
    def activity(self, issue, actor):
        return IssueActivity.objects.create(
            issue=issue, project=issue.project, workspace=issue.workspace, actor=actor, field="name", verb="updated"
        )

    def link(self, issue):
        return IssueLink.objects.create(
            issue=issue, project=issue.project, workspace=issue.workspace, url="https://example.com", title="x"
        )

    def test_relations_scoped(self, world):
        def url(issue):
            return base(world, issue).replace("/issues/", "/work-items/") + "/relations/"

        assert world.c["guest"].get(url(world.i_admin)).status_code == status.HTTP_404_NOT_FOUND
        assert world.c["guest"].get(url(world.i_guest)).status_code == status.HTTP_200_OK
        assert world.c["member"].get(url(world.i_admin)).status_code == status.HTTP_200_OK

    def test_activity_scoped(self, world):
        hidden = self.activity(world.i_admin, world.admin)
        own = self.activity(world.i_guest, world.guest)
        g, m = world.c["guest"], world.c["member"]
        assert g.get(base(world, world.i_admin) + "/activities/").status_code == status.HTTP_404_NOT_FOUND
        assert g.get(base(world, world.i_admin) + f"/activities/{hidden.id}/").status_code == status.HTTP_404_NOT_FOUND
        assert g.get(base(world, world.i_guest) + "/activities/").status_code == status.HTTP_200_OK
        assert g.get(base(world, world.i_guest) + f"/activities/{own.id}/").status_code == status.HTTP_200_OK
        assert m.get(base(world, world.i_admin) + "/activities/").status_code == status.HTTP_200_OK
        assert m.get(base(world, world.i_admin) + f"/activities/{hidden.id}/").status_code == status.HTTP_200_OK

    def test_links_scoped(self, world):
        hidden = self.link(world.i_admin)
        own = self.link(world.i_guest)
        g, m = world.c["guest"], world.c["member"]
        assert g.get(base(world, world.i_admin) + "/links/").status_code == status.HTTP_404_NOT_FOUND
        assert g.get(base(world, world.i_admin) + f"/links/{hidden.id}/").status_code == status.HTTP_404_NOT_FOUND
        assert g.get(base(world, world.i_guest) + "/links/").status_code == status.HTTP_200_OK
        assert g.get(base(world, world.i_guest) + f"/links/{own.id}/").status_code == status.HTTP_200_OK
        assert m.get(base(world, world.i_admin) + "/links/").status_code == status.HTTP_200_OK
        assert m.get(base(world, world.i_admin) + f"/links/{hidden.id}/").status_code == status.HTTP_200_OK

    def test_guest_view_all_features_lifts_scope(self, world):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        g = world.c["guest"]
        for suffix in ("/activities/", "/links/", "/comments/", "/issue-attachments/"):
            assert g.get(base(world, world.i_admin) + suffix).status_code == status.HTTP_200_OK, suffix
        relations = base(world, world.i_admin).replace("/issues/", "/work-items/") + "/relations/"
        assert g.get(relations).status_code == status.HTTP_200_OK


# --------------------------------------------------------------------------------------------------
# 7. Intake, module and cycle work-item lists are scoped for restricted guests
# --------------------------------------------------------------------------------------------------
def _results(response):
    return response.data["results"] if isinstance(response.data, dict) else response.data


@pytest.mark.contract
class TestIntakeScoping:
    @pytest.fixture
    def intake(self, world):
        Project.objects.filter(id=world.ga.id).update(intake_view=True)
        intake = Intake.objects.create(name="Intake", project=world.ga, workspace=world.ws, is_default=True)
        for issue in (world.i_admin, world.i_guest):
            intake_issue = IntakeIssue.objects.create(
                intake=intake, project=world.ga, workspace=world.ws, issue=issue, status=-2
            )
            IntakeIssue.objects.filter(id=intake_issue.id).update(created_by=issue.created_by)
        return intake

    def url(self, w, issue=None):
        url = f"/api/v1/workspaces/{w.ws.slug}/projects/{w.ga.id}/intake-issues/"
        return f"{url}{issue.id}/" if issue else url

    def test_restricted_guest_list_only_has_own_intake_items(self, world, intake):
        r = world.c["guest"].get(self.url(world))
        assert r.status_code == status.HTTP_200_OK, r.data
        assert {str(i["issue"]) for i in _results(r)} == {str(world.i_guest.id)}

    def test_restricted_guest_cannot_retrieve_other_users_intake_item(self, world, intake):
        # upstream WEB-9423: v1 answers 404 for a work item the guest cannot see (the fork used the app's 403)
        r = world.c["guest"].get(self.url(world, world.i_admin))
        assert r.status_code == status.HTTP_404_NOT_FOUND, getattr(r, "data", None)
        assert world.c["guest"].get(self.url(world, world.i_guest)).status_code == status.HTTP_200_OK

    def test_unknown_intake_item_is_still_a_404(self, world, intake):
        assert world.c["guest"].get(self.url(world, world.i_guest2)).status_code == status.HTTP_404_NOT_FOUND

    def test_scope_follows_the_intake_item_author_not_the_work_item_author(self, world, intake):
        # the app scopes on IntakeIssue.created_by; make the two authors disagree and check v1 follows the same field
        IntakeIssue.objects.filter(intake=intake, issue=world.i_admin).update(created_by=world.guest)
        IntakeIssue.objects.filter(intake=intake, issue=world.i_guest).update(created_by=world.admin)
        r = world.c["guest"].get(self.url(world))
        assert {str(i["issue"]) for i in _results(r)} == {str(world.i_admin.id)}
        assert world.c["guest"].get(self.url(world, world.i_admin)).status_code == status.HTTP_200_OK
        assert world.c["guest"].get(self.url(world, world.i_guest)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("who", ["admin", "member"])
    def test_team_members_still_see_every_intake_item(self, world, intake, who):
        r = world.c[who].get(self.url(world))
        assert {str(i["issue"]) for i in _results(r)} == {str(world.i_admin.id), str(world.i_guest.id)}
        assert world.c[who].get(self.url(world, world.i_admin)).status_code == status.HTTP_200_OK

    def test_guest_view_all_features_lifts_scope(self, world, intake):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        r = world.c["guest"].get(self.url(world))
        assert {str(i["issue"]) for i in _results(r)} == {str(world.i_admin.id), str(world.i_guest.id)}
        assert world.c["guest"].get(self.url(world, world.i_admin)).status_code == status.HTTP_200_OK

    def test_skip_activity_is_not_honoured_for_guests(self, world, intake, monkeypatch):
        activity = MagicMock()
        monkeypatch.setattr("plane.api.views.intake.issue_activity", activity)
        r = world.c["guest"].patch(
            self.url(world, world.i_guest),
            {"issue": {"name": "renamed"}, "description_html": "<p>x</p>", "skip_activity": True},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        world.i_guest.refresh_from_db()
        assert world.i_guest.name == "renamed"
        assert [c.kwargs["type"] for c in activity.delay.call_args_list] == ["issue.activity.updated"]


@pytest.mark.contract
class TestModuleAndCycleScoping:
    @pytest.fixture
    def module(self, world):
        module = Module.objects.create(name="M", project=world.ga, workspace=world.ws)
        for issue in (world.i_admin, world.i_guest, world.i_guest2):
            ModuleIssue.objects.create(module=module, issue=issue, project=world.ga, workspace=world.ws)
        return module

    @pytest.fixture
    def cycle(self, world):
        cycle = Cycle.objects.create(name="C", project=world.ga, workspace=world.ws, owned_by=world.admin)
        for issue in (world.i_admin, world.i_guest, world.i_guest2):
            CycleIssue.objects.create(cycle=cycle, issue=issue, project=world.ga, workspace=world.ws)
        return cycle

    def url(self, w, kind, obj, issue=None):
        url = f"/api/v1/workspaces/{w.ws.slug}/projects/{w.ga.id}/{kind}s/{obj.id}/{kind}-issues/"
        return f"{url}{issue.id}/" if issue else url

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    def test_restricted_guest_list_only_has_own_items(self, world, module, cycle, kind):
        obj = module if kind == "module" else cycle
        r = world.c["guest"].get(self.url(world, kind, obj))
        assert r.status_code == status.HTTP_200_OK, r.data
        assert {str(i["id"]) for i in _results(r)} == {str(world.i_guest.id)}

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    @pytest.mark.parametrize("who", ["admin", "member"])
    def test_team_members_see_every_item(self, world, module, cycle, kind, who):
        obj = module if kind == "module" else cycle
        r = world.c[who].get(self.url(world, kind, obj))
        assert r.status_code == status.HTTP_200_OK, r.data
        assert {str(i["id"]) for i in _results(r)} == {
            str(i.id) for i in (world.i_admin, world.i_guest, world.i_guest2)
        }

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    def test_guest_view_all_features_lifts_scope(self, world, module, cycle, kind):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        obj = module if kind == "module" else cycle
        r = world.c["guest"].get(self.url(world, kind, obj))
        assert len(_results(r)) == 3

    # FORK: guest-access PSR-119 — the module/cycle aggregates must not reveal how many hidden items exist
    def obj_url(self, w, kind, obj=None, archived=False):
        base = f"/api/v1/workspaces/{w.ws.slug}/projects/{w.ga.id}/"
        if archived:
            return f"{base}archived-{kind}s/"
        return f"{base}{kind}s/{obj.id}/" if obj else f"{base}{kind}s/"

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    @pytest.mark.parametrize("who, expected", [("guest", 1), ("guest2", 1), ("member", 3), ("admin", 3)])
    def test_aggregate_counts_only_include_visible_items(self, world, module, cycle, kind, who, expected):
        obj = module if kind == "module" else cycle
        detail = world.c[who].get(self.obj_url(world, kind, obj))
        assert detail.status_code == status.HTTP_200_OK, detail.data
        assert detail.data["total_issues"] == expected
        listing = world.c[who].get(self.obj_url(world, kind))
        assert listing.status_code == status.HTTP_200_OK, listing.data
        assert [o["total_issues"] for o in _results(listing) if str(o["id"]) == str(obj.id)] == [expected]

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    @pytest.mark.parametrize("who, expected", [("guest", 1), ("member", 3)])
    def test_archived_aggregate_counts_only_include_visible_items(self, world, module, cycle, kind, who, expected):
        obj = module if kind == "module" else cycle
        type(obj).objects.filter(id=obj.id).update(archived_at=timezone.now())
        r = world.c[who].get(self.obj_url(world, kind, archived=True))
        assert r.status_code == status.HTTP_200_OK, r.data
        assert [o["total_issues"] for o in _results(r) if str(o["id"]) == str(obj.id)] == [expected]

    @pytest.mark.parametrize("kind", ["module", "cycle"])
    def test_guest_view_all_features_lifts_the_count_scope(self, world, module, cycle, kind):
        Project.objects.filter(id=world.ga.id).update(guest_view_all_features=True)
        obj = module if kind == "module" else cycle
        assert world.c["guest"].get(self.obj_url(world, kind, obj)).data["total_issues"] == 3

    def test_restricted_guest_cannot_open_other_users_cycle_item(self, world, cycle):
        g = world.c["guest"]
        assert g.get(self.url(world, "cycle", cycle, world.i_admin)).status_code == status.HTTP_404_NOT_FOUND
        assert g.get(self.url(world, "cycle", cycle, world.i_guest)).status_code == status.HTTP_200_OK
        assert world.c["member"].get(self.url(world, "cycle", cycle, world.i_admin)).status_code == status.HTTP_200_OK


# --------------------------------------------------------------------------------------------------
# 8. Member serializer: the role cap cannot be skipped by omitting ``role`` or swapping ``member``
# --------------------------------------------------------------------------------------------------
@pytest.mark.contract
class TestProjectMemberRoleCapBypass:
    def members_url(self, w, project, pk=None):
        url = f"/api/v1/workspaces/{w.ws.slug}/projects/{project.id}/members/"
        return url + f"{pk}/" if pk else url

    def test_patch_member_swap_to_workspace_guest_is_rejected(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.member)
        url = self.members_url(world, world.ga, pm.id)
        r = world.c["admin"].patch(url, {"member": str(world.guest2.id)}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        pm.refresh_from_db()
        assert pm.member_id == world.member.id and pm.role == 15
        assert ProjectMember.objects.get(project=world.ga, member=world.guest2).role == 5

    def test_patch_member_swap_with_role_is_rejected_too(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.member)
        r = world.c["admin"].patch(
            self.members_url(world, world.ga, pm.id), {"member": str(world.outsider.id), "role": 15}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        pm.refresh_from_db()
        assert pm.member_id == world.member.id

    def test_patch_same_member_is_still_accepted(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.member)
        r = world.c["admin"].patch(
            self.members_url(world, world.ga, pm.id), {"member": str(world.member.id), "role": 20}, format="json"
        )
        assert r.status_code == status.HTTP_200_OK, r.data
        pm.refresh_from_db()
        assert pm.role == 20

    def test_patch_without_role_on_legacy_workspace_guest_row_is_a_noop(self, world):
        # a row that already holds a workspace guest at Member (legacy data): an update that sets no role changes
        # nothing, so it is a 200 no-op (not a 400 for data the client never sent) and the row is left as it was
        pm = ProjectMember.objects.get(project=world.ga, member=world.guest)
        ProjectMember.objects.filter(id=pm.id).update(role=15)
        url = self.members_url(world, world.ga, pm.id)
        for body in ({}, {"member": str(world.guest.id)}):
            r = world.c["admin"].patch(url, body, format="json")
            assert r.status_code == status.HTTP_200_OK, r.data
            assert r.data["role"] == 15
        pm.refresh_from_db()
        assert pm.role == 15

    def test_legacy_workspace_guest_row_can_be_corrected_but_not_reasserted(self, world):
        pm = ProjectMember.objects.get(project=world.ga, member=world.guest)
        ProjectMember.objects.filter(id=pm.id).update(role=15)
        url = self.members_url(world, world.ga, pm.id)
        for role in (15, 20):
            assert world.c["admin"].patch(url, {"role": role}, format="json").status_code == status.HTTP_400_BAD_REQUEST
        r = world.c["admin"].patch(url, {"role": 5}, format="json")
        assert r.status_code == status.HTTP_200_OK, r.data
        pm.refresh_from_db()
        assert pm.role == 5

    def test_create_without_role_defaults_to_guest_and_passes_for_workspace_guest(self, world):
        r = world.c["admin"].post(self.members_url(world, world.pub), {"member": str(world.guest.id)}, format="json")
        assert r.status_code == status.HTTP_201_CREATED, r.data
        assert ProjectMember.objects.get(project=world.pub, member=world.guest).role == 5

    def test_create_without_role_cannot_add_workspace_admin_below_admin(self, world):
        wsa = _user("wsadmin2", world.ws, 20)
        r = world.c["admin"].post(self.members_url(world, world.pub), {"member": str(wsa.id)}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST, r.data
        assert not ProjectMember.objects.filter(project=world.pub, member=wsa).exists()
