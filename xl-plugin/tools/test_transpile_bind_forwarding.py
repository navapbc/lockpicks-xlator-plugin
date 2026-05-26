# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for bind-forwarding validation in transpile_to_catala.py (ticket 14).

Covers:
- check_bind_forwarding() unit tests (pure function, no I/O)
- transpile() integration tests (temp files, verifies no output written on error)
"""

import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import check_bind_forwarding, emit_subscope_wiring, transpile


# =============================================================================
# Fixtures
# =============================================================================

def _make_sub_doc(fields: dict) -> dict:
    """Minimal sub-module CIVIL doc with given Household fields."""
    return {
        "module": "sub_module",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": fields,
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }


def _make_parent_doc(parent_fields: dict) -> dict:
    """Minimal parent CIVIL doc that invokes sub_module with bind: {Household: Household}."""
    return {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": parent_fields,
            }
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"Household": "Household"},
                },
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }


# =============================================================================
# Unit tests — check_bind_forwarding
# =============================================================================

def test_no_errors_when_parent_has_all_fields():
    sub_doc = _make_sub_doc({"gross_income": {"type": "money"}})
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    computed = parent_doc["computed"]
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, computed)

    assert errors == []


def test_error_when_parent_missing_field():
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    computed = parent_doc["computed"]
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, computed)

    assert len(errors) == 1
    assert "pay_frequency" in errors[0]


def test_error_names_module_entity_and_missing_fields():
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    computed = parent_doc["computed"]
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, computed)

    assert len(errors) == 1
    error = errors[0]
    assert "sub_module" in error
    assert "Household" in error
    assert "pay_frequency" in error
    # Should tell user how to fix it
    assert "optional" in error or "parent CIVIL spec" in error


def test_no_bind_no_error():
    """invoke: without bind: should not trigger validation."""
    parent_doc = {
        "inputs": {"Household": {"fields": {"gross_income": {"type": "money"}}}},
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {},  # no bind key
            }
        },
    }
    sub_doc = _make_sub_doc({"gross_income": {"type": "money"}, "pay_frequency": {"type": "int"}})
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


def test_non_invoke_computed_field_skipped():
    """Computed fields without invoke: should be ignored."""
    parent_doc = {
        "inputs": {"Household": {"fields": {"gross_income": {"type": "money"}}}},
        "computed": {
            "plain_field": {
                "type": "money",
                "expr": "Household.gross_income * 2",
            }
        },
    }
    errors = check_bind_forwarding(parent_doc, {}, parent_doc["computed"])

    assert errors == []


def test_multiple_missing_fields_all_named():
    """All missing fields should appear in the single error for one bind pair."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
        "hours_worked": {"type": "int"},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    computed = parent_doc["computed"]
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, computed)

    assert len(errors) == 1
    assert "pay_frequency" in errors[0]
    assert "hours_worked" in errors[0]


# =============================================================================
# Integration tests — transpile()
# =============================================================================

def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w") as fh:
        yaml.dump(data, fh)


def test_transpile_exits_on_missing_bind_fields(tmp_path):
    """transpile() must exit(1) and NOT write the .catala_en when bind fields are missing."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    with pytest.raises(SystemExit) as exc_info:
        transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert exc_info.value.code == 1
    assert not os.path.exists(output_catala), "No .catala_en should be written when bind validation fails"


def test_transpile_succeeds_when_all_bind_fields_present(tmp_path):
    """transpile() writes the .catala_en including subscope wiring when bind is valid."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    # Subscope wiring should forward both fields
    assert "sub_result.gross_income equals household.gross_income" in content
    assert "sub_result.pay_frequency equals household.pay_frequency" in content


def test_transpile_exits_on_missing_submodule_file(tmp_path):
    """transpile() must exit(1) with a clear message when the sub-module .civil.yaml is absent."""
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(parent_yaml, parent_doc)
    # Intentionally do NOT write sub_module.civil.yaml

    with pytest.raises(SystemExit) as exc_info:
        transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert exc_info.value.code == 1
    assert not os.path.exists(output_catala)


# =============================================================================
# Ticket 16 — subscope wiring must not emit forwarding lines for omitted fields
# =============================================================================


def _make_sub_doc_with_optional_string(extra_fields: dict = None) -> dict:
    """Sub-module CIVIL doc with an optional string field (no variants) plus a money field."""
    fields = {
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "string", "optional": True},
    }
    if extra_fields:
        fields.update(extra_fields)
    return {
        "module": "sub_module",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": fields,
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }


def _make_parent_doc_without_optional_field() -> dict:
    """Parent CIVIL doc that invokes sub_module — declares gross_income but not pay_frequency."""
    return {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    "gross_income": {"type": "money"},
                }
            }
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"Household": "Household"},
                },
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }


# --- Unit tests: emit_subscope_wiring ---


def test_subscope_wiring_omits_optional_string_no_variants():
    """Wiring must not emit a forwarding line for an optional string field with no variants."""
    sub_doc = _make_sub_doc_with_optional_string()
    computed = _make_parent_doc_without_optional_field()["computed"]
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(computed, "ParentDecision", sub_module_docs)

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "pay_frequency" not in wiring_text
    assert "gross_income" in wiring_text


def test_subscope_wiring_includes_string_field_with_values():
    """Wiring must emit a forwarding line when the string field has declared values."""
    sub_doc = _make_sub_doc_with_optional_string(
        extra_fields={
            "pay_frequency": {
                "type": "string",
                "optional": True,
                "values": ["weekly", "biweekly", "monthly"],
            }
        }
    )
    parent_doc = {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    "gross_income": {"type": "money"},
                    "pay_frequency": {
                        "type": "string",
                        "optional": True,
                        "values": ["weekly", "biweekly", "monthly"],
                    },
                }
            }
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"Household": "Household"},
                },
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(parent_doc["computed"], "ParentDecision", sub_module_docs)

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "pay_frequency" in wiring_text
    assert "gross_income" in wiring_text


# --- Unit tests: check_bind_forwarding ---


def test_check_bind_forwarding_ignores_omitted_fields():
    """Fields omitted from the sub-module's Catala scope must not be required of the parent."""
    sub_doc = _make_sub_doc_with_optional_string()
    parent_doc = _make_parent_doc_without_optional_field()
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


# --- Integration tests: transpile ---


def test_transpile_omits_optional_string_wiring_line(tmp_path):
    """transpile() must not emit a wiring line for an optional-string-no-variants sub-module field."""
    sub_doc = _make_sub_doc_with_optional_string()
    parent_doc = _make_parent_doc_without_optional_field()

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    assert "sub_result.gross_income equals household.gross_income" in content
    assert "sub_result.pay_frequency" not in content


def test_transpile_includes_string_with_values_wiring_line(tmp_path):
    """transpile() must emit wiring for a string field that has declared values."""
    sub_doc = _make_sub_doc_with_optional_string(
        extra_fields={
            "pay_frequency": {
                "type": "string",
                "optional": True,
                "values": ["weekly", "biweekly", "monthly"],
            }
        }
    )
    parent_doc = {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    "gross_income": {"type": "money"},
                    "pay_frequency": {
                        "type": "string",
                        "optional": True,
                        "values": ["weekly", "biweekly", "monthly"],
                    },
                }
            }
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"Household": "Household"},
                },
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    assert "sub_result.gross_income equals household.gross_income" in content
    assert "sub_result.pay_frequency equals household.pay_frequency" in content
