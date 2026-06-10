#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator validate-tests: deterministic schema check over a program's YAML test
cases.

The authoring skills (/create-tests, /expand-tests) write test YAML directly
and never pass through import_tests.py, so this is the hard-validation home
for that path. It enforces, across the program's whole test-file family:

  * every case has a non-empty `case_id`, `short_description`, `description`
  * `short_description` is UNIQUE program-wide (baseline + every expanded file)

`case_id` remains the canonical identity key; `short_description` is the
human-facing label, additionally constrained unique so it can stand in for
`case_id` in CSV exports, stale-case reports, and Catala fixture comments.

File family: `specs/tests/<program>*_tests.yaml` — matches the baseline
`<program>_tests.yaml` plus every file /expand-tests writes
(`*_derived_from_extracted_tests.yaml`, `*_boundary_expanded_tests.yaml`,
`*_null_input_expanded_tests.yaml`, `*_edge_case_expanded_tests.yaml`). The
`*_tests.yaml` anchor matches the glob /catala-emit-tests already uses. The
null-input file is intentionally included (uniqueness is program-wide) even
though /catala-emit-tests skips it for fixture emission.

Usage:
    xlator validate-tests <domain> <program>

Output:
    A readable report on stdout. Errors (missing field, duplicate label, YAML
    parse failure) are listed with file + case_id.

Exit codes:
    0  all cases valid
    1  one or more validation errors
    2  pre-flight failure (DOMAINS_FULLPATH unset, missing domain)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_FIELDS = ("case_id", "short_description", "description")


def _find_test_files(tests_dir: Path, program: str) -> list[Path]:
    if not tests_dir.is_dir():
        return []
    return sorted(p for p in tests_dir.glob(f"{program}*_tests.yaml") if p.is_file())


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(domain_dir: Path, program: str) -> list[str]:
    """Return a list of human-readable error strings (empty = valid)."""
    errors: list[str] = []
    tests_dir = domain_dir / "specs" / "tests"
    test_files = _find_test_files(tests_dir, program)

    if not test_files:
        # No test files is not itself an error — nothing to validate.
        return errors

    # value -> first "(file::case_id)" location that claimed it
    seen_short_desc: dict[str, str] = {}

    for test_file in test_files:
        rel = str(test_file.relative_to(domain_dir))
        try:
            doc = _load_yaml(test_file)
        except yaml.YAMLError as exc:
            errors.append(f"{rel}: YAML parse error: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        cases = doc.get("tests") or []
        if not isinstance(cases, list):
            continue

        for case in cases:
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("case_id") or "").strip() or "<no case_id>"
            for field in _REQUIRED_FIELDS:
                value = case.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"{rel} (case_id={case_id}): missing required field '{field}'"
                    )

            short_desc = case.get("short_description")
            if isinstance(short_desc, str) and short_desc.strip():
                key = short_desc.strip()
                here = f"{rel}::{case_id}"
                if key in seen_short_desc:
                    errors.append(
                        f"duplicate short_description '{key}' "
                        f"— first at {seen_short_desc[key]}, again at {here}"
                    )
                else:
                    seen_short_desc[key] = here

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a program's YAML test cases (required fields + "
                    "program-wide short_description uniqueness).",
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("program", help="Program name (matches specs/<program>.catala_en)")
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(2)

    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        print(f"Error: domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)

    errors = validate(domain_dir, args.program)

    if errors:
        print(f"FAIL  {len(errors)} validation error(s) in {args.program} test cases:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print(f"OK  all {args.program} test cases valid "
          f"(required fields present, short_description unique).")
    sys.exit(0)


if __name__ == "__main__":
    main()
