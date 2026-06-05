# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Tests for clerk_target_inject.py — U1 of
docs/plans/2026-06-04-002-fix-catala-to-python-bugs-plan.md.

Covers AE1 (error surfaces), AE2 (clerk default target_dir), AE3 (override
target_dir), AE4 (single-module inject), AE5 (multi-module topo order),
AE6 (never rewrite existing matching block).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import clerk_target_inject as cti  # noqa: E402
from clerk_target_inject import ensure_target_injected, main  # noqa: E402


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _layout(tmp: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """Build a domain layout: tmp/output/clerk.toml (per files map) and
    tmp/specs/<module>.catala_en per files map.

    `files` keys: "clerk.toml" (relative to tmp/output), or "<name>.catala_en"
    (relative to tmp/specs). Pass `None` value to skip the file entirely.
    """
    output_dir = tmp / "output"
    specs_dir = tmp / "specs"
    output_dir.mkdir()
    specs_dir.mkdir()
    for name, content in files.items():
        if content is None:
            continue
        if name == "clerk.toml":
            _write(output_dir / "clerk.toml", content)
        else:
            _write(specs_dir / name, content)
    return output_dir, specs_dir


# ---------- Single-module domain ----------

def test_single_module_creates_target_block_and_returns_default_target_dir(tmp_path):
    """AE4: fresh domain (only [project] in clerk.toml), single-module → inject
    block with one module entry; stdout (=return value) is `_targets`."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ninclude_dirs = ["."]\n',
            "foo.catala_en": "> Module Foo\n\n# Foo\n",
        },
    )
    td = ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert td == "_targets"
    text = (output_dir / "clerk.toml").read_text()
    assert "[[target]]" in text
    assert 'name = "foo"' in text
    assert 'modules = ["Foo"]' in text
    assert 'backends = ["python"]' in text


# ---------- Multi-module topo order ----------

def test_multi_module_topo_order_leaves_first(tmp_path):
    """AE5: passes_income → income_tests → deductions chain produces
    modules = ["Deductions", "Income_tests", "Passes_income"]."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ntarget_dir = "_targets"\n',
            "deductions.catala_en": "> Module Deductions\n",
            "income_tests.catala_en": "> Module Income_tests\n\n> Using Deductions\n",
            "passes_income.catala_en": "> Module Passes_income\n\n> Using Income_tests\n",
        },
    )
    td = ensure_target_injected(output_dir, "passes_income", "passes_income", specs_dir)
    assert td == "_targets"
    text = (output_dir / "clerk.toml").read_text()
    assert 'modules = ["Deductions", "Income_tests", "Passes_income"]' in text


# ---------- target_dir override (AE2 vs AE3) ----------

def test_target_dir_override_targets_no_underscore(tmp_path):
    """AE3: clerk.toml with target_dir = "targets" (no underscore, snap's
    existing override) → returns 'targets'; block still injected if missing."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ntarget_dir = "targets"\ninclude_dirs = ["."]\n',
            "foo.catala_en": "> Module Foo\n",
        },
    )
    td = ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert td == "targets"
    assert "[[target]]" in (output_dir / "clerk.toml").read_text()


def test_target_dir_default_underscore(tmp_path):
    """AE2: explicit `target_dir = "_targets"` (clerk default) → returns
    `_targets`."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ntarget_dir = "_targets"\n',
            "foo.catala_en": "> Module Foo\n",
        },
    )
    assert ensure_target_injected(output_dir, "foo", "foo", specs_dir) == "_targets"


# ---------- Idempotency (R7 / AE6) ----------

def test_existing_matching_target_block_leaves_file_byte_identical(tmp_path):
    """AE6 / R7: pre-existing `[[target]]` with the same name → file
    byte-identical after invocation."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": (
                '[project]\ninclude_dirs = ["."]\n\n'
                '[[target]]\nname = "foo"\nmodules = ["SomethingElse"]\n'
                'backends = ["python"]\n'
            ),
            "foo.catala_en": "> Module Foo\n",
        },
    )
    before = (output_dir / "clerk.toml").read_bytes()
    ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    after = (output_dir / "clerk.toml").read_bytes()
    assert before == after


def test_existing_different_name_target_block_still_appends_new(tmp_path):
    """`[[target]] name = "other"` already exists; ours is missing → append
    only the new one, leave the existing untouched."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": (
                '[project]\ninclude_dirs = ["."]\n\n'
                '[[target]]\nname = "other"\nmodules = ["X"]\nbackends = ["python"]\n'
            ),
            "foo.catala_en": "> Module Foo\n",
        },
    )
    ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    text = (output_dir / "clerk.toml").read_text()
    assert 'name = "other"' in text
    assert 'name = "foo"' in text
    assert text.count("[[target]]") == 2


# ---------- Empty / missing clerk.toml ----------

def test_clerk_toml_absent_creates_spec_tier_default_then_injects(tmp_path):
    """When clerk.toml is missing entirely (fresh new-domain), the helper
    writes SPEC_TIER then appends the [[target]] block."""
    output_dir = tmp_path / "output"
    specs_dir = tmp_path / "specs"
    output_dir.mkdir()
    specs_dir.mkdir()
    _write(specs_dir / "foo.catala_en", "> Module Foo\n")
    td = ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert td == "_targets"
    text = (output_dir / "clerk.toml").read_text()
    assert text.startswith("[project]")
    assert 'include_dirs = ["."]' in text
    assert "[[target]]" in text


def test_clerk_toml_empty_file_treated_as_no_project(tmp_path):
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": "",
            "foo.catala_en": "> Module Foo\n",
        },
    )
    td = ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert td == "_targets"
    assert "[[target]]" in (output_dir / "clerk.toml").read_text()


def test_clerk_toml_without_trailing_newline_gets_safely_appended(tmp_path):
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ninclude_dirs = ["."]',  # no trailing \n
            "foo.catala_en": "> Module Foo\n",
        },
    )
    ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    text = (output_dir / "clerk.toml").read_text()
    # Ensure no concatenated `include_dirs = ["."][[target]]` garbage.
    assert 'include_dirs = ["."]\n' in text
    assert "\n[[target]]\n" in text


# ---------- Error paths (AE1) ----------

def test_invalid_toml_raises_with_path_in_message(tmp_path):
    """AE1: misconfigured clerk.toml surfaces error to stderr with file path."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": "this is not = valid toml = [",
            "foo.catala_en": "> Module Foo\n",
        },
    )
    with pytest.raises(ValueError) as exc:
        ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert "clerk.toml" in str(exc.value)


def test_missing_root_module_spec_raises(tmp_path):
    """Helper raises when <module>.catala_en doesn't exist in specs/."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\n',
            "deductions.catala_en": "> Module Deductions\n",
        },
    )
    with pytest.raises(ValueError) as exc:
        ensure_target_injected(output_dir, "missing", "missing", specs_dir)
    assert "not found in specs" in str(exc.value)


def test_using_references_missing_module_raises(tmp_path):
    """`> Using NotThere` with no NotThere.catala_en file → ValueError."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\n',
            "foo.catala_en": "> Module Foo\n\n> Using NotThere\n",
        },
    )
    with pytest.raises(ValueError) as exc:
        ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert "NotThere" in str(exc.value)


def test_cyclic_using_raises(tmp_path):
    """A uses B and B uses A → ValueError naming a participant."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\n',
            "a.catala_en": "> Module A\n\n> Using B\n",
            "b.catala_en": "> Module B\n\n> Using A\n",
        },
    )
    with pytest.raises(ValueError) as exc:
        ensure_target_injected(output_dir, "a", "a", specs_dir)
    assert "Cyclic" in str(exc.value)


def test_multi_module_per_file_raises(tmp_path):
    """A single .catala_en with two `> Module` directives → ValueError."""
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\n',
            "foo.catala_en": "> Module Foo\n\n> Module Bar\n",
        },
    )
    with pytest.raises(ValueError) as exc:
        ensure_target_injected(output_dir, "foo", "foo", specs_dir)
    assert "more than one > Module" in str(exc.value)


# ---------- camel_module convention ----------

def test_camel_module_preserves_underscores():
    assert cti._camel_module("passes_income") == "Passes_income"
    assert cti._camel_module("eligibility") == "Eligibility"
    assert cti._camel_module("foo") == "Foo"
    assert cti._camel_module("") == ""


# ---------- main() CLI surface ----------

def test_main_prints_target_dir_on_stdout_only(tmp_path, capsys):
    output_dir, specs_dir = _layout(
        tmp_path,
        {
            "clerk.toml": '[project]\ntarget_dir = "_targets"\n',
            "foo.catala_en": "> Module Foo\n",
        },
    )
    rc = main([str(output_dir), "foo", "foo", str(specs_dir)])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out.strip() == "_targets"
    assert "Injected" in err  # info goes to stderr


def test_main_wrong_argc_returns_2_with_usage_on_stderr(capsys):
    rc = main(["only-one-arg"])
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "Usage:" in err


def test_main_missing_output_dir_returns_1(tmp_path, capsys):
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    rc = main([str(tmp_path / "nope"), "foo", "foo", str(specs_dir)])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "output_dir not found" in err
