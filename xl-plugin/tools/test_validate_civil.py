# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic>=2.0", "pyyaml>=6.0"]
# ///
"""Tests for validate_civil.py — U2 expression-aware validation pass.

Covers:
  - Happy path / backward compat (snap, ak_doh exit 0).
  - Comprehension parse errors (empty predicate, bare-name predicate).
  - Bound-name shadowing across entity / entity field / computed / constant kinds.
  - Non-list collection check.
  - Primitive-list iteration edge case (bound name referenced as value, no `.field`).
  - U1→U2 API contract — bound_names from extract_refs drive the shadow check.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

from validate_civil import (  # noqa: E402
    _build_name_inventory,
    _collect_expressions,
    _lookup_collection_type,
    _scan_comprehension_iterables,
    _validate_expressions,
    validate,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _minimal_module(**overrides) -> dict:
    """Build a minimal valid CIVIL doc dict for unit tests. Sections are merged
    shallowly with overrides. Top-level required keys are always present."""
    base = {
        "module": "test_mod",
        "description": "Test module",
        "version": "2026Q1",
        "jurisdiction": {"level": "state", "country": "US"},
        "effective": {"start": "2026-01-01"},
        "inputs": {},
        "outputs": {
            "eligible": {"type": "bool", "default": True, "expr": "true"},
        },
        "rule_set": {"name": "test", "precedence": "deny_overrides_allow"},
        "rules": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _collect_expressions — tuple-yielding refactor
# ---------------------------------------------------------------------------


class TestCollectExpressions:
    def test_yields_field_id_tuples(self):
        doc = _minimal_module(
            computed={
                "x": {"type": "bool", "expr": "a > 0"},
                "y": {
                    "type": "bool",
                    "conditional": {"if": "a > 0", "then": "true", "else": "false"},
                },
            },
            rules=[
                {"id": "R1", "kind": "deny", "priority": 1,
                 "when": "x", "then": [{"set": {"eligible": False}}]}
            ],
        )
        out = _collect_expressions(doc)
        # All entries are (field_id, expr_str) tuples.
        assert all(isinstance(t, tuple) and len(t) == 2 for t in out)
        field_ids = [t[0] for t in out]
        # outputs.eligible.expr is collected (default fixture).
        assert "outputs.eligible.expr" in field_ids
        assert "computed.x.expr" in field_ids
        assert "computed.y.conditional.if" in field_ids
        assert "computed.y.conditional.then" in field_ids
        assert "computed.y.conditional.else" in field_ids
        assert "rules.R1.when" in field_ids

    def test_empty_module_yields_outputs_only(self):
        doc = _minimal_module()
        out = _collect_expressions(doc)
        # Only the default outputs.eligible.expr fixture entry.
        assert out == [("outputs.eligible.expr", "true")]


# ---------------------------------------------------------------------------
# _build_name_inventory
# ---------------------------------------------------------------------------


class TestBuildNameInventory:
    def test_collects_all_kinds(self):
        doc = _minimal_module(
            inputs={
                "Household": {"fields": {"earned_income": {"type": "money"}}},
            },
            constants={"MAX_AGE": 65},
            tables={"deductions": {"key": ["k"], "value": ["v"], "rows": []}},
            computed={
                "weighted_total": {"type": "float", "expr": "1.0"},
            },
        )
        inv = _build_name_inventory(doc)
        assert inv["Household"] == ("entity", "Household")
        assert inv["earned_income"] == ("entity field", "Household.earned_income")
        assert inv["weighted_total"] == ("computed", "weighted_total")
        assert inv["MAX_AGE"] == ("constant", "MAX_AGE")
        assert inv["deductions"] == ("table", "deductions")


# ---------------------------------------------------------------------------
# _scan_comprehension_iterables
# ---------------------------------------------------------------------------


class TestScanComprehensionIterables:
    def test_basic(self):
        assert _scan_comprehension_iterables(
            "count(v in recent_violations where v.x > 0)"
        ) == ["recent_violations"]

    def test_sibling_comprehensions(self):
        out = _scan_comprehension_iterables(
            "count(v in xs where v.a > 0) + count(w in ys where w.b > 0)"
        )
        assert out == ["xs", "ys"]

    def test_nested_comprehension(self):
        out = _scan_comprehension_iterables(
            "count(v in coll where exists(w in v.items where w.flag))"
        )
        # Outer `coll` and inner `v.items` (dotted — caller filters).
        assert out == ["coll", "v.items"]

    def test_flat_count_no_iterable(self):
        assert _scan_comprehension_iterables("count(reasons)") == []


# ---------------------------------------------------------------------------
# Happy path — valid comprehension validates clean
# ---------------------------------------------------------------------------


class TestValidateExpressionsHappyPath:
    def test_valid_qualified_comprehension(self):
        doc = _minimal_module(
            inputs={
                "RecentViolation": {
                    "fields": {
                        "recent_violations": {"type": "list"},
                        "severity_class": {"type": "enum", "values": ["A", "B", "C", "D"]},
                    },
                },
            },
            computed={
                "severity_d_escalation": {
                    "type": "bool",
                    "expr": "count(v in recent_violations where v.severity_class == 'D') >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "severity_class")
        assert errors == []

    def test_no_comprehension_validates_clean(self):
        doc = _minimal_module(
            inputs={"Household": {"fields": {"earned_income": {"type": "money"}}}},
            computed={
                "gross": {"type": "money", "expr": "Household.earned_income"},
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert errors == []


# ---------------------------------------------------------------------------
# Error paths — parse / qualified-access / empty predicate
# ---------------------------------------------------------------------------


class TestValidateExpressionsParseErrors:
    def test_bare_name_predicate(self):
        # The U1 ValueError fires for the bare `severity_class` in the predicate.
        doc = _minimal_module(
            inputs={
                "RecentViolation": {
                    "fields": {
                        "recent_violations": {"type": "list"},
                        "severity_class": {"type": "enum", "values": ["A", "B", "C", "D"]},
                    },
                },
            },
            computed={
                "severity_d_escalation": {
                    "type": "bool",
                    "expr": "count(v in recent_violations where severity_class == 'D') >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "severity_class")
        assert len(errors) == 1
        assert errors[0].startswith("severity_class.computed.severity_d_escalation.expr:")
        assert "qualified" in errors[0]
        assert "severity_class" in errors[0]

    def test_empty_predicate(self):
        doc = _minimal_module(
            inputs={
                "RecentViolation": {"fields": {"recent_violations": {"type": "list"}}},
            },
            computed={
                "bad": {
                    "type": "bool",
                    "expr": "count(v in recent_violations where)",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        # The field-id prefix is present; the underlying ValueError text is preserved.
        assert len(errors) == 1
        assert errors[0].startswith("test.computed.bad.expr:")
        assert "Cannot parse" in errors[0]

    def test_bare_constant_in_predicate(self):
        # Even a known constant raises the qualified-access error inside a predicate.
        doc = _minimal_module(
            inputs={"R": {"fields": {"items": {"type": "list"}}}},
            constants={"MAX_SCORE": 100},
            computed={
                "bad": {
                    "type": "bool",
                    "expr": "count(v in items where MAX_SCORE > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert len(errors) == 1
        assert "qualified" in errors[0]


# ---------------------------------------------------------------------------
# Bound-name shadowing — parametrized across all three kinds
# ---------------------------------------------------------------------------


class TestBoundNameShadowing:
    def test_shadows_computed(self):
        doc = _minimal_module(
            inputs={"R": {"fields": {"items": {"type": "list"}, "a": {"type": "int"}}}},
            computed={
                "severity_d_escalation": {"type": "bool", "expr": "true"},
                "uses_shadow": {
                    "type": "bool",
                    # `severity_d_escalation` shadows the computed field above.
                    "expr": (
                        "count(severity_d_escalation in items "
                        "where severity_d_escalation.a > 0) >= 1"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "shadows a known computed" in e
            and "'severity_d_escalation'" in e
            for e in errors
        )

    def test_shadows_entity(self):
        # PascalCase bound name `RecentViolation` shadows the entity name.
        doc = _minimal_module(
            inputs={
                "RecentViolation": {
                    "fields": {"items": {"type": "list"}, "a": {"type": "int"}}
                }
            },
            computed={
                "bad": {
                    "type": "bool",
                    "expr": (
                        "count(RecentViolation in items "
                        "where RecentViolation.a > 0) >= 1"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "shadows a known entity" in e
            and "'RecentViolation'" in e
            for e in errors
        )

    def test_shadows_constant(self):
        doc = _minimal_module(
            inputs={"R": {"fields": {"items": {"type": "list"}, "a": {"type": "int"}}}},
            constants={"MAX_AGE": 65},
            computed={
                "bad": {
                    "type": "bool",
                    "expr": "count(MAX_AGE in items where MAX_AGE.a > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "shadows a known constant" in e and "'MAX_AGE'" in e
            for e in errors
        )

    def test_shadows_entity_field(self):
        # Bound name shadows an entity field (snake_case match).
        doc = _minimal_module(
            inputs={
                "Household": {
                    "fields": {
                        "earned_income": {"type": "money"},
                        "items": {"type": "list"},
                    }
                }
            },
            computed={
                "bad": {
                    "type": "bool",
                    "expr": (
                        "count(earned_income in items "
                        "where earned_income.a > 0) >= 1"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "shadows a known entity field" in e
            and "'Household.earned_income'" in e
            for e in errors
        )

    def test_non_shadow_bound_name_validates_clean(self):
        # `v` is not in the inventory anywhere — no shadow error.
        doc = _minimal_module(
            inputs={"R": {"fields": {"items": {"type": "list"}, "a": {"type": "int"}}}},
            computed={
                "good": {
                    "type": "bool",
                    "expr": "count(v in items where v.a > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert errors == []


# ---------------------------------------------------------------------------
# Non-list collection check
# ---------------------------------------------------------------------------


class TestNonListCollectionCheck:
    def test_scalar_entity_field_iter_raises(self):
        # `client_age` is `int`, not `list` — iterating it is the error.
        doc = _minimal_module(
            inputs={"Client": {"fields": {"client_age": {"type": "int"}}}},
            computed={
                "bad": {
                    "type": "bool",
                    "expr": "count(v in client_age where v > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "iterates over non-list 'client_age'" in e
            and "(type: int)" in e
            for e in errors
        )

    def test_scalar_computed_iter_raises(self):
        doc = _minimal_module(
            inputs={},
            computed={
                "total": {"type": "float", "expr": "1.0"},
                "bad": {
                    "type": "bool",
                    "expr": "count(v in total where v > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert any(
            "iterates over non-list 'total'" in e and "(type: float)" in e
            for e in errors
        )

    def test_list_iter_validates_clean(self):
        doc = _minimal_module(
            inputs={
                "R": {
                    "fields": {
                        "items": {"type": "list"},
                        "a": {"type": "int"},
                    }
                }
            },
            computed={
                "good": {
                    "type": "bool",
                    "expr": "count(v in items where v.a > 0) >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert errors == []

    def test_dotted_iter_skipped(self):
        # `v.items` from a nested comprehension is an outer-bound ref; lookup
        # should skip it (it can't be resolved against the module inventory).
        doc = _minimal_module(
            inputs={"R": {"fields": {"coll": {"type": "list"}}}},
            computed={
                "good": {
                    "type": "bool",
                    "expr": (
                        "count(v in coll where exists(w in v.items where w.flag))"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        # The outer iter `coll` is a list — clean. The inner `v.items` is dotted
        # and skipped without crashing.
        assert errors == []


# ---------------------------------------------------------------------------
# Edge case — primitive-list iteration (bound name referenced as value)
# ---------------------------------------------------------------------------


class TestPrimitiveListIteration:
    def test_primitive_list_iter_validates_clean(self):
        # `tags: list[str]` — `v == 'urgent'` references the bound iterator as
        # a scalar value, not a field access. U1's walker suppresses `v`.
        doc = _minimal_module(
            inputs={"R": {"fields": {"tags": {"type": "list"}}}},
            computed={
                "good": {
                    "type": "bool",
                    "expr": "count(v in tags where v == 'urgent') >= 1",
                },
            },
        )
        errors, _ = _validate_expressions(doc, "test")
        assert errors == []


# ---------------------------------------------------------------------------
# U1→U2 API contract — bound_names from extract_refs drives the shadow check
# ---------------------------------------------------------------------------


class TestU1ToU2Contract:
    def test_bound_names_from_extract_refs_trigger_shadow_check(self):
        """End-to-end contract: extract_refs surfaces bound_names; the validator
        consumes those names and emits the shadowing error.

        Constructing an ExprRefs directly is not enough — the validator never
        accepts a pre-built ExprRefs. Instead, this test exercises the full
        path: a comprehension whose bound name matches a known computed-field
        name triggers the shadow error, proving that the validator (a) called
        extract_refs, (b) received the bound_names list, and (c) checked it
        against the inventory.
        """
        from civil_expr import extract_refs

        # Direct extract_refs probe — confirms bound_names is populated.
        refs = extract_refs(
            "count(severity_d_escalation in items where severity_d_escalation.a > 0)",
            computed_names={"severity_d_escalation", "items"},
            table_names=set(),
        )
        assert "severity_d_escalation" in refs.bound_names

        # End-to-end validator path — confirms the shadow check fires.
        doc = _minimal_module(
            inputs={"R": {"fields": {"items": {"type": "list"}, "a": {"type": "int"}}}},
            computed={
                "severity_d_escalation": {"type": "bool", "expr": "true"},
                "uses": {
                    "type": "bool",
                    "expr": (
                        "count(severity_d_escalation in items "
                        "where severity_d_escalation.a > 0) >= 1"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "m")
        assert any("shadows a known computed" in e for e in errors)


# ---------------------------------------------------------------------------
# _lookup_collection_type direct unit tests
# ---------------------------------------------------------------------------


class TestLookupCollectionType:
    def test_computed_list(self):
        doc = _minimal_module(
            computed={"xs": {"type": "list", "expr": "[]"}},
        )
        assert _lookup_collection_type("xs", doc) == ("xs", "list")

    def test_entity_field_scalar(self):
        doc = _minimal_module(
            inputs={"H": {"fields": {"age": {"type": "int"}}}},
        )
        assert _lookup_collection_type("age", doc) == ("H.age", "int")

    def test_unknown_returns_none(self):
        doc = _minimal_module()
        assert _lookup_collection_type("nope", doc) is None


# ---------------------------------------------------------------------------
# Integration — full validate() on real domain files
# ---------------------------------------------------------------------------


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAINS_DIR = REPO_ROOT / "domains"


@pytest.mark.skipif(
    not (DOMAINS_DIR / "snap" / "specs" / "eligibility.civil.yaml").exists(),
    reason="domains symlink not present",
)
class TestRealDomainBackwardCompat:
    def test_snap_eligibility_validates(self):
        path = DOMAINS_DIR / "snap" / "specs" / "eligibility.civil.yaml"
        assert validate(str(path)) is True

    def test_ak_doh_eligibility_expression_pass_clean(self):
        # ak_doh today fails at the Pydantic structural step (source: format
        # mismatch — pre-existing, unrelated to U2). U2's expression pass MUST
        # not introduce any new errors on top — assert _validate_expressions
        # returns clean.
        path = DOMAINS_DIR / "ak_doh" / "specs" / "eligibility.civil.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)
        errors, _ = _validate_expressions(data, data["module"])
        assert errors == [], f"unexpected expression errors on ak_doh: {errors}"

    def test_bare_name_predicate_inline_fragment_rejected(self):
        # Stable negative test using an inline CIVIL fragment: a bare-name
        # predicate inside a comprehension must be rejected with a
        # qualified-access error, regardless of dl file evolution.
        doc = _minimal_module(
            inputs={
                "RecentViolation": {
                    "fields": {
                        "recent_violations": {"type": "list"},
                        "severity_class": {
                            "type": "enum",
                            "values": ["A", "B", "C", "D"],
                        },
                    },
                },
            },
            computed={
                "severity_d_escalation": {
                    "type": "bool",
                    "expr": (
                        "count(v in recent_violations "
                        "where severity_class == 'D') >= 1"
                    ),
                },
            },
        )
        errors, _ = _validate_expressions(doc, "severity_class")
        assert errors, "expected qualified-access error for bare-name predicate"
        assert any("qualified" in e for e in errors)

    @pytest.mark.skipif(
        not (DOMAINS_DIR / "dl" / "specs" / "severity_class.civil.yaml").exists(),
        reason="dl domain not present",
    )
    def test_dl_severity_class_validates_clean_post_u4(self):
        # Positive coverage: after U4's qualified-access migration, the dl
        # severity_class module's expression pass returns clean. Asserts the
        # integrated stack (parser + validator + dl migration) is wired up.
        path = DOMAINS_DIR / "dl" / "specs" / "severity_class.civil.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)
        errors, _ = _validate_expressions(data, data["module"])
        assert errors == [], f"unexpected expression errors on dl/severity_class: {errors}"
