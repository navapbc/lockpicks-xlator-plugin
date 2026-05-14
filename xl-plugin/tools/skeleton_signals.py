#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator skeleton-signals: deterministic signal-extraction tool for the
/create-skeleton skill. Walks the domain's per-file computation YAMLs,
naming manifest, output suggestions, and metadata, and emits a structured
JSON snapshot of every signal the skill consumes.

The skill's AI step consumes this JSON to produce enrichment.json (descriptions,
flow diagram, primary selection, prompt-context additions, etc.) which is then
fed to `xlator emit-skeleton`.

Inputs:
  - <domain>  positional, resolved against $DOMAINS_FULLPATH

Output:
  - stdout: the full signals JSON. No header/sentinel split — the entire
    stdout is the JSON object so the AI can consume it directly.

Exit codes:
    0 — success
    2 — pre-flight failure (missing folder, metadata, prompt-context, manifest,
        or empty policy_facets/computations/)
    1 — unexpected error (e.g. unparseable per-file YAML)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from civil_helpers import (  # noqa: E402
    load_per_file_computations,
    normalize_stage,
    parse_expr_hint,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_METADATA_REL = "specs/guidance/metadata.yaml"
_PROMPT_CONTEXT_REL = "specs/guidance/prompt-context.yaml"
_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_SUGGESTED_TARGETS_REL = "specs/suggested_targets"
_PER_FILE_REL = "policy_facets/computations"

# Step-4 output files that may already exist (preserved in `revise` mode).
_OUTPUT_FILES_REL = (
    "specs/guidance/skeleton.yaml",
    "specs/guidance/output-variables.yaml",
    "specs/guidance/input-variables.yaml",
    "specs/guidance/constants-and-tables.yaml",
)

_PROMPT_CONTEXT_SECTIONS = ("constraints", "standards", "guidance", "edge_cases")

# Candidate constants/tables regex patterns:
#  - multi-word title-case: two-or-more words each starting capital
#  - UPPER_SNAKE_CASE: at least 3 chars, all caps + digits + underscores
_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)+)\b")
_UPPER_SNAKE_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(domain_dir: Path) -> Optional[str]:
    """Return None on success, otherwise a stderr error message."""
    if not domain_dir.is_dir():
        return f"Domain not found: {domain_dir}/"
    metadata_path = domain_dir / _METADATA_REL
    if not metadata_path.is_file():
        return (
            f"guidance/metadata.yaml not found: {metadata_path}\n"
            f"Run /declare-target-ruleset {domain_dir.name} first."
        )
    prompt_context_path = domain_dir / _PROMPT_CONTEXT_REL
    if not prompt_context_path.is_file():
        return (
            f"guidance/prompt-context.yaml not found: {prompt_context_path}\n"
            f"Run /declare-target-ruleset {domain_dir.name} first."
        )
    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    if not manifest_path.is_file():
        return (
            f"naming-manifest.yaml not found: {manifest_path}\n"
            f"Run /declare-target-ruleset {domain_dir.name} first."
        )
    per_file_dir = domain_dir / _PER_FILE_REL
    if not per_file_dir.is_dir() or not any(per_file_dir.rglob("*.md.yaml")):
        return (
            f"Per-file computations not found under: {per_file_dir}/\n"
            f"Run /index-inputs {domain_dir.name} first."
        )
    return None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    """Load a YAML file. Returns the parsed object (may be None for empty)."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_yaml_safe(path: Path) -> Any:
    """Load YAML returning {} on missing/malformed."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError:
        return None


# ---------------------------------------------------------------------------
# Per-section iteration helpers
# ---------------------------------------------------------------------------

def _reconstruct_source_path(rel_path: str) -> str:
    """Map a per-file YAML's relative path back to its source policy doc.

    Example: `441.md.yaml` → `input/policy_docs/441.md`.
    """
    if rel_path.endswith(".yaml"):
        rel_path = rel_path[: -len(".yaml")]
    return f"input/policy_docs/{rel_path}"


def _iter_sections(per_file: dict[str, dict]):
    """Yield (rel_path, source_path, section_dict, section_index) for every
    section in every per-file YAML, preserving sort + declaration order."""
    for rel, doc in per_file.items():
        source_path = _reconstruct_source_path(rel)
        if not isinstance(doc, dict):
            continue
        sections = doc.get("sections")
        if not isinstance(sections, list):
            continue
        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            yield rel, source_path, section, idx


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def _collect_tags(per_file: dict[str, dict]) -> list[dict[str, Any]]:
    """Frequency-sorted list of {value, count}. Ties broken by first-seen order."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for _rel, _src, section, _idx in _iter_sections(per_file):
        tags = section.get("tags")
        if not isinstance(tags, list):
            continue
        for t in tags:
            if not isinstance(t, str) or not t:
                continue
            if t not in counts:
                counts[t] = 0
                order.append(t)
            counts[t] += 1
    return [
        {"value": v, "count": counts[v]}
        for v in sorted(order, key=lambda x: (-counts[x], order.index(x)))
    ]


def _collect_headings(per_file: dict[str, dict]) -> list[dict[str, Any]]:
    """List of {file, level, text, order} for each section heading."""
    out: list[dict[str, Any]] = []
    for _rel, source_path, section, idx in _iter_sections(per_file):
        heading = section.get("heading")
        if not isinstance(heading, str) or not heading:
            continue
        # Derive level from leading `#` count.
        m = re.match(r"^(#+)\s", heading)
        level = len(m.group(1)) if m else 0
        if level not in (1, 2, 3):
            level = 2  # default for non-standard headings
        out.append({
            "file": source_path,
            "level": level,
            "text": heading,
            "order": idx,
        })
    return out


def _collect_summaries(per_file: dict[str, dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _rel, source_path, section, _idx in _iter_sections(per_file):
        summary = section.get("summary")
        if not isinstance(summary, str) or not summary:
            continue
        out.append({
            "file": source_path,
            "section_heading": section.get("heading", ""),
            "text": summary,
        })
    return out


def _collect_expr_hints(per_file: dict[str, dict]) -> list[dict[str, Any]]:
    """Parsed expr_hint records, one per computation that carries `expr_hint:`."""
    out: list[dict[str, Any]] = []
    for _rel, source_path, section, _idx in _iter_sections(per_file):
        computations = section.get("computations")
        if not isinstance(computations, list):
            continue
        stage_raw = section.get("stage")
        stage = stage_raw if isinstance(stage_raw, str) else None
        stage_norm = normalize_stage(stage_raw)
        for comp in computations:
            if not isinstance(comp, dict):
                continue
            expr_hint = comp.get("expr_hint")
            if expr_hint is None:
                continue
            parsed = parse_expr_hint(expr_hint)
            if parsed is None:
                # Malformed — warn but skip.
                print(
                    f"WARN: malformed expr_hint in {source_path}: {expr_hint!r}",
                    file=sys.stderr,
                )
                continue
            output, expression, rhs_tokens = parsed
            preconditions = comp.get("preconditions")
            out.append({
                "file": source_path,
                "section_heading": section.get("heading", ""),
                "stage": stage,
                "stage_normalized": stage_norm,
                "output": output,
                "expression": expression,
                "rhs_tokens": list(rhs_tokens),
                "preconditions": preconditions if preconditions else None,
            })
    return out


def _build_intermediate_variables(expr_hints: list[dict[str, Any]]) -> list[str]:
    """Names that are LHS-of-one computation AND RHS-of-another."""
    lhs_set = {e["output"] for e in expr_hints}
    rhs_set: set[str] = set()
    for e in expr_hints:
        rhs_set.update(e["rhs_tokens"])
    intermediates = lhs_set & rhs_set
    # Preserve first-LHS-appearance order.
    seen: list[str] = []
    seen_set: set[str] = set()
    for e in expr_hints:
        name = e["output"]
        if name in intermediates and name not in seen_set:
            seen.append(name)
            seen_set.add(name)
    return seen


def _build_stage_index(expr_hints: list[dict[str, Any]]) -> dict[str, list[str]]:
    """{stage_normalized: [variable_names_in_source_order]}."""
    out: dict[str, list[str]] = {}
    seen_per_stage: dict[str, set[str]] = {}
    for e in expr_hints:
        stage = e["stage_normalized"]
        if not stage:
            continue
        name = e["output"]
        bucket = out.setdefault(stage, [])
        seen = seen_per_stage.setdefault(stage, set())
        if name not in seen:
            bucket.append(name)
            seen.add(name)
    return out


def _build_entities(naming_manifest: dict[str, Any]) -> dict[str, list[str]]:
    """{entity_name: [field_names]} from manifest's inputs:."""
    out: dict[str, list[str]] = {}
    inputs = naming_manifest.get("inputs") if isinstance(naming_manifest, dict) else None
    if not isinstance(inputs, dict):
        return out
    for entity_name, entity_def in inputs.items():
        if not isinstance(entity_name, str) or not isinstance(entity_def, dict):
            continue
        fields = entity_def.get("fields")
        if not isinstance(fields, dict):
            out[entity_name] = []
            continue
        out[entity_name] = list(fields.keys())
    return out


def _build_mirrored_fields(entities: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Fields appearing under ≥2 entities."""
    field_to_entities: dict[str, list[str]] = {}
    for entity_name, fields in entities.items():
        for field_name in fields:
            field_to_entities.setdefault(field_name, []).append(entity_name)
    out: list[dict[str, Any]] = []
    for field_name, entity_list in field_to_entities.items():
        if len(entity_list) >= 2:
            out.append({"field": field_name, "entities": entity_list})
    return out


def _build_outputs_in_manifest(naming_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """List of {name, type, description} from manifest.outputs."""
    out: list[dict[str, Any]] = []
    outputs = naming_manifest.get("outputs") if isinstance(naming_manifest, dict) else None
    if not isinstance(outputs, dict):
        return out
    for name, defn in outputs.items():
        if not isinstance(name, str):
            continue
        type_val = None
        description = None
        if isinstance(defn, dict):
            type_val = defn.get("type")
            description = defn.get("description")
        out.append({
            "name": name,
            "type": type_val,
            "description": description,
        })
    return out


def _build_output_primary_hint(
    domain_dir: Path,
    outputs_in_manifest: list[dict[str, Any]],
) -> Optional[str]:
    """Best-effort: scan specs/suggested_targets/*.yaml for `outputs.<name>.primary: true`.

    Returns the matching name when exactly one suggestion file flags a name
    that is in the manifest's outputs. Returns None otherwise (ambiguous or
    not found).
    """
    suggested_dir = domain_dir / _SUGGESTED_TARGETS_REL
    if not suggested_dir.is_dir():
        return None
    manifest_names = {o["name"] for o in outputs_in_manifest}
    matches: list[str] = []
    for path in sorted(suggested_dir.glob("*.yaml")):
        doc = _load_yaml_safe(path)
        if not isinstance(doc, dict):
            continue
        outputs = doc.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for name, defn in outputs.items():
            if not isinstance(name, str) or name not in manifest_names:
                continue
            if isinstance(defn, dict) and defn.get("primary") is True:
                matches.append(name)
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def _collect_candidate_constants(
    per_file: dict[str, dict],
    known_names: set[str],
) -> list[dict[str, Any]]:
    """Surface candidate constants/tables from computation/section prose.

    Scans `description:` of each computation and `summary:` of each section
    for multi-word title-case OR UPPER_SNAKE_CASE tokens; filters out matches
    that equal a known variable name (case-insensitive). Each unique
    (name, source_file, source_section) tuple is emitted; the same name may
    appear with multiple provenances if it surfaces in multiple sections.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    # Normalize known names to a canonical form for filtering.
    known_lower = {n.lower() for n in known_names}
    # Also include hyphen/space/underscore-collapsed variants
    known_collapsed = {re.sub(r"[\s_-]+", "", n.lower()) for n in known_names}

    def _is_known(token: str) -> bool:
        lo = token.lower()
        if lo in known_lower:
            return True
        return re.sub(r"[\s_-]+", "", lo) in known_collapsed

    for _rel, source_path, section, _idx in _iter_sections(per_file):
        heading = section.get("heading", "")
        candidates: list[str] = []

        # Scan summary.
        summary = section.get("summary")
        if isinstance(summary, str):
            for m in _TITLE_CASE_RE.findall(summary):
                candidates.append(m)
            for m in _UPPER_SNAKE_RE.findall(summary):
                candidates.append(m)

        # Scan computation descriptions.
        computations = section.get("computations")
        if isinstance(computations, list):
            for comp in computations:
                if not isinstance(comp, dict):
                    continue
                desc = comp.get("description")
                if not isinstance(desc, str):
                    continue
                for m in _TITLE_CASE_RE.findall(desc):
                    candidates.append(m)
                for m in _UPPER_SNAKE_RE.findall(desc):
                    candidates.append(m)

        for name in candidates:
            if _is_known(name):
                continue
            key = (name, source_path, heading)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name,
                "source_file": source_path,
                "source_section": heading,
            })

    return out


def _load_prompt_context_existing(prompt_context_doc: Any) -> dict[str, list[str]]:
    """Return {constraints, standards, guidance, edge_cases} as lists of strings."""
    out: dict[str, list[str]] = {s: [] for s in _PROMPT_CONTEXT_SECTIONS}
    if not isinstance(prompt_context_doc, dict):
        return out
    for section in _PROMPT_CONTEXT_SECTIONS:
        items = prompt_context_doc.get(section)
        if isinstance(items, list):
            out[section] = [str(x) for x in items if isinstance(x, str)]
    return out


def _load_existing_output_files(domain_dir: Path) -> dict[str, Any]:
    """Parse each Step-4 output file (if present); return name → parsed dict / None."""
    out: dict[str, Any] = {}
    for rel in _OUTPUT_FILES_REL:
        path = domain_dir / rel
        name = Path(rel).name
        if not path.is_file():
            out[name] = None
            continue
        doc = _load_yaml_safe(path)
        out[name] = doc
    return out


# ---------------------------------------------------------------------------
# Top-level signal extraction
# ---------------------------------------------------------------------------

def extract_signals(domain_dir: Path) -> dict[str, Any]:
    """Run every signal extractor and return the aggregated dict."""
    per_file = load_per_file_computations(domain_dir)

    metadata_doc = _load_yaml_safe(domain_dir / _METADATA_REL) or {}
    prompt_context_doc = _load_yaml_safe(domain_dir / _PROMPT_CONTEXT_REL) or {}
    naming_manifest_doc = _load_yaml_safe(domain_dir / _NAMING_MANIFEST_REL) or {}

    tags = _collect_tags(per_file)
    headings = _collect_headings(per_file)
    summaries = _collect_summaries(per_file)
    expr_hints = _collect_expr_hints(per_file)
    intermediate_variables = _build_intermediate_variables(expr_hints)
    stage_index = _build_stage_index(expr_hints)
    entities = _build_entities(naming_manifest_doc)
    mirrored_fields = _build_mirrored_fields(entities)
    outputs_in_manifest = _build_outputs_in_manifest(naming_manifest_doc)
    output_primary_hint = _build_output_primary_hint(domain_dir, outputs_in_manifest)

    # Build the "known names" set used to filter candidate constants:
    # every entity field, every output, every LHS in expr_hints, plus
    # intermediates. Filtering eliminates capitalized variable name
    # occurrences in prose from polluting the constants pool.
    known_names: set[str] = set()
    for fields in entities.values():
        known_names.update(fields)
    for o in outputs_in_manifest:
        known_names.add(o["name"])
    for e in expr_hints:
        known_names.add(e["output"])
        known_names.update(e["rhs_tokens"])
    candidate_constants_and_tables = _collect_candidate_constants(per_file, known_names)

    metadata = {
        "display_name": metadata_doc.get("display_name") if isinstance(metadata_doc, dict) else None,
        "description": metadata_doc.get("description") if isinstance(metadata_doc, dict) else None,
    }
    prompt_context_existing = _load_prompt_context_existing(prompt_context_doc)
    existing_files = _load_existing_output_files(domain_dir)

    return {
        "tags": tags,
        "headings": headings,
        "summaries": summaries,
        "expr_hints": expr_hints,
        "intermediate_variables": intermediate_variables,
        "stage_index": stage_index,
        "entities": entities,
        "mirrored_fields": mirrored_fields,
        "outputs_in_manifest": outputs_in_manifest,
        "output_primary_hint": output_primary_hint,
        "candidate_constants_and_tables": candidate_constants_and_tables,
        "metadata": metadata,
        "prompt_context_existing": prompt_context_existing,
        "existing_files": existing_files,
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(domain_dir: Path) -> int:
    preflight_err = _preflight(domain_dir)
    if preflight_err is not None:
        print(preflight_err, file=sys.stderr)
        return 2

    try:
        signals = extract_signals(domain_dir)
    except yaml.YAMLError as exc:
        print(f"ERROR: YAML parse error: {exc}", file=sys.stderr)
        return 1

    json.dump(signals, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract the structured signals the /create-skeleton skill needs "
            "from a domain's policy_facets/computations/, naming-manifest, "
            "suggested_targets, metadata, and existing output files. Emits the "
            "full signals object as JSON on stdout."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    return run(domain_dir)


if __name__ == "__main__":
    sys.exit(main())
