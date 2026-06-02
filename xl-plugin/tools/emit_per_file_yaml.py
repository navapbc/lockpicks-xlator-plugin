#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator emit-per-file-yaml: emit a per-source policy_facets/computations/<rel>.md.yaml
file from a JSON payload supplied on stdin.

This is the writer half of /extract-computations: the AI worker generates
the section data as JSON, pipes it through this tool, and the tool validates
the per-computation expr_hint shape and writes the YAML atomically
(tmp + os.replace) with the standard preamble.

Usage:
    uv run xl-plugin/tools/emit_per_file_yaml.py < payload.json
    echo '<json>' | xlator emit-per-file-yaml

Input JSON shape:
    {
      "destination": "<absolute path to .md.yaml file>",
      "source_rel":  "input/policy_docs/<rel>.md",
      "sections": [
        { "heading": "...", "summary": "...", "tags": [...],
          "stage": "...", "stage_source": "...",
          "computations": [
            { "description": "...",
              "preconditions": [...],
              "expr_hint": "<output_name> = <expression>" }
          ]
        }
      ]
    }

`expr_hint:` shape (when present): "<output_name> = <expression>" — single `=`
separator; LHS must be a non-empty snake_case identifier (lowercase letters,
digits, underscores; first char a letter or underscore); RHS is the
expression. Computations without an expression omit `expr_hint:` entirely
(descriptive-only path; consumers fall back to `description:` prose).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml


class ValidationError(Exception):
    """Raised when the input payload violates an invariant."""


_SNAKE_CASE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate(payload: dict) -> None:
    """Raise ValidationError if the payload violates an invariant."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    for required in ("destination", "source_rel", "sections"):
        if required not in payload:
            raise ValidationError(f"missing required field: {required}")

    sections = payload["sections"]
    if not isinstance(sections, list):
        raise ValidationError("sections must be an array")

    for s_idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        computations = section.get("computations") or []
        if not isinstance(computations, list):
            raise ValidationError(
                f"sections[{s_idx}].computations must be a list when present"
            )
        for c_idx, computation in enumerate(computations):
            if not isinstance(computation, dict):
                continue
            _validate_computation(computation, s_idx, c_idx)
        if "variables" in section:
            _validate_variables(section["variables"], s_idx)


def _validate_computation(computation: dict, s_idx: int, c_idx: int) -> None:
    """Validate per-computation invariants."""
    where = f"sections[{s_idx}].computations[{c_idx}]"

    expr_hint = computation.get("expr_hint")
    if expr_hint is None:
        return
    if not isinstance(expr_hint, str):
        raise ValidationError(f"{where}.expr_hint must be a string when present")
    if "=" not in expr_hint:
        raise ValidationError(
            f"{where}.expr_hint must be of the form 'output_name = <expression>'; "
            f"bare expressions (no '=') are no longer accepted (got {expr_hint!r})"
        )
    lhs, _, rhs = expr_hint.partition("=")
    lhs = lhs.strip()
    rhs = rhs.strip()
    if not lhs:
        raise ValidationError(
            f"{where}.expr_hint has empty output name on the left of '='; "
            f"expected 'output_name = <expression>' (got {expr_hint!r})"
        )
    if not _SNAKE_CASE_IDENTIFIER.match(lhs):
        raise ValidationError(
            f"{where}.expr_hint output name {lhs!r} is not a snake_case identifier "
            f"(lowercase letters, digits, underscores; first char a letter or underscore)"
        )
    if not rhs:
        raise ValidationError(
            f"{where}.expr_hint has empty expression on the right of '='; "
            f"expected 'output_name = <expression>' (got {expr_hint!r})"
        )


def _validate_variables(variables, s_idx: int) -> None:
    """Validate the per-section `variables:` block.

    Shape: a dict keyed by snake_case variable name; each value is a dict
    whose only supported key is the **required** `policy_phrase:` (non-empty
    string). When no verbatim noun phrase exists in the source body, the
    skill emits the deterministic-fallback string per
    `xl-plugin/core/naming_guide.md` (description noun phrase → section
    heading → parent heading → first sentence). Empty per-variable dicts
    (`<var>: {}`) are rejected — phrase absence at the section level is not
    a supported state."""
    where = f"sections[{s_idx}].variables"
    if not isinstance(variables, dict):
        raise ValidationError(f"{where} must be a map when present")
    for var_name, entry in variables.items():
        if not isinstance(var_name, str) or not _SNAKE_CASE_IDENTIFIER.match(var_name):
            raise ValidationError(
                f"{where} key {var_name!r} is not a snake_case identifier "
                f"(lowercase letters, digits, underscores; first char a letter or underscore)"
            )
        if not isinstance(entry, dict):
            raise ValidationError(
                f"{where}[{var_name!r}] must be a map (got {type(entry).__name__})"
            )
        if "policy_phrase" not in entry or entry["policy_phrase"] is None:
            raise ValidationError(
                f"{where}[{var_name!r}].policy_phrase is required and must be a non-empty string"
            )
        phrase = entry["policy_phrase"]
        if not isinstance(phrase, str):
            raise ValidationError(
                f"{where}[{var_name!r}].policy_phrase must be a string "
                f"(got {type(phrase).__name__})"
            )
        if not phrase:
            raise ValidationError(
                f"{where}[{var_name!r}].policy_phrase must be non-empty"
            )


def _strip_none(d: dict) -> dict:
    """Remove keys whose value is None so optional fields are omitted from YAML."""
    return {k: v for k, v in d.items() if v is not None}


def _normalize_sections(sections: list) -> list:
    """Strip None-valued optional fields from each section, computation, and
    per-variable entry in the optional `variables:` block."""
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
        if "variables" in section_clean and isinstance(section_clean["variables"], dict):
            section_clean["variables"] = {
                var_name: _strip_none(entry) if isinstance(entry, dict) else entry
                for var_name, entry in section_clean["variables"].items()
            }
        cleaned.append(section_clean)
    return cleaned


def emit(payload: dict) -> None:
    """Validate the payload and write the YAML to its destination atomically."""
    _validate(payload)

    destination = Path(payload["destination"])
    source_rel = payload["source_rel"]

    body = {
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
