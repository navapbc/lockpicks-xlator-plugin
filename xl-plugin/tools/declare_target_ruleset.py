#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator declare-target-ruleset: deterministic three-file bootstrap from a
suggested-target ruleset file produced by /suggest-target-ruleset.

Reads one specs/suggested_targets/<ruleset_name>.yaml and writes:
  - specs/naming-manifest.yaml         (version, inputs, computed?, outputs)
  - specs/guidance/metadata.yaml       (display_name, description)
  - specs/guidance/prompt-context.yaml (role, scope, constraints seed,
                                        standards, guidance, edge_cases: [])

Field-mapping rules:

  - inputs/computed/outputs entries carry `type:` and `description:` only when
    the suggestion supplies them; absent keys are omitted (not written as null).
    Any other keys present on a suggestion entry are silently dropped.
  - Provenance fields (policy_phrase, source_doc, section, synonyms) are NEVER
    written on seeded entries; /extract-ruleset Step 7 fills them in once an
    analyst confirms a seeded name against an observed phrase.
  - Output declaration order is preserved — /create-skeleton uses the first
    output as the primary decision when writing guidance/output-variables.yaml.
  - `constraints:` is a fixed 6-entry seed list, encoded in this file as
    `_CONSTRAINTS_SEED`. Future seed edits update this constant — single
    source of truth.
  - `edge_cases: []` always at creation; /create-skeleton populates it later.
  - `computed:` block is omitted entirely when the suggestion has no computed
    entries (matches current skill behavior).

The tool overwrites existing output files unconditionally. The skill is
responsible for prompting the analyst before invocation.

Partial-write risk on hard crash between writes is identical to today's
AI-driven 3-Write-call sequence — no regression. Re-running is safe.

Usage:
    xlator declare-target-ruleset <domain> <ruleset_name>

Exit codes:
    0 — success
    2 — pre-flight failure (missing domain, missing suggestion file,
        unset DOMAINS_FULLPATH, argparse errors)
    1 — unexpected error (malformed suggestion YAML, IO error)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml


_SUGGESTED_TARGETS_REL = "specs/suggested_targets"
_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_METADATA_REL = "specs/guidance/metadata.yaml"
_PROMPT_CONTEXT_REL = "specs/guidance/prompt-context.yaml"

_MANIFEST_VERSION = "2.0"

# Load-bearing: single source of truth for the constraints seed list.
# Future edits to the seed update this constant. Tests assert verbatim equality.
_CONSTRAINTS_SEED: tuple[str, ...] = (
    "Do not interpret beyond the text; do not add requirements that aren't stated.",
    "Ensure every rule has citations.",
    "Create a list of unknowns/gaps (things needed but not defined in the text).",
    "List any assumptions made.",
    "Do not invent verification requirements.",
    "Ensure no rule introduces concepts not present in the policy.",
)


def _load_yaml(path: Path) -> Any:
    """Parse YAML at `path`. Raises on parse failure with a wrapped error
    so main() can map it to exit code 1 with the standard stderr format."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _entry_subset(entry: Any) -> dict[str, Any]:
    """Return a new dict containing the seed-time subset of fields from
    `entry` — and only when those keys are present.

    Seedable fields (kept):
      - `type`: Catala-native type name, exactly one of `integer`, `decimal`,
        `money`, `boolean`, `date`, `duration`, `string`, `enum`, `list`,
        `structure`. Nullable — analysts may declare a field's intended type
        when known and leave it absent (or write `null`) when uncertain. The
        merge tool rejects any other value at /extract-ruleset Step 7.
      - `description`: short prose description of the field.
      - `optional` (U7): boolean flag — `Optional<T>` wrapping in the Catala
        emission. Nullable initial value; analysts confirm in /extract-ruleset
        Step 7.
      - `enum_variants` (U7): list of constructor names for enum types.
        Nullable initial value; analysts confirm in /extract-ruleset Step 7.

    Every other key on the suggestion entry (e.g. `policy_phrase:`,
    `source_doc:`, `section:`, `synonyms:`) is dropped — those are filled in
    by /extract-ruleset Step 7 once the analyst confirms a seeded name
    against an observed phrase. Returns `{}` when `entry` is not a dict."""
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    if "type" in entry:
        out["type"] = entry["type"]
    if "description" in entry:
        out["description"] = entry["description"]
    if "optional" in entry:
        out["optional"] = entry["optional"]
    if "enum_variants" in entry:
        out["enum_variants"] = entry["enum_variants"]
    return out


def _build_inputs(raw: Any) -> dict[str, Any]:
    """Build manifest `inputs:` block (entity-grouped, keyed by entity name).
    Each field maps to `{type?, description?}` via `_entry_subset`."""
    if not isinstance(raw, dict):
        return {}
    inputs: dict[str, Any] = {}
    for entity_name, fields in raw.items():
        if not isinstance(fields, dict):
            inputs[entity_name] = {}
            continue
        entity_block: dict[str, Any] = {}
        for field_name, field_entry in fields.items():
            entity_block[field_name] = _entry_subset(field_entry)
        inputs[entity_name] = entity_block
    return inputs


def _build_flat(raw: Any) -> dict[str, Any]:
    """Build a flat block (used for `computed:` and `outputs:`). Each key
    maps to `{type?, description?}`."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for name, entry in raw.items():
        out[name] = _entry_subset(entry)
    return out


def _build_naming_manifest(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Build the naming-manifest.yaml document. `computed:` is omitted when
    the suggestion has no computed entries; `inputs:` and `outputs:` are
    always present (may be empty dicts if the suggestion lacks them)."""
    manifest: dict[str, Any] = {"version": _MANIFEST_VERSION}
    manifest["inputs"] = _build_inputs(suggestion.get("inputs"))
    computed = _build_flat(suggestion.get("computed"))
    if computed:
        manifest["computed"] = computed
    manifest["outputs"] = _build_flat(suggestion.get("outputs"))
    return manifest


def _build_metadata(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Build guidance/metadata.yaml — verbatim copies of display_name and
    description. Absent fields write as empty strings (per Scope Boundaries:
    schema enforcement belongs to /suggest-target-ruleset)."""
    return {
        "display_name": suggestion.get("display_name", ""),
        "description": suggestion.get("description", ""),
    }


def _build_prompt_context(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Build guidance/prompt-context.yaml — role/scope verbatim, constraints
    from the seed constant, standards/guidance verbatim lists, edge_cases
    always empty."""
    standards = suggestion.get("standards")
    if not isinstance(standards, list):
        standards = []
    guidance = suggestion.get("guidance")
    if not isinstance(guidance, list):
        guidance = []
    return {
        "role": suggestion.get("role", ""),
        "scope": suggestion.get("scope", ""),
        "constraints": list(_CONSTRAINTS_SEED),
        "standards": list(standards),
        "guidance": list(guidance),
        "edge_cases": [],
    }


def _serialize(doc: dict[str, Any]) -> str:
    """Serialize a YAML doc with stable, declaration-order output. `width`
    is set high so long descriptions stay on a single line (matches the
    canonical formatting of hand-authored manifests)."""
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10_000,
    )


def _atomic_write(dest: Path, content: str) -> None:
    """Write `content` to `dest` via `tmp + os.replace` so a failed write
    leaves the prior file intact."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


def _format_summary(
    suggestion: dict[str, Any],
    manifest: dict[str, Any],
    creation_lines: list[str],
) -> str:
    """Format the binding stdout contract: 5 summary lines, blank line,
    then the three `Created <path>` lines."""
    display_name = suggestion.get("display_name", "")
    description = suggestion.get("description", "")

    inputs_block = manifest.get("inputs") or {}
    input_entities = ", ".join(inputs_block.keys()) if inputs_block else "none"

    computed_block = manifest.get("computed") or {}
    computed_names = ", ".join(computed_block.keys()) if computed_block else "none"

    outputs_raw = suggestion.get("outputs") or {}
    primary_name = ""
    primary_type = ""
    secondary_names: list[str] = []
    if isinstance(outputs_raw, dict) and outputs_raw:
        names = list(outputs_raw.keys())
        primary_name = str(names[0])
        first_entry = outputs_raw[names[0]]
        if isinstance(first_entry, dict) and "type" in first_entry:
            primary_type = str(first_entry["type"])
        secondary_names = [str(n) for n in names[1:]]

    output_line = (
        f"{primary_name} ({primary_type})" if primary_type else primary_name
    )
    secondary_line = ", ".join(secondary_names) if secondary_names else "none"

    summary_lines = [
        f"Ruleset: {display_name}",
        f"Description: {description}",
        f"Inputs: {input_entities}",
        f"Computed: {computed_names}",
        f"Output: {output_line}",
        f"Secondary outputs: {secondary_line}",
    ]
    return "\n".join(summary_lines) + "\n\n" + "\n".join(creation_lines)


def run(domain_dir: Path, ruleset_name: str) -> int:
    """Read the suggestion, build the three docs in memory, then write atomically."""
    suggestion_path = domain_dir / _SUGGESTED_TARGETS_REL / f"{ruleset_name}.yaml"
    if not suggestion_path.exists():
        print(f"Ruleset file not found: {suggestion_path}", file=sys.stderr)
        suggested_targets_dir = domain_dir / _SUGGESTED_TARGETS_REL
        if suggested_targets_dir.is_dir():
            available = sorted(
                p.stem for p in suggested_targets_dir.glob("*.yaml")
            )
            if available:
                print("Available ruleset files:", file=sys.stderr)
                for name in available:
                    print(f"  - {name}", file=sys.stderr)
        return 2

    try:
        suggestion = _load_yaml(suggestion_path)
    except yaml.YAMLError as exc:
        print(
            f"ERROR: {suggestion_path} failed to parse: {exc}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(suggestion, dict):
        print(
            f"ERROR: {suggestion_path} did not parse as a YAML mapping.",
            file=sys.stderr,
        )
        return 1

    manifest_doc = _build_naming_manifest(suggestion)
    metadata_doc = _build_metadata(suggestion)
    prompt_context_doc = _build_prompt_context(suggestion)

    manifest_yaml = _serialize(manifest_doc)
    metadata_yaml = _serialize(metadata_doc)
    prompt_context_yaml = _serialize(prompt_context_doc)

    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    metadata_path = domain_dir / _METADATA_REL
    prompt_context_path = domain_dir / _PROMPT_CONTEXT_REL

    _atomic_write(manifest_path, manifest_yaml)
    _atomic_write(metadata_path, metadata_yaml)
    _atomic_write(prompt_context_path, prompt_context_yaml)

    creation_lines = [
        f"Created {manifest_path}",
        f"Created {metadata_path}",
        f"Created {prompt_context_path}",
    ]
    print(_format_summary(suggestion, manifest_doc, creation_lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap a domain's specs/naming-manifest.yaml and "
            "specs/guidance/{metadata,prompt-context}.yaml from a "
            "specs/suggested_targets/<ruleset_name>.yaml file."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "ruleset_name",
        help="Ruleset stem (matches specs/suggested_targets/<ruleset_name>.yaml)",
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print(
            "Error: DOMAINS_FULLPATH not set in environment.",
            file=sys.stderr,
        )
        return 2

    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        print(f"Domain not found: {domain_dir}", file=sys.stderr)
        return 2

    return run(domain_dir, args.ruleset_name)


if __name__ == "__main__":
    sys.exit(main())
