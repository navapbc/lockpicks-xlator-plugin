# /// script
# requires-python = ">=3.14"
# ///
"""Tests for civil_expr.py — U1 comprehension parsing and bound-name scoping."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import civil_expr  # noqa: E402
from civil_expr import (  # noqa: E402
    ExprRefs,
    _rewrite_comprehensions_for_ast,
    _scan_comprehension_args,
    extract_refs,
)


# ---------------------------------------------------------------------------
# _scan_comprehension_args — direct scanner tests
# ---------------------------------------------------------------------------


class TestScanComprehensionArgs:
    def test_basic_shape(self):
        # count(v in xs where v.a > 0)
        s = "count(v in xs where v.a > 0)"
        # start is right after "count("
        result = _scan_comprehension_args(s, 6)
        assert result is not None
        var, coll, pred, end = result
        assert var == "v"
        assert coll == "xs"
        assert pred == "v.a > 0"
        assert s[end] == ")"

    def test_predicate_with_nested_parens(self):
        s = "count(v in items where between(v.value, 1, 10))"
        result = _scan_comprehension_args(s, 6)
        assert result is not None
        var, coll, pred, end = result
        assert var == "v"
        assert coll == "items"
        assert pred == "between(v.value, 1, 10)"
        assert s[end] == ")"
        # Outer close is at the last char.
        assert end == len(s) - 1

    def test_predicate_with_string_literal_containing_where(self):
        s = "count(v in items where v.status == 'archived where pending')"
        result = _scan_comprehension_args(s, 6)
        assert result is not None
        var, coll, pred, end = result
        assert var == "v"
        assert coll == "items"
        assert pred == "v.status == 'archived where pending'"

    def test_predicate_with_string_literal_containing_paren(self):
        s = "count(v in items where v.label == '(special)')"
        result = _scan_comprehension_args(s, 6)
        assert result is not None
        var, coll, pred, end = result
        assert pred == "v.label == '(special)'"

    def test_double_quoted_string(self):
        s = 'count(v in items where v.label == "in where")'
        result = _scan_comprehension_args(s, 6)
        assert result is not None
        _, _, pred, _ = result
        assert pred == 'v.label == "in where"'

    def test_flat_form_returns_none(self):
        # count(reasons) — no `in <ident> where ...`
        s = "count(reasons)"
        assert _scan_comprehension_args(s, 6) is None

    def test_single_arg_exists_returns_none(self):
        s = "exists(client_gross_earned)"
        # start is right after "exists("
        assert _scan_comprehension_args(s, 7) is None

    def test_empty_predicate_returns_none(self):
        s = "count(v in xs where)"
        assert _scan_comprehension_args(s, 6) is None

    def test_missing_where_returns_none(self):
        s = "count(v in xs)"
        assert _scan_comprehension_args(s, 6) is None

    def test_missing_in_returns_none(self):
        s = "count(v xs where v.a)"
        assert _scan_comprehension_args(s, 6) is None


# ---------------------------------------------------------------------------
# _rewrite_comprehensions_for_ast — rewrite tests
# ---------------------------------------------------------------------------


class TestRewriteComprehensionsForAst:
    def test_count_rewrite(self):
        out = _rewrite_comprehensions_for_ast(
            "count(v in xs where v.a > 0) >= 1"
        )
        assert out == "len([v for v in xs if v.a > 0]) >= 1"

    def test_exists_rewrite(self):
        out = _rewrite_comprehensions_for_ast(
            "exists(v in xs where v.a > 0)"
        )
        assert out == "any(v for v in xs if v.a > 0)"

    def test_flat_count_untouched(self):
        out = _rewrite_comprehensions_for_ast("count(reasons) == 0")
        # flat form left in place
        assert out == "count(reasons) == 0"

    def test_flat_exists_untouched(self):
        out = _rewrite_comprehensions_for_ast("exists(client_gross_earned)")
        assert out == "exists(client_gross_earned)"

    def test_sibling_comprehensions(self):
        out = _rewrite_comprehensions_for_ast(
            "count(v in xs where v.a > 0) + count(v in ys where v.b > 0)"
        )
        assert out == (
            "len([v for v in xs if v.a > 0]) + "
            "len([v for v in ys if v.b > 0])"
        )

    def test_nested_comprehension(self):
        # The outer `count(... where exists(...))`: the scanner sees the whole
        # predicate `exists(w in v.items where w.flag)` and rewrites the outer
        # count. The defensive guard then re-scans the result; the inner
        # `exists(` is now in the rewritten predicate as part of the count's
        # list comprehension's `if` clause and gets rewritten on a subsequent
        # pass... actually since we run one pass, it must rewrite inner first.
        # We rely on left-to-right scan: outer `count(` is found first, scanner
        # consumes the entire predicate including the inner exists(...). The
        # rewrite produces `len([v for v in coll if exists(w in v.items where w.flag)])`.
        # The defensive guard then catches the still-present `exists(... in ... where ...)`
        # and would raise. We need to handle this by running rewrite multiple
        # passes or by recursively rewriting predicates. Verify behavior:
        out = _rewrite_comprehensions_for_ast(
            "count(v in coll where exists(w in v.items where w.flag))"
        )
        # The output must have both rewrites applied for the defensive guard not to fire.
        # The current implementation rewrites left-to-right; let's check:
        # If outer is rewritten first, the inner exists(...) substring sits inside
        # the list-comp's `if`, and the rewriter (which is a single pass) won't
        # revisit it. The defensive guard then fires.
        # Expected: both layers must be rewritten. We'll verify this by asserting
        # the output contains neither raw "count(" nor raw "exists(" with `in`+`where`.
        assert "count(v in coll where" not in out
        # If the inner was not rewritten, defensive guard would have raised.
        # If we reach here, the output is well-formed.
        assert "exists(w in v.items where" not in out

    def test_partial_rewrite_raises_when_scanner_misbehaves(self):
        # Stub _scan_comprehension_args to always return None — the rewriter
        # then leaves the comprehension untouched, and the defensive guard
        # detects the still-present `in`+`where` and raises.
        with patch.object(civil_expr, "_scan_comprehension_args", return_value=None):
            with pytest.raises(ValueError, match="partial comprehension rewrite"):
                _rewrite_comprehensions_for_ast(
                    "count(v in xs where v.a > 0)"
                )

    # ---- String-literal-blind outer walker — regression tests (R1) ----

    def test_string_literal_with_count_substring_not_rewritten(self):
        # `count(...)` inside a single-quoted string literal must be preserved
        # verbatim. The outer walker must track string state and skip heads
        # found inside string literals.
        s = "reason == 'see count(v in xs where v > 0)'"
        out = _rewrite_comprehensions_for_ast(s)
        assert out == s, f"string literal content corrupted: {out!r}"

    def test_string_literal_with_exists_substring_not_rewritten(self):
        s = "reason == 'see exists(v in xs where v > 0)'"
        out = _rewrite_comprehensions_for_ast(s)
        assert out == s, f"string literal content corrupted: {out!r}"

    def test_string_literal_with_sum_substring_not_rewritten(self):
        # Sanity check: civil_expr only rewrites count/exists (sum is lowered
        # downstream by the transpiler), so this MUST be a no-op regardless.
        # Included to guard against future regressions if a sum head is added.
        s = "reason == 'see sum(v.amount for v in xs)'"
        out = _rewrite_comprehensions_for_ast(s)
        assert out == s, f"string literal content corrupted: {out!r}"

    def test_double_quoted_string_with_comprehension_substring(self):
        s = 'reason == "count(v in xs where v.a > 0)"'
        out = _rewrite_comprehensions_for_ast(s)
        assert out == s, f"double-quoted string literal content corrupted: {out!r}"

    def test_string_literal_with_escaped_quote_preserves_state(self):
        # An escaped single-quote inside a single-quoted string must not
        # terminate the literal — heads after it should still be considered
        # inside the string.
        s = r"reason == 'don\'t count(v in xs where v > 0)'"
        out = _rewrite_comprehensions_for_ast(s)
        assert out == s, f"escaped-quote string literal corrupted: {out!r}"


# ---------------------------------------------------------------------------
# extract_refs — happy path on comprehensions
# ---------------------------------------------------------------------------


class TestExtractRefsComprehensionHappyPath:
    def test_count_basic(self):
        refs = extract_refs(
            "count(v in recent_violations where v.severity_class == 'D') >= 1",
            computed_names={"recent_violations"},
            table_names=set(),
        )
        assert "recent_violations" in refs.computed_refs
        assert "v" in refs.bound_names
        # severity_class is suppressed as an iterated-row field access.
        assert "severity_class" not in refs.entity_fields
        assert "severity_class" not in refs.computed_refs
        assert "severity_class" not in refs.constant_refs
        # No bare Names for v should leak.
        assert "v" not in refs.computed_refs
        assert "v" not in refs.constant_refs

    def test_count_with_compound_predicate(self):
        refs = extract_refs(
            "count(v in recent_violations where v.severity_class == 'C' "
            "&& v.months_since_adjudication <= 12) >= 2",
            computed_names={"recent_violations"},
            table_names=set(),
        )
        assert "recent_violations" in refs.computed_refs
        assert "v" in refs.bound_names
        # Iterated-row fields suppressed.
        assert not any("v." in f for f in refs.entity_fields)

    def test_exists_basic(self):
        refs = extract_refs(
            "exists(v in recent_violations where v.severity_class == 'D')",
            computed_names={"recent_violations"},
            table_names=set(),
        )
        assert "recent_violations" in refs.computed_refs
        assert "v" in refs.bound_names

    def test_mixed_comprehension_and_scalar(self):
        refs = extract_refs(
            "count(v in recent_violations where v.severity_class == 'D') >= 1 "
            "|| weighted_point_total >= 10",
            computed_names={"recent_violations", "weighted_point_total"},
            table_names=set(),
        )
        assert "recent_violations" in refs.computed_refs
        assert "weighted_point_total" in refs.computed_refs
        assert "v" in refs.bound_names

    def test_sibling_comprehensions_reuse_bound_name(self):
        refs = extract_refs(
            "count(v in xs where v.a > 0) + count(v in ys where v.b > 0)",
            computed_names={"xs", "ys"},
            table_names=set(),
        )
        assert "xs" in refs.computed_refs
        assert "ys" in refs.computed_refs
        # bound_names contains `v` (deduplicated across siblings).
        assert "v" in refs.bound_names
        # No bleed: ensure neither comprehension's iterated-row fields leak.
        assert not any(name in refs.computed_refs for name in ("a", "b"))

    def test_nested_comprehension(self):
        refs = extract_refs(
            "count(v in coll where exists(w in v.items where w.flag))",
            computed_names={"coll"},
            table_names=set(),
        )
        assert "coll" in refs.computed_refs
        # Both bound names captured.
        assert "v" in refs.bound_names
        assert "w" in refs.bound_names
        # v.items and w.flag suppressed.
        assert not any(f.startswith("v.") for f in refs.entity_fields)
        assert not any(f.startswith("w.") for f in refs.entity_fields)

    def test_predicate_with_between_call(self):
        refs = extract_refs(
            "count(v in items where between(v.value, 1, 10))",
            computed_names={"items"},
            table_names=set(),
        )
        assert "items" in refs.computed_refs
        assert "v" in refs.bound_names

    def test_predicate_with_string_literal_containing_where(self):
        refs = extract_refs(
            "count(v in items where v.status == 'archived where pending')",
            computed_names={"items"},
            table_names=set(),
        )
        assert "items" in refs.computed_refs
        assert "v" in refs.bound_names


# ---------------------------------------------------------------------------
# extract_refs — backward compatibility
# ---------------------------------------------------------------------------


class TestExtractRefsBackwardCompat:
    def test_flat_count_still_works(self):
        refs = extract_refs(
            "count(reasons) == 0",
            computed_names={"reasons"},
            table_names=set(),
        )
        assert "reasons" in refs.computed_refs
        assert refs.bound_names == []

    def test_single_arg_exists_still_works(self):
        refs = extract_refs(
            "exists(client_gross_earned)",
            computed_names={"client_gross_earned"},
            table_names=set(),
        )
        assert "client_gross_earned" in refs.computed_refs
        assert refs.bound_names == []

    def test_simple_entity_field(self):
        refs = extract_refs(
            "Household.earned_income * 0.2",
            computed_names=set(),
            table_names=set(),
        )
        assert refs.entity_fields == ["Household.earned_income"]
        assert refs.bound_names == []

    def test_constant_ref(self):
        refs = extract_refs(
            "income * EARNED_INCOME_DEDUCTION_RATE",
            computed_names={"income"},
            table_names=set(),
        )
        assert "income" in refs.computed_refs
        assert "EARNED_INCOME_DEDUCTION_RATE" in refs.constant_refs
        assert refs.bound_names == []

    def test_table_call(self):
        refs = extract_refs(
            "table('standard_deductions', household_size).amount",
            computed_names={"household_size"},
            table_names={"standard_deductions"},
        )
        assert "standard_deductions" in refs.table_refs
        assert "household_size" in refs.computed_refs
        assert refs.bound_names == []

    def test_civil_operators_still_translated(self):
        refs = extract_refs(
            "income > 0 && !disabled || age >= 65",
            computed_names={"income", "disabled", "age"},
            table_names=set(),
        )
        for name in ("income", "disabled", "age"):
            assert name in refs.computed_refs
        assert refs.bound_names == []


# ---------------------------------------------------------------------------
# extract_refs — error paths
# ---------------------------------------------------------------------------


class TestExtractRefsErrors:
    def test_empty_predicate_raises(self):
        # `count(v in recent_violations where)` — scanner returns None, falls
        # through to ast.parse, which fails on the malformed string.
        with pytest.raises(ValueError):
            extract_refs(
                "count(v in recent_violations where)",
                computed_names={"recent_violations"},
                table_names=set(),
            )

    def test_bare_name_in_predicate_raises(self):
        # `severity_class` is a bare name inside the predicate; the walker must
        # raise the qualified-access-required ValueError.
        with pytest.raises(ValueError, match="qualified .* access is required"):
            extract_refs(
                "count(v in recent_violations where severity_class == 'D')",
                computed_names={"recent_violations", "severity_class"},
                table_names=set(),
            )

    def test_bare_constant_in_predicate_raises(self):
        # Even a known constant is rejected as a bare name inside the predicate.
        with pytest.raises(ValueError, match="qualified .* access is required"):
            extract_refs(
                "count(v in items where MAX_SCORE > 0)",
                computed_names={"items"},
                table_names=set(),
            )


# ---------------------------------------------------------------------------
# Sanity — ExprRefs default for non-comprehension expressions
# ---------------------------------------------------------------------------


class TestExprRefsBoundNamesDefault:
    def test_non_comprehension_has_empty_bound_names(self):
        refs = extract_refs(
            "a + b * c",
            computed_names={"a", "b", "c"},
            table_names=set(),
        )
        assert refs.bound_names == []

    def test_default_field_factory_is_isolated(self):
        # Construct two ExprRefs and verify their bound_names lists are independent.
        r1 = ExprRefs()
        r2 = ExprRefs()
        r1.bound_names.append("v")
        assert r2.bound_names == []
