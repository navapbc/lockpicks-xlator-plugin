#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator check-freshness: pull-based freshness check across the full xlator
derivation chain for a domain.

Traverses four tiers and compares current working-tree SHAs against per-tier
recorded manifests; emits categorized drift records to stdout and exits
non-zero when any drift or degraded-environment signal is detected.

Tier names match the downstream artifact each tier produces (mirroring the
`--tier` flag in record_tier_manifest.py):

facets:   input/policy_docs/ vs policy_facets/input-index.yaml.files.<path>.sha
guidance: policy_facets/* vs specs/guidance/.facets-manifest.yaml
civil:    specs/guidance/* + specs/naming-manifest.yaml vs
          specs/extraction-manifest.yaml.programs.*.consumed_guidance[].sha
          (dedup'd across program + sub_modules)
tests:    specs/*.civil.yaml vs specs/tests/.civil-manifest.yaml

Usage:
    xlator check-freshness [<domain>]

If <domain> is omitted, an interactive numbered menu lists all directories
matching $DOMAINS_DIR/*/input/policy_docs/ and prompts the user to choose.

Output (stdout, line-stable, machine-parseable):
    <tier> <category> <path>
    ...
    summary facets=<n> guidance=<n> civil=<n> tests=<n>

Categories emitted per tier:
    facets:   source_edited, source_added, source_removed, derived_missing,
              orphan_derived, index_missing, git_unavailable
    guidance: guidance_stale, guidance_manifest_missing, git_unavailable
    civil:    civil_stale, civil_manifest_missing, git_unavailable
    tests:    tests_stale, tests_manifest_missing, not_applicable, git_unavailable

Exit codes:
    0 — no drift records, no git_unavailable records (everything fresh)
    1 — any drift category appears, OR git_unavailable appears (degraded mode)
    2 — environment/usage error (DOMAINS_FULLPATH unset, domain missing)

`not_applicable` (empty/absent specs/tests/) is informational; it appears in
the per-line output but does not increment the summary counter and does not
affect the exit code.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml


_INPUT_POLICY_DOCS = "input/policy_docs"
_INPUT_REJECTED = "input/rejected"
_INPUT_INDEX = "policy_facets/input-index.yaml"
_COMPRESSED = "policy_facets/compressed"
_COMPUTATIONS = "policy_facets/computations"
_GUIDANCE = "specs/guidance"
_NAMING_MANIFEST = "specs/naming-manifest.yaml"
_EXTRACTION_MANIFEST = "specs/extraction-manifest.yaml"
_TESTS = "specs/tests"
_GUIDANCE_MANIFEST = "specs/guidance/.facets-manifest.yaml"
_CIVIL_MANIFEST = "specs/tests/.civil-manifest.yaml"

_REJECTED_SCORE_THRESHOLD = 40


# ---------------------------------------------------------------------------
# Drift record + emit helpers
# ---------------------------------------------------------------------------

class DriftRecord:
    __slots__ = ("tier", "category", "path")

    def __init__(self, tier: str, category: str, path: str) -> None:
        self.tier = tier
        self.category = category
        self.path = path

    def render(self) -> str:
        return f"{self.tier} {self.category} {self.path}"


def _git_sha(domain_dir: Path, path: Path) -> str:
    """Compute working-tree blob SHA via `git hash-object <path>`.

    Returns 'untracked' when git or hash-object cannot run. Mirrors the
    SP-LoadInputIndex fallback contract.
    """
    try:
        result = subprocess.run(
            ["git", "hash-object", str(path)],
            cwd=str(domain_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "untracked"
    sha = result.stdout.strip()
    return sha or "untracked"


# ---------------------------------------------------------------------------
# facets tier: input/policy_docs/ vs policy_facets/input-index.yaml
# ---------------------------------------------------------------------------

def _check_facets(domain_dir: Path) -> tuple[list[DriftRecord], bool]:
    """Detect drift between policy docs and policy_facets/.

    Returns (records, git_unavailable_flag).
    """
    records: list[DriftRecord] = []
    git_unavailable = False

    index_path = domain_dir / _INPUT_INDEX
    if not index_path.is_file():
        records.append(DriftRecord("facets", "index_missing", _INPUT_INDEX))
        return records, git_unavailable

    try:
        index_data = yaml.safe_load(index_path.read_text()) or {}
    except yaml.YAMLError:
        records.append(DriftRecord("facets", "index_missing", _INPUT_INDEX))
        return records, git_unavailable

    indexed_files: dict[str, dict] = index_data.get("files") or {}

    # Build set of indexed paths whose md_quality is acceptable (>= threshold or absent).
    # Rejected entries (score < threshold) have been moved to input/rejected/ — do not
    # check them at input/policy_docs/.
    eligible_indexed: dict[str, str] = {}
    for key, entry in indexed_files.items():
        if not isinstance(entry, dict):
            continue
        mq = entry.get("md_quality")
        if isinstance(mq, dict):
            score = mq.get("score")
            if isinstance(score, int) and score < _REJECTED_SCORE_THRESHOLD:
                continue  # rejected — skip
        sha = entry.get("sha")
        if isinstance(sha, str):
            eligible_indexed[key] = sha

    # Compare each eligible indexed entry to current working tree.
    for index_key, indexed_sha in eligible_indexed.items():
        # index_key is e.g. "input/policy_docs/foo.md".
        source_path = domain_dir / index_key
        if not source_path.is_file():
            records.append(DriftRecord("facets", "source_removed", index_key))
            continue
        if indexed_sha == "untracked":
            # SP-LoadInputIndex contract: skip comparison.
            pass
        else:
            current_sha = _git_sha(domain_dir, source_path)
            if current_sha == "untracked":
                git_unavailable = True
            elif current_sha != indexed_sha:
                records.append(DriftRecord("facets", "source_edited", index_key))

        # Check derived counterparts for this source.
        # index_key = "input/policy_docs/<rel>" -> rel relative to that root
        rel = Path(index_key).relative_to(_INPUT_POLICY_DOCS).as_posix()
        compressed_path = domain_dir / _COMPRESSED / rel
        comp_rel = f"{_COMPRESSED}/{rel}"
        if not compressed_path.is_file():
            records.append(DriftRecord("facets", "derived_missing", comp_rel))
        computations_rel = f"{_COMPUTATIONS}/{rel}.yaml"
        if not (domain_dir / computations_rel).is_file():
            records.append(DriftRecord("facets", "derived_missing", computations_rel))

    # Enumerate live sources; flag any not in the (full, including rejected) index
    # as source_added. Use the full `indexed_files` set here so that rejected
    # sources (which sit in input/rejected/ not input/policy_docs/) are not
    # spuriously flagged as `source_added`.
    docs_root = domain_dir / _INPUT_POLICY_DOCS
    if docs_root.is_dir():
        for md_path in sorted(docs_root.rglob("*.md")):
            rel_key = md_path.relative_to(domain_dir).as_posix()
            if rel_key not in indexed_files:
                records.append(DriftRecord("facets", "source_added", rel_key))

    # Enumerate derived files; flag any whose source no longer exists.
    compressed_root = domain_dir / _COMPRESSED
    if compressed_root.is_dir():
        for derived in sorted(compressed_root.rglob("*.md")):
            rel = derived.relative_to(compressed_root).as_posix()
            if not (domain_dir / _INPUT_POLICY_DOCS / rel).is_file():
                # also check rejected location
                if not (domain_dir / _INPUT_REJECTED / rel).is_file():
                    records.append(
                        DriftRecord("facets", "orphan_derived", f"{_COMPRESSED}/{rel}")
                    )
    computations_root = domain_dir / _COMPUTATIONS
    if computations_root.is_dir():
        for derived in sorted(computations_root.rglob("*.md.yaml")):
            rel = derived.relative_to(computations_root).as_posix()
            source_rel = rel[: -len(".yaml")]  # strip .yaml -> "<rel>.md"
            if not (domain_dir / _INPUT_POLICY_DOCS / source_rel).is_file():
                if not (domain_dir / _INPUT_REJECTED / source_rel).is_file():
                    records.append(
                        DriftRecord(
                            "facets",
                            "orphan_derived",
                            f"{_COMPUTATIONS}/{rel}",
                        )
                    )

    if git_unavailable:
        records.append(DriftRecord("facets", "git_unavailable", "git hash-object failed"))

    return records, git_unavailable


# ---------------------------------------------------------------------------
# guidance tier: policy_facets/* vs specs/guidance/.facets-manifest.yaml
# ---------------------------------------------------------------------------

def _guidance_tier_has_outputs(domain_dir: Path) -> bool:
    guidance = domain_dir / _GUIDANCE
    if not guidance.is_dir():
        return False
    # any *.yaml directly under specs/guidance/ counts as tier output
    return any(guidance.glob("*.yaml"))


def _check_guidance(domain_dir: Path) -> tuple[list[DriftRecord], bool]:
    records: list[DriftRecord] = []
    git_unavailable = False

    manifest_path = domain_dir / _GUIDANCE_MANIFEST
    if not manifest_path.is_file():
        if _guidance_tier_has_outputs(domain_dir):
            records.append(
                DriftRecord("guidance", "guidance_manifest_missing", _GUIDANCE_MANIFEST)
            )
        return records, git_unavailable

    try:
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError:
        records.append(
            DriftRecord("guidance", "guidance_manifest_missing", _GUIDANCE_MANIFEST)
        )
        return records, git_unavailable

    files_map: dict[str, str] = manifest.get("files") or {}
    for rel, recorded_sha in files_map.items():
        path = domain_dir / rel
        if not path.is_file():
            # Upstream file deleted since manifest was written — treat as stale.
            records.append(DriftRecord("guidance", "guidance_stale", rel))
            continue
        if recorded_sha == "untracked":
            continue
        current = _git_sha(domain_dir, path)
        if current == "untracked":
            git_unavailable = True
        elif current != recorded_sha:
            records.append(DriftRecord("guidance", "guidance_stale", rel))

    if git_unavailable:
        records.append(DriftRecord("guidance", "git_unavailable", "git hash-object failed"))

    return records, git_unavailable


# ---------------------------------------------------------------------------
# civil tier: specs/guidance/* + naming-manifest vs extraction-manifest consumed_guidance[]
# ---------------------------------------------------------------------------

def _civil_files_exist(domain_dir: Path) -> bool:
    specs = domain_dir / "specs"
    if not specs.is_dir():
        return False
    return any(specs.glob("*.civil.yaml"))


def _iter_consumed_guidance(manifest: dict) -> Iterable[dict]:
    """Yield every consumed_guidance[] entry across programs and sub_modules.

    Each yielded item is the {path, sha} dict from the manifest.
    """
    programs = manifest.get("programs") or {}
    if not isinstance(programs, dict):
        return
    for prog in programs.values():
        if not isinstance(prog, dict):
            continue
        for entry in prog.get("consumed_guidance") or []:
            if isinstance(entry, dict):
                yield entry
        for sub in prog.get("sub_modules") or []:
            if not isinstance(sub, dict):
                continue
            for entry in sub.get("consumed_guidance") or []:
                if isinstance(entry, dict):
                    yield entry


def _check_civil(domain_dir: Path) -> tuple[list[DriftRecord], bool]:
    records: list[DriftRecord] = []
    git_unavailable = False

    manifest_path = domain_dir / _EXTRACTION_MANIFEST
    civil_present = _civil_files_exist(domain_dir)

    if not manifest_path.is_file():
        if civil_present:
            records.append(
                DriftRecord("civil", "civil_manifest_missing", _EXTRACTION_MANIFEST)
            )
        return records, git_unavailable

    try:
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError:
        if civil_present:
            records.append(
                DriftRecord("civil", "civil_manifest_missing", _EXTRACTION_MANIFEST)
            )
        return records, git_unavailable

    # Collect dedup'd {path: recorded_sha} across all programs and sub-modules.
    # If the same path appears with different SHAs (cross-program inconsistency),
    # treat the first occurrence as canonical and stop scanning that path —
    # the freshness check is asking "is upstream consistent with what was recorded",
    # not "are all consumers internally consistent."
    consumed: dict[str, str] = {}
    for entry in _iter_consumed_guidance(manifest):
        path = entry.get("path")
        sha = entry.get("sha")
        if isinstance(path, str) and isinstance(sha, str):
            consumed.setdefault(path, sha)

    if civil_present and not consumed:
        records.append(
            DriftRecord("civil", "civil_manifest_missing", _EXTRACTION_MANIFEST)
        )
        return records, git_unavailable

    for rel, recorded_sha in sorted(consumed.items()):
        path = domain_dir / rel
        if not path.is_file():
            records.append(DriftRecord("civil", "civil_stale", rel))
            continue
        if recorded_sha == "untracked":
            continue
        current = _git_sha(domain_dir, path)
        if current == "untracked":
            git_unavailable = True
        elif current != recorded_sha:
            records.append(DriftRecord("civil", "civil_stale", rel))

    if git_unavailable:
        records.append(DriftRecord("civil", "git_unavailable", "git hash-object failed"))

    return records, git_unavailable


# ---------------------------------------------------------------------------
# tests tier: specs/*.civil.yaml vs specs/tests/.civil-manifest.yaml
# ---------------------------------------------------------------------------

def _tests_tier_has_outputs(domain_dir: Path) -> bool:
    tests = domain_dir / _TESTS
    if not tests.is_dir():
        return False
    return any(p for p in tests.rglob("*") if p.is_file() and not p.name.startswith("."))


def _check_tests(domain_dir: Path) -> tuple[list[DriftRecord], bool]:
    records: list[DriftRecord] = []
    git_unavailable = False

    if not _tests_tier_has_outputs(domain_dir):
        records.append(DriftRecord("tests", "not_applicable", _TESTS))
        return records, git_unavailable

    manifest_path = domain_dir / _CIVIL_MANIFEST
    if not manifest_path.is_file():
        records.append(DriftRecord("tests", "tests_manifest_missing", _CIVIL_MANIFEST))
        return records, git_unavailable

    try:
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError:
        records.append(DriftRecord("tests", "tests_manifest_missing", _CIVIL_MANIFEST))
        return records, git_unavailable

    files_map: dict[str, str] = manifest.get("files") or {}
    for rel, recorded_sha in files_map.items():
        path = domain_dir / rel
        if not path.is_file():
            records.append(DriftRecord("tests", "tests_stale", rel))
            continue
        if recorded_sha == "untracked":
            continue
        current = _git_sha(domain_dir, path)
        if current == "untracked":
            git_unavailable = True
        elif current != recorded_sha:
            records.append(DriftRecord("tests", "tests_stale", rel))

    if git_unavailable:
        records.append(DriftRecord("tests", "git_unavailable", "git hash-object failed"))

    return records, git_unavailable


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def cmd_check(domain_dir: Path) -> tuple[list[DriftRecord], dict[str, int]]:
    """Run all four tier checks; return all records and per-tier drift counts.

    Counts include *_stale, *_manifest_missing, *_missing, orphan_*, source_*,
    and git_unavailable categories. `not_applicable` is informational and is
    NOT counted.
    """
    all_records: list[DriftRecord] = []
    for checker in (_check_facets, _check_guidance, _check_civil, _check_tests):
        records, _ = checker(domain_dir)
        all_records.extend(records)

    counts = {"facets": 0, "guidance": 0, "civil": 0, "tests": 0}
    for rec in all_records:
        if rec.category == "not_applicable":
            continue
        counts[rec.tier] = counts.get(rec.tier, 0) + 1
    return all_records, counts


def _list_domains(domains_root: Path) -> list[str]:
    """Return sorted list of <domain> names matching $DOMAINS_DIR/*/input/policy_docs/."""
    if not domains_root.is_dir():
        return []
    domains = []
    for entry in sorted(domains_root.iterdir()):
        if entry.is_dir() and (entry / _INPUT_POLICY_DOCS).is_dir():
            domains.append(entry.name)
    return domains


def _prompt_for_domain(domains_root: Path) -> str:
    domains = _list_domains(domains_root)
    if not domains:
        print(
            f"Error: no domains found under {domains_root} (expected "
            "<domain>/input/policy_docs/ directories).",
            file=sys.stderr,
        )
        sys.exit(2)
    print("Available domains:", file=sys.stderr)
    for i, name in enumerate(domains, 1):
        print(f"  {i}. {name}", file=sys.stderr)
    print("Which domain? Enter a number or domain name: ", end="", file=sys.stderr)
    sys.stderr.flush()
    choice = sys.stdin.readline().strip()
    if not choice:
        print("Error: no selection.", file=sys.stderr)
        sys.exit(2)
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(domains):
            return domains[idx]
        print(f"Error: invalid choice: {choice}", file=sys.stderr)
        sys.exit(2)
    if choice in domains:
        return choice
    print(f"Error: domain not found: {choice}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check freshness across the full xlator derivation chain "
            "(policy_facets -> guidance -> civil -> tests) and emit per-tier "
            "drift records. Exits 1 on any drift or degraded-environment "
            "signal; 0 only when every tier is fresh."
        )
    )
    parser.add_argument(
        "domain",
        nargs="?",
        help="Domain name (e.g. snap, ak_doh). If omitted, an interactive menu "
        "lists all domains under $DOMAINS_DIR.",
    )
    args = parser.parse_args()

    domains_root_str = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root_str:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(2)
    domains_root = Path(domains_root_str)

    domain = args.domain
    if not domain:
        domain = _prompt_for_domain(domains_root)

    domain_dir = domains_root / domain
    if not domain_dir.is_dir():
        print(f"Error: Domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)

    records, counts = cmd_check(domain_dir)

    for rec in records:
        print(rec.render())
    print(
        f"summary facets={counts['facets']} guidance={counts['guidance']} "
        f"civil={counts['civil']} tests={counts['tests']}"
    )

    total_drift = sum(counts.values())
    sys.exit(1 if total_drift > 0 else 0)


if __name__ == "__main__":
    main()
