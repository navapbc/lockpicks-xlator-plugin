#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator detect-stale-cases: re-evaluate every test case under
specs/tests/<program>*.yaml against the current CIVIL spec, and emit the
list of cases whose `expected:` would now differ.

Usage:
    xlator detect-stale-cases <domain> <program>

Output:
    JSON header on stdout:
      {
        "scanned_count": <int>,
        "stale_count":   <int>,
        "error_count":   <int>,
        "stale_cases":   [
          {
            "case_id":             <str>,
            "file":                <relative path>,
            "current_expected":    {...},
            "recomputed_expected": {...},
            "diff": {<field>: {"current": <v>, "recomputed": <v>}}
          },
          ...
        ],
        "errors": [
          {"case_id": <str>, "file": <path>, "error": <message>},
          ...
        ]
      }

    Then a sentinel line, then a human-readable summary body.

Exit codes:
    0  always (stale cases are informational, not errors)
    2  pre-flight failure (missing domain or CIVIL file)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import civil_eval  # noqa: E402


HEADER_SENTINEL = "--- DETECT-STALE-CASES-HEADER-END ---"


def _preflight(domain_dir: Path, civil_path: Path) -> None:
    if not domain_dir.is_dir():
        print(f"Error: domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)
    if not civil_path.is_file():
        print(f"Error: CIVIL file not found: {civil_path}", file=sys.stderr)
        sys.exit(2)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_test_files(tests_dir: Path, program: str) -> list[Path]:
    """Return every `<program>*.yaml` file under specs/tests/ (alphabetical)."""
    if not tests_dir.is_dir():
        return []
    return sorted(p for p in tests_dir.glob(f"{program}*.yaml") if p.is_file())


def cmd_detect(domain_dir: Path, program: str) -> dict:
    """Walk every test case for `program`; return summary dict with stale list."""
    civil_path = domain_dir / "specs" / f"{program}.civil.yaml"
    _preflight(domain_dir, civil_path)

    civil_doc = _load_yaml(civil_path)
    if not isinstance(civil_doc, dict):
        print(f"Error: CIVIL file is not a YAML mapping: {civil_path}", file=sys.stderr)
        sys.exit(2)

    tests_dir = domain_dir / "specs" / "tests"
    test_files = _find_test_files(tests_dir, program)

    summary: dict[str, Any] = {
        "scanned_count": 0,
        "stale_count": 0,
        "error_count": 0,
        "stale_cases": [],
        "errors": [],
    }

    for test_file in test_files:
        rel = str(test_file.relative_to(domain_dir))
        try:
            test_doc = _load_yaml(test_file)
        except yaml.YAMLError as exc:
            summary["errors"].append({
                "case_id": None,
                "file": rel,
                "error": f"YAML parse error: {exc}",
            })
            summary["error_count"] += 1
            continue
        if not isinstance(test_doc, dict):
            continue
        cases = test_doc.get("tests") or []
        if not isinstance(cases, list):
            continue

        for case in cases:
            if not isinstance(case, dict):
                continue
            summary["scanned_count"] += 1
            case_id = case.get("case_id", "<unknown>")
            try:
                diff = civil_eval.detect_stale(civil_doc, case)
            except civil_eval.EvaluationError as exc:
                summary["errors"].append({
                    "case_id": case_id,
                    "file": rel,
                    "error": str(exc),
                })
                summary["error_count"] += 1
                continue
            if diff is None:
                continue
            summary["stale_count"] += 1
            summary["stale_cases"].append({
                "case_id": case_id,
                "file": rel,
                "current_expected": diff.current_expected,
                "recomputed_expected": diff.recomputed_expected,
                "diff": diff.diff,
            })

    return summary


def _print_body(summary: dict) -> None:
    """Human-readable body printed after the sentinel."""
    print(
        f"Scanned {summary['scanned_count']} test case(s). "
        f"{summary['stale_count']} stale, {summary['error_count']} errored."
    )
    if summary["stale_cases"]:
        print()
        print("Stale:")
        for entry in summary["stale_cases"]:
            fields = ", ".join(
                f"{k}: {_short(v['current'])}→{_short(v['recomputed'])}"
                for k, v in entry["diff"].items()
            )
            print(f"  {entry['case_id']} ({entry['file']}) — {fields}")
    if summary["errors"]:
        print()
        print("Errors:")
        for entry in summary["errors"]:
            cid = entry["case_id"] or "?"
            print(f"  {cid} ({entry['file']}): {entry['error']}")


def _short(value: Any, max_len: int = 40) -> str:
    s = json.dumps(value, default=str)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-evaluate test cases against current CIVIL; flag stale ones.",
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("program", help="Program name (matches specs/<program>.civil.yaml)")
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(2)

    domain_dir = Path(domains_root) / args.domain
    summary = cmd_detect(domain_dir, args.program)

    print(json.dumps(summary, indent=2, default=str))
    print(HEADER_SENTINEL)
    _print_body(summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
