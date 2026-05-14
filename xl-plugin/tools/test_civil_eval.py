# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for civil_eval.py — U1 evaluator library."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

import civil_eval  # noqa: E402
from civil_eval import EvaluationError, evaluate_civil, detect_stale  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(
    *,
    inputs=None,
    computed=None,
    rules=None,
    outputs=None,
    constants=None,
    tables=None,
):
    """Build a minimal CIVIL doc."""
    return {
        "inputs": inputs or {},
        "computed": computed or {},
        "rules": rules or [],
        "outputs": outputs or {},
        "constants": constants or {},
        "tables": tables or {},
    }


def _eval_expr(expr: str, *, inputs=None, computed=None, constants=None, tables=None,
               entities=None):
    """Direct expression evaluation via a synthetic single-output module."""
    doc = _doc(
        inputs=entities or {"Ctx": {"fields": {}}},
        computed=computed or {},
        constants=constants or {},
        tables=tables or {},
        outputs={"result": {"type": "string", "expr": expr}},
    )
    result = evaluate_civil(doc, inputs or {})
    return result.outputs["result"]


# ---------------------------------------------------------------------------
# Expression-level tests
# ---------------------------------------------------------------------------


class TestArithmetic:
    def test_addition(self):
        assert _eval_expr("2 + 3") == 5

    def test_subtraction(self):
        assert _eval_expr("10 - 4") == 6

    def test_multiplication(self):
        assert _eval_expr("3 * 4") == 12

    def test_division(self):
        assert _eval_expr("10 / 4") == 2.5

    def test_precedence(self):
        assert _eval_expr("2 + 3 * 4") == 14

    def test_parentheses(self):
        assert _eval_expr("(2 + 3) * 4") == 20

    def test_division_by_zero_raises(self):
        with pytest.raises(EvaluationError, match="division by zero"):
            _eval_expr("10 / 0")


class TestComparison:
    @pytest.mark.parametrize("expr,expected", [
        ("5 > 3", True),
        ("5 < 3", False),
        ("5 >= 5", True),
        ("5 <= 5", True),
        ("5 == 5", True),
        ("5 != 5", False),
        ("5 != 6", True),
        ("'a' == 'a'", True),
        ("'a' != 'b'", True),
    ])
    def test_comparisons(self, expr, expected):
        assert _eval_expr(expr) is expected


class TestBoolean:
    def test_and_true(self):
        assert _eval_expr("true and true") is True

    def test_and_false(self):
        assert _eval_expr("true and false") is False

    def test_or_first_true(self):
        assert _eval_expr("true or false") is True

    def test_or_both_false(self):
        assert _eval_expr("false or false") is False

    def test_civil_or_operator(self):
        # CIVIL `||` translated to Python `or` by the shim
        assert _eval_expr("true || false") is True

    def test_civil_and_operator(self):
        assert _eval_expr("false && true") is False

    def test_civil_not_operator(self):
        assert _eval_expr("!true") is False

    def test_not_keyword(self):
        assert _eval_expr("not true") is False

    def test_civil_short_circuit_or(self):
        # `5 == 5` short-circuits; `1/0` should not be evaluated
        assert _eval_expr("5 == 5 || 1 / 0 > 0") is True


class TestConditional:
    def test_ternary_then_branch(self):
        # Python ternary syntax (via ast.IfExp)
        # Note: CIVIL inline `if X then Y else Z` is not supported in v1; the
        # `conditional:` block is the supported form. visit_IfExp here ensures
        # that if a Python-style ternary survives the shim, it evaluates.
        ast_expr = '"pos" if 5 > 0 else "neg"'
        doc = {
            "inputs": {"Ctx": {"fields": {}}},
            "outputs": {"result": {"type": "string", "expr": ast_expr}},
        }
        result = evaluate_civil(doc, {})
        assert result.outputs["result"] == "pos"


class TestFunctions:
    def test_max_args(self):
        assert _eval_expr("max(1, 2, 3)") == 3

    def test_min_args(self):
        assert _eval_expr("min(1, 2, 3)") == 1

    def test_max_with_zero(self):
        assert _eval_expr("max(0, -5)") == 0

    def test_in_function(self):
        # CIVIL syntax: in(x, [1,2,3]) → in_(x, [1,2,3]) via shim
        assert _eval_expr("in(2, [1, 2, 3])") is True
        assert _eval_expr("in(5, [1, 2, 3])") is False

    def test_between_function(self):
        assert _eval_expr("between(5, 1, 10)") is True
        assert _eval_expr("between(15, 1, 10)") is False
        assert _eval_expr("between(1, 1, 10)") is True
        assert _eval_expr("between(10, 1, 10)") is True

    def test_count_list(self):
        assert _eval_expr("count([1, 2, 3])") == 3
        assert _eval_expr("count([])") == 0

    def test_count_reasons_zero(self):
        # `reasons` is implicitly empty in expression context
        assert _eval_expr("count(reasons) == 0") is True

    def test_is_null_with_optional_field(self):
        doc = {
            "inputs": {
                "H": {"fields": {"opt": {"type": "money", "optional": True}}}
            },
            "outputs": {"result": {"type": "bool", "expr": "is_null(opt)"}},
        }
        result = evaluate_civil(doc, {})
        assert result.outputs["result"] is True

    def test_exists_with_optional_present(self):
        doc = {
            "inputs": {
                "H": {"fields": {"opt": {"type": "money", "optional": True}}}
            },
            "outputs": {"result": {"type": "bool", "expr": "exists(opt)"}},
        }
        result = evaluate_civil(doc, {"opt": 100})
        assert result.outputs["result"] is True

    def test_unknown_function_raises(self):
        with pytest.raises(EvaluationError, match="unknown function 'foo'"):
            _eval_expr("foo(1, 2)")


class TestTableLookup:
    def test_lookup_by_single_key(self):
        doc = {
            "inputs": {"H": {"fields": {"size": {"type": "int"}}}},
            "tables": {
                "limits": {
                    "key": ["size"],
                    "value": ["amount"],
                    "rows": [
                        {"size": 1, "amount": 100},
                        {"size": 2, "amount": 200},
                        {"size": 3, "amount": 300},
                    ],
                }
            },
            "outputs": {"result": {"type": "money", "expr": "table('limits', size).amount"}},
        }
        result = evaluate_civil(doc, {"size": 2})
        assert result.outputs["result"] == 200

    def test_lookup_missing_row_raises(self):
        doc = {
            "inputs": {"H": {"fields": {"size": {"type": "int"}}}},
            "tables": {
                "t": {
                    "key": ["size"],
                    "value": ["v"],
                    "rows": [{"size": 1, "v": 10}],
                }
            },
            "outputs": {"result": {"type": "money", "expr": "table('t', size).v"}},
        }
        with pytest.raises(EvaluationError, match="no row in table"):
            evaluate_civil(doc, {"size": 99})

    def test_unknown_table_raises(self):
        doc = {
            "inputs": {"H": {"fields": {}}},
            "outputs": {"result": {"type": "string", "expr": "table('unknown', 1).v"}},
        }
        with pytest.raises(EvaluationError, match="unknown table"):
            evaluate_civil(doc, {})


# ---------------------------------------------------------------------------
# Computed-field topological sort
# ---------------------------------------------------------------------------


class TestComputedTopoSort:
    def test_linear_chain(self):
        # c3 depends on c2 depends on c1 depends on input
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "computed": {
                "c1": {"type": "int", "expr": "x + 1"},
                "c2": {"type": "int", "expr": "c1 * 2"},
                "c3": {"type": "int", "expr": "c2 + 100"},
            },
            "outputs": {"result": {"type": "int", "expr": "c3"}},
        }
        result = evaluate_civil(doc, {"x": 5})
        # c1 = 6, c2 = 12, c3 = 112
        assert result.computed == {"c1": 6, "c2": 12, "c3": 112}
        assert result.outputs["result"] == 112

    def test_cycle_detection(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "computed": {
                "a": {"type": "int", "expr": "b + 1"},
                "b": {"type": "int", "expr": "a + 1"},
            },
            "outputs": {"result": {"type": "int", "expr": "a"}},
        }
        with pytest.raises(EvaluationError, match="cycle"):
            evaluate_civil(doc, {"x": 0})

    def test_conditional_computed_field(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "computed": {
                "branch": {
                    "type": "int",
                    "conditional": {
                        "if": "x > 0",
                        "then": "x * 10",
                        "else": "0",
                    },
                },
            },
            "outputs": {"result": {"type": "int", "expr": "branch"}},
        }
        assert evaluate_civil(doc, {"x": 5}).outputs["result"] == 50
        assert evaluate_civil(doc, {"x": -3}).outputs["result"] == 0

    def test_table_lookup_computed_field(self):
        doc = {
            "inputs": {"H": {"fields": {"size": {"type": "int"}}}},
            "tables": {
                "t": {
                    "key": ["size"],
                    "value": ["amount"],
                    "rows": [{"size": 1, "amount": 100}, {"size": 2, "amount": 200}],
                }
            },
            "computed": {
                "looked_up": {
                    "type": "money",
                    "table_lookup": {"table": "t", "key": ["size"], "value": "amount"},
                },
            },
            "outputs": {"result": {"type": "money", "expr": "looked_up"}},
        }
        result = evaluate_civil(doc, {"size": 2})
        assert result.outputs["result"] == 200


# ---------------------------------------------------------------------------
# Rule firing
# ---------------------------------------------------------------------------


class TestRuleFiring:
    def test_two_rules_both_fire_in_priority_order(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R2",
                    "kind": "deny",
                    "priority": 2,
                    "when": "x > 0",
                    "then": [{"add_reason": {"code": "SECOND"}}],
                },
                {
                    "id": "R1",
                    "kind": "deny",
                    "priority": 1,
                    "when": "x > 0",
                    "then": [{"add_reason": {"code": "FIRST"}}],
                },
            ],
            "outputs": {
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }
        result = evaluate_civil(doc, {"x": 5})
        codes = [r["code"] for r in result.reasons]
        assert codes == ["FIRST", "SECOND"]
        assert result.debug["rules_fired"] == ["R1", "R2"]

    def test_mutex_group_only_first_fires(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R1",
                    "kind": "deny",
                    "priority": 1,
                    "mutex_group": "G",
                    "when": "x > 0",
                    "then": [{"add_reason": {"code": "FIRST"}}],
                },
                {
                    "id": "R2",
                    "kind": "deny",
                    "priority": 2,
                    "mutex_group": "G",
                    "when": "x > 0",
                    "then": [{"add_reason": {"code": "SECOND"}}],
                },
            ],
            "outputs": {
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }
        result = evaluate_civil(doc, {"x": 5})
        assert [r["code"] for r in result.reasons] == ["FIRST"]
        assert result.debug["rules_fired"] == ["R1"]

    def test_rule_not_fired_when_when_false(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": "x < 0",
                    "then": [{"add_reason": {"code": "NEG"}}],
                },
            ],
            "outputs": {
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }
        result = evaluate_civil(doc, {"x": 5})
        assert result.reasons == []
        assert result.debug["rules_fired"] == []

    def test_add_reason_carries_message_and_citations(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": "true",
                    "then": [
                        {
                            "add_reason": {
                                "code": "C",
                                "message": "explanation",
                                "citations": [{"label": "law", "url": "x"}],
                            }
                        }
                    ],
                },
            ],
            "outputs": {
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }
        result = evaluate_civil(doc, {})
        assert result.reasons == [
            {"code": "C", "message": "explanation", "citations": [{"label": "law", "url": "x"}]}
        ]

    def test_unsupported_action_raises(self):
        doc = {
            "inputs": {"H": {"fields": {}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": "true",
                    "then": [{"add_instruction": {"text": "Do X"}}],
                },
            ],
            "outputs": {"reasons": {"type": "list", "item": "Reason", "default": []}},
        }
        with pytest.raises(EvaluationError, match="add_instruction"):
            evaluate_civil(doc, {})


# ---------------------------------------------------------------------------
# Output evaluation
# ---------------------------------------------------------------------------


class TestOutputs:
    def _doc_with_deny_rule(self, when="true"):
        return {
            "inputs": {"H": {"fields": {}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": when,
                    "then": [{"add_reason": {"code": "C"}}],
                },
            ],
            "outputs": {
                "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }

    def test_eligible_true_when_no_rules_fire(self):
        doc = self._doc_with_deny_rule(when="false")
        result = evaluate_civil(doc, {})
        assert result.outputs["eligible"] is True
        assert result.outputs["reasons"] == []

    def test_eligible_false_when_rule_fires(self):
        doc = self._doc_with_deny_rule(when="true")
        result = evaluate_civil(doc, {})
        assert result.outputs["eligible"] is False
        assert result.outputs["reasons"] == [{"code": "C"}]

    def test_reasons_output_resolves_to_accumulated_list(self):
        doc = self._doc_with_deny_rule(when="true")
        result = evaluate_civil(doc, {})
        assert result.outputs["reasons"] == [{"code": "C"}]

    def test_conditional_output_evaluates(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "outputs": {
                "category": {
                    "type": "string",
                    "conditional": {
                        "if": "x > 10",
                        "then": "'high'",
                        "else": "'low'",
                    },
                },
            },
        }
        assert evaluate_civil(doc, {"x": 15}).outputs["category"] == "high"
        assert evaluate_civil(doc, {"x": 5}).outputs["category"] == "low"


# ---------------------------------------------------------------------------
# Input binding
# ---------------------------------------------------------------------------


class TestInputBinding:
    def test_single_entity_bare_keys(self):
        doc = {
            "inputs": {"Household": {"fields": {"size": {"type": "int"}}}},
            "outputs": {"result": {"type": "int", "expr": "size"}},
        }
        result = evaluate_civil(doc, {"size": 4})
        assert result.outputs["result"] == 4

    def test_single_entity_qualified_keys(self):
        doc = {
            "inputs": {"Household": {"fields": {"size": {"type": "int"}}}},
            "outputs": {"result": {"type": "int", "expr": "Household.size"}},
        }
        result = evaluate_civil(doc, {"Household.size": 4})
        assert result.outputs["result"] == 4

    def test_multi_entity_requires_qualified(self):
        doc = {
            "inputs": {
                "A": {"fields": {"v": {"type": "int"}}},
                "B": {"fields": {"v": {"type": "int"}}},
            },
            "outputs": {"sum": {"type": "int", "expr": "A.v + B.v"}},
        }
        result = evaluate_civil(doc, {"A.v": 3, "B.v": 7})
        assert result.outputs["sum"] == 10

    def test_missing_required_input_raises(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "outputs": {"result": {"type": "int", "expr": "x"}},
        }
        with pytest.raises(EvaluationError, match="missing required"):
            evaluate_civil(doc, {})

    def test_optional_missing_returns_none(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "money", "optional": True}}}},
            "outputs": {"result": {"type": "bool", "expr": "is_null(x)"}},
        }
        result = evaluate_civil(doc, {})
        assert result.outputs["result"] is True


# ---------------------------------------------------------------------------
# Integration: SNAP-style module
# ---------------------------------------------------------------------------


class TestIntegrationSnap:
    """A miniature SNAP-style module exercising entity inputs, constants,
    tables, multi-step computed fields, and rules. Hand-verified against the
    documented logic."""

    @pytest.fixture
    def snap_doc(self):
        return {
            "inputs": {
                "Household": {
                    "fields": {
                        "household_size": {"type": "int"},
                        "gross_monthly_income": {"type": "money"},
                        "earned_income": {"type": "money", "optional": True, "default": 0},
                        "shelter_costs_monthly": {"type": "money", "optional": True, "default": 0},
                        "has_elderly_member": {"type": "bool", "optional": True, "default": False},
                    }
                }
            },
            "constants": {
                "EARNED_INCOME_DEDUCTION_RATE": 0.20,
                "SHELTER_DEDUCTION_CAP": 744,
                "SHELTER_EXCESS_THRESHOLD_RATE": 0.50,
            },
            "tables": {
                "gross_income_limits": {
                    "key": ["household_size"],
                    "value": ["max_gross_monthly"],
                    "rows": [
                        {"household_size": 1, "max_gross_monthly": 1696},
                        {"household_size": 3, "max_gross_monthly": 2888},
                    ],
                },
                "standard_deductions": {
                    "key": ["household_size"],
                    "value": ["deduction_amount"],
                    "rows": [
                        {"household_size": 1, "deduction_amount": 209},
                        {"household_size": 3, "deduction_amount": 209},
                    ],
                },
            },
            "computed": {
                "earned_income_deduction": {
                    "type": "money",
                    "expr": "Household.earned_income * EARNED_INCOME_DEDUCTION_RATE",
                },
                "standard_deduction": {
                    "type": "money",
                    "expr": "table('standard_deductions', Household.household_size).deduction_amount",
                },
                "income_after_prior_deductions": {
                    "type": "money",
                    "expr": "Household.gross_monthly_income - earned_income_deduction - standard_deduction",
                },
                "shelter_excess": {
                    "type": "money",
                    "expr": "max(0, Household.shelter_costs_monthly - SHELTER_EXCESS_THRESHOLD_RATE * income_after_prior_deductions)",
                },
                "shelter_deduction": {
                    "type": "money",
                    "expr": "min(shelter_excess, SHELTER_DEDUCTION_CAP)",
                },
                "net_income": {
                    "type": "money",
                    "expr": "income_after_prior_deductions - shelter_deduction",
                },
                "gross_limit": {
                    "type": "money",
                    "expr": "table('gross_income_limits', Household.household_size).max_gross_monthly",
                },
            },
            "rules": [
                {
                    "id": "DENY-GROSS",
                    "kind": "deny",
                    "priority": 1,
                    "when": "!Household.has_elderly_member && Household.gross_monthly_income > gross_limit",
                    "then": [{"add_reason": {"code": "GROSS_INCOME_EXCEEDS_LIMIT"}}],
                },
            ],
            "outputs": {
                "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }

    def test_allow_low_income(self, snap_doc):
        # HH size 3, gross 1800, no other income — well below limit 2888
        result = evaluate_civil(snap_doc, {
            "household_size": 3,
            "gross_monthly_income": 1800,
            "earned_income": 0,
            "shelter_costs_monthly": 500,
            "has_elderly_member": False,
        })
        assert result.outputs["eligible"] is True
        assert result.outputs["reasons"] == []

    def test_deny_high_gross(self, snap_doc):
        # HH size 3, gross 3200 > limit 2888 (and no elderly)
        result = evaluate_civil(snap_doc, {
            "household_size": 3,
            "gross_monthly_income": 3200,
            "earned_income": 3200,
            "shelter_costs_monthly": 1306,
            "has_elderly_member": False,
        })
        assert result.outputs["eligible"] is False
        codes = [r["code"] for r in result.outputs["reasons"]]
        assert "GROSS_INCOME_EXCEEDS_LIMIT" in codes

    def test_computed_chain_correctness(self, snap_doc):
        # Validate the deduction chain math
        # earned_income_deduction = 1000 * 0.20 = 200
        # standard_deduction = 209
        # income_after_prior = 2000 - 200 - 209 = 1591
        # shelter_excess = max(0, 800 - 0.5 * 1591) = max(0, 800 - 795.5) = 4.5
        # shelter_deduction = min(4.5, 744) = 4.5
        # net_income = 1591 - 4.5 = 1586.5
        result = evaluate_civil(snap_doc, {
            "household_size": 3,
            "gross_monthly_income": 2000,
            "earned_income": 1000,
            "shelter_costs_monthly": 800,
            "has_elderly_member": False,
        })
        assert result.computed["earned_income_deduction"] == 200.0
        assert result.computed["standard_deduction"] == 209
        assert result.computed["income_after_prior_deductions"] == 1591
        assert result.computed["shelter_excess"] == pytest.approx(4.5)
        assert result.computed["shelter_deduction"] == pytest.approx(4.5)
        assert result.computed["net_income"] == pytest.approx(1586.5)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrors:
    def test_invoke_block_raises(self):
        doc = {
            "inputs": {"H": {"fields": {}}},
            "computed": {
                "sub": {"invoke": {"module": "other"}},
            },
            "outputs": {},
        }
        with pytest.raises(EvaluationError, match="invoke: not supported"):
            evaluate_civil(doc, {})

    def test_undefined_identifier_in_rule(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": "y > 0",
                    "then": [{"add_reason": {"code": "C"}}],
                },
            ],
            "outputs": {},
        }
        with pytest.raises(EvaluationError, match="undefined identifier 'y'"):
            evaluate_civil(doc, {"x": 5})


# ---------------------------------------------------------------------------
# detect_stale
# ---------------------------------------------------------------------------


class TestDetectStale:
    @pytest.fixture
    def simple_doc(self):
        return {
            "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
            "rules": [
                {
                    "id": "R",
                    "kind": "deny",
                    "priority": 1,
                    "when": "x > 100",
                    "then": [{"add_reason": {"code": "TOO_HIGH"}}],
                },
            ],
            "outputs": {
                "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
                "reasons": {"type": "list", "item": "Reason", "default": []},
            },
        }

    def test_no_drift(self, simple_doc):
        case = {
            "inputs": {"x": 50},
            "expected": {"eligible": True, "reasons": []},
        }
        assert detect_stale(simple_doc, case) is None

    def test_boundary_change(self, simple_doc):
        # Test expects deny but the case x=50 actually allows
        case = {
            "inputs": {"x": 50},
            "expected": {"eligible": False, "reasons": [{"code": "TOO_HIGH"}]},
        }
        diff = detect_stale(simple_doc, case)
        assert diff is not None
        assert "eligible" in diff.diff
        assert diff.diff["eligible"]["current"] is False
        assert diff.diff["eligible"]["recomputed"] is True

    def test_float_tolerance(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "float"}}}},
            "computed": {"y": {"type": "float", "expr": "x * 2"}},
            "outputs": {"result": {"type": "float", "expr": "y"}},
        }
        # 50.0 * 2 = 100.0; expected 100.001 → within ±0.005 tolerance
        case = {
            "inputs": {"x": 50.0},
            "expected": {"result": 100.001},
        }
        assert detect_stale(doc, case) is None

    def test_float_tolerance_exceeded(self):
        doc = {
            "inputs": {"H": {"fields": {"x": {"type": "float"}}}},
            "computed": {"y": {"type": "float", "expr": "x * 2"}},
            "outputs": {"result": {"type": "float", "expr": "y"}},
        }
        case = {
            "inputs": {"x": 50.0},
            "expected": {"result": 100.5},
        }
        diff = detect_stale(doc, case)
        assert diff is not None
        assert "result" in diff.diff


# ---------------------------------------------------------------------------
# Parity test against reference test corpus (xl-plugin/core/tests/...)
# ---------------------------------------------------------------------------


class TestReferenceCorpusParity:
    """Smoke check that the evaluator runs end-to-end against the reference
    SNAP module. Per the plan's Phase 1 parity gate, divergences are
    investigated and may indicate latent test-corpus bugs OR evaluator gaps."""

    def test_snap_corpus_parity(self):
        repo_root = Path(__file__).resolve().parents[2]
        civil_path = repo_root / "domains" / "snap" / "specs" / "eligibility.civil.yaml"
        tests_path = repo_root / "domains" / "snap" / "specs" / "tests" / "eligibility_tests.yaml"
        if not civil_path.exists() or not tests_path.exists():
            pytest.skip("SNAP reference module/tests not present")

        with civil_path.open() as f:
            civil_doc = yaml.safe_load(f)
        with tests_path.open() as f:
            test_doc = yaml.safe_load(f)

        # Just verify every test case evaluates without raising. Stale-case
        # divergences (which may exist) are surfaced via detect_stale and
        # reported separately, not asserted here.
        evaluated = 0
        for case in test_doc.get("tests") or []:
            evaluate_civil(civil_doc, case["inputs"])
            evaluated += 1
        assert evaluated > 0, "Expected at least one SNAP test case to evaluate"
