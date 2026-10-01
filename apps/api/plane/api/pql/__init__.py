# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plane Query Language engine for the public API (FORK: PSR-85).

Evaluates the PQL subset that the stock Plane MCP connector sends in ``?pql=``
(and the structured ``?filters=`` dict) into a Django ``Q`` for ``Issue``
querysets. Pipeline: `lexer` -> `parser` (AST + limits) -> `compiler` (Q).

Usage::

    ctx = PQLContext(workspace_slug=slug, user=request.user, project_id=project_id)
    issues = Issue.issue_objects.filter(compile_pql(pql, ctx))
"""

from plane.api.pql.compiler import SUPPORTED_FIELDS, PQLContext, compile_pql
from plane.api.pql.errors import PQLError
from plane.api.pql.filters import ALLOWED_FILTER_KEYS, compile_filters

__all__ = [
    "ALLOWED_FILTER_KEYS",
    "PQLContext",
    "PQLError",
    "SUPPORTED_FIELDS",
    "compile_filters",
    "compile_pql",
]
