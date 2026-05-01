#!/usr/bin/env python3
"""Run catala tests with --trace and export inputs + results to CSV files.

Usage (via xlator CLI):
    xlator export-test-results <domain>
"""

import argparse
import os
import subprocess
import re
import csv
import sys
from pathlib import Path

TEST_DIR = Path("tests")
STDLIB = "_build/libcatala"
CATALA_BIN = "catala"


# --- Test discovery ---

def find_tests(filepath: Path) -> list[tuple[str, str, str]]:
    """Return list of (scope_name, result_scope, description) from a catala test file."""
    content = filepath.read_text()
    tests = []

    # Extract (line_number, description) from ## Test: headings
    heading_pattern = re.compile(r'^## Test:\s*(.+)$', re.MULTILINE)
    headings = [(m.start(), m.group(1).strip()) for m in heading_pattern.finditer(content)]

    # Extract (char_offset, scope_name, result_scope) from #[test] blocks
    test_pattern = re.compile(
        r'#\[test\]\s*\ndeclaration scope (\w+):\s*\n\s*result scope ([\w.]+)',
        re.MULTILINE,
    )
    for m in test_pattern.finditer(content):
        scope_name = m.group(1)
        result_scope = m.group(2)
        # Find the closest preceding heading
        pos = m.start()
        description = ''
        for hpos, htxt in reversed(headings):
            if hpos < pos:
                description = htxt
                break
        tests.append((scope_name, result_scope, description))

    return tests


def find_assertions(filepath: Path) -> dict[str, dict[str, str]]:
    """Return {scope_name: {field: expected_value}} parsed from assertion statements."""
    content = filepath.read_text()
    result: dict[str, dict[str, str]] = {}

    # Match each #[test] block up to its closing ```
    block_pattern = re.compile(
        r'#\[test\]\s*\ndeclaration scope (\w+):.*?(?=^```)',
        re.MULTILINE | re.DOTALL,
    )
    assertion_pattern = re.compile(
        r'^\s*assertion\s*\(\s*result\.([\w.]+)\s*=\s*(.*?)\s*\)\s*$',
        re.MULTILINE,
    )

    for block_m in block_pattern.finditer(content):
        scope_name = block_m.group(1)
        block_text = block_m.group(0)
        assertions: dict[str, str] = {}
        for a_m in assertion_pattern.finditer(block_text):
            field = a_m.group(1)
            raw_value = a_m.group(2).strip()
            assertions[field] = parse_catala_value(raw_value)
        result[scope_name] = assertions

    return result


# --- Test execution ---

def run_test(test_file: Path, scope_name: str) -> tuple[str, int]:
    cmd = [
        CATALA_BIN, "interpret",
        "-I", "tests", str(test_file),
        f"--stdlib={STDLIB}", "-I", ".",
        f"--scope={scope_name}", "--trace",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Trace (LOG lines) goes to stdout; errors go to stderr
    return result.stdout, result.returncode


# --- Trace parsing ---

def determine_scope_type(result_scope: str) -> str:
    """Extract the scope name from 'Module.ScopeName' or bare 'ScopeName'."""
    return result_scope.split('.')[-1]


def extract_input_output(trace: str, scope_type: str) -> tuple[str | None, str | None]:
    """Find the top-level scope input and output lines from trace output.

    Top-level scope lines have exactly 2 spaces of indent after [LOG]:
        [LOG]   ≔  ScopeType.direct.
    Subscope lines have 4+ spaces of indent.
    """
    lines = trace.split('\n')
    prefix_re = re.compile(rf'^\[LOG\]\s+≔\s+{re.escape(scope_type)}\.direct\.$')
    input_str = None
    output_str = None

    for i, line in enumerate(lines):
        if prefix_re.match(line) and i + 1 < len(lines):
            next_line = lines[i + 1]
            stripped = next_line.strip()
            if stripped.startswith('input:') and input_str is None:
                input_str = stripped[len('input:'):].strip()
            elif stripped.startswith('output:'):
                output_str = stripped[len('output:'):].strip()

    return input_str, output_str


def get_brace_content(s: str, start_pos: int) -> str:
    """Return the content between the opening brace at start_pos and its matching close."""
    depth = 0
    content_start = None
    for i in range(start_pos, len(s)):
        if s[i] == '{':
            if depth == 0:
                content_start = i + 1
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return s[content_start:i]
    if content_start is not None:
        print(f'Warning: unmatched brace in trace output; struct may be truncated: {s[start_pos:start_pos+60]!r}', file=sys.stderr)
    return s[content_start:] if content_start is not None else ''


def split_depth_zero(s: str, sep: str) -> list[str]:
    """Split s on sep only at brace/bracket depth 0."""
    parts: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    current: list[str] = []
    sep_len = len(sep)
    i = 0
    while i < len(s):
        if s[i] == '{':
            brace_depth += 1
        elif s[i] == '}':
            brace_depth -= 1
        elif s[i] == '[':
            bracket_depth += 1
        elif s[i] == ']':
            bracket_depth -= 1
        if brace_depth == 0 and bracket_depth == 0 and s[i:i + sep_len] == sep:
            parts.append(''.join(current))
            current = []
            i += sep_len
            continue
        current.append(s[i])
        i += 1
    if current:
        parts.append(''.join(current))
    return parts


def parse_catala_list(s: str) -> str:
    """Parse a catala list [...] into a semicolon-separated readable string."""
    inner = s[1:]
    if inner.endswith(']'):
        inner = inner[:-1]
    inner = inner.strip()
    if not inner:
        return ''
    items = split_depth_zero(inner, '; ')
    parsed: list[str] = []
    for item in items:
        item = item.strip()
        if '{' in item:
            d = parse_catala_struct(item)
            parsed.append('{' + ', '.join(f'{k}={v}' for k, v in d.items()) + '}')
        else:
            parsed.append(parse_catala_scalar(item))
    return '; '.join(parsed)


def parse_catala_scalar(s: str) -> str:
    """Convert a catala scalar (non-list, non-struct) value to a plain string."""
    s = s.strip()
    if s.startswith('$'):
        return s[1:].replace(',', '')
    if s in ('true', 'false'):
        return s
    m = re.match(r'[\w.]+\.([\w]+)\s*\(\(\)\)', s)
    if m:
        return m.group(1)
    if re.match(r'^[\d,]+$', s):
        return s.replace(',', '')
    return s


def parse_catala_value(s: str) -> str:
    """Dispatch to list or scalar parser."""
    s = s.strip()
    if s.startswith('['):
        return parse_catala_list(s)
    return parse_catala_scalar(s)


def parse_catala_struct(s: str, prefix: str = '') -> dict[str, str]:
    """Recursively parse a catala struct into a flat dict with dotted keys."""
    s = s.strip()
    result: dict[str, str] = {}

    brace_pos = s.find('{')
    if brace_pos == -1:
        return result

    inner = get_brace_content(s, brace_pos)
    parts = split_depth_zero(inner, ' -- ')

    for part in parts:
        part = part.strip()
        if not part:
            continue
        colon_pos = part.find(': ')
        if colon_pos == -1:
            continue
        key = part[:colon_pos].strip()
        # Catala appends _in to input field names in trace output; strip it.
        # Guard len > 3 so a key that is literally '_in' is left unchanged.
        if key.endswith('_in') and len(key) > 3:
            key = key[:-3]
        full_key = key if not prefix else f'{prefix}.{key}'
        value_str = part[colon_pos + 2:].strip()

        if '{' in value_str and not value_str.strip().startswith('['):
            nested = parse_catala_struct(value_str, full_key)
            result.update(nested)
        else:
            result[full_key] = parse_catala_value(value_str)

    return result


# --- CSV output ---

def process_file(test_file: Path) -> None:
    tests = find_tests(test_file)
    if not tests:
        return
    assertions_by_scope = find_assertions(test_file)

    rows: list[dict[str, str]] = []
    all_input_keys: list[str] = []
    all_output_keys: list[str] = []

    for scope_name, result_scope, description in tests:
        scope_type = determine_scope_type(result_scope)
        trace, returncode = run_test(test_file, scope_name)
        status = 'pass' if returncode == 0 else 'fail'

        input_str, output_str = extract_input_output(trace, scope_type)

        input_data = parse_catala_struct(input_str) if input_str else {}
        output_data = parse_catala_struct(output_str) if output_str else {}

        # Collect ordered unique keys (input and output separately for column ordering)
        for k in input_data:
            if k not in all_input_keys:
                all_input_keys.append(k)
        for k in output_data:
            if k not in all_output_keys:
                all_output_keys.append(k)

        row: dict[str, str] = {
            'test_name': scope_name,
            'description': description,
            'status': status,
        }
        row.update(input_data)
        # Prefix output keys so they're visually distinct from inputs
        row.update({f'out.{k}': v for k, v in output_data.items()})
        rows.append(row)

        print(f'  {status}  {scope_name}')

    if not rows:
        return

    out_dir = Path('test-results')
    out_dir.mkdir(exist_ok=True)

    # Input CSV: test_name + description + all input fields
    input_fieldnames = ['test_name', 'description'] + all_input_keys
    input_csv_path = out_dir / (test_file.stem + '_inputs.csv')
    with open(input_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=input_fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, '') for k in input_fieldnames})

    # Output CSV: test_name + status + all output fields (out. prefix stripped)
    output_fieldnames = ['test_name', 'status'] + all_output_keys
    output_csv_path = out_dir / (test_file.stem + '_outputs.csv')
    with open(output_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Strip 'out.' prefix from keys when writing
            out_row = {k: row.get(f'out.{k}', '') for k in all_output_keys}
            out_row['test_name'] = row['test_name']
            out_row['status'] = row['status']
            writer.writerow({k: out_row.get(k, '') for k in output_fieldnames})

    # Expected CSV: test_name + assertion fields in test order
    all_expected_keys: list[str] = []
    for scope_name, _, _ in tests:
        for k in assertions_by_scope.get(scope_name, {}):
            if k not in all_expected_keys:
                all_expected_keys.append(k)

    expected_fieldnames = ['test_name'] + all_expected_keys
    expected_csv_path = out_dir / (test_file.stem + '_expected.csv')
    with open(expected_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=expected_fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            scope_name = row['test_name']
            exp_row: dict[str, str] = {'test_name': scope_name}
            exp_row.update(assertions_by_scope.get(scope_name, {}))
            writer.writerow({k: exp_row.get(k, '') for k in expected_fieldnames})

    # Actual CSV: same columns as expected, values from trace output
    actual_csv_path = out_dir / (test_file.stem + '_actual.csv')
    with open(actual_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=expected_fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            act_row: dict[str, str] = {'test_name': row['test_name']}
            act_row.update({k: row.get(f'out.{k}', '') for k in all_expected_keys})
            writer.writerow({k: act_row.get(k, '') for k in expected_fieldnames})

    print(f'  → {input_csv_path} ({len(rows)} rows, {len(input_fieldnames)} columns)')
    print(f'  → {output_csv_path} ({len(rows)} rows, {len(output_fieldnames)} columns)')
    print(f'  → {expected_csv_path} ({len(rows)} rows, {len(expected_fieldnames)} columns)')
    print(f'  → {actual_csv_path} ({len(rows)} rows, {len(expected_fieldnames)} columns)')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Run catala tests with --trace and export inputs + results to CSV files.'
    )
    parser.add_argument('domain', help='Domain name (e.g. ak_doh)')
    args = parser.parse_args()

    domains_fullpath = os.environ.get('DOMAINS_FULLPATH')
    if not domains_fullpath:
        print('Error: DOMAINS_FULLPATH environment variable not set', file=sys.stderr)
        sys.exit(1)

    output_dir = Path(domains_fullpath) / args.domain / 'output'
    if not output_dir.is_dir():
        print(f'Error: output directory not found: {output_dir}', file=sys.stderr)
        sys.exit(1)

    os.chdir(output_dir)
    print(f'Processing test files in {output_dir}...')

    test_files = sorted(TEST_DIR.glob('*.catala_en'))
    if not test_files:
        print(f'No test files found in {TEST_DIR}', file=sys.stderr)
        sys.exit(1)

    for test_file in test_files:
        print(f'\n{test_file.name}')
        process_file(test_file)


if __name__ == '__main__':
    main()
