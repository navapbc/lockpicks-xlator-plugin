#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
CIVIL Spec → CSV Test Template Generator

Generates a CSV template for policy analysts to author test cases without
needing to understand the CIVIL YAML structure.

Output: <module>_test_template.csv
  Row 1: header (case_id, description, fact fields, expected_* decisions, tags, notes)
  Row 2: descriptions (#desc row — visible in Excel/Google Sheets)
  Row 3: one approve/allow example row
  Row 4: one deny example row

Usage (via xlator CLI):
    xlator export-test-template <domain> <module>

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

# Allow running directly or as a module
sys.path.insert(0, str(Path(__file__).parent))

from civil_helpers import build_csv_field_specs, field_description_hint, load_civil_yaml


# ---------------------------------------------------------------------------
# Placeholder value generators
# ---------------------------------------------------------------------------

def _placeholder_allow(spec, index: int) -> str:
    """Return a placeholder allow/approve value for a field spec."""
    ct = spec.civil_type
    if spec.is_decision:
        # decision columns
        if ct == "bool":
            return "true"
        if ct == "enum" and spec.enum_values:
            # Pick the first value that looks like approve/allow/yes
            for v in spec.enum_values:
                if v.lower() in ("approve", "allow", "yes", "eligible", "true"):
                    return v
            return spec.enum_values[0]
        if ct in ("list", "set"):
            return ""  # empty = no denial reasons → eligible
        if ct == "money":
            return "1000"
        if ct == "int":
            return "1"
        return ""
    else:
        # fact columns
        if ct == "bool":
            return "false"
        if ct == "int":
            return str(1 + index)
        if ct == "money":
            return "1000"
        if ct == "float":
            return "1.0"
        if ct == "enum" and spec.enum_values:
            return spec.enum_values[0]
        if ct == "date":
            return "2026-01-01"
        return "example"


def _placeholder_deny(spec, index: int) -> str:
    """Return a placeholder deny value for a field spec."""
    ct = spec.civil_type
    if spec.is_decision:
        if ct == "bool":
            return "false"
        if ct == "enum" and spec.enum_values:
            for v in spec.enum_values:
                if v.lower() in ("deny", "denied", "no", "ineligible", "false"):
                    return v
            return spec.enum_values[-1]
        if ct in ("list", "set"):
            return "DENY_REASON_CODE"
        if ct == "money":
            return "0"
        if ct == "int":
            return "0"
        return ""
    else:
        # Reuse allow placeholder for fact fields in deny row (same inputs, different outcome)
        return _placeholder_allow(spec, index)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a CSV test template from a CIVIL spec."
    )
    parser.add_argument("civil_yaml", help="Path to the CIVIL spec YAML file")
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: same directory as civil_yaml)"
    )
    args = parser.parse_args()

    civil_path = Path(args.civil_yaml)
    civil_doc = load_civil_yaml(civil_path)

    module_name: str = civil_doc.get("module", civil_path.stem)

    # Determine output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = civil_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{module_name}_test_template.csv"

    if output_file.exists():
        print(f"WARN: overwriting {output_file}", file=sys.stderr)

    specs = build_csv_field_specs(civil_doc)

    # Build column headers
    header = ["case_id", "description"] + [s.column_name for s in specs] + ["tags", "notes"]

    # Build descriptions row
    desc_row = ["#desc", "[Description — required; human-readable summary of this test case]"]
    for spec in specs:
        desc_row.append(field_description_hint(spec))
    desc_row.append("Optional. Comma-separated tags (e.g. allow,boundary,size_3)")
    desc_row.append("Optional. Free-text notes about this test case")

    # Build allow example row
    allow_row = ["allow_001", "Basic approval example"]
    for i, spec in enumerate(specs):
        allow_row.append(_placeholder_allow(spec, i))
    allow_row.append("allow")
    allow_row.append("")

    # Build deny example row
    deny_row = ["deny_001", "Basic denial example"]
    for i, spec in enumerate(specs):
        deny_row.append(_placeholder_deny(spec, i))
    deny_row.append("deny")
    deny_row.append("")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(desc_row)
        writer.writerow(allow_row)
        writer.writerow(deny_row)

    print(f"OK  wrote {output_file}")


if __name__ == "__main__":
    main()
