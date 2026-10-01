# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Structured ``filters`` dict -> Django ``Q`` (FORK: PSR-85).

The MCP connector may send a JSON ``filters`` object alongside (or instead of)
``pql``: a flat ``{lookup: value}`` dict. Only the lookups in
`ALLOWED_FILTER_KEYS` are accepted; every value is type-checked (UUIDs, ISO
dates, lists for ``__in``) before it reaches the ORM. Keys are AND-ed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.db.models import Q

from plane.api.pql.compiler import (
    M2M_FIELDS,
    PRIORITIES,
    STATE_GROUPS,
    PQLContext,
    _Compiler,
)
from plane.api.pql.errors import PQLError
from plane.api.pql.parser import MAX_IN_VALUES

_DATE_OPS = ("lt", "lte", "gt", "gte")

ALLOWED_FILTER_KEYS = (
    "priority",
    "priority__in",
    "state_id",
    "state_id__in",
    "state__group",
    "state__group__in",
    "assignees__id__in",
    "labels__id__in",
    "cycle_id",
    "module_id",
    "created_by_id",
    "parent_id",
    "parent_id__isnull",
    *(f"target_date__{op}" for op in _DATE_OPS),
    *(f"start_date__{op}" for op in _DATE_OPS),
    "created_at__gte",
    "created_at__lte",
    "updated_at__gte",
    "updated_at__lte",
    "name__icontains",
)


def _fail(key: str, detail: str) -> PQLError:
    return PQLError(f"filters[{key!r}]: {detail}")


def _as_list(key: str, value: Any) -> list:
    if not isinstance(value, list):
        raise _fail(key, f"expected a JSON list for an __in lookup, got {type(value).__name__}.")
    if not value:
        raise _fail(key, "the list is empty.")
    if len(value) > MAX_IN_VALUES:
        raise _fail(key, f"at most {MAX_IN_VALUES} values are allowed.")
    return value


def _as_uuid(key: str, value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise _fail(key, f"expected a UUID string, got {value!r}.")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _fail(key, f"{value!r} is not a valid UUID.")


def _as_choice(key: str, value: Any, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value.strip().lower() not in choices:
        raise _fail(key, f"{value!r} is not allowed; use one of {', '.join(choices)}.")
    return value.strip().lower()


def _as_date(key: str, value: Any) -> date:
    if isinstance(value, str) and len(value.strip()) == 10:
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise _fail(key, f'expected an ISO date "YYYY-MM-DD", got {value!r}.')


def _as_datetime_bound(key: str, value: Any, ctx: PQLContext) -> datetime:
    """A ``created_at`` / ``updated_at`` bound: a full ISO datetime is used as
    is; a bare date covers that whole calendar day in the context timezone."""
    is_upper = key.endswith("__lte")
    if isinstance(value, str) and len(value.strip()) == 10:
        d = _as_date(key, value)
        if is_upper:
            d = d + timedelta(days=1)
        return datetime.combine(d, datetime.min.time(), tzinfo=ctx.tz)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=ctx.tz)
    raise _fail(key, f"expected an ISO date or datetime, got {value!r}.")


def _key_q(key: str, value: Any, ctx: PQLContext, compiler: _Compiler) -> Q:
    if key == "priority":
        return Q(priority=_as_choice(key, value, PRIORITIES))
    if key == "priority__in":
        return Q(priority__in=[_as_choice(key, v, PRIORITIES) for v in _as_list(key, value)])
    if key == "state__group":
        return Q(state__group=_as_choice(key, value, STATE_GROUPS))
    if key == "state__group__in":
        return Q(state__group__in=[_as_choice(key, v, STATE_GROUPS) for v in _as_list(key, value)])
    if key in ("state_id", "created_by_id", "parent_id"):
        return Q(**{key: _as_uuid(key, value)})
    if key == "state_id__in":
        return Q(state_id__in=[_as_uuid(key, v) for v in _as_list(key, value)])
    if key == "parent_id__isnull":
        if not isinstance(value, bool):
            raise _fail(key, f"expected true or false, got {value!r}.")
        return Q(parent_id__isnull=value)
    through_keys: dict[str, tuple[str, Callable[[], list]]] = {
        "assignees__id__in": ("assignee", lambda: [_as_uuid(key, v) for v in _as_list(key, value)]),
        "labels__id__in": ("label", lambda: [_as_uuid(key, v) for v in _as_list(key, value)]),
        "cycle_id": ("cycle", lambda: [_as_uuid(key, value)]),
        "module_id": ("module", lambda: [_as_uuid(key, value)]),
    }
    if key in through_keys:
        field_name, get_ids = through_keys[key]
        model, column, _domain = M2M_FIELDS[field_name]
        return compiler.has_rows(model, Q(**{f"{column}__in": get_ids()}))
    if key.startswith(("target_date__", "start_date__")):
        return Q(**{key: _as_date(key, value)})
    if key in ("created_at__gte", "updated_at__gte"):
        return Q(**{key: _as_datetime_bound(key, value, ctx)})
    if key in ("created_at__lte", "updated_at__lte"):
        bound = _as_datetime_bound(key, value, ctx)
        column = key.rsplit("__", 1)[0]
        # A bare date was widened to the next midnight: make the bound exclusive.
        lookup = "lt" if isinstance(value, str) and len(value.strip()) == 10 else "lte"
        return Q(**{f"{column}__{lookup}": bound})
    # name__icontains
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, "expected a non-empty string.")
    return Q(name__icontains=value)


def compile_filters(filters: dict, ctx: PQLContext) -> Q:
    """Compile a structured ``filters`` dict into a ``Q`` for ``Issue`` querysets.

    Raises:
        PQLError: when ``filters`` is not a dict, uses a key outside
            `ALLOWED_FILTER_KEYS`, or has a value of the wrong type.
    """
    if not isinstance(filters, dict):
        raise PQLError(
            f"filters must be a JSON object of lookup: value pairs, got {type(filters).__name__}.",
            hint=f"Allowed keys: {', '.join(ALLOWED_FILTER_KEYS)}.",
        )
    compiler = _Compiler(ctx)
    q = Q(workspace__slug=ctx.workspace_slug)
    for key, value in filters.items():
        if key not in ALLOWED_FILTER_KEYS:
            raise PQLError(
                f"filters key {key!r} is not supported.",
                hint=f"Allowed filters keys: {', '.join(ALLOWED_FILTER_KEYS)}. For anything richer, use pql.",
            )
        q &= _key_q(key, value, ctx, compiler)
    return q
