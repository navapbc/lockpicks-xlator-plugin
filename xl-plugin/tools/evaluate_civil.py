#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator evaluate-civil: evaluate a CIVIL ruleset against an inputs JSON file
and emit the resulting outputs + computed-field values as JSON on stdout.

Usage:
    xlator evaluate-civil <domain> <program> --inputs <path>

Reads:
    $DOMAINS_FULLPATH/<domain>/specs/<program>.civil.yaml
    <path>                                   (JSON: flat key-value inputs)

Emits (stdout, JSON):
    {
      "outputs":  {...},
      "computed": {...},
      "reasons":  [...],
      "debug":    {"rules_fired": [...]}
    }

Exit codes:
    0  success
    1  evaluation error (missing input, unsupported feature, invalid expr)
    2  pre-flight failure (missing domain, CIVIL file, or inputs file)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import civil_eval  # noqa: E402


def _preflight(domain_dir: Path, civil_path: Path, inputs_path: Path) -> None:
    """Verify all required paths exist. Exits 2 with a clear error on miss."""
    if not domain_dir.is_dir():
        print(f"Error: domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)
    if not civil_path.is_file():
        print(f"Error: CIVIL file not found: {civil_path}", file=sys.stderr)
        sys.exit(2)
    if not inputs_path.is_file():
        print(f"Error: inputs file not found: {inputs_path}", file=sys.stderr)
        sys.exit(2)


def _load_civil(civil_path: Path) -> dict:
    with civil_path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        print(f"Error: CIVIL file is not a YAML mapping: {civil_path}", file=sys.stderr)
        sys.exit(1)
    return doc


def _load_inputs(inputs_path: Path) -> dict:
    with inputs_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        print(f"Error: inputs file must be a JSON object: {inputs_path}", file=sys.stderr)
        sys.exit(1)
    return data


def cmd_evaluate(domain_dir: Path, program: str, inputs_path: Path) -> dict:
    """Evaluate one CIVIL module against the supplied inputs and return the
    result as a JSON-serializable dict. Raises civil_eval.EvaluationError on
    evaluation failure (caller maps to exit 1)."""
    civil_path = domain_dir / "specs" / f"{program}.civil.yaml"
    _preflight(domain_dir, civil_path, inputs_path)

    civil_doc = _load_civil(civil_path)
    inputs = _load_inputs(inputs_path)

    result = civil_eval.evaluate_civil(civil_doc, inputs)
    return result.as_dict()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a CIVIL ruleset against an inputs JSON file."
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("program", help="Program name (matches specs/<program>.civil.yaml)")
    parser.add_argument(
        "--inputs",
        required=True,
        help="Path to JSON file containing the inputs dict (flat key-value).",
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(2)

    domain_dir = Path(domains_root) / args.domain
    inputs_path = Path(args.inputs)

    try:
        result = cmd_evaluate(domain_dir, args.program, inputs_path)
    except civil_eval.EvaluationError as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as exc:
        print(f"YAML parse error: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"JSON parse error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)


if __name__ == "__main__":
    main()
