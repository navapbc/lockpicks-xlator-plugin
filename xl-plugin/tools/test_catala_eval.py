# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Tests for catala_eval.py — U3 Catala-backed evaluator wrapper.

Covers the test scenarios enumerated in U3 of the plan:

- Happy path (library API)
- Edge case: scope-not-found → EvaluationError with scope name
- Edge case: missing input field → EvaluationError identifying the field
- Edge case: canonicalization stability (byte-identical as_dict() across runs)
- Edge case: multi-rule scope routing each rule's outcome into the trace
- CLI happy path: JSON shape on stdout, exit 0
- CLI preflight: missing inputs path → exit 2; missing source → exit 2
- Pure unit coverage: derive scope, canonicalization, ANSI stripping
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import catala_eval  # noqa: E402
from catala_eval import (  # noqa: E402
    EvaluationError,
    EvaluationResult,
    _canonicalize,
    _derive_scope_from_module,
    _parse_scope_result,
    _strip_ansi,
    run,
)


# ---------------------------------------------------------------------------
# Test fixture discovery — reuse the PA3 synthetic eligibility module
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PA3_FIXTURE_DIR = (
    _REPO_ROOT / "xl-plugin" / "core" / "tests" / "fixtures" / "synthetic_eligibility"
)
_PA3_MODULE = _PA3_FIXTURE_DIR / "eligibility.catala_en"


def _have_catala() -> bool:
    return shutil.which("catala") is not None


def _have_clerk() -> bool:
    return shutil.which("clerk") is not None


_requires_catala = pytest.mark.skipif(
    not _have_catala(), reason="catala not on PATH"
)
_requires_clerk = pytest.mark.skipif(
    not _have_clerk(), reason="clerk not on PATH"
)


def _copy_fixture_to(tmp_path: Path, dest_name: str = "eligibility.catala_en") -> Path:
    """Copy the PA3 fixture into `tmp_path` and bootstrap clerk so the
    Catala stdlib is linked. Mirrors test_clerk_loop._copy_fixture_to."""
    dest = tmp_path / dest_name
    shutil.copy2(_PA3_MODULE, dest)
    if _have_clerk():
        subprocess.run(
            ["clerk", "start"], cwd=str(tmp_path),
            capture_output=True, text=True, check=False,
        )
    return dest


# ---------------------------------------------------------------------------
# Pure unit tests (no external tooling required)
# ---------------------------------------------------------------------------


class TestDeriveScope:
    def test_simple_lowercase(self):
        assert _derive_scope_from_module("eligibility") == "Eligibility"

    def test_underscored(self):
        assert _derive_scope_from_module("apa_adltc") == "ApaAdltc"

    def test_dashed(self):
        assert _derive_scope_from_module("foo-bar-baz") == "FooBarBaz"

    def test_already_camel(self):
        # capitalize() is destructive on existing camelcase; the module
        # convention is snake_case so this is the expected behavior.
        assert _derive_scope_from_module("CamelCase") == "Camelcase"


class TestCanonicalize:
    def test_dict_keys_sorted(self):
        out = _canonicalize({"b": 1, "a": 2, "c": {"z": 1, "y": 2}})
        assert list(out.keys()) == ["a", "b", "c"]
        assert list(out["c"].keys()) == ["y", "z"]

    def test_list_order_preserved(self):
        out = _canonicalize([{"b": 1, "a": 2}, {"d": 3, "c": 4}])
        assert out == [{"a": 2, "b": 1}, {"c": 4, "d": 3}]

    def test_ansi_stripped(self):
        s = "\x1b[31merror\x1b[0m message"
        assert _strip_ansi(s) == "error message"

    def test_canonicalize_strips_ansi_in_strings(self):
        out = _canonicalize({"k": "\x1b[1;31mboom\x1b[0m"})
        assert out == {"k": "boom"}

    def test_canonicalize_preserves_scalars(self):
        assert _canonicalize(42) == 42
        assert _canonicalize(True) is True
        assert _canonicalize(None) is None
        assert _canonicalize(3.14) == 3.14


class TestParseScopeResult:
    def test_simple_object(self):
        assert _parse_scope_result('{ "is_eligible": true }') == {"is_eligible": True}

    def test_multiline_object(self):
        text = '{\n  "x": 1,\n  "y": 2\n}'
        assert _parse_scope_result(text) == {"x": 1, "y": 2}

    def test_empty_output(self):
        assert _parse_scope_result("") == {}
        assert _parse_scope_result("   \n  ") == {}

    def test_trailing_object_after_trace_array(self):
        text = '[{"event":"foo"}]\n{ "result": 7 }'
        # JSON decode will fail on the concatenation; the fallback finds
        # the last `{` and parses from there.
        assert _parse_scope_result(text) == {"result": 7}

    def test_unparseable_raises(self):
        with pytest.raises(EvaluationError) as ei:
            _parse_scope_result("not json at all")
        assert "catala interpret" in ei.value.context


class TestEvaluationResultContract:
    """The `as_dict()` output must always carry exactly the four keys
    `{outputs, computed, reasons, debug}` — consumer skills depend on it."""

    def test_as_dict_keys_match_contract(self):
        r = EvaluationResult(
            outputs={"x": 1}, computed={"y": 2}, reasons=[], debug={"z": 3}
        )
        d = r.as_dict()
        assert set(d.keys()) == {"outputs", "computed", "reasons", "debug"}

    def test_as_dict_is_canonicalized(self):
        r = EvaluationResult(
            outputs={"b": 1, "a": 2},
            computed={"d": 3, "c": 4},
            reasons=[],
            debug={"f": 5, "e": 6},
        )
        d = r.as_dict()
        assert list(d["outputs"].keys()) == ["a", "b"]
        assert list(d["computed"].keys()) == ["c", "d"]
        assert list(d["debug"].keys()) == ["e", "f"]


# ---------------------------------------------------------------------------
# Catala-missing error path (no real tools needed — mock shutil.which)
# ---------------------------------------------------------------------------


class TestCatalaMissing:
    def test_missing_catala_raises_actionable_error(self, tmp_path):
        module = tmp_path / "fake.catala_en"
        module.write_text("> Module Fake\n")
        with mock.patch("catala_eval.shutil.which", return_value=None):
            with pytest.raises(EvaluationError) as ei:
                run(module, scope="Fake", inputs={})
        msg = str(ei.value)
        assert "catala" in msg.lower()
        assert "catala-lang.org" in msg or "opam" in msg.lower()


# ---------------------------------------------------------------------------
# Library API — happy path (requires catala + clerk for stdlib linkage)
# ---------------------------------------------------------------------------


@_requires_catala
@_requires_clerk
class TestRunHappyPath:
    def test_eligibility_scope_returns_expected_output(self, tmp_path):
        """PA3 fixture: a household below the income threshold and above
        no resource limit should be eligible."""
        module = _copy_fixture_to(tmp_path)
        result = run(
            module,
            scope="Eligibility",
            inputs={
                "household": {
                    "monthly_gross_income": 1000.0,
                    "size": 2,
                    "has_elderly_member": False,
                    "countable_resources": 1000.0,
                }
            },
        )
        assert isinstance(result, EvaluationResult)
        assert result.outputs == {"is_eligible": True}
        assert result.debug["scope"] == "Eligibility"
        assert result.debug["returncode"] == 0
        # Wall time is recorded
        assert isinstance(result.debug["wall_time_ms"], int)
        assert result.debug["wall_time_ms"] >= 0

    def test_ineligible_household(self, tmp_path):
        """A household above the income threshold AND without elderly
        members AND with resources above the limit is denied."""
        module = _copy_fixture_to(tmp_path)
        result = run(
            module,
            scope="Eligibility",
            inputs={
                "household": {
                    "monthly_gross_income": 10000.0,
                    "size": 1,
                    "has_elderly_member": False,
                    "countable_resources": 10000.0,
                }
            },
        )
        assert result.outputs == {"is_eligible": False}


# ---------------------------------------------------------------------------
# Error paths — scope-not-found and missing input
# ---------------------------------------------------------------------------


@_requires_catala
@_requires_clerk
class TestErrorPaths:
    def test_scope_not_found_raises(self, tmp_path):
        module = _copy_fixture_to(tmp_path)
        with pytest.raises(EvaluationError) as ei:
            run(
                module,
                scope="NonexistentScope",
                inputs={"household": {
                    "monthly_gross_income": 1000.0,
                    "size": 1,
                    "has_elderly_member": False,
                    "countable_resources": 0.0,
                }},
            )
        # The catala diagnostic mentions the missing scope name.
        assert "NonexistentScope" in str(ei.value)

    def test_missing_input_field_raises(self, tmp_path):
        module = _copy_fixture_to(tmp_path)
        with pytest.raises(EvaluationError) as ei:
            run(
                module,
                scope="Eligibility",
                # Missing `has_elderly_member` and `countable_resources`.
                inputs={"household": {"monthly_gross_income": 1000.0, "size": 2}},
            )
        msg = str(ei.value).lower()
        # Catala 1.1.0's diagnostic for missing JSON fields names the
        # field — locks the surface to the recognizable shape.
        assert "has_elderly_member" in msg or "missing" in msg

    def test_inputs_not_dict_raises(self, tmp_path):
        module = _copy_fixture_to(tmp_path)
        with pytest.raises(EvaluationError) as ei:
            run(module, scope="Eligibility", inputs="not a dict")  # type: ignore[arg-type]
        assert "inputs" in ei.value.context.lower() or "dict" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# Canonicalization stability — byte-identical as_dict() across runs
# ---------------------------------------------------------------------------


@_requires_catala
@_requires_clerk
class TestCanonicalizationStability:
    def test_same_invocation_is_byte_identical(self, tmp_path):
        module = _copy_fixture_to(tmp_path)
        inputs = {
            "household": {
                "monthly_gross_income": 1500.0,
                "size": 3,
                "has_elderly_member": True,
                "countable_resources": 200.0,
            }
        }
        r1 = run(module, scope="Eligibility", inputs=inputs).as_dict()
        r2 = run(module, scope="Eligibility", inputs=inputs).as_dict()
        # Drop the non-deterministic wall_time_ms field from debug before
        # comparing — that's expected to vary.
        for d in (r1, r2):
            d["debug"].pop("wall_time_ms", None)
        s1 = json.dumps(r1, sort_keys=True)
        s2 = json.dumps(r2, sort_keys=True)
        assert s1 == s2


# ---------------------------------------------------------------------------
# Multi-rule granularity — each rule's outcome surfaces in the trace
# ---------------------------------------------------------------------------


@_requires_catala
@_requires_clerk
class TestMultiRuleGranularity:
    """PA3 fixture has 5 rules in `Eligibility` (federal_poverty_line,
    income_test_passes, elderly_test_passes, resource_disqualification,
    is_eligible). v1 reports scope-level outputs only; the per-rule
    signal lives in the trace (deferred to U9 — see catala_eval module
    docstring). This test locks the v1 surface: the scope-level call
    produces the correct top-level decision for representative input
    combinations."""

    @pytest.mark.parametrize(
        "inputs,expected",
        [
            # Elderly path overrides income test failure
            (
                {"household": {
                    "monthly_gross_income": 5000.0,
                    "size": 1,
                    "has_elderly_member": True,
                    "countable_resources": 100.0,
                }},
                True,
            ),
            # Resource disqualification overrides eligible income
            (
                {"household": {
                    "monthly_gross_income": 500.0,
                    "size": 1,
                    "has_elderly_member": False,
                    "countable_resources": 6000.0,
                }},
                False,
            ),
        ],
    )
    def test_scope_level_decision_for_rule_combinations(
        self, tmp_path, inputs, expected
    ):
        module = _copy_fixture_to(tmp_path)
        result = run(module, scope="Eligibility", inputs=inputs)
        assert result.outputs == {"is_eligible": expected}


# ---------------------------------------------------------------------------
# Preflight (no tools needed — exercises pure path checks)
# ---------------------------------------------------------------------------


class TestPreflight:
    def test_missing_catala_source_raises(self, tmp_path):
        with mock.patch("catala_eval.shutil.which", return_value="/usr/bin/catala"):
            with pytest.raises(EvaluationError) as ei:
                run(tmp_path / "nonexistent.catala_en", scope="X", inputs={})
        assert "not found" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# CLI surface — happy path and preflight failure
# ---------------------------------------------------------------------------


@_requires_catala
@_requires_clerk
class TestCliHappyPath:
    def test_cli_returns_json_shape_exit_0(self, tmp_path, capsys, monkeypatch):
        # Lay out a fake domain root: <tmp>/snap/output/eligibility.catala_en
        domain_root = tmp_path / "domains"
        snap_dir = domain_root / "snap"
        (snap_dir / "output").mkdir(parents=True)
        shutil.copy2(_PA3_MODULE, snap_dir / "output" / "eligibility.catala_en")
        # `clerk start` from the dir housing the catala_en file
        subprocess.run(
            ["clerk", "start"], cwd=str(snap_dir / "output"),
            capture_output=True, text=True, check=False,
        )

        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text(json.dumps({
            "household": {
                "monthly_gross_income": 800.0,
                "size": 1,
                "has_elderly_member": False,
                "countable_resources": 0.0,
            }
        }))

        monkeypatch.setenv("DOMAINS_FULLPATH", str(domain_root))
        rc = catala_eval.main([
            "snap", "eligibility", "--inputs", str(inputs_path),
        ])
        captured = capsys.readouterr()
        assert rc == 0, captured.err

        # Parse the JSON shape — every contract key present
        payload = json.loads(captured.out)
        assert set(payload.keys()) == {"outputs", "computed", "reasons", "debug"}
        assert payload["outputs"] == {"is_eligible": True}


class TestCliPreflight:
    def test_missing_inputs_path_exits_2(self, tmp_path, capsys, monkeypatch):
        domain_root = tmp_path / "domains"
        snap_dir = domain_root / "snap" / "specs"
        snap_dir.mkdir(parents=True)
        (snap_dir / "eligibility.catala_en").write_text("> Module Eligibility\n")

        monkeypatch.setenv("DOMAINS_FULLPATH", str(domain_root))
        rc = catala_eval.main([
            "snap", "eligibility", "--inputs", str(tmp_path / "missing.json"),
        ])
        captured = capsys.readouterr()
        assert rc == 2, captured.out
        assert "inputs" in captured.err.lower()

    def test_missing_catala_source_exits_2(self, tmp_path, capsys, monkeypatch):
        domain_root = tmp_path / "domains"
        (domain_root / "snap" / "specs").mkdir(parents=True)
        # Intentionally do NOT create the catala_en file.
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("{}")

        monkeypatch.setenv("DOMAINS_FULLPATH", str(domain_root))
        rc = catala_eval.main([
            "snap", "eligibility", "--inputs", str(inputs_path),
        ])
        captured = capsys.readouterr()
        assert rc == 2, captured.out
        assert "catala source not found" in captured.err.lower()

    def test_missing_domains_fullpath_exits_2(self, tmp_path, capsys, monkeypatch):
        inputs_path = tmp_path / "inputs.json"
        inputs_path.write_text("{}")
        monkeypatch.delenv("DOMAINS_FULLPATH", raising=False)
        rc = catala_eval.main([
            "snap", "eligibility", "--inputs", str(inputs_path),
        ])
        captured = capsys.readouterr()
        assert rc == 2
        assert "domains_fullpath" in captured.err.lower()


# ---------------------------------------------------------------------------
# Argparse smoke
# ---------------------------------------------------------------------------


class TestArgparseSmoke:
    def test_help_runs(self):
        proc = subprocess.run(
            [sys.executable, str(Path(catala_eval.__file__)), "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "evaluate-catala" in proc.stdout

    def test_module_importable(self):
        """Public API surface stays callable — locks the contract."""
        assert callable(run)
        assert hasattr(EvaluationResult, "__dataclass_fields__")
        assert issubclass(EvaluationError, Exception)
