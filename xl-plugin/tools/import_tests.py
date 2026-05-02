#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
CSV / YAML → *_tests.yaml Importer

Validates and upserts test cases from a CSV (or native YAML) file into the
target *_tests.yaml file. All validation errors are collected before any write.

Usage (via xlator CLI):
    xlator import-tests <domain> <module> <csv_or_yaml_file>
    xlator import-tests <domain> <module> <csv_or_yaml_file> --dry-run
    xlator import-tests <domain> <module> <csv_or_yaml_file> --format yaml
    xlator import-tests <domain> <module> - --format yaml  # stdin

Options (passed through by xlator):
    --dry-run              Validate and report without writing
    --format {csv,yaml}    Input format (default: csv)
    --no-comment-check     Skip the YAML-comment-loss warning prompt
    --output-format {text,json}  Output format (default: text)

Exit codes:
    0 — success (or dry-run with no errors)
    1 — error
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from civil_helpers import (
    FieldSpec,
    build_csv_field_specs,
    load_civil_yaml,
)


# ---------------------------------------------------------------------------
# Money parsing
# ---------------------------------------------------------------------------

def parse_money(raw: str) -> int | float:
    """Parse a money string to int (whole dollars) or float (with cents).

    Accepts: "3500", "$3,500", "$3,500.00", "1824.17", "$1,824.17"
    Returns int when value is whole dollars, float with 2dp otherwise.
    Raises ValueError on non-numeric input.
    """
    cleaned = raw.strip().lstrip("$").replace(",", "")
    if not cleaned:
        raise ValueError(f"empty money value")
    val = float(cleaned)
    if val == int(val):
        return int(val)
    return round(val, 2)


# ---------------------------------------------------------------------------
# Type coercion (CIVIL-spec-driven)
# ---------------------------------------------------------------------------

BOOL_TRUE = {"true", "True", "TRUE", "1"}
BOOL_FALSE = {"false", "False", "FALSE", "0"}


def coerce_value(raw: str, spec: FieldSpec, row_num: int, case_id: str,
                 errors: list[dict]) -> Any:
    """Convert raw CSV string to Python value per CIVIL type.

    Appends to errors on failure. Returns None on error (caller skips the field).
    Returns the sentinel _OMIT if the field should be omitted from YAML.
    """
    stripped = raw.strip()

    ct = spec.civil_type
    col = spec.column_name

    # Empty cell handling
    if stripped == "":
        if spec.optional or spec.is_decision and ct in ("list", "set"):
            return _OMIT
        # non-optional decision or fact: we'll check required-ness outside
        return _OMIT

    if ct == "bool":
        if stripped in BOOL_TRUE:
            return True
        if stripped in BOOL_FALSE:
            return False
        errors.append({
            "row": row_num, "case_id": case_id, "field": col,
            "value": stripped,
            "message": f"cannot parse '{stripped}' as bool (expected true/false)",
            "code": "INVALID_BOOL",
        })
        return None

    if ct == "int":
        try:
            return int(stripped)
        except ValueError:
            errors.append({
                "row": row_num, "case_id": case_id, "field": col,
                "value": stripped,
                "message": f"cannot parse '{stripped}' as int",
                "code": "INVALID_INT",
            })
            return None

    if ct == "float":
        try:
            return float(stripped)
        except ValueError:
            errors.append({
                "row": row_num, "case_id": case_id, "field": col,
                "value": stripped,
                "message": f"cannot parse '{stripped}' as float",
                "code": "INVALID_FLOAT",
            })
            return None

    if ct == "money":
        try:
            return parse_money(stripped)
        except ValueError:
            errors.append({
                "row": row_num, "case_id": case_id, "field": col,
                "value": stripped,
                "message": f"cannot parse '{stripped}' as money for {col}",
                "code": "INVALID_MONEY",
            })
            return None

    if ct == "enum":
        valid = spec.enum_values or []
        if stripped not in valid:
            errors.append({
                "row": row_num, "case_id": case_id, "field": col,
                "value": stripped,
                "message": f"value \"{stripped}\" not in enum [{', '.join(valid)}]",
                "code": "INVALID_ENUM",
                "valid_values": valid,
            })
            return None
        return stripped

    if ct in ("list", "set"):
        # Semicolon-separated strings
        parts = [p.strip() for p in stripped.split(";") if p.strip()]
        return parts  # will be converted to dicts in upsert step

    # string, date, other
    return stripped


# Sentinel to indicate a field should be omitted from YAML output
_OMIT = object()


# ---------------------------------------------------------------------------
# Dict-item reconstruction for list decisions
# ---------------------------------------------------------------------------

def _reconstruct_list_items(parts: list[str], existing_items: list,
                            item_type: Optional[str] = None) -> list:
    """Convert semicolon-split strings to dict items by matching existing YAML entries.

    If existing_items has dict entries, inspect their field values to infer structure.
    E.g., "CODE_A" + existing [{code: "X"}] → [{code: "CODE_A"}]
    If no existing entries but item_type is set (typed objects like Reason), defaults
    to {code: value}. Falls back to plain strings only for untyped string lists.
    """
    if not parts:
        return []

    # Find the first dict in existing items to infer structure
    template_dict: Optional[dict] = None
    for item in existing_items:
        if isinstance(item, dict) and len(item) == 1:
            template_dict = item
            break
        if isinstance(item, dict):
            template_dict = item
            break

    if template_dict is None:
        # No existing dict entries — use {code: value} for typed items, plain string otherwise
        if item_type and item_type.lower() != "string":
            return [{"code": p} for p in parts]
        return list(parts)

    # For each part, build a dict where the matching field value = part
    # Find which field in the template holds values like our parts
    field_key = next(iter(template_dict))  # first key of the dict
    return [{field_key: p} for p in parts]


# ---------------------------------------------------------------------------
# CSV parsing and row validation
# ---------------------------------------------------------------------------

def _parse_csv_rows(csv_content: str, specs: list[FieldSpec], errors: list[dict],
                    ) -> list[dict[str, Any]]:
    """Parse CSV content into a list of raw row dicts. Collect errors."""
    reader = csv.DictReader(io.StringIO(csv_content))
    fact_specs = {s.column_name: s for s in specs if not s.is_decision}
    decision_specs = {s.column_name: s for s in specs if s.is_decision}
    all_specs_by_col = {s.column_name: s for s in specs}

    rows: list[dict[str, Any]] = []
    seen_case_ids: dict[str, int] = {}

    for row_num, raw_row in enumerate(reader, start=2):  # row 1 = header
        # Skip description row (#desc)
        case_id_raw = (raw_row.get("case_id") or "").strip()
        if case_id_raw == "#desc":
            continue

        # Skip blank rows (all non-case_id/description columns blank)
        data_cols = {k: v for k, v in raw_row.items()
                     if k not in ("case_id", "description")}
        if all(not v or not v.strip() for v in data_cols.values()):
            continue

        case_id = case_id_raw
        description = (raw_row.get("description") or "").strip()

        # Validate case_id
        if not case_id:
            errors.append({
                "row": row_num, "case_id": "",
                "field": "case_id",
                "message": "case_id is required",
                "code": "MISSING_REQUIRED",
            })
        else:
            # Duplicate detection
            if case_id in seen_case_ids:
                print(
                    f"WARN: duplicate case_id '{case_id}' at rows "
                    f"{seen_case_ids[case_id]} and {row_num} — last row wins",
                    file=sys.stderr,
                )
            seen_case_ids[case_id] = row_num

        # Validate description
        if not description:
            errors.append({
                "row": row_num, "case_id": case_id,
                "field": "description",
                "message": "description is required",
                "code": "MISSING_REQUIRED",
            })

        parsed_row: dict[str, Any] = {
            "case_id": case_id,
            "description": description,
            "_row_num": row_num,
            "inputs": {},
            "expected": {},
            "tags": None,
            "notes": None,
        }

        # Fact fields
        for col_name, spec in fact_specs.items():
            raw_val = (raw_row.get(col_name) or "").strip()
            # Required check
            if not raw_val and not spec.optional:
                errors.append({
                    "row": row_num, "case_id": case_id,
                    "field": col_name,
                    "message": f"{col_name} is required but empty",
                    "code": "MISSING_REQUIRED",
                })
                continue
            if not raw_val:
                continue  # optional, omit

            val = coerce_value(raw_val, spec, row_num, case_id, errors)
            if val is None:
                continue  # error already appended
            if val is _OMIT:
                continue
            parsed_row["inputs"][col_name] = val

        # Decision fields
        for col_name, spec in decision_specs.items():
            raw_val = (raw_row.get(col_name) or "").strip()
            dec_name = spec.decision_name

            if spec.civil_type in ("list", "set"):
                if not raw_val:
                    parsed_row["expected"][dec_name] = []
                else:
                    parts = [p.strip() for p in raw_val.split(";") if p.strip()]
                    parsed_row["expected"][dec_name] = ("__list__", parts, spec.item_type)
            else:
                # Required check for non-list decisions
                if not raw_val:
                    errors.append({
                        "row": row_num, "case_id": case_id,
                        "field": col_name,
                        "message": f"{col_name} is required but empty",
                        "code": "MISSING_REQUIRED",
                    })
                    continue
                val = coerce_value(raw_val, spec, row_num, case_id, errors)
                if val is None:
                    continue
                if val is _OMIT:
                    continue
                parsed_row["expected"][dec_name] = val

        # Tags
        tags_raw = (raw_row.get("tags") or "").strip()
        if tags_raw:
            parsed_row["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]

        # Notes
        notes_raw = (raw_row.get("notes") or "").strip()
        if notes_raw:
            parsed_row["notes"] = notes_raw

        rows.append(parsed_row)

    return rows


# ---------------------------------------------------------------------------
# YAML format parsing
# ---------------------------------------------------------------------------

def _parse_yaml_rows(yaml_content: str, errors: list[dict]) -> list[dict[str, Any]]:
    """Parse native YAML tests list format. Collect errors."""
    try:
        doc = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        errors.append({"row": 0, "case_id": "", "field": "", "message": str(e), "code": "YAML_PARSE_ERROR"})
        return []

    test_list = []
    if isinstance(doc, dict):
        test_list = doc.get("tests", []) or []
    elif isinstance(doc, list):
        test_list = doc

    rows: list[dict[str, Any]] = []
    for i, tc in enumerate(test_list, start=1):
        if not isinstance(tc, dict):
            continue
        case_id = str(tc.get("case_id", ""))
        if not case_id:
            errors.append({"row": i, "case_id": "", "field": "case_id",
                           "message": "case_id is required", "code": "MISSING_REQUIRED"})
        description = str(tc.get("description", ""))
        if not description:
            errors.append({"row": i, "case_id": case_id, "field": "description",
                           "message": "description is required", "code": "MISSING_REQUIRED"})
        rows.append({
            "case_id": case_id,
            "description": description,
            "_row_num": i,
            "inputs": tc.get("inputs", {}) or {},
            "expected": tc.get("expected", {}) or {},
            "tags": tc.get("tags"),
            "notes": tc.get("notes"),
            "source": tc.get("source"),
        })
    return rows


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def _build_test_case(parsed: dict[str, Any], existing_expected: dict,
                     specs: list[FieldSpec]) -> dict[str, Any]:
    """Build a YAML test case dict from a parsed row."""
    tc: dict[str, Any] = {
        "case_id": parsed["case_id"],
        "description": parsed["description"],
    }

    if parsed.get("source"):
        tc["source"] = parsed["source"]

    inputs: dict[str, Any] = {}
    for col_name, val in parsed["inputs"].items():
        inputs[col_name] = val
    if inputs:
        tc["inputs"] = inputs

    expected: dict[str, Any] = {}
    for dec_name, val in parsed["expected"].items():
        if isinstance(val, tuple) and val[0] == "__list__":
            # Reconstruct list items from existing YAML entries
            parts = val[1]
            item_type = val[2] if len(val) > 2 else None
            existing_list = existing_expected.get(dec_name, [])
            expected[dec_name] = _reconstruct_list_items(parts, existing_list, item_type)
        else:
            expected[dec_name] = val
    if expected:
        tc["expected"] = expected

    if parsed.get("tags"):
        tc["tags"] = parsed["tags"]

    if parsed.get("notes"):
        tc["notes"] = parsed["notes"]

    return tc


def _load_or_init_yaml(tests_path: Path, civil_doc: dict) -> tuple[dict, list]:
    """Load existing tests YAML or initialise a fresh one."""
    if not tests_path.exists():
        module_name = civil_doc.get("module", tests_path.stem.replace("_tests", ""))
        doc = {
            "test_suite": {
                "spec": f"{module_name}.civil.yaml",
                "description": f"Test cases for {module_name}",
                "version": "1.0",
            },
            "tests": [],
        }
        return doc, []

    with open(tests_path) as f:
        raw_text = f.read()

    doc = yaml.safe_load(raw_text) or {}
    tests_list = doc.get("tests", []) or []
    return doc, tests_list


def _upsert_rows(existing_tests: list, new_rows: list[dict[str, Any]],
                 specs: list[FieldSpec]) -> tuple[list, int, int]:
    """Upsert new_rows into existing_tests. Returns (merged, added, updated)."""
    existing_by_id: dict[str, int] = {}
    for i, tc in enumerate(existing_tests):
        if isinstance(tc, dict) and tc.get("case_id"):
            existing_by_id[str(tc["case_id"])] = i

    # Deduplicate new_rows (last-row-wins)
    deduped: dict[str, dict[str, Any]] = {}
    for row in new_rows:
        deduped[row["case_id"]] = row

    added = 0
    updated = 0

    # Work on a mutable copy
    merged = list(existing_tests)

    for case_id, parsed in deduped.items():
        # Get existing expected for list reconstruction
        existing_expected: dict = {}
        if case_id in existing_by_id:
            existing_tc = existing_tests[existing_by_id[case_id]]
            existing_expected = existing_tc.get("expected", {}) or {}

        tc = _build_test_case(parsed, existing_expected, specs)

        if case_id in existing_by_id:
            merged[existing_by_id[case_id]] = tc
            updated += 1
        else:
            merged.append(tc)
            added += 1

    return merged, added, updated


# ---------------------------------------------------------------------------
# Comment detection
# ---------------------------------------------------------------------------

def _has_yaml_comments(path: Path) -> bool:
    if not path.exists():
        return False
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("#"):
                return True
    return False


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit_errors_text(errors: list[dict]) -> None:
    print(f"ERROR: {len(errors)} validation error(s) found — no changes written.",
          file=sys.stderr)
    for e in errors:
        row = e.get("row", "?")
        cid = e.get("case_id", "")
        field = e.get("field", "")
        msg = e.get("message", "")
        cid_part = f'case_id="{cid}"' if cid else "no case_id"
        print(f'  Row {row} ({cid_part}): {field} {msg}' if field else
              f'  Row {row} ({cid_part}): {msg}', file=sys.stderr)


def _emit_errors_json(errors: list[dict]) -> None:
    out = {"status": "error", "errors": errors}
    print(json.dumps(out))


def _emit_success_text(added: int, updated: int, total: int,
                       target_path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"DRY-RUN  would import {added + updated} case(s): "
              f"{added} added, {updated} updated (no changes written)")
    else:
        print(f"OK  imported {added + updated} case(s): "
              f"{added} added, {updated} updated → {target_path}")
        print(f"Total cases in file: {total}")


def _emit_success_json(added: int, updated: int, total: int, dry_run: bool) -> None:
    out: dict[str, Any] = {
        "status": "ok",
        "added": added,
        "updated": updated,
        "total": total,
    }
    if dry_run:
        out["dry_run"] = True
    print(json.dumps(out))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import test cases from CSV or YAML into *_tests.yaml."
    )
    parser.add_argument("civil_yaml", help="Path to the CIVIL spec YAML file")
    parser.add_argument("input", help="Path to CSV or YAML file, or '-' for stdin")
    parser.add_argument("tests_yaml", help="Path to target *_tests.yaml file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report without writing")
    parser.add_argument("--format", choices=["csv", "yaml"], default="csv",
                        help="Input format (default: csv)")
    parser.add_argument("--no-comment-check", action="store_true",
                        help="Skip YAML comment-loss warning prompt")
    parser.add_argument("--output-format", choices=["text", "json"], default="text",
                        help="Error/result output format (default: text)")
    args = parser.parse_args()

    civil_path = Path(args.civil_yaml)
    tests_path = Path(args.tests_yaml)

    civil_doc = load_civil_yaml(civil_path)
    specs = build_csv_field_specs(civil_doc)

    # Read input
    if args.input == "-":
        content = sys.stdin.read()
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        with open(input_path, encoding="utf-8") as f:
            content = f.read()

    errors: list[dict] = []

    # Parse input
    if args.format == "yaml":
        new_rows = _parse_yaml_rows(content, errors)
    else:
        new_rows = _parse_csv_rows(content, specs, errors)

    # Emit errors and exit if any
    if errors:
        if args.output_format == "json":
            _emit_errors_json(errors)
        else:
            _emit_errors_text(errors)
        sys.exit(1)

    if not new_rows:
        if args.output_format == "json":
            print(json.dumps({"status": "ok", "added": 0, "updated": 0, "total": 0}))
        else:
            print("OK  no rows to import")
        return

    # Load existing YAML
    doc, existing_tests = _load_or_init_yaml(tests_path, civil_doc)

    # Comment-loss warning
    if not args.dry_run and not args.no_comment_check:
        if _has_yaml_comments(tests_path):
            print(
                f"WARN: {tests_path} contains YAML comments that will be lost after rewrite.",
                file=sys.stderr,
            )
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                sys.exit(1)

    # Upsert
    merged_tests, added, updated = _upsert_rows(existing_tests, new_rows, specs)
    total = len(merged_tests)

    if args.dry_run:
        if args.output_format == "json":
            _emit_success_json(added, updated, total, dry_run=True)
        else:
            _emit_success_text(added, updated, total, tests_path, dry_run=True)
        return

    # Write atomically
    doc["tests"] = merged_tests
    yaml_str = yaml.safe_dump(doc, allow_unicode=True, default_flow_style=False,
                              sort_keys=False)

    tests_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp",
            dir=tests_path.parent, delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(yaml_str)
        os.replace(str(tmp_path), str(tests_path))
        tmp_path = None  # replaced successfully
    except Exception as e:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        print(f"ERROR: failed to write {tests_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output_format == "json":
        _emit_success_json(added, updated, total, dry_run=False)
    else:
        _emit_success_text(added, updated, total, tests_path, dry_run=False)


if __name__ == "__main__":
    main()
