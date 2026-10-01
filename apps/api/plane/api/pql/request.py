# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Request-level wrapper around the PQL engine (FORK: PSR-85).

Turns the ``?pql=`` and ``?filters=`` query parameters of a public API request
into one ``Q``. Kept here (not in a view module) so the upstream
``views/issue.py`` and the fork-owned ``views/work_item_query.py`` can both use
it without an import cycle.
"""

from __future__ import annotations

import json
from typing import Mapping

from django.db.models import Q

from plane.api.pql.compiler import PQLContext, compile_pql
from plane.api.pql.errors import PQLError
from plane.api.pql.filters import compile_filters


class PQLParamError(Exception):
    """A ``pql``/``filters`` query parameter that cannot be applied.

    Attributes:
        param: ``"pql"`` or ``"filters"`` -- the key of the 400 response body.
        message: Human/agent-readable explanation.
    """

    def __init__(self, param: str, message: str) -> None:
        self.param = param
        self.message = message
        super().__init__(message)

    def as_response_body(self) -> dict:
        return {self.param: self.message}


def compile_request_filters(params: Mapping[str, str], ctx: PQLContext) -> Q:
    """Compile ``params["pql"]`` and ``params["filters"]`` (a JSON object) into one ``Q``.

    Empty or missing parameters contribute nothing (an empty ``Q``).

    Raises:
        PQLParamError: on invalid JSON in ``filters`` or any `PQLError`.
    """
    q = Q()
    pql = (params.get("pql") or "").strip()
    raw_filters = (params.get("filters") or "").strip()

    if raw_filters:
        try:
            filters = json.loads(raw_filters)
        except (TypeError, ValueError) as exc:
            raise PQLParamError(
                "filters",
                f"filters must be a JSON object of lookup: value pairs; could not parse it as JSON ({exc}).",
            ) from exc
        try:
            q &= compile_filters(filters, ctx)
        except PQLError as exc:
            raise PQLParamError("filters", str(exc)) from exc

    if pql:
        try:
            q &= compile_pql(pql, ctx)
        except PQLError as exc:
            raise PQLParamError("pql", str(exc)) from exc

    return q
