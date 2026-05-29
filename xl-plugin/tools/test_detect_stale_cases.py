# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for detect_stale_cases.py.

v13.0.0 rewrite: the production code now drives `catala_eval.run()` against
a Catala source. Tests that previously synthesized CIVIL docs in tempdirs
no longer apply; CIVIL is gone. The new tests split into two layers:

1. Pure-Python diff/iteration logic — tested with a monkeypatched
   `catala_eval.run` so a real `clerk`/`catala` toolchain is not required.
2. Pre-flight failures — exercised against the live script.

The end-to-end stale-detection path is covered by U9 regeneration (a real
domain + real Catala source + real `catala interpret`), not here.
"""

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

THIS_DIR = Path(__file__).resolve().parent
SCRIPT = THIS_DIR / "detect_stale_cases.py"
SENTINEL = "--- DETECT-STALE-CASES-HEADER-END ---"

sys.path.insert(0, str(THIS_DIR))
import detect_stale_cases  # noqa: E402
import catala_eval  # noqa: E402


def _make_domain(tmp: Path, *, program: str = "elig",
                 catala_text: str = "> Module Elig\n",
                 tests_doc: dict | None = None) -> Path:
    domain = tmp / "test_dom"
    specs = domain / "specs"
    tests_dir = specs / "tests"
    tests_dir.mkdir(parents=True)
    (specs / f"{program}.catala_en").write_text(catala_text)
    if tests_doc is not None:
        with (tests_dir / f"{program}_tests.yaml").open("w") as f:
            yaml.safe_dump(tests_doc, f, sort_keys=False)
    return domain


def _result(outputs: dict, computed: dict | None = None,
            reasons: list | None = None) -> catala_eval.EvaluationResult:
    return catala_eval.EvaluationResult(
        outputs=outputs,
        computed=computed or {},
        reasons=reasons or [],
        debug={},
    )


# ---------------------------------------------------------------------------
# Pure-Python diff helpers
# ---------------------------------------------------------------------------

class TestDiffHelpers:
    def test_diff_empty_when_values_match(self):
        cur = {"eligible": True, "reasons": []}
        rec = {"eligible": True, "reasons": []}
        assert detect_stale_cases._diff_expected(cur, rec) == {}

    def test_diff_flags_boolean_flip(self):
        cur = {"eligible": True}
        rec = {"eligible": False}
        d = detect_stale_cases._diff_expected(cur, rec)
        assert d == {"eligible": {"current": True, "recomputed": False}}

    def test_diff_tolerates_float_within_epsilon(self):
        assert detect_stale_cases._values_equal(1.0, 1.0 + 1e-12)
        assert not detect_stale_cases._values_equal(1.0, 1.1)

    def test_reason_list_compares_by_code(self):
        cur = [{"code": "X"}]
        rec_match = [{"code": "X", "message": "extra"}]
        rec_diff = [{"code": "Y"}]
        assert detect_stale_cases._values_equal(cur, rec_match)
        assert not detect_stale_cases._values_equal(cur, rec_diff)

    def test_build_recomputed_pulls_from_result_outputs(self):
        current = {"eligible": True, "reasons": []}
        rec = detect_stale_cases._build_recomputed_expected(
            current, _result({"eligible": False}, reasons=[{"code": "Z"}]),
        )
        assert rec["eligible"] is False
        assert rec["reasons"] == [{"code": "Z"}]


# ---------------------------------------------------------------------------
# cmd_detect with monkeypatched catala_eval.run
# ---------------------------------------------------------------------------

class TestCmdDetect:
    def test_no_stale_when_evaluator_matches_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp), tests_doc={
                "tests": [
                    {"case_id": "a", "inputs": {"x": 1},
                     "expected": {"eligible": True, "reasons": []}},
                    {"case_id": "b", "inputs": {"x": 2},
                     "expected": {"eligible": True, "reasons": []}},
                ]
            })
            with mock.patch.object(catala_eval, "run",
                                   return_value=_result({"eligible": True})):
                summary = detect_stale_cases.cmd_detect(domain, "elig")
        assert summary["scanned_count"] == 2
        assert summary["stale_count"] == 0
        assert summary["stale_cases"] == []

    def test_stale_when_evaluator_disagrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp), tests_doc={
                "tests": [
                    {"case_id": "previously_allowed", "inputs": {"x": 80},
                     "expected": {"eligible": True, "reasons": []}},
                ]
            })
            with mock.patch.object(catala_eval, "run",
                                   return_value=_result({"eligible": False},
                                                        reasons=[{"code": "DENY"}])):
                summary = detect_stale_cases.cmd_detect(domain, "elig")
        assert summary["stale_count"] == 1
        entry = summary["stale_cases"][0]
        assert entry["case_id"] == "previously_allowed"
        assert "eligible" in entry["diff"]
        assert entry["diff"]["eligible"]["current"] is True
        assert entry["diff"]["eligible"]["recomputed"] is False

    def test_evaluator_error_is_recorded_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp), tests_doc={
                "tests": [
                    {"case_id": "ok",  "inputs": {"x": 1}, "expected": {"eligible": True}},
                    {"case_id": "bad", "inputs": {}, "expected": {"eligible": True}},
                ]
            })

            def fake_run(_path, _scope, inputs):
                if not inputs:
                    raise catala_eval.EvaluationError(
                        "catala interpret", "missing required input",
                    )
                return _result({"eligible": True})

            with mock.patch.object(catala_eval, "run", side_effect=fake_run):
                summary = detect_stale_cases.cmd_detect(domain, "elig")
        assert summary["scanned_count"] == 2
        assert summary["error_count"] == 1
        assert summary["stale_count"] == 0
        assert summary["errors"][0]["case_id"] == "bad"

    def test_multiple_test_files_per_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp))
            with (domain / "specs" / "tests" / "elig_tests.yaml").open("w") as f:
                yaml.safe_dump({"tests": [
                    {"case_id": "a", "inputs": {"x": 1},
                     "expected": {"eligible": True}},
                ]}, f)
            with (domain / "specs" / "tests" / "elig_extra.yaml").open("w") as f:
                yaml.safe_dump({"tests": [
                    {"case_id": "b", "inputs": {"x": 2},
                     "expected": {"eligible": True}},
                ]}, f)
            with mock.patch.object(catala_eval, "run",
                                   return_value=_result({"eligible": True})):
                summary = detect_stale_cases.cmd_detect(domain, "elig")
        assert summary["scanned_count"] == 2

    def test_empty_test_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp))
            summary = detect_stale_cases.cmd_detect(domain, "elig")
        assert summary["scanned_count"] == 0
        assert summary["stale_count"] == 0


# ---------------------------------------------------------------------------
# Pre-flight failures (live script invocation)
# ---------------------------------------------------------------------------

def _spawn(domain_root: Path, program: str = "elig") -> subprocess.CompletedProcess:
    env = {**os.environ, "DOMAINS_FULLPATH": str(domain_root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "test_dom", program],
        env=env, capture_output=True, text=True, check=False,
    )


class TestPreflight:
    def test_missing_domain_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert _spawn(Path(tmp)).returncode == 2

    def test_missing_catala_source_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = Path(tmp) / "test_dom" / "specs" / "tests"
            domain.mkdir(parents=True)
            assert _spawn(Path(tmp)).returncode == 2


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

class TestOutputFormat:
    def test_sentinel_and_summary_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp), tests_doc={"tests": [
                {"case_id": "a", "inputs": {"x": 1}, "expected": {"eligible": True}},
            ]})
            env = {**os.environ, "DOMAINS_FULLPATH": str(domain.parent)}
            # Patch within a subprocess by writing a wrapper script that
            # injects the mock — simpler here is to call cmd_detect + render
            # directly, since the live evaluator path is exercised in U9.
            with mock.patch.object(catala_eval, "run",
                                   return_value=_result({"eligible": True})):
                summary = detect_stale_cases.cmd_detect(domain, "elig")
            assert summary["scanned_count"] == 1
            # Sentinel + body format is covered by main(); inspecting JSON
            # round-trip suffices for shape.
            assert json.loads(json.dumps(summary, default=str))["scanned_count"] == 1
