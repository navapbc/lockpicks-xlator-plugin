"""Tests for export_test_cases / export_test_template — short_description CSV
column (plan 2026-06-10-001, U1).

The exporters had no test coverage; these guard the column position and the
case round-trip into the CSV.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent

_MANIFEST = """\
version: "2.0"
inputs:
  Household:
    size:
      type: integer
outputs:
  eligible:
    type: boolean
"""

_TESTS = """\
test_suite:
  spec: "elig.catala_en"
  description: "x"
  version: "1.0"
tests:
  - case_id: "a1"
    short_description: "Approve — income eligible"
    description: "Full prose description here"
    inputs:
      size: 3
    expected:
      eligible: true
    tags: ["allow"]
"""


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_export_test_cases_emits_short_description_as_second_column(tmp_path):
    manifest = tmp_path / "naming-manifest.yaml"
    tests = tmp_path / "elig_tests.yaml"
    _write(manifest, _MANIFEST)
    _write(tests, _TESTS)

    result = subprocess.run(
        [sys.executable, str(THIS_DIR / "export_test_cases.py"),
         str(manifest), str(tests), "--output-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_path / "elig_tests.csv"
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    header = rows[0]
    assert header[0] == "case_id"
    assert header[1] == "short_description"
    assert header[2] == "description"

    # row 0 = header, row 1 = #desc, row 2 = first case
    case_row = rows[2]
    assert case_row[0] == "a1"
    assert case_row[1] == "Approve — income eligible"


def test_export_template_emits_short_description_column(tmp_path):
    manifest = tmp_path / "naming-manifest.yaml"
    _write(manifest, _MANIFEST)

    result = subprocess.run(
        [sys.executable, str(THIS_DIR / "export_test_template.py"),
         str(manifest), "--module", "elig", "--output-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_path / "elig_test_template.csv"
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    header = rows[0]
    assert header[1] == "short_description"
    # Each example row carries a distinct short_description in column 1.
    allow_row = rows[2]
    deny_row = rows[3]
    assert allow_row[1] and deny_row[1]
    assert allow_row[1] != deny_row[1]
