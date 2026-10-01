# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL error type (FORK: PSR-85).

Every failure in the PQL engine -- lexing, parsing, semantic validation, limits,
unsupported features and the structured ``filters`` dict -- raises `PQLError`.
Messages are written for an AI agent that will read them and retry: they say
what is wrong, where (0-based character offset for positional errors), and what
is allowed instead.
"""

from __future__ import annotations


class PQLError(ValueError):
    """A PQL query (or ``filters`` dict) that cannot be evaluated.

    Attributes:
        detail: What went wrong, without the position prefix.
        position: 0-based character offset into the PQL string, when known.
        hint: What the caller can do instead (allowed values, alternatives).
    """

    def __init__(self, detail: str, position: int | None = None, hint: str | None = None) -> None:
        self.detail = detail
        self.position = position
        self.hint = hint
        super().__init__(self._render())

    def _render(self) -> str:
        if self.position is not None:
            message = f"PQL error at position {self.position}: {self.detail}"
        else:
            message = f"PQL error: {self.detail}"
        if self.hint:
            message = f"{message} {self.hint}"
        return message

    @property
    def message(self) -> str:
        """The full human-readable message (same as ``str(error)``)."""
        return str(self)

    def as_dict(self) -> dict:
        """Structured form for API error payloads."""
        return {"error": str(self), "detail": self.detail, "position": self.position, "hint": self.hint}
