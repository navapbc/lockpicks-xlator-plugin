# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for unknown-input-name warnings in transpile_to_catala_tests.py.

A test YAML that references an input field name not declared in the CIVIL
spec (e.g. `year:` instead of `benefit_year:`) silently has its value dropped
and the field defaulted. The transpiler must emit a WARN naming the unknown
key and listing the known field names so the user can fix the typo.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala_tests import emit_test_scope


def _case(case_id: str = "case_1", inputs: dict | None = None, expected: dict | None = None) -> dict:
    return {
        "case_id": case_id,
        "description": "Test",
        "inputs": inputs or {},
        "expected": expected or {"eligible": True},
    }


def test_unknown_input_name_emits_warn(capsys) -> None:
    """Test YAML uses `year` but CIVIL declares `benefit_year` — must WARN."""
    case = _case(inputs={"household_size": 1, "year": 2026})
    all_fields = [
        ("household_size", "int", False),
        ("benefit_year", "int", True),
    ]
    emit_test_scope(
        case=case,
        scope_name="ProgramStandardsDecision",
        all_fields=all_fields,
        field_types={"household_size": "int", "benefit_year": "int"},
        optional_flags={"household_size": False, "benefit_year": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
    )
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "'year'" in captured.err
    # Suggestion includes valid CIVIL field names so the user can spot the typo.
    assert "benefit_year" in captured.err


def test_known_input_name_no_warn(capsys) -> None:
    """All test inputs are declared — no unknown-name WARN should appear."""
    case = _case(inputs={"household_size": 1, "benefit_year": 2026})
    all_fields = [
        ("household_size", "int", False),
        ("benefit_year", "int", True),
    ]
    emit_test_scope(
        case=case,
        scope_name="ProgramStandardsDecision",
        all_fields=all_fields,
        field_types={"household_size": "int", "benefit_year": "int"},
        optional_flags={"household_size": False, "benefit_year": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
    )
    captured = capsys.readouterr()
    assert "is not declared in CIVIL inputs" not in captured.err


def test_entity_prefixed_known_input_no_warn(capsys) -> None:
    """Multi-entity mode: `Household.benefit_year` is the entity-qualified form — no WARN."""
    case = _case(inputs={"Household.benefit_year": 2026, "Household.household_size": 1})
    entity_fields = {
        "Household": [
            ("household_size", "int", False),
            ("benefit_year", "int", True),
        ],
    }
    emit_test_scope(
        case=case,
        scope_name="ProgramStandardsDecision",
        all_fields=[],
        field_types={"household_size": "int", "benefit_year": "int"},
        optional_flags={"household_size": False, "benefit_year": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        entity_fields=entity_fields,
        catala_module_name="Program_standards",
        invoke_bound_entities={"Household"},
    )
    captured = capsys.readouterr()
    assert "is not declared in CIVIL inputs" not in captured.err


def test_unknown_entity_prefixed_input_warns(capsys) -> None:
    """Multi-entity mode: `Household.year` (wrong field) must WARN."""
    case = _case(inputs={"Household.year": 2026, "Household.household_size": 1})
    entity_fields = {
        "Household": [
            ("household_size", "int", False),
            ("benefit_year", "int", True),
        ],
    }
    emit_test_scope(
        case=case,
        scope_name="ProgramStandardsDecision",
        all_fields=[],
        field_types={"household_size": "int", "benefit_year": "int"},
        optional_flags={"household_size": False, "benefit_year": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        entity_fields=entity_fields,
        catala_module_name="Program_standards",
        invoke_bound_entities={"Household"},
    )
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "Household.year" in captured.err
