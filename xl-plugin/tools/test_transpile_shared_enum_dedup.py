# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for shared-enum deduplication in transpile_to_catala.py.

Two CIVIL fields that declare identical `values:` lists must share a single
Catala enumeration declaration. Without dedup, Catala emits ambiguous-constructor
errors when an expression like `field = "QMB"` is parsed — the bare constructor
QMB belongs to multiple enums.

The fix makes the first-encountered field name canonical: subsequent fields with
the same value-set reuse that enum name.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import emit_declarations


def _doc_with_input_and_output_sharing_values(values: list[str]) -> dict:
    """Doc declaring the same `values:` list on both an input field and an output field."""
    return {
        "module": "test_module.shared_enum",
        "description": "Test",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "ClientData": {
                "fields": {
                    "category_requested": {
                        "type": "string",
                        "values": values,
                        "optional": True,
                    },
                },
            },
        },
        "outputs": {
            "category_decision": {
                "type": "string",
                "values": values,
                "default": values[-1],
            },
        },
        "rules": [],
    }


class TestSharedEnumDedup:
    def test_identical_value_sets_emit_one_enum(self) -> None:
        doc = _doc_with_input_and_output_sharing_values(["QMB", "SLMB", "NONE"])
        lines = emit_declarations(doc, "SharedEnumDecision")
        declarations = [line for line in lines if "declaration enumeration" in line]
        # CategoryRequested (input, encountered first) and CategoryDecision (output)
        # share value set {QMB, SLMB, NONE}. Only one enum should be declared
        # (canonical = CategoryRequested, the first-encountered field's name).
        non_reason_decls = [d for d in declarations if "ReasonCode" not in d]
        assert len(non_reason_decls) == 1, (
            f"Expected exactly one shared enum, got {len(non_reason_decls)}: {non_reason_decls}"
        )
        assert "CategoryRequested" in non_reason_decls[0]

    def test_output_field_type_uses_canonical_enum_name(self) -> None:
        doc = _doc_with_input_and_output_sharing_values(["QMB", "SLMB", "NONE"])
        lines = emit_declarations(doc, "SharedEnumDecision")
        output_decls = [
            line for line in lines
            if "output category_decision" in line or "context category_decision" in line
        ]
        assert output_decls, "category_decision output not declared"
        # Output's Catala type should be the canonical enum (CategoryRequested),
        # not the field-derived CategoryDecision — otherwise the conditional that
        # echoes the input field won't typecheck.
        assert "content CategoryRequested" in output_decls[0], (
            f"Expected output typed as CategoryRequested; got: {output_decls[0]}"
        )

    def test_distinct_value_sets_emit_distinct_enums(self) -> None:
        doc = {
            "module": "test_module.distinct_enum",
            "description": "Test",
            "version": "1.0",
            "effective": {"start": "2024-01-01"},
            "jurisdiction": {"level": "federal", "country": "US"},
            "inputs": {
                "ClientData": {
                    "fields": {
                        "marital_status": {
                            "type": "string",
                            "values": ["SINGLE", "MARRIED"],
                            "optional": True,
                        },
                    },
                },
            },
            "outputs": {
                "category": {
                    "type": "string",
                    "values": ["QMB", "SLMB"],
                    "default": "QMB",
                },
            },
            "rules": [],
        }
        lines = emit_declarations(doc, "DistinctEnumDecision")
        non_reason_decls = [
            line for line in lines
            if "declaration enumeration" in line and "ReasonCode" not in line
        ]
        # Different value sets → distinct enums, both emitted.
        assert len(non_reason_decls) == 2, (
            f"Expected two distinct enums; got {len(non_reason_decls)}: {non_reason_decls}"
        )
