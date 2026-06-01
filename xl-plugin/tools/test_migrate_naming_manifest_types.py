"""Tests for migrate-naming-manifest-types.py (one-shot script).

This test file is deleted alongside the script in U2 of the
2026-06-01-002 plan.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


_SCRIPT_REL = "migrate-naming-manifest-types.py"
_SCRIPT_PATH = Path(__file__).parent / _SCRIPT_REL


def _load_module():
    """Load the kebab-cased script as a module under a snake-case alias."""
    spec = importlib.util.spec_from_file_location(
        "migrate_naming_manifest_types", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_module()


def _write_manifest(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


def _read_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _make_legacy_manifest() -> dict:
    return {
        "version": "1.0",
        "inputs": {
            "Household": {
                "size": {"type": "int", "description": "Number of people"},
                "is_eligible_unit": {"type": "bool"},
                "income": {"type": "float"},
                "name": {"type": "str"},
                "tags": {"type": "set"},
                "address": {"type": "object"},
            }
        },
        "computed": {
            "total": {"type": "int"},
            "deduction": {"type": "money"},  # pass-through
        },
        "outputs": {
            "is_eligible": {"type": "bool"},
            "issued_on": {"type": "date"},   # pass-through
        },
    }


# ---------------------------------------------------------------------------
# happy-path: every legacy name maps per R8; pass-throughs unchanged; version bumped
# ---------------------------------------------------------------------------

def test_full_legacy_manifest_migrates(tmp_path: Path):
    """AE2: bool->boolean, int->integer, float->decimal, str->string,
    set->list, object->structure. money/date/enum/list pass through.
    `version: '1.0'` -> `version: '2.0'`."""
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, _make_legacy_manifest())

    rc = mig.run("snap", False, False, domains_root)
    assert rc == 0

    out = _read_manifest(manifest_path)
    assert out["version"] == "2.0"
    h = out["inputs"]["Household"]
    assert h["size"]["type"] == "integer"
    assert h["is_eligible_unit"]["type"] == "boolean"
    assert h["income"]["type"] == "decimal"
    assert h["name"]["type"] == "string"
    assert h["tags"]["type"] == "list"
    assert h["address"]["type"] == "structure"
    assert out["computed"]["total"]["type"] == "integer"
    assert out["computed"]["deduction"]["type"] == "money"  # unchanged
    assert out["outputs"]["is_eligible"]["type"] == "boolean"
    assert out["outputs"]["issued_on"]["type"] == "date"     # unchanged


def test_catala_native_passthrough(tmp_path: Path):
    """Already-Catala-native values are unchanged. Version still bumps."""
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, {
        "version": "1.0",
        "inputs": {
            "Household": {
                "a": {"type": "integer"},
                "b": {"type": "decimal"},
                "c": {"type": "boolean"},
                "d": {"type": "duration"},
                "e": {"type": "string"},
                "f": {"type": "structure"},
            }
        },
        "computed": {},
        "outputs": {"x": {"type": "enum"}, "y": {"type": "list"}},
    })

    rc = mig.run("snap", False, False, domains_root)
    assert rc == 0

    out = _read_manifest(manifest_path)
    assert out["version"] == "2.0"
    h = out["inputs"]["Household"]
    for k, expected in [("a", "integer"), ("b", "decimal"),
                        ("c", "boolean"), ("d", "duration"),
                        ("e", "string"), ("f", "structure")]:
        assert h[k]["type"] == expected
    assert out["outputs"]["x"]["type"] == "enum"
    assert out["outputs"]["y"]["type"] == "list"


# ---------------------------------------------------------------------------
# idempotency: re-run on a fully Catala-native v2.0 manifest is a no-op
# ---------------------------------------------------------------------------

def test_idempotent_rerun(tmp_path: Path):
    """A second run on an already-migrated manifest rewrites 0 entries and
    does not bump the version (already 2.0)."""
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, _make_legacy_manifest())

    assert mig.run("snap", False, False, domains_root) == 0
    before = manifest_path.read_text(encoding="utf-8")
    mtime_before = manifest_path.stat().st_mtime_ns

    # Second run: no changes; no rewrite of file content.
    assert mig.run("snap", False, False, domains_root) == 0
    after = manifest_path.read_text(encoding="utf-8")
    assert before == after
    # File should not be rewritten when nothing changes.
    assert manifest_path.stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------------------
# --all enumerates only existing manifests
# ---------------------------------------------------------------------------

def test_all_skips_domains_without_manifest(tmp_path: Path):
    """--all globs domains/*/specs/naming-manifest.yaml; domains without a
    manifest are silently skipped (Key Technical Decision in the plan)."""
    domains_root = tmp_path
    # One domain with a manifest, two without.
    _write_manifest(
        domains_root / "snap" / "specs" / "naming-manifest.yaml",
        _make_legacy_manifest(),
    )
    (domains_root / "dl").mkdir()
    (domains_root / "ak_doh" / "specs").mkdir(parents=True)

    rc = mig.run(None, True, False, domains_root)
    assert rc == 0

    snap = _read_manifest(domains_root / "snap" / "specs" / "naming-manifest.yaml")
    assert snap["version"] == "2.0"
    assert snap["inputs"]["Household"]["size"]["type"] == "integer"


# ---------------------------------------------------------------------------
# manifest with no type: fields at all -> version bumps, 0 rewrites
# ---------------------------------------------------------------------------

def test_manifest_with_no_type_fields(tmp_path: Path):
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, {
        "version": "1.0",
        "inputs": {"Household": {"size": {"description": "x"}}},
        "computed": {},
        "outputs": {},
    })
    assert mig.run("snap", False, False, domains_root) == 0
    out = _read_manifest(manifest_path)
    assert out["version"] == "2.0"
    assert "type" not in out["inputs"]["Household"]["size"]


# ---------------------------------------------------------------------------
# --check-only does not write
# ---------------------------------------------------------------------------

def test_check_only_does_not_write(tmp_path: Path):
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, _make_legacy_manifest())
    before = manifest_path.read_text(encoding="utf-8")

    rc = mig.run("snap", False, True, domains_root)
    assert rc == 0

    after = manifest_path.read_text(encoding="utf-8")
    assert before == after  # unchanged


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------

def test_unknown_type_value_raises(tmp_path: Path, capsys):
    """An unrecognized `type:` (neither Catala-native nor legacy) exits 1
    with a clear error naming the entry path. No partial write occurs."""
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, {
        "version": "1.0",
        "inputs": {"Household": {"weird": {"type": "frobnicate"}}},
        "computed": {},
        "outputs": {},
    })
    before = manifest_path.read_text(encoding="utf-8")

    rc = mig.run("snap", False, False, domains_root)
    assert rc == 1
    err = capsys.readouterr().err
    assert "frobnicate" in err
    assert "inputs.Household.weird.type" in err

    # No partial write.
    assert manifest_path.read_text(encoding="utf-8") == before


def test_explicit_domain_without_manifest_exits_2(tmp_path: Path, capsys):
    """`migrate <domain>` against a domain with no specs/naming-manifest.yaml
    exits 2 with a "manifest not found" error."""
    domains_root = tmp_path
    (domains_root / "snap").mkdir()

    rc = mig.run("snap", False, False, domains_root)
    assert rc == 2
    err = capsys.readouterr().err
    assert "manifest not found" in err


def test_missing_domain_dir_exits_2(tmp_path: Path, capsys):
    rc = mig.run("nonexistent", False, False, tmp_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "domain directory not found" in err


# ---------------------------------------------------------------------------
# CLI end-to-end: spawn the script with DOMAINS_FULLPATH set, parse header
# ---------------------------------------------------------------------------

def test_cli_end_to_end(tmp_path: Path):
    domains_root = tmp_path
    manifest_path = domains_root / "snap" / "specs" / "naming-manifest.yaml"
    _write_manifest(manifest_path, _make_legacy_manifest())

    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(domains_root)

    result = subprocess.run(
        ["uv", "run", str(_SCRIPT_PATH), "snap"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    lines = result.stdout.strip().splitlines()
    header = json.loads(lines[0])
    assert lines[1] == "--- MIGRATE-NAMING-MANIFEST-TYPES-HEADER-END ---"
    assert header["files_processed"] == 1
    assert header["entries_rewritten"] == 8  # 6 inputs + 1 computed + 1 output
    assert header["version_bumped"] is True

    out = _read_manifest(manifest_path)
    assert out["version"] == "2.0"


def test_cli_rejects_both_domain_and_all(tmp_path: Path):
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    result = subprocess.run(
        ["uv", "run", str(_SCRIPT_PATH), "snap", "--all"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not both" in result.stderr


def test_cli_rejects_neither(tmp_path: Path):
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    result = subprocess.run(
        ["uv", "run", str(_SCRIPT_PATH)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
