# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for string-with-no-variants and cross-module enum handling in
transpile_to_catala_tests.py (tickets 11 and 18).

Covers:
- Optional string-no-variants fields are omitted from emitted test cases.
- Required string-no-variants raises ValueError (defensive fail-fast).
- build_field_type_map() populates cross-module enum variants from sub_module_docs.
- emit_test_scope emits qualified constructor form for cross-module enum fields.
- Invalid cross-module enum value defaults to first variant with a warning.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala_tests import build_field_type_map, emit_field_value, emit_test_scope


# =============================================================================
# Unit tests — emit_field_value
# =============================================================================

def test_emit_field_value_optional_string_no_variants_returns_none():
    """Optional string field with no enum variants returns (None, None) — skip signal."""
    result = emit_field_value(
        case_id="case_1",
        field_name="label",
        civil_type="string",
        is_optional=True,
        input_val=None,
        enum_variants={},
    )
    assert result == (None, None)


def test_emit_field_value_required_string_no_variants_raises():
    """Required string field with no enum variants raises ValueError.

    After ticket 18, the main transpiler raises at transpile time for required
    string-no-variants, so the test transpiler should also fail loudly rather
    than silently emitting nothing.  This catches regressions where sub-module
    docs are not threaded through to build_field_type_map().
    """
    with pytest.raises(ValueError, match="required string, no variants"):
        emit_field_value(
            case_id="case_1",
            field_name="category",
            civil_type="string",
            is_optional=False,
            input_val=None,
            enum_variants={},
        )


def test_emit_field_value_string_with_variants_unaffected():
    """String field with enum variants is emitted normally (no skip)."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="status",
        civil_type="string",
        is_optional=False,
        input_val="active",
        enum_variants={"status": {"active": "Active", "inactive": "Inactive"}},
    )
    assert catala_val == "Active"
    assert note is None


def test_emit_field_value_string_with_variants_supplied_value():
    """String field with variants and a valid input value emits the variant identifier."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="household_type",
        civil_type="string",
        is_optional=False,
        input_val="A1E",
        enum_variants={"household_type": {"A1E": "A1E", "B1E": "B1E"}},
    )
    assert catala_val == "A1E"
    assert note is None


# =============================================================================
# Unit tests — build_field_type_map cross-module enum support (ticket 18)
# =============================================================================

def _make_sub_doc_with_table(field_name: str, variants: list) -> dict:
    """Sub-module CIVIL doc that keys a table on `field_name`, inferring an enum."""
    return {
        "module": "prog_standards",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "state", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    field_name: {"type": "string"},
                    "household_size": {"type": "int"},
                }
            }
        },
        "tables": {
            "standards_table": {
                "key": [field_name],
                "value": ["amount"],
                "rows": [{field_name: v, "amount": 1000 + i} for i, v in enumerate(variants)],
            }
        },
        "outputs": {"amount": {"type": "money"}},
    }


def test_build_field_type_map_cross_module_enum_populated():
    """Cross-module string enum variants appear as bare names (no module prefix).

    Catala resolves enum constructors by name alone in struct literals, so
    build_field_type_map emits bare variant names (A1E, not Prog_standards.A1E).
    """
    civil_doc = {
        "module": "parent",
        "inputs": {
            "Household": {
                "fields": {
                    "household_type": {"type": "string"},
                    "age": {"type": "int"},
                }
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    sub_doc = _make_sub_doc_with_table("household_type", ["A1E", "B1E", "H1E"])
    sub_module_docs = {"prog_standards": sub_doc}

    _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc, sub_module_docs)

    assert "household_type" in enum_variants
    assert enum_variants["household_type"]["A1E"] == "A1E"
    assert enum_variants["household_type"]["B1E"] == "B1E"
    assert enum_variants["household_type"]["H1E"] == "H1E"


def test_build_field_type_map_no_sub_modules_cross_module_field_absent():
    """Without sub_module_docs, a cross-module string field has no enum_variants entry."""
    civil_doc = {
        "module": "parent",
        "inputs": {
            "Household": {
                "fields": {
                    "household_type": {"type": "string"},
                }
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
    assert "household_type" not in enum_variants


def test_build_field_type_map_local_enum_wins_over_cross_module():
    """Local table-derived enum takes precedence over sub-module enum for the same field."""
    civil_doc = {
        "module": "parent",
        "inputs": {
            "Household": {
                "fields": {
                    "code": {"type": "string"},
                }
            }
        },
        "tables": {
            "local_table": {
                "key": ["code"],
                "value": ["val"],
                "rows": [{"code": "LOCAL_A", "val": 1}],
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    sub_doc = _make_sub_doc_with_table("code", ["SUB_X", "SUB_Y"])
    sub_module_docs = {"sub_mod": sub_doc}

    _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc, sub_module_docs)

    # Local table wins — raw emit form (not prefixed)
    assert enum_variants["code"] == {"LOCAL_A": "LOCAL_A"}


# =============================================================================
# Integration tests — emit_test_scope emits qualified cross-module constructor
# =============================================================================

def _make_case(case_id: str = "case_1", inputs: dict = None, expected: dict = None) -> dict:
    return {
        "case_id": case_id,
        "description": "Test case",
        "inputs": inputs or {},
        "expected": expected or {"eligible": True},
    }


def test_emit_test_scope_cross_module_enum_single_entity():
    """Cross-module enum field emits qualified constructor in single-entity mode."""
    case = _make_case(inputs={"age": 30, "household_type": "A1E"})
    all_fields = [
        ("age", "int", False),
        ("household_type", "string", False),
    ]
    enum_variants = {
        "household_type": {
            "A1E": "Prog_standards.A1E",
            "B1E": "Prog_standards.B1E",
        }
    }
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=all_fields,
        field_types={"age": "int", "household_type": "string"},
        optional_flags={"age": False, "household_type": False},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants=enum_variants,
    )
    text = "\n".join(lines)
    assert "result.household_type equals Prog_standards.A1E" in text
    assert "result.age" in text


def test_emit_test_scope_cross_module_enum_multi_entity():
    """Cross-module enum field emits qualified constructor in multi-entity (struct-literal) mode."""
    case = _make_case(inputs={"Household.age": 30, "Household.household_type": "B1E"})
    entity_fields = {
        "Household": [
            ("age", "int", False),
            ("household_type", "string", False),
        ]
    }
    enum_variants = {
        "household_type": {
            "A1E": "Prog_standards.A1E",
            "B1E": "Prog_standards.B1E",
        }
    }
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=[],
        field_types={"age": "int", "household_type": "string"},
        optional_flags={"age": False, "household_type": False},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants=enum_variants,
        entity_fields=entity_fields,
        catala_module_name="Eligibility",
        invoke_bound_entities={"Household"},
    )
    text = "\n".join(lines)
    assert "-- household_type: Prog_standards.B1E" in text
    assert "-- age: 30" in text


def test_emit_test_scope_mixed_entities_flattens_non_invoke_bound():
    """Multi-entity mode: invoke-bound entity gets struct literal; the other
    entity (whose fields the main transpiler flattens to scope inputs) must be
    emitted as flat `definition result.<field>` lines, not as a struct literal.
    """
    case = _make_case(
        inputs={
            "Applicant.age": 25,
            "Household.lives_on_campus": True,
            "Household.weekly_meals_covered": 14,
        }
    )
    entity_fields = {
        "Applicant": [("age", "int", False)],
        "Household": [
            ("lives_on_campus", "bool", False),
            ("weekly_meals_covered", "int", False),
        ],
    }
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=[],
        field_types={
            "age": "int",
            "lives_on_campus": "bool",
            "weekly_meals_covered": "int",
        },
        optional_flags={
            "age": False,
            "lives_on_campus": False,
            "weekly_meals_covered": False,
        },
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants={},
        entity_fields=entity_fields,
        catala_module_name="Eligibility",
        invoke_bound_entities={"Applicant"},  # Applicant is bound; Household is not
    )
    text = "\n".join(lines)
    # Applicant: struct literal (invoke-bound).
    assert "definition result.applicant equals Eligibility.Applicant {" in text
    assert "-- age: 25" in text
    # Household: flat scalar definitions (not invoke-bound).
    assert "definition result.lives_on_campus equals true" in text
    assert "definition result.weekly_meals_covered equals 14" in text
    # No Household struct literal anywhere.
    assert "Eligibility.Household" not in text
    assert "definition result.household equals" not in text


def test_emit_test_scope_cross_module_enum_invalid_value_defaults(capsys):
    """Invalid cross-module enum value defaults to first variant with a warning."""
    case = _make_case(inputs={"household_type": "INVALID"})
    all_fields = [("household_type", "string", False)]
    enum_variants = {
        "household_type": {
            "A1E": "Prog_standards.A1E",
            "B1E": "Prog_standards.B1E",
        }
    }
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=all_fields,
        field_types={"household_type": "string"},
        optional_flags={"household_type": False},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants=enum_variants,
    )
    text = "\n".join(lines)
    # Defaults to first variant
    assert "Prog_standards.A1E" in text
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "non-representable" in captured.err


# =============================================================================
# Integration tests — emit_test_scope omits string-no-variants fields
# =============================================================================

def test_emit_test_scope_omits_optional_string_no_variants_single_entity():
    """In single-entity mode, optional string-no-variants field is absent from output."""
    case = _make_case(inputs={"age": 30, "label": "some text"})
    all_fields = [
        ("age", "int", False),
        ("label", "string", True),
    ]
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=all_fields,
        field_types={"age": "int", "label": "string"},
        optional_flags={"age": False, "label": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants={},
    )
    text = "\n".join(lines)
    assert "label" not in text
    assert "result.age" in text


def test_emit_test_scope_omits_optional_string_no_variants_multi_entity():
    """In multi-entity (struct-literal) mode, optional string-no-variants field is absent."""
    case = _make_case(inputs={"Applicant.age": 30, "Applicant.label": "some text"})
    entity_fields = {
        "Applicant": [
            ("age", "int", False),
            ("label", "string", True),
        ]
    }
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=[],
        field_types={"age": "int", "label": "string"},
        optional_flags={"age": False, "label": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants={},
        entity_fields=entity_fields,
        catala_module_name="Eligibility",
        invoke_bound_entities={"Applicant"},
    )
    text = "\n".join(lines)
    assert "label" not in text
    assert "-- age:" in text


# =============================================================================
# Unit tests — build_field_type_map table_key_defaults (ticket 19)
# =============================================================================

def _make_civil_doc_with_date_table_key() -> dict:
    """CIVIL doc with an optional date field used as a table key alongside household_type."""
    return {
        "module": "test_module",
        "inputs": {
            "Household": {
                "fields": {
                    "household_type": {"type": "string"},
                    "effective_date": {"type": "date", "optional": True},
                }
            }
        },
        "tables": {
            "income_limits": {
                "key": ["household_type", "effective_date"],
                "value": ["limit_amount"],
                "rows": [
                    {"household_type": "A1E", "effective_date": "2024-01-01", "limit_amount": 1751},
                    {"household_type": "A1E", "effective_date": "2025-01-01", "limit_amount": 1795},
                    {"household_type": "A1E", "effective_date": "2026-01-01", "limit_amount": 1845},
                ],
            }
        },
        "outputs": {"limit_amount": {"type": "money"}},
    }


def test_build_field_type_map_collects_date_table_key_default():
    """table_key_defaults contains date field with first-row value when used as table key."""
    civil_doc = _make_civil_doc_with_date_table_key()
    _, _, _, _, _, table_key_defaults = build_field_type_map(civil_doc)

    assert "effective_date" in table_key_defaults
    assert table_key_defaults["effective_date"] == "2024-01-01"


def test_build_field_type_map_collects_int_table_key_default():
    """table_key_defaults contains int field with a representative value when used as table key."""
    civil_doc = {
        "module": "test_module",
        "inputs": {
            "Household": {
                "fields": {
                    "program_year": {"type": "int"},
                    "household_type": {"type": "string"},
                }
            }
        },
        "tables": {
            "standards": {
                "key": ["program_year", "household_type"],
                "value": ["amount"],
                "rows": [
                    {"program_year": 2023, "household_type": "A1E", "amount": 1276},
                    {"program_year": 2024, "household_type": "A1E", "amount": 1305},
                ],
            }
        },
        "outputs": {"amount": {"type": "money"}},
    }
    _, _, _, _, _, table_key_defaults = build_field_type_map(civil_doc)

    # Int key with a single table covering {2023, 2024}: pick_representative returns max.
    assert "program_year" in table_key_defaults
    assert table_key_defaults["program_year"] == 2024


def test_build_field_type_map_disjoint_table_key_warns_and_picks_from_larger_set(capsys):
    """When two tables share a key but their value sets are disjoint, WARN and pick from the larger set."""
    civil_doc = {
        "module": "test_module",
        "inputs": {
            "Household": {
                "fields": {"effective_date": {"type": "date", "optional": True}}
            }
        },
        "tables": {
            "table_a": {
                "key": ["effective_date"],
                "value": ["val"],
                "rows": [
                    {"effective_date": "2024-01-01", "val": 100},
                    {"effective_date": "2025-01-01", "val": 200},
                ],
            },
            "table_b": {
                "key": ["effective_date"],
                "value": ["val2"],
                "rows": [
                    {"effective_date": "2023-01-01", "val2": 50},
                ],
            },
        },
        "outputs": {},
    }
    _, _, _, _, _, table_key_defaults = build_field_type_map(civil_doc)

    # No value is common to both tables → WARN; pick from larger set (table_a, 2 rows).
    # Strings fall back to lex-min within that set.
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert table_key_defaults["effective_date"] == "2024-01-01"


def test_build_field_type_map_no_tables_no_defaults():
    """table_key_defaults is empty when there are no tables."""
    civil_doc = {
        "module": "test_module",
        "inputs": {
            "Household": {
                "fields": {"income": {"type": "money"}}
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    _, _, _, _, _, table_key_defaults = build_field_type_map(civil_doc)
    assert table_key_defaults == {}


# =============================================================================
# Unit tests — emit_field_value with table_key_defaults (ticket 19)
# =============================================================================

def test_emit_field_value_optional_date_uses_table_key_default():
    """Optional date field with no input uses table_key_defaults value, not |2020-01-01|."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="effective_date",
        civil_type="date",
        is_optional=True,
        input_val=None,
        enum_variants={},
        table_key_defaults={"effective_date": "2024-01-01"},
    )
    assert catala_val == "|2024-01-01|"
    assert note is None


def test_emit_field_value_optional_date_falls_back_when_no_table_key():
    """Optional date field with no table_key_default falls back to default_value_for_type."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="effective_date",
        civil_type="date",
        is_optional=True,
        input_val=None,
        enum_variants={},
        table_key_defaults={},
    )
    assert catala_val == "|2020-01-01|"
    assert note is None


def test_emit_field_value_optional_int_uses_table_key_default():
    """Optional int field with no input uses table_key_defaults value, not 0."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="program_year",
        civil_type="int",
        is_optional=True,
        input_val=None,
        enum_variants={},
        table_key_defaults={"program_year": 2023},
    )
    assert catala_val == "2023"
    assert note is None


def test_emit_field_value_required_date_missing_uses_table_key_default(capsys):
    """Required date field not in inputs uses table_key_defaults and emits WARN."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="effective_date",
        civil_type="date",
        is_optional=False,
        input_val=None,
        enum_variants={},
        table_key_defaults={"effective_date": "2024-01-01"},
    )
    assert catala_val == "|2024-01-01|"
    assert note == "required field defaulted (not in test inputs)"
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "effective_date" in captured.err


def test_emit_field_value_supplied_date_ignores_table_key_default():
    """When input_val is provided, it takes precedence over table_key_defaults."""
    catala_val, note = emit_field_value(
        case_id="case_1",
        field_name="effective_date",
        civil_type="date",
        is_optional=True,
        input_val="2026-01-01",
        enum_variants={},
        table_key_defaults={"effective_date": "2024-01-01"},
    )
    assert catala_val == "|2026-01-01|"
    assert note is None


# =============================================================================
# Integration tests — emit_test_scope with optional date table key (ticket 19)
# =============================================================================

def test_emit_test_scope_optional_date_key_uses_first_row_value():
    """Test case omitting an optional date table-key field gets the first-row date, not |2020-01-01|."""
    case = _make_case(inputs={"household_type": "A1E"})
    all_fields = [
        ("household_type", "string", False),
        ("effective_date", "date", True),
    ]
    enum_variants = {"household_type": {"A1E": "A1E", "B1E": "B1E"}}
    table_key_defaults = {"effective_date": "2024-01-01"}

    lines = emit_test_scope(
        case=case,
        scope_name="ProgramStandardsLookupDecision",
        all_fields=all_fields,
        field_types={"household_type": "string", "effective_date": "date"},
        optional_flags={"household_type": False, "effective_date": True},
        bool_decision_fields=[],
        denial_field="reasons",
        enum_variants=enum_variants,
        table_key_defaults=table_key_defaults,
    )
    text = "\n".join(lines)
    assert "effective_date equals |2024-01-01|" in text
    assert "|2020-01-01|" not in text


def test_emit_test_scope_supplied_date_overrides_table_key_default():
    """When a test case supplies effective_date, that value is used, not the table default."""
    case = _make_case(inputs={"household_type": "A1E", "effective_date": "2026-01-01"})
    all_fields = [
        ("household_type", "string", False),
        ("effective_date", "date", True),
    ]
    enum_variants = {"household_type": {"A1E": "A1E"}}
    table_key_defaults = {"effective_date": "2024-01-01"}

    lines = emit_test_scope(
        case=case,
        scope_name="ProgramStandardsLookupDecision",
        all_fields=all_fields,
        field_types={"household_type": "string", "effective_date": "date"},
        optional_flags={"household_type": False, "effective_date": True},
        bool_decision_fields=[],
        denial_field="reasons",
        enum_variants=enum_variants,
        table_key_defaults=table_key_defaults,
    )
    text = "\n".join(lines)
    assert "effective_date equals |2026-01-01|" in text
    assert "|2024-01-01|" not in text


def test_emit_test_scope_non_table_key_date_unchanged():
    """Non-table-key optional date field still falls back to |2020-01-01| (regression guard)."""
    case = _make_case(inputs={})
    all_fields = [("start_date", "date", True)]
    lines = emit_test_scope(
        case=case,
        scope_name="EligibilityDecision",
        all_fields=all_fields,
        field_types={"start_date": "date"},
        optional_flags={"start_date": True},
        bool_decision_fields=["eligible"],
        denial_field="reasons",
        enum_variants={},
        table_key_defaults={},
    )
    text = "\n".join(lines)
    assert "start_date equals |2020-01-01|" in text
