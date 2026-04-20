#!/usr/bin/env python3
"""
Existing Test Cases → CSV Exporter

Serializes an existing *_tests.yaml file to CSV using the same column layout
as the test template. Analysts can review, edit, and reimport via import_tests.py.

Output: <tests_yaml_basename>.csv
  Row 1: header
  Row 2: descriptions row (#desc)
  Row 3+: one row per test case

Usage (via xlator CLI):
    xlator export-test-cases <domain> <module>

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from civil_helpers import (
    FieldSpec,
    build_csv_field_specs,
    field_description_hint,
    load_civil_yaml,
)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_value(value, spec: FieldSpec) -> str:
    """Convert a YAML test value to a CSV cell string."""
    if value is None:
        return ""

    ct = spec.civil_type

    if ct in ("list", "set"):
        # Serialize list of dicts or list of strings
        if not isinstance(value, list):
            return str(value)
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                # Join all field values — no hard-coded field name
                parts.append(";".join(str(v) for v in item.values()))
            else:
                parts.append(str(item))
        return ";".join(parts)

    if ct == "bool":
        return "true" if value else "false"

    if ct == "money":
        # Emit as plain number (int or float)
        if isinstance(value, float) and value != int(value):
            return f"{value:.2f}"
        return str(int(value)) if isinstance(value, (int, float)) else str(value)

    return str(value) if value is not None else ""


def _get_fact_value(test_inputs: dict, spec: FieldSpec) -> str:
    """Extract a fact field value from test inputs dict using column_name."""
    col = spec.column_name
    if "." in col:
        # Multi-entity: "EntityName.field_name"
        entity, field_name = col.split(".", 1)
        val = test_inputs.get(col)  # Try full key first
        if val is None:
            # Fall back to dot-separated lookup in nested dict
            entity_data = test_inputs.get(entity, {})
            if isinstance(entity_data, dict):
                val = entity_data.get(field_name)
    else:
        val = test_inputs.get(col)
    return _serialize_value(val, spec)


def _get_decision_value(test_expected: dict, spec: FieldSpec) -> str:
    """Extract a decision value from test expected dict."""
    dec_name = spec.decision_name
    val = test_expected.get(dec_name)
    return _serialize_value(val, spec)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export existing YAML test cases to CSV."
    )
    parser.add_argument("civil_yaml", help="Path to the CIVIL spec YAML file")
    parser.add_argument("tests_yaml", help="Path to the *_tests.yaml file")
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: same directory as tests_yaml)"
    )
    args = parser.parse_args()

    civil_path = Path(args.civil_yaml)
    tests_path = Path(args.tests_yaml)

    civil_doc = load_civil_yaml(civil_path)

    if not tests_path.exists():
        print(f"ERROR: tests file not found: {tests_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(tests_path) as f:
            tests_doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"ERROR: YAML parse error in {tests_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = tests_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / (tests_path.stem + ".csv")

    if output_file.exists():
        print(f"WARN: overwriting {output_file}", file=sys.stderr)

    specs = build_csv_field_specs(civil_doc)
    fact_specs = [s for s in specs if not s.is_decision]
    decision_specs = [s for s in specs if s.is_decision]

    header = ["case_id", "description"] + [s.column_name for s in specs] + ["tags", "notes"]

    desc_row = ["#desc", "[Description]"]
    for spec in specs:
        desc_row.append(field_description_hint(spec))
    desc_row.append("Optional. Comma-separated tags")
    desc_row.append("Optional. Free-text notes")

    test_cases = tests_doc.get("tests", []) if isinstance(tests_doc, dict) else []

    rows: list[list[str]] = []
    for tc in test_cases:
        if not isinstance(tc, dict):
            continue
        case_id = str(tc.get("case_id", ""))
        description = str(tc.get("description", ""))
        inputs: dict = tc.get("inputs", {}) or {}
        expected: dict = tc.get("expected", {}) or {}

        row = [case_id, description]

        for spec in fact_specs:
            row.append(_get_fact_value(inputs, spec))

        for spec in decision_specs:
            row.append(_get_decision_value(expected, spec))

        # tags
        tags = tc.get("tags", [])
        if isinstance(tags, list):
            row.append(",".join(str(t) for t in tags))
        else:
            row.append(str(tags) if tags else "")

        # notes
        notes = tc.get("notes", "")
        row.append(str(notes) if notes else "")

        rows.append(row)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(desc_row)
        for row in rows:
            writer.writerow(row)

    print(f"OK  exported {len(rows)} case(s) → {output_file}")


if __name__ == "__main__":
    main()
