# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Tests for xlator.py command functions.

Narrow surface: just `cmd_copy_source_to_output` for now (added with
the catala-pipeline bug fix that surfaced when running against snap's
multi-module Catala source). The xlator.py module-level code reads
DOMAINS_FULLPATH at import time, so this test file sets it before
importing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def xlator_module(monkeypatch):
    """Import xlator.py with DOMAINS_FULLPATH set to a writable temp dir.

    Returns the module. Each test that uses this fixture gets a fresh
    DOMAINS_FULLPATH so the global state in xlator.py reflects the test's
    temp domain layout."""
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DOMAINS_FULLPATH", tmp)
        # Force a fresh import each call so DOMAINS_FULLPATH is re-read.
        for k in [m for m in sys.modules if m == "xlator"]:
            del sys.modules[k]
        import xlator  # noqa: E402
        yield xlator, Path(tmp)


class TestCopySourceToOutput:
    def test_copies_catala_en_source(self, xlator_module):
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")

        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out = domains_root / "snap" / "output" / "is_eligible.catala_en"
        assert out.is_file()
        assert out.read_text() == "> Module Is_eligible\n"

    def test_copies_sibling_modules(self, xlator_module):
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")
        (specs / "deductions.catala_en").write_text("> Module Deductions\n")

        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out = domains_root / "snap" / "output"
        assert (out / "is_eligible.catala_en").is_file()
        assert (out / "deductions.catala_en").is_file()

    def test_copies_clerk_toml(self, xlator_module):
        # Regression: clerk.toml in specs/ must be mirrored to output/
        # so `clerk test` (run with cwd=output/) can resolve module
        # bindings. Without this, ninja fails with
        # `error: '<Module>@src' missing and no known rule to make it`.
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")
        clerk_toml = textwrap.dedent("""\
            [project]
            target_dir = "_targets"
            include_dirs = ["."]

            [[target]]
            name = "is_eligible"
            modules = ["Is_eligible"]
            backends = ["python"]
            """)
        (specs / "clerk.toml").write_text(clerk_toml)

        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out_clerk = domains_root / "snap" / "output" / "clerk.toml"
        assert out_clerk.is_file()
        assert out_clerk.read_text() == clerk_toml

    def test_missing_source_exits_with_clear_error(self, xlator_module):
        xlator, domains_root = xlator_module
        (domains_root / "snap" / "specs").mkdir(parents=True)
        with pytest.raises(SystemExit):
            xlator.cmd_copy_source_to_output("snap", "is_eligible")

    def test_copies_tests_catala_en_into_output_tests(self, xlator_module):
        # v14.0.0: .catala_en test fixtures live under specs/tests/
        # (authored, checked into git) and must be mirrored to
        # output/tests/ so `clerk test` finds them. Without this,
        # the post-cutover catala-pipeline has no test fixtures to run.
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs_tests = specs / "tests"
        specs_tests.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")
        catala_test_body = textwrap.dedent("""\
            > Using Is_eligible

            #[test] declaration scope TestAllow001:
              result scope Is_eligible.IsEligible
            """)
        (specs_tests / "is_eligible_tests.catala_en").write_text(catala_test_body)

        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out_test = (
            domains_root / "snap" / "output" / "tests" / "is_eligible_tests.catala_en"
        )
        assert out_test.is_file()
        assert out_test.read_text() == catala_test_body

    def test_specs_tests_yaml_not_mirrored(self, xlator_module):
        # YAML test files stay in specs/tests/ — they are the SME-facing
        # source; only the AI-emitted .catala_en peers are mirrored to
        # output/tests/ for clerk test consumption.
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs_tests = specs / "tests"
        specs_tests.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")
        (specs_tests / "is_eligible_tests.yaml").write_text("tests: []\n")

        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out_tests_dir = domains_root / "snap" / "output" / "tests"
        # The output/tests/ directory may or may not be created depending on
        # whether .catala_en peers exist; either is acceptable here. What
        # must NOT exist is a YAML copy.
        out_yaml = out_tests_dir / "is_eligible_tests.yaml"
        assert not out_yaml.exists()

    def test_missing_specs_tests_dir_is_not_an_error(self, xlator_module):
        # Domains that haven't authored any test fixtures yet have no
        # specs/tests/ directory. The copy step must not raise — matches
        # the pre-existing tolerance for missing clerk.toml.
        xlator, domains_root = xlator_module
        specs = domains_root / "snap" / "specs"
        specs.mkdir(parents=True)
        (specs / "is_eligible.catala_en").write_text("> Module Is_eligible\n")

        # No specs/tests/ directory created.
        xlator.cmd_copy_source_to_output("snap", "is_eligible")

        out = domains_root / "snap" / "output" / "is_eligible.catala_en"
        assert out.is_file()


class TestDeriveScopeName:
    def test_reads_first_declaration_scope(self, xlator_module, tmp_path):
        xlator, _ = xlator_module
        src = tmp_path / "is_eligible.catala_en"
        src.write_text(textwrap.dedent("""\
            > Module Is_eligible

            ```catala-metadata
            declaration scope IsEligible:
              input household content Household
              output eligible content boolean
            ```
            """))
        assert xlator._derive_scope_name(src) == "IsEligible"

    def test_does_not_assume_decision_suffix(self, xlator_module, tmp_path):
        # The helper must NOT silently append 'Decision' — the AI authors
        # any scope name it wants.
        xlator, _ = xlator_module
        src = tmp_path / "anything.catala_en"
        src.write_text("declaration scope AThingNamedDifferently:\n")
        assert xlator._derive_scope_name(src) == "AThingNamedDifferently"

    def test_exits_on_missing_declaration(self, xlator_module, tmp_path):
        xlator, _ = xlator_module
        src = tmp_path / "no_scope.catala_en"
        src.write_text("> Module X\n\n# Prose only, no scope decl.\n")
        with pytest.raises(SystemExit):
            xlator._derive_scope_name(src)
