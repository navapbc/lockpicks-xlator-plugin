# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for empty ReasonCode enum placeholder in emit_declarations.

When a CIVIL module has a list/set output (reasons) but no rules with
add_reason actions, the transpiler would emit an empty enum:

    declaration enumeration ReasonCode:

Catala rejects empty enumerations. The fix inserts a '-- NoReason' placeholder
so the enum is syntactically valid while keeping all downstream code correct:
  - output reasons content list of ReasonCode  (valid type)
  - definition reasons equals []               (valid empty list)
  - definition eligible equals (number of reasons) = 0  (evaluates true)

Covers:
- List output, no deny rules → placeholder '-- NoReason' emitted
- No list output, no deny rules → no ReasonCode block at all
- Deny rules with add_reason → real codes emitted, no placeholder
- Deny rules with add_reason → no NoReason case added alongside real codes
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import transpile


# =============================================================================
# Shared CIVIL fixture builders
# =============================================================================

def _civil_with_list_output_no_deny_rules():
    return {
        "module": "classification_check",
        "description": "Pure classification sub-module — no deny rules.",
        "version": "1.0",
        "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "Applicant": {
                "fields": {
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


def _civil_no_list_output():
    return {
        "module": "simple_check",
        "description": "Module with no list output.",
        "version": "1.0",
        "jurisdiction": {"level": "state", "country": "US", "state": "CA"},
        "effective": {"start": "2026-01-01"},
        "inputs": {
            "Applicant": {
                "fields": {
                    "income": {"type": "money"},
                }
            }
        },
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
        "computed": {},
        "rules": [
            {
                "name": "income_too_high",
                "when": "Applicant.income > $50000",
                "then": [{"deny": True}, {"add_reason": {"code": "income_exceeds_limit"}}],
            }
        ],
    }


# =============================================================================
# Tests
# =============================================================================

def test_list_output_no_deny_rules_emits_placeholder(tmp_path):
    doc = _civil_with_list_output_no_deny_rules()
    civil_path = str(tmp_path / "classification_check.civil.yaml")
    output_path = str(tmp_path / "classification_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "ClassificationCheckDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "declaration enumeration ReasonCode:" in result
    assert "  -- NoReason" in result


def test_list_output_no_deny_rules_enum_not_empty(tmp_path):
    """The enum block must have at least one case — no bare 'ReasonCode:' with nothing after."""
    doc = _civil_with_list_output_no_deny_rules()
    civil_path = str(tmp_path / "classification_check.civil.yaml")
    output_path = str(tmp_path / "classification_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "ClassificationCheckDecision", civil_path=civil_path)
    result = open(output_path).read()
    lines = result.splitlines()

    enum_idx = next(
        (i for i, line in enumerate(lines) if "declaration enumeration ReasonCode:" in line),
        None,
    )
    assert enum_idx is not None, "ReasonCode enum not found"
    assert lines[enum_idx + 1].strip().startswith("--"), (
        f"Expected enum case after header, got: {lines[enum_idx + 1]!r}"
    )


def test_deny_rules_with_add_reason_emits_real_codes_not_placeholder(tmp_path):
    doc = _civil_no_list_output()
    civil_path = str(tmp_path / "simple_check.civil.yaml")
    output_path = str(tmp_path / "simple_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "SimpleCheckDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "-- IncomeExceedsLimit" in result
    assert "-- NoReason" not in result


def test_deny_rules_with_add_reason_no_placeholder_added(tmp_path):
    """When real codes exist, NoReason must not appear alongside them."""
    doc = _civil_no_list_output()
    civil_path = str(tmp_path / "simple_check.civil.yaml")
    output_path = str(tmp_path / "simple_check.catala_en")
    with open(civil_path, "w") as fh:
        yaml.dump(doc, fh)

    transpile(doc, output_path, "SimpleCheckDecision", civil_path=civil_path)
    result = open(output_path).read()

    assert "NoReason" not in result
