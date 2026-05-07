# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for compress_inputs.py — covers U2 plan/finalize scenarios.

Run: uv run xl-plugin/tools/test_compress_inputs.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import compress_inputs  # noqa: E402


def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "input" / "policy_docs").mkdir(parents=True)
    return domain


def _write_doc(domain: Path, rel: str, content: str = "hello") -> Path:
    path = domain / "input" / "policy_docs" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _git_init_and_commit(domain: Path) -> None:
    """Create a git repo so source files have stable SHAs."""
    subprocess.run(["git", "init", "--quiet"], cwd=domain, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=domain, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=domain, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=domain, check=True)
    subprocess.run(["git", "add", "."], cwd=domain, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--quiet", "--no-verify"],
        cwd=domain, check=True,
    )


def _mark_succeeded(domain: Path, srcs: list[str]) -> None:
    """Simulate the skill marking files as compressed."""
    plan_path = domain / "policy_facets" / ".compress-plan.tmp"
    plan = json.loads(plan_path.read_text())
    plan["succeeded"].extend(srcs)
    plan_path.write_text(json.dumps(plan, indent=2))


def test_plan_fresh_domain():
    """Happy path: fresh domain, 2 source files, no manifest. All to_compress."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md", "doc a")
        _write_doc(domain, "sub/b.md", "doc b")
        _git_init_and_commit(domain)

        plan = compress_inputs.cmd_plan(domain)

        assert len(plan["to_compress"]) == 2
        assert plan["to_delete"] == []
        assert plan["noop"] == []
        assert plan["skipped"] == []
        # Copies happened.
        assert (domain / "policy_facets" / "compressed" / "a.md").exists()
        assert (domain / "policy_facets" / "compressed" / "sub" / "b.md").exists()
        # Plan file written.
        assert (domain / "policy_facets" / ".compress-plan.tmp").exists()


def test_plan_with_manifest_unchanged():
    """Happy path: all sources unchanged. 0 to_compress, all noop."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md", "doc a")
        _git_init_and_commit(domain)

        plan = compress_inputs.cmd_plan(domain)
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        compress_inputs.cmd_finalize(domain)

        # Re-run --plan: should be all noop.
        plan2 = compress_inputs.cmd_plan(domain)
        assert plan2["to_compress"] == []
        assert plan2["noop"] == [{"src": "input/policy_docs/a.md", "reason": "unchanged"}]


def test_plan_detects_changed_file():
    """Happy path: one source SHA changed -> 1 to_compress."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md", "doc a")
        _write_doc(domain, "b.md", "doc b")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        _mark_succeeded(domain, [
            "input/policy_docs/a.md",
            "input/policy_docs/b.md",
        ])
        compress_inputs.cmd_finalize(domain)

        # Modify one file and commit.
        _write_doc(domain, "a.md", "doc a CHANGED")
        subprocess.run(["git", "add", "."], cwd=domain, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change", "--quiet", "--no-verify"],
            cwd=domain, check=True,
        )

        plan = compress_inputs.cmd_plan(domain)
        srcs = [e["src"] for e in plan["to_compress"]]
        assert srcs == ["input/policy_docs/a.md"]
        assert len(plan["noop"]) == 1


def test_plan_mirror_delete():
    """Happy path: source removed -> compressed copy queued for delete."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        _mark_succeeded(domain, [
            "input/policy_docs/a.md",
            "input/policy_docs/b.md",
        ])
        compress_inputs.cmd_finalize(domain)

        # Delete one source.
        (domain / "input" / "policy_docs" / "b.md").unlink()
        # Mark its caveman copy as if it was real (we just have the raw copy).

        plan = compress_inputs.cmd_plan(domain)
        assert plan["to_delete"] == ["policy_facets/compressed/b.md"]

        compress_inputs.cmd_finalize(domain)
        assert not (domain / "policy_facets" / "compressed" / "b.md").exists()
        # Manifest entry pruned.
        manifest = compress_inputs.read_manifest(domain)
        assert "input/policy_docs/b.md" not in manifest


def test_untracked_always_recompresses():
    """Edge case: untracked source files are always in to_compress."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        # No git init -- file is "untracked"

        plan = compress_inputs.cmd_plan(domain)
        assert plan["to_compress"][0]["source_sha"] == "untracked"

        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        compress_inputs.cmd_finalize(domain)

        # Even after finalize, next --plan still re-compresses.
        plan2 = compress_inputs.cmd_plan(domain)
        assert len(plan2["to_compress"]) == 1


def test_nested_dirs_preserved():
    """Edge case: input/policy_docs/sub1/sub2/foo.md mirrors to compressed/."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "sub1/sub2/foo.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        dst = domain / "policy_facets" / "compressed" / "sub1" / "sub2" / "foo.md"
        assert dst.exists()
        assert dst.read_text() == "hello"


def test_filename_with_spaces():
    """Edge case: 'filename with spaces.md' round-trips through manifest."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "441-1 EARNED INCOME.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        _mark_succeeded(domain, ["input/policy_docs/441-1 EARNED INCOME.md"])
        compress_inputs.cmd_finalize(domain)

        manifest_path = domain / "policy_facets" / ".compress-manifest.yaml"
        # YAML must be readable on re-parse.
        data = yaml.safe_load(manifest_path.read_text())
        assert "input/policy_docs/441-1 EARNED INCOME.md" in (data["sources"] or {})


def test_non_md_allowed_skipped():
    """Edge case: .txt and other extensions are reported as skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.txt", "txt content")
        _git_init_and_commit(domain)

        plan = compress_inputs.cmd_plan(domain)
        skipped_srcs = [(e["src"], e["reason"]) for e in plan["skipped"]]
        assert ("input/policy_docs/b.txt", "not_allowed") in skipped_srcs


def test_finalize_aborts_uncompressed_dst_for_failed_files():
    """Error path: skill failed to compress -> finalize deletes uncompressed copy."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        # Only a.md succeeded; b.md failed.
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        compress_inputs.cmd_finalize(domain)

        # b.md uncompressed copy should be deleted, manifest should NOT contain it.
        assert (domain / "policy_facets" / "compressed" / "a.md").exists()
        assert not (domain / "policy_facets" / "compressed" / "b.md").exists()
        manifest = compress_inputs.read_manifest(domain)
        assert "input/policy_docs/a.md" in manifest
        assert "input/policy_docs/b.md" not in manifest


def test_finalize_removes_original_md_backups():
    """Edge case: caveman-written *.original.md files are swept by finalize."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        # Simulate caveman writing a sibling backup.
        (domain / "policy_facets" / "compressed" / "a.original.md").write_text("backup")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        compress_inputs.cmd_finalize(domain)

        assert not (domain / "policy_facets" / "compressed" / "a.original.md").exists()


def test_plan_sweeps_stale_backups():
    """Edge case: leftover *.original.md from a crashed run is swept on --plan."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)
        # Plant a stale backup BEFORE plan runs.
        (domain / "policy_facets" / "compressed").mkdir(parents=True, exist_ok=True)
        stale = domain / "policy_facets" / "compressed" / "stale.original.md"
        stale.write_text("crashed run leftover")

        compress_inputs.cmd_plan(domain)
        assert not stale.exists()


def test_finalize_without_plan_errors():
    """Error path: --finalize without prior --plan errors with guidance."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")

        try:
            compress_inputs.cmd_finalize(domain)
        except RuntimeError as exc:
            assert "compress-plan.tmp" in str(exc)
            assert "--plan" in str(exc)
        else:
            raise AssertionError("expected RuntimeError when no plan file exists")


def test_corrupt_manifest_treated_as_empty():
    """Edge case: unreadable manifest -> recompute everything."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)
        (domain / "policy_facets").mkdir(exist_ok=True)
        (domain / "policy_facets" / ".compress-manifest.yaml").write_text("not: : valid: yaml")

        plan = compress_inputs.cmd_plan(domain)
        # Recovered: file goes to to_compress (manifest empty, treated as new).
        assert len(plan["to_compress"]) == 1


def test_atomic_manifest_write_no_partial():
    """Verify manifest write uses os.replace (no half-written file on the disk)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        compress_inputs.cmd_plan(domain)
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        compress_inputs.cmd_finalize(domain)

        # No .tmp leftover from the atomic write.
        assert not (domain / "policy_facets" / ".compress-manifest.yaml.tmp").exists()


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
