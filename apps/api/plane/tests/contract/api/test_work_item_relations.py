# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for work item dependencies, custom relations and relation definitions (FORK: PSR-83, PSR-84).

These are the endpoints the stock Plane MCP connector's ``workitem_relation`` tool calls:
``.../work-items/<id>/dependencies/``, ``.../work-items/<id>/work-item-relations/`` and
``workspaces/<slug>/work-item-relation-definitions/``.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.issue_activities_task import issue_activity as real_issue_activity
from plane.db.models import (
    APIToken,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueLabel,
    IssueRelation,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.utils.issue_relation_mapper import get_inverse_relation

DEPENDENCY_TYPES = ["blocking", "blocked_by", "start_before", "start_after", "finish_before", "finish_after"]
REVERSED = {"blocking", "start_after", "finish_after"}
ITEM_FIELDS = {
    "id",
    "name",
    "sequence_id",
    "project_id",
    "state_id",
    "priority",
    "type_id",
    "is_epic",
    "label_ids",
    "assignee_ids",
    "sort_order",
    "created_at",
    "relation_type",
}
READ_ONLY_ERROR = (
    "Custom relation definitions are not available on this Plane edition. Use the built-in definitions "
    "from list_definitions, or a relation_type for dependencies."
)


@pytest.fixture(autouse=True)
def sync_activity(monkeypatch):
    """Run the activity task inline (notifications off) so tests can assert on IssueActivity rows."""
    mock = MagicMock()
    mock.delay.side_effect = lambda **kwargs: real_issue_activity(**{**kwargs, "notification": False})
    monkeypatch.setattr("plane.api.views.work_item_relations.issue_activity", mock)
    return mock


def _issue(name, project, creator):
    state = State.objects.filter(project=project).first()
    issue = Issue.objects.create(name=name, project=project, workspace=project.workspace, state=state)
    Issue.objects.filter(id=issue.id).update(created_by=creator)
    issue.refresh_from_db()
    return issue


def _client_for(user, token):
    APIToken.objects.create(user=user, label=f"token-{token}", token=token)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token)
    return client


@pytest.fixture
def world(db, workspace, create_user):
    """Caller = ``create_user`` (workspace admin).

    test-workspace:
      ALP (caller admin, guest user is a guest): a1, a2 (label + live assignee + deleted assignee), a3
      BET (caller member): b1
      GAM (caller NOT a member): g1 "secret gamma"
      GST (caller guest, guest_view_all_features=False): s1 created by u2 (hidden from caller)
    other-ws (caller is a member there too): OTH project, o1
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
        State.objects.create(name="Backlog", group="backlog", default=True, project=p, workspace=ws)
        return p

    alp = project("Alpha", "ALP", workspace, role=20)
    ProjectMember.objects.create(project=alp, member=guest, role=5, is_active=True)
    Project.objects.filter(id=alp.id).update(guest_view_all_features=True)
    bet = project("Beta", "BET", workspace, role=15)
    gam = project("Gamma", "GAM", workspace)
    gst = project("Guest", "GST", workspace, role=5)

    other_ws = Workspace.objects.create(name="Other", owner=me, slug="other-ws")
    WorkspaceMember.objects.create(workspace=other_ws, member=me, role=20)
    oth = project("Other", "OTH", other_ws, role=20)

    a1 = _issue("alpha one", alp, me)
    a2 = _issue("alpha two", alp, me)
    a3 = _issue("alpha three", alp, me)
    label = Label.objects.create(name="bug", project=alp, workspace=workspace)
    IssueLabel.objects.create(issue=a2, label=label, project=alp, workspace=workspace)
    IssueAssignee.objects.create(issue=a2, assignee=me, project=alp, workspace=workspace)
    IssueAssignee.objects.create(issue=a2, assignee=u2, project=alp, workspace=workspace)
    IssueAssignee.objects.filter(issue=a2, assignee=u2).update(deleted_at=timezone.now())

    return SimpleNamespace(
        ws=workspace,
        other_ws=other_ws,
        me=me,
        u2=u2,
        guest=guest,
        alp=alp,
        bet=bet,
        a1=a1,
        a2=a2,
        a3=a3,
        b1=_issue("beta one", bet, me),
        g1=_issue("secret gamma", gam, me),
        s1=_issue("secret guest", gst, u2),
        o1=_issue("other one", oth, me),
        label=label,
    )


def dep_url(issue, related=None, project=None):
    base = (
        f"/api/v1/workspaces/test-workspace/projects/{project.id if project else issue.project_id}"
        f"/work-items/{issue.id}/dependencies/"
    )
    return f"{base}{related.id}/" if related else base


def rel_url(issue, related=None):
    base = f"/api/v1/workspaces/test-workspace/projects/{issue.project_id}/work-items/{issue.id}/work-item-relations/"
    return f"{base}{related.id}/" if related else base


def defs_url(slug="test-workspace", pk=None):
    base = f"/api/v1/workspaces/{slug}/work-item-relation-definitions/"
    return f"{base}{pk}/" if pk else base


def _pair(a, b):
    return Q(issue=a, related_issue=b) | Q(issue=b, related_issue=a)


def _ids(items):
    return [item["id"] for item in items]


def _definitions(client, slug="test-workspace", **params):
    response = client.get(defs_url(slug), params)
    assert response.status_code == status.HTTP_200_OK, response.data
    return {d["name"]: d for d in response.data["results"]}


def _link_dep(client, src, relation_type, *targets):
    return client.post(
        dep_url(src), {"relation_type": relation_type, "work_item_ids": [str(t.id) for t in targets]}, format="json"
    )


def _link_custom(client, src, definition_id, label, *targets):
    return client.post(
        rel_url(src),
        {
            "relation_definition_id": definition_id,
            "relation_definition_type": label,
            "work_item_ids": [str(t.id) for t in targets],
        },
        format="json",
    )


@pytest.mark.contract
class TestDependencies:
    @pytest.mark.django_db
    @pytest.mark.parametrize("relation_type", DEPENDENCY_TYPES)
    def test_round_trip(self, api_key_client, world, relation_type):
        a1, a2 = world.a1, world.a2
        inverse = get_inverse_relation(relation_type)

        created = _link_dep(api_key_client, a1, relation_type, a2)
        assert created.status_code == status.HTTP_201_CREATED, created.data
        assert _ids(created.data) == [str(a2.id)]
        assert created.data[0]["relation_type"] == relation_type

        # Stored once, in the canonical direction/type the app uses.
        row = IssueRelation.objects.get(_pair(a1, a2))
        if relation_type in REVERSED:
            assert (row.issue_id, row.related_issue_id, row.relation_type) == (a2.id, a1.id, inverse)
        else:
            assert (row.issue_id, row.related_issue_id, row.relation_type) == (a1.id, a2.id, relation_type)

        mine = api_key_client.get(dep_url(a1)).data
        theirs = api_key_client.get(dep_url(a2)).data
        assert set(mine) == set(DEPENDENCY_TYPES) == set(theirs)
        assert _ids(mine[relation_type]) == [str(a2.id)]
        assert mine[relation_type][0]["relation_type"] == relation_type
        assert _ids(theirs[inverse]) == [str(a1.id)]
        assert theirs[inverse][0]["relation_type"] == inverse
        assert sum(len(v) for v in mine.values()) == 1 == sum(len(v) for v in theirs.values())

        # Also visible through the existing /relations/ endpoint.
        legacy = api_key_client.get(
            f"/api/v1/workspaces/test-workspace/projects/{world.alp.id}/work-items/{a1.id}/relations/"
        ).data
        assert [r["issue_id"] for r in legacy[relation_type]] == [str(a2.id)]

        # Remove it from the other side: gone for both, soft-deleted.
        removed = api_key_client.delete(dep_url(a2, a1))
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        assert not any(api_key_client.get(dep_url(a1)).data.values())
        assert not any(api_key_client.get(dep_url(a2)).data.values())
        assert not IssueRelation.objects.filter(_pair(a1, a2)).exists()
        assert IssueRelation.all_objects.filter(_pair(a1, a2), deleted_at__isnull=False).count() == 1

    @pytest.mark.django_db
    def test_item_shape(self, api_key_client, world):
        response = _link_dep(api_key_client, world.a1, "blocked_by", world.a2)
        item = response.data[0]
        assert ITEM_FIELDS <= set(item)
        assert item["name"] == "alpha two"
        assert item["sequence_id"] == world.a2.sequence_id
        assert item["project_id"] == str(world.alp.id)
        assert item["state_id"] == str(world.a2.state_id)
        assert item["is_epic"] is False
        assert item["type_id"] is None
        assert item["label_ids"] == [str(world.label.id)]
        assert item["assignee_ids"] == [str(world.me.id)]  # the soft-deleted assignee is excluded
        listed = api_key_client.get(dep_url(world.a1)).data["blocked_by"][0]
        assert ITEM_FIELDS <= set(listed) and listed["assignee_ids"] == [str(world.me.id)]

    @pytest.mark.django_db
    def test_multiple_targets_and_cross_project(self, api_key_client, world):
        response = _link_dep(api_key_client, world.a1, "blocking", world.a2, world.b1)
        assert response.status_code == status.HTTP_201_CREATED
        assert sorted(_ids(response.data)) == sorted([str(world.a2.id), str(world.b1.id)])
        assert _ids(api_key_client.get(dep_url(world.b1)).data["blocked_by"]) == [str(world.a1.id)]

    @pytest.mark.django_db
    def test_activity_logged_for_create_and_delete(self, api_key_client, world):
        a1, a2 = world.a1, world.a2
        _link_dep(api_key_client, a1, "blocked_by", a2)
        created = IssueActivity.objects.filter(verb="updated", field__in=["blocked_by", "blocking"])
        assert set(created.values_list("issue_id", "field")) == {(a1.id, "blocked_by"), (a2.id, "blocking")}
        assert created.filter(issue=a1, actor=world.me, new_value=f"ALP-{a2.sequence_id}").exists()

        api_key_client.delete(dep_url(a1, a2))
        deleted = IssueActivity.objects.filter(verb="deleted")
        assert set(deleted.values_list("issue_id", flat=True)) == {a1.id, a2.id}
        assert deleted.filter(issue=a1, field="blocked_by").exists()

    @pytest.mark.django_db
    def test_bad_requests(self, api_key_client, world):
        a1 = world.a1
        assert _link_dep(api_key_client, a1, "relates_to", world.a2).status_code == status.HTTP_400_BAD_REQUEST
        missing_ids = api_key_client.post(dep_url(a1), {"relation_type": "blocking"}, format="json")
        assert missing_ids.status_code == status.HTTP_400_BAD_REQUEST
        bad_uuid = api_key_client.post(
            dep_url(a1), {"relation_type": "blocking", "work_item_ids": ["nope"]}, format="json"
        )
        assert bad_uuid.status_code == status.HTTP_400_BAD_REQUEST and "nope" in bad_uuid.data["error"]

    @pytest.mark.django_db
    def test_self_link_rejected(self, api_key_client, world):
        response = _link_dep(api_key_client, world.a1, "blocked_by", world.a1)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "itself" in response.data["error"]
        assert not IssueRelation.objects.exists()

    @pytest.mark.django_db
    @pytest.mark.parametrize("target_attr", ["g1", "s1", "o1"])
    def test_inaccessible_target_rejected_without_leak(self, api_key_client, world, target_attr):
        """g1: project caller isn't in; s1: hidden from caller as a guest; o1: another workspace."""
        target = getattr(world, target_attr)
        response = _link_dep(api_key_client, world.a1, "blocked_by", world.a3, target)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert str(target.id) in response.data["error"]
        assert target.name not in str(response.data)
        assert not IssueRelation.objects.exists()  # all-or-nothing: a3 not linked either

    @pytest.mark.django_db
    def test_hidden_items_not_listed(self, api_key_client, world):
        IssueRelation.objects.create(
            issue=world.a1, related_issue=world.g1, relation_type="blocked_by", project=world.alp, workspace=world.ws
        )
        assert api_key_client.get(dep_url(world.a1)).data["blocked_by"] == []

    @pytest.mark.django_db
    def test_conflicts_and_idempotent_repeat(self, api_key_client, world, sync_activity):
        a1, a2, a3 = world.a1, world.a2, world.a3
        assert _link_dep(api_key_client, a1, "blocked_by", a2).status_code == status.HTTP_201_CREATED
        calls = sync_activity.delay.call_count

        # Exact repeat, from either side, is idempotent: 200, the item, nothing new stored or logged.
        for src, rel, tgt in ((a1, "blocked_by", a2), (a2, "blocking", a1)):
            repeat = _link_dep(api_key_client, src, rel, tgt)
            assert repeat.status_code == status.HTTP_200_OK
            assert _ids(repeat.data) == [str(tgt.id)] and repeat.data[0]["relation_type"] == rel
        assert IssueRelation.objects.count() == 1
        assert sync_activity.delay.call_count == calls

        # Any other link between the pair is a conflict, in either direction; nothing partial is created.
        clash = _link_dep(api_key_client, a1, "start_before", a3, a2)
        assert clash.status_code == status.HTTP_409_CONFLICT
        assert "'blocked_by'" in clash.data["error"] and "remove that link first" in clash.data["error"]
        reverse = _link_dep(api_key_client, a2, "blocked_by", a1)
        assert reverse.status_code == status.HTTP_409_CONFLICT and "'blocking'" in reverse.data["error"]
        defs = _definitions(api_key_client)
        custom = _link_custom(api_key_client, a1, defs["Relates to"]["id"], "relates to", a2)
        assert custom.status_code == status.HTTP_409_CONFLICT
        assert IssueRelation.objects.count() == 1

    @pytest.mark.django_db
    def test_delete_missing(self, api_key_client, world):
        assert api_key_client.delete(dep_url(world.a1, world.a2)).status_code == status.HTTP_404_NOT_FOUND
        unknown = SimpleNamespace(id=uuid.uuid4(), project_id=world.alp.id)
        assert api_key_client.delete(dep_url(unknown, world.a2)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_source_must_belong_to_project(self, api_key_client, world):
        assert api_key_client.get(dep_url(world.a1, project=world.bet)).status_code == status.HTTP_404_NOT_FOUND
        response = api_key_client.post(
            dep_url(world.a1, project=world.bet),
            {"relation_type": "blocking", "work_item_ids": [str(world.b1.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_guest_can_read_not_write(self, api_key_client, world):
        _link_dep(api_key_client, world.a1, "blocked_by", world.a2)
        guest = _client_for(world.guest, "guest-api-token")
        listed = guest.get(dep_url(world.a1))
        assert listed.status_code == status.HTTP_200_OK and _ids(listed.data["blocked_by"]) == [str(world.a2.id)]
        assert guest.get(rel_url(world.a1)).status_code == status.HTTP_200_OK
        assert _link_dep(guest, world.a1, "blocked_by", world.a3).status_code == status.HTTP_403_FORBIDDEN
        assert guest.delete(dep_url(world.a1, world.a2)).status_code == status.HTTP_403_FORBIDDEN
        defs = _definitions(guest)
        assert (
            _link_custom(guest, world.a1, defs["Duplicate"]["id"], "duplicate of", world.a3).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert guest.delete(rel_url(world.a1, world.a2)).status_code == status.HTTP_403_FORBIDDEN
        assert IssueRelation.objects.count() == 1

    @pytest.mark.django_db
    def test_non_member_cannot_read(self, world):
        outsider = User.objects.create(email="out@plane.so", username="out-user")
        WorkspaceMember.objects.create(workspace=world.ws, member=outsider, role=15)
        client = _client_for(outsider, "outsider-api-token")
        assert client.get(dep_url(world.a1)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
class TestCustomRelations:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "definition,label,source_sees,target_sees,stored",
        [
            ("Relates to", "relates to", "relates to", "relates to", ("a1", "a2", "relates_to")),
            ("Duplicate", "duplicate of", "duplicate of", "duplicate of", ("a1", "a2", "duplicate")),
        ],
    )
    def test_round_trip(self, api_key_client, world, definition, label, source_sees, target_sees, stored):
        a1, a2 = world.a1, world.a2
        definition_id = _definitions(api_key_client)[definition]["id"]

        created = _link_custom(api_key_client, a1, definition_id, label, a2)
        assert created.status_code == status.HTTP_201_CREATED, created.data
        assert _ids(created.data) == [str(a2.id)] and created.data[0]["relation_type"] == source_sees
        assert ITEM_FIELDS <= set(created.data[0])

        row = IssueRelation.objects.get(_pair(a1, a2))
        issue_attr, related_attr, stored_type = stored
        assert (row.issue_id, row.related_issue_id, row.relation_type) == (
            getattr(world, issue_attr).id,
            getattr(world, related_attr).id,
            stored_type,
        )

        mine = api_key_client.get(rel_url(a1)).data
        theirs = api_key_client.get(rel_url(a2)).data
        assert list(mine) == [source_sees] and _ids(mine[source_sees]) == [str(a2.id)]
        assert list(theirs) == [target_sees] and _ids(theirs[target_sees]) == [str(a1.id)]
        assert theirs[target_sees][0]["relation_type"] == target_sees
        # Custom relations are not dependencies.
        assert not any(api_key_client.get(dep_url(a1)).data.values())
        assert api_key_client.delete(dep_url(a1, a2)).status_code == status.HTTP_404_NOT_FOUND

        removed = api_key_client.delete(rel_url(a2, a1))
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        assert api_key_client.get(rel_url(a1)).data == {} == api_key_client.get(rel_url(a2)).data
        assert IssueActivity.objects.filter(verb="deleted", issue=a2).exists()

    @pytest.mark.django_db
    def test_empty_list_and_delete_missing(self, api_key_client, world):
        assert api_key_client.get(rel_url(world.a1)).data == {}
        assert api_key_client.delete(rel_url(world.a1, world.a2)).status_code == status.HTTP_404_NOT_FOUND
        _link_dep(api_key_client, world.a1, "blocked_by", world.a2)
        # A dependency is not a custom relation.
        assert api_key_client.delete(rel_url(world.a1, world.a2)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.django_db
    def test_unknown_definition_or_label(self, api_key_client, world):
        defs = _definitions(api_key_client)
        unknown = _link_custom(api_key_client, world.a1, str(uuid.uuid4()), "relates to", world.a2)
        assert unknown.status_code == status.HTTP_400_BAD_REQUEST
        for name, definition in defs.items():
            assert name in unknown.data["error"] and definition["id"] in unknown.data["error"]

        wrong_label = _link_custom(api_key_client, world.a1, defs["Duplicate"]["id"], "relates to", world.a2)
        assert wrong_label.status_code == status.HTTP_400_BAD_REQUEST
        assert "'duplicate of'" in wrong_label.data["error"]
        # "Implements" is not offered on this edition; its labels are just unknown labels.
        for label in ("implements", "implemented by"):
            removed = _link_custom(api_key_client, world.a1, defs["Relates to"]["id"], label, world.a2)
            assert removed.status_code == status.HTTP_400_BAD_REQUEST
            assert "valid labels: 'relates to'" in removed.data["error"]

        # Another workspace's definition id is unknown here.
        foreign = _definitions(api_key_client, "other-ws")["Relates to"]["id"]
        foreign_resp = _link_custom(api_key_client, world.a1, foreign, "relates to", world.a2)
        assert foreign_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not IssueRelation.objects.exists()

    @pytest.mark.django_db
    def test_conflict_with_other_custom_type(self, api_key_client, world):
        defs = _definitions(api_key_client)
        _link_custom(api_key_client, world.a1, defs["Relates to"]["id"], "relates to", world.a2)
        clash = _link_custom(api_key_client, world.a1, defs["Duplicate"]["id"], "duplicate of", world.a2)
        assert clash.status_code == status.HTTP_409_CONFLICT and "'relates to'" in clash.data["error"]
        dependency = _link_dep(api_key_client, world.a2, "blocking", world.a1)
        assert dependency.status_code == status.HTTP_409_CONFLICT and "'relates_to'" in dependency.data["error"]
        # Symmetric type: repeating it from the other side is the same link, so idempotent.
        repeat = _link_custom(api_key_client, world.a2, defs["Relates to"]["id"], "relates to", world.a1)
        assert repeat.status_code == status.HTTP_200_OK
        assert _ids(repeat.data) == [str(world.a1.id)] and repeat.data[0]["relation_type"] == "relates to"
        assert IssueRelation.objects.count() == 1

    @pytest.mark.django_db
    def test_existing_implemented_by_rows_ignored(self, api_key_client, world):
        """implemented_by is not exposed (the UI never shows it); legacy rows are skipped, not crashed on."""
        IssueRelation.objects.create(
            issue=world.a1,
            related_issue=world.a2,
            relation_type="implemented_by",
            project=world.alp,
            workspace=world.ws,
        )
        for issue in (world.a1, world.a2):
            listed = api_key_client.get(rel_url(issue))
            assert listed.status_code == status.HTTP_200_OK and listed.data == {}
            assert not any(api_key_client.get(dep_url(issue)).data.values())
        # It still occupies the pair, so a new link is refused rather than silently stacked.
        defs = _definitions(api_key_client)
        clash = _link_custom(api_key_client, world.a1, defs["Relates to"]["id"], "relates to", world.a2)
        assert clash.status_code == status.HTTP_409_CONFLICT


@pytest.mark.contract
class TestRelationDefinitions:
    @pytest.mark.django_db
    def test_list_built_ins(self, api_key_client, world):
        response = api_key_client.get(defs_url())
        assert response.status_code == status.HTTP_200_OK
        body = response.data
        for key in (
            "total_count",
            "next_cursor",
            "prev_cursor",
            "next_page_results",
            "prev_page_results",
            "count",
            "total_pages",
            "total_results",
        ):
            assert key in body
        assert body["total_results"] == 2 == body["count"] and body["next_page_results"] is False
        defs = {d["name"]: d for d in body["results"]}
        assert {name: (d["outward"], d["inward"]) for name, d in defs.items()} == {
            "Relates to": ("relates to", "relates to"),
            "Duplicate": ("duplicate of", "duplicate of"),
        }
        for d in defs.values():
            assert d["is_default"] is True and d["is_active"] is True
            assert d["workspace"] == str(world.ws.id)
            uuid.UUID(d["id"])

    @pytest.mark.django_db
    def test_ids_stable_and_per_workspace(self, api_key_client, world):
        first = {n: d["id"] for n, d in _definitions(api_key_client).items()}
        second = {n: d["id"] for n, d in _definitions(api_key_client).items()}
        other = {n: d["id"] for n, d in _definitions(api_key_client, "other-ws").items()}
        assert first == second
        assert not set(first.values()) & set(other.values())

    @pytest.mark.django_db
    def test_filters(self, api_key_client, world):
        assert len(_definitions(api_key_client, is_default="true", is_active="true")) == 2
        assert _definitions(api_key_client, is_default="false") == {}
        assert _definitions(api_key_client, is_active="false") == {}
        assert api_key_client.get(defs_url(), {"is_active": "maybe"}).status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    def test_pagination(self, api_key_client, world):
        page1 = api_key_client.get(defs_url(), {"per_page": 1}).data
        assert page1["count"] == 1 and page1["next_page_results"] is True and page1["prev_page_results"] is False
        assert page1["total_pages"] == 2
        page2 = api_key_client.get(defs_url(), {"per_page": 1, "cursor": page1["next_cursor"]}).data
        assert page2["count"] == 1 and page2["next_page_results"] is False and page2["prev_page_results"] is True
        names = [d["name"] for d in page1["results"] + page2["results"]]
        assert sorted(names) == ["Duplicate", "Relates to"]
        assert api_key_client.get(defs_url(), {"cursor": "garbage"}).status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    def test_writes_rejected(self, api_key_client, world):
        existing = next(iter(_definitions(api_key_client).values()))["id"]
        responses = [
            api_key_client.post(defs_url(), {"name": "Causes", "outward": "causes"}, format="json"),
            api_key_client.patch(defs_url(pk=existing), {"name": "Renamed"}, format="json"),
            api_key_client.delete(defs_url(pk=existing)),
            api_key_client.delete(defs_url(pk=uuid.uuid4())),
        ]
        for response in responses:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.data == {"error": READ_ONLY_ERROR}
        assert len(_definitions(api_key_client)) == 2

    @pytest.mark.django_db
    def test_non_member_forbidden(self, world):
        outsider = User.objects.create(email="out2@plane.so", username="out2-user")
        client = _client_for(outsider, "outsider2-api-token")
        assert client.get(defs_url()).status_code == status.HTTP_403_FORBIDDEN
