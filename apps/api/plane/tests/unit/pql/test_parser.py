# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Lexer / parser tests for the PQL engine (FORK: PSR-85). No database needed."""

import pytest

from plane.api.pql import PQLError
from plane.api.pql.lexer import tokenize
from plane.api.pql.parser import (
    And,
    Comparison,
    FuncCall,
    ListValue,
    Literal,
    Not,
    Or,
    Predicate,
    count_conditions,
    parse,
)


def _field(node):
    assert isinstance(node, Comparison)
    return node.field


@pytest.mark.unit
class TestPrecedence:
    def test_and_binds_tighter_than_or(self):
        node = parse('a = "1" OR b = "2" AND c = "3"')
        assert isinstance(node, Or)
        assert _field(node.children[0]) == "a"
        assert isinstance(node.children[1], And)
        assert [_field(c) for c in node.children[1].children] == ["b", "c"]

    def test_not_binds_tighter_than_and(self):
        node = parse('NOT a = "1" AND b = "2"')
        assert isinstance(node, And)
        assert isinstance(node.children[0], Not)
        assert _field(node.children[0].child) == "a"

    def test_parentheses_override(self):
        node = parse('(a = "1" OR b = "2") AND c = "3"')
        assert isinstance(node, And)
        assert isinstance(node.children[0], Or)

    def test_nested_not_and_parentheses(self):
        node = parse('NOT (a = "1" OR NOT b = "2")')
        assert isinstance(node, Not)
        assert isinstance(node.child, Or)
        assert isinstance(node.child.children[1], Not)

    def test_chains_are_flattened(self):
        node = parse('a = "1" AND b = "2" AND c = "3"')
        assert isinstance(node, And) and len(node.children) == 3


@pytest.mark.unit
class TestSyntaxForms:
    def test_keywords_are_case_insensitive(self):
        node = parse('priority in ("high") and not isOverdue() or label is not empty')
        assert isinstance(node, Or)
        left, right = node.children
        assert left.children[0].op == "IN"
        assert isinstance(left.children[1], Not)
        assert right.op == "IS NOT EMPTY"

    @pytest.mark.parametrize(
        "pql,op",
        [
            ('a = "x"', "="),
            ('a != "x"', "!="),
            ('a > "x"', ">"),
            ('a >= "x"', ">="),
            ('a < "x"', "<"),
            ('a <= "x"', "<="),
            ('a ~ "x"', "~"),
            ('a IN ("x", "y")', "IN"),
            ('a NOT IN ("x")', "NOT IN"),
            ("a IS NULL", "IS NULL"),
            ("a IS NOT NULL", "IS NOT NULL"),
            ("a IS EMPTY", "IS EMPTY"),
            ("a IS NOT EMPTY", "IS NOT EMPTY"),
        ],
    )
    def test_operators(self, pql, op):
        assert parse(pql).op == op

    def test_double_quote_escapes(self):
        node = parse(r'title ~ "say \"hi\" \\ ok"')
        assert node.value == Literal("string", 'say "hi" \\ ok', 8)

    def test_single_quotes_accepted(self):
        assert parse("title ~ 'it\\'s'").value.value == "it's"

    def test_between_both_forms_equivalent(self):
        paren = parse("dueDate BETWEEN (daysAgo(7), today())")
        bare = parse("dueDate BETWEEN daysAgo(7) AND today()")
        assert paren.op == bare.op == "BETWEEN"
        assert [v.name for v in paren.value] == [v.name for v in bare.value] == ["daysAgo", "today"]

    def test_between_inside_and_chain(self):
        node = parse('dueDate BETWEEN "2024-01-01" AND "2024-02-01" AND priority = "high"')
        assert isinstance(node, And)
        assert node.children[0].op == "BETWEEN"
        assert _field(node.children[1]) == "priority"

    def test_in_with_list_function(self):
        node = parse("stateGroup IN openStates()")
        assert isinstance(node.value, FuncCall) and node.value.name == "openStates"

    def test_in_list_items(self):
        node = parse('priority IN ("high", "urgent")')
        assert isinstance(node.value, ListValue)
        assert [i.value for i in node.value.items] == ["high", "urgent"]

    def test_predicates_and_relations(self):
        node = parse('isOverdue() AND childOf("WEB-5")')
        assert all(isinstance(c, Predicate) for c in node.children)
        assert node.children[1].call.args[0].value == "WEB-5"

    def test_numbers_and_booleans(self):
        assert parse("dueDate > daysAgo(-3)").value.args[0].value == -3
        assert parse("isDraft = TRUE").value == Literal("bool", True, 10)
        assert parse("isDraft = false").value.value is False

    def test_tokens_have_positions(self):
        toks = tokenize('priority = "high"')
        assert [(t.type, t.pos) for t in toks] == [("IDENT", 0), ("OP", 9), ("STRING", 11), ("EOF", 17)]


@pytest.mark.unit
class TestSyntaxErrors:
    @pytest.mark.parametrize(
        "pql,position",
        [
            ("", 0),
            ('priority = "high', 11),
            ("priority = high", 11),
            ('(priority = "high"', 18),
            ('priority = "high")', 17),
            ('priority "high"', 9),
            ("priority = ", 11),
            ('priority = "high" AND', 21),
            ("dueDate < today() - 7", 18),
            ("dueDate < today()-7", 17),
            ('priority == "high"', 9),
            ('priority = "a" && b = "c"', 15),
            ('priority <> "a"', 9),
            ("label IS FULL", 9),
            ('priority IN "high"', 12),
            ("priority IN ()", 13),
            ("dueDate BETWEEN today() today()", 24),
            ("dueDate BETWEEN (today() today())", 25),
            ("priority = #", 11),
            ('state NOT = "x"', 10),
            ("isOverdue() = true", 12),
            ('cf["abc"] = "x"', 0),
            ("NOT", 3),
            ("()", 1),
            ("priority = NULL", 11),
            ('title contains "x"', 6),
            ("dueDate > 7d", 10),
            ('priority = "a" OR', 17),
            ("childOf(", 8),
        ],
    )
    def test_error_has_position(self, pql, position):
        with pytest.raises(PQLError) as exc:
            parse(pql)
        assert exc.value.position == position, str(exc.value)
        assert f"position {position}" in str(exc.value)

    def test_unquoted_value_hint_suggests_quotes(self):
        with pytest.raises(PQLError, match='"high"'):
            parse("priority = high")

    def test_arithmetic_hint_suggests_functions(self):
        with pytest.raises(PQLError, match="daysAgo"):
            parse("dueDate < today() - 7")


@pytest.mark.unit
class TestLimits:
    def test_five_conditions_ok(self):
        node = parse('a = "1" AND b = "2" AND c IN ("3") AND d BETWEEN (today(), today()) AND isOverdue()')
        assert count_conditions(node) == 5

    def test_not_and_parentheses_do_not_count(self):
        assert count_conditions(parse('NOT (a = "1" OR NOT b = "2")')) == 2

    def test_six_conditions_rejected(self):
        with pytest.raises(PQLError, match="6 conditions; the maximum is 5"):
            parse('a = "1" AND b = "2" AND c = "3" AND d = "4" AND e = "5" OR f = "6"')

    def test_in_list_counts_once_and_caps_at_100(self):
        ok = "priority IN (" + ", ".join(['"high"'] * 100) + ")"
        assert count_conditions(parse(ok)) == 1
        too_many = "priority IN (" + ", ".join(['"high"'] * 101) + ")"
        with pytest.raises(PQLError, match="more than 100 values"):
            parse(too_many)

    def test_length_cap(self):
        long_query = 'title ~ "' + "x" * 1990 + '"'
        assert len(long_query) == 2000
        parse(long_query)
        with pytest.raises(PQLError, match="maximum is 2000"):
            parse(long_query + " ")
