# /// script
# requires-python = ">=3.14"
# ///
"""Tests for Fix #23.2 — broader conditional branch type-check (PLUGIN_IMPROVEMENTS #23).

Covers:
- _infer_civil_type: money/int/date/bool literals, field refs, complex expressions
- _check_cond_branch_type_compat: same-type pass, mismatch fail, complex-skip
- translate_expr_to_catala Step 14: inner IDENT mismatch detected, arithmetic skipped
- Backward compatibility: no field_type_map → no new check
- Regression: Step 13c bare-int coercion unaffected
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import (
    _build_all_field_type_map,
    _check_cond_branch_type_compat,
    _infer_civil_type,
    translate_expr_to_catala,
)


# ---------------------------------------------------------------------------
# _infer_civil_type
# ---------------------------------------------------------------------------

MONEY_MAP = {"payment": "money", "ssi_amount": "money", "benefit": "money"}
MIXED_MAP = {
    "payment": "money",
    "ssi_amount": "money",
    "status_code": "string",
    "effective_date": "date",
    "household_size": "int",
    "is_eligible": "bool",
}


def test_infer_money_literal():
    assert _infer_civil_type("$500", MIXED_MAP) == "money"


def test_infer_money_literal_with_cents():
    assert _infer_civil_type("$1,234.56", MIXED_MAP) == "money"


def test_infer_integer_literal():
    assert _infer_civil_type("42", MIXED_MAP) == "int"


def test_infer_zero_literal():
    assert _infer_civil_type("0", MIXED_MAP) == "int"


def test_infer_date_literal():
    assert _infer_civil_type("|2024-01-01|", MIXED_MAP) == "date"


def test_infer_true_literal():
    assert _infer_civil_type("true", MIXED_MAP) == "bool"


def test_infer_false_literal():
    assert _infer_civil_type("false", MIXED_MAP) == "bool"


def test_infer_known_money_field():
    assert _infer_civil_type("payment", MIXED_MAP) == "money"


def test_infer_known_string_field():
    assert _infer_civil_type("status_code", MIXED_MAP) == "string"


def test_infer_known_date_field():
    assert _infer_civil_type("effective_date", MIXED_MAP) == "date"


def test_infer_known_int_field():
    assert _infer_civil_type("household_size", MIXED_MAP) == "int"


def test_infer_entity_prefixed_field():
    assert _infer_civil_type("Household.payment", MIXED_MAP) == "money"


def test_infer_unknown_field_returns_none():
    assert _infer_civil_type("unknown_field", MIXED_MAP) is None


def test_infer_complex_if_expression_returns_none():
    assert _infer_civil_type("if x then a else b", MIXED_MAP) is None


def test_infer_arithmetic_expression_returns_none():
    assert _infer_civil_type("a + b", MIXED_MAP) is None


def test_infer_max_call_returns_none():
    assert _infer_civil_type("max(a, b)", MIXED_MAP) is None


def test_infer_whitespace_stripped():
    assert _infer_civil_type("  payment  ", MIXED_MAP) == "money"


# ---------------------------------------------------------------------------
# _check_cond_branch_type_compat
# ---------------------------------------------------------------------------


def test_compat_check_passes_same_type():
    # Both money — no exception raised
    _check_cond_branch_type_compat("ssi_amount", "payment", "benefit_field", MIXED_MAP)


def test_compat_check_passes_literal_same_type():
    # $0 (money) and payment (money) — compatible
    _check_cond_branch_type_compat("$0", "payment", "benefit_field", MIXED_MAP)


def test_compat_check_fails_string_vs_money():
    with pytest.raises(ValueError, match="incompatible types"):
        _check_cond_branch_type_compat("ssi_amount", "status_code", "benefit_field", MIXED_MAP)


def test_compat_check_error_names_field():
    with pytest.raises(ValueError, match="benefit_field"):
        _check_cond_branch_type_compat("ssi_amount", "status_code", "benefit_field", MIXED_MAP)


def test_compat_check_error_names_both_types():
    with pytest.raises(ValueError, match="money") as exc_info:
        _check_cond_branch_type_compat("ssi_amount", "status_code", "benefit_field", MIXED_MAP)
    assert "string" in str(exc_info.value)


def test_compat_check_fails_date_vs_money():
    with pytest.raises(ValueError, match="incompatible types"):
        _check_cond_branch_type_compat("payment", "effective_date", "some_field", MIXED_MAP)


def test_compat_check_fails_int_field_vs_money():
    # Integer field *reference* in money context is still an error — cannot auto-coerce
    with pytest.raises(ValueError, match="incompatible types"):
        _check_cond_branch_type_compat(
            "ssi_amount", "household_size", "some_field", MIXED_MAP, field_type="money"
        )


def test_compat_check_passes_bare_int_literal_in_money_context():
    # Bare integer literal "0" in money-typed field — Step 11/13c will coerce it; not a real error
    _check_cond_branch_type_compat(
        "ssi_amount", "0", "ssa_cola_disregarded", MIXED_MAP, field_type="money"
    )


def test_compat_check_passes_nonzero_int_literal_in_money_context():
    # Non-zero bare integer literal also coercible by Step 13c
    _check_cond_branch_type_compat(
        "100", "ssi_amount", "some_amount", MIXED_MAP, field_type="money"
    )


def test_compat_check_fails_int_literal_in_non_money_context():
    # Same bare int "0" but field is NOT money — int vs money is still a real mismatch
    with pytest.raises(ValueError, match="incompatible types"):
        _check_cond_branch_type_compat(
            "ssi_amount", "0", "some_field", MIXED_MAP, field_type="int"
        )


def test_compat_check_skips_when_then_is_complex():
    # Complex then expression → None → skip check entirely, no error
    _check_cond_branch_type_compat(
        "if X then a else b", "status_code", "some_field", MIXED_MAP
    )


def test_compat_check_skips_when_else_is_complex():
    # Complex else expression → None → skip
    _check_cond_branch_type_compat(
        "ssi_amount", "if X then a else b", "some_field", MIXED_MAP
    )


def test_compat_check_skips_unknown_fields():
    # Both fields unknown → both None → skip
    _check_cond_branch_type_compat("field_a", "field_b", "some_field", {})


def test_compat_check_handles_entity_prefixed_refs():
    with pytest.raises(ValueError, match="incompatible types"):
        _check_cond_branch_type_compat(
            "Household.ssi_amount", "Household.status_code", "benefit_field", MIXED_MAP
        )


# ---------------------------------------------------------------------------
# translate_expr_to_catala Step 14 — inner IDENT mismatch detection
# ---------------------------------------------------------------------------


def test_step14_catches_inner_string_vs_money_mismatch():
    with pytest.raises(ValueError, match="incompatible types"):
        translate_expr_to_catala(
            "if is_eligible then ssi_amount else status_code",
            field_type_map=MIXED_MAP,
        )


def test_step14_passes_when_both_branches_same_type():
    # Both money — no error
    result = translate_expr_to_catala(
        "if is_eligible then payment else ssi_amount",
        field_type_map=MIXED_MAP,
    )
    assert "payment" in result
    assert "ssi_amount" in result


def test_step14_skips_arithmetic_branch():
    # `then ssi_amount + benefit` — ssi_amount is followed by `+`, not matched as sole branch
    result = translate_expr_to_catala(
        "if is_eligible then ssi_amount + benefit else $0",
        field_type="money",
        field_type_map=MIXED_MAP,
    )
    # Should not raise — the then-branch is arithmetic, not a simple identifier
    assert "ssi_amount" in result


def test_step14_skips_paren_branch():
    # `then (ssi_amount)` — ssi_amount is followed by `)`, not an arithmetic op; but it IS inside
    # parens so the outer `then (` doesn't match the simple-IDENT regex
    result = translate_expr_to_catala(
        "if is_eligible then (ssi_amount) else $0",
        field_type="money",
        field_type_map=MIXED_MAP,
    )
    assert "ssi_amount" in result


def test_step14_absent_without_field_type_map():
    # No field_type_map → no check → no error even with "mismatched" names
    result = translate_expr_to_catala(
        "if is_eligible then ssi_amount else status_code",
    )
    assert "ssi_amount" in result
    assert "status_code" in result


def test_step14_unknown_fields_not_flagged():
    # Neither identifier in map → both None → no error
    result = translate_expr_to_catala(
        "if cond then field_a else field_b",
        field_type_map={},
    )
    assert "field_a" in result


# ---------------------------------------------------------------------------
# Step 13c regression — bare-int coercion must still work
# ---------------------------------------------------------------------------


def test_step13c_regression_bare_int_coerced_before_step14():
    """Step 13c coerces `else 0` → `else $0` before Step 14 runs.

    After coercion, `$0` is a money literal — _infer_civil_type returns "money",
    which is compatible with the `then ssi_amount` branch (also money).
    No ValueError should be raised.
    """
    result = translate_expr_to_catala(
        "if months <= 2 then ssi_amount else 0",
        field_type="money",
        field_type_map=MIXED_MAP,
    )
    assert "else $0" in result
    assert "else 0" not in result.replace("else $0", "")


def test_step13c_non_zero_int_coerced_before_step14():
    result = translate_expr_to_catala(
        "if months <= 2 then ssi_amount else 100",
        field_type="money",
        field_type_map=MIXED_MAP,
    )
    assert "else $100" in result


# ---------------------------------------------------------------------------
# _build_all_field_type_map
# ---------------------------------------------------------------------------


def test_build_field_type_map_from_inputs():
    civil_doc = {
        "inputs": {
            "Household": {
                "fields": {
                    "gross_income": {"type": "money"},
                    "household_size": {"type": "int"},
                    "application_date": {"type": "date"},
                }
            }
        }
    }
    field_map = _build_all_field_type_map(civil_doc)
    assert field_map["gross_income"] == "money"
    assert field_map["household_size"] == "int"
    assert field_map["application_date"] == "date"


def test_build_field_type_map_from_computed():
    civil_doc = {
        "computed": {
            "net_income": {"type": "money"},
            "is_eligible": {"type": "bool"},
        }
    }
    field_map = _build_all_field_type_map(civil_doc)
    assert field_map["net_income"] == "money"
    assert field_map["is_eligible"] == "bool"


def test_build_field_type_map_defaults_to_money():
    civil_doc = {
        "computed": {
            "some_amount": {},  # no type: key
        }
    }
    field_map = _build_all_field_type_map(civil_doc)
    assert field_map["some_amount"] == "money"


def test_build_field_type_map_merges_inputs_and_computed():
    civil_doc = {
        "inputs": {
            "Client": {
                "fields": {
                    "gross_income": {"type": "money"},
                }
            }
        },
        "computed": {
            "net_income": {"type": "money"},
        },
    }
    field_map = _build_all_field_type_map(civil_doc)
    assert "gross_income" in field_map
    assert "net_income" in field_map


def test_build_field_type_map_empty_doc():
    assert _build_all_field_type_map({}) == {}


# ---------------------------------------------------------------------------
# End-to-end: emit_computed_section_catala raises ValueError on type mismatch
# (Finding 1 / Finding 4 — ensures ValueError surfaces cleanly from emit layer)
# ---------------------------------------------------------------------------


def test_emit_computed_raises_value_error_on_mismatch():
    """emit_computed_section_catala raises ValueError when then/else have incompatible types.

    This exercises the full Level 1 outer check path including the field_type_map wiring,
    ensuring the error propagates cleanly rather than being silently swallowed.
    """
    from transpile_to_catala import emit_computed_section_catala

    civil_type_map = {"payment_amount": "money", "status_code": "string"}
    computed = {
        "benefit_amount": {
            "type": "money",
            "conditional": {
                "if": "Client.is_active",
                "then": "Client.payment_amount",  # money
                "else": "Client.status_code",     # string — real mismatch
            },
        }
    }
    with pytest.raises(ValueError, match="incompatible types"):
        emit_computed_section_catala(
            computed,
            scope_name="TestScope",
            constants={},
            tables={},
            field_type_map=civil_type_map,
        )


def test_emit_computed_allows_bare_int_literal_in_money_field():
    """Regression: then: money_field, else: "0" must NOT raise — Step 11 coerces "0" → "$0".

    Mirrors the real ssa_cola_disregarded failure (medicaid_income_exceptions).
    """
    from transpile_to_catala import emit_computed_section_catala

    civil_type_map = {"ssa_cola_increase_amount": "money"}
    computed = {
        "ssa_cola_disregarded": {
            "type": "money",
            "conditional": {
                "if": "Household.is_cola_eligible",
                "then": "Household.ssa_cola_increase_amount",  # money
                "else": "0",                                   # bare int literal — coercible
            },
        }
    }
    chunks = emit_computed_section_catala(
        computed,
        scope_name="TestScope",
        constants={},
        tables={},
        field_type_map=civil_type_map,
    )
    assert len(chunks) == 1
    code = "\n".join(chunks[0][3])
    assert "else $0" in code


def test_emit_computed_no_error_on_compatible_types():
    from transpile_to_catala import emit_computed_section_catala

    civil_type_map = {"payment_amount": "money", "benefit": "money"}
    computed = {
        "net_benefit": {
            "type": "money",
            "conditional": {
                "if": "Client.is_active",
                "then": "Client.payment_amount",
                "else": "Client.benefit",
            },
        }
    }
    chunks = emit_computed_section_catala(
        computed,
        scope_name="TestScope",
        constants={},
        tables={},
        field_type_map=civil_type_map,
    )
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Rules section coverage (Finding 2)
# ---------------------------------------------------------------------------


def test_rules_section_receives_field_type_map_via_translate_condition():
    """translate_condition_to_catala now accepts field_type_map and passes it to Step 14.

    A rule when: expression with inner if/then/else type mismatch should raise ValueError.
    """
    from transpile_to_catala import translate_condition_to_catala

    civil_type_map = {"payment": "money", "status_text": "string"}
    with pytest.raises(ValueError, match="incompatible types"):
        translate_condition_to_catala(
            "if active then payment else status_text",
            field_type_map=civil_type_map,
        )
