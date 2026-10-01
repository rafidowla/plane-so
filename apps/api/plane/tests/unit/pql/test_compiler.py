# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL compiler semantics against a real database (FORK: PSR-85).

Uses the `world` fixture from this package's conftest: issues A-F in workspace
"pql-ws" plus look-alike data in "pql-other" that must never leak.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from plane.api.pql import PQLContext, PQLError, compile_pql
from plane.api.pql.compiler import _Compiler
from plane.api.pql.parser import parse
from plane.db.models import Issue

ALL = {"A", "B", "C", "D", "E", "F"}


def _letter(world, issue):
    for letter in "abcdef":
        if getattr(world, letter).pk == issue.pk:
            return letter.upper()
    return issue.name  # leaked from another workspace


def run(world, pql, user=None, **ctx_kwargs):
    """Apply the compiled Q to an UNSCOPED queryset; return issue letters.

    Also asserts the queryset never duplicates rows."""
    ctx = PQLContext(workspace_slug=world.ws.slug, user=user or world.owner, **ctx_kwargs)
    qs = Issue.objects.filter(compile_pql(pql, ctx))
    letters = [_letter(world, i) for i in qs]
    assert len(letters) == len(set(letters)), f"duplicate rows for {pql!r}: {letters}"
    return set(letters)


def fails(world, pql, match, user=None, **ctx_kwargs):
    ctx = PQLContext(workspace_slug=world.ws.slug, user=user or world.owner, **ctx_kwargs)
    with pytest.raises(PQLError, match=match) as exc:
        Issue.objects.filter(compile_pql(pql, ctx)).count()
    return exc.value


pytestmark = [pytest.mark.unit, pytest.mark.django_db]


class TestScalarFields:
    def test_priority(self, world):
        assert run(world, 'priority = "urgent"') == {"A"}
        assert run(world, 'priority = "URGENT"') == {"A"}
        assert run(world, 'priority IN ("urgent", "high")') == {"A", "B"}
        assert run(world, 'priority != "urgent"') == ALL - {"A"}
        assert run(world, 'priority NOT IN ("medium", "none")') == {"A", "B", "C"}
        fails(world, 'priority = "critical"', "not a valid priority")

    def test_state_group_and_functions(self, world):
        assert run(world, 'stateGroup = "completed"') == {"C", "E"}
        assert run(world, "stateGroup IN openStates()") == {"A", "B", "D", "F"}
        assert run(world, "stateGroup NOT IN closedStates()") == {"A", "B", "D", "F"}
        assert run(world, "stateGroup IN activeStates()") == {"A"}
        assert run(world, "stateGroup IN (closedStates())") == {"C", "E"}
        fails(world, "priority IN openStates()", "only valid with the stateGroup field")
        fails(world, "stateGroup IN today()", "cannot be used as a value for stateGroup")

    def test_state_project_created_by(self, world):
        assert run(world, f'state = "{world.started.id}"') == {"A"}
        assert run(world, f'state IN ("{world.started.id}", "{world.done.id}")') == {"A", "C", "E"}
        assert run(world, "state IS NULL") == set()
        assert run(world, f'project = "{world.api.id}"') == {"F"}
        assert run(world, f'project != "{world.api.id}"') == ALL - {"F"}
        assert run(world, f'createdBy = "{world.u2.id}"') == {"B"}
        assert run(world, "createdBy = currentUser()") == ALL - {"B"}
        fails(world, 'state = "Doing"', "not a valid UUID for state")
        fails(world, 'project = "WEB"', "project list")

    def test_title_and_text(self, world):
        assert run(world, 'title ~ "CAPEX"') == {"A"}
        assert run(world, 'title = "beta REPORT"') == {"B"}
        assert run(world, 'title != "Beta report"') == ALL - {"B"}
        assert run(world, 'text ~ "spreadsheet"') == {"A"}
        assert run(world, 'text ~ "beta"') == {"B"}
        fails(world, 'title ~ "  "', "non-empty")
        fails(world, 'text = "x"', "not allowed for field 'text'")
        fails(world, 'title IN ("a")', "not allowed for field 'title'")

    def test_id(self, world):
        a_key = f"WEB-{world.a.sequence_id}"
        f_key = f"API-{world.f.sequence_id}"
        assert run(world, f'id = "{a_key}"') == {"A"}
        assert run(world, f'id = "{a_key.lower()}"') == {"A"}
        assert run(world, f'id IN ("{a_key}", "{f_key}")') == {"A", "F"}
        assert run(world, f'id != "{a_key}"') == ALL - {"A"}
        assert run(world, f'id = "{world.b.id}"') == {"B"}
        assert run(world, 'id ~ "API"') == {"F"}
        assert run(world, 'id ~ "api-"') == {"F"}
        fails(world, 'id = "Alpha capex launch"', "not a valid UUID for id")

    def test_booleans(self, world):
        assert run(world, "isDraft = true") == {"D"}
        assert run(world, "isDraft = false") == ALL - {"D"}
        assert run(world, "isDraft != true") == ALL - {"D"}
        assert run(world, "isArchived = true") == {"E"}
        assert run(world, "isArchived != true") == ALL - {"E"}
        fails(world, 'isDraft = "true"', "unquoted true or false")


class TestManyToMany:
    def test_assignee(self, world):
        o, u2 = world.owner.id, world.u2.id
        assert run(world, "assignee = currentUser()") == {"A", "F"}
        # C's u2 assignment is soft-deleted, so it never counts.
        assert run(world, f'assignee = "{u2}"') == {"A", "B"}
        assert run(world, "assignee = currentUser()", user=world.u2) == {"A", "B"}
        assert run(world, f'assignee IN ("{o}", "{u2}")') == {"A", "B", "F"}
        assert run(world, f'assignee IN (currentUser(), "{u2}")') == {"A", "B", "F"}

    def test_assignee_negation_means_has_none_of(self, world):
        o, u2 = world.owner.id, world.u2.id
        assert run(world, f'assignee != "{u2}"') == {"C", "D", "E", "F"}
        assert run(world, f'assignee NOT IN ("{o}", "{u2}")') == {"C", "D", "E"}
        assert run(world, f'NOT assignee = "{u2}"') == {"C", "D", "E", "F"}

    def test_assignee_empty_and_null(self, world):
        assert run(world, "assignee IS EMPTY") == {"C", "E"}
        assert run(world, "assignee IS NULL") == {"C", "E"}
        assert run(world, "assignee IS NOT EMPTY") == {"A", "B", "D", "F"}
        assert run(world, "assignee IS NOT NULL") == {"A", "B", "D", "F"}

    def test_user_functions(self, world):
        # Only active project members: owner (u2 is inactive on WEB).
        assert run(world, f'assignee IN membersOf("project:{world.web.id}")') == {"A", "F"}
        # Only active workspace members: owner + u2 (u3 is inactive).
        assert run(world, "assignee IN workspaceMembers()") == {"A", "B", "F"}
        assert run(world, f'assignee NOT IN membersOf("project:{world.web.id}")') == {"B", "C", "D", "E"}
        fails(world, f'assignee IN membersOf("teamspace:{uuid4()}")', "teamspaces are not available on this Plane")
        fails(world, f'assignee IN membersOf("project:{uuid4()}")', "was not found in this workspace")
        fails(world, 'assignee IN membersOf("project:abc")', "not a valid UUID")
        fails(world, "assignee IN activeCycle()", "cannot be used as a value for assignee")

    def test_label(self, world):
        bug, feature = world.bug.id, world.feature.id
        assert run(world, f'label = "{bug}"') == {"A"}  # E's bug row is soft-deleted
        assert run(world, f'label != "{bug}"') == ALL - {"A"}
        assert run(world, f'label IN ("{bug}", "{feature}")') == {"A", "B"}
        assert run(world, "label IS EMPTY") == {"C", "D", "E", "F"}
        assert run(world, "label IS NOT EMPTY") == {"A", "B"}

    def test_cycle(self, world):
        assert run(world, "cycle IN activeCycle()") == {"A"}
        assert run(world, "cycle = activeCycle()") == {"A"}
        assert run(world, "cycle IN completedCycles()") == {"B"}
        assert run(world, "cycle IN upcomingCycles()") == {"C"}
        assert run(world, "cycle NOT IN activeCycle()") == ALL - {"A"}
        assert run(world, f'cycle = "{world.past.id}"') == {"B"}
        assert run(world, "cycle IS EMPTY") == {"D", "E", "F"}
        # Scoped to ctx.project_id when set: API has no cycles.
        assert run(world, "cycle IN activeCycle()", project_id=world.api.id) == set()
        assert run(world, "cycle IN activeCycle()", project_id=world.web.id) == {"A"}

    def test_module_mention_subscriber(self, world):
        assert run(world, f'module = "{world.m1.id}"') == {"A"}
        assert run(world, f'module != "{world.m1.id}"') == ALL - {"A"}
        assert run(world, f'mention = "{world.u2.id}"') == {"A"}
        assert run(world, "mention IS EMPTY") == ALL - {"A"}
        assert run(world, "subscriber = currentUser()") == {"B"}


class TestDates:
    def test_due_and_start_dates(self, world):
        t = world.today
        assert run(world, "dueDate < today()") == {"A", "C"}
        assert run(world, "dueDate IS NULL") == {"D", "E", "F"}
        assert run(world, "dueDate IS NOT NULL") == {"A", "B", "C"}
        assert run(world, "dueDate BETWEEN (daysAgo(7), today())") == {"A", "C"}
        assert run(world, "dueDate BETWEEN daysAgo(7) AND today()") == {"A", "C"}
        assert run(world, f'dueDate = "{(t - timedelta(days=3)).isoformat()}"') == {"A", "C"}
        assert run(world, "dueDate >= daysFromNow(5)") == {"B"}
        assert run(world, "dueDate > weeksFromNow(1)") == set()
        assert run(world, "startDate = daysAgo(10)") == {"A"}
        assert run(world, "dueDate <= now()") == {"A", "C"}
        fails(world, 'dueDate > "next week"', "not a valid date")
        fails(world, "dueDate BETWEEN (today(), daysAgo(7))", "reversed")
        fails(world, 'dueDate ~ "2024"', "not allowed for field 'dueDate'")
        fails(world, "dueDate > daysAgo()", "takes 1 argument")
        fails(world, 'dueDate > daysAgo("7")', "whole number")
        fails(world, "dueDate > daysAgo(1.5)", "whole number")
        fails(world, "dueDate > currentUser()", "cannot be used as a value for dueDate")

    def test_created_at_by_calendar_day(self, world):
        assert run(world, "createdAt < daysAgo(5)") == {"A"}
        assert run(world, "createdAt = daysAgo(10)") == {"A"}
        assert run(world, "createdAt <= daysAgo(10)") == {"A"}
        assert run(world, "createdAt > daysAgo(10)") == ALL - {"A"}
        assert run(world, "createdAt >= startOfDay()") == ALL - {"A"}
        assert run(world, "createdAt BETWEEN (daysAgo(10), daysAgo(10))") == {"A"}
        assert run(world, "createdAt <= now()") == ALL
        assert run(world, "updatedAt IS NULL") == set()

    def test_created_at_uses_user_timezone(self, world):
        # 2026-03-10T03:00Z is still 2026-03-09 in Toronto; 15:00Z is 2026-03-10.
        Issue.objects.filter(pk=world.a.pk).update(created_at=datetime(2026, 3, 10, 3, tzinfo=dt_timezone.utc))
        Issue.objects.filter(pk=world.b.pk).update(created_at=datetime(2026, 3, 10, 15, tzinfo=dt_timezone.utc))
        assert run(world, 'createdAt = "2026-03-10"') == {"A", "B"}
        world.owner.user_timezone = "America/Toronto"
        assert run(world, 'createdAt = "2026-03-10"') == {"B"}
        assert run(world, 'createdAt = "2026-03-09"') == {"A"}

    def test_date_function_values(self):
        user = SimpleNamespace(id=uuid4(), user_timezone="UTC")
        # 2026-03-15 is a Sunday.
        compiler = _Compiler(PQLContext(workspace_slug="w", user=user, today=date(2026, 3, 15)))

        def value(fn):
            return compiler.date_value(parse(f"dueDate = {fn}").value, "dueDate", allow_instant=True)

        assert value("today()") == value("startOfDay()") == value("endOfDay()") == date(2026, 3, 15)
        assert value("startOfWeek()") == date(2026, 3, 9)
        assert value("endOfWeek()") == date(2026, 3, 15)
        assert value("startOfMonth()") == date(2026, 3, 1)
        assert value("endOfMonth()") == date(2026, 3, 31)
        assert value("startOfYear()") == date(2026, 1, 1)
        assert value("endOfYear()") == date(2026, 12, 31)
        assert value("daysAgo(15)") == date(2026, 2, 28)
        assert value("daysFromNow(17)") == date(2026, 4, 1)
        assert value("weeksAgo(2)") == date(2026, 3, 1)
        assert value("weeksFromNow(1)") == date(2026, 3, 22)
        assert value("monthsAgo(13)") == date(2025, 2, 15)
        assert value("monthsFromNow(10)") == date(2027, 1, 15)
        assert isinstance(value("now()"), datetime)
        month_end = _Compiler(PQLContext(workspace_slug="w", user=user, today=date(2024, 3, 31)))
        assert month_end.date_value(parse("dueDate = monthsAgo(1)").value, "dueDate", False) == date(2024, 2, 29)

    def test_default_today_follows_user_timezone(self):
        ctx = PQLContext(workspace_slug="w", user=SimpleNamespace(id=uuid4(), user_timezone="Pacific/Kiritimati"))
        assert ctx.today == datetime.now(ctx.tz).date()
        assert str(ctx.tz) == "Pacific/Kiritimati"
        bad = PQLContext(workspace_slug="w", user=SimpleNamespace(id=uuid4(), user_timezone="Not/AZone"))
        assert str(bad.tz) == "UTC"

    def test_out_of_range(self, world):
        fails(world, "dueDate > daysAgo(100001)", "out of range")
        fails(world, "dueDate > monthsFromNow(100000)", "outside the supported date range")


class TestPredicates:
    def test_predicates(self, world):
        assert run(world, "isOverdue()") == {"A"}  # C is overdue by date but completed
        assert run(world, "hasNoAssignee()") == {"C", "E"}
        assert run(world, "hasNoLabel()") == {"C", "D", "E", "F"}
        assert run(world, "isTopLevel()") == ALL - {"B"}
        assert run(world, "isSubWorkItem()") == {"B"}
        assert run(world, "hasChildren()") == {"A"}
        assert run(world, "hasStartAndDueDates()") == {"A"}
        assert run(world, "isDraft()") == {"D"}
        assert run(world, "isArchived()") == {"E"}
        assert run(world, "isIntake()") == {"C"}
        assert run(world, "NOT isIntake()") == ALL - {"C"}
        fails(world, "isOverdue(1)", "takes 0 argument")
        fails(world, "today()", "not a standalone condition")


class TestRelations:
    def test_parent_child(self, world):
        a_key, b_key = f"WEB-{world.a.sequence_id}", f"WEB-{world.b.sequence_id}"
        assert run(world, f'childOf("{a_key}")') == {"B"}
        assert run(world, f'childOf("{world.a.id}")') == {"B"}
        assert run(world, f'parentOf("{b_key}")') == {"A"}
        assert run(world, f'parentOf("{a_key}")') == set()
        assert run(world, f'NOT parentOf("{a_key}")') == ALL

    def test_links_both_directions(self, world):
        a_key, c_key, d_key = (f"WEB-{getattr(world, k).sequence_id}" for k in "acd")
        assert run(world, f'linkedTo("{a_key}")') == {"C"}
        assert run(world, f'linkedTo("{c_key}")') == {"A"}
        assert run(world, f'duplicateOf("{c_key}")') == {"D"}
        assert run(world, f'duplicateOf("{d_key}")') == {"C"}

    def test_blocking_both_directions(self, world):
        a_key, b_key = f"WEB-{world.a.sequence_id}", f"WEB-{world.b.sequence_id}"
        # B is blocked by A; F's blocked_by row on A is soft-deleted.
        assert run(world, f'blockedBy("{a_key}")') == {"B"}
        assert run(world, f'blocks("{b_key}")') == {"A"}
        assert run(world, f'blocks("{a_key}")') == set()
        assert run(world, f'blockedBy("{b_key}")') == set()

    def test_unknown_targets(self, world):
        fails(world, 'childOf("WEB-999")', "'WEB-999' passed to childOf\\(\\) was not found")
        fails(world, f'blocks("{uuid4()}")', "was not found in this workspace")
        fails(world, 'linkedTo("Alpha capex launch")', "never a title")
        fails(world, "childOf()", "takes 1 argument")
        fails(world, "childOf(5)", "quoted work item identifier")


class TestUnsupported:
    @pytest.mark.parametrize(
        "pql,match",
        [
            (f'type = "{uuid4()}"', "field 'type' is not available on this Plane edition"),
            (f'milestone = "{uuid4()}"', "Milestones are not available"),
            (f'teamspaceProject = "{uuid4()}"', "use project ="),
            ('cf["abc"] = "x"', "custom properties"),
            ("isEpic()", "isEpic\\(\\) is not available"),
            ('wasEver(assignee, "x")', "history query"),
            ('changedFrom("x")', "history query"),
            ('assignees = "x"', "unknown field 'assignees'. Did you mean 'assignee'"),
            ("frobnicate()", "unknown function frobnicate"),
            ('priority ~ "x"', "operator ~ is not allowed for field 'priority'"),
            (f'label > "{uuid4()}"', "operator > is not allowed for field 'label'"),
            ("isDraft IN (true)", "not allowed for field 'isDraft'"),
            ('title BETWEEN ("a", "b")', "not allowed for field 'title'"),
            ("label = 5", "expects a quoted UUID"),
        ],
    )
    def test_rejected(self, world, pql, match):
        err = fails(world, pql, match)
        assert err.position is not None

    def test_unknown_field_lists_supported_fields(self, world):
        err = fails(world, 'colour = "red"', "unknown field")
        assert "priority" in err.hint and "stateGroup" in err.hint


class TestBooleanLogic:
    def test_precedence_semantics(self, world):
        assert run(world, 'priority = "urgent" OR priority = "high" AND stateGroup = "completed"') == {"A"}
        assert run(world, '(priority = "urgent" OR priority = "high") AND stateGroup = "backlog"') == {"B"}
        assert run(world, 'NOT priority = "urgent" AND stateGroup = "started"') == set()
        assert run(world, 'NOT (priority = "urgent" OR isDraft())') == ALL - {"A", "D"}

    def test_condition_limit(self, world):
        five = 'priority = "urgent" AND isOverdue() AND label IS NOT EMPTY AND hasChildren() AND isTopLevel()'
        assert run(world, five) == {"A"}
        fails(world, five + ' AND title ~ "a"', "6 conditions")


class TestWorkspaceIsolation:
    def test_q_is_scoped_even_on_unscoped_querysets(self, world):
        # X in the other workspace is urgent and contains "capex".
        assert run(world, 'priority = "urgent"') == {"A"}
        assert run(world, 'title ~ "capex"') == {"A"}
        assert run(world, "isOverdue()") == {"A"}

    def test_foreign_uuids_never_match(self, world):
        assert run(world, f'state = "{world.oth_state.id}"') == set()
        assert run(world, f'label = "{world.oth_label.id}"') == set()
        assert run(world, f'assignee = "{world.stranger.id}"') == set()
        assert run(world, f'cycle = "{world.oth_cycle.id}"') == set()
        assert run(world, f'project = "{world.oth.id}"') == set()
        assert run(world, f'id = "OTH-{world.x.sequence_id}"') == set()
        assert run(world, f'id = "{world.x.id}"') == set()

    def test_foreign_cycles_and_members_excluded(self, world):
        assert run(world, "cycle IN activeCycle()") == {"A"}
        assert run(world, "assignee IN workspaceMembers()", user=world.stranger) == {"A", "B", "F"}
        assert run(world, "assignee = currentUser()", user=world.stranger) == set()

    def test_relation_functions_on_foreign_issue_raise(self, world):
        x_key = f"OTH-{world.x.sequence_id}"
        for fn in ("childOf", "parentOf", "linkedTo", "blockedBy", "blocks", "duplicateOf"):
            fails(world, f'{fn}("{x_key}")', f"'{x_key}' passed to {fn}")
            fails(world, f'{fn}("{world.x.id}")', "was not found in this workspace")

    def test_members_of_foreign_project_raises(self, world):
        fails(world, f'assignee IN membersOf("project:{world.oth.id}")', "was not found in this workspace")
