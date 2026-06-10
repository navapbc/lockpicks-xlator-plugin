"""Tests for import_tests — short_description required + unique (plan 2026-06-10-001, U2).

Focused on the short_description validation and carry-through added in U2;
not a full import_tests suite. Exercises both the CSV and native-YAML parse
paths plus the test-case assembly in _build_test_case.
"""

from __future__ import annotations

import import_tests as it
from manifest_helpers import build_csv_field_specs

_MANIFEST = {
    "version": "2.0",
    "inputs": {"Household": {"size": {"type": "integer"}}},
    "outputs": {"eligible": {"type": "boolean"}},
}

_SPECS = build_csv_field_specs(_MANIFEST)

_HEADER = "case_id,short_description,description,size,expected_eligible,tags,notes"


def _codes(errors):
    return [e.get("code") for e in errors]


# --- CSV path ---------------------------------------------------------------

def test_csv_missing_short_description_is_required_error():
    csv = f"{_HEADER}\na1,,a description,3,true,allow,\n"
    errors: list[dict] = []
    rows = it._parse_csv_rows(csv, _SPECS, errors)
    assert "MISSING_REQUIRED" in _codes(errors)
    assert any(e["field"] == "short_description" for e in errors)


def test_csv_duplicate_short_description_is_hard_error():
    csv = (
        f"{_HEADER}\n"
        "a1,Approve — eligible,first,3,true,allow,\n"
        "a2,Approve — eligible,second,4,true,deny,\n"
    )
    errors: list[dict] = []
    it._parse_csv_rows(csv, _SPECS, errors)
    assert "DUPLICATE_SHORT_DESCRIPTION" in _codes(errors)


def test_csv_duplicate_case_id_warns_not_errors(capsys):
    """Duplicate case_id stays a last-row-wins WARN; only short_description
    duplication is a hard error. The two rules are independent."""
    csv = (
        f"{_HEADER}\n"
        "dup,Approve — first,first,3,true,allow,\n"
        "dup,Deny — second,second,4,false,deny,\n"
    )
    errors: list[dict] = []
    it._parse_csv_rows(csv, _SPECS, errors)
    assert "DUPLICATE_SHORT_DESCRIPTION" not in _codes(errors)
    assert "duplicate case_id" in capsys.readouterr().err


def test_csv_label_only_row_is_skipped_as_blank_stub():
    """A row carrying only case_id + short_description (no fact/decision
    columns) is treated as a blank stub and skipped — not surfaced as a
    MISSING_REQUIRED fact error."""
    csv = f"{_HEADER}\nstub,Just a label,,,,,\n"
    errors: list[dict] = []
    rows = it._parse_csv_rows(csv, _SPECS, errors)
    assert rows == []
    assert errors == []


def test_csv_valid_row_carries_short_description():
    csv = f"{_HEADER}\na1,Approve — eligible,a description,3,true,allow,\n"
    errors: list[dict] = []
    rows = it._parse_csv_rows(csv, _SPECS, errors)
    assert errors == []
    assert rows[0]["short_description"] == "Approve — eligible"


# --- YAML path --------------------------------------------------------------

def test_yaml_missing_short_description_is_required_error():
    yaml_doc = "tests:\n  - case_id: a1\n    description: d\n    inputs: {size: 3}\n"
    errors: list[dict] = []
    it._parse_yaml_rows(yaml_doc, errors)
    assert "MISSING_REQUIRED" in _codes(errors)
    assert any(e["field"] == "short_description" for e in errors)


def test_yaml_duplicate_short_description_is_hard_error():
    yaml_doc = (
        "tests:\n"
        "  - case_id: a1\n    short_description: Approve — ok\n    description: d1\n"
        "  - case_id: a2\n    short_description: Approve — ok\n    description: d2\n"
    )
    errors: list[dict] = []
    it._parse_yaml_rows(yaml_doc, errors)
    assert "DUPLICATE_SHORT_DESCRIPTION" in _codes(errors)


# --- assembly ---------------------------------------------------------------

def test_build_test_case_orders_short_description_after_case_id():
    parsed = {
        "case_id": "a1",
        "short_description": "Approve — eligible",
        "description": "a description",
        "inputs": {"size": 3},
        "expected": {"eligible": True},
        "tags": None,
        "notes": None,
    }
    tc = it._build_test_case(parsed, {}, _SPECS)
    keys = list(tc.keys())
    assert keys[:3] == ["case_id", "short_description", "description"]
