# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL lexer (FORK: PSR-85).

Turns a PQL string into a flat list of `Token`s. Keywords are matched
case-insensitively; identifiers keep their spelling (field and function names
are resolved case-insensitively later, by the compiler).
"""

from __future__ import annotations

from dataclasses import dataclass

from plane.api.pql.errors import PQLError

# Token types
IDENT = "IDENT"
STRING = "STRING"
NUMBER = "NUMBER"
KEYWORD = "KEYWORD"
OP = "OP"
LPAREN = "LPAREN"
RPAREN = "RPAREN"
LBRACKET = "LBRACKET"
RBRACKET = "RBRACKET"
COMMA = "COMMA"
ARITH = "ARITH"
EOF = "EOF"

KEYWORDS = frozenset({"AND", "OR", "NOT", "IN", "IS", "NULL", "EMPTY", "BETWEEN", "TRUE", "FALSE"})

# Longest first so ">=" wins over ">".
OPERATORS = ("!=", ">=", "<=", "=", ">", "<", "~")

# Common operator spellings from other query languages, mapped to a fix.
_MISTAKES = {
    "==": "Use a single '=' for equality.",
    "<>": "Use '!=' for inequality.",
    "&&": "Use the keyword AND.",
    "||": "Use the keyword OR.",
    "!~": "PQL has no '!~'; write NOT title ~ \"...\" instead.",
}

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}

# A '+'/'-' directly followed by a digit is a signed number only where a value
# can start; after a value it would be date arithmetic, which PQL forbids.
_VALUE_END = frozenset({IDENT, STRING, NUMBER, RPAREN, RBRACKET})


@dataclass(frozen=True)
class Token:
    """One lexical token. ``value`` is the decoded text (strings unescaped,
    keywords upper-cased); ``pos`` is the 0-based offset of its first char."""

    type: str
    value: str
    pos: int


def _read_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    i = start + 1
    out: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text):
                break
            nxt = text[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise PQLError(
        f"unterminated string starting with {quote}.",
        position=start,
        hint=f'Close the string with a matching {quote}; escape an embedded quote as \\{quote}, e.g. "say \\"hi\\"".',
    )


def _read_number(text: str, start: int) -> tuple[str, int]:
    i = start
    if text[i] in "+-":
        i += 1
    while i < len(text) and text[i].isdigit():
        i += 1
    if i < len(text) and text[i] == "." and i + 1 < len(text) and text[i + 1].isdigit():
        i += 1
        while i < len(text) and text[i].isdigit():
            i += 1
    if i < len(text) and (text[i].isalpha() or text[i] == "_"):
        raise PQLError(
            f"invalid number near {text[start : i + 1]!r}.",
            position=start,
            hint='Numbers are plain digits (e.g. daysAgo(7)); quote dates and identifiers, e.g. "2024-01-31".',
        )
    return text[start:i], i


def tokenize(text: str) -> list[Token]:
    """Split ``text`` into tokens, ending with an EOF token.

    Raises:
        PQLError: on an unterminated string or a character PQL does not use.
    """
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "\"'":
            value, i_next = _read_string(text, i)
            tokens.append(Token(STRING, value, i))
            i = i_next
            continue
        two = text[i : i + 2]
        if two in _MISTAKES:
            raise PQLError(f"unsupported operator {two!r}.", position=i, hint=_MISTAKES[two])
        prev_type = tokens[-1].type if tokens else None
        if ch.isdigit() or (ch in "+-" and i + 1 < n and text[i + 1].isdigit() and prev_type not in _VALUE_END):
            value, i_next = _read_number(text, i)
            tokens.append(Token(NUMBER, value, i))
            i = i_next
            continue
        if ch in "+-":
            tokens.append(Token(ARITH, ch, i))
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            if word.upper() in KEYWORDS:
                tokens.append(Token(KEYWORD, word.upper(), i))
            else:
                tokens.append(Token(IDENT, word, i))
            i = j
            continue
        op = next((o for o in OPERATORS if text.startswith(o, i)), None)
        if op is not None:
            tokens.append(Token(OP, op, i))
            i += len(op)
            continue
        single = {"(": LPAREN, ")": RPAREN, "[": LBRACKET, "]": RBRACKET, ",": COMMA}.get(ch)
        if single is not None:
            tokens.append(Token(single, ch, i))
            i += 1
            continue
        raise PQLError(
            f"unexpected character {ch!r}.",
            position=i,
            hint="PQL uses field names, double-quoted strings, numbers, function calls like today(), "
            "the operators = != > >= < <= ~, and the keywords AND OR NOT IN IS NULL EMPTY BETWEEN.",
        )
    tokens.append(Token(EOF, "", n))
    return tokens
