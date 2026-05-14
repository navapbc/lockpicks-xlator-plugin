# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for the shared detection helpers in civil_helpers.py
(parse_expr_hint, normalize_stage, load_per_file_computations)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import civil_helpers as ch  # noqa: E402


# ---------------------------------------------------------------------------
# parse_expr_hint
# ---------------------------------------------------------------------------

def test_parse_expr_hint_happy_path():
    """Standard `x = a + b` parses cleanly."""
    result = ch.parse_expr_hint("x = a + b")
    assert result == ("x", "a + b", ["a", "b"])


def test_parse_expr_hint_skips_numeric_literals():
    """RHS numeric tokens contribute nothing to rhs_tokens."""
    result = ch.parse_expr_hint("x = 5 + a")
    assert result is not None
    _, _, tokens = result
    assert tokens == ["a"]


def test_parse_expr_hint_skips_keywords():
    """`if`, `then`, `else`, `and`, `or`, `not`, `min`, `max` filtered."""
    result = ch.parse_expr_hint("x = if a then b else c")
    assert result is not None
    _, _, tokens = result
    assert tokens == ["a", "b", "c"]


def test_parse_expr_hint_strips_string_literals():
    """Single- and double-quoted strings are stripped before tokenization."""
    result = ch.parse_expr_hint("x = label == 'DENY'")
    assert result is not None
    _, _, tokens = result
    assert tokens == ["label"]


def test_parse_expr_hint_rejects_missing_equals():
    assert ch.parse_expr_hint("x + y") is None


def test_parse_expr_hint_rejects_empty_lhs():
    assert ch.parse_expr_hint(" = a + b") is None


def test_parse_expr_hint_rejects_non_identifier_lhs():
    """LHS that isn't a single snake_case identifier is unparseable
    (prevents misreading `a == b` as an assignment to `a`)."""
    assert ch.parse_expr_hint("a == b") is None


def test_parse_expr_hint_handles_dot_notation_rhs():
    """Dot-notation members on RHS contribute only the base name."""
    result = ch.parse_expr_hint("x = obj.member + other")
    assert result is not None
    _, _, tokens = result
    # `obj` is the base; `member` is excluded by the lookbehind on `.`;
    # `other` surfaces.
    assert "obj" in tokens
    assert "member" not in tokens
    assert "other" in tokens


def test_parse_expr_hint_returns_none_for_non_string():
    assert ch.parse_expr_hint(None) is None  # type: ignore[arg-type]
    assert ch.parse_expr_hint("") is None
    assert ch.parse_expr_hint(123) is None  # type: ignore[arg-type]


def test_parse_expr_hint_duplicate_tokens_preserved():
    """`a + a + b` should preserve duplicate `a` tokens in source order."""
    result = ch.parse_expr_hint("x = a + a + b")
    assert result is not None
    _, _, tokens = result
    assert tokens == ["a", "a", "b"]


# ---------------------------------------------------------------------------
# normalize_stage
# ---------------------------------------------------------------------------

def test_normalize_stage_preserves_canonical_value():
    assert ch.normalize_stage("initial_screening") == "initial_screening"


def test_normalize_stage_strips_test_suffix():
    assert ch.normalize_stage("income_test") == "income"


def test_normalize_stage_strips_check_suffix():
    assert ch.normalize_stage("gross_check") == "gross"


def test_normalize_stage_strips_evaluation_suffix():
    assert ch.normalize_stage("eligibility_evaluation") == "eligibility"


def test_normalize_stage_case_insensitive():
    assert ch.normalize_stage("GROSS_CHECK") == "gross"


def test_normalize_stage_none_passthrough():
    assert ch.normalize_stage(None) is None


def test_normalize_stage_empty_string():
    assert ch.normalize_stage("") is None
    assert ch.normalize_stage("   ") is None


def test_normalize_stage_strips_only_one_suffix():
    """Should strip a single suffix, not chain (`income_test_check` →
    `income_test`, not `income`)."""
    assert ch.normalize_stage("income_test_check") == "income_test"


# ---------------------------------------------------------------------------
# load_per_file_computations
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def test_load_per_file_computations_happy_path():
    """Domain with 2 per-file YAMLs returns dict with both."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "test_dom"
        base = domain / "policy_facets" / "computations"
        _write_yaml(base / "a.md.yaml", {"sections": [{"heading": "A"}]})
        _write_yaml(base / "sub" / "b.md.yaml", {"sections": [{"heading": "B"}]})

        result = ch.load_per_file_computations(domain)
        assert set(result.keys()) == {"a.md.yaml", "sub/b.md.yaml"}
        assert result["a.md.yaml"]["sections"][0]["heading"] == "A"
        assert result["sub/b.md.yaml"]["sections"][0]["heading"] == "B"


def test_load_per_file_computations_missing_dir_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "no_such_domain"
        assert ch.load_per_file_computations(domain) == {}


def test_load_per_file_computations_empty_dir_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "test_dom"
        (domain / "policy_facets" / "computations").mkdir(parents=True)
        assert ch.load_per_file_computations(domain) == {}


def test_load_per_file_computations_skips_non_yaml_files():
    """Files matching `*.md.yaml` are picked up; other files (e.g. `.md`)
    are ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "test_dom"
        base = domain / "policy_facets" / "computations"
        _write_yaml(base / "good.md.yaml", {"sections": []})
        (base / "ignored.md").write_text("not yaml", encoding="utf-8")
        result = ch.load_per_file_computations(domain)
        assert set(result.keys()) == {"good.md.yaml"}


def test_load_per_file_computations_skips_malformed_yaml():
    """Files that fail to parse don't break the loader."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = Path(tmp) / "test_dom"
        base = domain / "policy_facets" / "computations"
        _write_yaml(base / "good.md.yaml", {"sections": []})
        base.mkdir(parents=True, exist_ok=True)
        (base / "bad.md.yaml").write_text(
            "sections:\n  - heading: 'unterminated", encoding="utf-8"
        )
        result = ch.load_per_file_computations(domain)
        # `bad.md.yaml` is silently skipped; `good.md.yaml` survives.
        assert "good.md.yaml" in result
        assert "bad.md.yaml" not in result
