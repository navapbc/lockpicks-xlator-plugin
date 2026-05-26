# /// script
# requires-python = ">=3.14"
# ///
"""Unit tests for catala_pipeline_checks.stale_catala_files (ticket 17).

Verifies that stale_catala_files() detects stale .catala_en files before
the OCaml build phase, preventing misleading downstream Catala errors.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from catala_pipeline_checks import StaleReport, stale_catala_files

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLDER = 1_000_000.0   # epoch seconds — obviously older
_NEWER = 2_000_000.0   # epoch seconds — obviously newer


def _touch(path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# Tests — no staleness
# ---------------------------------------------------------------------------


def test_no_stale_when_output_dir_empty(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _OLDER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert result == []


def test_no_stale_when_catala_newer_than_both(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _OLDER)

    catala_file = output_dir / "eligibility.catala_en"
    civil_file = specs_dir / "eligibility.civil.yaml"
    _touch(civil_file, _OLDER)
    _touch(catala_file, _NEWER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert result == []


def test_no_stale_when_no_civil_yaml_and_catala_newer_than_transpiler(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _OLDER)

    catala_file = output_dir / "orphan.catala_en"
    _touch(catala_file, _NEWER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert result == []


# ---------------------------------------------------------------------------
# Tests — civil-newer staleness
# ---------------------------------------------------------------------------


def test_civil_newer_detected(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _OLDER)

    catala_file = output_dir / "eligibility.catala_en"
    civil_file = specs_dir / "eligibility.civil.yaml"
    _touch(catala_file, _OLDER)
    _touch(civil_file, _NEWER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 1
    assert result[0].program == "eligibility"
    assert result[0].reason == "civil-newer"
    assert result[0].catala_file == catala_file


def test_civil_newer_takes_precedence_over_transpiler_newer(tmp_path):
    """When both CIVIL and transpiler are newer, reason is civil-newer (checked first)."""
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    catala_file = output_dir / "eligibility.catala_en"
    civil_file = specs_dir / "eligibility.civil.yaml"
    _touch(catala_file, _OLDER)
    _touch(civil_file, _NEWER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 1
    assert result[0].reason == "civil-newer"


# ---------------------------------------------------------------------------
# Tests — transpiler-newer staleness
# ---------------------------------------------------------------------------


def test_transpiler_newer_detected(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    catala_file = output_dir / "eligibility.catala_en"
    civil_file = specs_dir / "eligibility.civil.yaml"
    _touch(civil_file, _OLDER)
    _touch(catala_file, _OLDER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 1
    assert result[0].program == "eligibility"
    assert result[0].reason == "transpiler-newer"


def test_transpiler_newer_detected_when_no_civil_yaml(tmp_path):
    """No .civil.yaml for a program — civil check is skipped but transpiler check fires."""
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    catala_file = output_dir / "orphan.catala_en"
    _touch(catala_file, _OLDER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 1
    assert result[0].program == "orphan"
    assert result[0].reason == "transpiler-newer"


# ---------------------------------------------------------------------------
# Tests — multiple files
# ---------------------------------------------------------------------------


def test_multiple_stale_files_all_reported(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    for program in ("alpha", "beta", "gamma"):
        catala_file = output_dir / f"{program}.catala_en"
        civil_file = specs_dir / f"{program}.civil.yaml"
        _touch(civil_file, _OLDER)
        _touch(catala_file, _OLDER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 3
    programs = {report.program for report in result}
    assert programs == {"alpha", "beta", "gamma"}


def test_mixed_stale_and_fresh(tmp_path):
    """Only stale files are reported; fresh files are silently passed."""
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    stale_catala = output_dir / "stale.catala_en"
    stale_civil = specs_dir / "stale.civil.yaml"
    _touch(stale_civil, _OLDER)
    _touch(stale_catala, _OLDER)

    fresh_catala = output_dir / "fresh.catala_en"
    fresh_civil = specs_dir / "fresh.civil.yaml"
    _touch(fresh_civil, _OLDER)
    _touch(fresh_catala, _NEWER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert len(result) == 1
    assert result[0].program == "stale"


def test_results_sorted_by_filename(tmp_path):
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    transpiler = tmp_path / "transpile_to_catala.py"
    _touch(transpiler, _NEWER)

    for program in ("zzz", "aaa", "mmm"):
        catala_file = output_dir / f"{program}.catala_en"
        civil_file = specs_dir / f"{program}.civil.yaml"
        _touch(civil_file, _OLDER)
        _touch(catala_file, _OLDER)

    result = stale_catala_files(output_dir, specs_dir, transpiler)

    assert [report.program for report in result] == ["aaa", "mmm", "zzz"]
