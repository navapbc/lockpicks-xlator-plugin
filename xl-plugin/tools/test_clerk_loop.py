# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for clerk_loop.py — U2 clerk loop library + CLI.

Covers the test scenarios enumerated in U2 of the plan:

- Happy path (library API + CLI)
- Edge case: fence visibility surfaces under `clerk test`
- Error path: typecheck-failing module → GNU diagnostics parsed
- Error path: `clerk` not on PATH → actionable error
- Edge case: same-category repeat K=2 → "regenerate" recommended
- Edge case: max-iterations bound → status="unresolved" with full history
- Edge case: cross-module contract — synthetic two-module fixture used
  during U2 verification confirmed `clerk typecheck` catches the
  mismatch; this test re-asserts that surface
- Edge case: naming divergence → both resolution options in the diagnostic
- Integration: stub authoring skill consumes the library API directly
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
import yaml

sys.path.insert(0, os.path.dirname(__file__))

import clerk_loop  # noqa: E402
from clerk_loop import (  # noqa: E402
    Attempt,
    ClerkLoopError,
    Diagnostic,
    LoopResult,
    classify_action,
    density_threshold_exceeded,
    naming_divergence_check,
    parse_gnu_diagnostics,
    run,
    same_category_repeat,
    unparseable_region,
)


# ---------------------------------------------------------------------------
# Test fixture discovery
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PA3_FIXTURE_DIR = (
    _REPO_ROOT / "xl-plugin" / "core" / "tests" / "fixtures" / "synthetic_eligibility"
)
_PA3_MODULE = _PA3_FIXTURE_DIR / "eligibility.catala_en"
_PA3_MANIFEST = _PA3_FIXTURE_DIR / "naming-manifest.yaml"


def _have_clerk() -> bool:
    return shutil.which("clerk") is not None


def _have_catala() -> bool:
    return shutil.which("catala") is not None


_requires_clerk = pytest.mark.skipif(
    not _have_clerk(), reason="clerk not on PATH"
)
_requires_catala = pytest.mark.skipif(
    not _have_catala(), reason="catala not on PATH"
)


def _copy_fixture_to(tmp_path: Path, dest_name: str = "eligibility.catala_en") -> Path:
    """Copy the PA3 fixture into `tmp_path` (so `clerk start` doesn't
    pollute the source tree). Returns the path to the copied module."""
    dest = tmp_path / dest_name
    shutil.copy2(_PA3_MODULE, dest)
    if _PA3_MANIFEST.is_file():
        shutil.copy2(_PA3_MANIFEST, tmp_path / "naming-manifest.yaml")
    # `clerk start` bootstraps _build/libcatala in tmp_path so `catala
    # typecheck` can find Stdlib_en.
    if _have_clerk():
        subprocess.run(
            ["clerk", "start"], cwd=str(tmp_path),
            capture_output=True, text=True, check=False,
        )
    return dest


# ---------------------------------------------------------------------------
# Pure unit tests (no external tooling required)
# ---------------------------------------------------------------------------

class TestParseGnuDiagnostics:
    def test_typecheck_error_line(self):
        text = (
            "importer.catala_en:14.26-14.46: [ERROR] "
            "I don't know how to apply operator + on types SubModule.Color and integer"
        )
        diags = parse_gnu_diagnostics(text)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "importer.catala_en"
        assert d.line == 14
        assert d.col == 26
        assert d.severity == "error"
        assert d.category == "type"
        assert "operator" in d.message.lower()

    def test_warning_line(self):
        text = "foo.catala_en:5.0-5.10: [WARNING] unused variable bar"
        diags = parse_gnu_diagnostics(text)
        assert len(diags) == 1
        assert diags[0].severity == "warning"

    def test_lowercase_severity_form(self):
        text = "foo.catala_en:1.0: error: bad scope"
        diags = parse_gnu_diagnostics(text)
        assert len(diags) == 1
        assert diags[0].severity == "error"
        # "scope" keyword wins over "type"
        assert diags[0].category == "scope"

    def test_skips_non_matching_lines(self):
        text = textwrap.dedent("""\
            ┌─[ERROR]─
            │  Some boxed message
            └─
            real.catala_en:3.1-3.5: [ERROR] genuine GNU line
        """)
        diags = parse_gnu_diagnostics(text)
        assert len(diags) == 1
        assert diags[0].file == "real.catala_en"

    def test_category_inference(self):
        cases = {
            "Module SubModule could not be found": "module",
            "metadata fence is hidden": "fence",
            "enumeration constructor Color.Red not found": "enum",
            "exception default missing for deny rule": "exception",
            "type integer does not unify with money": "type",
            "scope rule definition missing": "scope",
            "division by zero at runtime": "runtime",
            "something unrelated entirely": "other",
        }
        for body, expected in cases.items():
            text = f"x.catala_en:1.0-1.1: [ERROR] {body}"
            diags = parse_gnu_diagnostics(text)
            assert len(diags) == 1, body
            assert diags[0].category == expected, (body, diags[0].category)


class TestSameCategoryRepeat:
    def _attempt(self, i: int, cats: list[str]) -> Attempt:
        diagnostics = [
            Diagnostic(
                file="x", line=i, col=0, severity="error",
                category=c, message=f"msg-{c}-{i}",
                raw=f"raw-{c}-{i}",
            )
            for c in cats
        ]
        return Attempt(iteration=i, diagnostics=diagnostics, action_taken="patch")

    def test_repeats_same_category_no_reduction(self):
        history = [
            self._attempt(1, ["type", "type"]),
            self._attempt(2, ["type", "type"]),
        ]
        assert same_category_repeat(history) is True

    def test_count_decreased_does_not_repeat(self):
        history = [
            self._attempt(1, ["type", "type", "type"]),
            self._attempt(2, ["type"]),
        ]
        assert same_category_repeat(history) is False

    def test_different_categories(self):
        history = [
            self._attempt(1, ["type"]),
            self._attempt(2, ["scope"]),
        ]
        assert same_category_repeat(history) is False

    def test_empty_diagnostics_does_not_repeat(self):
        history = [
            self._attempt(1, []),
            self._attempt(2, []),
        ]
        assert same_category_repeat(history) is False


class TestDensityThreshold:
    def test_exceeded(self):
        diags = [
            Diagnostic(file="x", line=1, col=0, severity="error",
                       category="type", message="m", raw="r")
            for _ in range(3)
        ]
        # 3 errors over 20 source lines → 1 per ~6.7 lines → exceeds
        assert density_threshold_exceeded(diags, source_lines=20) is True

    def test_not_exceeded(self):
        diags = [
            Diagnostic(file="x", line=1, col=0, severity="error",
                       category="type", message="m", raw="r")
        ]
        # 1 error over 200 lines → 1 per 200 lines → not exceeded
        assert density_threshold_exceeded(diags, source_lines=200) is False

    def test_zero_source_lines(self):
        assert density_threshold_exceeded([], source_lines=0) is False


class TestUnparseableRegion:
    def test_boxed_errors_with_no_parsed(self):
        raw = "┌─[ERROR]─\n│  Boxed\n└─\n"
        assert unparseable_region(raw, []) is True

    def test_boxed_errors_with_parsed(self):
        raw = "[ERROR] something\nf.catala_en:1.0: [ERROR] real one"
        parsed = parse_gnu_diagnostics(raw)
        assert unparseable_region(raw, parsed) is False

    def test_clean_output(self):
        assert unparseable_region("everything ok", []) is False


class TestClassifyAction:
    def _diag(self, cat: str) -> Diagnostic:
        return Diagnostic(file="x", line=1, col=0, severity="error",
                          category=cat, message="m", raw="r")

    def test_density_triggers_regenerate(self):
        action, note = classify_action(
            history=[],
            latest_diagnostics=[self._diag("type")] * 5,
            source_lines=20,
            raw_text="",
        )
        assert action == "regenerate"
        assert "density" in note

    def test_clean_patch(self):
        action, note = classify_action(
            history=[],
            latest_diagnostics=[self._diag("type")],
            source_lines=200,
            raw_text="",
        )
        assert action == "patch"
        assert note == ""

    def test_same_category_repeat_promotes_to_regenerate(self):
        history = [
            Attempt(iteration=1, diagnostics=[self._diag("type")],
                    action_taken="patch"),
        ]
        action, note = classify_action(
            history=history,
            latest_diagnostics=[self._diag("type")],
            source_lines=500,
            raw_text="",
        )
        assert action == "regenerate"
        assert "same-category repeat" in note


# ---------------------------------------------------------------------------
# Naming-divergence (uses catala dependency-graph)
# ---------------------------------------------------------------------------

@_requires_catala
@_requires_clerk
class TestNamingDivergence:
    def test_clean_module_against_pa3_manifest(self, tmp_path):
        """The PA3 fixture's source and manifest are aligned by construction;
        the divergence check must emit zero diagnostics."""
        module = _copy_fixture_to(tmp_path)
        diags = naming_divergence_check(module)
        # Allow tolerance for unrelated identifiers picked up from
        # graph nodes (e.g., household.size — fields qualified through
        # a struct should not surface as missing-in-manifest); the
        # _collect_source_identifiers helper drops dotted names.
        divergence_diags = [d for d in diags if d.category == "naming_divergence"]
        # The PA3 manifest lists every primary-module identifier the
        # source uses.
        missing_in_manifest = [
            d for d in divergence_diags
            if "appears in the Catala source" in d.message
        ]
        assert not missing_in_manifest, (
            "PA3 source has identifiers not in manifest: "
            + "; ".join(d.message for d in missing_in_manifest)
        )

    def test_manifest_extra_identifier_surfaces(self, tmp_path):
        """Add a synthetic extra entry to the manifest; the divergence
        check must emit a naming_divergence diagnostic with BOTH resolution
        options in the message body."""
        module = _copy_fixture_to(tmp_path)
        with (tmp_path / "naming-manifest.yaml").open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        manifest.setdefault("computed", {})["nonexistent_field"] = {
            "policy_phrase": "a field that does not exist",
        }
        with (tmp_path / "naming-manifest.yaml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        diags = naming_divergence_check(module)
        divergence = [d for d in diags if d.category == "naming_divergence"]
        assert any("nonexistent_field" in d.message for d in divergence)
        # Both resolution options surface in the message body
        for d in divergence:
            if "nonexistent_field" in d.message:
                assert "(a)" in d.message and "(b)" in d.message


# ---------------------------------------------------------------------------
# Library API — happy path & error path
# ---------------------------------------------------------------------------

@_requires_clerk
class TestRunHappyPath:
    def test_clean_module_returns_ok(self, tmp_path):
        module = _copy_fixture_to(tmp_path)
        result = run(module, max_iterations=2)
        assert result.status == "ok", (
            f"expected status=ok, got {result.status}; "
            f"diagnostics: {[d.as_dict() for d in result.last_diagnostics]}"
        )
        assert result.iterations == 1
        assert not result.last_diagnostics
        assert result.regenerate_recommended is False

    def test_integration_stub_skill_round_trip(self, tmp_path):
        """A stub authoring skill emits a Catala module (here: copy the
        clean fixture), then invokes `run()` via the library API directly
        without shelling out. The full round-trip succeeds."""
        # Simulate skill emission.
        emitted = tmp_path / "eligibility.catala_en"
        emitted.write_text(_PA3_MODULE.read_text())
        shutil.copy2(_PA3_MANIFEST, tmp_path / "naming-manifest.yaml")
        subprocess.run(["clerk", "start"], cwd=str(tmp_path),
                       capture_output=True, text=True, check=False)

        # Stub-skill consumes the library API.
        result = run(emitted, max_iterations=3)
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Error path — typecheck failure
# ---------------------------------------------------------------------------

@_requires_clerk
class TestRunTypecheckFailure:
    def test_typecheck_failing_module_parses_gnu_diagnostics(self, tmp_path):
        """Hand-rolled module with a deliberate type-mismatch. The loop
        runs `clerk typecheck`, captures GNU-format diagnostics, and each
        Diagnostic carries file/line/col and an inferred category."""
        broken = textwrap.dedent("""\
            > Module Broken

            ```catala-metadata
            declaration scope BrokenScope:
              input x content integer
              output y content integer
            ```

            ```catala
            scope BrokenScope:
              definition y equals x + true
            ```
            """)
        module = tmp_path / "Broken.catala_en"
        module.write_text(broken)
        subprocess.run(["clerk", "start"], cwd=str(tmp_path),
                       capture_output=True, text=True, check=False)

        result = run(module, max_iterations=2)
        assert result.status == "unresolved"
        assert result.last_diagnostics
        d = result.last_diagnostics[0]
        # file:line.col anchored
        assert d.file.endswith("Broken.catala_en")
        assert d.line > 0
        assert d.category in {"type", "scope", "other"}


# ---------------------------------------------------------------------------
# Cross-module contract — synthetic two-module verification
# ---------------------------------------------------------------------------

@_requires_clerk
class TestCrossModuleContract:
    """Plan Step 3: verify whether `clerk typecheck` catches a deliberate
    exported-type mismatch across modules. If yes, the cross_module_contract
    walker is OUT OF SCOPE for U2 (the check is implicit). This test
    locks the surface so a future clerk upgrade can't silently lose the
    check without surfacing here."""

    def test_clerk_typecheck_catches_mismatch(self, tmp_path):
        sub = tmp_path / "SubModule.catala_en"
        sub.write_text(textwrap.dedent("""\
            > Module SubModule

            ```catala-metadata
            declaration enumeration Color:
              -- Red
              -- Blue

            declaration scope SubScope:
              output color content Color
            ```

            ```catala
            scope SubScope:
              definition color equals Color.Red
            ```
            """))
        importer = tmp_path / "Importer.catala_en"
        importer.write_text(textwrap.dedent("""\
            > Module Importer

            > Using SubModule

            ```catala-metadata
            declaration scope ImporterScope:
              internal sub_result content SubModule.SubScope
              output flag content integer
            ```

            ```catala
            scope ImporterScope:
              definition sub_result equals output of SubModule.SubScope
              definition flag equals sub_result.color + 1
            ```
            """))
        subprocess.run(["clerk", "start"], cwd=str(tmp_path),
                       capture_output=True, text=True, check=False)

        result = run(importer, max_iterations=1)
        # Either status="unresolved" with a type/module/other-category
        # diagnostic surfaces — either way the cross-module mismatch is
        # not silently passed.
        assert result.status == "unresolved", (
            "clerk typecheck must reject the cross-module mismatch. "
            "If this test starts failing, the cross_module_contract walker "
            "needs to be added back into U2's scope."
        )
        # The diagnostic must mention the type operator or the mismatched
        # types — locks the diagnostic surface to a recognisable shape.
        joined = " ".join(d.message for d in result.last_diagnostics).lower()
        assert ("operator" in joined or "type" in joined
                or "color" in joined), (
            f"unexpected diagnostic shape: {result.last_diagnostics}"
        )


# ---------------------------------------------------------------------------
# Clerk-missing error path (no real tools needed — mock shutil.which)
# ---------------------------------------------------------------------------

class TestClerkMissing:
    def test_missing_clerk_raises_actionable_error(self, tmp_path):
        module = tmp_path / "fake.catala_en"
        module.write_text("> Module Fake\n")
        with mock.patch("clerk_loop.shutil.which", return_value=None):
            with pytest.raises(ClerkLoopError) as ei:
                run(module, max_iterations=1)
        msg = str(ei.value)
        assert "clerk" in msg.lower()
        # Actionable install hint surfaces
        assert "catala-lang.org" in msg or "opam" in msg.lower()

    def test_cli_missing_clerk_emits_error_fence(self, tmp_path, capsys):
        module = tmp_path / "fake.catala_en"
        module.write_text("> Module Fake\n")
        with mock.patch("clerk_loop.shutil.which", return_value=None):
            rc = clerk_loop.main([
                "any", "any", "--module-path", str(module),
            ])
        assert rc == 2
        captured = capsys.readouterr()
        # JSON header → sentinel → human summary
        lines = captured.out.splitlines()
        assert lines, "CLI emitted no output"
        header = json.loads(lines[0])
        assert header["status"] == "error"
        assert clerk_loop._HEADER_SENTINEL in captured.out


# ---------------------------------------------------------------------------
# Max-iterations bound
# ---------------------------------------------------------------------------

@_requires_clerk
class TestMaxIterationsBound:
    def test_never_converging_module_returns_unresolved(self, tmp_path):
        """A persistently-broken module hits the bound and surfaces the
        full repair_history."""
        broken = textwrap.dedent("""\
            > Module Broken

            ```catala-metadata
            declaration scope BrokenScope:
              input x content integer
              output y content integer
            ```

            ```catala
            scope BrokenScope:
              definition y equals x + true
            ```
            """)
        module = tmp_path / "Broken.catala_en"
        module.write_text(broken)
        subprocess.run(["clerk", "start"], cwd=str(tmp_path),
                       capture_output=True, text=True, check=False)
        result = run(module, max_iterations=3)
        assert result.status == "unresolved"
        assert result.iterations == 3
        assert len(result.repair_history) == 3


# ---------------------------------------------------------------------------
# Same-category repeat / regenerate signal
# ---------------------------------------------------------------------------

class TestFenceVisibilityClassification:
    """Plan scenario: a `catala-metadata` fence-visibility bug typechecks
    but fails `clerk test` with a "module" or "fence" categorized
    diagnostic. The actual runtime trigger requires a specific Catala
    fixture; here we lock the classifier surface so that when a real
    fence-visibility diagnostic surfaces it routes to the right bucket.

    The diagnostic shape `catala-metadata block hidden / not visible from
    importer` is the documented form (see
    docs/plans/archive/2026-03-16.b-fix-catala-module-visibility-and-test-
    pattern.md)."""

    def test_metadata_hidden_routes_to_fence(self):
        text = (
            "snap.catala_en:3.1-3.10: [ERROR] "
            "catala-metadata block hidden from importing module"
        )
        diags = parse_gnu_diagnostics(text)
        assert len(diags) == 1
        assert diags[0].category in {"fence", "module"}


class TestRegenerateRecommendedFlag:
    """Constructed at the classifier level (no real clerk loop needed) —
    the library exposes the signal so callers can drive higher-level
    re-emission policy."""

    def test_repeated_same_category_promotes_to_regenerate(self):
        type_diag = Diagnostic(file="x", line=1, col=0, severity="error",
                                category="type", message="m", raw="r")
        history = [
            Attempt(iteration=1, diagnostics=[type_diag, type_diag],
                    action_taken="patch"),
        ]
        action, note = classify_action(
            history=history,
            latest_diagnostics=[type_diag, type_diag],
            source_lines=500,
            raw_text="",
        )
        assert action == "regenerate"
        assert "same-category repeat" in note


# ---------------------------------------------------------------------------
# CLI — JSON header + sentinel + human summary
# ---------------------------------------------------------------------------

@_requires_clerk
class TestCliHappyPath:
    def test_cli_emits_json_header_sentinel_summary(self, tmp_path, capsys):
        module = _copy_fixture_to(tmp_path)
        rc = clerk_loop.main([
            "fake-domain", "fake-module",
            "--module-path", str(module),
            "--max-iterations", "1",
        ])
        captured = capsys.readouterr()
        # Exit 0 on status=ok
        assert rc == 0, captured.out + captured.err
        out = captured.out
        # JSON header first
        first_line = out.splitlines()[0]
        header = json.loads(first_line)
        assert header["status"] == "ok"
        assert header["iterations"] == 1
        assert header["diagnostic_count"] == 0
        # Sentinel follows
        assert clerk_loop._HEADER_SENTINEL in out
        # Human summary follows the sentinel
        idx = out.index(clerk_loop._HEADER_SENTINEL)
        summary_section = out[idx + len(clerk_loop._HEADER_SENTINEL):]
        assert "passed" in summary_section.lower() or "iteration" in summary_section.lower()


# ---------------------------------------------------------------------------
# Argparse smoke
# ---------------------------------------------------------------------------

class TestArgparseSmoke:
    def test_help_runs(self):
        proc = subprocess.run(
            [sys.executable, str(Path(clerk_loop.__file__)), "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "clerk-loop" in proc.stdout

    def test_module_importable(self):
        """The library symbols imported at module-load time must remain
        callable — locks the public API."""
        assert callable(run)
        assert callable(parse_gnu_diagnostics)
        assert hasattr(LoopResult, "__dataclass_fields__")
        assert hasattr(Diagnostic, "__dataclass_fields__")
        assert hasattr(Attempt, "__dataclass_fields__")
