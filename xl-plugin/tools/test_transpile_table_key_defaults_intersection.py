# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for intersection-based table-key defaults in transpile_to_catala_tests.py.

When an input field keys multiple tables with different row coverage, the
default chosen for optional/missing test inputs must lie in every table's
coverage — otherwise outputs derived from the non-covering tables fail
Catala's "no applicable rule" check and the entire test scope fails.

The previous implementation used the first-seen table's first-row value as
the default. With three tables on `benefit_year` covering 2023-2026,
2024-2026, and 2023-2026, the default `2023` is absent from
`table_excess_home_equity` (which starts at 2024), so the `excess_home_equity_threshold`
output is unsatisfiable for every test scope.

The fix computes the intersection of every value set seen for the key and
picks a deterministic representative.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala_tests import build_field_type_map, pick_representative


# =============================================================================
# Unit tests — pick_representative helper
# =============================================================================

def test_pick_representative_numeric_int_returns_max() -> None:
    assert pick_representative({2023, 2024, 2025, 2026}) == 2026


def test_pick_representative_numeric_float_returns_max() -> None:
    assert pick_representative({0.388, 0.973, 0.516}) == 0.973


def test_pick_representative_string_returns_lex_min() -> None:
    assert pick_representative({"individual", "couple"}) == "couple"


def test_pick_representative_single_value_returns_that_value() -> None:
    assert pick_representative({2026}) == 2026
    assert pick_representative({"only"}) == "only"


def test_pick_representative_mixed_types_falls_back_to_lex_min() -> None:
    # Heterogeneous sets are unusual but must remain deterministic.
    result = pick_representative({"abc", 1})
    assert result == min({"abc", 1}, key=str)


# =============================================================================
# Helpers — minimal CIVIL doc fixtures
# =============================================================================

def _doc_with_tables(tables: dict, input_fields: dict | None = None) -> dict:
    """Minimal CIVIL doc carrying the given tables: block and optional input fields."""
    return {
        "module": "test_intersection",
        "description": "Test",
        "version": "1.0",
        "effective": {"start": "2026-01-01"},
        "jurisdiction": {"level": "state", "country": "US", "state": "AK"},
        "inputs": {
            "Household": {
                "fields": input_fields or {
                    "benefit_year": {"type": "int", "optional": True},
                },
            },
        },
        "outputs": {},
        "rules": [],
        "tables": tables,
    }


# =============================================================================
# Unit tests — build_field_type_map.table_key_defaults
# =============================================================================

def test_default_in_intersection_of_overlapping_tables() -> None:
    """When 3 tables overlap on {2024, 2025, 2026}, the default must be in that set."""
    tables = {
        "table_program_standards": {
            "key": ["benefit_year"],
            "value": ["amount"],
            "rows": [
                {"benefit_year": 2023, "amount": 100},
                {"benefit_year": 2024, "amount": 200},
                {"benefit_year": 2025, "amount": 300},
                {"benefit_year": 2026, "amount": 400},
            ],
        },
        "table_excess_home_equity": {
            "key": ["benefit_year"],
            "value": ["threshold"],
            "rows": [
                {"benefit_year": 2024, "threshold": 713000},
                {"benefit_year": 2025, "threshold": 730000},
                {"benefit_year": 2026, "threshold": 752000},
            ],
        },
        "table_student_exclusion": {
            "key": ["benefit_year"],
            "value": ["monthly_cap"],
            "rows": [
                {"benefit_year": 2023, "monthly_cap": 2220},
                {"benefit_year": 2024, "monthly_cap": 2290},
                {"benefit_year": 2025, "monthly_cap": 2350},
                {"benefit_year": 2026, "monthly_cap": 2410},
            ],
        },
    }
    doc = _doc_with_tables(tables)
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    assert "benefit_year" in table_key_defaults
    assert table_key_defaults["benefit_year"] in {2024, 2025, 2026}


def test_default_for_overlapping_tables_picks_max() -> None:
    """Deterministic choice within the intersection: max for numeric keys."""
    tables = {
        "table_a": {
            "key": ["year"],
            "value": ["v"],
            "rows": [{"year": 2023, "v": 1}, {"year": 2024, "v": 2}, {"year": 2025, "v": 3}],
        },
        "table_b": {
            "key": ["year"],
            "value": ["v"],
            "rows": [{"year": 2024, "v": 10}, {"year": 2025, "v": 20}],
        },
    }
    doc = _doc_with_tables(tables, input_fields={"year": {"type": "int", "optional": True}})
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    # Intersection is {2024, 2025} → max picks 2025.
    assert table_key_defaults["year"] == 2025


def test_default_for_single_table_picks_max() -> None:
    """One table → intersection is that table's value set → max is in range."""
    tables = {
        "table_solo": {
            "key": ["benefit_year"],
            "value": ["v"],
            "rows": [
                {"benefit_year": 2023, "v": 1},
                {"benefit_year": 2024, "v": 2},
                {"benefit_year": 2025, "v": 3},
                {"benefit_year": 2026, "v": 4},
            ],
        },
    }
    doc = _doc_with_tables(tables)
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    assert table_key_defaults["benefit_year"] == 2026


def test_disjoint_tables_warn_and_pick_from_largest_set(capsys) -> None:
    """When tables sharing a key have no common value, WARN and pick deterministically."""
    tables = {
        "table_a": {
            "key": ["k"],
            "value": ["v"],
            "rows": [{"k": 1, "v": 10}, {"k": 2, "v": 20}],
        },
        "table_b": {
            "key": ["k"],
            "value": ["v"],
            "rows": [{"k": 3, "v": 30}, {"k": 4, "v": 40}, {"k": 5, "v": 50}],
        },
    }
    doc = _doc_with_tables(tables, input_fields={"k": {"type": "int", "optional": True}})
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "'k'" in captured.err
    # Larger set wins (3 elements > 2) and max picks 5.
    assert table_key_defaults["k"] == 5


def test_string_key_intersection_picks_lex_min() -> None:
    """String-keyed tables — intersection determines defaults; lex-min for strings."""
    tables = {
        "table_a": {
            "key": ["category"],
            "value": ["v"],
            "rows": [{"category": "alpha", "v": 1}, {"category": "beta", "v": 2}, {"category": "gamma", "v": 3}],
        },
        "table_b": {
            "key": ["category"],
            "value": ["v"],
            "rows": [{"category": "beta", "v": 4}, {"category": "gamma", "v": 5}],
        },
    }
    doc = _doc_with_tables(
        tables, input_fields={"category": {"type": "string", "optional": True}}
    )
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    # Intersection {beta, gamma} → lex-min is "beta".
    assert table_key_defaults["category"] == "beta"


def test_empty_table_does_not_contribute_to_value_set() -> None:
    """A table with no rows must be skipped, not produce an empty intersection."""
    tables = {
        "table_filled": {
            "key": ["year"],
            "value": ["v"],
            "rows": [{"year": 2024, "v": 1}, {"year": 2025, "v": 2}],
        },
        "table_empty": {
            "key": ["year"],
            "value": ["v"],
            "rows": [],
        },
    }
    doc = _doc_with_tables(
        tables, input_fields={"year": {"type": "int", "optional": True}}
    )
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    # Empty table contributes no value set; intersection equals filled table's set.
    assert table_key_defaults["year"] == 2025


def test_key_in_only_one_table_uses_that_tables_values() -> None:
    """A key column present in just one of several tables uses that table's set."""
    tables = {
        "table_year": {
            "key": ["year"],
            "value": ["v"],
            "rows": [{"year": 2024, "v": 1}, {"year": 2025, "v": 2}],
        },
        "table_size": {
            "key": ["household_size"],
            "value": ["v"],
            "rows": [{"household_size": 1, "v": 1}, {"household_size": 8, "v": 8}],
        },
    }
    doc = _doc_with_tables(
        tables,
        input_fields={
            "year": {"type": "int", "optional": True},
            "household_size": {"type": "int", "optional": True},
        },
    )
    _, _, _, _, _, table_key_defaults = build_field_type_map(doc)
    assert table_key_defaults["year"] == 2025
    assert table_key_defaults["household_size"] == 8
