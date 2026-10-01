# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL recursive-descent parser (FORK: PSR-85).

Grammar (keywords case-insensitive; precedence NOT > AND > OR)::

    query      := or_expr EOF
    or_expr    := and_expr ( OR and_expr )*
    and_expr   := not_expr ( AND not_expr )*
    not_expr   := NOT not_expr | primary
    primary    := "(" or_expr ")"
                | call                                  -- standalone predicate / relation
                | IDENT tail                            -- field comparison
    tail       := ( "=" | "!=" | ">" | ">=" | "<" | "<=" | "~" ) value
                | [ NOT ] IN ( "(" value ( "," value )* ")" | call )
                | IS [ NOT ] ( NULL | EMPTY )
                | BETWEEN ( "(" value ( "," | AND ) value ")" | value AND value )
    value      := STRING | NUMBER | TRUE | FALSE | call
    call       := IDENT "(" [ value ( "," value )* ] ")"

The parser only checks syntax and the size limits. Whether a field, function or
operator is supported is decided by the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from plane.api.pql.errors import PQLError
from plane.api.pql.lexer import (
    ARITH,
    COMMA,
    EOF,
    IDENT,
    KEYWORD,
    LBRACKET,
    LPAREN,
    NUMBER,
    OP,
    RPAREN,
    STRING,
    Token,
    tokenize,
)

MAX_PQL_LENGTH = 2000
MAX_CONDITIONS = 5
MAX_IN_VALUES = 100

OPERATOR_HELP = "Allowed after a field: = != > >= < <= ~ IN (...) NOT IN (...) IS [NOT] NULL IS [NOT] EMPTY BETWEEN."


# --- AST ------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    """A literal value. ``kind`` is "string", "number" or "bool"; inside a
    function's argument list it may also be "ident" (a bare word such as the
    ``assignee`` in ``wasEver(assignee, ...)``), which the compiler rejects
    after it has decided whether the function itself is supported."""

    kind: str
    value: Union[str, int, float, bool]
    pos: int


@dataclass(frozen=True)
class FuncCall:
    """A function call such as ``today()`` or ``childOf("WEB-5")``."""

    name: str
    args: tuple["Value", ...]
    pos: int


Value = Union[Literal, FuncCall]


@dataclass(frozen=True)
class ListValue:
    """A parenthesised value list, as used by ``IN (...)``."""

    items: tuple[Value, ...]
    pos: int


@dataclass(frozen=True)
class Comparison:
    """``field <op> value``. ``op`` is one of the comparison operators, "IN",
    "NOT IN", "IS NULL", "IS NOT NULL", "IS EMPTY", "IS NOT EMPTY" or
    "BETWEEN". ``value`` is None for IS-forms, a (low, high) tuple for BETWEEN,
    a `ListValue` or `FuncCall` for IN-forms, and a `Value` otherwise."""

    field: str
    op: str
    value: object
    pos: int
    field_pos: int


@dataclass(frozen=True)
class Predicate:
    """A standalone function call used as a condition, e.g. ``isOverdue()``."""

    call: FuncCall
    pos: int


@dataclass(frozen=True)
class And:
    children: tuple["Node", ...]
    pos: int


@dataclass(frozen=True)
class Or:
    children: tuple["Node", ...]
    pos: int


@dataclass(frozen=True)
class Not:
    child: "Node"
    pos: int


Node = Union[Comparison, Predicate, And, Or, Not]


# --- Parser ---------------------------------------------------------------


def _describe(tok: Token) -> str:
    if tok.type == EOF:
        return "end of query"
    if tok.type == STRING:
        return f'string "{tok.value}"'
    return repr(tok.value)


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = tokenize(text)
        self.i = 0

    # token helpers
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.i + offset, len(self.tokens) - 1)]

    def advance(self) -> Token:
        tok = self.tokens[self.i]
        if tok.type != EOF:
            self.i += 1
        return tok

    def is_kw(self, word: str, tok: Token | None = None) -> bool:
        tok = tok or self.tok
        return tok.type == KEYWORD and tok.value == word

    def error(self, detail: str, tok: Token | None = None, hint: str | None = None) -> PQLError:
        tok = tok or self.tok
        return PQLError(f"{detail} (found {_describe(tok)}).", position=tok.pos, hint=hint)

    def expect(self, ttype: str, what: str, hint: str | None = None) -> Token:
        if self.tok.type != ttype:
            raise self.error(f"expected {what}", hint=hint)
        return self.advance()

    # grammar
    def parse(self) -> Node:
        if self.tok.type == EOF:
            raise PQLError(
                "the query is empty.",
                position=0,
                hint='Write at least one condition, e.g. priority = "high" or isOverdue().',
            )
        node = self.parse_or()
        if self.tok.type != EOF:
            if self.tok.type == RPAREN:
                raise self.error("unbalanced ')'", hint="Remove the extra ')' or add the matching '('.")
            raise self.error(
                "expected AND, OR or the end of the query",
                hint="Join conditions with AND / OR; quote string values in double quotes.",
            )
        return node

    def parse_or(self) -> Node:
        start = self.tok.pos
        children = [self.parse_and()]
        while self.is_kw("OR"):
            self.advance()
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else Or(tuple(children), start)

    def parse_and(self) -> Node:
        start = self.tok.pos
        children = [self.parse_not()]
        while self.is_kw("AND"):
            self.advance()
            children.append(self.parse_not())
        return children[0] if len(children) == 1 else And(tuple(children), start)

    def parse_not(self) -> Node:
        if self.is_kw("NOT"):
            tok = self.advance()
            return Not(self.parse_not(), tok.pos)
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.tok
        if tok.type == LPAREN:
            self.advance()
            if self.tok.type == RPAREN:
                raise self.error("empty parentheses", hint="Put a condition inside ( ).")
            node = self.parse_or()
            self.expect(RPAREN, "')' to close the '(' opened at position %d" % tok.pos)
            return node
        if tok.type == IDENT:
            nxt = self.peek()
            if nxt.type == LPAREN:
                call = self.parse_call()
                if self.tok.type == OP or self.is_kw("IN") or self.is_kw("IS") or self.is_kw("BETWEEN"):
                    raise self.error(
                        f"function {call.name}() is a standalone condition and cannot be compared",
                        hint=f"Write {call.name}() on its own (e.g. isDraft() instead of isDraft() = true), "
                        "or put a field on the left: e.g. dueDate < today().",
                    )
                return Predicate(call, call.pos)
            if nxt.type == LBRACKET:
                raise PQLError(
                    f'custom properties ({tok.value}["..."]) are not available on this Plane edition.',
                    position=tok.pos,
                    hint="Remove this condition; filter by label, state, priority or another built-in field instead.",
                )
            self.advance()
            return self.parse_tail(tok)
        if tok.type == EOF:
            raise self.error(
                "the query ends where a condition was expected",
                hint="Add a condition after AND / OR / NOT, or remove the trailing keyword.",
            )
        raise self.error(
            "expected a field name, a function call, NOT or '('",
            hint='Conditions look like: priority = "high", label IS EMPTY, isOverdue(), childOf("WEB-5").',
        )

    def parse_tail(self, field_tok: Token) -> Comparison:
        field = field_tok.value
        tok = self.tok
        if tok.type == OP:
            self.advance()
            value = self.parse_value()
            return Comparison(field, tok.value, value, tok.pos, field_tok.pos)
        if self.is_kw("NOT"):
            self.advance()
            if not self.is_kw("IN"):
                raise self.error(
                    f"expected IN after '{field} NOT'",
                    hint="Use 'NOT IN (...)' here, or put NOT before the whole condition: NOT field = \"x\".",
                )
            self.advance()
            return Comparison(field, "NOT IN", self.parse_in_values(), tok.pos, field_tok.pos)
        if self.is_kw("IN"):
            self.advance()
            return Comparison(field, "IN", self.parse_in_values(), tok.pos, field_tok.pos)
        if self.is_kw("IS"):
            self.advance()
            negate = False
            if self.is_kw("NOT"):
                self.advance()
                negate = True
            if self.is_kw("NULL") or self.is_kw("EMPTY"):
                word = self.advance().value
                return Comparison(field, f"IS NOT {word}" if negate else f"IS {word}", None, tok.pos, field_tok.pos)
            raise self.error(
                "expected NULL or EMPTY after IS", hint="Use IS NULL, IS NOT NULL, IS EMPTY or IS NOT EMPTY."
            )
        if self.is_kw("BETWEEN"):
            self.advance()
            return Comparison(field, "BETWEEN", self.parse_between(), tok.pos, field_tok.pos)
        if tok.type == IDENT and tok.value.lower() in ("contains", "like"):
            raise self.error(f"unsupported operator {tok.value!r}", hint=f"Use ~ for contains. {OPERATOR_HELP}")
        raise self.error(f"expected an operator after field '{field}'", hint=OPERATOR_HELP)

    def parse_between(self) -> tuple[Value, Value]:
        if self.tok.type == LPAREN:
            open_tok = self.advance()
            low = self.parse_value()
            if self.tok.type == COMMA or self.is_kw("AND"):
                self.advance()
            else:
                raise self.error("expected ',' between the two BETWEEN bounds", hint="Write BETWEEN (low, high).")
            high = self.parse_value()
            self.expect(RPAREN, "')' to close BETWEEN ( opened at position %d" % open_tok.pos)
            return low, high
        low = self.parse_value()
        if not self.is_kw("AND"):
            raise self.error(
                "expected AND between the two BETWEEN bounds",
                hint="Write BETWEEN low AND high, or BETWEEN (low, high).",
            )
        self.advance()
        return low, self.parse_value()

    def parse_in_values(self) -> ListValue | FuncCall:
        tok = self.tok
        if tok.type == IDENT and self.peek().type == LPAREN:
            return self.parse_call()
        if tok.type != LPAREN:
            raise self.error(
                "expected '(' after IN",
                hint='Write IN ("a", "b") with parentheses, or IN followed by a list function such as openStates().',
            )
        self.advance()
        if self.tok.type == RPAREN:
            raise self.error("empty IN list", hint="Put at least one value inside IN ( ).")
        items = [self.parse_value()]
        while self.tok.type == COMMA:
            self.advance()
            items.append(self.parse_value())
            if len(items) > MAX_IN_VALUES:
                raise PQLError(
                    f"IN list has more than {MAX_IN_VALUES} values.",
                    position=tok.pos,
                    hint=f"Use at most {MAX_IN_VALUES} values per IN list; split the request into several queries.",
                )
        self.expect(RPAREN, "',' or ')' in the IN list opened at position %d" % tok.pos)
        return ListValue(tuple(items), tok.pos)

    def parse_value(self) -> Value:
        tok = self.tok
        if tok.type == STRING:
            self.advance()
            value: Value = Literal("string", tok.value, tok.pos)
        elif tok.type == NUMBER:
            self.advance()
            num: Union[int, float] = float(tok.value) if "." in tok.value else int(tok.value)
            value = Literal("number", num, tok.pos)
        elif self.is_kw("TRUE") or self.is_kw("FALSE"):
            self.advance()
            value = Literal("bool", tok.value == "TRUE", tok.pos)
        elif tok.type == IDENT and self.peek().type == LPAREN:
            value = self.parse_call()
        elif tok.type == IDENT:
            raise self.error(
                "unquoted value",
                hint=f'Put string values in double quotes, e.g. "{tok.value}", or call a function with (), '
                f"e.g. {tok.value}().",
            )
        elif self.is_kw("NULL") or self.is_kw("EMPTY"):
            raise self.error(f"{tok.value} cannot be compared with an operator", hint=f"Write IS {tok.value} instead.")
        else:
            raise self.error(
                "expected a value",
                hint='Values are double-quoted strings ("high"), numbers, true/false, or function calls (today()).',
            )
        if self.tok.type == ARITH:
            raise self.error(
                "date arithmetic is not supported",
                hint="Never write today() - 7; use daysAgo(7), daysFromNow(7), weeksAgo(n), monthsAgo(n), etc.",
            )
        return value

    def parse_arg(self) -> Value:
        tok = self.tok
        if tok.type == IDENT and self.peek().type != LPAREN:
            self.advance()
            return Literal("ident", tok.value, tok.pos)
        return self.parse_value()

    def parse_call(self) -> FuncCall:
        name_tok = self.expect(IDENT, "a function name")
        self.expect(LPAREN, f"'(' after {name_tok.value}")
        args: list[Value] = []
        if self.tok.type != RPAREN:
            args.append(self.parse_arg())
            while self.tok.type == COMMA:
                self.advance()
                args.append(self.parse_arg())
        self.expect(RPAREN, f"')' to close {name_tok.value}(")
        return FuncCall(name_tok.value, tuple(args), name_tok.pos)


def count_conditions(node: Node) -> int:
    """Number of conditions per the PQL limits: each comparison, IN, BETWEEN,
    predicate and relation call counts as one."""
    if isinstance(node, (Comparison, Predicate)):
        return 1
    if isinstance(node, Not):
        return count_conditions(node.child)
    return sum(count_conditions(child) for child in node.children)


def parse(text: str) -> Node:
    """Parse a PQL string into an AST, enforcing the length and condition limits.

    Raises:
        PQLError: on any syntax error (with position) or exceeded limit.
    """
    if not isinstance(text, str):
        raise PQLError("pql must be a string.")
    if len(text) > MAX_PQL_LENGTH:
        raise PQLError(
            f"the query is {len(text)} characters long; the maximum is {MAX_PQL_LENGTH}.",
            hint="Shorten the query or split it into several requests.",
        )
    node = _Parser(text).parse()
    conditions = count_conditions(node)
    if conditions > MAX_CONDITIONS:
        raise PQLError(
            f"the query has {conditions} conditions; the maximum is {MAX_CONDITIONS}.",
            hint="Each comparison, IN, BETWEEN, predicate or relation call counts as one. Merge equality "
            'checks into one IN list (priority IN ("high", "urgent") counts once) or split into several requests.',
        )
    return node
