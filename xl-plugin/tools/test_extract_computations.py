# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for extract_computations.py — covers U1 plan/finalize scenarios.

Run: uv run xl-plugin/tools/test_extract_computations.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import extract_computations  # noqa: E402
import outcome_markers  # noqa: E402

_PLAN_TMP_DIR = "policy_facets/.extract-plan.d"


def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "input" / "policy_docs").mkdir(parents=True)
    return domain


def _write_doc(domain: Path, rel: str, content: str = "hello") -> Path:
    path = domain / "input" / "policy_docs" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_dst(domain: Path, rel: str, content: str = "extracted") -> Path:
    """Simulate the AI step writing a per-file computations file.

    `rel` is the source rel (e.g. 'a.md'); the destination filename appends '.yaml'.
    """
    path = domain / "policy_facets" / "computations" / f"{rel}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _git_init_and_commit(domain: Path) -> None:
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
    """Simulate the skill marking files as extracted by writing per-file markers."""
    plan_path = domain / "policy_facets" / ".extract-plan.tmp"
    plan = json.loads(plan_path.read_text())
    sha_by_src = {entry["src"]: entry["source_sha"] for entry in plan["to_extract"]}
    marker_dir = domain / _PLAN_TMP_DIR
    for src in srcs:
        outcome_markers.write_marker(
            marker_dir, src, "succeeded", source_sha=sha_by_src.get(src),
        )


def _mark_failed(domain: Path, src: str, error: str = "boom") -> None:
    """Simulate a worker writing a failure marker."""
    plan_path = domain / "policy_facets" / ".extract-plan.tmp"
    plan = json.loads(plan_path.read_text())
    sha_by_src = {entry["src"]: entry["source_sha"] for entry in plan["to_extract"]}
    marker_dir = domain / _PLAN_TMP_DIR
    outcome_markers.write_marker(
        marker_dir, src, "failed", source_sha=sha_by_src.get(src), error=error,
    )


def _mark_in_progress(domain: Path, src: str) -> None:
    """Simulate a worker that wrote in_progress and then died before completing."""
    plan_path = domain / "policy_facets" / ".extract-plan.tmp"
    plan = json.loads(plan_path.read_text())
    sha_by_src = {entry["src"]: entry["source_sha"] for entry in plan["to_extract"]}
    marker_dir = domain / _PLAN_TMP_DIR
    outcome_markers.write_marker(
        marker_dir, src, "in_progress", source_sha=sha_by_src.get(src),
    )


def test_plan_fresh_domain():
    """Happy path: fresh domain, 2 source files, no manifest. All to_extract."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "sub/b.md")
        _git_init_and_commit(domain)

        plan = extract_computations.cmd_plan(domain)

        assert len(plan["to_extract"]) == 2
        assert plan["to_delete"] == []
        assert plan["noop"] == []
        # Plan does NOT pre-write destination files (AI generates them).
        assert not (domain / "policy_facets" / "computations" / "a.md.yaml").exists()
        # Plan file written; intermediate dirs created.
        assert (domain / "policy_facets" / ".extract-plan.tmp").exists()
        assert (domain / "policy_facets" / "computations" / "sub").is_dir()


def test_plan_with_manifest_unchanged_and_dst_present():
    """Happy path: SHA matches manifest AND dst exists -> noop."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        # AI step wrote the file.
        _write_dst(domain, "a.md")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        # Re-run --plan: should be noop now.
        plan2 = extract_computations.cmd_plan(domain)
        assert plan2["to_extract"] == []
        assert plan2["noop"] == [{"src": "input/policy_docs/a.md", "reason": "unchanged"}]


def test_plan_reclassifies_when_destination_missing():
    """Edge case (Finding 3 fix): manifest matches but destination is missing -> to_extract.

    If the per-file file has been deleted manually (or never landed in a partial git
    checkout), the next --plan must re-extract it. Without the destination-existence
    check, the file would be classified as noop and silently skipped.
    """
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        # Manually delete the destination file (simulating partial checkout).
        (domain / "policy_facets" / "computations" / "a.md.yaml").unlink()

        plan2 = extract_computations.cmd_plan(domain)
        # Source SHA matches manifest, but destination is missing — must re-extract.
        srcs = [e["src"] for e in plan2["to_extract"]]
        assert srcs == ["input/policy_docs/a.md"]
        assert plan2["noop"] == []


def test_plan_detects_changed_file():
    """Happy path: one source SHA changed -> 1 to_extract, others noop."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md", "doc a")
        _write_doc(domain, "b.md", "doc b")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md")
        _write_dst(domain, "b.md")
        _mark_succeeded(domain, [
            "input/policy_docs/a.md",
            "input/policy_docs/b.md",
        ])
        extract_computations.cmd_finalize(domain)

        # Modify one file and commit.
        _write_doc(domain, "a.md", "doc a CHANGED")
        subprocess.run(["git", "add", "."], cwd=domain, check=True)
        subprocess.run(
            ["git", "commit", "-m", "change", "--quiet", "--no-verify"],
            cwd=domain, check=True,
        )

        plan = extract_computations.cmd_plan(domain)
        srcs = [e["src"] for e in plan["to_extract"]]
        assert srcs == ["input/policy_docs/a.md"]
        assert len(plan["noop"]) == 1


def test_plan_mirror_delete():
    """Happy path: source removed -> per-file file queued for delete; manifest pruned."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md")
        _write_dst(domain, "b.md")
        _mark_succeeded(domain, [
            "input/policy_docs/a.md",
            "input/policy_docs/b.md",
        ])
        extract_computations.cmd_finalize(domain)

        # Delete one source.
        (domain / "input" / "policy_docs" / "b.md").unlink()

        plan = extract_computations.cmd_plan(domain)
        assert plan["to_delete"] == ["policy_facets/computations/b.md.yaml"]

        extract_computations.cmd_finalize(domain)
        assert not (domain / "policy_facets" / "computations" / "b.md.yaml").exists()
        manifest = extract_computations.read_manifest(domain)
        assert "input/policy_docs/b.md" not in manifest


def test_untracked_always_re_extracts():
    """Edge case: untracked source files are always in to_extract."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        # No git init -- file is "untracked"

        plan = extract_computations.cmd_plan(domain)
        assert plan["to_extract"][0]["source_sha"] == "untracked"

        _write_dst(domain, "a.md")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        # Even after finalize, next --plan still re-extracts (untracked is never noop).
        plan2 = extract_computations.cmd_plan(domain)
        assert len(plan2["to_extract"]) == 1


def test_nested_dirs_preserved():
    """Edge case: input/policy_docs/sub1/sub2/foo.md mirrors to computations/."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "sub1/sub2/foo.md")
        _git_init_and_commit(domain)

        plan = extract_computations.cmd_plan(domain)
        assert plan["to_extract"][0]["dst"] == "policy_facets/computations/sub1/sub2/foo.md.yaml"
        # Intermediate dirs created so AI can write there.
        assert (domain / "policy_facets" / "computations" / "sub1" / "sub2").is_dir()


def test_filename_with_spaces():
    """Edge case: '441-1 EARNED INCOME.md' round-trips through manifest."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "441-1 EARNED INCOME.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "441-1 EARNED INCOME.md")
        _mark_succeeded(domain, ["input/policy_docs/441-1 EARNED INCOME.md"])
        extract_computations.cmd_finalize(domain)

        manifest_path = domain / "policy_facets" / ".computations-manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text())
        assert "input/policy_docs/441-1 EARNED INCOME.md" in (data["sources"] or {})


def test_non_md_skipped():
    """Edge case: .txt and other extensions are reported as skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.txt", "txt content")
        _git_init_and_commit(domain)

        plan = extract_computations.cmd_plan(domain)
        skipped_srcs = [(e["src"], e["reason"]) for e in plan["skipped"]]
        assert ("input/policy_docs/b.txt", "not_allowed") in skipped_srcs


def test_finalize_aborts_partial_dst_for_failed_files():
    """Error path: skill failed for one file -> finalize deletes its partial dst."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _write_doc(domain, "b.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        # AI step succeeded for a.md and even started b.md (partial write), but only
        # marked a.md as succeeded.
        _write_dst(domain, "a.md")
        _write_dst(domain, "b.md", "partial garbage")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        assert (domain / "policy_facets" / "computations" / "a.md.yaml").exists()
        assert not (domain / "policy_facets" / "computations" / "b.md.yaml").exists()
        manifest = extract_computations.read_manifest(domain)
        assert "input/policy_docs/a.md" in manifest
        assert "input/policy_docs/b.md" not in manifest


def test_finalize_without_plan_errors():
    """Error path: --finalize without prior --plan errors with guidance."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")

        try:
            extract_computations.cmd_finalize(domain)
        except RuntimeError as exc:
            assert "extract-plan.tmp" in str(exc)
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
        (domain / "policy_facets" / ".computations-manifest.yaml").write_text("not: : valid: yaml")

        plan = extract_computations.cmd_plan(domain)
        assert len(plan["to_extract"]) == 1


def test_atomic_manifest_write_no_partial():
    """Verify manifest write uses os.replace (no half-written file on the disk)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        assert not (domain / "policy_facets" / ".computations-manifest.yaml.tmp").exists()


def test_legacy_input_sections_yaml_ignored():
    """Edge case (Finding 1 resolution): legacy input-sections.yaml is left untouched.

    The new flow does not migrate from policy_facets/input-sections.yaml or
    specs/input-sections.yaml; it queues every source as to_extract on first run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)
        # Pretend there's a stale legacy file from a prior version.
        (domain / "policy_facets").mkdir(exist_ok=True)
        legacy = domain / "policy_facets" / "input-sections.yaml"
        legacy.write_text("# legacy content from prior version\nsections: []\n")

        plan = extract_computations.cmd_plan(domain)
        # Legacy file is ignored — we did NOT read it, did NOT split it, did NOT delete it.
        assert legacy.exists()
        assert legacy.read_text().startswith("# legacy content")
        # Source goes to to_extract regardless.
        assert len(plan["to_extract"]) == 1


def test_finalize_in_progress_marker_treated_as_aborted():
    """Edge case: in_progress marker (worker died mid-action) -> dst removed, summary lists aborted."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        # Worker started writing dst, then died before updating the marker.
        _write_dst(domain, "a.md", "partial dst from aborted worker")
        _mark_in_progress(domain, "input/policy_docs/a.md")

        summary = extract_computations.cmd_finalize(domain)
        assert summary["extracted"] == 0
        assert summary["aborted"] == 1
        assert summary["failed"] == 1  # in_progress aborts surface as failures too
        # Dst was cleaned up.
        assert not (domain / "policy_facets" / "computations" / "a.md.yaml").exists()
        # Manifest does NOT include the aborted src.
        manifest = extract_computations.read_manifest(domain)
        assert "input/policy_docs/a.md" not in manifest


def test_finalize_missing_marker_treated_as_aborted():
    """Edge case: src in to_extract has no marker (worker died before writing) -> aborted."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        # Worker died before writing any marker. Dst may or may not exist; simulate "exists".
        _write_dst(domain, "a.md")
        # Note: no _mark_succeeded / _mark_in_progress / _mark_failed call.

        summary = extract_computations.cmd_finalize(domain)
        assert summary["extracted"] == 0
        assert summary["aborted"] == 1
        assert summary["failed"] == 1
        assert not (domain / "policy_facets" / "computations" / "a.md.yaml").exists()


def test_finalize_failed_marker_does_not_count_as_aborted():
    """Edge case: explicit failed marker counts in failed but NOT in aborted."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md", "partial garbage from failed run")
        _mark_failed(domain, "input/policy_docs/a.md", error="bad yaml")

        summary = extract_computations.cmd_finalize(domain)
        assert summary["extracted"] == 0
        # An explicit failure marker is NOT an abort — only missing/in_progress markers are.
        assert summary["aborted"] == 0
        assert summary["failed"] == 1
        # Dst was still cleaned up because the action did not succeed.
        assert not (domain / "policy_facets" / "computations" / "a.md.yaml").exists()


def test_finalize_v3_schema_aborts():
    """Error path: v3.0.0-schema plan tmp (non-empty succeeded array) is rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        # Hand-mutate the plan tmp to look like the v3.0.0 schema (with a succeeded array).
        plan_path = domain / "policy_facets" / ".extract-plan.tmp"
        plan = json.loads(plan_path.read_text())
        plan["succeeded"] = ["input/policy_docs/a.md"]
        plan["failed"] = []
        plan_path.write_text(json.dumps(plan, indent=2))

        try:
            extract_computations.cmd_finalize(domain)
        except RuntimeError as exc:
            assert "v3.0.0 schema" in str(exc)
            assert "Delete" in str(exc)
        else:
            raise AssertionError("expected RuntimeError on v3.0.0-schema plan tmp")


def test_plan_clears_stale_marker_dir():
    """Crash recovery: pre-existing .extract-plan.d/ from a prior crashed run is wiped by --plan."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        # Simulate a stale marker dir from a prior crashed run.
        stale_dir = domain / _PLAN_TMP_DIR
        stale_dir.mkdir(parents=True, exist_ok=True)
        stale_marker = stale_dir / "input" / "policy_docs" / "stale-source.md.outcome.json"
        stale_marker.parent.mkdir(parents=True, exist_ok=True)
        stale_marker.write_text('{"src": "stale", "status": "succeeded"}')

        extract_computations.cmd_plan(domain)

        # Stale marker is gone; marker dir exists but is empty (or contains no leftovers).
        assert stale_dir.is_dir()
        assert not stale_marker.exists()
        leftovers = list(stale_dir.rglob("*.outcome.json"))
        assert leftovers == []


def test_finalize_cleans_up_marker_dir():
    """Happy path: --finalize removes the .extract-plan.d/ directory after collation."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write_doc(domain, "a.md")
        _git_init_and_commit(domain)

        extract_computations.cmd_plan(domain)
        _write_dst(domain, "a.md")
        _mark_succeeded(domain, ["input/policy_docs/a.md"])
        extract_computations.cmd_finalize(domain)

        assert not (domain / _PLAN_TMP_DIR).exists()


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
