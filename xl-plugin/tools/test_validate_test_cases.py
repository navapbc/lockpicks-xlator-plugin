"""Tests for validate_test_cases — required fields + program-wide unique
short_description (plan 2026-06-10-001, U3)."""

from __future__ import annotations

from pathlib import Path

import validate_test_cases as v


def _write(tests_dir: Path, name: str, body: str) -> None:
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / name).write_text(body, encoding="utf-8")


def _case(case_id: str, short_desc: str | None = "ok", desc: str | None = "d") -> str:
    lines = [f"  - case_id: {case_id}"]
    if short_desc is not None:
        lines.append(f"    short_description: {short_desc!r}")
    if desc is not None:
        lines.append(f"    description: {desc!r}")
    return "\n".join(lines)


def _suite(*cases: str) -> str:
    return "tests:\n" + "\n".join(cases) + "\n"


def test_clean_program_set_is_valid(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("a", "Approve — ok"), _case("b", "Deny — gross")))
    _write(tests, "elig_boundary_expanded_tests.yaml", _suite(_case("c", "Boundary — at $1,830")))
    assert v.validate(tmp_path, "elig") == []


def test_missing_short_description_errors(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("a", short_desc=None)))
    errors = v.validate(tmp_path, "elig")
    assert any("short_description" in e for e in errors)


def test_missing_case_id_and_description_error(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("", "L", desc=None)))
    errors = v.validate(tmp_path, "elig")
    assert any("case_id" in e for e in errors)
    assert any("description" in e for e in errors)


def test_cross_file_duplicate_short_description_errors(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("a", "Deny — gross income test failed")))
    _write(tests, "elig_edge_case_expanded_tests.yaml",
           _suite(_case("z", "Deny — gross income test failed")))
    errors = v.validate(tmp_path, "elig")
    assert any("duplicate short_description" in e for e in errors)


def test_derived_from_extracted_file_is_included(tmp_path):
    """The derived-from-extracted file ends in _tests.yaml and must be in the
    uniqueness set (regression guard for the *_tests.yaml glob anchor)."""
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("a", "Shared label")))
    _write(tests, "elig_derived_from_extracted_tests.yaml", _suite(_case("d", "Shared label")))
    errors = v.validate(tmp_path, "elig")
    assert any("duplicate short_description" in e for e in errors)


def test_baseline_only_program_validates(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", _suite(_case("a", "Approve — ok")))
    assert v.validate(tmp_path, "elig") == []


def test_no_test_files_is_not_an_error(tmp_path):
    (tmp_path / "specs" / "tests").mkdir(parents=True)
    assert v.validate(tmp_path, "elig") == []


def test_malformed_yaml_reported_not_crash(tmp_path):
    tests = tmp_path / "specs" / "tests"
    _write(tests, "elig_tests.yaml", "tests:\n  - case_id: a\n   bad-indent: x\n")
    errors = v.validate(tmp_path, "elig")
    assert any("parse error" in e.lower() for e in errors)


def test_sibling_prefix_program_not_swept_by_baseline(tmp_path):
    """A sibling program whose name extends this one shares the label, but the
    baseline-only check for the shorter program should still validate its own
    baseline cleanly when labels within its own family are unique."""
    tests = tmp_path / "specs" / "tests"
    _write(tests, "income_tests.yaml", _suite(_case("a", "Approve — income")))
    _write(tests, "income_extra_tests.yaml", _suite(_case("a", "Approve — income")))
    # Validating "income_extra" must not see "income_tests.yaml" cases.
    assert v.validate(tmp_path, "income_extra") == []
