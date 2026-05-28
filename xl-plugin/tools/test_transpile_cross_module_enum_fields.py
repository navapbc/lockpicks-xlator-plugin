# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for build_cross_module_enums extension to type: "enum" fields.

Previously build_cross_module_enums only scanned type: "string" fields inferred as
enums via table keys. It now also scans type: "enum" fields with explicit values: lists.

This prevents parent modules from redeclaring enums that already exist in sub-modules,
which would create distinct Catala types and break subscope wiring.

Covers:
- type:"enum" fields are detected and registered with qualified type name
- type:"string" fields still work as before (no regression)
- Same enum in two sub-modules with identical variants: first-found wins, no error
- Same enum in two sub-modules with divergent variants: fail()
- Integration: parent module uses qualified type for enum field from sub-module
"""

import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import (
    _emit_enum_conversion_match,
    _wire_enum_or_default,
    build_cross_module_enums,
    build_cross_module_enums_per_module,
    transpile,
)


# =============================================================================
# Unit tests — build_cross_module_enums
# =============================================================================

def _make_sub_doc(module_name: str, enum_field: str, values: list, field_type: str = "enum") -> dict:
    return {
        "module": module_name,
        "inputs": {
            "Entity": {
                "fields": {
                    enum_field: {
                        "type": field_type,
                        "values": values,
                    }
                }
            }
        },
    }


def test_enum_type_field_detected():
    sub_docs = {
        "b_exemption_flags": _make_sub_doc(
            "b_exemption_flags",
            "enrollment_intensity",
            ["less_than_half_time", "half_time", "full_time"],
        )
    }
    result = build_cross_module_enums(sub_docs)
    assert "enrollment_intensity" in result
    qualified_type, variants = result["enrollment_intensity"]
    assert qualified_type == "B_exemption_flags.EnrollmentIntensity"
    assert sorted(variants) == ["full_time", "half_time", "less_than_half_time"]


def test_string_type_field_still_works():
    sub_docs = {
        "prog_lookup": {
            "module": "prog_lookup",
            "inputs": {
                "HH": {
                    "fields": {
                        "household_type": {
                            "type": "string",
                            "values": ["single", "family"],
                        }
                    }
                }
            },
            "tables": {},
        }
    }
    result = build_cross_module_enums(sub_docs)
    assert "household_type" in result
    qualified_type, _ = result["household_type"]
    assert qualified_type == "Prog_lookup.HouseholdType"


def test_two_sub_modules_same_enum_same_variants_first_wins():
    sub_docs = {
        "b_exemption_flags": _make_sub_doc(
            "b_exemption_flags", "enrollment_intensity",
            ["less_than_half_time", "half_time", "full_time"],
        ),
        "ihe_classification": _make_sub_doc(
            "ihe_classification", "enrollment_intensity",
            ["less_than_half_time", "half_time", "full_time"],
        ),
    }
    result = build_cross_module_enums(sub_docs)
    qualified_type, _ = result["enrollment_intensity"]
    # First sub-module scanned wins
    assert qualified_type == "B_exemption_flags.EnrollmentIntensity"


def test_two_sub_modules_same_enum_divergent_variants_raises():
    sub_docs = {
        "mod_a": _make_sub_doc("mod_a", "campus_type", ["CCC", "CSU"]),
        "mod_b": _make_sub_doc("mod_b", "campus_type", ["CCC", "CSU", "UC"]),
    }
    with pytest.raises(SystemExit):
        build_cross_module_enums(sub_docs)


def test_non_enum_non_string_fields_ignored():
    sub_docs = {
        "some_mod": {
            "module": "some_mod",
            "inputs": {
                "Entity": {
                    "fields": {
                        "age": {"type": "int"},
                        "income": {"type": "money"},
                        "eligible": {"type": "bool"},
                    }
                }
            },
        }
    }
    result = build_cross_module_enums(sub_docs)
    assert result == {}


def test_enum_field_without_values_ignored():
    sub_docs = {
        "some_mod": _make_sub_doc("some_mod", "empty_enum", [])
    }
    result = build_cross_module_enums(sub_docs)
    assert "empty_enum" not in result


# =============================================================================
# Integration — parent module uses qualified enum type from sub-module
# =============================================================================

def _write_sub_module(tmp_path, name: str, enum_field: str, values: list) -> str:
    sub_doc = {
        "module": name,
        "description": f"Sub-module {name}",
        "version": "1.0",
        "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "Entity": {
                "fields": {
                    enum_field: {"type": "enum", "values": values},
                    "age": {"type": "int"},
                }
            }
        },
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
        "computed": {},
        "rules": [],
    }
    path = str(tmp_path / f"{name}.civil.yaml")
    with open(path, "w") as fh:
        yaml.dump(sub_doc, fh)
    return path


def test_cross_module_enum_parent_uses_qualified_type(tmp_path):
    # Write the sub-module with an enum field
    _write_sub_module(tmp_path, "sub_flags", "campus_type", ["CCC", "CSU", "UC"])

    parent_doc = {
        "module": "parent_module",
        "description": "Parent invoking sub_flags",
        "version": "1.0",
        "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "Applicant": {
                "fields": {
                    "campus_type": {"type": "enum", "values": ["CCC", "CSU", "UC"]},
                    "age": {"type": "int"},
                }
            }
        },
        "computed": {
            "flags_result": {
                "invoke": {"bind": {"Entity": "Applicant"}},
                "module": "sub_flags",
            }
        },
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
        "rules": [],
    }

    civil_path = str(tmp_path / "parent_module.civil.yaml")
    output_path = str(tmp_path / "parent_module.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(parent_doc, fh)

    transpile(parent_doc, output_path, "ParentModuleDecision", civil_path=civil_path)
    result = open(output_path).read()

    # Parent should use the qualified type, not declare its own local CampusType
    assert "Sub_flags.CampusType" in result
    # There should be no standalone local declaration of CampusType
    lines = result.splitlines()
    local_decl_lines = [
        line for line in lines
        if "declaration enumeration CampusType:" in line
    ]
    assert len(local_decl_lines) == 0, (
        f"Found unexpected local CampusType declaration: {local_decl_lines}"
    )


# =============================================================================
# Unit tests — build_cross_module_enums_per_module
# =============================================================================

def test_per_module_single_sub_module_returns_one_entry():
    sub_docs = {
        "sub_a": _make_sub_doc("sub_a", "campus_type", ["CCC", "CSU"]),
    }
    per_module = build_cross_module_enums_per_module(sub_docs)
    assert per_module == {"campus_type": {"sub_a": "Sub_a.CampusType"}}


def test_per_module_two_sub_modules_lists_both():
    sub_docs = {
        "sub_a": _make_sub_doc("sub_a", "enrollment_intensity", ["full_time", "half_time"]),
        "sub_b": _make_sub_doc("sub_b", "enrollment_intensity", ["full_time", "half_time"]),
    }
    per_module = build_cross_module_enums_per_module(sub_docs)
    assert per_module["enrollment_intensity"] == {
        "sub_a": "Sub_a.EnrollmentIntensity",
        "sub_b": "Sub_b.EnrollmentIntensity",
    }


def test_per_module_ignores_non_enum_fields():
    sub_docs = {
        "sub_a": {
            "module": "sub_a",
            "inputs": {"E": {"fields": {"age": {"type": "int"}}}},
        }
    }
    assert build_cross_module_enums_per_module(sub_docs) == {}


# =============================================================================
# Unit tests — _emit_enum_conversion_match
# =============================================================================

def test_emit_enum_conversion_match_pascal_case():
    block = _emit_enum_conversion_match(
        "applicant.enrollment_intensity",
        "Ihe_classification.EnrollmentIntensity",
        "B_exemption_flags.EnrollmentIntensity",
        ["less_than_half_time", "half_time", "full_time"],
    )
    assert "match applicant.enrollment_intensity with pattern" in block
    # Patterns must qualify the source type to disambiguate from the target type,
    # which Catala otherwise reports as an ambiguous constructor.
    assert (
        "-- Ihe_classification.EnrollmentIntensity.LessThanHalfTime : "
        "B_exemption_flags.EnrollmentIntensity.LessThanHalfTime"
    ) in block
    assert (
        "-- Ihe_classification.EnrollmentIntensity.HalfTime : "
        "B_exemption_flags.EnrollmentIntensity.HalfTime"
    ) in block
    assert (
        "-- Ihe_classification.EnrollmentIntensity.FullTime : "
        "B_exemption_flags.EnrollmentIntensity.FullTime"
    ) in block


# =============================================================================
# Unit tests — _wire_enum_or_default
# =============================================================================

def test_wire_enum_no_conflict_returns_plain():
    """Field only declared in one sub-module → no conversion needed."""
    per_module = {"campus_type": {"sub_a": "Sub_a.CampusType"}}
    result = _wire_enum_or_default(
        field="campus_type",
        sub_field_def={"type": "enum", "values": ["CCC"]},
        parent_var="applicant",
        sub_module_name="sub_a",
        cross_module_enums_per_module=per_module,
    )
    assert result == "applicant.campus_type"


def test_wire_enum_matching_types_returns_plain():
    """Two sub-modules but the current one is also the first-found (source). No conversion."""
    per_module = {
        "enrollment_intensity": {
            "sub_a": "Sub_a.EnrollmentIntensity",
            "sub_b": "Sub_b.EnrollmentIntensity",
        }
    }
    result = _wire_enum_or_default(
        field="enrollment_intensity",
        sub_field_def={"type": "enum", "values": ["full_time"]},
        parent_var="applicant",
        sub_module_name="sub_a",  # first in insertion order → matches source
        cross_module_enums_per_module=per_module,
    )
    assert result == "applicant.enrollment_intensity"


def test_wire_enum_differing_types_emits_match():
    """Two sub-modules, current one is NOT first-found → emit conversion."""
    per_module = {
        "enrollment_intensity": {
            "sub_a": "Sub_a.EnrollmentIntensity",
            "sub_b": "Sub_b.EnrollmentIntensity",
        }
    }
    result = _wire_enum_or_default(
        field="enrollment_intensity",
        sub_field_def={"type": "enum", "values": ["full_time", "half_time"]},
        parent_var="applicant",
        sub_module_name="sub_b",  # NOT the first-found (which is sub_a)
        cross_module_enums_per_module=per_module,
    )
    assert "match applicant.enrollment_intensity with pattern" in result
    # Source type is the first-found qtype (Sub_a, since sub_a is in dict first).
    assert "-- Sub_a.EnrollmentIntensity.FullTime : Sub_b.EnrollmentIntensity.FullTime" in result
    assert "-- Sub_a.EnrollmentIntensity.HalfTime : Sub_b.EnrollmentIntensity.HalfTime" in result


def test_wire_non_enum_returns_plain():
    """Non-enum fields bypass the conversion logic regardless of per-module dict."""
    result = _wire_enum_or_default(
        field="age",
        sub_field_def={"type": "int"},
        parent_var="applicant",
        sub_module_name="sub_a",
        cross_module_enums_per_module={"age": {"sub_a": "Sub_a.Age", "sub_b": "Sub_b.Age"}},
    )
    assert result == "applicant.age"


def test_wire_enum_no_per_module_dict_returns_plain():
    """When the per-module dict isn't supplied (legacy callers), fall back to plain."""
    result = _wire_enum_or_default(
        field="enrollment_intensity",
        sub_field_def={"type": "enum", "values": ["full_time"]},
        parent_var="applicant",
        sub_module_name="sub_a",
        cross_module_enums_per_module=None,
    )
    assert result == "applicant.enrollment_intensity"


# =============================================================================
# Integration — parent with two sub-modules sharing an enum
# =============================================================================

def test_two_sub_modules_sharing_enum_emits_match_in_one_wiring(tmp_path):
    """Parent invokes two sub-modules that both declare enrollment_intensity.
    The Applicant struct uses the first-found qualified type. The wiring to the
    OTHER sub-module's subscope must include a match-with-pattern conversion.
    """
    _write_sub_module(tmp_path, "ihe_classification", "enrollment_intensity",
                      ["less_than_half_time", "half_time", "full_time"])
    _write_sub_module(tmp_path, "b_exemption_flags", "enrollment_intensity",
                      ["less_than_half_time", "half_time", "full_time"])

    parent_doc = {
        "module": "parent_module",
        "description": "Parent invoking both sub-modules",
        "version": "1.0",
        "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "Applicant": {
                "fields": {
                    "enrollment_intensity": {
                        "type": "enum",
                        "values": ["less_than_half_time", "half_time", "full_time"],
                    },
                    "age": {"type": "int"},
                }
            }
        },
        "computed": {
            "ihe_result": {
                "invoke": {"bind": {"Entity": "Applicant"}},
                "module": "ihe_classification",
            },
            "exemption_result": {
                "invoke": {"bind": {"Entity": "Applicant"}},
                "module": "b_exemption_flags",
            },
        },
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
        "rules": [],
    }
    civil_path = str(tmp_path / "parent_module.civil.yaml")
    output_path = str(tmp_path / "parent_module.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(parent_doc, fh)

    transpile(parent_doc, output_path, "ParentModuleDecision", civil_path=civil_path)
    result = open(output_path).read()

    # First-found is ihe_classification (declared first in parent's computed map).
    # Wiring to ihe_classification subscope stays plain.
    assert "definition ihe_result.enrollment_intensity equals applicant.enrollment_intensity" in result
    # Wiring to b_exemption_flags subscope must convert via match.
    assert "match applicant.enrollment_intensity with pattern" in result
    assert "B_exemption_flags.EnrollmentIntensity.FullTime" in result
    assert "B_exemption_flags.EnrollmentIntensity.HalfTime" in result
    assert "B_exemption_flags.EnrollmentIntensity.LessThanHalfTime" in result
