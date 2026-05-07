#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator compress-inputs: maintain <domain>/policy_facets/compressed/ as a
caveman-compressed mirror of <domain>/input/policy_docs/.

This tool handles the non-AI half of /compress-inputs: bootstrap, file
enumeration, copy, manifest read/write, mirror-deletes, and *.original.md
cleanup. The orchestrating skill invokes the AI half (caveman /compress
per file) between --plan and --finalize.

Usage:
    xlator compress-inputs <domain> --plan
    xlator compress-inputs <domain> --finalize

--plan:
  - Bootstrap: mkdir policy_facets/ if absent; move
    specs/input-{index,sections}.yaml into policy_facets/ if applicable.
  - Defensive sweep: remove any stray *.original.md files under compressed/.
  - Enumerate eligible source files (.md only for v1; sensitive paths skipped).
  - Compute a work plan {to_compress, to_delete, noop, skipped} by comparing
    each source file's git SHA against the manifest's recorded SHA.
  - Copy each to_compress source to its compressed/ destination.
  - Write the work plan + an empty `succeeded`/`failed` list to a transient
    file at policy_facets/.compress-plan.tmp. The skill mutates these lists
    as it processes each file.
  - Emit the work plan as JSON on stdout.

--finalize:
  - Read .compress-plan.tmp; abort if absent.
  - Walk compressed/ and delete every *.original.md backup.
  - For to_compress entries NOT in `succeeded`, also delete the (uncompressed)
    copy at the destination so the next run reattempts it.
  - Apply mirror-deletes from to_delete and prune their manifest entries.
  - For each `succeeded` entry, write {source_path: source_sha} into the
    manifest using atomic write (tmp + os.replace).
  - Remove .compress-plan.tmp.
  - Emit a summary line on stdout.

Output (JSON, --plan only):
    {
      "to_compress":     [ {src, dst, source_sha}, ... ],
      "to_delete":       [ "policy_facets/compressed/<rel>.md", ... ],
      "noop":            [ {src, reason: "unchanged"}, ... ],
      "skipped":         [ {src, reason: "sensitive_path"|"not_eligible"}, ... ],
      "bootstrap_moved": [ "specs/input-index.yaml -> policy_facets/...", ... ]
    }

Exit codes:
    0 — success
    1 — error (missing domain, conflicting bootstrap state, corrupt plan, ...)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


_POLICY_DOCS = "input/policy_docs"
_POLICY_FACETS = "policy_facets"
_COMPRESSED = "policy_facets/compressed"
_MANIFEST = "policy_facets/.compress-manifest.yaml"
_PLAN_TMP = "policy_facets/.compress-plan.tmp"

_INDEX_FILE = "input-index.yaml"
_SECTIONS_FILE = "input-sections.yaml"

_ELIGIBLE_SUFFIXES = {".md"}
_SENSITIVE_PATTERN = re.compile(
    r"(secret|credential|password|api[_-]?key|token|private[_-]?key)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap(domain_dir: Path) -> list[str]:
    """Ensure policy_facets/ exists and move legacy index files into it.

    Returns a list of human-readable move records ("from -> to").
    Raises RuntimeError on conflict (both source and destination exist).
    """
    moved: list[str] = []
    facets = domain_dir / _POLICY_FACETS
    facets.mkdir(parents=True, exist_ok=True)

    for filename in (_INDEX_FILE, _SECTIONS_FILE):
        legacy = domain_dir / "specs" / filename
        new = domain_dir / _POLICY_FACETS / filename
        if not legacy.exists():
            continue
        if new.exists():
            raise RuntimeError(
                f"Conflicting index files: both {legacy.relative_to(domain_dir)} "
                f"and {new.relative_to(domain_dir)} exist. Resolve manually before "
                f"running /compress-inputs."
            )
        legacy.rename(new)
        moved.append(f"{legacy.relative_to(domain_dir)} -> {new.relative_to(domain_dir)}")

    return moved


def sweep_stale_backups(domain_dir: Path) -> int:
    """Remove any *.original.md files under policy_facets/compressed/.

    Caveman /compress refuses to compress when the sibling backup already
    exists (it errors with 'data loss' guard). A previous crashed run can
    leave these behind; sweep them defensively before any compression.
    Returns the count of removed files.
    """
    compressed = domain_dir / _COMPRESSED
    if not compressed.is_dir():
        return 0
    count = 0
    for path in compressed.rglob("*.original.md"):
        if path.is_file():
            path.unlink()
            count += 1
    return count


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def is_eligible(rel_path: Path) -> bool:
    return rel_path.suffix.lower() in _ELIGIBLE_SUFFIXES


def is_sensitive(rel_path: Path) -> bool:
    return bool(_SENSITIVE_PATTERN.search(str(rel_path)))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def read_manifest(domain_dir: Path) -> dict[str, str]:
    """Return {source_path: source_sha}. Treat unreadable/corrupt as empty."""
    path = domain_dir / _MANIFEST
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        # Corrupt or unparseable — treat as empty; --finalize will rewrite cleanly.
        print(
            f"# warning: {path.relative_to(domain_dir)} unreadable; treating as empty",
            file=sys.stderr,
        )
        return {}
    sources = (data.get("sources") or {}) if isinstance(data, dict) else {}
    return {str(k): str(v.get("source_sha", "")) for k, v in sources.items() if isinstance(v, dict)}


def write_manifest(domain_dir: Path, sources: dict[str, str]) -> None:
    """Atomic write: tmp file + os.replace."""
    path = domain_dir / _MANIFEST
    payload = {
        "sources": {
            src: {"source_sha": sha}
            for src, sha in sorted(sources.items())
        }
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated by xlator compress-inputs — do not edit manually\n")
        yaml.safe_dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Git SHA
# ---------------------------------------------------------------------------

def git_sha(domain_dir: Path, abs_path: Path) -> str:
    """git log -1 --format=%H -- <path>; returns 'untracked' if empty."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(abs_path)],
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
# Plan
# ---------------------------------------------------------------------------

def cmd_plan(domain_dir: Path) -> dict[str, object]:
    if not domain_dir.is_dir():
        raise RuntimeError(f"Domain directory not found: {domain_dir}")

    bootstrap_moved = bootstrap(domain_dir)
    swept = sweep_stale_backups(domain_dir)

    source_root = domain_dir / _POLICY_DOCS
    if not source_root.is_dir():
        raise RuntimeError(
            f"{_POLICY_DOCS}/ not found under {domain_dir.name}/. "
            f"Add .md policy documents first."
        )

    manifest = read_manifest(domain_dir)
    seen_sources: set[str] = set()

    to_compress: list[dict[str, str]] = []
    noop: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    # Walk source files in sorted order for deterministic output.
    for abs_src in sorted(source_root.rglob("*")):
        if not abs_src.is_file():
            continue
        rel = abs_src.relative_to(domain_dir)  # e.g. input/policy_docs/sub/foo.md
        rel_str = str(rel)

        if not is_eligible(rel):
            skipped.append({"src": rel_str, "reason": "not_eligible"})
            continue
        if is_sensitive(rel):
            skipped.append({"src": rel_str, "reason": "sensitive_path"})
            continue

        seen_sources.add(rel_str)
        sha = git_sha(domain_dir, abs_src)
        prev_sha = manifest.get(rel_str)

        # Untracked sources always re-compress (no stable SHA to compare).
        if sha == "untracked" or prev_sha != sha:
            sub_rel = rel.relative_to(_POLICY_DOCS)
            dst_rel = Path(_COMPRESSED) / sub_rel
            abs_dst = domain_dir / dst_rel
            abs_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_src, abs_dst)
            to_compress.append({
                "src": rel_str,
                "dst": str(dst_rel),
                "source_sha": sha,
            })
        else:
            noop.append({"src": rel_str, "reason": "unchanged"})

    # Mirror-deletes: manifest entries with no current source.
    to_delete: list[str] = []
    for src_rel in manifest:
        if src_rel in seen_sources:
            continue
        try:
            sub_rel = Path(src_rel).relative_to(_POLICY_DOCS)
        except ValueError:
            # Manifest key not under input/policy_docs/ — keep entry, log skip.
            continue
        to_delete.append(str(Path(_COMPRESSED) / sub_rel))

    plan = {
        "to_compress": to_compress,
        "to_delete": to_delete,
        "noop": noop,
        "skipped": skipped,
        "bootstrap_moved": bootstrap_moved,
        "succeeded": [],   # mutated by skill as it processes each file
        "failed": [],      # mutated by skill on per-file caveman failure
    }

    # Persist transient plan for --finalize.
    plan_path = domain_dir / _PLAN_TMP
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    # Human-readable summary on stderr.
    print(
        f"# plan: {len(to_compress)} to compress, {len(to_delete)} to delete, "
        f"{len(noop)} unchanged, {len(skipped)} skipped, {swept} stale backups removed",
        file=sys.stderr,
    )
    if bootstrap_moved:
        for line in bootstrap_moved:
            print(f"# bootstrap: moved {line}", file=sys.stderr)

    return plan


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

def cmd_finalize(domain_dir: Path) -> dict[str, int]:
    if not domain_dir.is_dir():
        raise RuntimeError(f"Domain directory not found: {domain_dir}")

    plan_path = domain_dir / _PLAN_TMP
    if not plan_path.exists():
        raise RuntimeError(
            f"{_PLAN_TMP} not found. Run 'xlator compress-inputs <domain> --plan' first."
        )

    with plan_path.open(encoding="utf-8") as f:
        plan = json.load(f)

    succeeded: set[str] = set(plan.get("succeeded") or [])
    to_compress: list[dict[str, str]] = plan.get("to_compress") or []
    to_delete: list[str] = plan.get("to_delete") or []

    # 1. Remove every *.original.md backup under compressed/.
    backups_removed = 0
    compressed = domain_dir / _COMPRESSED
    if compressed.is_dir():
        for path in compressed.rglob("*.original.md"):
            if path.is_file():
                path.unlink()
                backups_removed += 1

    # 2. For to_compress entries NOT in succeeded, delete the uncompressed dst
    #    so the next run reattempts. This prevents an uncompressed copy from
    #    matching the manifest SHA on next --plan and being skipped.
    aborted = 0
    for entry in to_compress:
        if entry["src"] in succeeded:
            continue
        dst_abs = domain_dir / entry["dst"]
        if dst_abs.exists():
            dst_abs.unlink()
            aborted += 1

    # 3. Mirror-deletes: remove compressed counterparts whose source is gone.
    deleted = 0
    for rel_dst in to_delete:
        dst_abs = domain_dir / rel_dst
        if dst_abs.exists():
            dst_abs.unlink()
            deleted += 1

    # 4. Update the manifest: keep noop entries, write succeeded entries,
    #    drop deleted entries.
    manifest = read_manifest(domain_dir)
    # Drop manifest entries whose compressed counterpart was just deleted.
    deleted_sources = set()
    for rel_dst in to_delete:
        try:
            sub = Path(rel_dst).relative_to(_COMPRESSED)
        except ValueError:
            continue
        deleted_sources.add(str(Path(_POLICY_DOCS) / sub))
    for src in deleted_sources:
        manifest.pop(src, None)
    # Write succeeded source SHAs.
    src_to_sha = {entry["src"]: entry["source_sha"] for entry in to_compress}
    for src in succeeded:
        if src in src_to_sha:
            manifest[src] = src_to_sha[src]

    write_manifest(domain_dir, manifest)

    # 5. Remove the transient plan file.
    plan_path.unlink()

    summary = {
        "compressed": len(succeeded),
        "deleted": deleted,
        "unchanged": len(plan.get("noop") or []),
        "skipped": len(plan.get("skipped") or []),
        "aborted": aborted,
        "backups_removed": backups_removed,
        "failed": len(plan.get("failed") or []),
    }
    print(
        f"compressed: {summary['compressed']}, deleted: {summary['deleted']}, "
        f"unchanged: {summary['unchanged']}, skipped: {summary['skipped']}, "
        f"failed: {summary['failed']}"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Maintain <domain>/policy_facets/compressed/ via caveman /compress."
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true",
                      help="Bootstrap, enumerate, copy, write transient plan; emit JSON.")
    mode.add_argument("--finalize", action="store_true",
                      help="Apply succeeded list to manifest; clean up backups and aborted copies.")
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(1)
    domain_dir = Path(domains_root) / args.domain

    try:
        if args.plan:
            plan = cmd_plan(domain_dir)
            print(json.dumps(plan, indent=2))
        else:
            cmd_finalize(domain_dir)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
