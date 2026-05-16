# /// script
# requires-python = ">=3.14"
# ///
"""Tests for transpile_to_catala.py — U3 comprehension lowering to Catala.

Covers:
- `count(v in coll where pred)` → `(number for v among coll such that pred)`
- `exists(v in coll where pred)` → `(exists v among coll such that pred)`
- Pipeline ordering: comprehension rewrites run BEFORE `&&`/`||` translation
- Balanced brackets: nested `between(...)`, string literals containing `where`
- Nested comprehensions (mixed count/exists, dotted collections like `v.items`)
- Backward compat: flat `count(<list>)` form preserved
- Trust boundary: malformed input passes through unchanged
- Sibling comprehensions in one expression
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import (  # noqa: E402
    _rewrite_count_comprehension,
    _rewrite_exists_comprehension,
    translate_expr_to_catala,
)


# ---------------------------------------------------------------------------
# Helper-level tests — pure rewrite, no full pipeline
# ---------------------------------------------------------------------------


class TestRewriteCountComprehension:
    def test_basic_shape(self):
        out = _rewrite_count_comprehension("count(v in items where v.x > 0)")
        assert out == "(number for v among items such that v.x > 0)"

    def test_flat_form_untouched(self):
        # Flat-form `count(<list>)` is not a comprehension; scanner returns None
        # and the substring is left untouched for Step 3.6c regex to consume.
        out = _rewrite_count_comprehension("count(reasons)")
        assert out == "count(reasons)"

    def test_two_sibling_comprehensions(self):
        out = _rewrite_count_comprehension(
            "count(v in xs where v.a > 0) + count(w in ys where w.b > 0)"
        )
        assert out == (
            "(number for v among xs such that v.a > 0) + "
            "(number for w among ys such that w.b > 0)"
        )

    def test_string_literal_with_inner_where(self):
        # The scanner is string-literal-aware; an inner `where` inside a quoted
        # literal must NOT terminate predicate scanning.
        out = _rewrite_count_comprehension(
            "count(v in items where v.status == 'archived where pending')"
        )
        assert out == (
            "(number for v among items such that v.status == "
            "'archived where pending')"
        )

    def test_nested_between_preserved(self):
        # The comprehension rewrite extracts the predicate verbatim; the inner
        # `between(...)` survives intact for the Step 3.5 rewrite (which runs
        # BEFORE comprehension lowering in the full pipeline — here we call
        # the helper directly and just check that nested parens don't confuse
        # the scanner).
        out = _rewrite_count_comprehension(
            "count(v in items where between(v.value, 1, 10))"
        )
        assert out == (
            "(number for v among items such that between(v.value, 1, 10))"
        )

    def test_token_boundary_discount_not_count(self):
        # `discount(...)` must NOT match the `count(` head.
        out = _rewrite_count_comprehension("discount(reasons)")
        assert out == "discount(reasons)"

    def test_malformed_passes_through(self):
        # Trust boundary: U1's validator would have caught this. The transpiler
        # leaves it unchanged; downstream Catala typecheck will surface the error.
        out = _rewrite_count_comprehension("count(v items where v.x > 0)")
        assert out == "count(v items where v.x > 0)"


class TestRewriteExistsComprehension:
    def test_basic_shape(self):
        out = _rewrite_exists_comprehension("exists(v in items where v.x == 'D')")
        assert out == "(exists v among items such that v.x == 'D')"

    def test_flat_form_untouched(self):
        # Single-arg `exists(<field>)` flat-form (no comprehension structure).
        out = _rewrite_exists_comprehension("exists(some_field)")
        assert out == "exists(some_field)"


class TestNestedComprehensions:
    def test_count_containing_exists_dotted_collection(self):
        # The collection `v.items` is a dotted attribute chain (U1 extension).
        out = _rewrite_count_comprehension(
            "count(v in coll where exists(w in v.items where w.flag))"
        )
        # The recursive sibling rewrite lowers the nested `exists(...)` first;
        # the outer `count(...)` then wraps the lowered predicate.
        assert out == (
            "(number for v among coll such that "
            "(exists w among v.items such that w.flag))"
        )

    def test_count_containing_count(self):
        out = _rewrite_count_comprehension(
            "count(v in xs where count(w in v.subs where w.flag) > 0)"
        )
        assert out == (
            "(number for v among xs such that "
            "(number for w among v.subs such that w.flag) > 0)"
        )

    def test_exists_containing_count(self):
        # When entering via _rewrite_exists_comprehension, the sibling-chain
        # rewrite handles the nested `count(...)`.
        out = _rewrite_exists_comprehension(
            "exists(v in xs where count(w in v.subs where w.flag) > 0)"
        )
        assert out == (
            "(exists v among xs such that "
            "(number for w among v.subs such that w.flag) > 0)"
        )


# ---------------------------------------------------------------------------
# Full-pipeline tests via translate_expr_to_catala
# ---------------------------------------------------------------------------


class TestFullPipelineComprehensions:
    def test_count_with_simple_predicate(self):
        out = translate_expr_to_catala("count(v in items where v.x > 0) >= 1")
        assert "(number for v among items such that v.x > 0) >= 1" in out

    def test_exists_with_eq_predicate(self):
        # Catala `==` → `=` (Step 9). String literals are converted to enum
        # constructors (Step 12) but only for double-quoted; single-quoted are
        # left alone here.
        out = translate_expr_to_catala("exists(v in items where v.x == 'D')")
        assert "(exists v among items such that v.x = 'D')" in out

    def test_amp_amp_in_predicate_becomes_and(self):
        # ORDERING INVARIANT TEST: comprehension lowering runs BEFORE Step 6
        # (`&&` → `and`), so the predicate's `&&` reaches Step 6 and gets
        # translated to Catala-native `and`.
        out = translate_expr_to_catala(
            "count(v in items where v.a > 0 && v.b < 10)"
        )
        assert "&&" not in out, f"Expected no literal '&&' in output: {out}"
        assert " and " in out
        assert "such that v.a > 0 and v.b < 10" in out

    def test_pipe_pipe_in_predicate_becomes_or(self):
        out = translate_expr_to_catala(
            "exists(v in items where v.a > 0 || v.b < 0)"
        )
        assert "||" not in out
        assert " or " in out

    def test_between_in_predicate_lowered(self):
        # Step 3.5 (`between` → `(low <= val and val <= high)`) runs BEFORE the
        # comprehension rewrite. Verify the resulting Catala has the expanded
        # between form inside the `such that` clause.
        out = translate_expr_to_catala(
            "count(v in items where between(v.value, 1, 10))"
        )
        assert "between(" not in out
        assert "(number for v among items such that " in out
        # The between rewrite emits `(1 <= v.value and v.value <= 10)`.
        assert "1 <= v.value and v.value <= 10" in out

    def test_string_literal_inner_where_preserved(self):
        out = translate_expr_to_catala(
            "count(v in items where v.status == 'archived where pending')"
        )
        # The single-quoted literal survives untouched; Step 12 only rewrites
        # double-quoted identifiers to enum constructors.
        assert "'archived where pending'" in out
        assert "(number for v among items such that " in out

    def test_nested_comprehensions_pipeline(self):
        out = translate_expr_to_catala(
            "count(v in coll where exists(w in v.items where w.flag))"
        )
        assert (
            "(number for v among coll such that "
            "(exists w among v.items such that w.flag))" in out
        )

    def test_two_sibling_comprehensions_pipeline(self):
        out = translate_expr_to_catala(
            "count(v in xs where v.a > 0) + count(w in ys where w.b > 0)"
        )
        assert "(number for v among xs such that v.a > 0)" in out
        assert "(number for w among ys such that w.b > 0)" in out


# ---------------------------------------------------------------------------
# Backward compat — flat-form invariants
# ---------------------------------------------------------------------------


class TestBackwardCompatFlatForm:
    def test_flat_count_equals_zero(self):
        # Catala uses `=` for equality (`==` → `=` at Step 9). Step 3.6c emits
        # `(number of reasons)`.
        out = translate_expr_to_catala("count(reasons) == 0")
        assert "(number of reasons) = 0" in out

    def test_flat_count_greater_than(self):
        out = translate_expr_to_catala("count(reasons) > 0")
        assert "(number of reasons) > 0" in out

    def test_flat_count_in_compound_expr(self):
        # `&&` becomes `and` at Step 6 (after Step 3.6c).
        out = translate_expr_to_catala("count(reasons) == 0 && approved")
        assert "(number of reasons) = 0" in out
        assert "&&" not in out
        assert " and " in out


# ---------------------------------------------------------------------------
# Snap eligibility end-to-end smoke test — backward-compat baseline check
# ---------------------------------------------------------------------------


class TestSnapEligibilityBaseline:
    """Confirms that transpiling the snap eligibility module is unchanged by U3.

    The snap module uses only the flat-form `count(reasons)`, so introducing
    the new comprehension rewrites must not alter its output.
    """

    def test_snap_eligibility_transpiles_unchanged(self, tmp_path):
        import subprocess

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        civil_path = os.path.join(
            repo_root, "domains", "snap", "specs", "eligibility.civil.yaml"
        )
        if not os.path.exists(civil_path):
            pytest.skip(f"snap eligibility CIVIL file not present at {civil_path}")
        out_path = str(tmp_path / "eligibility.catala_en")
        script = os.path.join(os.path.dirname(__file__), "transpile_to_catala.py")
        result = subprocess.run(
            [sys.executable, script, civil_path, out_path, "--scope", "Eligibility"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"transpile failed: stderr={result.stderr}\nstdout={result.stdout}"
        )
        with open(out_path, encoding="utf-8") as f:
            output = f.read()
        # Sanity: snap eligibility uses flat-form count(reasons); confirm the
        # backward-compat lowering still produced `(number of reasons)`.
        assert "(number of reasons)" in output
        # And NO accidental comprehension forms appeared.
        assert "number for " not in output
        assert "exists v among" not in output
