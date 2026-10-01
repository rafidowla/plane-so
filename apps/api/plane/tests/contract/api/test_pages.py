# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for project pages and the "not available on this edition" routes (FORK: PSR-86).

These are the endpoints the stock Plane MCP connector's ``page`` and ``workitem_type`` tools call:
``.../projects/<id>/pages/``, ``.../pages/<id>/``, ``.../pages/<id>/archive/``, plus the workspace
page, work-item page and work item type routes this edition answers with a 400 and a way forward.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    Page,
    PageVersion,
    Project,
    ProjectMember,
    ProjectPage,
    User,
    Workspace,
    WorkspaceMember,
)

WORKSPACE_PAGES_UNAVAILABLE = (
    "Workspace-level pages (wiki) are not available on this Plane edition. Pass project_id to work with a "
    "project's pages."
)
WORK_ITEM_PAGES_UNAVAILABLE = (
    "Attaching pages to work items is not available on this Plane edition. Add the page URL as a work item "
    "link instead (workitem_link create)."
)
WORK_ITEM_TYPES_UNAVAILABLE = (
    "Work item types and epics are not available on this Plane edition. To model an epic, create a normal "
    "work item, add an 'Epic' label, set parent=<its id> on the child work items, and list children with "
    'pql childOf("<IDENTIFIER>").'
)
# What the SDK's PaginatedPageResponse requires of a list answer.
ENVELOPE_FIELDS = {
    "total_count",
    "next_cursor",
    "prev_cursor",
    "next_page_results",
    "prev_page_results",
    "count",
    "total_pages",
    "total_results",
    "results",
}
LIST_FIELDS = {
    "id",
    "name",
    "owned_by",
    "access",
    "color",
    "is_locked",
    "archived_at",
    "parent_id",
    "workspace",
    "projects",
    "view_props",
    "logo_props",
    "external_id",
    "external_source",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
}
DETAIL_FIELDS = LIST_FIELDS | {"description_html", "description_stripped"}
METHODS = ["get", "post", "put", "patch", "delete"]


@pytest.fixture(autouse=True)
def transaction_task(monkeypatch):
    """Stand in for the page_transaction celery task so no broker is needed; tests assert on its calls."""
    mock = MagicMock()
    monkeypatch.setattr("plane.api.views.page.page_transaction", mock)
    return mock


def _client_for(user, token):
    APIToken.objects.create(user=user, label=f"token-{token}", token=token)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token)
    return client


def _page(name, project, owner, access=Page.PUBLIC_ACCESS, parent=None, html="<p>body</p>", **extra):
    page = Page.objects.create(
        name=name,
        workspace=project.workspace,
        owned_by=owner,
        access=access,
        parent=parent,
        description_html=html,
        **extra,
    )
    ProjectPage.objects.create(project=project, page=page, workspace=project.workspace)
    # BaseModel.save() takes created_by from the request user, and there is none here.
    Page.objects.filter(id=page.id).update(created_by=owner)
    page.refresh_from_db()
    return page


def _archive(*pages):
    Page.objects.filter(id__in=[page.id for page in pages]).update(archived_at=timezone.now().date())


def _pages_url(project, slug=None):
    return f"/api/v1/workspaces/{slug or project.workspace.slug}/projects/{project.id}/pages/"


def _page_url(project, page_id):
    return f"{_pages_url(project)}{page_id}/"


def _archive_url(project, page_id):
    return f"{_page_url(project, page_id)}archive/"


def _names(response):
    return {row["name"] for row in response.data["results"]}


@pytest.fixture
def world(db, workspace, create_user):
    """Caller = ``create_user`` (workspace admin, admin of ALP).

    test-workspace:
      ALP (caller admin; u2 member; guest is a guest; guest_view_all_features off):
        "mine public", "mine private" (caller), "theirs public", "theirs private" (u2)
      GAM (caller NOT a member; u2 member): "gamma public"
    other-ws (caller is an admin there too): OTH project, "other public"
    """
    me = create_user
    u2 = User.objects.create(email="u2@plane.so", username="u2-user")
    guest = User.objects.create(email="guest@plane.so", username="guest-user")
    WorkspaceMember.objects.create(workspace=workspace, member=u2, role=15)
    WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)

    def project(name, identifier, ws, role=None):
        p = Project.objects.create(name=name, identifier=identifier, workspace=ws, created_by=me)
        if role is not None:
            ProjectMember.objects.create(project=p, member=me, role=role, is_active=True)
        ProjectMember.objects.create(project=p, member=u2, role=15, is_active=True)
        return p

    alp = project("Alpha", "ALP", workspace, role=20)
    ProjectMember.objects.create(project=alp, member=guest, role=5, is_active=True)
    gam = project("Gamma", "GAM", workspace)

    other_ws = Workspace.objects.create(name="Other", owner=me, slug="other-ws")
    WorkspaceMember.objects.create(workspace=other_ws, member=me, role=20)
    oth = project("Other", "OTH", other_ws, role=20)

    return SimpleNamespace(
        ws=workspace,
        other_ws=other_ws,
        me=me,
        u2=u2,
        guest=guest,
        alp=alp,
        gam=gam,
        oth=oth,
        mine_public=_page("mine public", alp, me),
        mine_private=_page("mine private", alp, me, access=Page.PRIVATE_ACCESS),
        theirs_public=_page("theirs public", alp, u2),
        theirs_private=_page("theirs private", alp, u2, access=Page.PRIVATE_ACCESS),
        gamma_public=_page("gamma public", gam, u2),
        other_public=_page("other public", oth, me),
    )


@pytest.fixture
def u2_client(world):
    return _client_for(world.u2, "u2-token-12345")


@pytest.fixture
def guest_client(world):
    return _client_for(world.guest, "guest-token-12345")


@pytest.mark.contract
class TestCreatePage:
    @pytest.mark.django_db
    def test_create_public_page(self, api_key_client, world, transaction_task):
        response = api_key_client.post(
            _pages_url(world.alp),
            {"name": "Runbook", "description_html": "<h1>Deploy</h1><p>Step one</p>", "color": "#ff0000"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert set(response.data) == DETAIL_FIELDS
        assert response.data["name"] == "Runbook"
        assert response.data["description_html"] == "<h1>Deploy</h1><p>Step one</p>"
        assert response.data["access"] == Page.PUBLIC_ACCESS
        assert response.data["owned_by"] == world.me.id
        assert response.data["created_by"] == world.me.id
        assert response.data["projects"] == [str(world.alp.id)]
        assert response.data["parent_id"] is None
        assert response.data["archived_at"] is None

        page = Page.objects.get(id=response.data["id"])
        assert page.workspace_id == world.ws.id
        assert page.color == "#ff0000"
        assert "Deploy" in page.description_stripped and "Step one" in page.description_stripped
        # Left empty so the live editor builds its document from the html on first open.
        assert page.description_binary is None
        assert page.description_json == {}
        assert ProjectPage.objects.filter(page=page, project=world.alp).count() == 1
        transaction_task.delay.assert_called_once_with(
            new_description_html="<h1>Deploy</h1><p>Step one</p>",
            old_description_html=None,
            page_id=str(page.id),
        )

    @pytest.mark.django_db
    def test_create_private_child_page(self, api_key_client, world, u2_client):
        response = api_key_client.post(
            _pages_url(world.alp),
            {
                "name": "Notes",
                "description_html": "<p>mine</p>",
                "access": Page.PRIVATE_ACCESS,
                "parent_id": str(world.mine_public.id),
                "is_locked": True,
                "external_id": "ext-1",
                "external_source": "importer",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["access"] == Page.PRIVATE_ACCESS
        assert str(response.data["parent_id"]) == str(world.mine_public.id)
        assert response.data["is_locked"] is True
        assert response.data["external_id"] == "ext-1"
        # Private: the other member cannot reach it.
        assert u2_client.get(_page_url(world.alp, response.data["id"])).status_code == status.HTTP_404_NOT_FOUND

        duplicate = api_key_client.post(
            _pages_url(world.alp),
            {"name": "Again", "description_html": "<p>x</p>", "external_id": "ext-1", "external_source": "importer"},
            format="json",
        )
        assert duplicate.status_code == status.HTTP_409_CONFLICT
        assert duplicate.data["id"] == str(response.data["id"])

    @pytest.mark.django_db
    def test_create_without_body_gets_an_empty_page(self, api_key_client, world):
        response = api_key_client.post(_pages_url(world.alp), {"name": "Blank"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["description_html"] == "<p></p>"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "extra, fragment",
        [
            ({"collection_id": str(uuid.uuid4())}, "collection_id"),
            ({"collection": str(uuid.uuid4())}, "collection"),
            ({"archived_at": "2026-01-01"}, "archived_at"),
            ({"labels": []}, "labels"),
            ({"description_binary": "AAAA"}, "description_binary"),
            ({"access": 7}, "access"),
        ],
    )
    def test_create_refuses_what_it_cannot_honour(self, api_key_client, world, extra, fragment):
        before = Page.objects.count()
        response = api_key_client.post(
            _pages_url(world.alp), {"name": "Nope", "description_html": "<p>x</p>", **extra}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fragment in str(response.data)
        assert Page.objects.count() == before

    @pytest.mark.django_db
    def test_create_requires_a_name(self, api_key_client, world):
        response = api_key_client.post(_pages_url(world.alp), {"description_html": "<p>x</p>"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.data

    @pytest.mark.django_db
    def test_create_parent_must_be_a_visible_live_page_of_the_project(self, api_key_client, world):
        archived = _page("old", world.alp, world.me)
        _archive(archived)
        for parent in (world.theirs_private, world.gamma_public, world.other_public, archived):
            response = api_key_client.post(
                _pages_url(world.alp),
                {"name": "Child", "description_html": "<p>x</p>", "parent_id": str(parent.id)},
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, parent.name
            assert "Parent page not found" in response.data["error"]

    @pytest.mark.django_db
    def test_create_sanitises_html(self, api_key_client, world):
        response = api_key_client.post(
            _pages_url(world.alp),
            {
                "name": "Unsafe",
                "description_html": (
                    '<p onclick="steal()">hello</p><script>alert(1)</script><a href="javascript:alert(2)">link</a>'
                ),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        stored = Page.objects.get(id=response.data["id"]).description_html
        assert stored == response.data["description_html"]
        assert "hello" in stored
        for unsafe in ("<script", "alert(1)", "onclick", "javascript:"):
            assert unsafe not in stored


@pytest.mark.contract
class TestListPages:
    @pytest.mark.django_db
    def test_list_shows_public_and_own_private_pages_only(self, api_key_client, world, u2_client):
        response = api_key_client.get(_pages_url(world.alp))

        assert response.status_code == status.HTTP_200_OK
        assert ENVELOPE_FIELDS <= set(response.data)
        assert _names(response) == {"mine public", "mine private", "theirs public"}
        # Rows carry no body.
        assert all(set(row) == LIST_FIELDS for row in response.data["results"])

        assert _names(u2_client.get(_pages_url(world.alp))) == {"mine public", "theirs public", "theirs private"}

    @pytest.mark.django_db
    def test_list_includes_child_pages_and_hides_archived(self, api_key_client, world):
        child = _page("child", world.alp, world.me, parent=world.mine_public)
        gone = _page("archived one", world.alp, world.me)
        _archive(gone)

        response = api_key_client.get(_pages_url(world.alp))
        rows = {row["name"]: row for row in response.data["results"]}
        assert "archived one" not in rows
        assert str(rows["child"]["parent_id"]) == str(world.mine_public.id)
        assert str(rows["child"]["id"]) == str(child.id)

        archived = api_key_client.get(_pages_url(world.alp), {"archived": "true"})
        assert _names(archived) == {"archived one"}

    @pytest.mark.django_db
    def test_list_forbidden_for_a_non_member_project(self, api_key_client, world):
        assert api_key_client.get(_pages_url(world.gam)).status_code == status.HTTP_403_FORBIDDEN
        assert (
            api_key_client.post(_pages_url(world.gam), {"name": "x"}, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )

    @pytest.mark.django_db
    def test_other_workspace_is_isolated(self, api_key_client, world):
        # Never listed under another workspace's project...
        assert "other public" not in _names(api_key_client.get(_pages_url(world.alp)))
        # ...not reachable through it by id...
        assert api_key_client.get(_page_url(world.alp, world.other_public.id)).status_code == status.HTTP_404_NOT_FOUND
        # ...and a project addressed under the wrong workspace slug is refused outright.
        assert api_key_client.get(_pages_url(world.oth, slug=world.ws.slug)).status_code == status.HTTP_403_FORBIDDEN
        assert api_key_client.get(_pages_url(world.alp, slug=world.other_ws.slug)).status_code == (
            status.HTTP_403_FORBIDDEN
        )
        # Its own route still works.
        assert _names(api_key_client.get(_pages_url(world.oth))) == {"other public"}

    @pytest.mark.django_db
    def test_removed_project_link_hides_the_page(self, api_key_client, world):
        ProjectPage.objects.filter(page=world.mine_public).update(deleted_at=timezone.now())

        assert "mine public" not in _names(api_key_client.get(_pages_url(world.alp)))
        assert api_key_client.get(_page_url(world.alp, world.mine_public.id)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_pagination(self, api_key_client, world):
        first = api_key_client.get(_pages_url(world.alp), {"per_page": 2})

        assert first.status_code == status.HTTP_200_OK
        assert len(first.data["results"]) == 2
        assert first.data["total_count"] == 3
        assert first.data["next_page_results"] is True

        second = api_key_client.get(_pages_url(world.alp), {"per_page": 2, "cursor": first.data["next_cursor"]})
        assert len(second.data["results"]) == 1
        assert second.data["next_page_results"] is False
        assert _names(first) | _names(second) == {"mine public", "mine private", "theirs public"}

    @pytest.mark.django_db
    def test_order_by_ignores_unknown_fields(self, api_key_client, world):
        by_name = api_key_client.get(_pages_url(world.alp), {"order_by": "name"})
        assert [row["name"] for row in by_name.data["results"]] == ["mine private", "mine public", "theirs public"]

        unsafe = api_key_client.get(_pages_url(world.alp), {"order_by": "owned_by__password"})
        assert unsafe.status_code == status.HTTP_200_OK
        assert len(unsafe.data["results"]) == 3


@pytest.mark.contract
class TestRetrievePage:
    @pytest.mark.django_db
    def test_retrieve_returns_the_body(self, api_key_client, world):
        response = api_key_client.get(_page_url(world.alp, world.theirs_public.id))

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == DETAIL_FIELDS
        assert response.data["description_html"] == "<p>body</p>"
        assert response.data["description_stripped"] == "body"
        assert response.data["owned_by"] == world.u2.id

    @pytest.mark.django_db
    def test_private_pages_are_only_visible_to_their_owner(self, api_key_client, world):
        assert api_key_client.get(_page_url(world.alp, world.mine_private.id)).status_code == status.HTTP_200_OK

        hidden = api_key_client.get(_page_url(world.alp, world.theirs_private.id))
        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert hidden.data == {"error": "Page not found"}

    @pytest.mark.django_db
    def test_page_of_another_project_is_not_reachable_through_this_one(self, api_key_client, world):
        response = api_key_client.get(_page_url(world.alp, world.gamma_public.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_archived_page_can_still_be_retrieved(self, api_key_client, world):
        _archive(world.mine_public)

        response = api_key_client.get(_page_url(world.alp, world.mine_public.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["archived_at"] is not None


@pytest.mark.contract
class TestGuest:
    @pytest.mark.django_db
    def test_guest_sees_only_own_pages_unless_the_project_opens_up(self, guest_client, world):
        assert _names(guest_client.get(_pages_url(world.alp))) == set()
        assert guest_client.get(_page_url(world.alp, world.mine_public.id)).status_code == status.HTTP_404_NOT_FOUND

        Project.objects.filter(id=world.alp.id).update(guest_view_all_features=True)
        assert _names(guest_client.get(_pages_url(world.alp))) == {"mine public", "theirs public"}
        assert guest_client.get(_page_url(world.alp, world.mine_public.id)).status_code == status.HTTP_200_OK
        assert guest_client.get(_page_url(world.alp, world.mine_private.id)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_guest_is_read_only(self, guest_client, world):
        Project.objects.filter(id=world.alp.id).update(guest_view_all_features=True)
        page_url = _page_url(world.alp, world.mine_public.id)
        archive_url = _archive_url(world.alp, world.mine_public.id)

        responses = [
            guest_client.post(_pages_url(world.alp), {"name": "x", "description_html": "<p>x</p>"}, format="json"),
            guest_client.patch(page_url, {"name": "x"}, format="json"),
            guest_client.put(page_url, {"name": "x"}, format="json"),
            guest_client.delete(page_url),
            guest_client.post(archive_url),
            guest_client.delete(archive_url),
        ]

        assert [response.status_code for response in responses] == [status.HTTP_403_FORBIDDEN] * 6
        world.mine_public.refresh_from_db()
        assert world.mine_public.name == "mine public"
        assert world.mine_public.archived_at is None


@pytest.mark.contract
class TestUpdatePage:
    @pytest.mark.django_db
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_update_html_resets_the_editor_document_and_records_versions(
        self, api_key_client, world, transaction_task, method
    ):
        page = world.mine_public
        Page.objects.filter(id=page.id).update(description_binary=b"yjs-doc", description_json={"type": "doc"})

        response = getattr(api_key_client, method)(
            _page_url(world.alp, page.id), {"description_html": "<p>new body</p>"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == DETAIL_FIELDS
        assert response.data["description_html"] == "<p>new body</p>"
        assert response.data["description_stripped"] == "new body"

        page.refresh_from_db()
        assert page.description_html == "<p>new body</p>"
        assert page.description_stripped == "new body"
        assert page.description_binary is None
        assert page.description_json == {}
        assert page.name == "mine public"
        assert page.updated_by_id == world.me.id

        versions = list(PageVersion.objects.filter(page=page).order_by("last_saved_at", "created_at"))
        assert [version.description_html for version in versions] == ["<p>body</p>", "<p>new body</p>"]
        # The outgoing content is kept whole, editor document included, so it can be restored.
        assert bytes(versions[0].description_binary) == b"yjs-doc"
        assert versions[0].description_json == {"type": "doc"}
        assert versions[0].description_stripped == "body"
        assert versions[1].description_binary is None
        assert versions[1].owned_by_id == world.me.id
        transaction_task.delay.assert_called_once_with(
            new_description_html="<p>new body</p>",
            old_description_html="<p>body</p>",
            page_id=str(page.id),
        )

    @pytest.mark.django_db
    def test_repeated_updates_add_one_version_each(self, api_key_client, world):
        url = _page_url(world.alp, world.mine_public.id)
        api_key_client.patch(url, {"description_html": "<p>one</p>"}, format="json")
        api_key_client.patch(url, {"description_html": "<p>two</p>"}, format="json")

        versions = PageVersion.objects.filter(page=world.mine_public).order_by("last_saved_at", "created_at")
        assert [version.description_html for version in versions] == ["<p>body</p>", "<p>one</p>", "<p>two</p>"]

    @pytest.mark.django_db
    def test_versions_are_capped(self, api_key_client, world):
        url = _page_url(world.alp, world.mine_public.id)
        for index in range(22):
            assert api_key_client.patch(url, {"description_html": f"<p>v{index}</p>"}, format="json").status_code == 200

        versions = PageVersion.objects.filter(page=world.mine_public).order_by("-last_saved_at", "-created_at")
        assert versions.count() == 20
        assert versions.first().description_html == "<p>v21</p>"

    @pytest.mark.django_db
    def test_name_only_or_unchanged_html_leaves_the_editor_document_alone(
        self, api_key_client, world, transaction_task
    ):
        page = world.mine_public
        Page.objects.filter(id=page.id).update(description_binary=b"yjs-doc", description_json={"type": "doc"})

        response = api_key_client.patch(
            _page_url(world.alp, page.id), {"name": "Renamed", "description_html": "<p>body</p>"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Renamed"
        page.refresh_from_db()
        assert page.name == "Renamed"
        assert bytes(page.description_binary) == b"yjs-doc"
        assert page.description_json == {"type": "doc"}
        assert not PageVersion.objects.filter(page=page).exists()
        transaction_task.delay.assert_not_called()

    @pytest.mark.django_db
    def test_update_sanitises_html(self, api_key_client, world):
        response = api_key_client.patch(
            _page_url(world.alp, world.mine_public.id),
            {"description_html": '<p>safe</p><script>alert(1)</script><img src="x" onerror="alert(2)">'},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        world.mine_public.refresh_from_db()
        stored = world.mine_public.description_html
        assert "safe" in stored
        for unsafe in ("<script", "alert(1)", "onerror"):
            assert unsafe not in stored

    @pytest.mark.django_db
    def test_update_refused_when_locked(self, api_key_client, world):
        Page.objects.filter(id=world.mine_public.id).update(is_locked=True)
        url = _page_url(world.alp, world.mine_public.id)

        response = api_key_client.patch(url, {"description_html": "<p>nope</p>"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "locked" in response.data["error"]
        world.mine_public.refresh_from_db()
        assert world.mine_public.description_html == "<p>body</p>"

        # Unlocking is the one edit a locked page takes.
        unlocked = api_key_client.patch(url, {"is_locked": False, "name": "Open again"}, format="json")
        assert unlocked.status_code == status.HTTP_200_OK
        assert unlocked.data["is_locked"] is False
        assert unlocked.data["name"] == "Open again"

    @pytest.mark.django_db
    def test_update_refused_when_archived(self, api_key_client, world):
        _archive(world.mine_public)

        response = api_key_client.patch(_page_url(world.alp, world.mine_public.id), {"name": "nope"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "archived" in response.data["error"]
        world.mine_public.refresh_from_db()
        assert world.mine_public.name == "mine public"

    @pytest.mark.django_db
    def test_non_owner_cannot_edit_a_private_page(self, u2_client, world):
        url = _page_url(world.alp, world.mine_private.id)

        responses = [
            u2_client.patch(url, {"name": "hijacked", "description_html": "<p>hijacked</p>"}, format="json"),
            u2_client.put(url, {"name": "hijacked"}, format="json"),
            u2_client.delete(url),
            u2_client.post(_archive_url(world.alp, world.mine_private.id)),
            u2_client.delete(_archive_url(world.alp, world.mine_private.id)),
        ]

        # 404, not 403: a private page's existence is not disclosed.
        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 5
        world.mine_private.refresh_from_db()
        assert world.mine_private.name == "mine private"
        assert world.mine_private.description_html == "<p>body</p>"
        assert world.mine_private.archived_at is None

    @pytest.mark.django_db
    def test_member_can_edit_a_public_page_but_only_the_owner_changes_access(self, u2_client, api_key_client, world):
        url = _page_url(world.alp, world.mine_public.id)

        assert u2_client.patch(url, {"name": "Edited by u2"}, format="json").status_code == status.HTTP_200_OK

        refused = u2_client.patch(url, {"access": Page.PRIVATE_ACCESS}, format="json")
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert "owned by someone else" in refused.data["error"]

        allowed = api_key_client.patch(url, {"access": Page.PRIVATE_ACCESS}, format="json")
        assert allowed.status_code == status.HTTP_200_OK
        world.mine_public.refresh_from_db()
        assert world.mine_public.access == Page.PRIVATE_ACCESS
        assert world.mine_public.name == "Edited by u2"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "body, fragment",
        [
            ({"parent_id": str(uuid.uuid4())}, "parent is fixed"),
            ({"parent": None}, "parent is fixed"),
            ({"archived_at": "2026-01-01"}, "archive endpoint"),
            ({"collection_id": str(uuid.uuid4())}, "collection_id"),
            ({"description_binary": "AAAA"}, "description_binary"),
            ({}, "Nothing to update"),
            ([], "JSON object"),
        ],
    )
    def test_update_refuses_what_it_cannot_honour(self, api_key_client, world, body, fragment):
        response = api_key_client.patch(_page_url(world.alp, world.mine_public.id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fragment in response.data["error"]

    @pytest.mark.django_db
    def test_update_unknown_page(self, api_key_client, world):
        response = api_key_client.patch(_page_url(world.alp, uuid.uuid4()), {"name": "x"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
class TestArchivePage:
    @pytest.mark.django_db
    def test_archive_and_restore_take_descendants_along(self, api_key_client, world):
        child = _page("child", world.alp, world.me, parent=world.mine_public)
        grandchild = _page("grandchild", world.alp, world.u2, parent=child)
        url = _archive_url(world.alp, world.mine_public.id)

        archived = api_key_client.post(url)
        assert archived.status_code == status.HTTP_200_OK
        assert archived.data["id"] == str(world.mine_public.id)
        for page in (world.mine_public, child, grandchild):
            page.refresh_from_db()
            assert page.archived_at is not None, page.name
        world.theirs_public.refresh_from_db()
        assert world.theirs_public.archived_at is None

        # Archiving again is a no-op.
        assert api_key_client.post(url).status_code == status.HTTP_200_OK

        restored = api_key_client.delete(url)
        assert restored.status_code == status.HTTP_204_NO_CONTENT
        for page in (world.mine_public, child, grandchild):
            page.refresh_from_db()
            assert page.archived_at is None, page.name
        child.refresh_from_db()
        assert child.parent_id == world.mine_public.id

    @pytest.mark.django_db
    def test_restoring_a_child_of_an_archived_parent_detaches_it(self, api_key_client, world):
        child = _page("child", world.alp, world.me, parent=world.mine_public)
        _archive(world.mine_public, child)

        response = api_key_client.delete(_archive_url(world.alp, child.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        child.refresh_from_db()
        world.mine_public.refresh_from_db()
        assert child.archived_at is None
        assert child.parent_id is None
        assert world.mine_public.archived_at is not None

    @pytest.mark.django_db
    def test_only_owner_or_admin_archives(self, api_key_client, u2_client, world):
        # u2 is a plain member and does not own this page.
        refused = u2_client.post(_archive_url(world.alp, world.mine_public.id))
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert refused.data["error"] == "Only the owner or admin can archive the page"
        world.mine_public.refresh_from_db()
        assert world.mine_public.archived_at is None

        # The caller is a project admin, so may archive u2's public page; u2, its owner, may restore it.
        assert api_key_client.post(_archive_url(world.alp, world.theirs_public.id)).status_code == status.HTTP_200_OK
        _archive(world.mine_public)
        refused = u2_client.delete(_archive_url(world.alp, world.mine_public.id))
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert refused.data["error"] == "Only the owner or admin can un archive the page"
        assert (
            u2_client.delete(_archive_url(world.alp, world.theirs_public.id)).status_code == status.HTTP_204_NO_CONTENT
        )

    @pytest.mark.django_db
    def test_archive_unknown_page(self, api_key_client, world):
        assert api_key_client.post(_archive_url(world.alp, uuid.uuid4())).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
class TestDeletePage:
    @pytest.mark.django_db
    def test_delete_requires_the_page_to_be_archived(self, api_key_client, world):
        url = _page_url(world.alp, world.mine_public.id)

        response = api_key_client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "The page should be archived before deleting"
        assert Page.objects.filter(id=world.mine_public.id).exists()

    @pytest.mark.django_db
    def test_delete_archived_page_releases_its_children(self, api_key_client, world):
        child = _page("child", world.alp, world.me, parent=world.mine_public)
        url = _page_url(world.alp, world.mine_public.id)
        assert api_key_client.post(_archive_url(world.alp, world.mine_public.id)).status_code == status.HTTP_200_OK

        response = api_key_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Page.objects.filter(id=world.mine_public.id).exists()
        child.refresh_from_db()
        assert child.parent_id is None
        assert api_key_client.get(url).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_only_owner_or_admin_deletes(self, api_key_client, u2_client, world):
        _archive(world.mine_public, world.theirs_public)

        refused = u2_client.delete(_page_url(world.alp, world.mine_public.id))
        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert refused.data["error"] == "Only admin or owner can delete the page"
        assert Page.objects.filter(id=world.mine_public.id).exists()

        # A project admin may delete someone else's public page.
        assert (
            api_key_client.delete(_page_url(world.alp, world.theirs_public.id)).status_code
            == status.HTTP_204_NO_CONTENT
        )


def _unavailable_routes():
    project = "/api/v1/workspaces/test-workspace/projects/{project}/"
    workspace = "/api/v1/workspaces/test-workspace/"
    any_id = str(uuid.uuid4())
    return [
        (workspace + "pages/", WORKSPACE_PAGES_UNAVAILABLE),
        (workspace + f"pages/{any_id}/", WORKSPACE_PAGES_UNAVAILABLE),
        (workspace + f"pages/{any_id}/archive/", WORKSPACE_PAGES_UNAVAILABLE),
        (project + f"work-items/{any_id}/pages/", WORK_ITEM_PAGES_UNAVAILABLE),
        (project + f"work-items/{any_id}/pages/{any_id}/", WORK_ITEM_PAGES_UNAVAILABLE),
        (project + "work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE),
        (project + f"work-item-types/{any_id}/", WORK_ITEM_TYPES_UNAVAILABLE),
        (project + "import-work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE),
        (workspace + "work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE),
        (workspace + f"work-item-types/{any_id}/", WORK_ITEM_TYPES_UNAVAILABLE),
    ]


@pytest.mark.contract
class TestNotAvailableOnThisEdition:
    @pytest.mark.django_db
    @pytest.mark.parametrize("route, message", _unavailable_routes())
    @pytest.mark.parametrize("method", METHODS)
    def test_every_method_answers_400_with_the_way_forward(self, api_key_client, world, route, message, method):
        url = route.format(project=world.alp.id)

        response = getattr(api_key_client, method)(url, {"name": "Epic"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"error": message}

    @pytest.mark.django_db
    def test_malformed_ids_still_get_the_message(self, api_key_client, world):
        for url, message in (
            ("/api/v1/workspaces/test-workspace/pages/not-a-uuid/", WORKSPACE_PAGES_UNAVAILABLE),
            ("/api/v1/workspaces/test-workspace/projects/nope/work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE),
            ("/api/v1/workspaces/test-workspace/projects/nope/work-items/x/pages/", WORK_ITEM_PAGES_UNAVAILABLE),
        ):
            response = api_key_client.get(url)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, url
            assert response.data == {"error": message}

    @pytest.mark.django_db
    def test_messages_do_not_trip_the_connector_gates(self):
        # plane-mcp-server rewrites a 400 mentioning a plan upgrade, or carrying these keys, into its
        # own generic text, which would hide the way forward from the agent.
        for message in (WORKSPACE_PAGES_UNAVAILABLE, WORK_ITEM_PAGES_UNAVAILABLE, WORK_ITEM_TYPES_UNAVAILABLE):
            assert "upgrade your plan" not in message.lower()

    @pytest.mark.django_db
    def test_authentication_still_applies(self, api_client, world):
        response = api_client.get("/api/v1/workspaces/test-workspace/work-item-types/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
