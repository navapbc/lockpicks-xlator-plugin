# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for merge_naming_manifest.py — locks every rename-via-synonyms
branch, preserve-non-null scenarios, section-routing, idempotence,
atomicity, and the inventory schema validator.

Schema is v3.0: provenance lives in an `observations: [{policy_phrase,
source_doc, section}, ...]` list field on each entry. Scalar
policy_phrase / source_doc / section fields are retired."""

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


_V = mnm._MANIFEST_VERSION  # "3.0"


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
    """Build a minimal inventory entry. Fills the required `prior_name`
    key; other fields are passed verbatim through kwargs.

    Convenience: `observations` may be passed via kwargs as a list of dicts.
    """
    base = {
        "name": name,
        "section": section,
        "prior_name": kwargs.pop("prior_name", None),
    }
    base.update(kwargs)
    return base


def _obs(policy_phrase: str | None = None,
         source_doc: str | None = None,
         section: str | None = None) -> dict:
    """Build an observation dict. Omits keys whose value is None."""
    out: dict = {}
    if policy_phrase is not None:
        out["policy_phrase"] = policy_phrase
    if source_doc is not None:
        out["source_doc"] = source_doc
    if section is not None:
        out["section"] = section
    return out


def _fresh_manifest_doc() -> dict:
    return {"version": _V, "inputs": {}, "computed": {}, "outputs": {}}


# ---------------------------------------------------------------------------
# Schema validator unit tests
# ---------------------------------------------------------------------------

def test_validate_top_level_must_be_list():
    with pytest.raises(mnm.InventoryError):
        mnm.validate_inventory({})


def test_validate_name_required():
    with pytest.raises(mnm.InventoryError, match="inventory\\[0\\].name"):
        mnm.validate_inventory([{"section": "computed", "prior_name": None}])


def test_validate_name_must_be_snake_case():
    inv = [_entry("BadName", "computed")]
    with pytest.raises(mnm.InventoryError, match="must be snake_case"):
        mnm.validate_inventory(inv)


def test_validate_section_required():
    inv = [{"name": "x", "prior_name": None}]
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


def test_validate_observations_absent_ok():
    """`observations` is optional on inventory entries — absent is valid."""
    inv = [_entry("x", "computed")]
    mnm.validate_inventory(inv)  # no raise


def test_validate_observations_empty_list_ok():
    inv = [_entry("x", "computed", observations=[])]
    mnm.validate_inventory(inv)  # no raise


def test_validate_observations_must_be_list():
    inv = [_entry("x", "computed", observations="not a list")]
    with pytest.raises(mnm.InventoryError, match="observations"):
        mnm.validate_inventory(inv)


def test_validate_observation_full_triple_ok():
    inv = [_entry(
        "x", "computed",
        observations=[_obs("p", "foo.md", "§1")],
    )]
    mnm.validate_inventory(inv)  # no raise


def test_validate_observation_phrase_absent_source_present_ok():
    """Per relaxed per-observation invariant (R4): source_doc + section
    present with no policy_phrase is a valid observation."""
    inv = [_entry(
        "x", "computed",
        observations=[_obs(None, "foo.md", "§1")],
    )]
    mnm.validate_inventory(inv)  # no raise


def test_validate_observation_phrase_only_no_source_ok():
    """phrase present, no source — also valid (independently optional)."""
    inv = [_entry(
        "x", "computed",
        observations=[_obs("p", None, None)],
    )]
    mnm.validate_inventory(inv)  # no raise


def test_validate_observation_source_without_section_rejected():
    inv = [_entry(
        "x", "computed",
        observations=[_obs("p", "foo.md", None)],
    )]
    with pytest.raises(mnm.InventoryError, match="source_doc and section"):
        mnm.validate_inventory(inv)


def test_validate_observation_section_without_source_rejected():
    inv = [_entry(
        "x", "computed",
        observations=[_obs("p", None, "§1")],
    )]
    with pytest.raises(mnm.InventoryError, match="source_doc and section"):
        mnm.validate_inventory(inv)


def test_validate_observation_empty_string_rejected():
    inv = [_entry(
        "x", "computed",
        observations=[{"policy_phrase": "", "source_doc": "foo.md", "section": "§"}],
    )]
    with pytest.raises(mnm.InventoryError, match="must be non-empty"):
        mnm.validate_inventory(inv)


def test_validate_observation_non_dict_rejected():
    inv = [_entry("x", "computed", observations=["not a dict"])]
    with pytest.raises(mnm.InventoryError, match="must be an object"):
        mnm.validate_inventory(inv)


def test_validate_prior_name_required_key():
    inv = [{"name": "x", "section": "computed"}]
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


def test_validate_v3_manifest_with_catala_native_type_loads(tmp_path: Path):
    """A v3.0 manifest with Catala-native `type:` values still loads cleanly."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        _fresh_manifest_doc(),
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
    inv = [_entry("x", "computed", observed_synonyms=[{"foo": "bar"}])]
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
    """Verify the U7 fields round-trip through the merge tool."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        _fresh_manifest_doc(),
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
    """U7 fields follow the same preserve-non-null rule as other optionals."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {},
            "outputs": {
                "status": {
                    "type": "string",
                    "optional": True,
                    "enum_variants": ["Eligible", "Denied"],
                    "observations": [
                        _obs("eligibility status", "foo.md", "§1"),
                    ],
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
            "version": _V,
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
        _fresh_manifest_doc(),
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
            "version": _V,
            "inputs": {},
            "computed": {
                "gross_income": {
                    "observations": [
                        _obs("gross income", "foo.md", "§1"),
                    ],
                    "description": "analyst-written",
                    "type": "money",
                },
            },
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        # Inventory has no observations and all other fields absent —
        # existing must survive verbatim.
        _entry("gross_income", "computed"),
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["computed"]["gross_income"]
    assert e["observations"] == [
        {"policy_phrase": "gross income", "source_doc": "foo.md", "section": "§1"},
    ]
    assert e["description"] == "analyst-written"
    assert e["type"] == "money"


def test_seeded_entry_gap_fill(tmp_path: Path):
    domain = tmp_path / "dom"
    # Seeded entry: just present with no provenance (matches what
    # /declare-target-ruleset wrote in v2.0; under v3.0 the equivalent is an
    # entry with no `observations:` field — observations arrive from inventory).
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {"gross_income": {}},
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        _entry("gross_income", "computed",
               observations=[_obs("gross income", "foo.md", "§1")],
               description="Total monthly earnings",
               type="money"),
    ])
    assert rc == 0
    assert header["entries_preserved"] == 1
    m = _read_manifest(domain)
    e = m["computed"]["gross_income"]
    assert e["observations"] == [
        {"policy_phrase": "gross income", "source_doc": "foo.md", "section": "§1"},
    ]
    assert e["description"] == "Total monthly earnings"
    assert e["type"] == "money"


def test_observations_list_passed_through_multi_source(tmp_path: Path):
    """Multi-observation list round-trips without dedup or collapse."""
    domain = _empty_manifest(tmp_path)
    rc, header = _run(domain, [
        _entry(
            "gross_income", "computed",
            observations=[
                _obs("gross monthly income", "input/policy_docs/eligibility.md",
                     "# Income Calculation"),
                _obs("gross household income", "input/policy_docs/deductions.md",
                     "## Income Sources"),
                _obs("monthly gross", "input/policy_docs/summary.md",
                     "# Summary"),
            ],
        ),
    ])
    assert rc == 0
    assert header["entries_added"] == 1
    m = _read_manifest(domain)
    obs = m["computed"]["gross_income"]["observations"]
    assert len(obs) == 3
    assert obs[0]["policy_phrase"] == "gross monthly income"
    assert obs[1]["policy_phrase"] == "gross household income"
    assert obs[2]["policy_phrase"] == "monthly gross"


def test_observations_union_on_inventory_add(tmp_path: Path):
    """Existing 3-observation list + inventory's new 4th observation produces
    4-observation merged list (union semantics, existing-first order)."""
    domain = tmp_path / "dom"
    existing_obs = [
        _obs("phrase A", "a.md", "§A"),
        _obs("phrase B", "b.md", "§B"),
        _obs("phrase C", "c.md", "§C"),
    ]
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {
                "gross_income": {"observations": existing_obs},
            },
            "outputs": {},
        },
    )
    # Inventory carries the original 3 + a 4th new observation.
    new_obs = [
        _obs("phrase A", "a.md", "§A"),  # dedup
        _obs("phrase B", "b.md", "§B"),  # dedup
        _obs("phrase D", "d.md", "§D"),  # new
        _obs("phrase C", "c.md", "§C"),  # dedup (out of order)
    ]
    rc, _ = _run(domain, [
        _entry("gross_income", "computed", observations=new_obs),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    obs = m["computed"]["gross_income"]["observations"]
    assert len(obs) == 4
    # Existing-first order preserved:
    assert obs[0]["policy_phrase"] == "phrase A"
    assert obs[1]["policy_phrase"] == "phrase B"
    assert obs[2]["policy_phrase"] == "phrase C"
    assert obs[3]["policy_phrase"] == "phrase D"


def test_observations_union_when_existing_empty(tmp_path: Path):
    """No existing observations + inventory non-empty → inventory's full list
    is taken."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {"gross_income": {}},
            "outputs": {},
        },
    )
    rc, _ = _run(domain, [
        _entry("gross_income", "computed",
               observations=[
                   _obs("phrase 1", "x.md", "§"),
                   _obs("phrase 2", "y.md", "§"),
               ]),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    obs = m["computed"]["gross_income"]["observations"]
    assert [o["policy_phrase"] for o in obs] == ["phrase 1", "phrase 2"]


def test_observations_no_collapse_on_missing_phrase(tmp_path: Path):
    """Observation with source_doc + section but no policy_phrase round-trips
    correctly (per-observation invariant relaxed from all-or-nothing)."""
    domain = _empty_manifest(tmp_path)
    rc, _ = _run(domain, [
        _entry("x", "computed",
               observations=[_obs(None, "policy.md", "§1")]),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    obs = m["computed"]["x"]["observations"]
    assert len(obs) == 1
    assert "policy_phrase" not in obs[0]
    assert obs[0]["source_doc"] == "policy.md"
    assert obs[0]["section"] == "§1"


def test_observations_empty_inventory_omits_field(tmp_path: Path):
    """Inventory with absent or empty observations and no prior observations
    produces an entry with no `observations:` field (synthesized output)."""
    domain = _empty_manifest(tmp_path)
    rc, _ = _run(domain, [
        _entry("eligibility_status", "outputs"),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    e = m["outputs"]["eligibility_status"]
    assert "observations" not in e


def test_observations_tolerates_stale_scalar_keys_silently(tmp_path: Path):
    """Inventory carrying stale scalar policy_phrase / source_doc / section_text
    keys silently drops them; merged manifest has only what `observations:`
    supplies."""
    domain = _empty_manifest(tmp_path)
    # Build a raw entry that includes stale scalars alongside a valid
    # observations list.
    entry = _entry(
        "gross_income", "computed",
        observations=[_obs("gross income", "foo.md", "§1")],
    )
    entry["policy_phrase"] = "stale phrase"
    entry["source_doc"] = "stale.md"
    entry["section_text"] = "stale section"
    rc, _ = _run(domain, [entry])
    assert rc == 0
    m = _read_manifest(domain)
    e = m["computed"]["gross_income"]
    assert e["observations"] == [
        {"policy_phrase": "gross income", "source_doc": "foo.md", "section": "§1"},
    ]
    # Stale scalar keys must not appear on the entry.
    assert "policy_phrase" not in e
    assert "source_doc" not in e
    assert "section" not in e
    assert "section_text" not in e


def test_rename_via_prior_name_happy_path(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {
                "monthly_gross": {
                    "observations": [_obs("monthly gross", "foo.md", "§1")],
                    "description": "...",
                },
            },
            "outputs": {},
        },
    )
    rc, header = _run(domain, [
        _entry("gross_income", "computed",
               prior_name="monthly_gross",
               observations=[_obs("monthly gross", "foo.md", "§1")]),
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
    # Synonyms are {name}-only under v3.0:
    assert new["synonyms"] == [{"name": "monthly_gross"}]


def test_rename_idempotent_on_rerun(tmp_path: Path):
    """Re-running the tool with the same inventory must produce byte-identical
    output on the second run."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V,
            "inputs": {},
            "computed": {
                "monthly_gross": {
                    "observations": [_obs("monthly gross", "foo.md", "§1")],
                },
            },
            "outputs": {},
        },
    )
    inventory = [
        _entry("gross_income", "computed",
               prior_name="monthly_gross",
               observations=[_obs("monthly gross", "foo.md", "§1")]),
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
            "version": _V, "inputs": {},
            "computed": {
                "original_name": {
                    "observations": [_obs("phrase", "foo.md", "§1")],
                },
            },
            "outputs": {},
        },
    )

    # Round 1: original_name → intermediate_name
    rc, _ = _run(domain, [
        _entry("intermediate_name", "computed",
               prior_name="original_name",
               observations=[_obs("phrase", "foo.md", "§1")]),
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
               observations=[_obs("phrase", "foo.md", "§1")]),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    assert "intermediate_name" not in m["computed"]
    assert m["computed"]["final_name"]["synonyms"] == [
        {"name": "original_name"},
        {"name": "intermediate_name"},
    ]


def test_observed_synonyms_append(tmp_path: Path):
    """Observed synonyms collapse to {name}-only under v3.0 — per-synonym
    source_doc / section are no longer carried."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V, "inputs": {},
            "computed": {
                "gross_income": {
                    "observations": [_obs("p", "foo.md", "§")],
                },
            },
            "outputs": {},
        },
    )
    inv = [
        _entry("gross_income", "computed",
               observed_synonyms=[{"name": "wages"}]),
    ]
    rc, header = _run(domain, inv)
    assert rc == 0
    assert header["synonyms_appended_observed"] == 1
    m = _read_manifest(domain)
    syns = m["computed"]["gross_income"]["synonyms"]
    assert syns == [{"name": "wages"}]

    # Re-run: no duplicate.
    rc, header = _run(domain, inv)
    assert rc == 0
    assert header["synonyms_appended_observed"] == 0
    m = _read_manifest(domain)
    assert m["computed"]["gross_income"]["synonyms"] == [{"name": "wages"}]


def test_observed_synonyms_drop_legacy_provenance_keys(tmp_path: Path):
    """Inventory observed_synonyms carrying stale source_doc / section keys
    are accepted (the validator only requires `name`); the merge tool drops
    those legacy keys, producing {name}-only synonyms on output."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V, "inputs": {},
            "computed": {
                "gross_income": {
                    "observations": [_obs("p", "foo.md", "§")],
                },
            },
            "outputs": {},
        },
    )
    inv = [
        _entry("gross_income", "computed",
               observed_synonyms=[{"name": "wages",
                                    "source_doc": "bar.md",
                                    "section": "§2"}]),
    ]
    rc, _ = _run(domain, inv)
    assert rc == 0
    m = _read_manifest(domain)
    syns = m["computed"]["gross_income"]["synonyms"]
    assert syns == [{"name": "wages"}]
    assert "source_doc" not in syns[0]
    assert "section" not in syns[0]


def test_observed_and_rename_anchor_coexist(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V, "inputs": {},
            "computed": {
                "old_canonical": {
                    "observations": [_obs("p", "foo.md", "§")],
                },
            },
            "outputs": {},
        },
    )
    rc, _ = _run(domain, [
        _entry("new_canonical", "computed",
               prior_name="old_canonical",
               observations=[_obs("p", "foo.md", "§")],
               observed_synonyms=[{"name": "wages"}]),
    ])
    assert rc == 0
    m = _read_manifest(domain)
    syns = m["computed"]["new_canonical"]["synonyms"]
    assert syns[0] == {"name": "old_canonical"}
    assert syns[1] == {"name": "wages"}


def test_is_rename_anchor_returns_true_for_name_only_synonym():
    """Post-3.0, every {name}-shaped synonym answers True; the distinction
    against observed-phrasing synonyms collapsed."""
    assert mnm._is_rename_anchor({"name": "old"}) is True


def test_is_rename_anchor_returns_false_for_malformed_input():
    assert mnm._is_rename_anchor({"foo": "bar"}) is False
    assert mnm._is_rename_anchor("not a dict") is False


def test_cross_section_move(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V, "inputs": {},
            "computed": {
                "x": {"observations": [_obs("p", "foo.md", "§")]},
            },
            "outputs": {},
        },
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
        {
            "version": _V, "inputs": {},
            "computed": {
                "new": {"observations": [_obs("p1", "a.md", "§a")]},
                "phantom": {"observations": [_obs("p2", "b.md", "§b")]},
            },
            "outputs": {},
        },
    )
    rc, _ = _run(domain, [
        _entry("new", "computed", prior_name="phantom"),
    ])
    assert rc == 1


def test_preserve_unmentioned_flag_kept_off_by_default(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {
            "version": _V, "inputs": {},
            "computed": {
                "old1": {"observations": [_obs("p", "a.md", "§")]},
                "old2": {"observations": [_obs("p", "b.md", "§")]},
            },
            "outputs": {},
        },
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
        {
            "version": _V, "inputs": {},
            "computed": {
                "old1": {"observations": [_obs("p", "a.md", "§")]},
                "old2": {"observations": [_obs("p", "b.md", "§")]},
            },
            "outputs": {},
        },
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
        {
            "version": _V, "inputs": {},
            "computed": {
                "old": {"observations": [_obs("p", "a.md", "§")]},
                "untouched": {"observations": [_obs("p", "b.md", "§")]},
            },
            "outputs": {},
        },
    )
    rc, _ = _run(domain, [
        _entry("new", "computed", prior_name="old",
               observations=[_obs("p", "a.md", "§")]),
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
        _fresh_manifest_doc(),
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
        _fresh_manifest_doc(),
    )
    rc, _ = _run(domain, [_entry("a", "computed")])
    assert rc == 0
    m = _read_manifest(domain)
    assert m["version"] == _V
    assert isinstance(m["version"], str)


def test_version_rejection_includes_regenerate_instructions(tmp_path: Path):
    """A v2.0 manifest is rejected with exit code 1 and the regenerate
    instructions in the stderr error message."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"version": "2.0", "inputs": {}, "computed": {}, "outputs": {}},
    )
    inv_path = _write_inventory(tmp_path, [_entry("a", "computed")])
    import io
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    real_stderr = sys.stderr
    real_stdout = sys.stdout
    sys.stderr = err_buf
    sys.stdout = out_buf
    try:
        rc = mnm.run(domain, "prog", inv_path, False, False)
    finally:
        sys.stderr = real_stderr
        sys.stdout = real_stdout
    assert rc == 1
    err_text = err_buf.getvalue()
    assert "version" in err_text
    assert "3.0" in err_text
    assert "observations" in err_text
    assert "/index-inputs" in err_text
    assert "/suggest-target-ruleset" in err_text
    assert "/declare-target-ruleset" in err_text


def test_version_rejection_when_absent(tmp_path: Path):
    """A manifest with no `version:` field is rejected with exit code 1."""
    domain = tmp_path / "dom"
    _write_yaml(
        domain / "specs" / "naming-manifest.yaml",
        {"inputs": {}, "computed": {}, "outputs": {}},
    )
    inv_path = _write_inventory(tmp_path, [_entry("a", "computed")])
    import io
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    real_stderr = sys.stderr
    real_stdout = sys.stdout
    sys.stderr = err_buf
    sys.stdout = out_buf
    try:
        rc = mnm.run(domain, "prog", inv_path, False, False)
    finally:
        sys.stderr = real_stderr
        sys.stdout = real_stdout
    assert rc == 1
    err_text = err_buf.getvalue()
    assert "no version field" in err_text or "version" in err_text
    assert "3.0" in err_text


def test_version_rejection_for_numeric_version(tmp_path: Path):
    """A manifest with `version: 1.0` (numeric, not string) is rejected."""
    domain = tmp_path / "dom"
    text = "version: 1.0\ninputs: {}\ncomputed: {}\noutputs: {}\n"
    (domain / "specs" / "naming-manifest.yaml").parent.mkdir(parents=True)
    (domain / "specs" / "naming-manifest.yaml").write_text(text)
    inv_path = _write_inventory(tmp_path, [_entry("a", "computed")])
    import io
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    real_stderr = sys.stderr
    real_stdout = sys.stdout
    sys.stderr = err_buf
    sys.stdout = out_buf
    try:
        rc = mnm.run(domain, "prog", inv_path, False, False)
    finally:
        sys.stderr = real_stderr
        sys.stdout = real_stdout
    assert rc == 1
    err_text = err_buf.getvalue()
    assert "3.0" in err_text


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
    assert m["version"] == _V


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
        {
            "version": _V, "inputs": {},
            "computed": {
                "existing": {"observations": [_obs("p", "a.md", "§")]},
            },
            "outputs": {},
        },
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
