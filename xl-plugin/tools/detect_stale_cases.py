#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator detect-stale-cases: re-evaluate every test case under
specs/tests/<program>*.yaml against the current Catala source, and emit
the list of cases whose `expected:` would now differ.

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
            "short_description":    <str|null>,
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
    2  pre-flight failure (missing domain or Catala source)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import catala_eval  # noqa: E402


HEADER_SENTINEL = "--- DETECT-STALE-CASES-HEADER-END ---"

FLOAT_TOLERANCE = 1e-9


@dataclasses.dataclass
class StaleCaseDiff:
    current_expected: dict
    recomputed_expected: dict
    diff: dict


def _preflight(domain_dir: Path, catala_path: Path) -> None:
    if not domain_dir.is_dir():
        print(f"Error: domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)
    if not catala_path.is_file():
        print(f"Error: Catala source not found: {catala_path}", file=sys.stderr)
        sys.exit(2)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_test_files(tests_dir: Path, program: str) -> list[Path]:
    if not tests_dir.is_dir():
        return []
    return sorted(p for p in tests_dir.glob(f"{program}*.yaml") if p.is_file())


def _derive_scope_name(program: str) -> str:
    """PascalCase + 'Decision'; matches xlator.cmd_catala_test_transpile."""
    pascal = "".join(w.capitalize() for w in program.split("_") if w)
    return pascal + "Decision"


def _detect_stale(catala_path: Path, scope: str, case: dict) -> StaleCaseDiff | None:
    """Re-evaluate one test case against the Catala source. Returns None
    when current expected: still matches; otherwise a StaleCaseDiff."""
    inputs = case.get("inputs") or {}
    current_expected = dict(case.get("expected") or {})
    result = catala_eval.run(catala_path, scope, inputs)
    recomputed = _build_recomputed_expected(current_expected, result)
    diff = _diff_expected(current_expected, recomputed)
    if not diff:
        return None
    return StaleCaseDiff(
        current_expected=current_expected,
        recomputed_expected=recomputed,
        diff=diff,
    )


def _build_recomputed_expected(current: dict, result: catala_eval.EvaluationResult) -> dict:
    recomputed: dict[str, Any] = {}
    for key in current:
        if key in result.outputs:
            recomputed[key] = result.outputs[key]
        elif key == "reasons":
            recomputed[key] = [{"code": r.get("code")} for r in result.reasons]
        else:
            recomputed[key] = result.outputs.get(key)
    for key, value in result.outputs.items():
        if key not in recomputed:
            recomputed[key] = value
    return recomputed


def _diff_expected(current: dict, recomputed: dict) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    all_keys = set(current) | set(recomputed)
    for key in all_keys:
        c = current.get(key)
        r = recomputed.get(key)
        if _values_equal(c, r):
            continue
        diff[key] = {"current": c, "recomputed": r}
    return diff


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if (
        isinstance(a, (int, float)) and not isinstance(a, bool)
        and isinstance(b, (int, float)) and not isinstance(b, bool)
    ):
        return abs(a - b) <= FLOAT_TOLERANCE
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_reason_equal(x, y) for x, y in zip(a, b))
    return a == b


def _reason_equal(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        for key, val in a.items():
            if key not in b or b[key] != val:
                return False
        return True
    return a == b


def cmd_detect(domain_dir: Path, program: str) -> dict:
    catala_path = domain_dir / "specs" / f"{program}.catala_en"
    _preflight(domain_dir, catala_path)

    scope = _derive_scope_name(program)
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
            short_description = case.get("short_description")
            try:
                diff = _detect_stale(catala_path, scope, case)
            except catala_eval.EvaluationError as exc:
                summary["errors"].append({
                    "case_id": case_id,
                    "short_description": short_description,
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
                "short_description": short_description,
                "file": rel,
                "current_expected": diff.current_expected,
                "recomputed_expected": diff.recomputed_expected,
                "diff": diff.diff,
            })

    return summary


def _print_body(summary: dict) -> None:
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
            label = f" [{entry['short_description']}]" if entry.get("short_description") else ""
            print(f"  {entry['case_id']}{label} ({entry['file']}) — {fields}")
    if summary["errors"]:
        print()
        print("Errors:")
        for entry in summary["errors"]:
            cid = entry["case_id"] or "?"
            label = f" [{entry['short_description']}]" if entry.get("short_description") else ""
            print(f"  {cid}{label} ({entry['file']}): {entry['error']}")


def _short(value: Any, max_len: int = 40) -> str:
    s = json.dumps(value, default=str)
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-evaluate test cases against current Catala source; flag stale ones.",
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("program", help="Program name (matches specs/<program>.catala_en)")
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
