# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for is_null() rewrite in translate_expr_to_catala (step 3.7).

CIVIL supports is_null(field) to check whether an optional field is absent.
The Catala transpiler must convert this to the sentinel-value equality form:
  - int/unknown type  → field = 0
  - money type        → field = $0

Covers:
- Bare ident, no type map   → field = 0
- Bare ident, int type map  → field = 0
- Bare ident, money type map → field = $0
- Entity-prefixed ident (prefix stripped by step 0 before this step)
- Compound expression: A && B && is_null(C) → full rewrite
- Integration: CIVIL spec with is_null() expr transpiles without error
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import translate_expr_to_catala, transpile


# =============================================================================
# Unit tests — translate_expr_to_catala step 3.7
# =============================================================================

def test_is_null_bare_ident_defaults_to_integer_sentinel():
    result = translate_expr_to_catala("is_null(refused_work_study_month)")
    assert result == "refused_work_study_month = 0"


def test_is_null_bare_ident_with_int_type_map():
    result = translate_expr_to_catala(
        "is_null(refused_work_study_month)",
        field_type_map={"refused_work_study_month": "int"},
    )
    assert result == "refused_work_study_month = 0"


def test_is_null_bare_ident_with_money_type_map():
    result = translate_expr_to_catala(
        "is_null(optional_income)",
        field_type_map={"optional_income": "money"},
    )
    assert result == "optional_income = $0"


def test_is_null_entity_prefix_already_stripped():
    """Entity prefixes are stripped by step 0 before step 3.7 fires.
    When calling translate_expr_to_catala directly with fact_entities, the
    prefix is stripped first, then is_null sees the bare ident.
    """
    result = translate_expr_to_catala(
        "is_null(Applicant.refused_work_study_month)",
        fact_entities={"Applicant"},
    )
    assert result == "refused_work_study_month = 0"


def test_is_null_in_compound_and_expression():
    result = translate_expr_to_catala(
        "work_study_approved_for_term && anticipates_working && is_null(refused_work_study_month)"
    )
    assert result == "work_study_approved_for_term and anticipates_working and refused_work_study_month = 0"


def test_is_null_does_not_affect_other_function_calls():
    result = translate_expr_to_catala("count(items) > 0")
    assert "is_null" not in result
    assert "(number of items)" in result


# =============================================================================
# Integration test — minimal CIVIL spec with is_null() expr transpiles cleanly
# =============================================================================

_IS_NULL_CIVIL = {
    "module": "work_study_check",
    "description": "Checks work study exemption eligibility.",
    "version": "1.0",
    "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
    "effective": {"start": "2026-01-01"},
    "inputs": {
        "Applicant": {
            "fields": {
                "work_study_approved_for_term": {"type": "bool"},
                "anticipates_working": {"type": "bool"},
                "refused_work_study_month": {"type": "int", "optional": True},
            }
        }
    },
    "outputs": {
        "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
        "reasons": {"type": "list", "item": "Reason", "default": []},
    },
    "computed": {
        "work_study_exemption_met": {
            "type": "bool",
            "tags": ["expose"],
            "expr": (
                "Applicant.work_study_approved_for_term"
                " && Applicant.anticipates_working"
                " && is_null(Applicant.refused_work_study_month)"
            ),
        }
    },
    "rules": [],
}


def test_is_null_integration_transpiles_without_error(tmp_path):
    civil_path = str(tmp_path / "work_study_check.civil.yaml")
    output_path = str(tmp_path / "work_study_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(_IS_NULL_CIVIL, fh)

    transpile(_IS_NULL_CIVIL, output_path, "WorkStudyCheckDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "is_null" not in result, "is_null should have been rewritten"
    assert "refused_work_study_month = 0" in result


def test_is_null_integration_contains_valid_work_study_definition(tmp_path):
    civil_path = str(tmp_path / "work_study_check.civil.yaml")
    output_path = str(tmp_path / "work_study_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(_IS_NULL_CIVIL, fh)

    transpile(_IS_NULL_CIVIL, output_path, "WorkStudyCheckDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "work_study_approved_for_term and anticipates_working and refused_work_study_month = 0" in result
