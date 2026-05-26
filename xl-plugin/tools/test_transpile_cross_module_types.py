# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for cross-module type resolution in transpile_to_catala.py (ticket 15).

When a CIVIL `type: string` field has no local table or values: declaration,
the transpiler should look up the field's enum type from loaded sub-module docs
and emit a qualified Catala type (e.g. Program_standards_lookup.HouseholdType)
rather than falling back to integer.

Covers:
- build_cross_module_enums() unit tests (pure function)
- emit_declarations() integration: struct fields use qualified type
- Layer 2 divergence check
- transpile() end-to-end: qualified type in .catala_en output
"""

import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import build_cross_module_enums, emit_declarations, transpile


# =============================================================================
# Fixtures
# =============================================================================

def _sub_doc_with_table(field_name: str, variants: list[str]) -> dict:
    """Sub-module CIVIL doc that keys a table on `field_name`, inferring an enum."""
    return {
        "module": "sub_mod",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    field_name: {"type": "string"},
                    "household_size": {"type": "int"},
                }
            }
        },
        "tables": {
            "lookup_table": {
                "key": [field_name],
                "value": ["amount"],
                "rows": [{field_name: v, "amount": 100} for v in variants],
            }
        },
        "outputs": {"result": {"type": "bool"}},
    }


def _parent_doc(parent_fields: dict, sub_module_name: str = "sub_mod") -> dict:
    """Parent CIVIL doc with Household invoke-bound via bind."""
    return {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {"fields": parent_fields}
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": sub_module_name,
                "invoke": {"bind": {"Household": "Household"}},
            }
        },
        "outputs": {"result": {"type": "bool"}},
    }


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w") as fh:
        yaml.dump(data, fh)


# =============================================================================
# Unit tests — build_cross_module_enums
# =============================================================================

def test_cross_module_enums_empty_when_no_sub_modules():
    result = build_cross_module_enums({})
    assert result == {}


def test_cross_module_enums_finds_table_keyed_field():
    sub_doc = _sub_doc_with_table("household_type", ["A1E", "B1E", "H1E"])
    result = build_cross_module_enums({"sub_mod": sub_doc})

    assert "household_type" in result
    qualified_type, variants = result["household_type"]
    assert qualified_type == "Sub_mod.HouseholdType"
    assert sorted(variants) == ["A1E", "B1E", "H1E"]


def test_cross_module_enums_ignores_non_string_fields():
    sub_doc = {
        "inputs": {
            "Household": {
                "fields": {
                    "household_size": {"type": "int"},
                    "gross_income": {"type": "money"},
                }
            }
        },
        "tables": {},
    }
    result = build_cross_module_enums({"sub_mod": sub_doc})
    assert result == {}


def test_cross_module_enums_ignores_string_without_table():
    sub_doc = {
        "inputs": {
            "Household": {
                "fields": {"household_type": {"type": "string"}}
            }
        },
        "tables": {},  # no table keyed on household_type
    }
    result = build_cross_module_enums({"sub_mod": sub_doc})
    assert result == {}


def test_cross_module_enums_picks_up_values_declared_field():
    sub_doc = {
        "inputs": {
            "Household": {
                "fields": {
                    "status": {"type": "string", "values": ["active", "inactive"]},
                }
            }
        },
        "tables": {},
    }
    result = build_cross_module_enums({"sub_mod": sub_doc})
    assert "status" in result
    qualified_type, variants = result["status"]
    assert qualified_type == "Sub_mod.Status"
    assert sorted(variants) == ["active", "inactive"]


def test_cross_module_enums_same_variants_across_modules_no_error():
    """Two sub-modules with the SAME variants for a field should not fail."""
    sub_a = _sub_doc_with_table("household_type", ["A1E", "B1E"])
    sub_b = _sub_doc_with_table("household_type", ["A1E", "B1E"])
    # Should not raise — same variants, first one wins
    result = build_cross_module_enums({"sub_a": sub_a, "sub_b": sub_b})
    assert "household_type" in result


def test_cross_module_enums_divergent_variants_fails():
    """Two sub-modules with DIFFERENT variants for the same field → SystemExit."""
    sub_a = _sub_doc_with_table("household_type", ["A1E", "B1E"])
    sub_b = _sub_doc_with_table("household_type", ["X", "Y", "Z"])
    with pytest.raises(SystemExit) as exc_info:
        build_cross_module_enums({"sub_a": sub_a, "sub_b": sub_b})
    assert exc_info.value.code == 1


def test_cross_module_enums_divergence_error_names_both_modules(capsys):
    sub_a = _sub_doc_with_table("household_type", ["A1E", "B1E"])
    sub_b = _sub_doc_with_table("household_type", ["X", "Y"])
    with pytest.raises(SystemExit):
        build_cross_module_enums({"sub_a": sub_a, "sub_b": sub_b})
    captured = capsys.readouterr()
    assert "household_type" in captured.err
    assert "Sub_a" in captured.err or "Sub_b" in captured.err


# =============================================================================
# Unit tests — emit_declarations with cross-module type
# =============================================================================

def _run_emit_declarations(parent_doc: dict, sub_module_docs: dict) -> list[str]:
    from transpile_to_catala import emit_declarations
    return emit_declarations(parent_doc, "ParentDecision", sub_module_docs=sub_module_docs)


def test_emit_declarations_struct_uses_cross_module_qualified_type():
    """Structure field with no local enum picks up qualified type from sub-module."""
    sub_doc = _sub_doc_with_table("household_type", ["A1E", "B1E", "H1E"])
    parent_doc = _parent_doc({
        "household_type": {"type": "string"},
        "household_size": {"type": "int"},
    })
    lines = _run_emit_declarations(parent_doc, {"sub_mod": sub_doc})
    joined = "\n".join(lines)

    assert "data household_type content Sub_mod.HouseholdType" in joined
    # Local enum declaration must NOT be emitted (it lives in the sub-module)
    assert "declaration enumeration HouseholdType:" not in joined


def test_emit_declarations_local_enum_wins_over_cross_module():
    """When the parent has its own table for a field, local enum takes precedence."""
    sub_doc = _sub_doc_with_table("household_type", ["X", "Y"])
    parent_doc = _parent_doc({"household_type": {"type": "string"}})
    # Give the parent its own table keyed on household_type
    parent_doc["tables"] = {
        "local_lookup": {
            "key": ["household_type"],
            "value": ["amount"],
            "rows": [{"household_type": "A1E", "amount": 100}],
        }
    }
    lines = _run_emit_declarations(parent_doc, {"sub_mod": sub_doc})
    joined = "\n".join(lines)

    # Local enum declared locally and used without qualifier
    assert "declaration enumeration HouseholdType:" in joined
    assert "data household_type content HouseholdType" in joined
    assert "Sub_mod.HouseholdType" not in joined


def test_emit_declarations_required_string_no_variants_raises_error():
    """Required string field with no enum variants raises ValueError (no integer downgrade)."""
    parent_doc = _parent_doc({"some_field": {"type": "string"}})
    with pytest.raises(ValueError, match="some_field"):
        _run_emit_declarations(parent_doc, {})


def test_emit_declarations_optional_string_no_variants_omitted_from_struct():
    """Optional string field with no enum variants is absent from struct declaration."""
    parent_doc = _parent_doc({
        "some_field": {"type": "string", "optional": True},
        "household_size": {"type": "int"},
    })
    lines = _run_emit_declarations(parent_doc, {})
    joined = "\n".join(lines)

    assert "some_field" not in joined
    assert "data household_size content integer" in joined


def test_emit_declarations_optional_string_no_variants_omitted_from_scope_inputs():
    """Optional string field on a non-invoke-bound entity is absent from scope inputs."""
    doc = {
        "module": "test_mod",
        "description": "Test",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Applicant": {
                "fields": {
                    "label": {"type": "string", "optional": True},
                    "age": {"type": "int"},
                }
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    lines = _run_emit_declarations(doc, {})
    joined = "\n".join(lines)

    assert "label" not in joined
    assert "input age content integer" in joined


def test_emit_declarations_required_string_no_variants_scope_input_raises_error():
    """Required string field on a non-invoke-bound entity raises ValueError."""
    doc = {
        "module": "test_mod",
        "description": "Test",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Applicant": {
                "fields": {
                    "category": {"type": "string"},
                }
            }
        },
        "outputs": {"eligible": {"type": "bool"}},
    }
    with pytest.raises(ValueError, match="category"):
        _run_emit_declarations(doc, {})


def test_emit_declarations_multiple_cross_module_fields():
    """All forwarded fields that need qualified types get them."""
    sub_doc = {
        "module": "sub_mod",
        "inputs": {
            "Household": {
                "fields": {
                    "household_type": {"type": "string"},
                    "living_arrangement": {"type": "string"},
                }
            }
        },
        "tables": {
            "ht_table": {
                "key": ["household_type"],
                "value": ["amount"],
                "rows": [{"household_type": "A1E", "amount": 1}],
            },
            "la_table": {
                "key": ["living_arrangement"],
                "value": ["amount"],
                "rows": [{"living_arrangement": "A", "amount": 2}],
            },
        },
    }
    parent_doc = _parent_doc({
        "household_type": {"type": "string"},
        "living_arrangement": {"type": "string"},
        "household_size": {"type": "int"},
    })
    lines = _run_emit_declarations(parent_doc, {"sub_mod": sub_doc})
    joined = "\n".join(lines)

    assert "data household_type content Sub_mod.HouseholdType" in joined
    assert "data living_arrangement content Sub_mod.LivingArrangement" in joined
    assert "declaration enumeration HouseholdType:" not in joined
    assert "declaration enumeration LivingArrangement:" not in joined


# =============================================================================
# Integration tests — transpile() end-to-end
# =============================================================================

def test_transpile_emits_qualified_type_for_cross_module_field(tmp_path):
    """transpile() produces .catala_en with qualified enum type for forwarded string field."""
    sub_doc = _sub_doc_with_table("household_type", ["A1E", "B1E", "H1E"])
    parent_doc = _parent_doc({
        "household_type": {"type": "string"},
        "household_size": {"type": "int"},
    })
    # Parent must declare all sub-module fields (ticket 14 validation)
    parent_doc["inputs"]["Household"]["fields"]["household_size"] = {"type": "int"}

    sub_yaml = str(tmp_path / "sub_mod.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    assert "Sub_mod.HouseholdType" in content
    assert "data household_type content integer" not in content
    # No local re-declaration of the enum
    assert "declaration enumeration HouseholdType:" not in content


def test_transpile_local_enum_not_overridden_by_cross_module(tmp_path):
    """transpile() keeps local enum when parent has its own table for the field."""
    sub_doc = _sub_doc_with_table("household_type", ["X", "Y"])
    parent_doc = _parent_doc({
        "household_type": {"type": "string"},
        "household_size": {"type": "int"},
    })
    parent_doc["tables"] = {
        "local_lookup": {
            "key": ["household_type"],
            "value": ["amount"],
            "rows": [{"household_type": "A1E", "amount": 100}],
        }
    }

    sub_yaml = str(tmp_path / "sub_mod.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    content = open(output_catala).read()
    assert "declaration enumeration HouseholdType:" in content
    assert "data household_type content HouseholdType" in content
    assert "Sub_mod.HouseholdType" not in content
