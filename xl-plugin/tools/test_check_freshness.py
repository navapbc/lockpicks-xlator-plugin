# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for check_freshness.py — covers facets/guidance/catala/tests detection and edge cases.

Run: uv run xl-plugin/tools/test_check_freshness.py
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

import check_freshness  # noqa: E402
import record_tier_manifest  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "input" / "policy_docs").mkdir(parents=True)
    return domain


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


def _git_sha_of(domain: Path, rel: str) -> str:
    result = subprocess.run(
        ["git", "hash-object", str(domain / rel)],
        cwd=domain, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _write(domain: Path, rel: str, content: str) -> Path:
    p = domain / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _write_index(domain: Path, entries: dict[str, dict]) -> None:
    """Write policy_facets/input-index.yaml with the given entries."""
    p = domain / "policy_facets" / "input-index.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"files": entries}, sort_keys=False))


def _populate_full_chain(domain: Path, *, with_manifests: bool = True) -> dict[str, str]:
    """Build a domain with every tier present and (optionally) manifests recorded.

    Returns a map of useful path -> SHA for tests to assert against.
    """
    _write(domain, "input/policy_docs/foo.md", "foo content")
    _write(domain, "input/policy_docs/sub/bar.md", "bar content")
    _write(domain, "policy_facets/compressed/foo.md", "foo compressed")
    _write(domain, "policy_facets/compressed/sub/bar.md", "bar compressed")
    _write(domain, "policy_facets/computations/foo.md.yaml", "sections: []\n")
    _write(domain, "policy_facets/computations/sub/bar.md.yaml", "sections: []\n")
    _write(domain, "specs/guidance/skeleton.yaml", "computations: []\n")
    _write(domain, "specs/guidance/ruleset-modules.yaml", "ruleset_modules: []\n")
    _write(domain, "specs/naming-manifest.yaml", "entries: {}\n")
    _write(domain, "specs/eligibility.catala_en", "> Module Eligibility\n")
    _write(domain, "specs/tests/eligibility_tests.yaml", "tests: []\n")
    _git_init_and_commit(domain)

    # Index after git init so SHAs are stable.
    foo_sha = _git_sha_of(domain, "input/policy_docs/foo.md")
    bar_sha = _git_sha_of(domain, "input/policy_docs/sub/bar.md")
    _write_index(domain, {
        "input/policy_docs/foo.md": {"sha": foo_sha, "md_quality": {"score": 100}},
        "input/policy_docs/sub/bar.md": {"sha": bar_sha, "md_quality": {"score": 100}},
    })

    if with_manifests:
        record_tier_manifest.cmd_record(domain, "guidance")
        # catala tier: hand-author extraction-manifest with consumed_guidance[]
        sk_sha = _git_sha_of(domain, "specs/guidance/skeleton.yaml")
        rm_sha = _git_sha_of(domain, "specs/guidance/ruleset-modules.yaml")
        nm_sha = _git_sha_of(domain, "specs/naming-manifest.yaml")
        _write(domain, "specs/extraction-manifest.yaml", yaml.safe_dump({
            "programs": {
                "eligibility": {
                    "catala_file": "specs/eligibility.catala_en",
                    "source_docs": [
                        {"path": "input/policy_docs/foo.md", "git_sha": foo_sha},
                    ],
                    "consumed_guidance": [
                        {"path": "specs/guidance/skeleton.yaml", "sha": sk_sha},
                        {"path": "specs/guidance/ruleset-modules.yaml", "sha": rm_sha},
                        {"path": "specs/naming-manifest.yaml", "sha": nm_sha},
                    ],
                }
            }
        }, sort_keys=False))
        record_tier_manifest.cmd_record(domain, "tests")

    return {"foo_sha": foo_sha, "bar_sha": bar_sha}


def _categories(records, tier: str | None = None) -> list[tuple[str, str, str]]:
    """Return [(tier, category, path), ...] for inspection."""
    out = []
    for r in records:
        if tier is None or r.tier == tier:
            out.append((r.tier, r.category, r.path))
    return out


def _stdout_records(stdout: str) -> list[tuple[str, ...]]:
    """Tokenize non-blank, non-summary stdout lines into split-tuples.

    Robust to column-alignment padding: any whitespace counts as a separator.
    """
    return [
        tuple(line.split())
        for line in stdout.splitlines()
        if line.strip() and not line.startswith("summary")
    ]


# ---------------------------------------------------------------------------
# facets tier
# ---------------------------------------------------------------------------

def test_facets_happy_path_no_drift():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        records, counts = check_freshness.cmd_check(domain)
        assert counts["facets"] == 0, _categories(records, "facets")


def test_facets_source_edited():
    """AE1: edit a policy doc after indexing -> facets source_edited."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        # Edit foo.md after the index recorded its SHA.
        _write(domain, "input/policy_docs/foo.md", "EDITED content")
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert ("facets", "source_edited", "input/policy_docs/foo.md") in cats


def test_facets_source_added():
    """AE2: new file not in index -> facets source_added."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "input/policy_docs/new.md", "new content")
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert ("facets", "source_added", "input/policy_docs/new.md") in cats


def test_facets_source_removed():
    """Index entry present, file missing -> facets source_removed."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        (domain / "input/policy_docs/foo.md").unlink()
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert ("facets", "source_removed", "input/policy_docs/foo.md") in cats


def test_facets_derived_missing():
    """AE3: compressed counterpart deleted -> facets derived_missing."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        (domain / "policy_facets/compressed/foo.md").unlink()
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert ("facets", "derived_missing", "policy_facets/compressed/foo.md") in cats


def test_facets_orphan_derived():
    """Compressed file with no live source -> facets orphan_derived."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "policy_facets/compressed/orphan.md", "orphan content")
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert ("facets", "orphan_derived", "policy_facets/compressed/orphan.md") in cats


def test_facets_index_missing():
    """No input-index.yaml -> facets index_missing, exit 1."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write(domain, "input/policy_docs/foo.md", "foo")
        _git_init_and_commit(domain)
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert any(c == "index_missing" for _, c, _ in cats)
        assert counts["facets"] >= 1


def test_facets_rejected_source_skipped():
    """AE8: md_quality.score < 40, source moved to input/rejected/ -> no drift."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        # foo.md is rejected: in index with low score, lives at input/rejected/foo.md
        _write(domain, "input/rejected/foo.md", "rejected content")
        _git_init_and_commit(domain)
        foo_sha = _git_sha_of(domain, "input/rejected/foo.md")
        _write_index(domain, {
            "input/policy_docs/foo.md": {"sha": foo_sha, "md_quality": {"score": 30}},
        })
        records, _ = check_freshness.cmd_check(domain)
        # No drift for foo.md: not flagged as source_removed (it's rejected) and
        # not flagged as source_added (it's still in the index).
        cats = _categories(records, "facets")
        paths = [p for _, _, p in cats]
        assert "input/policy_docs/foo.md" not in paths
        assert "input/rejected/foo.md" not in paths


def test_facets_untracked_sha_skipped():
    """Index sha='untracked' -> comparison skipped, no spurious drift."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write(domain, "input/policy_docs/foo.md", "foo")
        _write(domain, "policy_facets/compressed/foo.md", "compressed")
        _write(domain, "policy_facets/computations/foo.md.yaml", "sections: []\n")
        _write_index(domain, {
            "input/policy_docs/foo.md": {"sha": "untracked", "md_quality": {"score": 100}},
        })
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "facets")
        assert all(c != "source_edited" for _, c, _ in cats)


def test_facets_git_unavailable_signal():
    """Mock subprocess.run to fail -> facets git_unavailable record, exit 1."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "input/policy_docs/foo.md", "EDITED")  # force a comparison

        with mock.patch.object(check_freshness, "subprocess") as mock_sub:
            mock_sub.run.side_effect = OSError("git not found")
            records, counts = check_freshness.cmd_check(domain)

        cats = _categories(records, "facets")
        assert any(c == "git_unavailable" for _, c, _ in cats)
        assert counts["facets"] >= 1


# ---------------------------------------------------------------------------
# guidance tier
# ---------------------------------------------------------------------------

def test_guidance_guidance_stale_when_facets_changes():
    """AE4: edit computations/foo.md.yaml after manifest -> guidance guidance_stale."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "policy_facets/computations/foo.md.yaml", "sections: [edited]\n")
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "guidance")
        assert ("guidance", "guidance_stale", "policy_facets/computations/foo.md.yaml") in cats


def test_guidance_manifest_missing_when_guidance_present():
    """AE14: guidance files exist, no .facets-manifest.yaml -> guidance_manifest_missing."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain, with_manifests=False)
        # Don't write the guidance manifest.
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "guidance")
        assert any(c == "guidance_manifest_missing" for _, c, _ in cats)


def test_guidance_no_guidance_no_manifest_no_drift():
    """No guidance files and no manifest -> no guidance-tier drift."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _write(domain, "input/policy_docs/foo.md", "foo")
        _git_init_and_commit(domain)
        foo_sha = _git_sha_of(domain, "input/policy_docs/foo.md")
        _write_index(domain, {
            "input/policy_docs/foo.md": {"sha": foo_sha, "md_quality": {"score": 100}},
        })
        # No specs/guidance/, no facets-manifest.
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "guidance")
        # Empty — no manifest-missing emitted because guidance tier has no outputs.
        assert cats == []


# ---------------------------------------------------------------------------
# catala tier
# ---------------------------------------------------------------------------

def test_catala_catala_stale_when_guidance_changes():
    """AE5: edit specs/guidance/skeleton.yaml after extraction -> catala catala_stale."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "specs/guidance/skeleton.yaml", "computations: [edited]\n")
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "catala")
        assert ("catala", "catala_stale", "specs/guidance/skeleton.yaml") in cats


def test_catala_manifest_missing_when_catala_present():
    """AE15: catala files exist, no consumed_guidance[] -> catala_manifest_missing."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain, with_manifests=False)
        # Catala source exists (populated by _populate_full_chain), but no extraction-manifest.
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "catala")
        assert any(c == "catala_manifest_missing" for _, c, _ in cats)


def test_catala_dedup_across_sub_modules():
    """Same guidance file in program + sub-module -> single catala_stale record."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        # Rewrite extraction-manifest with same path in program AND sub-module.
        sk_sha = _git_sha_of(domain, "specs/guidance/skeleton.yaml")
        _write(domain, "specs/extraction-manifest.yaml", yaml.safe_dump({
            "programs": {
                "eligibility": {
                    "catala_file": "specs/eligibility.catala_en",
                    "consumed_guidance": [
                        {"path": "specs/guidance/skeleton.yaml", "sha": sk_sha},
                    ],
                    "sub_modules": [
                        {
                            "name": "submod",
                            "consumed_guidance": [
                                {"path": "specs/guidance/skeleton.yaml", "sha": sk_sha},
                            ],
                        }
                    ],
                }
            }
        }, sort_keys=False))
        # Force drift on the shared path.
        _write(domain, "specs/guidance/skeleton.yaml", "EDITED")
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "catala")
        # Expect exactly one catala_stale for skeleton.yaml, not two.
        stale_entries = [c for c in cats if c[1] == "catala_stale"]
        assert len(stale_entries) == 1, stale_entries


# ---------------------------------------------------------------------------
# tests tier
# ---------------------------------------------------------------------------

def test_tests_tests_stale_when_catala_changes():
    """AE6: regenerate catala source after tests -> tests tests_stale."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "specs/eligibility.catala_en", "> Module Eligibility\n# edited\n")
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "tests")
        assert ("tests", "tests_stale", "specs/eligibility.catala_en") in cats


def test_tests_manifest_missing_when_tests_present():
    """AE16: tests dir non-empty, no .catala-manifest.yaml -> tests_manifest_missing."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain, with_manifests=False)
        records, _ = check_freshness.cmd_check(domain)
        cats = _categories(records, "tests")
        assert any(c == "tests_manifest_missing" for _, c, _ in cats)


def test_tests_not_applicable_for_empty_tests_dir():
    """AE7: catala source exists, empty specs/tests/ -> not_applicable."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain, with_manifests=False)
        # Remove tests file to make dir effectively empty.
        (domain / "specs/tests/eligibility_tests.yaml").unlink()
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "tests")
        assert any(c == "not_applicable" for _, c, _ in cats)
        # not_applicable is informational; tests count is 0.
        assert counts["tests"] == 0


def test_tests_not_applicable_for_absent_tests_dir():
    """AE7 variant: tests dir doesn't exist at all -> not_applicable."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain, with_manifests=False)
        # Wipe tests entirely.
        import shutil
        shutil.rmtree(domain / "specs/tests")
        records, counts = check_freshness.cmd_check(domain)
        cats = _categories(records, "tests")
        assert any(c == "not_applicable" for _, c, _ in cats)
        assert counts["tests"] == 0


# ---------------------------------------------------------------------------
# Composition + summary
# ---------------------------------------------------------------------------

def test_full_chain_happy_path_exits_zero(monkeypatch_env=None):
    """AE9: every tier in sync -> summary all-zero."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _, counts = check_freshness.cmd_check(domain)
        assert counts == {"facets": 0, "guidance": 0, "catala": 0, "tests": 0}


def test_multi_tier_drift_composition():
    """Both tier-1 source edit AND tier-2 stale -> both records emitted."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "input/policy_docs/foo.md", "EDITED")
        _write(domain, "policy_facets/computations/foo.md.yaml", "EDITED\n")
        records, counts = check_freshness.cmd_check(domain)
        assert counts["facets"] >= 1
        assert counts["guidance"] >= 1


# ---------------------------------------------------------------------------
# Argparse / main error paths
# ---------------------------------------------------------------------------

def _run_main_with(argv: list[str], env: dict[str, str] | None = None,
                   stdin: str | None = None) -> tuple[int, str, str]:
    tool = Path(__file__).parent / "check_freshness.py"
    result = subprocess.run(
        ["uv", "run", str(tool), *argv],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        input=stdin,
    )
    return result.returncode, result.stdout, result.stderr


def test_main_domains_fullpath_unset_exits_2():
    tool = Path(__file__).parent / "check_freshness.py"
    clean_env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    result = subprocess.run(
        ["uv", "run", str(tool), "mydomain"],
        capture_output=True, text=True, env=clean_env,
    )
    assert result.returncode == 2
    assert "DOMAINS_FULLPATH" in result.stderr


def test_main_domain_dir_missing_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        code, _stdout, stderr = _run_main_with(
            ["does_not_exist"], env={"DOMAINS_FULLPATH": tmp}
        )
        assert code == 2
        assert "not found" in stderr.lower() or "does_not_exist" in stderr


def test_main_happy_path_exits_0_with_summary():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        code, stdout, _stderr = _run_main_with(
            ["test_dom"], env={"DOMAINS_FULLPATH": tmp},
        )
        assert code == 0, stdout
        assert "summary facets=0 guidance=0 catala=0 tests=0" in stdout


def test_main_drift_exits_1():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _make_domain(Path(tmp))
        _populate_full_chain(domain)
        _write(domain, "input/policy_docs/foo.md", "EDITED")
        code, stdout, _stderr = _run_main_with(
            ["test_dom"], env={"DOMAINS_FULLPATH": tmp},
        )
        assert code == 1
        assert ("facets", "source_edited", "input/policy_docs/foo.md") in _stdout_records(stdout)
        assert "summary facets=" in stdout


def test_main_no_arg_menu_selects_by_number():
    """AE10: invoke with no <domain> -> numbered menu lists domains, user picks."""
    with tempfile.TemporaryDirectory() as tmp:
        # Two valid domains under tmp.
        for name in ("alpha", "beta"):
            (Path(tmp) / name / "input" / "policy_docs").mkdir(parents=True)
        code, stdout, stderr = _run_main_with(
            [], env={"DOMAINS_FULLPATH": tmp}, stdin="1\n",
        )
        # Either drift or clean — but it should have proceeded past the menu.
        # The menu itself goes to stderr; stdout has the summary line.
        assert "Available domains:" in stderr
        assert "alpha" in stderr and "beta" in stderr
        assert "summary" in stdout


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
