#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator emit-skeleton: schema-validating writer for the /create-skeleton skill.

Reads signals (re-extracted via skeleton_signals.extract_signals) plus a
skeleton.json file (the AI-produced enrichment) produced by the skill's AI
step, validates the enrichment shape, then writes/merges six guidance files:

  - specs/guidance/prompt-context.yaml  (merge — additions only)
  - specs/guidance/skeleton.yaml
  - specs/guidance/flow_diagram.yaml
  - specs/guidance/output-variables.yaml
  - specs/guidance/input-variables.yaml
  - specs/guidance/constants-and-tables.yaml

Inputs:
  - <domain>            positional, resolved against $DOMAINS_FULLPATH
  - --mode {create,replace,revise}
  - --enrichment <path> path to the AI-produced enrichment JSON

Output:
  - stdout: JSON header line, sentinel divider, human-readable summary.

Exit codes:
    0 — success
    2 — pre-flight failure (missing folder/files, create-mode collision)
    1 — enrichment schema violation or unexpected error
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

from skeleton_signals import (  # noqa: E402
    _preflight as _signals_preflight,
    extract_signals,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GUIDANCE_DIR_REL = "specs/guidance"

_OUTPUT_FILES_REL = {
    "prompt-context.yaml": "specs/guidance/prompt-context.yaml",
    "skeleton.yaml": "specs/guidance/skeleton.yaml",
    "flow_diagram.yaml": "specs/guidance/flow_diagram.yaml",
    "output-variables.yaml": "specs/guidance/output-variables.yaml",
    "input-variables.yaml": "specs/guidance/input-variables.yaml",
    "constants-and-tables.yaml": "specs/guidance/constants-and-tables.yaml",
}

# Files that are "Step-4 outputs" — `--mode create` collision check applies
# only to these (not prompt-context.yaml, which is always merged).
_STEP4_FILES = (
    "skeleton.yaml",
    "flow_diagram.yaml",
    "output-variables.yaml",
    "input-variables.yaml",
    "constants-and-tables.yaml",
)

_PROMPT_CONTEXT_SECTIONS = ("constraints", "standards", "guidance", "edge_cases")

_HEADER_SENTINEL = "--- EMIT-SKELETON-HEADER-END ---"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class EnrichmentError(Exception):
    """Raised when enrichment JSON fails schema validation."""


# ---------------------------------------------------------------------------
# Enrichment schema validation
# ---------------------------------------------------------------------------

def _require_dict(value: Any, dotted_path: str) -> dict:
    if not isinstance(value, dict):
        raise EnrichmentError(f"enrichment.{dotted_path}: expected object, got {type(value).__name__}")
    return value


def _require_list(value: Any, dotted_path: str) -> list:
    if not isinstance(value, list):
        raise EnrichmentError(f"enrichment.{dotted_path}: expected list, got {type(value).__name__}")
    return value


def _require_string(value: Any, dotted_path: str) -> str:
    if not isinstance(value, str):
        raise EnrichmentError(f"enrichment.{dotted_path}: expected string, got {type(value).__name__}")
    return value


def _require_bool(value: Any, dotted_path: str) -> bool:
    if not isinstance(value, bool):
        raise EnrichmentError(f"enrichment.{dotted_path}: expected bool, got {type(value).__name__}")
    return value


def _require_string_list(value: Any, dotted_path: str) -> list[str]:
    items = _require_list(value, dotted_path)
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise EnrichmentError(
                f"enrichment.{dotted_path}[{i}]: expected string, got {type(item).__name__}"
            )
    return items


def validate_enrichment(enrichment: Any) -> None:
    """Raise EnrichmentError on schema violation; return None on success.

    See plan R6 for the binding shape.
    """
    if not isinstance(enrichment, dict):
        raise EnrichmentError("enrichment: top-level value must be an object")

    # prompt_context_additions
    if "prompt_context_additions" not in enrichment:
        raise EnrichmentError("enrichment.prompt_context_additions: required field missing")
    pca = _require_dict(enrichment["prompt_context_additions"], "prompt_context_additions")
    for section in _PROMPT_CONTEXT_SECTIONS:
        if section not in pca:
            raise EnrichmentError(
                f"enrichment.prompt_context_additions.{section}: required field missing"
            )
        _require_string_list(pca[section], f"prompt_context_additions.{section}")

    # skeleton_flow_diagram
    if "skeleton_flow_diagram" not in enrichment:
        raise EnrichmentError("enrichment.skeleton_flow_diagram: required field missing")
    _require_string(enrichment["skeleton_flow_diagram"], "skeleton_flow_diagram")

    # skeleton_inputs / skeleton_outputs
    for field in ("skeleton_inputs", "skeleton_outputs"):
        if field not in enrichment:
            raise EnrichmentError(f"enrichment.{field}: required field missing")
        _require_string_list(enrichment[field], field)

    # output_variables
    if "output_variables" not in enrichment:
        raise EnrichmentError("enrichment.output_variables: required field missing")
    ov = _require_dict(enrichment["output_variables"], "output_variables")
    primary_count = 0
    for name, defn in ov.items():
        if not isinstance(name, str):
            raise EnrichmentError(f"enrichment.output_variables: keys must be strings")
        defn_obj = _require_dict(defn, f"output_variables.{name}")
        if "description" not in defn_obj:
            raise EnrichmentError(
                f"enrichment.output_variables.{name}.description: required field missing"
            )
        _require_string(defn_obj["description"], f"output_variables.{name}.description")
        if "primary" not in defn_obj:
            raise EnrichmentError(
                f"enrichment.output_variables.{name}.primary: required field missing"
            )
        primary = _require_bool(defn_obj["primary"], f"output_variables.{name}.primary")
        if primary:
            primary_count += 1
        if "examples" in defn_obj and defn_obj["examples"] is not None:
            _require_string_list(defn_obj["examples"], f"output_variables.{name}.examples")
    if primary_count != 1:
        raise EnrichmentError(
            f"enrichment.output_variables: exactly one entry must have primary: true "
            f"(found {primary_count})"
        )

    # input_variables
    if "input_variables" not in enrichment:
        raise EnrichmentError("enrichment.input_variables: required field missing")
    iv = _require_dict(enrichment["input_variables"], "input_variables")
    if "categories" not in iv:
        raise EnrichmentError("enrichment.input_variables.categories: required field missing")
    categories = _require_list(iv["categories"], "input_variables.categories")
    for i, cat in enumerate(categories):
        if not isinstance(cat, dict):
            raise EnrichmentError(
                f"enrichment.input_variables.categories[{i}]: expected object"
            )
        for required in ("category", "description", "fields"):
            if required not in cat:
                raise EnrichmentError(
                    f"enrichment.input_variables.categories[{i}].{required}: required field missing"
                )
        _require_string(cat["category"], f"input_variables.categories[{i}].category")
        _require_string(cat["description"], f"input_variables.categories[{i}].description")
        fields = _require_list(cat["fields"], f"input_variables.categories[{i}].fields")
        for j, fld in enumerate(fields):
            if not isinstance(fld, dict):
                raise EnrichmentError(
                    f"enrichment.input_variables.categories[{i}].fields[{j}]: expected object"
                )
            if "name_ref" not in fld:
                raise EnrichmentError(
                    f"enrichment.input_variables.categories[{i}].fields[{j}].name_ref: "
                    f"required field missing"
                )
            _require_string(
                fld["name_ref"],
                f"input_variables.categories[{i}].fields[{j}].name_ref",
            )
        if "examples" in cat and cat["examples"] is not None:
            _require_string_list(cat["examples"], f"input_variables.categories[{i}].examples")
        for opt in ("source_file", "source_section", "exact_phrase"):
            if opt in cat and cat[opt] is not None:
                _require_string(cat[opt], f"input_variables.categories[{i}].{opt}")

    # constants_and_tables
    if "constants_and_tables" not in enrichment:
        raise EnrichmentError("enrichment.constants_and_tables: required field missing")
    cat_dict = _require_dict(enrichment["constants_and_tables"], "constants_and_tables")
    for name, defn in cat_dict.items():
        if not isinstance(name, str):
            raise EnrichmentError("enrichment.constants_and_tables: keys must be strings")
        defn_obj = _require_dict(defn, f"constants_and_tables.{name}")
        if "description" not in defn_obj:
            raise EnrichmentError(
                f"enrichment.constants_and_tables.{name}.description: required field missing"
            )
        _require_string(defn_obj["description"], f"constants_and_tables.{name}.description")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(domain_dir: Path, enrichment_path: Path) -> Optional[tuple[int, str]]:
    """Return None on success, otherwise (exit_code, stderr_message)."""
    signals_err = _signals_preflight(domain_dir)
    if signals_err is not None:
        return (2, signals_err)
    if not enrichment_path.is_file():
        return (2, f"Enrichment file not found: {enrichment_path}")
    return None


# ---------------------------------------------------------------------------
# prompt-context.yaml merge
# ---------------------------------------------------------------------------

def _normalize_for_dedup(text: str) -> str:
    """Whitespace-normalized, case-insensitive string for dedup comparison."""
    collapsed = re.sub(r"\s+", " ", text.strip().lower())
    return collapsed


def _merge_prompt_context(
    existing_doc: Any,
    additions: dict[str, list[str]],
) -> tuple[dict, dict[str, int]]:
    """Merge enrichment.prompt_context_additions into the existing
    prompt-context.yaml doc. Return (merged_doc, added_counts)."""
    if not isinstance(existing_doc, dict):
        existing_doc = {}

    # Build a fresh dict to preserve key order: start from existing, then
    # ensure the four sections are present.
    merged: dict = dict(existing_doc)
    added_counts: dict[str, int] = {}

    for section in _PROMPT_CONTEXT_SECTIONS:
        current = merged.get(section)
        if not isinstance(current, list):
            current = []
        existing_keys = {_normalize_for_dedup(x) for x in current if isinstance(x, str)}
        new_items: list[str] = []
        for item in additions.get(section, []):
            key = _normalize_for_dedup(item)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            new_items.append(item)
        if new_items:
            merged[section] = list(current) + new_items
        else:
            merged[section] = list(current)
        added_counts[section] = len(new_items)

    return merged, added_counts


# ---------------------------------------------------------------------------
# skeleton.yaml build
# ---------------------------------------------------------------------------

def _build_skeleton(signals: dict, enrichment: dict, mode: str, existing: Any) -> dict:
    """Build the skeleton.yaml document content.

    In revise mode, preserve existing top-level keys when their values are
    non-null/non-empty.
    """
    stage_index = signals["stage_index"]
    expr_hints = signals["expr_hints"]

    # Group expr_hints by normalized stage; unstaged go to a single null bucket.
    by_stage: dict[Optional[str], list[dict]] = {}
    for e in expr_hints:
        by_stage.setdefault(e["stage_normalized"], []).append(e)

    computations: list[dict] = []

    # Emit one entry per normalized stage (in stage_index source order).
    for stage_name, variables in stage_index.items():
        entry: dict = {"stage": stage_name, "variables": list(variables)}
        exprs: dict[str, str] = {}
        for e in by_stage.get(stage_name, []):
            if e["expression"] is None:
                continue
            exprs[e["output"]] = e["expression"]
        if exprs:
            entry["exprs"] = exprs
        computations.append(entry)

    # Unstaged catch-all: variables that have stage_normalized==None.
    unstaged = by_stage.get(None, [])
    if unstaged:
        names: list[str] = []
        seen: set[str] = set()
        for e in unstaged:
            if e["output"] not in seen:
                names.append(e["output"])
                seen.add(e["output"])
        entry = {"variables": names}
        exprs = {}
        for e in unstaged:
            if e["expression"] is None:
                continue
            exprs[e["output"]] = e["expression"]
        if exprs:
            entry["exprs"] = exprs
        computations.append(entry)

    skeleton_body: dict = {
        "inputs": list(enrichment["skeleton_inputs"]),
        "outputs": list(enrichment["skeleton_outputs"]),
        "computations": computations,
    }

    if mode == "revise" and isinstance(existing, dict):
        existing_skel = existing.get("skeleton")
        if isinstance(existing_skel, dict):
            for key in ("inputs", "outputs", "computations"):
                if existing_skel.get(key) not in (None, [], "", {}):
                    skeleton_body[key] = existing_skel[key]

    return {"skeleton": skeleton_body}


# ---------------------------------------------------------------------------
# flow_diagram.yaml build
# ---------------------------------------------------------------------------

def _build_flow_diagram(enrichment: dict, mode: str, existing: Any) -> dict:
    """Build the flow_diagram.yaml document content.

    In revise mode, preserve an existing non-empty `flow_diagram` string so
    analyst edits to the ASCII diagram survive re-runs.
    """
    diagram = enrichment["skeleton_flow_diagram"]
    if mode == "revise" and isinstance(existing, dict):
        existing_diagram = existing.get("flow_diagram")
        if isinstance(existing_diagram, str) and existing_diagram:
            diagram = existing_diagram
    return {"flow_diagram": diagram}


# ---------------------------------------------------------------------------
# output-variables.yaml build
# ---------------------------------------------------------------------------

def _build_output_variables(
    enrichment: dict,
    mode: str,
    existing: Any,
) -> tuple[dict, int]:
    """Return (doc, preserved_field_count)."""
    out: dict = {}
    preserved = 0
    existing_dict = existing if isinstance(existing, dict) else {}
    for name, defn in enrichment["output_variables"].items():
        entry: dict = {
            "description": defn["description"],
            "primary": defn["primary"],
        }
        examples = defn.get("examples")
        if isinstance(examples, list) and examples:
            entry["examples"] = list(examples)

        if mode == "revise":
            existing_entry = existing_dict.get(name)
            if isinstance(existing_entry, dict):
                if existing_entry.get("description"):
                    entry["description"] = existing_entry["description"]
                    preserved += 1
                if "examples" in existing_entry and existing_entry["examples"]:
                    entry["examples"] = existing_entry["examples"]
                    preserved += 1
                # primary is not preserved — emit tool enforces single-primary
                # invariant from the enrichment.
        out[name] = entry
    return out, preserved


# ---------------------------------------------------------------------------
# input-variables.yaml build
# ---------------------------------------------------------------------------

def _build_input_variables(
    enrichment: dict,
    signals: dict,
    mode: str,
    existing: Any,
) -> tuple[dict, list[str], int]:
    """Return (doc, warnings, preserved_count)."""
    warnings: list[str] = []
    preserved = 0

    # Build a quick lookup of known field names: every entity field + intermediates.
    known: set[str] = set()
    for fields in signals["entities"].values():
        known.update(fields)
    known.update(signals["intermediate_variables"])

    existing_by_name: dict[str, dict] = {}
    if mode == "revise" and isinstance(existing, dict):
        cats = existing.get("categories")
        if isinstance(cats, list):
            for c in cats:
                if isinstance(c, dict):
                    cname = c.get("category")
                    if isinstance(cname, str):
                        existing_by_name[cname] = c

    categories_out: list[dict] = []
    for i, cat in enumerate(enrichment["input_variables"]["categories"]):
        entry: dict = {
            "category": cat["category"],
            "description": cat["description"],
        }
        examples = cat.get("examples")
        if isinstance(examples, list) and examples:
            entry["examples"] = list(examples)

        fields_out: list[dict] = []
        for j, fld in enumerate(cat["fields"]):
            name_ref = fld["name_ref"]
            if name_ref not in known:
                warnings.append(
                    f"input_variables.categories[{i}].fields[{j}].name_ref "
                    f"{name_ref!r} not found in entities or intermediate_variables"
                )
            fields_out.append({"name_ref": name_ref})
        entry["fields"] = fields_out

        for opt in ("source_file", "source_section", "exact_phrase"):
            if cat.get(opt):
                entry[opt] = cat[opt]

        if mode == "revise":
            existing_entry = existing_by_name.get(cat["category"])
            if isinstance(existing_entry, dict):
                # Preserve analyst-edited description / examples / provenance.
                if existing_entry.get("description"):
                    entry["description"] = existing_entry["description"]
                    preserved += 1
                if existing_entry.get("examples"):
                    entry["examples"] = existing_entry["examples"]
                    preserved += 1
                for opt in ("source_file", "source_section", "exact_phrase"):
                    if existing_entry.get(opt):
                        entry[opt] = existing_entry[opt]
                        preserved += 1
        categories_out.append(entry)

    return {"categories": categories_out}, warnings, preserved


# ---------------------------------------------------------------------------
# constants-and-tables.yaml build
# ---------------------------------------------------------------------------

def _build_constants_and_tables(
    enrichment: dict,
    signals: dict,
    mode: str,
    existing: Any,
) -> tuple[dict, list[dict[str, str]], int]:
    """Return (doc, dropped_entries, preserved_count)."""
    candidates = signals["candidate_constants_and_tables"]
    # First candidate row per name wins for provenance.
    provenance_by_name: dict[str, dict[str, str]] = {}
    for c in candidates:
        if c["name"] not in provenance_by_name:
            provenance_by_name[c["name"]] = {
                "source_file": c["source_file"],
                "source_section": c["source_section"],
            }

    existing_by_name: dict[str, dict] = {}
    if mode == "revise" and isinstance(existing, dict):
        entries = existing.get("constants_and_tables")
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("name"), str):
                    existing_by_name[e["name"]] = e

    out_entries: list[dict] = []
    dropped: list[dict[str, str]] = []
    preserved = 0

    for name, defn in enrichment["constants_and_tables"].items():
        existing_entry = existing_by_name.get(name) if mode == "revise" else None

        if existing_entry is not None:
            # Revise mode: preserve full existing entry, fill only missing fields.
            entry: dict = {"name": name}
            entry["description"] = (
                existing_entry.get("description") or defn["description"]
            )
            if existing_entry.get("description"):
                preserved += 1
            source_file = existing_entry.get("source_file")
            source_section = existing_entry.get("source_section")
            if not source_file or not source_section:
                prov = provenance_by_name.get(name)
                if prov:
                    source_file = source_file or prov["source_file"]
                    source_section = source_section or prov["source_section"]
            if not source_file or not source_section:
                dropped.append({"name": name, "reason": "no matching candidate in signals"})
                continue
            entry["source_file"] = source_file
            entry["source_section"] = source_section
            out_entries.append(entry)
            continue

        prov = provenance_by_name.get(name)
        if prov is None:
            dropped.append({"name": name, "reason": "no matching candidate in signals"})
            continue
        out_entries.append({
            "name": name,
            "description": defn["description"],
            "source_file": prov["source_file"],
            "source_section": prov["source_section"],
        })

    return {"constants_and_tables": out_entries}, dropped, preserved


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.SafeDumper.add_representer(str, _str_representer)


def _serialize_yaml(doc: Any) -> str:
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10_000,
    )


def _atomic_write(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(domain_dir: Path, mode: str, enrichment_path: Path) -> int:
    pre = _preflight(domain_dir, enrichment_path)
    if pre is not None:
        rc, msg = pre
        print(msg, file=sys.stderr)
        return rc

    # Load enrichment JSON.
    try:
        with enrichment_path.open(encoding="utf-8") as f:
            enrichment = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: enrichment file is not valid JSON: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: cannot read enrichment file: {exc}", file=sys.stderr)
        return 1

    # Validate enrichment schema.
    try:
        validate_enrichment(enrichment)
    except EnrichmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Extract signals in-process.
    try:
        signals = extract_signals(domain_dir)
    except yaml.YAMLError as exc:
        print(f"ERROR: YAML parse error: {exc}", file=sys.stderr)
        return 1

    existing_files = signals["existing_files"]

    # create-mode collision check (Step-4 files only).
    if mode == "create":
        collisions = [f for f in _STEP4_FILES if existing_files.get(f) is not None]
        if collisions:
            print(
                "ERROR: file exists. Use --mode replace or --mode revise.\n"
                f"  Existing: {', '.join(collisions)}",
                file=sys.stderr,
            )
            return 2

    # Build each file's content.
    guidance_dir = domain_dir / _GUIDANCE_DIR_REL

    pc_path = domain_dir / _OUTPUT_FILES_REL["prompt-context.yaml"]
    pc_existing_doc: Any = None
    try:
        with pc_path.open(encoding="utf-8") as f:
            pc_existing_doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        pc_existing_doc = {}
    pc_merged, pc_added_counts = _merge_prompt_context(
        pc_existing_doc, enrichment["prompt_context_additions"]
    )

    skeleton_doc = _build_skeleton(
        signals, enrichment, mode, existing_files.get("skeleton.yaml")
    )

    flow_diagram_doc = _build_flow_diagram(
        enrichment, mode, existing_files.get("flow_diagram.yaml")
    )

    output_vars_doc, ov_preserved = _build_output_variables(
        enrichment, mode, existing_files.get("output-variables.yaml")
    )

    input_vars_doc, iv_warnings, iv_preserved = _build_input_variables(
        enrichment, signals, mode, existing_files.get("input-variables.yaml")
    )

    constants_doc, constants_dropped, ct_preserved = _build_constants_and_tables(
        enrichment, signals, mode, existing_files.get("constants-and-tables.yaml")
    )

    # Track which files were "merged" vs "created/replaced" before writing.
    file_actions: dict[str, str] = {}
    file_actions["prompt-context.yaml"] = (
        f"MERGED — {sum(pc_added_counts.values())} additions appended"
        if any(pc_added_counts.values())
        else "MERGED — no additions"
    )
    for fname in _STEP4_FILES:
        existing = existing_files.get(fname)
        if existing is None:
            file_actions[fname] = "CREATED"
        elif mode == "replace":
            file_actions[fname] = "REPLACED"
        else:  # revise
            file_actions[fname] = "REVISED"

    # Write files atomically.
    file_payloads: list[tuple[str, Path, str]] = [
        ("prompt-context.yaml", pc_path, _serialize_yaml(pc_merged)),
        (
            "skeleton.yaml",
            domain_dir / _OUTPUT_FILES_REL["skeleton.yaml"],
            _serialize_yaml(skeleton_doc),
        ),
        (
            "flow_diagram.yaml",
            domain_dir / _OUTPUT_FILES_REL["flow_diagram.yaml"],
            _serialize_yaml(flow_diagram_doc),
        ),
        (
            "output-variables.yaml",
            domain_dir / _OUTPUT_FILES_REL["output-variables.yaml"],
            _serialize_yaml(output_vars_doc),
        ),
        (
            "input-variables.yaml",
            domain_dir / _OUTPUT_FILES_REL["input-variables.yaml"],
            _serialize_yaml(input_vars_doc),
        ),
        (
            "constants-and-tables.yaml",
            domain_dir / _OUTPUT_FILES_REL["constants-and-tables.yaml"],
            _serialize_yaml(constants_doc),
        ),
    ]

    files_written: list[str] = []
    try:
        for _name, dest, content in file_payloads:
            _atomic_write(dest, content)
            files_written.append(str(dest.relative_to(domain_dir)))
    except OSError as exc:
        print(f"ERROR: write failed: {exc}", file=sys.stderr)
        return 1

    # Emit warnings to stderr.
    for w in iv_warnings:
        print(f"WARN: {w}", file=sys.stderr)
    for d in constants_dropped:
        print(
            f"WARN: dropping constant {d['name']!r}: {d['reason']}",
            file=sys.stderr,
        )

    # Header.
    files_preserved_count = ov_preserved + iv_preserved + ct_preserved
    header = {
        "mode": mode,
        "files_written": files_written,
        "files_preserved_count": files_preserved_count,
        "constants_dropped": constants_dropped,
        "warnings": iv_warnings,
    }
    print(json.dumps(header))
    print(_HEADER_SENTINEL)
    print(f"Wrote {len(files_written)} files:")
    for name, _dest, _content in file_payloads:
        action = file_actions.get(name, "")
        path = _OUTPUT_FILES_REL[name]
        print(f"  {path} [{action}]")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an AI-produced enrichment JSON for /create-skeleton and "
            "write five guidance files (prompt-context merge + four Step-4 "
            "outputs) atomically. Enforces the enrichment schema and the "
            "single-primary-output invariant."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "--mode",
        choices=("create", "replace", "revise"),
        required=True,
        help=(
            "create: refuse to overwrite Step-4 files. replace: overwrite "
            "Step-4 files unconditionally. revise: per-field preservation."
        ),
    )
    parser.add_argument(
        "--enrichment",
        required=True,
        help="Path to the AI-produced enrichment.json file.",
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    enrichment_path = Path(args.enrichment)
    return run(domain_dir, args.mode, enrichment_path)


if __name__ == "__main__":
    sys.exit(main())
