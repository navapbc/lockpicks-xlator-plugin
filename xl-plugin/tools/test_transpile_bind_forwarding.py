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


# =============================================================================
# Audit #23.4 — cross-entity bind shape (Household → ClientData)
# =============================================================================
# Every test above uses bind: {"Household": "Household"} (same entity on both sides).
# These four tests confirm the check and wiring also work when the parent entity
# name differs from the sub-module entity name, e.g. bind: {"Household": "ClientData"}.
# =============================================================================


def _make_parent_doc_cross_entity(parent_entity_name: str, parent_fields: dict) -> dict:
    """Parent CIVIL doc that invokes sub_module with bind: {Household: parent_entity_name}.

    The parent's inputs use parent_entity_name (e.g. 'ClientData'), not 'Household'.
    This is the cross-entity bind shape: sub-module's Household fields are forwarded
    from a differently-named entity on the parent side.
    """
    return {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            parent_entity_name: {
                "fields": parent_fields,
            }
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"Household": parent_entity_name},
                },
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
    }


def test_cross_entity_error_when_parent_missing_field():
    """check_bind_forwarding detects a missing field on the parent's cross-entity entity."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc_cross_entity("ClientData", {"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert len(errors) == 1
    assert "ClientData" in errors[0]
    assert "pay_frequency" in errors[0]


def test_cross_entity_no_error_when_parent_has_all_fields():
    """check_bind_forwarding passes when the parent's cross-entity entity has all sub-module fields."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc_cross_entity("ClientData", {
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


def test_cross_entity_subscope_wiring_uses_parent_entity_var():
    """emit_subscope_wiring uses the parent entity variable (client_data), not the sub-module entity (household)."""
    sub_doc = _make_sub_doc({"gross_income": {"type": "money"}})
    parent_doc = _make_parent_doc_cross_entity("ClientData", {"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(parent_doc["computed"], "ParentDecision", sub_module_docs)

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "client_data.gross_income" in wiring_text
    assert "household.gross_income" not in wiring_text


def test_transpile_exits_on_cross_entity_missing_bind_fields(tmp_path):
    """transpile() exits(1) and writes no .catala_en when cross-entity bind fields are missing."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent_doc = _make_parent_doc_cross_entity("ClientData", {"gross_income": {"type": "money"}})

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    with pytest.raises(SystemExit) as exc_info:
        transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert exc_info.value.code == 1
    assert not os.path.exists(output_catala), "No .catala_en should be written when cross-entity bind validation fails"


# =============================================================================
# Ticket N — optional non-string sub-module fields are not required of parent
# =============================================================================
# The check currently enforces only non-optional fields. Optional money/bool/list
# fields in the sub-module may be absent from the parent entity; the wiring layer
# emits zero/empty defaults ($0, false, []) so the Catala struct stays fully
# initialized without referencing non-existent parent fields.
# =============================================================================


def test_check_bind_forwarding_ignores_optional_money_field():
    """Optional money fields in the sub-module are not required of the parent."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "iim_account_balance": {"type": "money", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


def test_check_bind_forwarding_ignores_optional_bool_and_list_fields():
    """Optional bool and list fields are not required of the parent."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "is_institutional": {"type": "bool", "optional": True},
        "client_stated_resources": {"type": "list", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


def test_check_bind_forwarding_still_requires_non_optional_fields():
    """Non-optional fields are still enforced regardless of the optional relaxation."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "required_count": {"type": "int"},
        "opt_balance": {"type": "money", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert len(errors) == 1
    assert "required_count" in errors[0]
    assert "opt_balance" not in errors[0]


def test_subscope_wiring_emits_zero_for_absent_optional_money_field():
    """Wiring emits $0 for an optional money field the parent entity doesn't have."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "iim_account_balance": {"type": "money", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.gross_income equals household.gross_income" in wiring_text
    assert "sub_result.iim_account_balance equals $0" in wiring_text


def test_subscope_wiring_emits_empty_list_for_absent_optional_list_field():
    """Wiring emits [] for an optional list field the parent entity doesn't have."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "payment_history": {"type": "list", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.payment_history equals []" in wiring_text


def test_subscope_wiring_emits_false_for_absent_optional_bool_field():
    """Wiring emits false for an optional bool field the parent entity doesn't have."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "is_institutional": {"type": "bool", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.is_institutional equals false" in wiring_text


def test_subscope_wiring_forwards_optional_field_when_parent_has_it():
    """When the parent entity has an optional field, it is forwarded normally — not zeroed."""
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "iim_account_balance": {"type": "money", "optional": True},
    })
    parent_doc = _make_parent_doc({
        "gross_income": {"type": "money"},
        "iim_account_balance": {"type": "money", "optional": True},
    })
    sub_module_docs = {"sub_module": sub_doc}

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.iim_account_balance equals household.iim_account_balance" in wiring_text
    assert "equals $0" not in wiring_text


def test_transpile_optional_absent_fields_get_zero_defaults(tmp_path):
    """transpile() succeeds and emits zero/empty defaults for optional sub-module fields
    absent from the parent entity — the medicaid_eligibility pattern where AVSData is
    bound to a module that also expects client-only fields.
    """
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "iim_account_balance": {"type": "money", "optional": True},
        "client_resources": {"type": "list", "optional": True},
        "is_institutional": {"type": "bool", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    assert "sub_result.gross_income equals household.gross_income" in content
    assert "sub_result.iim_account_balance equals $0" in content
    assert "sub_result.client_resources equals []" in content
    assert "sub_result.is_institutional equals false" in content


# =============================================================================
# _default_catala_literal — no silent fallback for non-scalar types (C-02)
# =============================================================================


def test_subscope_wiring_raises_for_optional_date_field_absent_from_parent():
    """emit_subscope_wiring raises ValueError when a sub-module has an optional
    type:date field that the parent entity does not supply.

    date has no safe Catala zero-literal — $0 would be a type error.
    """
    from transpile_to_catala import emit_subscope_wiring
    sub_doc = _make_sub_doc({
        "gross_income": {"type": "money"},
        "effective_date": {"type": "date", "optional": True},
    })
    parent_doc = _make_parent_doc({"gross_income": {"type": "money"}})
    sub_module_docs = {"sub_module": sub_doc}

    with pytest.raises(ValueError, match="effective_date|date"):
        emit_subscope_wiring(
            parent_doc["computed"], "ParentDecision", sub_module_docs,
            parent_inputs=parent_doc["inputs"],
        )


# =============================================================================
# Fix #28 — field_bind: forward computed values to sub-module inputs
# =============================================================================


def _make_sub_doc_with_computed_input(extra_inputs: dict = None) -> dict:
    """Sub-module with a gross_income field plus any extra_inputs (for field_bind targets)."""
    fields = {"gross_income": {"type": "money"}}
    if extra_inputs:
        fields.update(extra_inputs)
    return {
        "module": "sub_module",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {"Household": {"fields": fields}},
        "outputs": {"result": {"type": "bool"}},
    }


def _make_parent_doc_with_field_bind(
    parent_entity: str,
    parent_fields: dict,
    bind: dict,
    field_bind: dict,
) -> dict:
    """Parent CIVIL doc invoking sub_module with both bind: and field_bind:."""
    return {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {parent_entity: {"fields": parent_fields}},
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": bind,
                    "field_bind": field_bind,
                },
            }
        },
        "outputs": {"result": {"type": "bool"}},
    }


# --- Unit tests: check_bind_forwarding ---


def test_check_bind_forwarding_field_bind_satisfies_required_field():
    """A required sub-module field covered by field_bind: must not appear in errors."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert errors == []


def test_check_bind_forwarding_field_bind_does_not_suppress_other_missing():
    """field_bind: only satisfies the fields it explicitly names; other missing fields still error."""
    sub_doc = _make_sub_doc_with_computed_input({
        "gross_earned_income": {"type": "money"},
        "another_required": {"type": "int"},
    })
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert len(errors) == 1
    assert "another_required" in errors[0]
    assert "gross_earned_income" not in errors[0]


# --- Unit tests: emit_subscope_wiring ---


def test_subscope_wiring_emits_field_bind_expression():
    """field_bind: value is translated and emitted as a definition line."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}
    parent_context = {
        "constants": {},
        "tables": {},
        "fact_entities": {"ClientData"},
        "invoke_bound_entities": {"ClientData"},
    }

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        parent_context=parent_context,
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.gross_earned_income equals earned_class_result.gross_earned_income" in wiring_text


def test_subscope_wiring_field_bind_strips_entity_prefix():
    """A cross-entity reference like DOLRecord.dol_quarter_total strips the entity prefix."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"dol_quarter_total": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"dol_quarter_total": "DOLRecord.dol_quarter_total"}},
    )
    sub_module_docs = {"sub_module": sub_doc}
    # DOLRecord is a fact entity but NOT invoke-bound, so its prefix is stripped.
    parent_context = {
        "constants": {},
        "tables": {},
        "fact_entities": {"ClientData", "DOLRecord"},
        "invoke_bound_entities": {"ClientData"},
    }

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        parent_context=parent_context,
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.dol_quarter_total equals dol_quarter_total" in wiring_text
    assert "DOLRecord" not in wiring_text


def test_subscope_wiring_field_bind_and_bind_combined():
    """Both bind: and field_bind: definitions appear in the output wiring block."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}
    parent_context = {
        "constants": {},
        "tables": {},
        "fact_entities": {"ClientData"},
        "invoke_bound_entities": {"ClientData"},
    }

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        parent_context=parent_context,
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    assert "sub_result.gross_income equals client_data.gross_income" in wiring_text
    assert "sub_result.gross_earned_income equals earned_class_result.gross_earned_income" in wiring_text


# --- Integration test: transpile ---


def test_transpile_field_bind_resolves_computed_value(tmp_path):
    """transpile() succeeds and emits the correct definition line for a field_bind: entry."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )

    sub_yaml = str(tmp_path / "sub_module.civil.yaml")
    parent_yaml = str(tmp_path / "parent.civil.yaml")
    output_catala = str(tmp_path / "parent.catala_en")

    _write_yaml(sub_yaml, sub_doc)
    _write_yaml(parent_yaml, parent_doc)

    transpile(parent_doc, output_catala, "ParentDecision", civil_path=parent_yaml)

    assert os.path.exists(output_catala)
    content = open(output_catala).read()
    assert "sub_result.gross_income equals client_data.gross_income" in content
    assert "sub_result.gross_earned_income equals earned_class_result.gross_earned_income" in content


# --- F-03: field_bind sub-field name validation ---


def test_check_bind_forwarding_field_bind_typo_errors():
    """A misspelled field_bind sub-field name that doesn't exist in the sub-module returns an error."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"groos_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}

    errors = check_bind_forwarding(parent_doc, sub_module_docs, parent_doc["computed"])

    assert len(errors) >= 1
    assert any("groos_earned_income" in err for err in errors)


# --- F-04: bind + field_bind overlap produces no duplicate definition ---


def test_subscope_wiring_field_bind_overlap_no_duplicate_definition():
    """When a field appears in both bind: and field_bind:, only the field_bind: line is emitted."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    # gross_earned_income is in both the parent entity (reachable via bind) and field_bind.
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={
            "gross_income": {"type": "money"},
            "gross_earned_income": {"type": "money"},
        },
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}
    parent_context = {
        "constants": {},
        "tables": {},
        "fact_entities": {"ClientData"},
        "invoke_bound_entities": {"ClientData"},
    }

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        parent_context=parent_context,
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    # Only one definition line for gross_earned_income — the field_bind: expression, not the entity-bind copy.
    assert wiring_text.count("sub_result.gross_earned_income") == 1
    assert "sub_result.gross_earned_income equals earned_class_result.gross_earned_income" in wiring_text
    assert "sub_result.gross_earned_income equals client_data.gross_earned_income" not in wiring_text


# --- F-02: parent_context=None with field_bind emits a stderr warning ---


def test_subscope_wiring_field_bind_scoped_to_entity():
    """field_bind: covering a field on EntityA must not suppress entity-bind forwarding
    for a same-named field on EntityB in a multi-entity bind."""
    # Sub-module has two entities, each with a field named 'net_income'.
    sub_doc = {
        "module": "sub_module",
        "description": "Sub",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "EntityA": {"fields": {"net_income": {"type": "money"}}},
            "EntityB": {"fields": {"net_income": {"type": "money"}}},
        },
        "outputs": {"result": {"type": "bool"}},
    }
    parent_doc = {
        "module": "parent",
        "description": "Parent",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "ParentA": {"fields": {"net_income": {"type": "money"}}},
            "ParentB": {"fields": {"net_income": {"type": "money"}}},
        },
        "computed": {
            "sub_result": {
                "type": "object",
                "module": "sub_module",
                "invoke": {
                    "bind": {"EntityA": "ParentA", "EntityB": "ParentB"},
                    "field_bind": {
                        "EntityA": {"net_income": "computed_net_income"},
                    },
                },
            }
        },
        "outputs": {"result": {"type": "bool"}},
    }
    sub_module_docs = {"sub_module": sub_doc}
    parent_context = {
        "constants": {},
        "tables": {},
        "fact_entities": {"ParentA", "ParentB"},
        "invoke_bound_entities": {"ParentA", "ParentB"},
    }

    chunks = emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        parent_context=parent_context,
    )

    assert len(chunks) == 1
    _field_name, _desc, _source, code_lines = chunks[0]
    wiring_text = "\n".join(code_lines)
    # EntityA's net_income comes from field_bind expression.
    assert "sub_result.net_income equals computed_net_income" in wiring_text
    # EntityB's net_income must still be forwarded from entity-bind (not suppressed).
    assert "sub_result.net_income equals parent_b.net_income" in wiring_text


def test_subscope_wiring_warns_when_field_bind_without_context(capsys):
    """emit_subscope_wiring writes a warning to stderr when field_bind is present but parent_context is None."""
    sub_doc = _make_sub_doc_with_computed_input(
        {"gross_earned_income": {"type": "money"}}
    )
    parent_doc = _make_parent_doc_with_field_bind(
        parent_entity="ClientData",
        parent_fields={"gross_income": {"type": "money"}},
        bind={"Household": "ClientData"},
        field_bind={"Household": {"gross_earned_income": "earned_class_result.gross_earned_income"}},
    )
    sub_module_docs = {"sub_module": sub_doc}

    emit_subscope_wiring(
        parent_doc["computed"], "ParentDecision", sub_module_docs,
        parent_inputs=parent_doc["inputs"],
        # parent_context intentionally omitted
    )

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "parent_context" in captured.err or "field_bind" in captured.err
