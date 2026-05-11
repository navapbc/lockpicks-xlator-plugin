# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for validate_guidance.py — covers U8 alignment validator."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import validate_guidance  # noqa: E402


def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True)
    return domain


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def test_clean_alignment_returns_ok():
    """Manifest has gross_income; output-variables.yaml references it; ok."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {
                "Household": {
                    "gross_income": {"policy_phrase": "gross monthly income", "type": "money"},
                },
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "primary": {"name_ref": "gross_income", "description": "Total income."},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["missing"] == []


def test_typo_in_name_ref_reported():
    """Manifest has gross_income; guidance has gross_inocme (typo). FAIL."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {
                "Household": {
                    "gross_income": {"policy_phrase": "gross income", "type": "money"},
                },
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "primary": {"name_ref": "gross_inocme", "description": "typo"},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert len(summary["missing"]) == 1
        assert summary["missing"][0]["name_ref"] == "gross_inocme"
        assert "output-variables.yaml" in summary["missing"][0]["file"]


def test_orphan_in_manifest_warned_but_not_fatal():
    """Manifest has unused_var; no guidance references it. Listed as orphan
    but ok=True (orphans are non-fatal at v1)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "computed": {
                "unused_var": {"policy_phrase": "unused", "type": "string"},
            },
        })
        # No guidance files referencing it.
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True  # orphans are non-fatal
        assert "unused_var" in summary["orphans"]


def test_missing_manifest_fails():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        # No manifest file.
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert any("not found" in e for e in summary["errors"])


def test_missing_guidance_files_not_errors():
    """Missing output-variables.yaml etc. is not an error at v1."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {"Household": {"gross_income": {"policy_phrase": "", "type": "money"}}},
        })
        # No guidance files at all.
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True


def test_empty_manifest_is_valid():
    """Manifest with empty blocks + no guidance refs → valid."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["orphans"] == []


def test_include_with_output_flat_list():
    """include-with-output.yaml as a flat list of name strings."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "computed": {
                "intermediate_a": {"policy_phrase": "a", "type": "money"},
                "intermediate_b": {"policy_phrase": "b", "type": "money"},
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "include-with-output.yaml",
                    ["intermediate_a", "intermediate_b"])
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True


def test_include_with_output_typo_caught():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "computed": {"intermediate_a": {"policy_phrase": "a", "type": "money"}},
        })
        _write_yaml(domain / "specs" / "guidance" / "include-with-output.yaml",
                    ["intermediate_xyz"])  # typo
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert any(m["name_ref"] == "intermediate_xyz" for m in summary["missing"])


def test_input_variables_with_name_refs():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {
                "Applicant": {
                    "applicant_age": {"policy_phrase": "age", "type": "int"},
                },
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "input-variables.yaml", {
            "categories": [
                {
                    "category": "demographics",
                    "fields": [{"name_ref": "applicant_age", "description": "age"}],
                },
            ],
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True


def test_type_agreement_ok_when_matching():
    """Guidance type matches manifest type → OK."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "outputs": {
                "eligibility": {"policy_phrase": "eligible", "type": "bool"},
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "eligibility": {"name_ref": "eligibility", "type": "bool", "primary": True},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["mismatches"] == []


def test_type_mismatch_caught():
    """Guidance type 'string' contradicts manifest type 'bool' → FAIL."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "outputs": {
                "eligibility": {"policy_phrase": "eligible", "type": "bool"},
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "eligibility": {"name_ref": "eligibility", "type": "string", "primary": True},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert len(summary["mismatches"]) == 1
        mm = summary["mismatches"][0]
        assert mm["name_ref"] == "eligibility"
        assert mm["field"] == "type"
        assert mm["guidance"] == "string"
        assert mm["manifest"] == "bool"


def test_type_absent_on_guidance_is_not_mismatch():
    """Guidance entry without type: is allowed (manifest is authority)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "outputs": {
                "eligibility": {"policy_phrase": "eligible", "type": "bool"},
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "eligibility": {"name_ref": "eligibility", "primary": True},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["mismatches"] == []


def test_values_mismatch_caught():
    """Guidance values disagree with manifest values → FAIL."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "outputs": {
                "decision": {
                    "policy_phrase": "decision",
                    "type": "enum",
                    "values": ["approve", "deny"],
                },
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "output-variables.yaml", {
            "decision": {
                "name_ref": "decision",
                "type": "enum",
                "values": ["approve", "deny", "manual"],
                "primary": True,
            },
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert any(m["field"] == "values" for m in summary["mismatches"])


def test_constants_and_tables_ok_when_provenance_present():
    """Every entry has source_file and source_section → OK."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {},
        })
        _write_yaml(domain / "specs" / "guidance" / "constants-and-tables.yaml", {
            "constants_and_tables": [
                {
                    "name": "income_limits",
                    "description": "Income thresholds.",
                    "source_file": "input/policy_docs/manual/income.md",
                    "source_section": "1.2 Income Limits",
                },
            ],
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["missing_fields"] == []


def test_constants_and_tables_missing_source_file_caught():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {},
        })
        _write_yaml(domain / "specs" / "guidance" / "constants-and-tables.yaml", {
            "constants_and_tables": [
                {
                    "name": "income_limits",
                    "description": "Income thresholds.",
                    "source_section": "1.2 Income Limits",
                },
            ],
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert len(summary["missing_fields"]) == 1
        mf = summary["missing_fields"][0]
        assert mf["entry"] == "income_limits"
        assert mf["field"] == "source_file"


def test_constants_and_tables_empty_string_treated_as_missing():
    """An empty source_section: '' counts as missing."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {},
        })
        _write_yaml(domain / "specs" / "guidance" / "constants-and-tables.yaml", {
            "constants_and_tables": [
                {
                    "name": "income_limits",
                    "description": "Income thresholds.",
                    "source_file": "input/policy_docs/manual/income.md",
                    "source_section": "   ",
                },
            ],
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert any(mf["field"] == "source_section" for mf in summary["missing_fields"])


def test_constants_and_tables_missing_file_not_an_error():
    """When constants-and-tables.yaml does not exist, validation skips it
    (consistent with other guidance files)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {},
            "computed": {},
            "outputs": {},
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is True
        assert summary["missing_fields"] == []


def test_input_variables_type_mismatch_caught():
    """input-variables.yaml field type disagrees with manifest → FAIL."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_yaml(domain / "specs" / "naming-manifest.yaml", {
            "version": "1.0",
            "inputs": {
                "Applicant": {
                    "applicant_age": {"policy_phrase": "age", "type": "int"},
                },
            },
        })
        _write_yaml(domain / "specs" / "guidance" / "input-variables.yaml", {
            "categories": [
                {
                    "category": "demographics",
                    "fields": [{"name_ref": "applicant_age", "type": "string"}],
                },
            ],
        })
        summary = validate_guidance.cmd_validate(domain)
        assert summary["ok"] is False
        assert len(summary["mismatches"]) == 1
        assert summary["mismatches"][0]["field"] == "type"


def main() -> int:
    failed = 0
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
            else:
                passed += 1
                print(f"ok {name}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
