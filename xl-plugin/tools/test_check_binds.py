# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for the bind-consistency pass (check_binds.py).

Covers:
- compute_bind_repairs(): pure per-parent computation of additions + conflicts
- compute_domain_repairs(): walks an entire domain
- apply_bind_repairs(): edits a parent CIVIL in place, preserving comments
- ah-doh2 regression: reproduces the 12-field gap that motivated the design
"""

import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from check_binds import (
    FieldAddition,
    FieldConflict,
    apply_bind_repairs,
    compute_bind_repairs,
    compute_domain_repairs,
)


# =============================================================================
# Fixtures — minimal CIVIL docs (mirrors test_transpile_bind_forwarding.py shape)
# =============================================================================

def _sub_doc(sub_module_name: str, entity: str, fields: dict) -> dict:
    """Minimal sub-module doc declaring `entity` with the given fields."""
    return {
        "module": sub_module_name,
        "description": f"Sub-module {sub_module_name}",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {entity: {"fields": fields}},
        "outputs": {"result": {"type": "bool"}},
    }


def _parent_doc(
    parent_module: str,
    parent_entity: str,
    parent_fields: dict,
    binds: list[tuple[str, str, str]],   # (sub_module, sub_entity, parent_entity)
) -> dict:
    """Parent doc binding each (sub_module, sub_entity) → parent_entity."""
    computed = {}
    for sub_module, sub_entity, target_parent_entity in binds:
        computed[f"{sub_module}_result"] = {
            "type": "object",
            "module": sub_module,
            "invoke": {"bind": {sub_entity: target_parent_entity}},
        }
    return {
        "module": parent_module,
        "description": f"Parent {parent_module}",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {parent_entity: {"fields": parent_fields}},
        "computed": computed,
        "outputs": {"result": {"type": "bool"}},
    }


# =============================================================================
# compute_bind_repairs — single parent
# =============================================================================

def test_returns_empty_when_parent_has_all_fields():
    sub = _sub_doc("sub_a", "Household", {"gross_income": {"type": "money"}})
    parent = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}},
        [("sub_a", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub})

    assert additions == []
    assert conflicts == []


def test_detects_single_missing_field():
    sub = _sub_doc("sub_a", "Household", {
        "gross_income": {"type": "money"},
        "pay_frequency": {"type": "int"},
    })
    parent = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}},
        [("sub_a", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub})

    assert conflicts == []
    assert len(additions) == 1
    addition = additions[0]
    assert addition.parent_module == "parent"
    assert addition.parent_entity == "Household"
    assert addition.field_name == "pay_frequency"
    assert addition.source_sub_module == "sub_a"
    # Auto-imported fields are always optional
    assert addition.field_spec.get("optional") is True
    # Type carried over from sub-module
    assert addition.field_spec.get("type") == "int"


def test_optional_sub_fields_do_not_trigger_addition():
    """Optional sub-module fields are NOT required on the parent."""
    sub = _sub_doc("sub_a", "Household", {
        "gross_income": {"type": "money"},
        "optional_extra": {"type": "money", "optional": True},
    })
    parent = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}},
        [("sub_a", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub})

    assert additions == []
    assert conflicts == []


def test_unions_additions_across_multiple_subs():
    sub_a = _sub_doc("sub_a", "Household", {
        "gross_income": {"type": "money"},
        "age": {"type": "int"},
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "gross_income": {"type": "money"},
        "household_size": {"type": "int"},
    })
    parent = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, _ = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    names = sorted(addition.field_name for addition in additions)
    assert names == ["age", "household_size"]
    sources = {addition.field_name: addition.source_sub_module for addition in additions}
    assert sources["age"] == "sub_a"
    assert sources["household_size"] == "sub_b"


def test_deduplicates_when_multiple_subs_agree_on_same_field():
    """Two subs declaring `age: int` should produce ONE addition, not two."""
    shared = {"age": {"type": "int"}}
    sub_a = _sub_doc("sub_a", "Household", shared)
    sub_b = _sub_doc("sub_b", "Household", shared)
    parent = _parent_doc(
        "parent",
        "Household",
        {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert conflicts == []
    assert len(additions) == 1
    assert additions[0].field_name == "age"


def test_reports_type_conflict_between_subs():
    """When two subs disagree on the type of the same field, no addition is emitted."""
    sub_a = _sub_doc("sub_a", "Household", {"age": {"type": "int"}})
    sub_b = _sub_doc("sub_b", "Household", {"age": {"type": "money"}})
    parent = _parent_doc(
        "parent",
        "Household",
        {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert additions == []
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.parent_entity == "Household"
    assert conflict.field_name == "age"
    declarers = sorted(name for name, _ in conflict.declarations)
    assert declarers == ["sub_a", "sub_b"]


def test_field_bind_overrides_skip_addition():
    """Fields covered by an explicit field_bind: should not appear as additions."""
    sub = _sub_doc("sub_a", "Household", {
        "gross_income": {"type": "money"},
        "age": {"type": "int"},
    })
    parent_doc = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}, "parent_age": {"type": "int"}},
        [],
    )
    # Replace the computed entry with one that uses field_bind for `age`.
    parent_doc["computed"] = {
        "sub_a_result": {
            "type": "object",
            "module": "sub_a",
            "invoke": {
                "bind": {"Household": "Household"},
                "field_bind": {"Household": {"age": "parent_age"}},
            },
        }
    }

    additions, _ = compute_bind_repairs(parent_doc, {"sub_a": sub})

    assert additions == []


def test_non_invoke_computed_fields_ignored():
    parent_doc = {
        "module": "parent",
        "inputs": {"Household": {"fields": {"gross_income": {"type": "money"}}}},
        "computed": {
            "plain_field": {
                "type": "money",
                "expr": "Household.gross_income * 2",
            }
        },
    }

    additions, conflicts = compute_bind_repairs(parent_doc, {})

    assert additions == []
    assert conflicts == []


def test_missing_sub_module_doc_treated_as_no_op():
    """If the sub-module doc isn't loaded, skip silently (caller decides if that's an error)."""
    parent = _parent_doc(
        "parent",
        "Household",
        {"gross_income": {"type": "money"}},
        [("sub_a", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {})

    assert additions == []
    assert conflicts == []


# =============================================================================
# compute_domain_repairs — entire domain
# =============================================================================

def test_domain_repairs_returns_per_parent_diffs():
    sub_a = _sub_doc("sub_a", "Household", {"age": {"type": "int"}})
    sub_b = _sub_doc("sub_b", "Household", {"income": {"type": "money"}})
    parent_one = _parent_doc(
        "parent_one",
        "Household",
        {},
        [("sub_a", "Household", "Household")],
    )
    parent_two = _parent_doc(
        "parent_two",
        "Household",
        {},
        [("sub_b", "Household", "Household")],
    )

    diffs = compute_domain_repairs({
        "parent_one": parent_one,
        "parent_two": parent_two,
        "sub_a": sub_a,
        "sub_b": sub_b,
    })

    assert set(diffs.keys()) == {"parent_one", "parent_two"}
    parent_one_adds, _ = diffs["parent_one"]
    parent_two_adds, _ = diffs["parent_two"]
    assert [add.field_name for add in parent_one_adds] == ["age"]
    assert [add.field_name for add in parent_two_adds] == ["income"]


def test_domain_repairs_skips_modules_with_no_invokes():
    """A sub-module loaded as a parent candidate but with no computed.invoke entries → no diff."""
    sub_a = _sub_doc("sub_a", "Household", {"age": {"type": "int"}})

    diffs = compute_domain_repairs({"sub_a": sub_a})

    assert diffs == {}


# =============================================================================
# apply_bind_repairs — file I/O wrapper
# =============================================================================

def _write_civil(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip())


def test_apply_appends_field_to_existing_entity(tmp_path):
    civil = tmp_path / "parent.civil.yaml"
    _write_civil(civil, """
        module: parent
        description: Parent
        version: '1.0'
        effective: {start: '2024-01-01'}
        jurisdiction: {level: federal, country: US}
        inputs:
          Household:
            fields:
              gross_income:
                type: money
        computed: {}
        outputs:
          result:
            type: bool
    """)

    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    apply_bind_repairs(civil, additions)

    content = civil.read_text()
    assert "age:" in content
    assert "auto-imported from sub_a" in content
    assert "type: int" in content
    assert "optional: true" in content
    # Existing fields preserved
    assert "gross_income:" in content


def test_apply_is_idempotent(tmp_path):
    civil = tmp_path / "parent.civil.yaml"
    _write_civil(civil, """
        module: parent
        description: Parent
        version: '1.0'
        effective: {start: '2024-01-01'}
        jurisdiction: {level: federal, country: US}
        inputs:
          Household:
            fields:
              gross_income:
                type: money
        computed: {}
        outputs:
          result:
            type: bool
    """)
    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    apply_bind_repairs(civil, additions)
    first = civil.read_text()
    apply_bind_repairs(civil, additions)
    second = civil.read_text()

    assert first == second
    # Field appears exactly once
    assert second.count("age:") == 1


def test_apply_preserves_inline_comments(tmp_path):
    civil = tmp_path / "parent.civil.yaml"
    _write_civil(civil, """
        module: parent
        # IMPORTANT human-authored comment
        description: Parent
        version: '1.0'
        effective: {start: '2024-01-01'}
        jurisdiction: {level: federal, country: US}
        inputs:
          Household:
            fields:
              gross_income:
                type: money  # money in cents
        computed: {}
        outputs:
          result:
            type: bool
    """)

    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    apply_bind_repairs(civil, additions)

    content = civil.read_text()
    assert "IMPORTANT human-authored comment" in content
    assert "money in cents" in content


def test_apply_raises_when_entity_missing(tmp_path):
    civil = tmp_path / "parent.civil.yaml"
    _write_civil(civil, """
        module: parent
        description: Parent
        version: '1.0'
        effective: {start: '2024-01-01'}
        jurisdiction: {level: federal, country: US}
        inputs:
          OtherEntity:
            fields:
              gross_income:
                type: money
        computed: {}
        outputs:
          result:
            type: bool
    """)

    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",   # not present in file
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    with pytest.raises(ValueError) as exc_info:
        apply_bind_repairs(civil, additions)

    assert "Household" in str(exc_info.value)


# =============================================================================
# Conflict semantics — currency and values disagreements
# =============================================================================

def test_currency_disagreement_is_a_conflict():
    sub_a = _sub_doc("sub_a", "Household", {
        "amount": {"type": "money", "currency": "USD"},
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "amount": {"type": "money", "currency": "EUR"},
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert additions == []
    assert len(conflicts) == 1
    assert conflicts[0].field_name == "amount"


def test_enum_values_disagreement_is_a_conflict():
    sub_a = _sub_doc("sub_a", "Household", {
        "category": {"type": "string", "values": ["QMB", "SLMB"]},
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "category": {"type": "string", "values": ["A", "B"]},
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert additions == []
    assert len(conflicts) == 1


def test_same_enum_values_different_order_is_not_a_conflict():
    """C-01: enum membership is order-insensitive — the variants are a set."""
    sub_a = _sub_doc("sub_a", "Household", {
        "category": {"type": "string", "values": ["QMB", "SLMB", "NONE"]},
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "category": {"type": "string", "values": ["NONE", "QMB", "SLMB"]},
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert conflicts == []
    assert len(additions) == 1
    # First declarer's order wins for the emitted spec.
    assert additions[0].field_spec["values"] == ["QMB", "SLMB", "NONE"]


def test_missing_currency_conforms_to_specifier():
    """C-02: a sub that omits an attribute defers to the specifier. No conflict."""
    sub_a = _sub_doc("sub_a", "Household", {
        "amount": {"type": "money", "currency": "USD"},
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "amount": {"type": "money"},  # silent about currency
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert conflicts == []
    assert len(additions) == 1
    assert additions[0].field_spec.get("currency") == "USD"


def test_specifier_wins_regardless_of_declaration_order():
    """C-02: even if the silent sub appears first in the declarers list,
    the specifier's value is what ends up in the emitted spec."""
    sub_a = _sub_doc("sub_a", "Household", {
        "amount": {"type": "money"},  # silent first
    })
    sub_b = _sub_doc("sub_b", "Household", {
        "amount": {"type": "money", "currency": "EUR"},  # specifier second
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household"), ("sub_b", "Household", "Household")],
    )

    additions, conflicts = compute_bind_repairs(parent, {"sub_a": sub_a, "sub_b": sub_b})

    assert conflicts == []
    assert len(additions) == 1
    assert additions[0].field_spec.get("currency") == "EUR"


def test_currency_is_carried_into_addition():
    sub = _sub_doc("sub_a", "Household", {
        "amount": {"type": "money", "currency": "USD"},
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household")],
    )

    additions, _ = compute_bind_repairs(parent, {"sub_a": sub})

    assert len(additions) == 1
    assert additions[0].field_spec.get("currency") == "USD"


def test_enum_values_are_carried_into_addition():
    """Critical for the ticket-11 string-no-variants omit-rule — without values:,
    optional strings are dropped from the Catala scope entirely, defeating repair.
    """
    sub = _sub_doc("sub_a", "Household", {
        "category": {"type": "string", "values": ["QMB", "SLMB", "NONE"]},
    })
    parent = _parent_doc(
        "parent", "Household", {},
        [("sub_a", "Household", "Household")],
    )

    additions, _ = compute_bind_repairs(parent, {"sub_a": sub})

    assert len(additions) == 1
    assert additions[0].field_spec.get("values") == ["QMB", "SLMB", "NONE"]


# =============================================================================
# Indent detection — apply matches source file style
# =============================================================================

def test_apply_uses_4_space_indent_when_source_does(tmp_path):
    """H1: child_indent must come from the file, not be hard-coded to fields_indent+2."""
    civil = tmp_path / "parent.civil.yaml"
    # 4-space indent throughout
    civil.write_text(
        "module: parent\n"
        "inputs:\n"
        "    Household:\n"
        "        fields:\n"
        "            gross_income:\n"
        "                type: money\n"
        "computed: {}\n"
        "outputs:\n"
        "    result:\n"
        "        type: bool\n"
    )

    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    apply_bind_repairs(civil, additions)
    content = civil.read_text()

    # The new line must use 12-space indent to match siblings (8 for fields +4).
    age_lines = [line for line in content.split("\n") if "age:" in line]
    assert len(age_lines) == 1
    assert age_lines[0].startswith("            age:"), repr(age_lines[0])

    # File should still parse as valid YAML.
    import yaml as _yaml
    parsed = _yaml.safe_load(content)
    assert "age" in parsed["inputs"]["Household"]["fields"]


# =============================================================================
# CRLF preservation
# =============================================================================

def test_apply_preserves_crlf_line_endings(tmp_path):
    civil = tmp_path / "parent.civil.yaml"
    # write_bytes — write_text on POSIX may strip the CR in CRLF
    civil.write_bytes(
        b"module: parent\r\n"
        b"inputs:\r\n"
        b"  Household:\r\n"
        b"    fields:\r\n"
        b"      gross_income:\r\n"
        b"        type: money\r\n"
        b"computed: {}\r\n"
        b"outputs:\r\n"
        b"  result:\r\n"
        b"    type: bool\r\n"
    )

    additions = [FieldAddition(
        parent_module="parent",
        parent_entity="Household",
        field_name="age",
        field_spec={"type": "int", "optional": True},
        source_sub_module="sub_a",
    )]

    apply_bind_repairs(civil, additions)
    raw = civil.read_bytes()

    # No bare-LF lines should be introduced.
    assert b"\r\n" in raw
    # Specifically: the inserted line should also end in CRLF.
    text = raw.decode()
    age_index = text.index("age:")
    after_age = text[age_index:]
    assert "\r\n" in after_age[: after_age.index("\n") + 1]


# =============================================================================
# Path containment (H3)
# =============================================================================

def test_resolve_specs_dir_rejects_traversal(monkeypatch, tmp_path):
    from check_binds import _resolve_domain_specs_dir
    monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))

    with pytest.raises(ValueError):
        _resolve_domain_specs_dir("..")
    with pytest.raises(ValueError):
        _resolve_domain_specs_dir("../etc")
    with pytest.raises(ValueError):
        _resolve_domain_specs_dir("/etc")
    with pytest.raises(ValueError):
        _resolve_domain_specs_dir("")


def test_resolve_specs_dir_accepts_bare_name(monkeypatch, tmp_path):
    from check_binds import _resolve_domain_specs_dir
    monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
    result = _resolve_domain_specs_dir("snap")
    assert result == (tmp_path / "snap" / "specs").resolve()


# =============================================================================
# Domain-level module-name collision (M4)
# =============================================================================

def test_domain_repairs_raises_on_module_name_collision():
    doc_one = {"module": "shared", "inputs": {"Household": {"fields": {}}}}
    doc_two = {"module": "shared", "inputs": {"Household": {"fields": {}}}}
    with pytest.raises(ValueError) as exc_info:
        compute_domain_repairs({"file_a": doc_one, "file_b": doc_two})
    assert "shared" in str(exc_info.value)


# =============================================================================
# Unit regression — synthetic multi-entity multi-sub case
# =============================================================================
# This test uses hand-crafted dicts to exercise `compute_bind_repairs` against
# the same shape as ah-doh2's medicaid_eligibility (two parent entities,
# overlapping sub-modules). Kept as a fast unit test; the real-fixture
# round-trip lives below under `Regression — ah-doh2 real fixture`.

def test_compute_bind_repairs_multi_entity_multi_sub():
    """Synthetic shape match for ah-doh2 medicaid_eligibility.

    medicaid_eligibility binds Household → ClientData (and Household → AVSData) into
    four sub-modules.
    """
    # Parent declares ClientData/AVSData with only a few fields; we expect the 12 missing.
    parent = {
        "module": "medicaid_eligibility",
        "inputs": {
            "ClientData": {"fields": {"name": {"type": "string", "values": ["A", "B"]}}},
            "AVSData": {"fields": {}},
        },
        "computed": {
            "uie_result": {
                "type": "object",
                "module": "unearned_income_exclusions",
                "invoke": {"bind": {"Household": "ClientData"}},
            },
            "mie_result": {
                "type": "object",
                "module": "medicaid_income_exceptions",
                "invoke": {"bind": {"Household": "ClientData"}},
            },
            "mr_client_result": {
                "type": "object",
                "module": "medicaid_resources",
                "invoke": {"bind": {"Household": "ClientData"}},
            },
            "mr_avs_result": {
                "type": "object",
                "module": "medicaid_resources",
                "invoke": {"bind": {"Household": "AVSData"}},
            },
            "rcc_result": {
                "type": "object",
                "module": "reasonable_compatibility_check",
                "invoke": {"bind": {"Household": "ClientData"}},
            },
        },
    }
    subs = {
        "unearned_income_exclusions": _sub_doc(
            "unearned_income_exclusions", "Household",
            {"gross_unearned_income": {"type": "money"}},
        ),
        "medicaid_income_exceptions": _sub_doc(
            "medicaid_income_exceptions", "Household",
            {
                "gross_earned_income": {"type": "money"},
                "pickle_reduction_factor": {"type": "money"},
            },
        ),
        "medicaid_resources": {
            "module": "medicaid_resources",
            "inputs": {
                "Household": {
                    "fields": {
                        # First bind (ClientData) needs these:
                        "avs_matched_amounts": {"type": "money"},
                        "client_matched_amounts": {"type": "money"},
                        "excess_home_equity_threshold": {"type": "money"},
                        "new_avs_accounts": {"type": "money"},
                        # Second bind (AVSData) needs these — declared on the same
                        # sub-module Household entity in this fixture for simplicity:
                        "alaska_native_real_property_value": {"type": "money"},
                        "client_stated_resources": {"type": "money"},
                        "dingell_act_land_value": {"type": "money"},
                        "home_equity": {"type": "money"},
                        "iim_account_balance": {"type": "money"},
                        "is_institutional": {"type": "bool"},
                    }
                }
            },
        },
        "reasonable_compatibility_check": _sub_doc(
            "reasonable_compatibility_check", "Household",
            {"dol_quarter_total": {"type": "money"}},
        ),
    }

    additions, conflicts = compute_bind_repairs(parent, subs)

    assert conflicts == []
    # Note: medicaid_resources binds to ClientData AND AVSData, so both entities
    # receive the same 10 fields from that sub-module. Plus 1 from unearned, 2 from
    # exceptions, 1 from rcc → all into ClientData. AVSData gets 10 from resources.
    client_data_adds = [a for a in additions if a.parent_entity == "ClientData"]
    avs_data_adds = [a for a in additions if a.parent_entity == "AVSData"]
    client_names = sorted(a.field_name for a in client_data_adds)
    avs_names = sorted(a.field_name for a in avs_data_adds)

    assert "gross_unearned_income" in client_names
    assert "gross_earned_income" in client_names
    assert "pickle_reduction_factor" in client_names
    assert "dol_quarter_total" in client_names
    assert "avs_matched_amounts" in client_names
    assert "excess_home_equity_threshold" in client_names

    assert "alaska_native_real_property_value" in avs_names
    assert "iim_account_balance" in avs_names
    assert "is_institutional" in avs_names


# =============================================================================
# Regression — ah-doh2 real fixture (round-trip against on-disk CIVIL files)
# =============================================================================
# Snapshot under fixtures/ah_doh2_medicaid_repair/: the post-repair state of
# medicaid_eligibility + four sub-modules from the live ah-doh2 domain. The
# round-trip test strips known fields from a tmp copy, runs the pipeline, and
# verifies they are restored — exercising YAML parsing, indent detection, and
# the apply_bind_repairs path the unit tests can't reach.

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ah_doh2_medicaid_repair"


def _load_civil_dir(path: Path) -> dict[str, dict]:
    import yaml as _yaml
    docs: dict[str, dict] = {}
    for yaml_path in sorted(path.glob("*.civil.yaml")):
        with yaml_path.open() as fh:
            docs[yaml_path.name.removesuffix(".civil.yaml")] = _yaml.safe_load(fh) or {}
    return docs


def test_ah_doh2_real_fixture_is_already_consistent():
    """The post-repair snapshot in fixtures/ should report no missing fields.

    Sanity check on the fixture itself — if this fails, the fixture has drifted
    and the round-trip below will be invalid.
    """
    civil_docs = _load_civil_dir(_FIXTURE_DIR)
    diffs = compute_domain_repairs(civil_docs)
    assert diffs == {}, (
        f"fixture should be repair-consistent already; "
        f"compute_domain_repairs returned: {diffs}"
    )


def test_ah_doh2_real_fixture_round_trip(tmp_path):
    """Real-CIVIL round-trip: remove the `field_bind:` override that currently
    masks `gross_unearned_income`, run the full pipeline, and verify
    `apply_bind_repairs` adds the field back to ClientData with the right spec.

    Exercises YAML parsing, indent detection (the real file uses 2-space indents
    + alignment whitespace), the apply-and-rewrite path, and idempotency — none
    of which the unit tests' synthetic-dict fixtures can reach.
    """
    work_dir = tmp_path / "ah_doh2"
    work_dir.mkdir()
    for source_path in _FIXTURE_DIR.glob("*.civil.yaml"):
        (work_dir / source_path.name).write_bytes(source_path.read_bytes())

    parent_path = work_dir / "medicaid_eligibility.civil.yaml"
    parent_text = parent_path.read_text()

    # Surgical removal of the field_bind: block that satisfies gross_unearned_income
    # via earned/unearned classification output. After removal, the sub-module's
    # required Household.gross_unearned_income is no longer field-bind-covered and
    # must come from ClientData.
    field_bind_block = (
        "      field_bind:\n"
        "        Household:\n"
        '          gross_unearned_income: "unearned_class_result.gross_unearned_income"\n'
    )
    assert field_bind_block in parent_text, (
        "Fixture format changed; round-trip surgery target not found. "
        "Update the field_bind_block literal above to match the fixture."
    )
    parent_text = parent_text.replace(field_bind_block, "", 1)
    parent_path.write_text(parent_text)

    diffs = compute_domain_repairs(_load_civil_dir(work_dir))
    assert "medicaid_eligibility" in diffs
    additions, conflicts = diffs["medicaid_eligibility"]
    assert conflicts == []

    addition = next(
        (a for a in additions if a.field_name == "gross_unearned_income"),
        None,
    )
    assert addition is not None, (
        f"expected gross_unearned_income to be needed after stripping field_bind; "
        f"got additions: {[(a.parent_entity, a.field_name) for a in additions]}"
    )
    assert addition.parent_entity == "ClientData"
    assert addition.field_spec.get("type") == "money"
    assert addition.field_spec.get("optional") is True
    assert addition.source_sub_module == "unearned_income_exclusions"

    apply_bind_repairs(parent_path, additions)

    import yaml as _yaml
    repaired = _yaml.safe_load(parent_path.read_text())
    client_data_fields = repaired["inputs"]["ClientData"]["fields"]
    assert "gross_unearned_income" in client_data_fields, (
        "Field should have been appended to ClientData by apply_bind_repairs"
    )
    assert client_data_fields["gross_unearned_income"]["type"] == "money"
    assert client_data_fields["gross_unearned_income"]["optional"] is True

    # The repaired file should re-pass check-binds — proves the round-trip is
    # complete and apply_bind_repairs writes a YAML-valid file that the indent
    # detector and the bind-forwarding gate both accept.
    assert compute_domain_repairs(_load_civil_dir(work_dir)) == {}
