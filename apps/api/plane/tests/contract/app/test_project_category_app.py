# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: PSR-123 -- project categories, ``category_id`` on projects, and the saved Projects List/Grid choice."""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Project,
    ProjectCategory,
    ProjectIdentifier,
    ProjectMember,
    Workspace,
    WorkspaceMember,
)
from plane.tests.factories import UserFactory


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _member(workspace, role):
    user = UserFactory()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=True)
    return user


def _project(workspace, name, identifier, category=None, creator=None, **extra):
    project = Project.objects.create(
        workspace=workspace, name=name, identifier=identifier, category=category, created_by=creator, **extra
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name=identifier)
    return project


@pytest.fixture
def admin_client(create_user, workspace):
    return _client(create_user)  # workspace fixture makes create_user a Super Admin (role 20)


@pytest.fixture
def member_user(workspace):
    return _member(workspace, 15)


@pytest.fixture
def guest_user(workspace):
    return _member(workspace, 5)


@pytest.fixture
def other_workspace(db):
    owner = UserFactory()
    ws = Workspace.objects.create(name="Other", owner=owner, slug="other-ws")
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=20, is_super_admin=True)
    return ws


def _cat_url(workspace, pk=None):
    base = f"/api/workspaces/{workspace.slug}/project-categories/"
    return f"{base}{pk}/" if pk else base


# --- CRUD ----------------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_admin_crud(workspace, admin_client):
    r = admin_client.post(_cat_url(workspace), {"name": "  Web  "}, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["name"] == "Web"
    assert r.data["sort_order"] == 65535
    assert r.data["project_count"] == 0
    cat_id = r.data["id"]

    admin_client.post(_cat_url(workspace), {"name": "Alpha", "sort_order": 10}, format="json")
    r = admin_client.get(_cat_url(workspace))
    assert r.status_code == 200
    assert [c["name"] for c in r.data] == ["Alpha", "Web"]
    assert set(r.data[0]) == {"id", "name", "sort_order", "project_count"}

    r = admin_client.patch(_cat_url(workspace, cat_id), {"name": "Web 2", "sort_order": 1}, format="json")
    assert r.status_code == 200
    assert r.data["name"] == "Web 2" and r.data["sort_order"] == 1

    assert admin_client.delete(_cat_url(workspace, cat_id)).status_code == status.HTTP_204_NO_CONTENT
    assert [c["name"] for c in admin_client.get(_cat_url(workspace)).data] == ["Alpha"]


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize("who", ["member_user", "guest_user"])
def test_non_admin_cannot_write_but_can_read(request, workspace, admin_client, who):
    cat = ProjectCategory.objects.create(workspace=workspace, name="Ops")
    client = _client(request.getfixturevalue(who))
    assert client.post(_cat_url(workspace), {"name": "X"}, format="json").status_code == 403
    assert client.patch(_cat_url(workspace, cat.pk), {"name": "Y"}, format="json").status_code == 403
    assert client.delete(_cat_url(workspace, cat.pk)).status_code == 403
    r = client.get(_cat_url(workspace))
    assert r.status_code == 200 and [c["name"] for c in r.data] == ["Ops"]
    assert ProjectCategory.objects.get(pk=cat.pk).name == "Ops"


@pytest.mark.contract
@pytest.mark.django_db
def test_non_member_cannot_read(workspace):
    assert _client(UserFactory()).get(_cat_url(workspace)).status_code == 403


@pytest.mark.contract
@pytest.mark.django_db
def test_cross_workspace_id_is_404(workspace, other_workspace, admin_client):
    foreign = ProjectCategory.objects.create(workspace=other_workspace, name="Foreign")
    assert admin_client.patch(_cat_url(workspace, foreign.pk), {"name": "x"}, format="json").status_code == 404
    assert admin_client.delete(_cat_url(workspace, foreign.pk)).status_code == 404
    assert ProjectCategory.objects.filter(pk=foreign.pk).exists()
    assert admin_client.get(_cat_url(workspace)).data == []


@pytest.mark.contract
@pytest.mark.django_db
def test_duplicate_name_case_insensitive_and_recreate_after_delete(workspace, other_workspace, admin_client):
    r = admin_client.post(_cat_url(workspace), {"name": "Design"}, format="json")
    cat_id = r.data["id"]
    dup = admin_client.post(_cat_url(workspace), {"name": "dEsign "}, format="json")
    assert dup.status_code == 400 and "already exists" in dup.data["error"]

    other = admin_client.post(_cat_url(workspace), {"name": "Other"}, format="json")
    r = admin_client.patch(_cat_url(workspace, other.data["id"]), {"name": "DESIGN"}, format="json")
    assert r.status_code == 400
    # renaming to its own name with different case is fine
    assert admin_client.patch(_cat_url(workspace, cat_id), {"name": "DESIGN"}, format="json").status_code == 200

    # same name in another workspace is fine
    ProjectCategory.objects.create(workspace=other_workspace, name="Design")

    assert admin_client.delete(_cat_url(workspace, cat_id)).status_code == 204
    assert admin_client.post(_cat_url(workspace), {"name": "Design"}, format="json").status_code == 201


@pytest.mark.contract
@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "a", "sort_order": "z"}]
)
def test_invalid_create_payload(workspace, admin_client, payload):
    assert admin_client.post(_cat_url(workspace), payload, format="json").status_code == 400


@pytest.mark.contract
@pytest.mark.django_db
def test_project_count_ignores_archived_and_deleted(workspace, create_user, admin_client):
    from django.utils import timezone

    cat = ProjectCategory.objects.create(workspace=workspace, name="C")
    _project(workspace, "A", "AAA", cat, create_user)
    _project(workspace, "B", "BBB", cat, create_user, archived_at=timezone.now())
    gone = _project(workspace, "D", "DDD", cat, create_user)
    gone.delete()
    r = admin_client.get(_cat_url(workspace))
    assert r.data[0]["project_count"] == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_unsets_category_on_projects(workspace, create_user, admin_client):
    cat = ProjectCategory.objects.create(workspace=workspace, name="C")
    p1 = _project(workspace, "A", "AAA", cat, create_user)
    from django.utils import timezone

    p2 = _project(workspace, "B", "BBB", cat, create_user, archived_at=timezone.now())
    assert admin_client.delete(_cat_url(workspace, cat.pk)).status_code == 204
    for p in (p1, p2):
        p.refresh_from_db()
        assert p.category_id is None and p.deleted_at is None
    assert Project.objects.filter(pk=p1.pk).exists()


# --- project serializers -------------------------------------------------------------------


def _projects_url(workspace, pk=None):
    base = f"/api/workspaces/{workspace.slug}/projects/"
    return f"{base}{pk}/" if pk else base


@pytest.mark.contract
@pytest.mark.django_db
def test_create_and_update_project_with_category(workspace, other_workspace, admin_client):
    cat = ProjectCategory.objects.create(workspace=workspace, name="C")
    foreign = ProjectCategory.objects.create(workspace=other_workspace, name="F")

    r = admin_client.post(
        _projects_url(workspace), {"name": "Proj1", "identifier": "PRA", "category_id": str(cat.pk)}, format="json"
    )
    assert r.status_code == 201, r.data
    assert str(r.data["category_id"]) == str(cat.pk)
    pid = r.data["id"]

    # default is null
    r2 = admin_client.post(_projects_url(workspace), {"name": "Proj2", "identifier": "PRB"}, format="json")
    assert r2.status_code == 201 and r2.data["category_id"] is None

    # foreign category rejected on create and update
    bad = admin_client.post(
        _projects_url(workspace), {"name": "Proj3", "identifier": "PRC", "category_id": str(foreign.pk)}, format="json"
    )
    assert bad.status_code == 400
    assert not Project.objects.filter(identifier="PRC").exists()
    bad = admin_client.patch(_projects_url(workspace, pid), {"category_id": str(foreign.pk)}, format="json")
    assert bad.status_code == 400
    assert str(Project.objects.get(pk=pid).category_id) == str(cat.pk)

    # clear it
    r = admin_client.patch(_projects_url(workspace, pid), {"category_id": None}, format="json")
    assert r.status_code == 200 and r.data["category_id"] is None


@pytest.mark.contract
@pytest.mark.django_db
def test_member_without_project_admin_cannot_set_category(workspace, member_user, admin_client):
    cat = ProjectCategory.objects.create(workspace=workspace, name="C")
    project = _project(workspace, "P", "PPP")
    ProjectMember.objects.create(project=project, workspace=workspace, member=member_user, role=15, is_active=True)
    r = _client(member_user).patch(_projects_url(workspace, project.pk), {"category_id": str(cat.pk)}, format="json")
    assert r.status_code == 403
    project.refresh_from_db()
    assert project.category_id is None


@pytest.mark.contract
@pytest.mark.django_db
def test_category_id_in_list_and_detail(workspace, create_user, admin_client):
    cat = ProjectCategory.objects.create(workspace=workspace, name="C")
    p = _project(workspace, "P", "PPP", cat, create_user)
    ProjectMember.objects.create(project=p, workspace=workspace, member=create_user, role=20, is_active=True)
    r = admin_client.get(_projects_url(workspace))
    assert r.status_code == 200
    assert str(r.data[0]["category_id"]) == str(cat.pk)
    r = admin_client.get(f"/api/workspaces/{workspace.slug}/projects/details/")
    assert r.status_code == 200 and str(r.data[0]["category_id"]) == str(cat.pk)
    r = admin_client.get(_projects_url(workspace, p.pk))
    assert r.status_code == 200 and str(r.data["category_id"]) == str(cat.pk)


# --- saved List/Grid choice ----------------------------------------------------------------


def _props_url(workspace):
    return f"/api/workspaces/{workspace.slug}/user-properties/"


@pytest.mark.contract
@pytest.mark.django_db
def test_projects_layout_default_roundtrip_invalid_and_isolated(workspace, admin_client, member_user):
    r = admin_client.get(_props_url(workspace))
    assert r.status_code == 200 and r.data["display_filters"]["projects_layout"] == "list"

    r = admin_client.patch(_props_url(workspace), {"display_filters": {"projects_layout": "grid"}}, format="json")
    assert r.status_code == 200 and r.data["display_filters"]["projects_layout"] == "grid"
    assert admin_client.get(_props_url(workspace)).data["display_filters"]["projects_layout"] == "grid"

    # other display filter keys are kept when only projects_layout is sent
    admin_client.patch(_props_url(workspace), {"display_filters": {"layout": "kanban"}}, format="json")
    df = admin_client.get(_props_url(workspace)).data["display_filters"]
    assert df["projects_layout"] == "grid" and df["layout"] == "kanban"

    for bad in ("table", None, 3):
        r = admin_client.patch(_props_url(workspace), {"display_filters": {"projects_layout": bad}}, format="json")
        assert r.status_code == 400
    assert admin_client.get(_props_url(workspace)).data["display_filters"]["projects_layout"] == "grid"

    # another user is unaffected and can't touch it
    other = _client(member_user)
    assert other.get(_props_url(workspace)).data["display_filters"]["projects_layout"] == "list"
    other.patch(_props_url(workspace), {"display_filters": {"projects_layout": "grid"}}, format="json")
    admin_client.patch(_props_url(workspace), {"display_filters": {"projects_layout": "list"}}, format="json")
    assert other.get(_props_url(workspace)).data["display_filters"]["projects_layout"] == "grid"
