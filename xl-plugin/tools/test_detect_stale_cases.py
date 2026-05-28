# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for detect_stale_cases.py CLI."""

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
SCRIPT = THIS_DIR / "detect_stale_cases.py"


def _make_domain(tmp: Path, civil_doc: dict, tests_doc: dict | None = None,
                 *, program: str = "elig") -> Path:
    domain = tmp / "test_dom"
    specs = domain / "specs"
    tests_dir = specs / "tests"
    tests_dir.mkdir(parents=True)
    with (specs / f"{program}.civil.yaml").open("w") as f:
        yaml.safe_dump(civil_doc, f, sort_keys=False)
    if tests_doc is not None:
        with (tests_dir / f"{program}_tests.yaml").open("w") as f:
            yaml.safe_dump(tests_doc, f, sort_keys=False)
    return domain


def _run(domain_dir: Path, program: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DOMAINS_FULLPATH": str(domain_dir.parent)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), domain_dir.name, program],
        env=env, capture_output=True, text=True, check=False,
    )


def _parse_header(stdout: str) -> dict:
    """Extract the JSON header from the stdout — everything before the sentinel."""
    sentinel = "--- DETECT-STALE-CASES-HEADER-END ---"
    head, _, _body = stdout.partition(sentinel)
    return json.loads(head.strip())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _doc_with_threshold(threshold: int) -> dict:
    return {
        "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
        "rules": [
            {
                "id": "R",
                "kind": "deny",
                "priority": 1,
                "when": f"x > {threshold}",
                "then": [{"add_reason": {"code": "TOO_HIGH"}}],
            }
        ],
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_three_cases_no_stale():
    civil_doc = _doc_with_threshold(100)
    tests_doc = {
        "tests": [
            {"case_id": "low",  "inputs": {"x": 50},  "expected": {"eligible": True, "reasons": []}},
            {"case_id": "mid",  "inputs": {"x": 99},  "expected": {"eligible": True, "reasons": []}},
            {"case_id": "high", "inputs": {"x": 200}, "expected": {"eligible": False, "reasons": [{"code": "TOO_HIGH"}]}},
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc, tests_doc)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["scanned_count"] == 3
    assert header["stale_count"] == 0
    assert header["stale_cases"] == []


def test_boundary_change_detected():
    """Threshold was lowered to 60; an 'allow' case at x=80 is now stale (deny)."""
    civil_doc = _doc_with_threshold(60)
    tests_doc = {
        "tests": [
            {
                "case_id": "previously_allowed",
                "inputs": {"x": 80},
                "expected": {"eligible": True, "reasons": []},
            },
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc, tests_doc)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["scanned_count"] == 1
    assert header["stale_count"] == 1
    stale = header["stale_cases"][0]
    assert stale["case_id"] == "previously_allowed"
    assert "eligible" in stale["diff"]
    assert stale["diff"]["eligible"]["current"] is True
    assert stale["diff"]["eligible"]["recomputed"] is False


def test_logic_only_change_detected():
    """The case the existing /create-tests heuristic misses: operator change
    from > to >=, no values changed."""
    # Original CIVIL: x > 100 (deny). New CIVIL: x >= 100 (deny). The boundary
    # value x=100 used to allow but now denies.
    civil_doc = {
        "inputs": {"H": {"fields": {"x": {"type": "int"}}}},
        "rules": [
            {
                "id": "R",
                "kind": "deny",
                "priority": 1,
                "when": "x >= 100",  # operator change
                "then": [{"add_reason": {"code": "TOO_HIGH"}}],
            }
        ],
        "outputs": {
            "eligible": {"type": "bool", "default": False, "expr": "count(reasons) == 0"},
            "reasons": {"type": "list", "item": "Reason", "default": []},
        },
    }
    tests_doc = {
        "tests": [
            {"case_id": "boundary_100", "inputs": {"x": 100}, "expected": {"eligible": True, "reasons": []}},
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc, tests_doc)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["stale_count"] == 1
    assert header["stale_cases"][0]["case_id"] == "boundary_100"


def test_evaluation_error_recorded_not_fatal():
    """Test case with inputs missing a required field is recorded in errors,
    not stale_cases — and the overall exit code is still 0."""
    civil_doc = _doc_with_threshold(100)
    tests_doc = {
        "tests": [
            {"case_id": "good", "inputs": {"x": 50}, "expected": {"eligible": True, "reasons": []}},
            {"case_id": "missing", "inputs": {}, "expected": {"eligible": True, "reasons": []}},
        ]
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc, tests_doc)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["scanned_count"] == 2
    assert header["error_count"] == 1
    assert header["stale_count"] == 0
    assert header["errors"][0]["case_id"] == "missing"


def test_multiple_test_files_for_same_program():
    civil_doc = _doc_with_threshold(100)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        domain = _make_domain(tmp_path, civil_doc)
        # First file
        with (domain / "specs" / "tests" / "elig_tests.yaml").open("w") as f:
            yaml.safe_dump({
                "tests": [{"case_id": "a", "inputs": {"x": 50},
                           "expected": {"eligible": True, "reasons": []}}]
            }, f)
        # Second file
        with (domain / "specs" / "tests" / "elig_extra.yaml").open("w") as f:
            yaml.safe_dump({
                "tests": [{"case_id": "b", "inputs": {"x": 200},
                           "expected": {"eligible": False, "reasons": [{"code": "TOO_HIGH"}]}}]
            }, f)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["scanned_count"] == 2


def test_empty_test_corpus():
    civil_doc = _doc_with_threshold(100)
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc)
        result = _run(domain, "elig")

    assert result.returncode == 0
    header = _parse_header(result.stdout)
    assert header["scanned_count"] == 0
    assert header["stale_count"] == 0


# ---------------------------------------------------------------------------
# Pre-flight failures
# ---------------------------------------------------------------------------


def test_missing_domain_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "DOMAINS_FULLPATH": tmp}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "nope", "elig"],
            env=env, capture_output=True, text=True, check=False,
        )
    assert result.returncode == 2


def test_missing_civil_file_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "test_dom"
        (domain / "specs" / "tests").mkdir(parents=True)
        env = {**os.environ, "DOMAINS_FULLPATH": tmp}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "test_dom", "elig"],
            env=env, capture_output=True, text=True, check=False,
        )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Body format
# ---------------------------------------------------------------------------


def test_body_contains_sentinel_and_summary():
    civil_doc = _doc_with_threshold(100)
    tests_doc = {"tests": [{"case_id": "a", "inputs": {"x": 50},
                            "expected": {"eligible": True, "reasons": []}}]}
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp), civil_doc, tests_doc)
        result = _run(domain, "elig")

    assert "--- DETECT-STALE-CASES-HEADER-END ---" in result.stdout
    assert "Scanned 1 test case(s)" in result.stdout
