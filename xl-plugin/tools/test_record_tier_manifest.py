# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for record_tier_manifest.py.

Run: uv run xl-plugin/tools/test_record_tier_manifest.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import record_tier_manifest  # noqa: E402


_GUIDANCE_MANIFEST = "specs/guidance/.facets-manifest.yaml"
_TESTS_MANIFEST = "specs/tests/.civil-manifest.yaml"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "input" / "policy_docs").mkdir(parents=True)
    return domain


def _git_init_and_commit(domain: Path) -> None:
    """Create a git repo so files have stable SHAs."""
    subprocess.run(["git", "init", "--quiet"], cwd=domain, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=domain, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=domain, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=domain, check=True)
    subprocess.run(["git", "add", "."], cwd=domain, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--quiet", "--no-verify"],
        cwd=domain, check=True,
    )


def _populate_policy_facets(domain: Path) -> None:
    """Populate a domain with a typical post-/index-inputs policy_facets/ tree."""
    pf = domain / "policy_facets"
    pf.mkdir(parents=True, exist_ok=True)
    (pf / "input-index.yaml").write_text("files:\n  input/policy_docs/foo.md:\n    sha: deadbeef\n")
    (pf / "compressed").mkdir(parents=True, exist_ok=True)
    (pf / "compressed" / "foo.md").write_text("compressed content")
    (pf / "compressed" / "sub").mkdir(parents=True, exist_ok=True)
    (pf / "compressed" / "sub" / "bar.md").write_text("nested compressed")
    (pf / "computations").mkdir(parents=True, exist_ok=True)
    (pf / "computations" / "foo.md.yaml").write_text("sections: []\n")
    (pf / "computations" / "sub" / "bar.md.yaml").parent.mkdir(parents=True, exist_ok=True)
    (pf / "computations" / "sub" / "bar.md.yaml").write_text("sections: []\n")


def _populate_civil(domain: Path, program: str = "eligibility") -> None:
    """Drop a specs/<program>.civil.yaml file for tests-tier fixtures."""
    specs = domain / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / f"{program}.civil.yaml").write_text("name: eligibility\nrulesets: []\n")


def _read_manifest(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_record_guidance_writes_manifest_for_all_facets_files():
    """Happy path: --tier guidance with populated policy_facets/ records every file."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_policy_facets(domain)
        _git_init_and_commit(domain)

        record_tier_manifest.cmd_record(domain, "guidance")

        manifest = _read_manifest(domain / _GUIDANCE_MANIFEST)
        assert "recorded_at" in manifest
        assert "files" in manifest
        files = manifest["files"]
        assert "policy_facets/input-index.yaml" in files
        assert "policy_facets/compressed/foo.md" in files
        assert "policy_facets/compressed/sub/bar.md" in files
        assert "policy_facets/computations/foo.md.yaml" in files
        assert "policy_facets/computations/sub/bar.md.yaml" in files
        # Each value should be a 40-hex SHA (we committed via git).
        for path, sha in files.items():
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
                f"expected real SHA for {path}, got {sha!r}"
            )


def test_record_tests_writes_manifest_for_civil_files():
    """Happy path: --tier tests records each specs/*.civil.yaml file."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_civil(domain, "eligibility")
        _populate_civil(domain, "exclusion_chain")
        _git_init_and_commit(domain)

        record_tier_manifest.cmd_record(domain, "tests")

        manifest = _read_manifest(domain / _TESTS_MANIFEST)
        files = manifest["files"]
        assert "specs/eligibility.civil.yaml" in files
        assert "specs/exclusion_chain.civil.yaml" in files
        assert all(
            len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
            for sha in files.values()
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_record_guidance_empty_policy_facets_writes_empty_files_map():
    """Edge case: --tier guidance with no policy_facets/ files writes empty map."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        # No policy_facets/ at all.
        record_tier_manifest.cmd_record(domain, "guidance")

        manifest = _read_manifest(domain / _GUIDANCE_MANIFEST)
        assert manifest["files"] == {}


def test_record_tests_no_civil_writes_empty_files_map():
    """Edge case: --tier tests when no specs/*.civil.yaml writes empty map."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        record_tier_manifest.cmd_record(domain, "tests")

        manifest = _read_manifest(domain / _TESTS_MANIFEST)
        assert manifest["files"] == {}


def test_sha_falls_back_to_untracked_on_subprocess_failure():
    """Edge case: git hash-object exits non-zero -> 'untracked'."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_policy_facets(domain)
        # No git init — git hash-object will fail because there's no repo.

        with mock.patch.object(record_tier_manifest, "subprocess") as mock_subprocess:
            # Simulate hash-object failing with empty stdout.
            failed = mock.Mock(stdout="", stderr="not a git repo")
            mock_subprocess.run.return_value = failed
            record_tier_manifest.cmd_record(domain, "guidance")

        manifest = _read_manifest(domain / _GUIDANCE_MANIFEST)
        for sha in manifest["files"].values():
            assert sha == "untracked"


def test_sha_falls_back_to_untracked_when_git_unavailable():
    """Edge case: subprocess.run raises OSError (git binary missing) -> 'untracked'."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_policy_facets(domain)

        with mock.patch.object(record_tier_manifest, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = OSError("git not found")
            record_tier_manifest.cmd_record(domain, "guidance")

        manifest = _read_manifest(domain / _GUIDANCE_MANIFEST)
        assert all(sha == "untracked" for sha in manifest["files"].values())
        assert manifest["files"]  # nonempty — we did enumerate files


def test_unsupported_tier_raises_in_library_path():
    """Edge case: cmd_record with an unrecognized tier raises ValueError."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        try:
            record_tier_manifest.cmd_record(domain, "civil")
        except ValueError as exc:
            assert "civil" in str(exc)
        else:
            raise AssertionError("expected ValueError for unsupported tier 'civil'")


def test_re_record_overwrites_atomically():
    """Edge case: re-running produces a byte-identical manifest modulo recorded_at."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_policy_facets(domain)
        _git_init_and_commit(domain)

        record_tier_manifest.cmd_record(domain, "guidance")
        first = _read_manifest(domain / _GUIDANCE_MANIFEST)
        record_tier_manifest.cmd_record(domain, "guidance")
        second = _read_manifest(domain / _GUIDANCE_MANIFEST)

        assert first["files"] == second["files"]
        # No leftover tmp file.
        assert not (domain / (_GUIDANCE_MANIFEST + ".tmp")).exists()


def test_atomic_write_no_partial_on_replace_failure():
    """Edge case: if os.replace raises mid-write, prior manifest stays intact."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_policy_facets(domain)
        _git_init_and_commit(domain)

        # First successful write establishes a baseline.
        record_tier_manifest.cmd_record(domain, "guidance")
        baseline = (domain / _GUIDANCE_MANIFEST).read_text()

        # Second write: force os.replace to fail.
        with mock.patch.object(record_tier_manifest.os, "replace", side_effect=OSError("disk full")):
            try:
                record_tier_manifest.cmd_record(domain, "guidance")
            except OSError:
                pass

        # Prior content survives; no half-written manifest.
        assert (domain / _GUIDANCE_MANIFEST).read_text() == baseline


# ---------------------------------------------------------------------------
# Argparse error paths (via main())
# ---------------------------------------------------------------------------

def _run_main_with(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Invoke main() in a subprocess to capture exit code + stdout/stderr."""
    tool = Path(__file__).parent / "record_tier_manifest.py"
    result = subprocess.run(
        ["uv", "run", str(tool), *argv],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.returncode, result.stdout, result.stderr


def test_main_missing_tier_flag_exits_2():
    """Error path: missing --tier -> argparse exits 2."""
    with tempfile.TemporaryDirectory() as tmp:
        code, _stdout, stderr = _run_main_with(["mydomain"], env={"DOMAINS_FULLPATH": tmp})
        assert code == 2
        assert "--tier" in stderr or "required" in stderr.lower()


def test_main_invalid_tier_civil_exits_2():
    """Error path: --tier civil rejected by argparse choices=, with helpful epilog."""
    with tempfile.TemporaryDirectory() as tmp:
        code, _stdout, stderr = _run_main_with(
            ["mydomain", "--tier", "civil"], env={"DOMAINS_FULLPATH": tmp}
        )
        assert code == 2
        assert "civil" in stderr.lower() or "choose from" in stderr.lower()


def test_main_domains_fullpath_unset_exits_2():
    """Error path: DOMAINS_FULLPATH unset -> exit 2 with stderr message."""
    # Use a clean env that explicitly drops DOMAINS_FULLPATH.
    tool = Path(__file__).parent / "record_tier_manifest.py"
    clean_env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    result = subprocess.run(
        ["uv", "run", str(tool), "mydomain", "--tier", "guidance"],
        capture_output=True,
        text=True,
        env=clean_env,
    )
    assert result.returncode == 2
    assert "DOMAINS_FULLPATH" in result.stderr


def test_main_domain_dir_missing_exits_2():
    """Error path: domain directory does not exist -> exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        code, _stdout, stderr = _run_main_with(
            ["does_not_exist", "--tier", "guidance"], env={"DOMAINS_FULLPATH": tmp}
        )
        assert code == 2
        assert "not found" in stderr.lower() or "does_not_exist" in stderr


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failed.append((test.__name__, f"AssertionError: {exc}"))
            print(f"FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append((test.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
