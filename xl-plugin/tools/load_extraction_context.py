#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator load-extraction-context: read guidance + manifest + index inputs for
/extract-ruleset and /update-ruleset and emit a single JSON payload covering
the five in-memory structures, SHA maps, and work-list resolution.

Subsumes the AI-consumed prose of SP-LoadInputIndex, SP-LoadGuidanceShas,
SP-LoadNamingManifest, and SP-ResolveRulesetModules's resolution into a
deterministic, read-only Python tool.

Output (stdout): a single pretty-printed JSON object — no human body, no
sentinel divider. Unlike scan_ruleset_groups, which pairs a JSON header
with a sentinel divider and a human-readable body so the calling skill
can relay the body to the analyst, this tool's stdout is consumed only
programmatically by the calling skill.

Usage:
    xlator load-extraction-context <domain> [<program>] [--mode {extract,update,review}]

Exit codes:
    0 — success
    1 — working-tree drift detected, or unexpected error
    2 — pre-flight failure (missing required folder/file, env var)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INPUT_INDEX_REL = "policy_facets/input-index.yaml"
_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_EXTRACTION_MANIFEST_REL = "specs/extraction-manifest.yaml"
_GUIDANCE_DIR_REL = "specs/guidance"
_METADATA_REL = "specs/guidance/metadata.yaml"
_PROMPT_CONTEXT_REL = "specs/guidance/prompt-context.yaml"
_OUTPUT_VARIABLES_REL = "specs/guidance/output-variables.yaml"
_INPUT_VARIABLES_REL = "specs/guidance/input-variables.yaml"
_SKELETON_REL = "specs/guidance/skeleton.yaml"
_SAMPLE_ARTIFACTS_REL = "specs/guidance/sample-artifacts.yaml"
_INCLUDE_WITH_OUTPUT_REL = "specs/guidance/include-with-output.yaml"
_CONSTANTS_AND_TABLES_REL = "specs/guidance/constants-and-tables.yaml"
_RULESET_MODULES_REL = "specs/guidance/ruleset-modules.yaml"

# Pre-flight: required files (missing → exit 2).
_REQUIRED_FILES = (
    _METADATA_REL,
    _PROMPT_CONTEXT_REL,
    _NAMING_MANIFEST_REL,
    _OUTPUT_VARIABLES_REL,
    _INPUT_INDEX_REL,
)

_REJECTED_SCORE_THRESHOLD = 40

_MODES = ("extract", "update", "review")


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml_or_none(path: Path) -> Any:
    """Return parsed YAML or None when absent/unreadable."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None


def _load_yaml_or_empty_dict(path: Path) -> dict:
    """Return parsed dict or {} when absent/unparseable/non-mapping."""
    data = _load_yaml_or_none(path)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# git hash-object helper
# ---------------------------------------------------------------------------

def _git_sha(domain_dir: Path, path: Path) -> str:
    """Compute working-tree blob SHA via `git hash-object <path>`. Returns
    'untracked' when git or hash-object cannot run."""
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
# Per-structure builders
# ---------------------------------------------------------------------------

def _build_confirmed_exprs(skeleton_doc: Any) -> dict[str, str]:
    """Flatten `skeleton.computations[].exprs` into a single
    `{variable_name: expression}` map. Stage information is not preserved —
    the AI's downstream consumer only needs name → expr."""
    if not isinstance(skeleton_doc, dict):
        return {}
    skel = skeleton_doc.get("skeleton")
    if not isinstance(skel, dict):
        return {}
    computations = skel.get("computations")
    if not isinstance(computations, list):
        return {}
    out: dict[str, str] = {}
    for entry in computations:
        if not isinstance(entry, dict):
            continue
        exprs = entry.get("exprs")
        if not isinstance(exprs, dict):
            continue
        for name, expr in exprs.items():
            if isinstance(name, str) and isinstance(expr, str):
                out[name] = expr
    return out


def _build_example_rules(sample_artifacts_doc: Any) -> list[dict[str, Any]]:
    """Extract top-level `sample_rules:` list verbatim. Each entry is passed
    through unchanged (the AI consumes id/rule_type/source/civil)."""
    if not isinstance(sample_artifacts_doc, dict):
        return []
    rules = sample_artifacts_doc.get("sample_rules")
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def _build_guidance_output_set(include_doc: Any) -> list[str]:
    """`include-with-output.yaml` is a flat top-level list of strings."""
    if not isinstance(include_doc, list):
        return []
    return [x for x in include_doc if isinstance(x, str)]


def _build_constants_tables_seed(
    constants_doc: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (seed_list, warnings).

    Each entry must have `name`, `description`, `source_file`, `source_section`.
    Entries missing any of those four are dropped; one warning string is
    appended per drop (relayed by the skill to the analyst)."""
    warnings: list[str] = []
    if not isinstance(constants_doc, dict):
        return [], warnings
    entries = constants_doc.get("constants_and_tables")
    if not isinstance(entries, list):
        return [], warnings
    seed: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            warnings.append(
                f"constants_and_tables[{i}]: not a mapping, dropped"
            )
            continue
        missing = [
            k for k in ("name", "description", "source_file", "source_section")
            if not entry.get(k)
        ]
        if missing:
            warnings.append(
                f"constants_and_tables[{i}] (name={entry.get('name')!r}): "
                f"missing required fields {missing}, dropped"
            )
            continue
        seed.append({
            "name": entry["name"],
            "description": entry["description"],
            "source_file": entry["source_file"],
            "source_section": entry["source_section"],
        })
    return seed, warnings


def _build_per_module_sample_rules(
    ruleset_modules_doc: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Build `{<module_name>: [<sample_rule>...]}`. Modules without
    `sample_rules:` are omitted from the map."""
    if not isinstance(ruleset_modules_doc, dict):
        return {}
    modules = ruleset_modules_doc.get("ruleset_modules")
    if not isinstance(modules, list):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in modules:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        rules = entry.get("sample_rules")
        if not isinstance(rules, list) or not rules:
            continue
        out[name] = [r for r in rules if isinstance(r, dict)]
    return out


# ---------------------------------------------------------------------------
# Input-index drift check
# ---------------------------------------------------------------------------

def _check_input_index_drift(
    domain_dir: Path,
    index_doc: Any,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (path → sha map, drifted_paths, missing_paths).

    Filters md_quality.score < threshold (rejected entries — their sources
    live in input/rejected/ now, not input/policy_docs/). For each eligible
    entry, re-computes `git hash-object` and reports mismatches as drift."""
    out: dict[str, str] = {}
    drifted: list[str] = []
    missing: list[str] = []

    if not isinstance(index_doc, dict):
        return out, drifted, missing
    files_map = index_doc.get("files")
    if not isinstance(files_map, dict):
        return out, drifted, missing

    for path_key, entry in files_map.items():
        if not isinstance(path_key, str) or not isinstance(entry, dict):
            continue
        mq = entry.get("md_quality")
        if isinstance(mq, dict):
            score = mq.get("score")
            if isinstance(score, int) and score < _REJECTED_SCORE_THRESHOLD:
                continue  # rejected — skip
        recorded_sha = entry.get("sha")
        if not isinstance(recorded_sha, str):
            continue

        abs_path = domain_dir / path_key
        if not abs_path.is_file():
            missing.append(path_key)
            out[path_key] = recorded_sha
            continue

        if recorded_sha == "untracked":
            out[path_key] = recorded_sha
            continue

        current = _git_sha(domain_dir, abs_path)
        if current == "untracked":
            # git unavailable — record the index value as-is; not drift.
            out[path_key] = recorded_sha
            continue
        if current != recorded_sha:
            drifted.append(path_key)
        out[path_key] = recorded_sha

    return out, drifted, missing


# ---------------------------------------------------------------------------
# Guidance SHA map
# ---------------------------------------------------------------------------

def _build_guidance_shas(domain_dir: Path) -> dict[str, str]:
    """For each `specs/guidance/*.yaml` + `specs/naming-manifest.yaml`
    that exists as a regular file, compute git hash-object. Dot-prefixed
    files are skipped (those are tier-write metadata)."""
    out: dict[str, str] = {}
    guidance_dir = domain_dir / _GUIDANCE_DIR_REL
    if guidance_dir.is_dir():
        for entry in sorted(guidance_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix != ".yaml":
                continue
            rel = entry.relative_to(domain_dir).as_posix()
            out[rel] = _git_sha(domain_dir, entry)
    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    if manifest_path.is_file():
        out[_NAMING_MANIFEST_REL] = _git_sha(domain_dir, manifest_path)
    return out


# ---------------------------------------------------------------------------
# Work-list resolution
# ---------------------------------------------------------------------------

def _build_work_list(
    domain_dir: Path,
    program: str | None,
    ruleset_modules_doc: Any,
) -> list[dict[str, Any]]:
    """Resolve the multi-file work-list.

    No ruleset-modules entries → `[{name: <program>, role: main, action: <a>}]`
    where action depends on whether `<program>.civil.yaml` exists.

    With entries → sub-modules first (in declaration order), main module
    (the entry with `role: main`, if any) last. Each entry's action is
    `generate` (file absent) or `reference` (file present).
    """
    specs_dir = domain_dir / "specs"

    modules: list[dict[str, Any]] = []
    if isinstance(ruleset_modules_doc, dict):
        raw = ruleset_modules_doc.get("ruleset_modules")
        if isinstance(raw, list):
            modules = [m for m in raw if isinstance(m, dict)]

    if not modules:
        if not program:
            return []
        civil_path = specs_dir / f"{program}.civil.yaml"
        action = "reference" if civil_path.is_file() else "generate"
        return [{
            "name": program,
            "role": "main",
            "action": action,
            "civil_file": f"specs/{program}.civil.yaml",
        }]

    # Sub-modules first (entries without role: main), main module last.
    subs: list[dict[str, Any]] = []
    main: dict[str, Any] | None = None
    for m in modules:
        role = m.get("role")
        if role == "main":
            main = m
        else:
            subs.append(m)
    ordered: list[dict[str, Any]] = subs + ([main] if main else [])

    work_list: list[dict[str, Any]] = []
    for m in ordered:
        name = m.get("name")
        if not isinstance(name, str):
            continue
        role = "main" if m.get("role") == "main" else "sub"
        civil_rel = f"specs/{name}.civil.yaml"
        civil_path = specs_dir / f"{name}.civil.yaml"
        action = "reference" if civil_path.is_file() else "generate"
        entry: dict[str, Any] = {
            "name": name,
            "role": role,
            "action": action,
            "civil_file": civil_rel,
        }
        bound = m.get("bound_entities")
        if isinstance(bound, list):
            entry["bound_entities"] = list(bound)
        depends_on = m.get("depends_on")
        if isinstance(depends_on, list):
            entry["sub_modules"] = [d for d in depends_on if isinstance(d, str)]
        work_list.append(entry)

    return work_list


# ---------------------------------------------------------------------------
# Program auto-detection
# ---------------------------------------------------------------------------

def _resolve_program(
    domain_dir: Path,
    program_arg: str | None,
    ruleset_modules_doc: Any,
) -> tuple[str | None, list[str]]:
    """Resolve `program` argument.

    Returns (program, candidate_programs).

    Precedence:
      1. If ruleset_modules.yaml has a `role: main` entry, use its name (the
         skill ignores any conflicting CLI arg per SP-ResolveRulesetModules).
      2. Else if program_arg given, use it.
      3. Else glob specs/*.civil.yaml: 1 candidate → use; 0 → None;
         2+ → None + candidate list (caller prompts).
    """
    if isinstance(ruleset_modules_doc, dict):
        modules = ruleset_modules_doc.get("ruleset_modules")
        if isinstance(modules, list):
            for m in modules:
                if isinstance(m, dict) and m.get("role") == "main":
                    name = m.get("name")
                    if isinstance(name, str):
                        return name, []

    if program_arg:
        return program_arg, []

    specs_dir = domain_dir / "specs"
    if not specs_dir.is_dir():
        return None, []
    candidates = sorted(
        p.stem.removesuffix(".civil")
        for p in specs_dir.glob("*.civil.yaml")
    )
    if len(candidates) == 1:
        return candidates[0], []
    if len(candidates) == 0:
        return None, []
    return None, candidates


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(domain_dir: Path) -> tuple[int, str] | None:
    """Return None on success, otherwise (exit_code, stderr_message)."""
    if not domain_dir.is_dir():
        return (2, f"Error: Domain directory not found: {domain_dir}")
    for rel in _REQUIRED_FILES:
        if not (domain_dir / rel).is_file():
            if rel == _INPUT_INDEX_REL:
                return (
                    2,
                    f"ERROR: Run /index-inputs {domain_dir.name} first "
                    f"(missing {rel}).",
                )
            return (2, f"ERROR: required file missing: {rel}")
    return None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(
    domain_dir: Path,
    program_arg: str | None,
    mode: str,
) -> int:
    pre = _preflight(domain_dir)
    if pre is not None:
        rc, msg = pre
        print(msg, file=sys.stderr)
        return rc

    # Load all guidance/manifest/index documents up-front.
    metadata = _load_yaml_or_empty_dict(domain_dir / _METADATA_REL)
    prompt_context = _load_yaml_or_empty_dict(domain_dir / _PROMPT_CONTEXT_REL)
    output_variables = _load_yaml_or_empty_dict(domain_dir / _OUTPUT_VARIABLES_REL)
    input_variables = _load_yaml_or_empty_dict(domain_dir / _INPUT_VARIABLES_REL)
    naming_manifest = _load_yaml_or_empty_dict(domain_dir / _NAMING_MANIFEST_REL)

    skeleton_doc = _load_yaml_or_none(domain_dir / _SKELETON_REL)
    sample_artifacts_doc = _load_yaml_or_none(domain_dir / _SAMPLE_ARTIFACTS_REL)
    include_doc = _load_yaml_or_none(domain_dir / _INCLUDE_WITH_OUTPUT_REL)
    constants_doc = _load_yaml_or_none(domain_dir / _CONSTANTS_AND_TABLES_REL)
    ruleset_modules_doc = _load_yaml_or_none(domain_dir / _RULESET_MODULES_REL)
    input_index_doc = _load_yaml_or_none(domain_dir / _INPUT_INDEX_REL)

    # Build the five in-memory structures.
    confirmed_exprs = _build_confirmed_exprs(skeleton_doc)
    example_rules = _build_example_rules(sample_artifacts_doc)
    guidance_output_set = _build_guidance_output_set(include_doc)
    constants_tables_seed, ct_warnings = _build_constants_tables_seed(constants_doc)
    per_module_sample_rules = _build_per_module_sample_rules(ruleset_modules_doc)

    # Working-tree drift check (input-index tier).
    input_index_shas, drifted, missing = _check_input_index_drift(
        domain_dir, input_index_doc
    )
    if missing:
        # Missing sources are a separate failure mode from SHA drift; surface
        # them as exit 1 with a specific message.
        print(
            "ERROR: source missing on disk: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if drifted:
        print(
            f"ERROR: Working-tree drift detected. Re-run "
            f"/index-inputs {domain_dir.name}. Drifted files: "
            + ", ".join(drifted),
            file=sys.stderr,
        )
        return 1

    # Guidance SHA map (no drift check — fresh capture).
    guidance_shas = _build_guidance_shas(domain_dir)

    # Resolve program + work-list.
    program, candidate_programs = _resolve_program(
        domain_dir, program_arg, ruleset_modules_doc
    )
    work_list = _build_work_list(domain_dir, program, ruleset_modules_doc)

    # Optional: existing extraction-manifest.
    existing_extraction_manifest = _load_yaml_or_none(
        domain_dir / _EXTRACTION_MANIFEST_REL
    )

    # Emit constants-and-tables warnings to stderr so the skill can relay.
    for w in ct_warnings:
        print(f"WARN: {w}", file=sys.stderr)

    payload: dict[str, Any] = {
        "domain": domain_dir.name,
        "program": program,
        "mode": mode,
        "confirmed_exprs": confirmed_exprs,
        "example_rules": example_rules,
        "guidance_output_set": guidance_output_set,
        "constants_tables_seed": constants_tables_seed,
        "per_module_sample_rules": per_module_sample_rules,
        "input_index_shas": input_index_shas,
        "guidance_shas": guidance_shas,
        "work_list": work_list,
        "metadata": metadata,
        "prompt_context": prompt_context,
        "output_variables": output_variables,
        "input_variables": input_variables,
        "naming_manifest": naming_manifest,
        "existing_extraction_manifest": existing_extraction_manifest,
        "candidate_programs": candidate_programs,
        "warnings": ct_warnings,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load all guidance + manifest + index inputs for /extract-ruleset "
            "and /update-ruleset; emit a single JSON payload to stdout."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "program",
        nargs="?",
        default=None,
        help="Program name (optional). Auto-detected from "
        "ruleset-modules.yaml's role: main entry, or from a single "
        "specs/*.civil.yaml file when unambiguous.",
    )
    parser.add_argument(
        "--mode",
        choices=_MODES,
        default="extract",
        help=(
            "Invocation context. extract: /extract-ruleset; update: "
            "/update-ruleset; review: /review-ruleset (loads the program's "
            "existing manifest entry as-is). Default: extract."
        ),
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    return run(domain_dir, args.program, args.mode)


if __name__ == "__main__":
    sys.exit(main())
