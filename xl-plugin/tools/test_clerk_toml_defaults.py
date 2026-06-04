# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Tests for clerk_toml_defaults.py — U1 of
docs/plans/2026-06-04-001-fix-lazy-create-clerk-toml-plan.md.

Covers the U1 test scenarios:
- spec-tier dir → include_dirs = ["."]
- test-tier dir → include_dirs = [".", ".."]
- (AE5) a deeper dir under tests/ resolves to spec-tier (strict basename rule)
- both literals carry [project] + target_dir
- spec-tier literal matches the pre-centralization _CLERK_TOML_DEFAULT
  byte-for-byte (guards the specs→output mirror regression)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import clerk_toml_defaults  # noqa: E402
from clerk_toml_defaults import SPEC_TIER, TEST_TIER, clerk_toml_for  # noqa: E402


# Byte-for-byte snapshot of the literal that lived in clerk_loop._CLERK_TOML_DEFAULT
# (and inline in xlator.cmd_new_domain) before centralization. If SPEC_TIER ever
# drifts from this, the specs→output clerk.toml mirror and existing clerk
# consumers would see a diff — that is the regression this guards.
_PRIOR_DEFAULT = '[project]\ntarget_dir = "_targets"\ninclude_dirs = ["."]\n'


class TestClerkTomlFor:
    def test_spec_dir_gets_spec_tier(self):
        assert 'include_dirs = ["."]' in clerk_toml_for(Path("/x/domains/foo/specs"))

    def test_output_dir_gets_spec_tier(self):
        # output/ basename is not "tests" → spec-tier, matching today's eager
        # output/clerk.toml content.
        assert clerk_toml_for(Path("/x/domains/foo/output")) == SPEC_TIER

    def test_tests_dir_gets_test_tier(self):
        result = clerk_toml_for(Path("/x/domains/foo/specs/tests"))
        assert 'include_dirs = [".", ".."]' in result
        assert result == TEST_TIER

    def test_nested_under_tests_gets_spec_tier(self):
        # AE5: strict basename rule — a dir named `regression` whose ancestor is
        # `tests` is spec-tier, not test-tier.
        assert clerk_toml_for(Path("/x/domains/foo/specs/tests/regression")) == SPEC_TIER

    def test_accepts_str_path(self):
        assert clerk_toml_for("/x/domains/foo/specs/tests") == TEST_TIER
        assert clerk_toml_for("/x/domains/foo/specs") == SPEC_TIER


class TestLiterals:
    def test_both_literals_have_project_header_and_target_dir(self):
        for literal in (SPEC_TIER, TEST_TIER):
            assert literal.startswith("[project]\n")
            assert 'target_dir = "_targets"' in literal

    def test_spec_tier_matches_prior_default_byte_for_byte(self):
        assert SPEC_TIER == _PRIOR_DEFAULT

    def test_tiers_differ_only_in_include_dirs(self):
        assert SPEC_TIER != TEST_TIER
        assert 'include_dirs = ["."]' in SPEC_TIER
        assert 'include_dirs = [".", ".."]' in TEST_TIER
