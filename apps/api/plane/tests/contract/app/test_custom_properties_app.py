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
