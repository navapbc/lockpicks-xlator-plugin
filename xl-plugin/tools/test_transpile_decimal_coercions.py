# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for decimal/integer coercion fixes (steps 9.5, 9.6, 9.7) and money constant recognition.

Catala is strictly typed: integer × decimal, decimal × integer, decimal vs integer,
and money × integer are all type errors. These tests verify that the transpiler
emits the required 'decimal of' coercions.

Covers:
Fix A1 — constant_to_catala: money-amount suffixes (_WAGE etc.) recognized before float check
Fix A2 — Step 9.5: integer field × percentage → (decimal of field) × percentage
Fix A3 — Step 9.6a: decimal field >= integer literal → decimal field >= decimal of N
          Step 9.6b: integer field >= decimal expression → (decimal of field) >= decimal expr
Fix A4 — Step 9.7: $money_literal * integer_literal → $money_literal * (decimal of N)
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import constant_to_catala, translate_expr_to_catala, transpile


# =============================================================================
# Fix A1 — constant_to_catala money suffix recognition
# =============================================================================

def test_wage_suffix_float_becomes_money():
    assert constant_to_catala("FEDERAL_MINIMUM_WAGE", 7.25) == "$7.25"


def test_wage_suffix_int_becomes_money():
    assert constant_to_catala("MINIMUM_WAGE", 8) == "$8"


def test_rate_suffix_unaffected():
    assert constant_to_catala("HALF_TIME_CREDIT_FRACTION", 0.5) == "50%"


def test_plain_float_still_becomes_percent():
    assert constant_to_catala("MULTIPLIER", 4.33) == "433%"


def test_cap_suffix_unaffected():
    assert constant_to_catala("NET_INCOME_CAP", 1500) == "$1,500"


# =============================================================================
# Fix A2 — Step 9.5: integer × percentage coercion
# =============================================================================

def test_step_9_5_int_lhs_pct_rhs_coerced():
    result = translate_expr_to_catala(
        "hours_per_week * 433%",
        field_type_map={"hours_per_week": "int"},
    )
    assert result == "(decimal of hours_per_week) * 433%"


def test_step_9_5_pct_lhs_int_rhs_coerced():
    result = translate_expr_to_catala(
        "50% * credits_per_term_to_graduate_on_track",
        field_type_map={"credits_per_term_to_graduate_on_track": "int"},
    )
    assert result == "50% * (decimal of credits_per_term_to_graduate_on_track)"


def test_step_9_5_float_field_not_coerced():
    """A decimal-typed field next to a percentage should NOT get 'decimal of' wrapping."""
    result = translate_expr_to_catala(
        "some_rate * 50%",
        field_type_map={"some_rate": "float"},
    )
    assert "decimal of some_rate" not in result


def test_step_9_5_no_field_type_map_no_coercion():
    """Without field_type_map the coercion must not fire (would be wrong for non-int fields)."""
    result = translate_expr_to_catala("hours * 100%")
    assert "decimal of" not in result


# =============================================================================
# Fix A3 — Step 9.6a/b: comparison coercions
# =============================================================================

def test_step_9_6a_decimal_field_vs_int_literal():
    result = translate_expr_to_catala(
        "average_monthly_hours >= 80",
        field_type_map={"average_monthly_hours": "float"},
    )
    assert result == "average_monthly_hours >= decimal of 80"


def test_step_9_6a_int_field_vs_int_literal_unchanged():
    """Integer-typed field compared to integer literal should not be coerced."""
    result = translate_expr_to_catala(
        "hours_per_week >= 20",
        field_type_map={"hours_per_week": "int"},
    )
    assert "decimal of" not in result
    assert "hours_per_week >= 20" in result


def test_step_9_6b_int_field_vs_pct_expr():
    result = translate_expr_to_catala(
        "current_term_credits >= 50% * (decimal of credits_per_term_to_graduate_on_track)",
        field_type_map={"current_term_credits": "int"},
    )
    assert result.startswith("(decimal of current_term_credits) >=")


def test_step_9_6b_does_not_span_or_boundary():
    """Greedy `.+` previously consumed past `or` into a downstream decimal clause,
    wrongly wrapping an int field whose own RHS was a bare integer literal."""
    result = translate_expr_to_catala(
        "hours_per_week >= 20 or average_monthly_hours >= 80",
        field_type_map={"hours_per_week": "int", "average_monthly_hours": "float"},
    )
    # hours_per_week's RHS is `20` (int literal) — no coercion needed.
    assert "(decimal of hours_per_week)" not in result
    # average_monthly_hours is float vs int literal — 9.6a coerces the literal.
    assert "average_monthly_hours >= decimal of 80" in result


def test_step_9_6b_does_not_span_and_boundary():
    """`and` boundary must terminate the RHS just like `or`."""
    result = translate_expr_to_catala(
        "credits >= 12 and ratio >= 50% * (decimal of total)",
        field_type_map={"credits": "int", "ratio": "int", "total": "int"},
    )
    # credits's RHS is `12` — no coercion.
    assert "(decimal of credits)" not in result
    # ratio's RHS contains `%` and `decimal of` — must coerce.
    assert "(decimal of ratio) >=" in result


def test_step_9_6b_terminates_at_closing_paren():
    """RHS must stop at a closing paren so a coercion inside parens does not
    leak into an outer comparison's RHS that begins right after."""
    result = translate_expr_to_catala(
        "(a >= 1) and (b >= 50% * (decimal of c))",
        field_type_map={"a": "int", "b": "int", "c": "int"},
    )
    assert "(decimal of a)" not in result
    assert "(decimal of b) >=" in result


# =============================================================================
# Fix A4 — Step 9.7: money literal × integer literal
# =============================================================================

def test_step_9_7_money_times_int_literal():
    result = translate_expr_to_catala("$7.25 * 20")
    assert result == "$7.25 * (decimal of 20)"


def test_step_9_7_money_times_pct_unaffected():
    """$N * M% is money × decimal — already valid in Catala, must not be changed."""
    result = translate_expr_to_catala("$7.25 * 50%")
    assert "decimal of" not in result


def test_step_9_7_money_with_thousands_sep():
    result = translate_expr_to_catala("$1,500 * 12")
    assert result == "$1,500 * (decimal of 12)"


# =============================================================================
# Integration — CIVIL spec with all patterns transpiles cleanly
# =============================================================================

_DECIMAL_CIVIL = {
    "module": "decimal_coerce_test",
    "description": "Integration test for decimal coercions.",
    "version": "1.0",
    "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
    "effective": {"start": "2026-01-01"},
    "inputs": {
        "Applicant": {
            "fields": {
                "hours_per_week": {"type": "int"},
                "weekly_gross_earnings": {"type": "money"},
                "current_term_credits": {"type": "int"},
                "credits_per_term_to_graduate_on_track": {"type": "int"},
                "is_self_employed": {"type": "bool"},
            }
        }
    },
    "constants": {
        "WEEKS_TO_MONTHS_MULTIPLIER": 4.33,
        "WORK_HOURS_PER_WEEK_THRESHOLD": 20,
        "WORK_HOURS_PER_MONTH_THRESHOLD": 80,
        "HALF_TIME_CREDIT_FRACTION": 0.5,
        "FEDERAL_MINIMUM_WAGE": 7.25,
    },
    "computed": {
        "average_monthly_hours": {
            "type": "float",
            "tags": ["expose"],
            "expr": "Applicant.hours_per_week * WEEKS_TO_MONTHS_MULTIPLIER",
        },
        "is_half_time": {
            "type": "bool",
            "tags": ["expose"],
            "expr": (
                "Applicant.current_term_credits >= "
                "HALF_TIME_CREDIT_FRACTION * Applicant.credits_per_term_to_graduate_on_track"
            ),
        },
        "min_wage_threshold": {
            "type": "money",
            "tags": ["expose"],
            "expr": "FEDERAL_MINIMUM_WAGE * WORK_HOURS_PER_WEEK_THRESHOLD",
        },
    },
    "outputs": {
        "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
        "reasons": {"type": "list", "item": "Reason", "default": []},
    },
    "rules": [
        {
            "name": "paid_work",
            "when": (
                "Applicant.hours_per_week >= WORK_HOURS_PER_WEEK_THRESHOLD or "
                "average_monthly_hours >= WORK_HOURS_PER_MONTH_THRESHOLD"
            ),
            "then": [{"allow": True}],
        },
        {
            "name": "min_wage_check",
            "when": (
                "Applicant.weekly_gross_earnings >= "
                "FEDERAL_MINIMUM_WAGE * WORK_HOURS_PER_WEEK_THRESHOLD"
            ),
            "then": [{"allow": True}],
        },
    ],
}


def test_decimal_coercion_integration_no_raw_pct_times_int(tmp_path):
    civil_path = str(tmp_path / "decimal_coerce_test.civil.yaml")
    output_path = str(tmp_path / "decimal_coerce_test.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(_DECIMAL_CIVIL, fh)

    transpile(_DECIMAL_CIVIL, output_path, "DecimalCoerceTestDecision", civil_path=civil_path)
    result = open(output_path).read()

    # The raw percentage-times-integer form must not appear in output
    import re
    assert not re.search(r"\b\w+\s*\*\s*\d+%", result), (
        "Found unrewritten integer × percentage in output"
    )
    assert not re.search(r"\d+%\s*\*\s*\w+\b(?!\s*\(decimal)", result), (
        "Found unrewritten percentage × integer in output"
    )


def test_decimal_coercion_integration_average_monthly_hours(tmp_path):
    civil_path = str(tmp_path / "decimal_coerce_test.civil.yaml")
    output_path = str(tmp_path / "decimal_coerce_test.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(_DECIMAL_CIVIL, fh)

    transpile(_DECIMAL_CIVIL, output_path, "DecimalCoerceTestDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "(decimal of hours_per_week) * 433%" in result


def test_decimal_coercion_integration_federal_wage_is_money(tmp_path):
    civil_path = str(tmp_path / "decimal_coerce_test.civil.yaml")
    output_path = str(tmp_path / "decimal_coerce_test.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(_DECIMAL_CIVIL, fh)

    transpile(_DECIMAL_CIVIL, output_path, "DecimalCoerceTestDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "$7.25" in result
    assert "725%" not in result
