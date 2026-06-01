# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for clerk_loop_multi.py — U3 (multi-module orchestrator).

Covers the test scenarios enumerated in U3 of the plan:

- Happy path: multi-module fixture, aggregated check passes (AE1).
- Single-module work-list, behavior equivalent to pre-fix check (AE4).
- Empty work-list / no `generate` entries — still resolves manifest path
  and runs aggregation.
- Pre-flight failure: missing naming-manifest, missing domain dir.
- Per-module clerk-loop failure: failed_module + verified_modules.
- Aggregated divergence: manifest entry no module declares.
- Source → manifest direction (R3): module declares identifier not in
  manifest (AE2).
- CLI: JSON header + sentinel + summary round-trip.
- Edge case: clerk not on PATH → pre-flight surface clean.
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
import clerk_loop_multi  # noqa: E402
import load_extraction_context  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FIXTURE_DIR = (
    _REPO_ROOT / "xl-plugin" / "core" / "tests"
    / "fixtures" / "synthetic_multi_module"
)


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


def _copy_multi_fixture(tmp_path: Path, domain_name: str = "synthetic_multi_module") -> Path:
    """Copy the multi-module fixture to `tmp_path/<domain_name>/` and run
    `clerk start` inside its specs/ so the bundled stdlib resolves.
    Returns the absolute domain directory path."""
    dst = tmp_path / domain_name
    shutil.copytree(_FIXTURE_DIR, dst)
    if _have_clerk():
        subprocess.run(
            ["clerk", "start"], cwd=str(dst / "specs"),
            capture_output=True, text=True, check=False,
        )
    return dst


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_cli_output(captured_out: str) -> tuple[dict, str]:
    """Return (header_dict, summary_body) parsed from the CLI's stdout."""
    lines = captured_out.splitlines()
    assert lines, "no CLI output"
    header = json.loads(lines[0])
    assert clerk_loop_multi._HEADER_SENTINEL in captured_out, (
        f"sentinel missing; got:\n{captured_out}"
    )
    idx = captured_out.index(clerk_loop_multi._HEADER_SENTINEL)
    body = captured_out[idx + len(clerk_loop_multi._HEADER_SENTINEL):]
    return header, body


# ---------------------------------------------------------------------------
# Happy path — multi-module fixture covers manifest under aggregation
# ---------------------------------------------------------------------------

@_requires_clerk
@_requires_catala
class TestHappyPathMultiModule:
    """AE1: 2-module domain where the per-file divergence check would
    false-positive but the aggregated post-pass does not."""

    def test_aggregated_check_passes_when_union_covers_manifest(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module"])
        captured = capsys.readouterr()
        assert rc == 0, captured.out + captured.err
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "ok"
        assert header["diagnostic_count"] == 0
        assert header["modules_checked"] == 2
        # Both fixture modules already exist on disk → action=reference; the
        # per-module generate-loop pass is empty by design here.
        assert header["modules_generated"] == 0
        assert "verified" in body.lower() or "passed" in body.lower()


# ---------------------------------------------------------------------------
# Aggregated divergence + R3 direction
# ---------------------------------------------------------------------------

@_requires_clerk
@_requires_catala
class TestAggregatedDivergenceSurfaces:
    """AE2: an identifier declared in a module's source but absent from
    the manifest surfaces as a source → manifest diagnostic even under
    aggregation."""

    def test_manifest_entry_no_module_declares_surfaces(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
        with manifest_path.open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        manifest.setdefault("computed", {})["phantom_field"] = {
            "policy_phrase": "no module declares this",
        }
        with manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module"])
        captured = capsys.readouterr()
        assert rc == 1, captured.out
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert header["diagnostic_count"] >= 1
        assert "phantom_field" in body, body

    def test_source_to_manifest_direction_surfaces(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        # Drop a manifest entry so module A's `field_a` becomes unmanifested.
        manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
        with manifest_path.open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        del manifest["computed"]["field_a"]
        with manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module"])
        captured = capsys.readouterr()
        assert rc == 1
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert "field_a" in body, body


# ---------------------------------------------------------------------------
# Per-module clerk-loop failure
# ---------------------------------------------------------------------------

@_requires_clerk
@_requires_catala
class TestPerModuleFailure:
    """AE3: a per-module typecheck failure halts after that module, the
    header carries failed_module + verified_modules."""

    def test_typecheck_failure_halts_with_verified_modules(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        specs = domain_dir / "specs"
        # ModuleA stays clean (from fixture). Overwrite ModuleB with a
        # deliberate type mismatch (integer + boolean) so its per-module
        # clerk loop halts on a typecheck diagnostic.
        (specs / "ModuleB.catala_en").write_text(textwrap.dedent("""\
            > Module ModuleB

            ```catala-metadata
            declaration scope ScopeB:
              output field_b content integer
            ```

            ```catala
            scope ScopeB:
              definition field_b equals 1 + true
            ```
            """))
        subprocess.run(
            ["clerk", "start"], cwd=str(specs),
            capture_output=True, text=True, check=False,
        )

        # `_build_work_list` tags any on-disk module as action=reference, so
        # the per-module loop pass would otherwise be empty. Coerce both
        # entries to action=generate to exercise the per-module pass that
        # /extract-ruleset Step 6 drives in production (where Step 4
        # authored these files moments earlier).
        real_load_context = load_extraction_context.load_context

        def patched_load_context(d, p, mode):
            payload = real_load_context(d, p, mode)
            for entry in payload["work_list"]:
                entry["action"] = "generate"
            return payload

        monkeypatch.setattr(
            clerk_loop_multi.load_extraction_context,
            "load_context", patched_load_context,
        )

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main([
            "synthetic_multi_module", "--max-iterations", "1",
        ])
        captured = capsys.readouterr()
        assert rc == 1, captured.out
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert header["failed_module"] == "ModuleB"
        assert header["verified_modules"] == ["ModuleA"]


# ---------------------------------------------------------------------------
# Pre-flight failures
# ---------------------------------------------------------------------------

class TestPreFlightFailures:
    def test_missing_domain_dir_returns_exit_2(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["does_not_exist"])
        captured = capsys.readouterr()
        assert rc == 2
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "error"

    def test_missing_naming_manifest_returns_exit_2(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        (domain_dir / "specs" / "naming-manifest.yaml").unlink()
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module"])
        captured = capsys.readouterr()
        # Missing naming-manifest is also caught by load_context's pre-flight
        # (it's in _REQUIRED_FILES). Either way, exit 2 + status="error".
        assert rc == 2
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "error"

    def test_missing_domains_fullpath_returns_exit_2(self, monkeypatch, capsys):
        monkeypatch.delenv("DOMAINS_FULLPATH", raising=False)
        rc = clerk_loop_multi.main(["any-domain"])
        captured = capsys.readouterr()
        assert rc == 2
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "error"


# ---------------------------------------------------------------------------
# Clerk-missing pre-flight surface
# ---------------------------------------------------------------------------

@_requires_catala
class TestClerkMissing:
    """When `clerk` is not on PATH and a per-module generate-loop would
    fire, the ClerkLoopError is caught and the header surfaces cleanly
    without a stack trace leaking past."""

    def test_missing_clerk_caught_by_orchestrator(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        # Delete the on-disk files so they become action="generate".
        for name in ("ModuleA.catala_en", "ModuleB.catala_en"):
            (domain_dir / "specs" / name).unlink()
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        with mock.patch("clerk_loop.shutil.which", return_value=None):
            rc = clerk_loop_multi.main(["synthetic_multi_module"])
        captured = capsys.readouterr()
        # ClerkLoopError caught → status "unresolved"; no stack-trace leak.
        assert rc == 1
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert header["failed_module"] is not None
        assert "Traceback" not in captured.out
        assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# CLI shape
# ---------------------------------------------------------------------------

class TestCliShape:
    def test_help_runs(self):
        proc = subprocess.run(
            [sys.executable, str(Path(clerk_loop_multi.__file__)), "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "clerk-loop-multi" in proc.stdout

    def test_module_importable(self):
        """Locks the public API."""
        assert callable(clerk_loop_multi.run)
        assert callable(clerk_loop_multi.main)
        assert hasattr(clerk_loop_multi, "_HEADER_SENTINEL")


# ---------------------------------------------------------------------------
# Empty / single-module work-list edges
# ---------------------------------------------------------------------------

@_requires_clerk
@_requires_catala
class TestSingleModuleEquivalence:
    """AE4: when the work-list collapses to a single module (no
    ruleset-modules.yaml), the orchestrator's behavior is equivalent to
    the pre-fix single-module path."""

    def test_no_ruleset_modules_falls_back_to_single_module(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        # Remove ModuleA + ruleset-modules.yaml so the work-list collapses
        # to a single ModuleB entry. Drop ModuleA from the manifest too so
        # the single-module aggregation passes cleanly.
        (domain_dir / "specs" / "ModuleA.catala_en").unlink()
        (domain_dir / "specs" / "guidance" / "ruleset-modules.yaml").unlink()
        manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
        with manifest_path.open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        del manifest["computed"]["field_a"]
        with manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module", "ModuleB"])
        captured = capsys.readouterr()
        assert rc == 0, captured.out + captured.err
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "ok"
        assert header["modules_checked"] == 1


# ---------------------------------------------------------------------------
# U6: --check-only flag (used by /update-ruleset Step 0)
# ---------------------------------------------------------------------------

@_requires_catala
class TestCheckOnlyMode:
    """--check-only skips every per-module clerk loop and runs only the
    aggregated divergence check. Used as a pre-edit gate from
    /update-ruleset Step 0."""

    def test_check_only_passes_on_clean_fixture(
        self, tmp_path, monkeypatch, capsys
    ):
        _copy_multi_fixture(tmp_path)
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))

        # Track whether clerk_loop.run() fires; --check-only must skip it.
        called: list[Any] = []
        real_run = clerk_loop.run

        def spy_run(*a, **kw):
            called.append((a, kw))
            return real_run(*a, **kw)

        monkeypatch.setattr(clerk_loop_multi.clerk_loop, "run", spy_run)

        rc = clerk_loop_multi.main(["synthetic_multi_module", "--check-only"])
        captured = capsys.readouterr()
        assert rc == 0, captured.out + captured.err
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "ok"
        assert header["mode"] == "check_only"
        assert header["modules_generated"] == 0
        assert called == [], (
            f"--check-only must skip per-module clerk_loop.run(); fired: "
            f"{called}"
        )

    def test_check_only_surfaces_aggregated_divergence(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
        with manifest_path.open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        manifest.setdefault("computed", {})["phantom_check_only"] = {
            "policy_phrase": "no module declares this",
        }
        with manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module", "--check-only"])
        captured = capsys.readouterr()
        assert rc == 1
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert header["mode"] == "check_only"
        assert "phantom_check_only" in body

    def test_check_only_surfaces_source_to_manifest_divergence(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
        with manifest_path.open(encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
        del manifest["computed"]["field_b"]
        with manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f)

        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module", "--check-only"])
        captured = capsys.readouterr()
        assert rc == 1
        header, body = _parse_cli_output(captured.out)
        assert header["status"] == "unresolved"
        assert header["mode"] == "check_only"
        assert "field_b" in body

    def test_check_only_missing_manifest_returns_exit_2(
        self, tmp_path, monkeypatch, capsys
    ):
        domain_dir = _copy_multi_fixture(tmp_path)
        (domain_dir / "specs" / "naming-manifest.yaml").unlink()
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main(["synthetic_multi_module", "--check-only"])
        captured = capsys.readouterr()
        assert rc == 2
        header, _ = _parse_cli_output(captured.out)
        assert header["status"] == "error"

    def test_check_only_with_max_iterations_emits_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        _copy_multi_fixture(tmp_path)
        monkeypatch.setenv("DOMAINS_FULLPATH", str(tmp_path))
        rc = clerk_loop_multi.main([
            "synthetic_multi_module", "--check-only", "--max-iterations", "9",
        ])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WARN: --max-iterations is ignored with --check-only" in captured.err
