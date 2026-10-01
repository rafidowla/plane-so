# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL AST -> Django ``Q`` compiler (FORK: PSR-85).

`compile_pql` parses a PQL string and returns a ``Q`` to apply to ``Issue``
querysets. Callers still restrict workspace / project / visibility themselves;
the returned ``Q`` is additionally AND-ed with the context workspace so a
mis-scoped caller can never widen results.

Design rules:

* ORM only. Every user-supplied UUID goes through ``uuid.UUID``; every lookup
  that turns user input into rows (cycles, members, projects, relation targets
  and through-rows) is filtered by ``ctx.workspace_slug``.
* Many-to-many fields (assignee, label, cycle, module, mention, subscriber)
  compile to ``Q(id__in=<through-row issue ids>)`` excluding soft-deleted
  through-rows (mirroring ``plane.utils.issue_filters``), so ``!=`` / ``NOT IN``
  mean "has none of" and results never duplicate.
* ``createdAt`` / ``updatedAt`` compare by calendar date in the context
  timezone, except against ``now()``, which is an exact instant.
"""

from __future__ import annotations

import calendar
import difflib
import re
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, time, timedelta
from functools import reduce
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q, QuerySet
from django.utils import timezone

from plane.api.pql.errors import PQLError
from plane.api.pql.parser import (
    And,
    Comparison,
    FuncCall,
    ListValue,
    Literal,
    Node,
    Not,
    Or,
    Predicate,
    parse,
)
from plane.db.models import (
    Cycle,
    CycleIssue,
    IntakeIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueMention,
    IssueRelation,
    IssueSubscriber,
    ModuleIssue,
    Project,
    ProjectMember,
    WorkspaceMember,
)

# --- Vocabulary -----------------------------------------------------------

PRIORITIES = ("urgent", "high", "medium", "low", "none")
STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")
OPEN_STATE_GROUPS = ("backlog", "unstarted", "started")

STATE_GROUP_FUNCTIONS = {
    "openStates": OPEN_STATE_GROUPS,
    "closedStates": ("completed", "cancelled"),
    "activeStates": ("unstarted", "started"),
}
DATE_FUNCTIONS_NOARG = (
    "today",
    "now",
    "startOfDay",
    "endOfDay",
    "startOfWeek",
    "endOfWeek",
    "startOfMonth",
    "endOfMonth",
    "startOfYear",
    "endOfYear",
)
DATE_FUNCTIONS_OFFSET = ("daysAgo", "daysFromNow", "weeksAgo", "weeksFromNow", "monthsAgo", "monthsFromNow")
USER_FUNCTIONS = ("currentUser", "membersOf", "workspaceMembers")
CYCLE_FUNCTIONS = ("activeCycle", "completedCycles", "upcomingCycles")
PREDICATE_FUNCTIONS = (
    "isOverdue",
    "hasNoAssignee",
    "hasNoLabel",
    "isTopLevel",
    "isSubWorkItem",
    "hasChildren",
    "hasStartAndDueDates",
    "isDraft",
    "isArchived",
    "isIntake",
)
RELATION_FUNCTIONS = ("childOf", "parentOf", "linkedTo", "blockedBy", "blocks", "duplicateOf")

ALL_FUNCTIONS = (
    DATE_FUNCTIONS_NOARG
    + DATE_FUNCTIONS_OFFSET
    + USER_FUNCTIONS
    + CYCLE_FUNCTIONS
    + tuple(STATE_GROUP_FUNCTIONS)
    + PREDICATE_FUNCTIONS
    + RELATION_FUNCTIONS
)
_FUNCTIONS_BY_LOWER = {name.lower(): name for name in ALL_FUNCTIONS}

HISTORY_FUNCTIONS = (
    "wasEver",
    "was",
    "wasIn",
    "wasNot",
    "wasNotIn",
    "changed",
    "changedFrom",
    "changedTo",
    "changedAfter",
    "changedBefore",
    "changedBy",
    "updatedBy",
    "commentedBy",
    "fieldChangedBy",
    "wasAssignedTo",
)
UNSUPPORTED_FUNCTIONS = {
    "isepic": (
        "isEpic() is not available on this Plane edition (work item types / epics are not supported).",
        "Remove it; use hasChildren() to find parent work items.",
    ),
    **{
        name.lower(): (
            f"{name}() is a history query, which PQL does not support.",
            "Filter on the current value instead (e.g. assignee = currentUser(), updatedAt >= daysAgo(7)), "
            "or read the work item's activity with the workitem_activity tool.",
        )
        for name in HISTORY_FUNCTIONS
    },
}

# PQL field -> kind. Order is the public listing order.
FIELD_KINDS = {
    "priority": "enum",
    "stateGroup": "enum",
    "state": "fk",
    "project": "fk",
    "createdBy": "fk",
    "assignee": "m2m",
    "label": "m2m",
    "cycle": "m2m",
    "module": "m2m",
    "mention": "m2m",
    "subscriber": "m2m",
    "title": "text",
    "text": "text",
    "id": "id",
    "dueDate": "date",
    "startDate": "date",
    "createdAt": "datetime",
    "updatedAt": "datetime",
    "isDraft": "bool",
    "isArchived": "bool",
}
SUPPORTED_FIELDS = tuple(FIELD_KINDS)
_FIELDS_BY_LOWER = {name.lower(): name for name in FIELD_KINDS}

UNSUPPORTED_FIELDS = {
    "type": "Work item types are not available on this Plane edition; use hasChildren() or label instead.",
    "milestone": "Milestones are not available on this Plane edition; use module or cycle instead.",
    "teamspaceproject": 'Teamspaces are not available on this Plane edition; use project = "<project-uuid>" instead.',
    "cf": "Custom properties are not available on this Plane edition; use label, state or priority instead.",
}

ENUM_FIELDS = {"priority": ("priority", PRIORITIES), "stateGroup": ("state__group", STATE_GROUPS)}
# PQL field -> (Issue column, value domain)
FK_FIELDS = {
    "state": ("state_id", "state"),
    "project": ("project_id", "project"),
    "createdBy": ("created_by_id", "user"),
}
# PQL field -> (through model, column on the through model, value domain)
M2M_FIELDS = {
    "assignee": (IssueAssignee, "assignee_id", "user"),
    "label": (IssueLabel, "label_id", "label"),
    "cycle": (CycleIssue, "cycle_id", "cycle"),
    "module": (ModuleIssue, "module_id", "module"),
    "mention": (IssueMention, "mention_id", "user"),
    "subscriber": (IssueSubscriber, "subscriber_id", "user"),
}
DATE_COLUMNS = {"dueDate": "target_date", "startDate": "start_date"}
DATETIME_COLUMNS = {"createdAt": "created_at", "updatedAt": "updated_at"}

ORDER_OPS = ("=", "!=", ">", ">=", "<", "<=", "BETWEEN", "IS NULL", "IS NOT NULL")
ALLOWED_OPS = {
    "enum": ("=", "!=", "IN", "NOT IN"),
    "fk": ("=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"),
    "m2m": ("=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL", "IS EMPTY", "IS NOT EMPTY"),
    "title": ("=", "!=", "~"),
    "text": ("~",),
    "id": ("=", "!=", "~", "IN", "NOT IN"),
    "date": ORDER_OPS,
    "datetime": ORDER_OPS,
    "bool": ("=", "!="),
}

# How to obtain UUIDs for each value domain (mirrors the agent-facing reference).
DOMAIN_HINTS = {
    "state": "a state UUID (resolve names with `state list`)",
    "project": "a project UUID (resolve identifiers like WEB with `project list`)",
    "user": 'a user UUID, currentUser(), membersOf("project:<uuid>") or workspaceMembers() '
    "(resolve names with `member list_workspace`)",
    "label": "a label UUID (resolve names with `label list`)",
    "cycle": "a cycle UUID, activeCycle(), completedCycles() or upcomingCycles() (resolve names with `cycle list`)",
    "module": "a module UUID (resolve names with `module list`)",
}

_ISSUE_IDENTIFIER_RE = re.compile(r"^\s*([A-Za-z0-9_]+)-(\d+)\s*$")
MAX_OFFSET = 100_000


# --- Context --------------------------------------------------------------


@dataclass
class PQLContext:
    """Request context for compiling PQL.

    Attributes:
        workspace_slug: Workspace every resolved lookup is scoped to.
        user: The authenticated user (``currentUser()``; timezone source).
        project_id: When set, cycle functions only consider this project's cycles.
        today: "Today" for date functions; defaults to today in the user's
            ``user_timezone`` (UTC when unset or invalid).
    """

    workspace_slug: str
    user: Any
    project_id: uuid.UUID | None = None
    today: date | None = None
    tz: ZoneInfo = dc_field(init=False, repr=False)

    def __post_init__(self) -> None:
        tz_name = getattr(self.user, "user_timezone", None) or "UTC"
        try:
            self.tz = ZoneInfo(str(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = ZoneInfo("UTC")
        if self.today is None:
            self.today = datetime.now(self.tz).date()


# --- Helpers --------------------------------------------------------------


def _err(detail: str, pos: int | None, hint: str | None = None) -> PQLError:
    return PQLError(detail, position=pos, hint=hint)


def _suggest(name: str, choices: Iterable[str]) -> str:
    match = difflib.get_close_matches(name, list(choices), n=1, cutoff=0.6)
    return f" Did you mean {match[0]!r}?" if match else ""


def _any_of(qs: list[Q]) -> Q:
    return reduce(lambda a, b: a | b, qs)


def _all_of(qs: list[Q]) -> Q:
    return reduce(lambda a, b: a & b, qs)


def parse_uuid(raw: Any, what: str, pos: int | None, hint: str | None = None) -> uuid.UUID:
    """Validate ``raw`` as a UUID or raise a PQLError naming ``what``."""
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise _err(f"{raw!r} is not a valid UUID for {what}.", pos, hint)


def add_months(d: date, months: int) -> date:
    """Shift ``d`` by whole calendar months, clamping to the month's last day."""
    year, month0 = divmod(d.month - 1 + months, 12)
    year += d.year
    day = min(d.day, calendar.monthrange(year, month0 + 1)[1]) if 1 <= year <= 9999 else 1
    return date(year, month0 + 1, day)


# --- Compiler -------------------------------------------------------------


class _Compiler:
    def __init__(self, ctx: PQLContext) -> None:
        self.ctx = ctx
        self.slug = ctx.workspace_slug
        self.today: date = ctx.today  # set by PQLContext.__post_init__
        self.tz = ctx.tz

    # nodes
    def compile(self, node: Node) -> Q:
        if isinstance(node, And):
            return _all_of([self.compile(c) for c in node.children])
        if isinstance(node, Or):
            return _any_of([self.compile(c) for c in node.children])
        if isinstance(node, Not):
            return ~self.compile(node.child)
        if isinstance(node, Predicate):
            return self.compile_predicate(node.call)
        return self.compile_comparison(node)

    # names
    def function_name(self, call: FuncCall) -> str:
        lower = call.name.lower()
        if lower in UNSUPPORTED_FUNCTIONS:
            detail, hint = UNSUPPORTED_FUNCTIONS[lower]
            raise _err(detail, call.pos, hint)
        if lower not in _FUNCTIONS_BY_LOWER:
            raise _err(
                f"unknown function {call.name}().{_suggest(call.name, ALL_FUNCTIONS)}",
                call.pos,
                f"Supported functions: {', '.join(f + '()' for f in ALL_FUNCTIONS)}.",
            )
        return _FUNCTIONS_BY_LOWER[lower]

    def field_name(self, node: Comparison) -> str:
        lower = node.field.lower()
        if lower in UNSUPPORTED_FIELDS:
            raise _err(
                f"field {node.field!r} is not available on this Plane edition.",
                node.field_pos,
                UNSUPPORTED_FIELDS[lower],
            )
        if lower not in _FIELDS_BY_LOWER:
            raise _err(
                f"unknown field {node.field!r}.{_suggest(node.field, SUPPORTED_FIELDS)}",
                node.field_pos,
                f"Supported fields: {', '.join(SUPPORTED_FIELDS)}.",
            )
        return _FIELDS_BY_LOWER[lower]

    def check_arity(self, call: FuncCall, name: str, expected: int, usage: str) -> None:
        if len(call.args) != expected:
            raise _err(f"{name}() takes {expected} argument(s), got {len(call.args)}.", call.pos, f"Usage: {usage}.")

    # comparisons
    def compile_comparison(self, node: Comparison) -> Q:
        name = self.field_name(node)
        kind = FIELD_KINDS[name]
        op_kind = "title" if name == "title" else kind
        allowed = ALLOWED_OPS[op_kind]
        if node.op not in allowed:
            raise _err(
                f"operator {node.op} is not allowed for field {name!r}.",
                node.pos,
                f"Allowed operators for {name}: {', '.join(allowed)}.",
            )
        handler: Callable[[str, Comparison], Q] = getattr(self, f"compile_{kind}")
        return handler(name, node)

    def values_of(self, node: Comparison) -> list:
        """The value(s) of an =, !=, IN or NOT IN comparison as a list."""
        if isinstance(node.value, ListValue):
            return list(node.value.items)
        return [node.value]

    @staticmethod
    def negated(op: str) -> bool:
        return op in ("!=", "NOT IN", "IS NOT NULL", "IS NOT EMPTY")

    def string_value(self, value: Any, name: str, allowed_hint: str) -> str:
        if isinstance(value, Literal) and value.kind == "string":
            return str(value.value)
        if isinstance(value, FuncCall):
            fn = self.function_name(value)
            raise _err(f"{fn}() cannot be used as a value for {name}.", value.pos, f"{name} expects {allowed_hint}.")
        shown = "true/false" if value.kind == "bool" else str(value.value)
        raise _err(f"{name} expects a double-quoted string, got {shown}.", value.pos, f"{name} expects {allowed_hint}.")

    # enum: priority, stateGroup
    def compile_enum(self, name: str, node: Comparison) -> Q:
        column, choices = ENUM_FIELDS[name]
        hint = ", ".join(f'"{c}"' for c in choices)
        if name == "stateGroup":
            hint += ", or openStates() / closedStates() / activeStates()"
        resolved: list[str] = []
        for value in self.values_of(node):
            if isinstance(value, FuncCall) and name == "stateGroup":
                fn = self.function_name(value)
                if fn in STATE_GROUP_FUNCTIONS:
                    self.check_arity(value, fn, 0, f"stateGroup IN {fn}()")
                    resolved.extend(STATE_GROUP_FUNCTIONS[fn])
                    continue
            if isinstance(value, FuncCall) and self.function_name(value) in STATE_GROUP_FUNCTIONS:
                raise _err(
                    f"{value.name}() returns state groups and is only valid with the stateGroup field.",
                    value.pos,
                    f"Write stateGroup IN {value.name}().",
                )
            raw = self.string_value(value, name, hint).strip().lower()
            if raw not in choices:
                raise _err(f"{raw!r} is not a valid {name}.", value.pos, f"Allowed values: {hint}.")
            resolved.append(raw)
        q = Q(**{f"{column}__in": resolved})
        return ~q if self.negated(node.op) else q

    # UUID-valued domains
    def id_lookup(self, column: str, values: list, domain: str, field_name: str) -> Q:
        """OR of ``column`` matches over literal UUIDs and set-valued functions."""
        uuids: list[uuid.UUID] = []
        parts: list[Q] = []
        hint = f"{field_name} expects {DOMAIN_HINTS[domain]}."
        for value in values:
            if isinstance(value, FuncCall):
                parts.append(Q(**{f"{column}__in": self.id_function(value, domain, field_name, hint)}))
                continue
            if value.kind != "string":
                raise _err(f"{field_name} expects a quoted UUID, got {value.value!r}.", value.pos, hint)
            uuids.append(parse_uuid(value.value, field_name, value.pos, hint))
        if uuids:
            parts.append(Q(**{f"{column}__in": uuids}))
        return _any_of(parts)

    def id_function(self, call: FuncCall, domain: str, field_name: str, hint: str) -> list | QuerySet:
        fn = self.function_name(call)
        if domain == "user" and fn in USER_FUNCTIONS:
            return self.user_function(call, fn)
        if domain == "cycle" and fn in CYCLE_FUNCTIONS:
            self.check_arity(call, fn, 0, f"cycle IN {fn}()")
            return self.cycle_function(fn)
        raise _err(f"{fn}() cannot be used as a value for {field_name}.", call.pos, hint)

    def compile_fk(self, name: str, node: Comparison) -> Q:
        column, domain = FK_FIELDS[name]
        if node.op in ("IS NULL", "IS NOT NULL"):
            return Q(**{f"{column}__isnull": node.op == "IS NULL"})
        q = self.id_lookup(column, self.values_of(node), domain, name)
        return ~q if self.negated(node.op) else q

    def compile_m2m(self, name: str, node: Comparison) -> Q:
        model, column, domain = M2M_FIELDS[name]
        if node.op in ("IS NULL", "IS EMPTY", "IS NOT NULL", "IS NOT EMPTY"):
            q = self.has_rows(model, Q())
            return q if self.negated(node.op) else ~q
        q = self.has_rows(model, self.id_lookup(column, self.values_of(node), domain, name))
        return ~q if self.negated(node.op) else q

    def has_rows(self, model: Any, condition: Q) -> Q:
        """Issues having at least one live (non soft-deleted) through-row matching ``condition``."""
        rows = model.objects.filter(condition, deleted_at__isnull=True, workspace__slug=self.slug)
        return Q(id__in=rows.values("issue_id"))

    # text: title, text
    def compile_text(self, name: str, node: Comparison) -> Q:
        raw = self.string_value(node.value, name, "a double-quoted string")
        if node.op == "~":
            if not raw.strip():
                raise _err(f"{name} ~ needs a non-empty search string.", node.value.pos, f'e.g. {name} ~ "capex".')
            if name == "text":
                return Q(name__icontains=raw) | Q(description_stripped__icontains=raw)
            return Q(name__icontains=raw)
        q = Q(name__iexact=raw)
        return ~q if node.op == "!=" else q

    # id: "WEB-12" / UUID
    def compile_id(self, name: str, node: Comparison) -> Q:
        hint = 'id expects a work item identifier like "WEB-12" (or a work item UUID); id ~ "WEB" matches a project.'
        if node.op == "~":
            raw = self.string_value(node.value, name, hint).strip().rstrip("-")
            if not raw:
                raise _err("id ~ needs a non-empty project identifier.", node.value.pos, hint)
            return Q(project__identifier__icontains=raw)
        parts = []
        for value in self.values_of(node):
            raw = self.string_value(value, name, hint)
            match = _ISSUE_IDENTIFIER_RE.match(raw)
            if match:
                parts.append(Q(project__identifier__iexact=match.group(1), sequence_id=int(match.group(2))))
            else:
                parts.append(Q(id=parse_uuid(raw, "id", value.pos, hint)))
        q = _any_of(parts)
        return ~q if self.negated(node.op) else q

    # bool: isDraft, isArchived
    def compile_bool(self, name: str, node: Comparison) -> Q:
        value = node.value
        if not (isinstance(value, Literal) and value.kind == "bool"):
            raise _err(
                f"{name} expects unquoted true or false.",
                value.pos,
                f'Write {name} = true (not "true"), or use the predicate {name}().',
            )
        flag = bool(value.value) != (node.op == "!=")
        if name == "isDraft":
            return Q(is_draft=flag)
        return Q(archived_at__isnull=not flag)

    # dates
    def date_value(self, value: Any, name: str, allow_instant: bool) -> date | datetime:
        hint = f'{name} expects a quoted "YYYY-MM-DD" date or a date function such as today() or daysAgo(7).'
        if isinstance(value, FuncCall):
            return self.date_function(value, name, hint)
        if value.kind != "string":
            raise _err(f"{name} expects a date, got {value.value!r}.", value.pos, hint)
        raw = str(value.value).strip()
        try:
            if len(raw) == 10:
                return date.fromisoformat(raw)
            if allow_instant:
                parsed = datetime.fromisoformat(raw)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=self.tz)
        except ValueError:
            pass
        raise _err(f"{raw!r} is not a valid date for {name}.", value.pos, hint)

    def date_function(self, call: FuncCall, name: str, hint: str) -> date | datetime:
        fn = self.function_name(call)
        today = self.today
        if fn in DATE_FUNCTIONS_NOARG:
            self.check_arity(call, fn, 0, f"{fn}()")
            if fn == "now":
                return timezone.now()
            week_start = today - timedelta(days=today.weekday())
            return {
                "today": today,
                "startOfDay": today,
                "endOfDay": today,
                "startOfWeek": week_start,
                "endOfWeek": week_start + timedelta(days=6),
                "startOfMonth": today.replace(day=1),
                "endOfMonth": today.replace(day=calendar.monthrange(today.year, today.month)[1]),
                "startOfYear": date(today.year, 1, 1),
                "endOfYear": date(today.year, 12, 31),
            }[fn]
        if fn in DATE_FUNCTIONS_OFFSET:
            self.check_arity(call, fn, 1, f"{fn}(7)")
            arg = call.args[0]
            if not (isinstance(arg, Literal) and arg.kind == "number" and isinstance(arg.value, int)):
                raise _err(f"{fn}() needs a whole number, e.g. {fn}(7).", arg.pos, "No quotes and no decimals.")
            n = arg.value
            if abs(n) > MAX_OFFSET:
                raise _err(
                    f"{fn}({n}) is out of range.", arg.pos, f"Use a number between -{MAX_OFFSET} and {MAX_OFFSET}."
                )
            sign = -1 if fn.endswith("Ago") else 1
            try:
                if fn.startswith("days"):
                    return today + timedelta(days=sign * n)
                if fn.startswith("weeks"):
                    return today + timedelta(weeks=sign * n)
                return add_months(today, sign * n)
            except (OverflowError, ValueError):
                raise _err(f"{fn}({n}) is outside the supported date range.", call.pos, "Use a smaller number.")
        raise _err(f"{fn}() cannot be used as a value for {name}.", call.pos, hint)

    def day_start(self, d: date, pos: int) -> datetime:
        try:
            return datetime.combine(d, time.min, tzinfo=self.tz)
        except (OverflowError, ValueError):
            raise _err(f"date {d} is out of range.", pos)

    def next_day(self, d: date, pos: int) -> date:
        try:
            return d + timedelta(days=1)
        except OverflowError:
            raise _err(f"date {d} is out of range.", pos)

    def compile_date(self, name: str, node: Comparison) -> Q:
        column = DATE_COLUMNS[name]
        if node.op in ("IS NULL", "IS NOT NULL"):
            return Q(**{f"{column}__isnull": node.op == "IS NULL"})

        def as_date(value: Any) -> date:
            v = self.date_value(value, name, allow_instant=False)
            return v.astimezone(self.tz).date() if isinstance(v, datetime) else v

        if node.op == "BETWEEN":
            low, high = (as_date(v) for v in node.value)
            if low > high:
                raise _err(
                    f"BETWEEN bounds are reversed ({low} is after {high}).", node.pos, "Write BETWEEN (low, high)."
                )
            return Q(**{f"{column}__gte": low, f"{column}__lte": high})
        d = as_date(node.value)
        if node.op == "!=":
            return ~Q(**{column: d})
        lookup = {"=": "exact", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[node.op]
        return Q(**{f"{column}__{lookup}": d})

    def compile_datetime(self, name: str, node: Comparison) -> Q:
        column = DATETIME_COLUMNS[name]
        if node.op in ("IS NULL", "IS NOT NULL"):
            return Q(**{f"{column}__isnull": node.op == "IS NULL"})
        if node.op == "BETWEEN":
            low_v, high_v = node.value
            low = self.date_value(low_v, name, allow_instant=True)
            high = self.date_value(high_v, name, allow_instant=True)
            low_q = Q(**{f"{column}__gte": low if isinstance(low, datetime) else self.day_start(low, low_v.pos)})
            if isinstance(high, datetime):
                high_q = Q(**{f"{column}__lte": high})
            else:
                high_q = Q(**{f"{column}__lt": self.day_start(self.next_day(high, high_v.pos), high_v.pos)})
            return low_q & high_q
        v = self.date_value(node.value, name, allow_instant=True)
        if isinstance(v, datetime):
            if node.op == "!=":
                return ~Q(**{column: v})
            lookup = {"=": "exact", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[node.op]
            return Q(**{f"{column}__{lookup}": v})
        pos = node.value.pos
        lo = self.day_start(v, pos)
        hi = self.day_start(self.next_day(v, pos), pos)
        same_day = Q(**{f"{column}__gte": lo, f"{column}__lt": hi})
        return {
            "=": same_day,
            "!=": ~same_day,
            ">": Q(**{f"{column}__gte": hi}),
            ">=": Q(**{f"{column}__gte": lo}),
            "<": Q(**{f"{column}__lt": lo}),
            "<=": Q(**{f"{column}__lt": hi}),
        }[node.op]

    # set-valued functions
    def user_function(self, call: FuncCall, fn: str) -> list | QuerySet:
        if fn == "currentUser":
            self.check_arity(call, fn, 0, "assignee = currentUser()")
            user_id = getattr(self.ctx.user, "id", None)
            if user_id is None:
                raise _err("currentUser() needs an authenticated user.", call.pos)
            return [user_id]
        if fn == "workspaceMembers":
            self.check_arity(call, fn, 0, "assignee IN workspaceMembers()")
            return WorkspaceMember.objects.filter(
                workspace__slug=self.slug, is_active=True, deleted_at__isnull=True
            ).values("member_id")
        # membersOf("project:<uuid>")
        usage = 'assignee IN membersOf("project:<project-uuid>")'
        self.check_arity(call, fn, 1, usage)
        arg = call.args[0]
        if not (isinstance(arg, Literal) and arg.kind == "string") or ":" not in str(arg.value):
            raise _err('membersOf() needs a quoted "project:<uuid>" argument.', arg.pos, f"Usage: {usage}.")
        scope, _, raw_id = str(arg.value).partition(":")
        scope = scope.strip().lower()
        if scope == "teamspace":
            raise _err(
                "teamspaces are not available on this Plane edition.",
                arg.pos,
                f'Use membersOf("project:<project-uuid>") or workspaceMembers() instead. Usage: {usage}.',
            )
        if scope != "project":
            raise _err(f"membersOf() scope {scope!r} is not supported.", arg.pos, f"Usage: {usage}.")
        project_id = parse_uuid(raw_id.strip(), "membersOf", arg.pos, f"Usage: {usage}.")
        if not Project.objects.filter(id=project_id, workspace__slug=self.slug).exists():
            raise _err(
                f"project {project_id} was not found in this workspace.",
                arg.pos,
                "Resolve the project UUID with `project list`.",
            )
        return ProjectMember.objects.filter(
            project_id=project_id, workspace__slug=self.slug, is_active=True, deleted_at__isnull=True
        ).values("member_id")

    def cycle_function(self, fn: str) -> QuerySet:
        cycles = Cycle.objects.filter(workspace__slug=self.slug, deleted_at__isnull=True)
        if self.ctx.project_id:
            cycles = cycles.filter(project_id=self.ctx.project_id)
        today_start = self.day_start(self.today, 0)
        tomorrow_start = self.day_start(self.today + timedelta(days=1), 0)
        if fn == "activeCycle":
            cycles = cycles.filter(start_date__lt=tomorrow_start, end_date__gte=today_start)
        elif fn == "completedCycles":
            cycles = cycles.filter(end_date__lt=today_start)
        else:
            cycles = cycles.filter(start_date__gte=tomorrow_start)
        return cycles.values("id")

    # predicates and relations
    def compile_predicate(self, call: FuncCall) -> Q:
        fn = self.function_name(call)
        if fn in RELATION_FUNCTIONS:
            return self.compile_relation(call, fn)
        if fn not in PREDICATE_FUNCTIONS:
            raise _err(
                f"{fn}() is a value function, not a standalone condition.",
                call.pos,
                "Compare it with a field, e.g. dueDate < today(), assignee = currentUser(), "
                "cycle IN activeCycle(), stateGroup IN openStates().",
            )
        self.check_arity(call, fn, 0, f"{fn}()")
        if fn == "isOverdue":
            return Q(target_date__lt=self.today, state__group__in=OPEN_STATE_GROUPS)
        if fn == "hasNoAssignee":
            return ~self.has_rows(IssueAssignee, Q())
        if fn == "hasNoLabel":
            return ~self.has_rows(IssueLabel, Q())
        if fn == "isTopLevel":
            return Q(parent_id__isnull=True)
        if fn == "isSubWorkItem":
            return Q(parent_id__isnull=False)
        if fn == "hasChildren":
            children = Issue.objects.filter(
                workspace__slug=self.slug, deleted_at__isnull=True, parent_id__isnull=False
            ).values("parent_id")
            return Q(id__in=children)
        if fn == "hasStartAndDueDates":
            return Q(start_date__isnull=False, target_date__isnull=False)
        if fn == "isDraft":
            return Q(is_draft=True)
        if fn == "isArchived":
            return Q(archived_at__isnull=False)
        # isIntake
        return self.has_rows(IntakeIssue, Q())

    def resolve_issue(self, call: FuncCall, fn: str) -> Issue:
        usage = f'{fn}("WEB-5") or {fn}("<work-item-uuid>")'
        self.check_arity(call, fn, 1, usage)
        arg = call.args[0]
        if not (isinstance(arg, Literal) and arg.kind == "string"):
            raise _err(f"{fn}() needs a quoted work item identifier or UUID.", arg.pos, f"Usage: {usage}.")
        raw = str(arg.value).strip()
        issues = Issue.objects.filter(workspace__slug=self.slug, deleted_at__isnull=True)
        match = _ISSUE_IDENTIFIER_RE.match(raw)
        if match:
            issues = issues.filter(project__identifier__iexact=match.group(1), sequence_id=int(match.group(2)))
        else:
            hint = f'Pass a work item identifier like "WEB-5" or a work item UUID, never a title. Usage: {usage}.'
            issues = issues.filter(id=parse_uuid(raw, f"{fn}()", arg.pos, hint))
        issue = issues.only("id", "parent_id").first()
        if issue is None:
            raise _err(
                f"work item {raw!r} passed to {fn}() was not found in this workspace.",
                arg.pos,
                "Check the identifier (e.g. with `workitem list`) and retry.",
            )
        return issue

    def compile_relation(self, call: FuncCall, fn: str) -> Q:
        target = self.resolve_issue(call, fn)
        if fn == "childOf":
            return Q(parent_id=target.id)
        if fn == "parentOf":
            return Q(id=target.parent_id) if target.parent_id else Q(pk__in=[])
        relations = IssueRelation.objects.filter(workspace__slug=self.slug, deleted_at__isnull=True)
        if fn in ("linkedTo", "duplicateOf"):
            relation_type = "relates_to" if fn == "linkedTo" else "duplicate"
            rows = relations.filter(relation_type=relation_type)
            return Q(id__in=rows.filter(issue_id=target.id).values("related_issue_id")) | Q(
                id__in=rows.filter(related_issue_id=target.id).values("issue_id")
            )
        rows = relations.filter(relation_type="blocked_by")
        if fn == "blockedBy":
            # IssueRelation(issue=A, related_issue=X, blocked_by): A is blocked by X.
            return Q(id__in=rows.filter(related_issue_id=target.id).values("issue_id"))
        # blocks(X): issues B with IssueRelation(issue=X, related_issue=B, blocked_by).
        return Q(id__in=rows.filter(issue_id=target.id).values("related_issue_id"))


def compile_ast(node: Node, ctx: PQLContext) -> Q:
    """Compile an already-parsed PQL AST (see `parser.parse`)."""
    return Q(workspace__slug=ctx.workspace_slug) & _Compiler(ctx).compile(node)


def compile_pql(pql: str, ctx: PQLContext) -> Q:
    """Parse and compile a PQL string into a ``Q`` for ``Issue`` querysets.

    Raises:
        PQLError: on syntax errors, unsupported fields/functions, invalid values,
            unknown relation targets, or exceeded limits.
    """
    return compile_ast(parse(pql), ctx)
