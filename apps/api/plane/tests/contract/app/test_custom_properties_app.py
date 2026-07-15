# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the fork's custom-properties feature (plane.properties).

Pattern mirrors test_time_tracking_app.py. The instance kill switch is an env
var read live by plane.properties.flags.is_instance_enabled, so tests flip it
with monkeypatch.setenv/delenv.
"""

import uuid

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectIdentifier, ProjectMember, User
from plane.properties.models import (
    IssueProperty,
    IssuePropertyOption,
    IssuePropertyValue,
    ProjectPropertiesFeature,
)


@pytest.fixture
def proj(db, create_user, workspace):
    """A project with the test user (session_client's user) as project admin."""
    project = Project.objects.create(
        workspace=workspace, name="CP Project", identifier="CPP", created_by=create_user
    )
    ProjectIdentifier.objects.create(workspace=workspace, project=project, name="CPP")
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return {"project": project, "workspace": workspace, "user": create_user}


def _member(workspace, project, role=15):
    uid = uuid.uuid4().hex[:8]
    u = User.objects.create(email=f"cp-{uid}@plane.so", username=f"cp_{uid}", first_name="Mem")
    u.set_password("x")
    u.save()
    ProjectMember.objects.create(project=project, workspace=workspace, member=u, role=role, is_active=True)
    return u


def _feature_url(slug, pid):
    return f"/api/workspaces/{slug}/projects/{pid}/properties-feature/"


def _props_url(slug, pid):
    return f"/api/workspaces/{slug}/projects/{pid}/work-item-properties/"


def _prop_url(slug, pid, prop_id):
    return f"{_props_url(slug, pid)}{prop_id}/"


def _options_url(slug, pid, prop_id):
    return f"{_prop_url(slug, pid, prop_id)}options/"


def _option_url(slug, pid, prop_id, opt_id):
    return f"{_options_url(slug, pid, prop_id)}{opt_id}/"


def _values_url(slug, pid, iid, prop_id):
    return (
        f"/api/workspaces/{slug}/projects/{pid}/work-items/{iid}"
        f"/work-item-properties/{prop_id}/values/"
    )


def _bulk_url(slug, pid):
    return f"/api/workspaces/{slug}/projects/{pid}/work-item-property-values/"


@pytest.fixture
def enabled(proj, monkeypatch):
    """proj with the feature fully on: instance kill switch + per-project toggle."""
    monkeypatch.setenv("CUSTOM_PROPERTIES_ENABLED", "1")
    ProjectPropertiesFeature.objects.create(
        project=proj["project"], workspace=proj["workspace"], is_enabled=True
    )
    return proj


def _make_issue(proj, name="I"):
    from plane.db.models import Issue, State

    state, _ = State.objects.get_or_create(
        workspace=proj["workspace"],
        project=proj["project"],
        name="Todo",
        defaults={"group": "unstarted", "default": True},
    )
    issue = Issue(workspace=proj["workspace"], project=proj["project"], name=name, state=state)
    issue.save(created_by_id=proj["user"].id)
    return issue


@pytest.mark.contract
@pytest.mark.django_db
class TestPropertiesModels:
    def test_option_property_smoke(self, proj):
        # Models + relations + JSON color in logo_props round-trip.
        prop = IssueProperty.objects.create(
            workspace=proj["workspace"],
            project=proj["project"],
            name="status",
            display_name="Status",
            property_type="OPTION",
        )
        opt = IssuePropertyOption.objects.create(
            workspace=proj["workspace"],
            project=proj["project"],
            property=prop,
            name="Done",
            is_default=True,
            logo_props={"in_use": "color", "color": {"name": "green", "background": "#00C875"}},
        )
        assert prop.options.count() == 1
        assert opt.is_default is True
        assert opt.logo_props["color"]["background"] == "#00C875"

    def test_value_row_links(self, proj):
        prop = IssueProperty.objects.create(
            workspace=proj["workspace"], project=proj["project"],
            name="stage", display_name="Stage", property_type="OPTION",
        )
        opt = IssuePropertyOption.objects.create(
            workspace=proj["workspace"], project=proj["project"], property=prop, name="Won"
        )
        from plane.db.models import Issue, State

        state = State.objects.create(
            workspace=proj["workspace"], project=proj["project"], name="Todo", group="unstarted", default=True
        )
        issue = Issue(workspace=proj["workspace"], project=proj["project"], name="I1", state=state)
        issue.save(created_by_id=proj["user"].id)
        val = IssuePropertyValue.objects.create(
            workspace=proj["workspace"], project=proj["project"],
            issue=issue, property=prop, value_option=opt,
        )
        assert val.value_option_id == opt.id
        assert issue.property_values.count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestPropertiesFeatureToggle:
    def test_default_disabled(self, session_client, proj):
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        r = session_client.get(_feature_url(slug, pid))
        assert r.status_code == status.HTTP_200_OK
        # No row + instance off ⇒ disabled.
        assert r.data["is_enabled"] is False

    def test_admin_toggles_when_instance_on(self, session_client, proj, monkeypatch):
        monkeypatch.setenv("CUSTOM_PROPERTIES_ENABLED", "1")
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        r = session_client.patch(_feature_url(slug, pid), {"is_enabled": True}, format="json")
        assert r.status_code == status.HTTP_200_OK
        assert r.data["is_enabled"] is True
        assert r.data["instance_enabled"] is True
        assert ProjectPropertiesFeature.objects.get(project_id=pid).is_enabled is True
        # Read it back.
        assert session_client.get(_feature_url(slug, pid)).data["is_enabled"] is True

    def test_patch_forbidden_when_instance_off(self, session_client, proj, monkeypatch):
        monkeypatch.delenv("CUSTOM_PROPERTIES_ENABLED", raising=False)
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        r = session_client.patch(_feature_url(slug, pid), {"is_enabled": True}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert not ProjectPropertiesFeature.objects.filter(project_id=pid).exists()

    def test_stored_toggle_masked_by_instance_switch(self, session_client, proj, monkeypatch):
        # Enable per-project while instance is on...
        monkeypatch.setenv("CUSTOM_PROPERTIES_ENABLED", "1")
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        session_client.patch(_feature_url(slug, pid), {"is_enabled": True}, format="json")
        # ...then the operator turns the instance off: GET reports disabled.
        monkeypatch.delenv("CUSTOM_PROPERTIES_ENABLED", raising=False)
        r = session_client.get(_feature_url(slug, pid))
        assert r.data["is_enabled"] is False
        assert r.data["instance_enabled"] is False

    def test_member_cannot_toggle(self, session_client, proj, monkeypatch):
        monkeypatch.setenv("CUSTOM_PROPERTIES_ENABLED", "1")
        member = _member(proj["workspace"], proj["project"])
        session_client.force_authenticate(user=member)
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        r = session_client.patch(_feature_url(slug, pid), {"is_enabled": True}, format="json")
        assert r.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestPropertiesCRUD:
    def _create_status(self, client, slug, pid, options=None):
        body = {
            "display_name": "Deal stage",
            "property_type": "OPTION",
            "options": options
            or [
                {"name": "Lead", "logo_props": {"in_use": "color", "color": {"background": "#579BFC"}}},
                {"name": "Won", "is_default": True, "logo_props": {"in_use": "color", "color": {"background": "#00C875"}}},
                {"name": "Lost", "logo_props": {"in_use": "color", "color": {"background": "#E2445C"}}},
            ],
        }
        return client.post(_props_url(slug, pid), body, format="json")

    def test_create_status_with_options(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        r = self._create_status(session_client, slug, pid)
        assert r.status_code == status.HTTP_201_CREATED
        assert r.data["property_type"] == "OPTION"
        assert r.data["name"] == "deal-stage"
        assert len(r.data["options"]) == 3
        won = next(o for o in r.data["options"] if o["name"] == "Won")
        assert won["is_default"] is True
        assert won["logo_props"]["color"]["background"] == "#00C875"

    def test_non_option_type_rejected(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        r = session_client.post(
            _props_url(slug, pid), {"display_name": "Notes", "property_type": "TEXT"}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_and_get_single(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        lst = session_client.get(_props_url(slug, pid))
        assert lst.status_code == 200 and len(lst.data) == 1
        single = session_client.get(_prop_url(slug, pid, pr["id"]))
        assert single.status_code == 200 and single.data["id"] == pr["id"]

    def test_patch_property_reslugs(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        r = session_client.patch(_prop_url(slug, pid, pr["id"]), {"display_name": "Stage"}, format="json")
        assert r.status_code == 200
        assert r.data["display_name"] == "Stage" and r.data["name"] == "stage"

    def test_delete_cascades_options(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        opt_id = pr["options"][0]["id"]
        r = session_client.delete(_prop_url(slug, pid, pr["id"]))
        assert r.status_code == status.HTTP_204_NO_CONTENT
        # soft-deleted -> excluded by the default manager
        assert not IssueProperty.objects.filter(pk=pr["id"]).exists()
        assert not IssuePropertyOption.objects.filter(pk=opt_id).exists()

    def test_option_crud(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        add = session_client.post(
            _options_url(slug, pid, pr["id"]),
            {"name": "Negotiation", "logo_props": {"color": {"background": "#A25DDC"}}},
            format="json",
        )
        assert add.status_code == status.HTTP_201_CREATED
        oid = add.data["id"]
        patched = session_client.patch(
            _option_url(slug, pid, pr["id"], oid), {"name": "Negotiating"}, format="json"
        )
        assert patched.status_code == 200 and patched.data["name"] == "Negotiating"
        assert len(session_client.get(_options_url(slug, pid, pr["id"])).data) == 4
        assert session_client.delete(_option_url(slug, pid, pr["id"], oid)).status_code == 204
        assert len(session_client.get(_options_url(slug, pid, pr["id"])).data) == 3

    def test_values_replace_semantics(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        # DRF response.data holds ids as UUID objects; our endpoints return them
        # as strings — normalise to str for comparison.
        opts = {o["name"]: str(o["id"]) for o in pr["options"]}
        issue = _make_issue(enabled)
        iid = str(issue.id)
        r1 = session_client.post(_values_url(slug, pid, iid, pr["id"]), {"values": [opts["Lead"]]}, format="json")
        assert r1.status_code == 200 and r1.data["values"] == [opts["Lead"]]
        r2 = session_client.post(_values_url(slug, pid, iid, pr["id"]), {"values": [opts["Won"]]}, format="json")
        assert r2.data["values"] == [opts["Won"]]
        assert session_client.get(_values_url(slug, pid, iid, pr["id"])).data["values"] == [opts["Won"]]
        # replace = exactly one live row
        assert IssuePropertyValue.objects.filter(issue=issue, property_id=pr["id"]).count() == 1

    def test_single_select_rejects_multiple(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        pr = self._create_status(session_client, slug, pid).data
        opts = [o["id"] for o in pr["options"]]
        issue = _make_issue(enabled)
        r = session_client.post(
            _values_url(slug, pid, str(issue.id), pr["id"]), {"values": opts[:2]}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_cross_property_option_rejected(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        a = self._create_status(session_client, slug, pid).data
        b = self._create_status(session_client, slug, pid, options=[{"name": "X"}]).data
        issue = _make_issue(enabled)
        r = session_client.post(
            _values_url(slug, pid, str(issue.id), a["id"]),
            {"values": [b["options"][0]["id"]]},
            format="json",
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_values(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        p1 = self._create_status(session_client, slug, pid).data
        p2 = self._create_status(session_client, slug, pid, options=[{"name": "A"}, {"name": "B"}]).data
        issues = [_make_issue(enabled, f"I{i}") for i in range(3)]
        session_client.post(
            _values_url(slug, pid, str(issues[0].id), p1["id"]),
            {"values": [p1["options"][0]["id"]]},
            format="json",
        )
        session_client.post(
            _values_url(slug, pid, str(issues[1].id), p2["id"]),
            {"values": [p2["options"][0]["id"]]},
            format="json",
        )
        ids = ",".join(str(i.id) for i in issues)
        r = session_client.get(_bulk_url(slug, pid) + f"?work_item_ids={ids}")
        assert r.status_code == 200
        assert r.data[str(issues[0].id)][str(p1["id"])] == [str(p1["options"][0]["id"])]
        assert r.data[str(issues[1].id)][str(p2["id"])] == [str(p2["options"][0]["id"])]
        assert str(issues[2].id) not in r.data  # no values -> absent

    def test_disabled_project_list_empty_writes_403(self, session_client, proj, monkeypatch):
        # instance off (no env) -> list empty 200, create 403
        monkeypatch.delenv("CUSTOM_PROPERTIES_ENABLED", raising=False)
        slug, pid = proj["workspace"].slug, str(proj["project"].id)
        lst = session_client.get(_props_url(slug, pid))
        assert lst.status_code == 200 and lst.data == []
        r = session_client.post(
            _props_url(slug, pid), {"display_name": "S", "property_type": "OPTION"}, format="json"
        )
        assert r.status_code == status.HTTP_403_FORBIDDEN

    def test_member_cannot_create_property(self, session_client, enabled):
        slug, pid = enabled["workspace"].slug, str(enabled["project"].id)
        member = _member(enabled["workspace"], enabled["project"])
        session_client.force_authenticate(user=member)
        r = self._create_status(session_client, slug, pid)
        assert r.status_code == status.HTTP_403_FORBIDDEN
