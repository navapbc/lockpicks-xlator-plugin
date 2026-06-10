"""Tests for manifest_helpers — alias-map narrowing (plan 2026-06-01-002, U4).

The module is exercised indirectly by import_tests / export_test_template /
export_test_cases test suites, but those don't cover the alias map directly.
This file adds a focused regression guard for the strict Catala-native
vocabulary.
"""

from __future__ import annotations

import pytest

import manifest_helpers as mh


_CATALA_NATIVE_TO_INTERNAL = {
    "integer": "int",
    "decimal": "float",
    "boolean": "bool",
    "duration": "string",
    "money": "money",
    "string": "string",
    "enum": "enum",
    "list": "list",
    "date": "date",
    "structure": "string",
}

_LEGACY_CIVIL = ("bool", "int", "float", "str", "set", "object")


@pytest.mark.parametrize("name,internal", _CATALA_NATIVE_TO_INTERNAL.items())
def test_catala_native_names_normalize(name, internal):
    """Every Catala-native name maps to its internal leaf type."""
    assert mh._normalize_type(name) == internal


@pytest.mark.parametrize("name", _LEGACY_CIVIL)
def test_legacy_civil_names_fall_back_to_default(name):
    """Legacy CIVIL names are no longer aliased — they're treated as unknown
    and fall back to `_DEFAULT_LEAF_TYPE` ('string'). Regression guard
    against re-introducing legacy aliases."""
    assert mh._normalize_type(name) == mh._DEFAULT_LEAF_TYPE
    assert mh._DEFAULT_LEAF_TYPE == "string"


def test_unknown_type_falls_back_to_default():
    assert mh._normalize_type("Household") == mh._DEFAULT_LEAF_TYPE
    assert mh._normalize_type("frobnicate") == mh._DEFAULT_LEAF_TYPE


def test_none_falls_back_to_default():
    assert mh._normalize_type(None) == mh._DEFAULT_LEAF_TYPE


def test_short_description_is_reserved_column():
    """`short_description` is a reserved test-case column, so a manifest input
    field literally named `short_description` is forced to an `Entity.field`
    column rather than being emitted as a bare field column that would collide
    with the reserved test-case column."""
    assert "short_description" in mh.RESERVED_COLUMNS
    manifest_doc = {
        "version": "2.0",
        "inputs": {
            "Case": {
                "short_description": {"type": "string", "description": "x"},
            },
        },
        "outputs": {},
    }
    specs = mh.build_csv_field_specs(manifest_doc)
    # Single-entity manifest would normally emit the bare field name; the
    # reserved-column guard forces the Entity-prefixed form instead.
    assert specs[0].column_name == "Case.short_description"


def test_structure_type_in_field_specs_does_not_warn(capsys):
    """A `type: structure` manifest entry renders with leaf_type='string'
    and does NOT emit the 'has no `type:`' warning that the missing-type
    path triggers."""
    manifest_doc = {
        "version": "2.0",
        "inputs": {
            "Household": {
                "address": {"type": "structure", "description": "address"},
            },
        },
        "outputs": {},
    }
    specs = mh.build_csv_field_specs(manifest_doc)
    assert len(specs) == 1
    assert specs[0].leaf_type == "string"
    assert "has no `type:`" not in capsys.readouterr().err
