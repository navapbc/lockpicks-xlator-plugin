# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for merge_naming_manifest.py — locks every rename-via-synonyms
branch, preserve-non-null scenarios, section-routing, idempotence,
atomicity, and the inventory schema validator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

import merge_naming_manifest as mnm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _write_inventory(tmp: Path, entries: list[dict]) -> Path:
    p = tmp / "inv.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


def _read_manifest(domain_dir: Path) -> dict:
    p = domain_dir / "specs" / "naming-manifest.yaml"
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _run(domain_dir: Path, inventory: list[dict], **kwargs) -> tuple[int, dict | None]:
    """Invoke `mnm.run` and return (rc, parsed_header_or_None)."""
    inv_path = _write_inventory(domain_dir.parent, inventory)
    program = kwargs.get("program", "prog")
    check_only = kwargs.get("check_only", False)
    preserve = kwargs.get("preserve_unmentioned", False)
    import io
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        rc = mnm.run(domain_dir, program, inv_path, check_only, preserve)
    finally:
        sys.stdout = real_stdout
    out = buf.getvalue()
    header = None
    if out:
        # First line is JSON header.
        first = out.split("\n", 1)[0]
        try:
            header = json.loads(first)
        except json.JSONDecodeError:
            header = None
    return rc, header


def _entry(name: str, section: str, **kwargs) -> dict:
    """Build a minimal inventory entry. Fills all required keys; overrides
    via kwargs."""
    base = {
        "name": name,
        "section": section,
        "policy_phrase": kwargs.pop("policy_phrase", None),
        "source_doc": kwargs.pop("source_doc", None),
        "section_text": kwargs.pop("section_text", None),
        "prior_name": kwargs.pop("prior_name", None),
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Schema validator unit tests
# ---------------------------------------------------------------------------

def test_validate_top_level_must_be_list():
    with pytest.raises(mnm.InventoryError):
        mnm.validate_inventory({})


def test_validate_name_required():
    with pytest.raises(mnm.InventoryError, match="inventory\\[0\\].name"):
        mnm.validate_inventory([{"section": "computed", "policy_phrase": None,
                                  "source_doc": None, "section_text": None,
                                  "prior_name": None}])


def test_validate_name_must_be_snake_case():
    inv = [_entry("BadName", "computed")]
    with pytest.raises(mnm.InventoryError, match="must be snake_case"):
        mnm.validate_inventory(inv)


def test_validate_section_required():
    inv = [{"name": "x", "policy_phrase": None, "source_doc": None,
            "section_text": None, "prior_name": None}]
    with pytest.raises(mnm.InventoryError, match="section"):
        mnm.validate_inventory(inv)


def test_validate_section_must_match_pattern():
    inv = [_entry("x", "invalid_section")]
    with pytest.raises(mnm.InventoryError, match="must be one of"):
        mnm.validate_inventory(inv)


def test_validate_inputs_must_be_camelcase():
    inv = [_entry("x", "inputs.household")]
    with pytest.raises(mnm.InventoryError):
        mnm.validate_inventory(inv)


def test_validate_provenance_all_or_nothing_pp_only():
    inv = [_entry("x", "computed", policy_phrase="p")]
    with pytest.raises(mnm.InventoryError, match="all-or-nothing"):
        mnm.validate_inventory(inv)


def test_validate_provenance_all_or_nothing_sd_only():
    inv = [_entry("x", "computed", source_doc="foo.md")]
    with pytest.raises(mnm.InventoryError, match="all-or-nothing"):
        mnm.validate_inventory(inv)


def test_validate_provenance_all_set_ok():
    inv = [_entry("x", "computed",
                   policy_phrase="p", source_doc="foo.md", section_text="§1")]
    mnm.validate_inventory(inv)  # no raise


def test_validate_provenance_all_null_ok():
    inv = [_entry("x", "computed")]
    mnm.validate_inventory(inv)


def test_validate_prior_name_required_key():
    inv = [{"name": "x", "section": "computed",
            "policy_phrase": None, "source_doc": None, "section_text": None}]
    with pytest.raises(mnm.InventoryError, match="prior_name"):
        mnm.validate_inventory(inv)


def test_validate_prior_name_cannot_equal_name():
    inv = [_entry("x", "computed", prior_name="x")]
    with pytest.raises(mnm.InventoryError, match="must differ from name"):
        mnm.validate_inventory(inv)


def test_validate_type_enum_without_values_is_allowed():
    """Existing real-world manifests declare `type: enum` without `values:`.
    The merge tool permits this; enumeration is advisory, not load-bearing."""
    inv = [_entry("x", "computed", type="enum")]
    mnm.validate_inventory(inv)  # no raise


def test_validate_values_invalid_list_entry_rejected():
    inv = [_entry("x", "computed", type="enum", values=[""])]
    with pytest.raises(mnm.InventoryError, match="values\\[0\\]"):
        mnm.validate_inventory(inv)


def test_validate_type_invalid():
    inv = [_entry("x", "computed", type="weird")]
    with pytest.raises(mnm.InventoryError, match="must be one of"):
        mnm.validate_inventory(inv)


# ---------------------------------------------------------------------------
# Strict Catala-native vocabulary (plan 2026-06-01-002)
# ---------------------------------------------------------------------------

_CATALA_NATIVE_10 = (
    "integer", "decimal", "money", "boolean", "date",
    "duration", "string", "enum", "list", "structure",
)
_LEGACY_CIVIL_REJECTED = ("bool", "int", "float", "str", "set", "object")


@pytest.mark.parametrize("t", _CATALA_NATIVE_10)
def test_validate_catala_native_types_accepted(t):
    """All 10 Catala-native names are accepted (R1)."""
    inv = [_entry("x", "computed", type=t)]
    mnm.validate_inventory(inv)  # no raise


@pytest.mark.parametrize("t", _LEGACY_CIVIL_REJECTED)
def test_validate_legacy_civil_types_rejected(t):
    """Legacy CIVIL names are rejected — no deprecated-alias layer (R3)."""
    inv = [_entry("x", "computed", type=t)]
    with pytest.raises(mnm.InventoryError, match="must be one of"):
        mnm.validate_inventory(inv)


def test_validate_type_error_message_lists_catala_native_vocab():
    """The rejection error names the 10 Catala-native types verbatim (R2)."""
    inv = [_entry("x", "computed", type="weird")]
    with pytest.raises(mnm.InventoryError) as excinfo:
        mnm.validate_inventory(inv)
    msg = str(excinfo.value)
    for t in _CATALA_NATIVE_10:
        assert repr(t) in msg, f"expected {t!r} in error message, got: {msg}"


def test_validate_existing_v1_version_string_still_loads(tmp_path: Path):
    """The validator does not gate on `version:` (R6). A '1.0' string on a
    manifest with Catala-native `type:` values still loads cleanly."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    rc, _ = _run(
        domain, [_entry("x", "computed", type="integer")],
    )
    assert rc == 0


def test_validate_type_absent_or_null_accepted():
    """`type:` is optional — absent or null entries pass (regression guard)."""
    # Absent
    inv = [_entry("x", "computed")]
    mnm.validate_inventory(inv)
    # Explicit null
    inv = [_entry("x", "computed", type=None)]
    mnm.validate_inventory(inv)


def test_validate_observed_synonyms_each_needs_name():
    inv = [_entry("x", "computed",
                   observed_synonyms=[{"source_doc": "foo.md", "section": "§"}])]
    with pytest.raises(mnm.InventoryError, match="name"):
        mnm.validate_inventory(inv)


# ---------------------------------------------------------------------------
# `optional:` + `enum_variants:` schema fields
# ---------------------------------------------------------------------------

def test_validate_optional_must_be_bool():
    inv = [_entry("x", "computed", optional="yes")]
    with pytest.raises(mnm.InventoryError, match="optional"):
        mnm.validate_inventory(inv)


def test_validate_optional_bool_ok():
    for v in (True, False, None):
        inv = [_entry("x", "computed", optional=v)]
        mnm.validate_inventory(inv)  # no raise


def test_validate_enum_variants_must_be_list():
    inv = [_entry("x", "computed", enum_variants="Eligible")]
    with pytest.raises(mnm.InventoryError, match="enum_variants"):
        mnm.validate_inventory(inv)


def test_validate_enum_variants_each_must_be_non_empty_str():
    inv = [_entry("x", "computed", enum_variants=["Eligible", ""])]
    with pytest.raises(mnm.InventoryError, match="enum_variants\\[1\\]"):
        mnm.validate_inventory(inv)


def test_validate_enum_variants_valid_list_ok():
    inv = [_entry("x", "computed", enum_variants=["Eligible", "Denied"])]
    mnm.validate_inventory(inv)  # no raise


def test_merge_writes_optional_and_enum_variants(tmp_path: Path):
    """Verify the new U7 fields round-trip through the merge tool."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    rc, header = _run(domain, [
        _entry("status", "outputs",
                type="string",
                optional=False,
                enum_variants=["Eligible", "Denied", "ManualVerification"]),
    ])
    assert rc == 0
    assert header["entries_added"] == 1
    m = _read_manifest(domain)
    e = m["outputs"]["status"]
    assert e["type"] == "string"
    assert e["optional"] is False
    assert e["enum_variants"] == ["Eligible", "Denied", "ManualVerification"]


def test_merge_preserves_non_null_for_optional_and_enum_variants(tmp_path: Path):
    """U7 fields follow the same preserve-non-null rule as policy_phrase / source_doc / section."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {
                "status": {
                    "type": "string",
                    "optional": True,
                    "enum_variants": ["Eligible", "Denied"],
                    "policy_phrase": "eligibility status",
                    "source_doc": "foo.md",
                    "section": "§1",
                },
            },
        },
    )
    # Inventory leaves the U7 fields null → existing wins.
    rc, header = _run(domain, [
        _entry("status", "outputs"),
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["outputs"]["status"]
    assert e["optional"] is True
    assert e["enum_variants"] == ["Eligible", "Denied"]


def test_merge_seeded_entry_gap_fill_for_u7_fields(tmp_path: Path):
    """Seeded entry with no type metadata picks up types when inventory provides them."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {"status": {}},  # seeded with no metadata
        },
    )
    rc, header = _run(domain, [
        _entry("status", "outputs",
                type="boolean",
                optional=False,
                enum_variants=None),  # not an enum
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["outputs"]["status"]
    assert e["type"] == "boolean"
    assert e["optional"] is False
    # enum_variants stays absent
    assert "enum_variants" not in e


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def test_parse_section_each_form():
    assert mnm._parse_section("computed") == ("computed", None)
    assert mnm._parse_section("outputs") == ("outputs", None)
    assert mnm._parse_section("inputs.Household") == ("inputs", "Household")


def test_lookup_existing_inputs_3level():
    m = {"inputs": {"Household": {"size": {"x": 1}}}}
    assert mnm._lookup_existing(m, "inputs", "Household", "size") == {"x": 1}
    assert mnm._lookup_existing(m, "inputs", "Household", "missing") is None
    assert mnm._lookup_existing(m, "inputs", "Other", "size") is None


def test_find_name_anywhere():
    m = {
        "inputs": {"Household": {"x": {}}, "Applicant": {"y": {}}},
        "computed": {"z": {}},
        "outputs": {"x": {}},  # name collision
    }
    # First match wins; entity-grouped is searched first.
    assert mnm._find_name_anywhere(m, "y") == ("inputs", "Applicant")
    assert mnm._find_name_anywhere(m, "z") == ("computed", None)
    assert mnm._find_name_anywhere(m, "missing") is None


# ---------------------------------------------------------------------------
# Merge branch tests — these are the load-bearing scenarios
# ---------------------------------------------------------------------------

def _empty_manifest(tmp_path: Path) -> Path:
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    return domain


def test_append_only_no_existing(tmp_path: Path):
    domain = _empty_manifest(tmp_path)
    rc, header = _run(domain, [
        _entry("a", "computed"),
        _entry("b", "computed"),
        _entry("c", "outputs"),
    ])
    assert rc == 0
    assert header["entries_added"] == 3
    assert header["entries_renamed"] == 0
    assert header["entries_preserved"] == 0
    m = _read_manifest(domain)
    assert set(m["computed"].keys()) == {"a", "b"}
    assert set(m["outputs"].keys()) == {"c"}


def test_match_by_name_preserve_non_null(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {
                "gross_income": {
                    "policy_phrase": "gross income",
                    "source_doc": "foo.md",
                    "section": "§1",
                    "description": "analyst-written",
                    "type": "money",
                },
            },
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        # Inventory has all null/absent — existing must survive verbatim.
        _entry("gross_income", "computed"),
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["computed"]["gross_income"]
    assert e["policy_phrase"] == "gross income"
    assert e["source_doc"] == "foo.md"
    assert e["section"] == "§1"
    assert e["description"] == "analyst-written"
    assert e["type"] == "money"


def test_seeded_entry_gap_fill(tmp_path: Path):
    domain = tmp_path / "dom"
    # Seeded entry: just present with no provenance (matches what
    # /declare-target-ruleset writes).
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {"gross_income": {}},
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        _entry("gross_income", "computed",
                policy_phrase="gross income",
                source_doc="foo.md",
                section_text="§1",
                description="Total monthly earnings",
                type="money"),
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["computed"]["gross_income"]
    assert e["policy_phrase"] == "gross income"
    assert e["source_doc"] == "foo.md"
    assert e["section"] == "§1"
    assert e["description"] == "Total monthly earnings"
    assert e["type"] == "money"


def test_rename_via_prior_name_happy_path(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {
                "monthly_gross": {
                    "policy_phrase": "monthly gross",
                    "source_doc": "foo.md",
                    "section": "§1",
                    "description": "...",
                },
            },
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        _entry("gross_income", "computed",
                prior_name="monthly_gross",
                policy_phrase="monthly gross",
                source_doc="foo.md",
                section_text="§1"),
    ])
    assert rc == 0
    assert header["entries_renamed"] == 1
    assert header["synonyms_appended_rename_anchor"] == 1
    m = _read_manifest(domain)
    # Old key dropped:
    assert "monthly_gross" not in m["computed"]
    # New key present:
    new = m["computed"]["gross_income"]
    assert new["description"] == "..."  # carried forward
    assert new["synonyms"] == [{"name": "monthly_gross"}]
    # rename-anchor synonyms must have no source_doc / section:
    assert "source_doc" not in new["synonyms"][0]
    assert "section" not in new["synonyms"][0]


def test_rename_idempotent_on_rerun(tmp_path: Path):
    """Re-running the tool with the same inventory must produce byte-identical
    output on the second run."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {},
            "computed": {
                "monthly_gross": {
                    "policy_phrase": "monthly gross",
                    "source_doc": "foo.md",
                    "section": "§1",
                },
            },
            "outputs": {},
        },
    )
    inventory = [
        _entry("gross_income", "computed",
                prior_name="monthly_gross",
                policy_phrase="monthly gross",
                source_doc="foo.md",
                section_text="§1"),
    ]
    rc, _ = _run(domain, inventory)
    assert rc == 0
    first_bytes = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    # Second run: prior_name doesn't exist anymore; the rename anchor IS in
    # synonyms; tool should NOT append a duplicate.
    rc, header = _run(domain, inventory)
    assert rc == 0
    second_bytes = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    assert first_bytes == second_bytes


def test_rename_chain_accumulates(tmp_path: Path):
    """original_name → intermediate_name → final_name; both prior names
    accumulate as rename-anchor synonyms."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0", "inputs": {},
            "computed": {
                "original_name": {
                    "policy_phrase": "phrase",
                    "source_doc": "foo.md",
                    "section": "§1",
                },
            },
            "outputs": {},
        },
    )

    # Round 1: original_name → intermediate_name
    rc, _ = _run(domain, [
        _entry("intermediate_name", "computed",
                prior_name="original_name",
                policy_phrase="phrase",
                source_doc="foo.md",
                section_text="§1"),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    assert m["computed"]["intermediate_name"]["synonyms"] == [
        {"name": "original_name"},
    ]

    # Round 2: intermediate_name → final_name (with carry-forward)
    rc, _ = _run(domain, [
        _entry("final_name", "computed",
                prior_name="intermediate_name",
                policy_phrase="phrase",
                source_doc="foo.md",
                section_text="§1"),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    assert "intermediate_name" not in m["computed"]
    assert m["computed"]["final_name"]["synonyms"] == [
        {"name": "original_name"},
        {"name": "intermediate_name"},
    ]


def test_observed_synonyms_append(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {"gross_income": {"policy_phrase": "p",
                                        "source_doc": "foo.md",
                                        "section": "§"}},
         "outputs": {}},
    )
    inv = [
        _entry("gross_income", "computed",
                observed_synonyms=[{"name": "wages",
                                     "source_doc": "bar.md",
                                     "section": "§2"}]),
    ]
    rc, header = _run(domain, inv)
    assert rc == 0
    assert header["synonyms_appended_observed"] == 1
    m = _read_manifest(domain)
    syns = m["computed"]["gross_income"]["synonyms"]
    assert syns == [{"name": "wages", "source_doc": "bar.md", "section": "§2"}]

    # Re-run: no duplicate.
    rc, header = _run(domain, inv)
    assert rc == 0
    assert header["synonyms_appended_observed"] == 0
    m = _read_manifest(domain)
    assert m["computed"]["gross_income"]["synonyms"] == [
        {"name": "wages", "source_doc": "bar.md", "section": "§2"}
    ]


def test_observed_and_rename_anchor_coexist(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {"old_canonical": {"policy_phrase": "p",
                                          "source_doc": "foo.md",
                                          "section": "§"}},
         "outputs": {}},
    )
    rc, _ = _run(domain, [
        _entry("new_canonical", "computed",
                prior_name="old_canonical",
                policy_phrase="p", source_doc="foo.md", section_text="§",
                observed_synonyms=[{"name": "wages",
                                     "source_doc": "bar.md",
                                     "section": "§2"}]),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    syns = m["computed"]["new_canonical"]["synonyms"]
    assert syns[0] == {"name": "old_canonical"}
    assert syns[1] == {"name": "wages", "source_doc": "bar.md", "section": "§2"}


def test_cross_section_move(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {"x": {"policy_phrase": "p",
                              "source_doc": "foo.md",
                              "section": "§"}},
         "outputs": {}},
    )
    rc, header = _run(domain, [
        _entry("x", "outputs"),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    assert "x" not in m["computed"]
    assert "x" in m["outputs"]
    # Warning surfaced:
    assert any("moved" in w for w in header["warnings"])


def test_prior_name_stale_no_existing_either(tmp_path: Path):
    """inventory.prior_name references a non-existent entry, and the new
    name doesn't exist either — APPEND + warn."""
    domain = _empty_manifest(tmp_path)
    rc, header = _run(domain, [
        _entry("new_name", "computed", prior_name="phantom"),
    ])
    assert rc == 0
    assert header["entries_added"] == 1
    assert any("phantom" in w for w in header["warnings"])
    m = _read_manifest(domain)
    assert "new_name" in m["computed"]
    # No rename-anchor synonym since phantom didn't exist:
    assert "synonyms" not in m["computed"]["new_name"]


def test_pathological_both_name_and_prior_name_exist(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {
             "new": {"policy_phrase": "p1", "source_doc": "a.md", "section": "§a"},
             "phantom": {"policy_phrase": "p2", "source_doc": "b.md", "section": "§b"},
         },
         "outputs": {}},
    )
    rc, _ = _run(domain, [
        _entry("new", "computed", prior_name="phantom"),
    ])
    assert rc == 1


def test_preserve_unmentioned_flag_kept_off_by_default(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {
             "old1": {"policy_phrase": "p", "source_doc": "a.md", "section": "§"},
             "old2": {"policy_phrase": "p", "source_doc": "b.md", "section": "§"},
         },
         "outputs": {}},
    )
    rc, _ = _run(domain, [_entry("new", "computed")])
    assert rc == 0
    m = _read_manifest(domain)
    assert "old1" not in m["computed"]
    assert "old2" not in m["computed"]
    assert "new" in m["computed"]


def test_preserve_unmentioned_flag_kept_on(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {
             "old1": {"policy_phrase": "p", "source_doc": "a.md", "section": "§"},
             "old2": {"policy_phrase": "p", "source_doc": "b.md", "section": "§"},
         },
         "outputs": {}},
    )
    rc, _ = _run(domain, [_entry("new", "computed")], preserve_unmentioned=True)
    assert rc == 0
    m = _read_manifest(domain)
    assert "old1" in m["computed"]
    assert "old2" in m["computed"]
    assert "new" in m["computed"]


def test_preserve_unmentioned_drops_rename_source(tmp_path: Path):
    """Renames still drop the prior entry even under --preserve-unmentioned —
    the flag only affects entries with NO inventory reference at all."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {
             "old": {"policy_phrase": "p", "source_doc": "a.md", "section": "§"},
             "untouched": {"policy_phrase": "p", "source_doc": "b.md", "section": "§"},
         },
         "outputs": {}},
    )
    rc, _ = _run(domain, [
        _entry("new", "computed", prior_name="old",
                policy_phrase="p", source_doc="a.md", section_text="§"),
    ], preserve_unmentioned=True)
    assert rc == 0
    m = _read_manifest(domain)
    assert "old" not in m["computed"]  # dropped by rename
    assert "untouched" in m["computed"]  # preserved by flag
    assert "new" in m["computed"]
    assert m["computed"]["new"]["synonyms"] == [{"name": "old"}]


def test_check_only_does_not_write(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    before = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    rc, header = _run(domain, [_entry("a", "computed")], check_only=True)
    assert rc == 0
    assert header["mode"] == "check_only"
    after = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    assert before == after


def test_inputs_3level_routing(tmp_path: Path):
    domain = _empty_manifest(tmp_path)
    rc, _ = _run(domain, [
        _entry("age", "inputs.Applicant"),
        _entry("size", "inputs.Household"),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    assert "age" in m["inputs"]["Applicant"]
    assert "size" in m["inputs"]["Household"]


def test_output_ordering(tmp_path: Path):
    """Top-level: version, inputs, computed, outputs.
    inputs: entities alpha; fields within entity alpha.
    computed/outputs: alpha by name."""
    domain = _empty_manifest(tmp_path)
    rc, _ = _run(domain, [
        _entry("z_field", "inputs.Zebra"),
        _entry("a_field", "inputs.Antelope"),
        _entry("z_comp", "computed"),
        _entry("a_comp", "computed"),
        _entry("z_out", "outputs"),
        _entry("a_out", "outputs"),
    ])
    assert rc == 0
    # Read raw text to assert key ORDER (not just presence).
    text = (domain / "specs" / "naming-manifest.yaml").read_text()
    # Top-level order:
    assert text.index("version:") < text.index("inputs:")
    assert text.index("inputs:") < text.index("computed:")
    assert text.index("computed:") < text.index("outputs:")
    # Entity alpha order:
    assert text.index("Antelope:") < text.index("Zebra:")
    # Computed alpha:
    assert text.index("a_comp:") < text.index("z_comp:")
    # Outputs alpha:
    assert text.index("a_out:") < text.index("z_out:")


def test_version_preserved(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    rc, _ = _run(domain, [_entry("a", "computed")])
    assert rc == 0
    m = _read_manifest(domain)
    assert m["version"] == "1.0"
    assert isinstance(m["version"], str)


def test_version_numeric_coerces_with_warning(tmp_path: Path):
    domain = tmp_path / "dom"
    # Write `version: 1.0` (number, not string).
    text = "version: 1.0\ninputs: {}\ncomputed: {}\noutputs: {}\n"
    (domain / "specs" / "naming-manifest.yaml").parent.mkdir(parents=True)
    (domain / "specs" / "naming-manifest.yaml").write_text(text)
    rc, header = _run(domain, [_entry("a", "computed")])
    assert rc == 0
    m = _read_manifest(domain)
    assert m["version"] == mnm._MANIFEST_VERSION
    assert isinstance(m["version"], str)
    assert any("version" in w for w in header["warnings"])


def test_version_initialized_when_absent(tmp_path: Path):
    """No existing manifest at all → tool initializes with the default
    version constant (_MANIFEST_VERSION)."""
    domain = tmp_path / "dom"
    domain.mkdir()
    (domain / "specs").mkdir()
    # No naming-manifest.yaml.
    rc, _ = _run(domain, [_entry("a", "computed")])
    assert rc == 0
    m = _read_manifest(domain)
    assert m["version"] == mnm._MANIFEST_VERSION


def test_role_hint_never_written(tmp_path: Path):
    domain = _empty_manifest(tmp_path)
    # Inventory carries role_hint accidentally (not in schema) — must be ignored.
    inv_entry = _entry("a", "computed")
    inv_entry["role_hint"] = "decision"  # extra field — ignored
    rc, _ = _run(domain, [inv_entry])
    assert rc == 0
    text = (domain / "specs" / "naming-manifest.yaml").read_text()
    assert "role_hint" not in text


def test_atomicity_failure_leaves_prior_intact(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "1.0", "inputs": {},
         "computed": {"existing": {"policy_phrase": "p",
                                     "source_doc": "a.md",
                                     "section": "§"}},
         "outputs": {}},
    )
    before = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    with mock.patch(
        "merge_naming_manifest.os.replace",
        side_effect=OSError("disk full"),
    ):
        rc, _ = _run(domain, [_entry("a", "computed")])
    assert rc == 1
    after = (domain / "specs" / "naming-manifest.yaml").read_bytes()
    assert before == after


def test_stdout_shape(tmp_path: Path, capsys):
    domain = _empty_manifest(tmp_path)
    inv_path = _write_inventory(tmp_path, [_entry("a", "computed")])
    rc = mnm.run(domain, "prog", inv_path, False, False)
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.split("\n")
    header = json.loads(lines[0])
    assert header["mode"] == "write"
    assert lines[1] == mnm._HEADER_SENTINEL
    assert "Wrote specs/naming-manifest.yaml." in out


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def test_preflight_domain_missing(tmp_path: Path):
    rc, _ = _run(tmp_path / "missing_dom", [_entry("a", "computed")])
    assert rc == 2


def test_preflight_inventory_missing(tmp_path: Path):
    domain = _empty_manifest(tmp_path)
    rc = mnm.run(domain, "prog", tmp_path / "no.json", False, False)
    assert rc == 2


def test_preflight_inventory_malformed_json(tmp_path: Path):
    domain = _empty_manifest(tmp_path)
    inv = tmp_path / "bad.json"
    inv.write_text("{not json", encoding="utf-8")
    rc = mnm.run(domain, "prog", inv, False, False)
    assert rc == 1


# ---------------------------------------------------------------------------
# Subprocess integration: full main() + argparse
# ---------------------------------------------------------------------------

def test_subprocess_happy_path():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _empty_manifest(tmp_path)
        inv = tmp_path / "inv.json"
        inv.write_text(json.dumps([_entry("a", "computed")]), encoding="utf-8")

        script = Path(__file__).parent / "merge_naming_manifest.py"
        env = os.environ.copy()
        env["DOMAINS_FULLPATH"] = str(tmp_path)
        proc = subprocess.run(
            ["uv", "run", str(script), "dom", "prog", "--inventory", str(inv)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        first_line = proc.stdout.split("\n", 1)[0]
        header = json.loads(first_line)
        assert header["entries_added"] == 1
