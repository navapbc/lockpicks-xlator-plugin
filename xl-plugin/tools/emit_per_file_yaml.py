#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator emit-per-file-yaml: emit a per-source policy_facets/computations/<rel>.md.yaml
file from a JSON payload supplied on stdin.

This is the writer half of /extract-computations: the AI worker generates
the section data and naming_manifest as JSON, pipes it through this tool, and
the tool validates the cross-block name-set invariant and writes the YAML
atomically (tmp + os.replace) with the standard preamble.

Usage:
    uv run xl-plugin/tools/emit_per_file_yaml.py < payload.json
    echo '<json>' | xlator emit-per-file-yaml

Input JSON shape:
    {
      "destination": "<absolute path to .md.yaml file>",
      "source_rel":  "input/policy_docs/<rel>.md",
      "naming_manifest": {
        "variables": {
          "<name>": {
            "policy_phrase": "...",
            "role_hint": "input|computed|output",   # optional
            "source_section": "..."
          }
        }
      },
      "sections": [
        { "heading": "...", "summary": "...", "tags": [...],
          "phase": "...", "phase_source": "...",
          "computations": [
            { "description": "...", "variables": ["..."],
              "preconditions": [...], "expr_hint": "..." }
          ]
        }
      ]
    }

Cross-block invariant: every name appearing in
sections[*].computations[*].variables MUST be a key in
naming_manifest.variables. Worker correctness is enforced at write time, not
discovered downstream.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml


class ValidationError(Exception):
    """Raised when the input payload violates an invariant."""


def _collect_section_variables(sections: list) -> set[str]:
    """Return every variable name referenced in sections[*].computations[*].variables."""
    names: set[str] = set()
    for section in sections or []:
        for computation in section.get("computations") or []:
            for var in computation.get("variables") or []:
                if isinstance(var, str):
                    names.add(var)
    return names


def _validate(payload: dict) -> None:
    """Raise ValidationError if the payload violates an invariant."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    for required in ("destination", "source_rel", "naming_manifest", "sections"):
        if required not in payload:
            raise ValidationError(f"missing required field: {required}")

    naming_manifest = payload["naming_manifest"]
    if not isinstance(naming_manifest, dict):
        raise ValidationError("naming_manifest must be an object")
    variables = naming_manifest.get("variables", {})
    if not isinstance(variables, dict):
        raise ValidationError("naming_manifest.variables must be an object")

    sections = payload["sections"]
    if not isinstance(sections, list):
        raise ValidationError("sections must be an array")

    section_vars = _collect_section_variables(sections)
    manifest_keys = set(variables.keys())
    missing = sorted(section_vars - manifest_keys)
    if missing:
        raise ValidationError(
            "section variables missing from naming_manifest.variables: "
            + ", ".join(missing)
        )


def _strip_none(d: dict) -> dict:
    """Remove keys whose value is None so optional fields are omitted from YAML."""
    return {k: v for k, v in d.items() if v is not None}


def _normalize_naming_manifest(naming_manifest: dict) -> dict:
    """Ensure variable entries omit None-valued optional fields like role_hint."""
    variables = naming_manifest.get("variables", {}) or {}
    cleaned = {
        name: _strip_none(entry) if isinstance(entry, dict) else entry
        for name, entry in variables.items()
    }
    return {"variables": cleaned}


def _normalize_sections(sections: list) -> list:
    """Strip None-valued optional fields from each section and computation."""
    cleaned: list = []
    for section in sections or []:
        if not isinstance(section, dict):
            cleaned.append(section)
            continue
        section_clean = _strip_none(section)
        if "computations" in section_clean and isinstance(section_clean["computations"], list):
            section_clean["computations"] = [
                _strip_none(c) if isinstance(c, dict) else c
                for c in section_clean["computations"]
            ]
        cleaned.append(section_clean)
    return cleaned


def emit(payload: dict) -> None:
    """Validate the payload and write the YAML to its destination atomically."""
    _validate(payload)

    destination = Path(payload["destination"])
    source_rel = payload["source_rel"]

    body = {
        "naming_manifest": _normalize_naming_manifest(payload["naming_manifest"]),
        "sections": _normalize_sections(payload["sections"]),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated by /extract-computations — do not edit manually\n")
        f.write(f"# Source: {source_rel}\n")
        f.write("\n")
        yaml.safe_dump(
            body,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    os.replace(tmp, destination)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"emit-per-file-yaml: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    try:
        emit(payload)
    except ValidationError as exc:
        print(f"emit-per-file-yaml: validation error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"emit-per-file-yaml: write failed: {exc}", file=sys.stderr)
        return 4

    print(f"ok {payload['destination']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
