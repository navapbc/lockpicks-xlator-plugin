# /// script
# requires-python = ">=3.14"
# ///
"""Tests for Step 13c in translate_expr_to_catala (PLUGIN_IMPROVEMENTS #24).

CIVIL conditional branches like `if X then ssi_amount else 0` mix `money` and
`integer`, which Catala rejects. Step 13c coerces bare integer literals after
`then`/`else` keywords into money literals when field_type == "money".

Covers:
- Inner conditional `then ssi_amount else 0` → `then ssi_amount else $0`
- Non-zero bare integer: `else 100` → `else $100`
- Non-money fields untouched
- Decimal literals untouched (`else 0.5` stays as is)
- Already-money literals untouched (`else $0` stays as is)
- Identifiers containing `then`/`else` not affected (`else_value`)
- Nested if/then/else: every branch coerced
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import translate_expr_to_catala


def test_inner_else_zero_in_money_context_becomes_money_literal():
    result = translate_expr_to_catala(
        "if months_since_institutionalization <= 2 then ssi_amount else 0",
        field_type="money",
    )
    assert "else $0" in result
    assert "else 0" not in result.replace("else $0", "")


def test_then_integer_in_money_context_becomes_money_literal():
    result = translate_expr_to_catala(
        "if disabled then 100 else exclusion_amount",
        field_type="money",
    )
    assert "then $100" in result


def test_non_money_field_leaves_integer_alone():
    result = translate_expr_to_catala(
        "if disabled then count else 0",
        field_type="int",
    )
    assert "$" not in result
    assert "else 0" in result


def test_decimal_literal_not_coerced():
    result = translate_expr_to_catala(
        "if disabled then rate else 0.5",
        field_type="money",
    )
    assert "else $0.5" not in result
    assert "else 0.5" in result


def test_already_money_literal_unchanged():
    result = translate_expr_to_catala(
        "if disabled then ssi_amount else $0",
        field_type="money",
    )
    money_zero_count = result.count("$0")
    assert money_zero_count == 1


def test_identifier_containing_else_not_affected():
    result = translate_expr_to_catala(
        "else_value + 10",
        field_type="money",
    )
    assert "else_value" in result
    assert "$else_value" not in result


def test_nested_if_then_else_both_branches_coerced():
    result = translate_expr_to_catala(
        "if a then (if b then 100 else 0) else 0",
        field_type="money",
    )
    assert "then $100" in result
    assert result.count("else $0") == 2


def test_outer_conditional_else_zero_alone_still_coerced_by_step_11():
    """Regression guard: Step 11 already converts a standalone `0` to `$0` when the entire
    result is `0`. Step 13c should not regress that behavior."""
    result = translate_expr_to_catala("0", field_type="money")
    assert result == "$0"
