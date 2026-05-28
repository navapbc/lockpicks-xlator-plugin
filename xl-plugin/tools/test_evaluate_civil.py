# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for evaluate_civil.py CLI wrapper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

THIS_DIR = Path(__file__).resolve().parent
SCRIPT = THIS_DIR / "evaluate_civil.py"


def _make_domain(tmp: Path, civil_doc: dict, *, program: str = "elig") -> Path:
    domain = tmp / "test_dom"
    specs = domain / "specs"
    specs.mkdir(parents=True)
    with (specs / f"{program}.civil.yaml").open("w") as f:
        yaml.safe_dump(civil_doc, f, sort_keys=False)
    return domain


def _run(domain_dir: Path, program: str, inputs_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DOMAINS_FULLPATH": str(domain_dir.parent)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), domain_dir.name, program, "--inputs", str(inputs_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_outputs_json():
    civil_doc = {
        "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
        "rules": [
            {
                "id": "R",
                "kind": "deny",
                "priority": 1,
                "when": "x > 100",
                "then": [{"add_reason": {"code": "TOO_HIGH"}}],
            }
        ],
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text(json.dumps({"x": 50}))

        result = _run(domain, "elig", inputs_path)

    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    parsed = json.loads(result.stdout)
    assert parsed["outputs"]["eligible"] is True
    assert parsed["outputs"]["reasons"] == []
    assert parsed["debug"]["rules_fired"] == []


def test_happy_path_rule_fires():
    civil_doc = {
        "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
        "rules": [
            {
                "id": "R",
                "kind": "deny",
                "priority": 1,
                "when": "x > 100",
                "then": [{"add_reason": {"code": "TOO_HIGH"}}],
            }
        ],
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text(json.dumps({"x": 500}))

        result = _run(domain, "elig", inputs_path)

    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed["outputs"]["eligible"] is False
    assert parsed["debug"]["rules_fired"] == ["R"]


# ---------------------------------------------------------------------------
# Pre-flight failures (exit 2)
# ---------------------------------------------------------------------------


def test_missing_domain_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("{}")
        env = {**os.environ, "DOMAINS_FULLPATH": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "nope", "elig", "--inputs", str(inputs_path)],
            env=env, capture_output=True, text=True, check=False,
        )
    assert result.returncode == 2
    assert "domain directory not found" in result.stderr


def test_missing_civil_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = tmp_path / "test_dom"
        (domain / "specs").mkdir(parents=True)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("{}")
        result = _run(domain, "elig", inputs_path)
    assert result.returncode == 2
    assert "CIVIL file not found" in result.stderr


def test_missing_inputs_exits_2():
    civil_doc = {"inputs": {}, "outputs": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        result = _run(domain, "elig", tmp_path / "nope.json")
    assert result.returncode == 2
    assert "inputs file not found" in result.stderr


# ---------------------------------------------------------------------------
# Evaluation errors (exit 1)
# ---------------------------------------------------------------------------


def test_missing_required_input_exits_1():
    civil_doc = {
        "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
        "outputs": {"result": {"type": "int", "expr": "x"}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("{}")
        result = _run(domain, "elig", inputs_path)
    assert result.returncode == 1
    assert "Evaluation error" in result.stderr
    assert "missing required" in result.stderr


def test_invalid_json_inputs_exits_1():
    civil_doc = {"inputs": {}, "outputs": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("not json at all")
        result = _run(domain, "elig", inputs_path)
    assert result.returncode == 1
    assert "JSON parse error" in result.stderr
