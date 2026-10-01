# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Structured ``filters`` dict tests (FORK: PSR-85)."""

from datetime import timedelta
from uuid import uuid4

import pytest

from plane.api.pql import PQLContext, PQLError, compile_filters
from plane.db.models import Issue

from .test_compiler import ALL, _letter

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def run(world, filters):
    ctx = PQLContext(workspace_slug=world.ws.slug, user=world.owner)
    letters = [_letter(world, i) for i in Issue.objects.filter(compile_filters(filters, ctx))]
    assert len(letters) == len(set(letters))
    return set(letters)


def test_scalar_keys(world):
    assert run(world, {"priority": "urgent"}) == {"A"}
    assert run(world, {"priority__in": ["urgent", "high"]}) == {"A", "B"}
    assert run(world, {"state__group": "completed"}) == {"C", "E"}
    assert run(world, {"state__group__in": ["backlog", "started"]}) == {"A", "B", "D", "F"}
    assert run(world, {"state_id": str(world.started.id)}) == {"A"}
    assert run(world, {"state_id__in": [str(world.started.id), str(world.done.id)]}) == {"A", "C", "E"}
    assert run(world, {"created_by_id": str(world.u2.id)}) == {"B"}
    assert run(world, {"parent_id": str(world.a.id)}) == {"B"}
    assert run(world, {"parent_id__isnull": False}) == {"B"}
    assert run(world, {"name__icontains": "CAPEX"}) == {"A"}


def test_relation_keys_exclude_soft_deleted_rows(world):
    assert run(world, {"assignees__id__in": [str(world.owner.id), str(world.u2.id)]}) == {"A", "B", "F"}
    assert run(world, {"labels__id__in": [str(world.bug.id)]}) == {"A"}
    assert run(world, {"cycle_id": str(world.active.id)}) == {"A"}
    assert run(world, {"module_id": str(world.m1.id)}) == {"A"}


def test_date_keys(world):
    t = world.today
    assert run(world, {"target_date__lt": t.isoformat()}) == {"A", "C"}
    assert run(world, {"target_date__gte": t.isoformat()}) == {"B"}
    assert run(world, {"start_date__lte": t.isoformat()}) == {"A"}
    assert run(world, {"created_at__lte": (t - timedelta(days=10)).isoformat()}) == {"A"}
    assert run(world, {"created_at__gte": (t - timedelta(days=9)).isoformat()}) == ALL - {"A"}
    assert run(world, {"updated_at__gte": f"{(t - timedelta(days=1)).isoformat()}T00:00:00+00:00"}) == ALL


def test_keys_are_anded_and_scoped(world):
    assert run(world, {"priority__in": ["urgent", "high"], "state__group": "backlog"}) == {"B"}
    assert run(world, {}) == ALL  # never leaks the other workspace's issues


@pytest.mark.parametrize(
    "filters,match",
    [
        ({"state__name": "Done"}, "filters key 'state__name' is not supported.*Allowed filters keys: priority"),
        ({"priority": "critical"}, "not allowed; use one of"),
        ({"priority__in": "high"}, "expected a JSON list"),
        ({"priority__in": []}, "list is empty"),
        ({"priority__in": ["high"] * 101}, "at most 100"),
        ({"state_id": "nope"}, "not a valid UUID"),
        ({"assignees__id__in": [123]}, "expected a UUID string"),
        ({"target_date__lt": "31/01/2024"}, "ISO date"),
        ({"created_at__gte": "yesterday"}, "ISO date or datetime"),
        ({"parent_id__isnull": "true"}, "expected true or false"),
        ({"name__icontains": ""}, "non-empty string"),
        ({"cycle_id": str(uuid4()), "id__in": []}, "'id__in' is not supported"),
    ],
)
def test_invalid_filters(world, filters, match):
    ctx = PQLContext(workspace_slug=world.ws.slug, user=world.owner)
    with pytest.raises(PQLError, match=match):
        compile_filters(filters, ctx)


def test_filters_must_be_a_dict(world):
    ctx = PQLContext(workspace_slug=world.ws.slug, user=world.owner)
    with pytest.raises(PQLError, match="filters must be a JSON object"):
        compile_filters(["priority"], ctx)
