# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for transpile_to_catala_tests.py — verify the U7 manifest-driven
type-shaped lookups produce correct test-scope emission."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

import transpile_to_catala_tests as tct  # noqa: E402


# ---------------------------------------------------------------------------
# Type normalization
# ---------------------------------------------------------------------------

def test_normalize_catala_native_types():
    assert tct._normalize_type("integer") == "int"
    assert tct._normalize_type("decimal") == "float"
    assert tct._normalize_type("boolean") == "bool"
    assert tct._normalize_type("money") == "money"


def test_normalize_legacy_short_type_names():
    assert tct._normalize_type("int") == "int"
    assert tct._normalize_type("bool") == "bool"
    assert tct._normalize_type("enum") == "enum"


def test_normalize_unknown_type_falls_back_to_string():
    # Struct/enum type references default to string per the U7 fallback rule.
    assert tct._normalize_type("Household") == "string"


def test_normalize_none_returns_default():
    assert tct._normalize_type(None) == "string"


# ---------------------------------------------------------------------------
# Manifest-driven type map (U7)
# ---------------------------------------------------------------------------

def test_build_field_type_map_from_manifest_single_entity():
    manifest = {
        "inputs": {
            "Household": {
                "size": {"type": "integer", "optional": False},
                "monthly_income": {"type": "money", "optional": False},
            }
        },
        "computed": {},
        "outputs": {},
    }
    types, opt, enums, ent, comp = tct.build_field_type_map_from_manifest(
        manifest, "Eligibility"
    )
    assert types["size"] == "int"
    assert types["monthly_income"] == "money"
    assert opt["size"] is False
    assert ent["Household"][0] == ("size", "int", False)


def test_build_field_type_map_optional_flag_picked_up():
    manifest = {
        "inputs": {"Household": {"is_veteran": {"type": "boolean", "optional": True}}},
    }
    _, opt, _, _, _ = tct.build_field_type_map_from_manifest(manifest, "X")
    assert opt["is_veteran"] is True


def test_build_field_type_map_u7_enum_variants():
    """U7's `enum_variants:` field switches a field into enum-handling mode."""
    manifest = {
        "inputs": {
            "App": {
                "status": {
                    "type": "string",
                    "enum_variants": ["Eligible", "Denied"],
                },
            }
        },
    }
    types, _, enums, _, _ = tct.build_field_type_map_from_manifest(manifest, "X")
    assert types["status"] == "enum"
    assert enums["status"] == {"Eligible": "Eligible", "Denied": "Denied"}


def test_build_field_type_map_legacy_values_fallback():
    """Legacy `values:` list is still honored (PascalCased)."""
    manifest = {
        "inputs": {
            "App": {
                "tier": {"type": "string", "values": ["basic", "premium"]},
            }
        },
    }
    types, _, enums, _, _ = tct.build_field_type_map_from_manifest(manifest, "X")
    # `values:` is the legacy enum signal — Catala constructor names are
    # PascalCased per the pre-pivot convention.
    assert enums["tier"] == {"basic": "Basic", "premium": "Premium"}


def test_build_field_type_map_missing_type_defaults_and_warns(capsys):
    manifest = {
        "inputs": {"E": {"x": {}}},  # no type:
    }
    types, _, _, _, _ = tct.build_field_type_map_from_manifest(manifest, "X")
    assert types["x"] == "string"
    captured = capsys.readouterr()
    assert "no `type:`" in captured.err


def test_partition_outputs_by_type_money_int_bool_string():
    manifest = {
        "outputs": {
            "eligible": {"type": "boolean"},
            "amount": {"type": "money"},
            "status": {"type": "string", "enum_variants": ["Eligible", "Denied"]},
            "reasons": {"type": "list"},
        }
    }
    bools, strs, nums, denial = tct.partition_outputs_by_type(manifest)
    assert "eligible" in bools
    assert "status" in strs
    assert ("amount", "money") in nums
    assert denial == "reasons"


def test_partition_outputs_string_without_variants_skipped():
    """A `string` output with no enum_variants is not a decision."""
    manifest = {"outputs": {"label": {"type": "string"}}}
    bools, strs, nums, _ = tct.partition_outputs_by_type(manifest)
    assert "label" not in strs
    assert "label" not in bools
    assert not nums


# ---------------------------------------------------------------------------
# Catala literal emission
# ---------------------------------------------------------------------------

def test_money_literal_emission():
    assert tct.money_literal(1500) == "$1,500"
    assert tct.money_literal(1500.50) == "$1,500.50"
    assert tct.money_literal(-200) == "-$200"


def test_money_literal_accepts_catala_native_string():
    # Post-pivot test YAML authors money values as Catala-native string
    # literals: `gross_monthly_income: "$1,800"`. The helper must accept
    # those round-trip-style and not flag them as non-representable.
    assert tct.money_literal("$1,800") == "$1,800"
    assert tct.money_literal("$0") == "$0"
    assert tct.money_literal("$1,800.50") == "$1,800.50"
    assert tct.money_literal("-$500") == "-$500"
    # Numeric forms still work.
    assert tct.money_literal(1500) == "$1,500"


def test_value_to_catala_money():
    assert tct.value_to_catala(1500, "money") == "$1,500"


def test_value_to_catala_money_catala_native_string():
    # Round-trip the Catala literal — caller emitted it from a prior YAML
    # authoring round, and re-running the transpile must not silently
    # zero the field.
    assert tct.value_to_catala("$1,800", "money") == "$1,800"


def test_value_to_catala_enum_uses_variant_map():
    variants = {"Eligible": "Eligible", "Denied": "Denied"}
    assert tct.value_to_catala("Eligible", "enum", variants) == "Eligible"
    # Unknown variant returns None — caller defaults and warns.
    assert tct.value_to_catala("Other", "enum", variants) is None


# ---------------------------------------------------------------------------
# End-to-end transpile (happy path)
# ---------------------------------------------------------------------------

def test_transpile_emits_correct_money_literal(tmp_path: Path, capsys):
    """When the manifest declares a field as money, the emitted scope uses
    the money literal form ($N,NNN)."""
    manifest_path = tmp_path / "naming-manifest.yaml"
    with manifest_path.open("w") as f:
        yaml.safe_dump({
            "version": "1.0",
            "inputs": {
                "Applicant": {
                    "income": {"type": "money", "optional": False},
                },
            },
            "outputs": {
                "eligible": {"type": "boolean"},
            },
        }, f)

    tests_path = tmp_path / "eligibility_tests.yaml"
    with tests_path.open("w") as f:
        yaml.safe_dump({
            "test_suite": {"description": "demo"},
            "tests": [
                {
                    "case_id": "allow_001",
                    "description": "happy path",
                    "inputs": {"income": 1500},
                    "expected": {"eligible": True},
                    "tags": ["allow"],
                },
            ],
        }, f)

    out_path = tmp_path / "out.catala_en"
    tct.transpile(
        str(tests_path),
        str(out_path),
        "EligibilityDecision",
        str(manifest_path),
        catala_module_name="Eligibility",
    )
    text = out_path.read_text(encoding="utf-8")
    assert "definition result.income equals $1,500" in text
    assert "assertion (result.eligible = true)" in text


def test_transpile_empty_tests_emits_placeholder(tmp_path: Path):
    manifest_path = tmp_path / "naming-manifest.yaml"
    with manifest_path.open("w") as f:
        yaml.safe_dump({"version": "1.0"}, f)
    tests_path = tmp_path / "eligibility_tests.yaml"
    with tests_path.open("w") as f:
        yaml.safe_dump({"tests": []}, f)
    out_path = tmp_path / "out.catala_en"
    tct.transpile(
        str(tests_path),
        str(out_path),
        "EligibilityDecision",
        str(manifest_path),
        catala_module_name="Eligibility",
    )
    text = out_path.read_text(encoding="utf-8")
    assert "> Using Eligibility" in text


def test_transpile_duplicate_case_id_fails(tmp_path: Path):
    manifest_path = tmp_path / "naming-manifest.yaml"
    with manifest_path.open("w") as f:
        yaml.safe_dump({
            "inputs": {"A": {"x": {"type": "integer"}}},
            "outputs": {"y": {"type": "boolean"}},
        }, f)
    tests_path = tmp_path / "t.yaml"
    with tests_path.open("w") as f:
        yaml.safe_dump({
            "tests": [
                {"case_id": "dup_001", "inputs": {"x": 1}, "expected": {"y": True}},
                {"case_id": "dup_001", "inputs": {"x": 2}, "expected": {"y": False}},
            ]
        }, f)
    out_path = tmp_path / "out.catala_en"
    with pytest.raises(SystemExit):
        tct.transpile(
            str(tests_path),
            str(out_path),
            "Scope",
            str(manifest_path),
            catala_module_name="Mod",
        )
