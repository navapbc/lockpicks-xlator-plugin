# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for sum() rewrite in translate_expr_to_catala (ticket 20 / PLUGIN_IMPROVEMENTS #7).

Catala requires `sum <type> of <list>` — bare `sum(list)` is a syntax error.
The rewrite lives at step 3.65 in translate_expr_to_catala().

Covers:
- Bare ident: sum(field) → (sum money of field)
- Entity-prefixed ident: sum(Entity.field) → (sum money of field) after step-0 strip
- Invoke-bound entity: sum(Entity.field) → (sum money of entity.field) with struct prefix
- Multiple sums in one expression: both get rewritten
- list_item_types lookup: correct element type chosen over money default
- Integration: CIVIL spec with sum() expr transpiles to valid Catala form
"""

import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import build_list_item_types, translate_expr_to_catala, transpile


# =============================================================================
# Unit tests — translate_expr_to_catala step 3.65
# =============================================================================

def test_sum_bare_ident_rewrites_to_catala_form():
    result = translate_expr_to_catala(
        "sum(client_stated_resources_per_account)",
        list_item_types={"client_stated_resources_per_account": "money"},
    )
    assert "sum money of client_stated_resources_per_account" in result


def test_sum_falls_back_to_money_when_field_not_in_list_item_types():
    result = translate_expr_to_catala("sum(unknown_list)")
    assert "sum money of unknown_list" in result


def test_sum_entity_prefix_stripped_before_rewrite():
    """Entity.field prefix on a flat (non-invoke-bound) entity is stripped by step 0,
    then step 3.65 rewrites the bare ident."""
    result = translate_expr_to_catala(
        "sum(ClientData.client_stated_resources_per_account)",
        fact_entities={"ClientData"},
        list_item_types={"client_stated_resources_per_account": "money"},
    )
    assert "sum money of client_stated_resources_per_account" in result
    assert "ClientData" not in result


def test_sum_invoke_bound_entity_prefix_lowercased():
    """For invoke-bound entities, Entity.field becomes entity.field (struct access).
    The sum rewrite fires on entity.field form."""
    result = translate_expr_to_catala(
        "sum(ClientData.client_stated_resources_per_account)",
        fact_entities={"ClientData"},
        invoke_bound_entities={"ClientData"},
        list_item_types={"client_stated_resources_per_account": "money"},
    )
    assert "sum money of client_data.client_stated_resources_per_account" in result


def test_sum_multiple_in_one_expression_both_rewritten():
    result = translate_expr_to_catala(
        "sum(matched_account_higher) + sum(avs_only_accounts)",
        list_item_types={
            "matched_account_higher": "money",
            "avs_only_accounts": "money",
        },
    )
    assert "sum money of matched_account_higher" in result
    assert "sum money of avs_only_accounts" in result


def test_sum_uses_non_money_item_type_from_list_item_types():
    result = translate_expr_to_catala(
        "sum(payment_counts)",
        list_item_types={"payment_counts": "integer"},
    )
    assert "sum integer of payment_counts" in result
    assert "sum money of payment_counts" not in result


def test_sum_result_is_parenthesised():
    """The rewrite wraps the result in parens so the list identifier does not
    absorb trailing arithmetic (e.g. `/ number_of_payments`)."""
    result = translate_expr_to_catala(
        "sum(amounts) / count",
        list_item_types={"amounts": "money"},
    )
    assert result.startswith("(sum money of amounts)")


# =============================================================================
# Unit tests — build_list_item_types
# =============================================================================

def test_build_list_item_types_single_entity():
    inputs = {
        "ClientData": {
            "fields": {
                "resources_per_account": {"type": "list", "item": "money"},
                "household_size": {"type": "int"},
            }
        }
    }
    result = build_list_item_types(inputs)
    assert result == {"resources_per_account": "money"}


def test_build_list_item_types_multiple_entities():
    inputs = {
        "ClientData": {
            "fields": {
                "client_list": {"type": "list", "item": "money"},
            }
        },
        "AVSRecord": {
            "fields": {
                "avs_list": {"type": "list", "item": "money"},
                "scalar_field": {"type": "money"},
            }
        },
    }
    result = build_list_item_types(inputs)
    assert "client_list" in result
    assert "avs_list" in result
    assert "scalar_field" not in result


def test_build_list_item_types_defaults_item_to_money_when_missing():
    inputs = {
        "Entity": {
            "fields": {
                "some_list": {"type": "list"},
            }
        }
    }
    result = build_list_item_types(inputs)
    assert result["some_list"] == "money"


def test_build_list_item_types_empty_inputs():
    assert build_list_item_types({}) == {}
    assert build_list_item_types(None) == {}


# =============================================================================
# Integration test — transpile() end-to-end
# =============================================================================

def _resource_compatibility_doc() -> dict:
    return {
        "module": "resource_test",
        "description": "Test module for sum rewrite",
        "version": "2026Q1",
        "jurisdiction": {"level": "state", "country": "US", "state": "AK"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "ClientData": {
                "fields": {
                    "resources_per_account": {
                        "type": "list",
                        "item": "money",
                        "optional": True,
                    }
                }
            }
        },
        "computed": {
            "total_resources": {
                "type": "money",
                "expr": "sum(ClientData.resources_per_account)",
            }
        },
        "outputs": {
            "total_resources_value": {
                "type": "money",
                "source_field": "total_resources",
            }
        },
    }


def test_transpile_sum_expr_emits_catala_sum_of_form(tmp_path):
    doc = _resource_compatibility_doc()
    civil_path = str(tmp_path / "resource_test.civil.yaml")
    output_path = str(tmp_path / "resource_test.catala_en")

    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "ResourceTestDecision", civil_path=civil_path)

    content = open(output_path).read()
    assert "sum money of resources_per_account" in content
    assert "sum(resources_per_account)" not in content
    assert "sum(ClientData.resources_per_account)" not in content


def test_transpile_sum_no_bare_paren_form_remains(tmp_path):
    """After transpile, no `sum(...)` paren form should appear anywhere in output."""
    doc = _resource_compatibility_doc()
    civil_path = str(tmp_path / "resource_test.civil.yaml")
    output_path = str(tmp_path / "resource_test.catala_en")

    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "ResourceTestDecision", civil_path=civil_path)

    content = open(output_path).read()
    import re
    assert not re.search(r"\bsum\(", content), (
        f"Found bare sum(...) in output:\n"
        + "\n".join(line for line in content.splitlines() if "sum(" in line)
    )
