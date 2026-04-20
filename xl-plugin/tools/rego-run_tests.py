#!/usr/bin/env python3
"""
CIVIL Test Runner

Reads a CIVIL _tests.yaml file and executes each test case against the
OPA REST server, reporting pass/fail per case.

Requires OPA REST server to be running:
    opa run --server --addr :8181 <path/to/policy.rego>

Usage (via xlator CLI):
    xlator rego-test <domain> <module>

Example:
    xlator rego-test snap eligibility

Options:
    --opa-url   OPA REST server base URL (default: http://localhost:8181)
    --opa-path  OPA REST decision path (default: /v1/data/snap/eligibility/decision)

Exit codes:
    0 — all tests passed
    1 — one or more tests failed, or connection error
"""

import sys
import json
import yaml
import urllib.request
import urllib.error
import urllib.parse


DEFAULT_OPA_URL = "http://localhost:8181"
DEFAULT_OPA_PATH = "/v1/data/snap/eligibility/decision"


def load_tests(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"ERROR: Tests file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"ERROR: YAML parse error: {e}", file=sys.stderr)
        sys.exit(1)


def query_opa(opa_url, opa_path, inputs):
    url = opa_url.rstrip("/") + opa_path
    payload = json.dumps({"input": inputs}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return body.get("result")
    except urllib.error.URLError as e:
        print(f"\nERROR: Could not connect to OPA at {opa_url}: {e}", file=sys.stderr)
        print("Is the OPA server running? Try:", file=sys.stderr)
        print("    opa run --server --addr :8181 <path/to/policy.rego>", file=sys.stderr)
        sys.exit(1)


NUMERIC_TOLERANCE = 0.005  # half-cent tolerance for float/int comparisons


def check_result(result, expected, case_id):
    """Returns list of failure messages (empty = pass)."""
    failures = []

    if result is None:
        failures.append("OPA returned undefined result (missing required input fields?)")
        return failures

    for key, want in expected.items():
        got = result.get(key)
        if isinstance(want, list):
            # List field (e.g. reasons) — verify expected codes are present
            got_codes = {r.get("code") for r in (got or [])}
            for expected_item in want:
                code = expected_item.get("code")
                if code and code not in got_codes:
                    failures.append(f"{key}: expected code {code!r}, got codes {sorted(got_codes)}")
        elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if abs(got - want) > NUMERIC_TOLERANCE:
                failures.append(f"{key}: expected {want}, got {got}")
        else:
            if got != want:
                failures.append(f"{key}: expected {want!r}, got {got!r}")

    return failures


def run_tests(tests_path, opa_url, opa_path):
    suite = load_tests(tests_path)
    test_cases = suite.get("tests", [])
    suite_desc = suite.get("test_suite", {}).get("description", tests_path)

    print(f"Running: {suite_desc}")
    print(f"OPA:     {opa_url}{opa_path}")
    print(f"Cases:   {len(test_cases)}")
    print()

    passed = 0
    failed = 0
    failures = []

    for case in test_cases:
        case_id = case.get("case_id", "?")
        description = case.get("description", "")
        inputs = case.get("inputs", {})
        expected = case.get("expected", {})

        result = query_opa(opa_url, opa_path, inputs)
        case_failures = check_result(result, expected, case_id)

        if case_failures:
            failed += 1
            failures.append((case_id, description, case_failures, result))
            print(f"  FAIL  {case_id}: {description}")
            for msg in case_failures:
                print(f"        ↳ {msg}")
        else:
            passed += 1
            print(f"  PASS  {case_id}: {description}")
            if result:
                decision_fields = {k: v for k, v in result.items() if k != "computed"}
                summary = ", ".join(
                    f"{k}=${v:,.2f}" if isinstance(v, (int, float)) else f"{k}={v}"
                    for k, v in decision_fields.items()
                    if not isinstance(v, list)
                )
                if summary:
                    print(f"        {summary}")

    print()
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)} total")

    if failures:
        print()
        print("FAILED CASES — full OPA output:")
        for case_id, desc, msgs, result in failures:
            print(f"\n  {case_id}: {desc}")
            print(f"  OPA result: {json.dumps(result, indent=4)}")

    return failed == 0


def main():
    args = sys.argv[1:]
    tests_path = None
    opa_url = DEFAULT_OPA_URL
    opa_path = DEFAULT_OPA_PATH

    i = 0
    while i < len(args):
        if args[i] == "--opa-url" and i + 1 < len(args):
            opa_url = args[i + 1]
            i += 2
        elif args[i] == "--opa-path" and i + 1 < len(args):
            opa_path = args[i + 1]
            i += 2
        else:
            tests_path = args[i]
            i += 1

    if not tests_path:
        print(f"Usage: {sys.argv[0]} <tests_yaml> [--opa-url URL] [--opa-path PATH]", file=sys.stderr)
        sys.exit(1)

    success = run_tests(tests_path, opa_url, opa_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
