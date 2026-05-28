# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for enum constructor capitalization in transpile_to_catala.py.

Covers Fix #23.3 (type:enum values: path) and Fix #26 (type:string table_vals
path, _format_key_condition string path, and build_field_type_map emit forms).

type: enum fields with lowercase values: entries must emit PascalCase Catala
constructors (e.g. "individual" → "-- Individual"). Uppercase-initial codes
(e.g. "A1E") must be preserved unchanged.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from transpile_to_catala import emit_declarations, transpile, _format_key_condition
from transpile_to_catala_tests import build_field_type_map


# =============================================================================
# Fixtures
# =============================================================================

def _doc_with_enum_field(values: list[str]) -> dict:
    """Minimal CIVIL doc with a type:enum input field carrying the given values."""
    return {
        "module": "test_module.program_standards",
        "description": "Test",
        "version": "1.0",
        "effective": {"start": "2024-01-01"},
        "jurisdiction": {"level": "federal", "country": "US"},
        "inputs": {
            "Household": {
                "fields": {
                    "filing_status": {
                        "type": "enum",
                        "values": values,
                    }
                }
            }
        },
        "outputs": {
            "result": {"type": "bool"},
        },
        "rules": [
            {
                "kind": "standard",
                "output": "result",
                "when": "true",
                "value": "true",
            }
        ],
    }


# =============================================================================
# Unit tests — emit_declarations
# =============================================================================

class TestEnumCaseCapitalization:
    def test_lowercase_enum_values_are_pascalized(self) -> None:
        doc = _doc_with_enum_field(["individual", "married", "dependent"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert "  -- Individual" in lines
        assert "  -- Married" in lines
        assert "  -- Dependent" in lines

    def test_lowercase_enum_values_not_emitted_verbatim(self) -> None:
        doc = _doc_with_enum_field(["individual"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert "  -- individual" not in lines

    def test_snake_case_enum_values_are_pascalized(self) -> None:
        doc = _doc_with_enum_field(["low_income", "medium_income", "high_income"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert "  -- LowIncome" in lines
        assert "  -- MediumIncome" in lines
        assert "  -- HighIncome" in lines

    def test_already_pascalcase_enum_values_unchanged(self) -> None:
        doc = _doc_with_enum_field(["Individual", "Married"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert "  -- Individual" in lines
        assert "  -- Married" in lines

    def test_enum_name_itself_is_pascalized(self) -> None:
        doc = _doc_with_enum_field(["individual"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert any("declaration enumeration FilingStatus:" in line for line in lines)

    def test_upper_snake_case_enum_values_unchanged_in_declaration(self) -> None:
        """UPPER_SNAKE values like QMB, SLMB_PLUS must not be mangled to Qmb, SlmbPlus."""
        doc = _doc_with_enum_field(["QMB", "SLMB", "SLMB_PLUS", "QDWI"])
        lines = emit_declarations(doc, "ProgramStandardsDecision")
        assert "  -- QMB" in lines
        assert "  -- SLMB" in lines
        assert "  -- SLMB_PLUS" in lines
        assert "  -- QDWI" in lines
        assert "  -- Qmb" not in lines
        assert "  -- SlmbPlus" not in lines


class TestStringFieldTableValueCapitalization:
    """type:string fields with table-derived enum values (Fix #26 — table_vals path)."""

    def _doc_with_string_table_field(self, table_values: list[str]) -> dict:
        rows = [{**{"household_type": v}, "benefit_amount": 100} for v in table_values]
        return {
            "module": "test_module.standards",
            "description": "Test",
            "version": "1.0",
            "effective": {"start": "2024-01-01"},
            "jurisdiction": {"level": "federal", "country": "US"},
            "inputs": {
                "Household": {
                    "fields": {
                        "household_type": {"type": "string"},
                    }
                }
            },
            "tables": {
                "limits": {
                    "key": ["household_type"],
                    "value": ["benefit_amount"],
                    "rows": rows,
                }
            },
            "outputs": {"result": {"type": "bool"}},
            "rules": [{"kind": "standard", "output": "result", "when": "true", "value": "true"}],
        }

    def test_lowercase_table_values_pascalized_in_declaration(self) -> None:
        doc = self._doc_with_string_table_field(["individual", "couple"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- Individual" in lines
        assert "  -- Couple" in lines

    def test_lowercase_table_values_not_emitted_verbatim(self) -> None:
        doc = self._doc_with_string_table_field(["individual"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- individual" not in lines

    def test_uppercase_initial_table_values_unchanged(self) -> None:
        """Codes like 'A1E' already start uppercase — must not be mangled to 'A1e'."""
        doc = self._doc_with_string_table_field(["A1E", "B1E"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- A1E" in lines
        assert "  -- B1E" in lines
        assert "  -- A1e" not in lines

    def test_snake_case_table_values_pascalized(self) -> None:
        doc = self._doc_with_string_table_field(["low_income", "high_income"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- LowIncome" in lines
        assert "  -- HighIncome" in lines


class TestFormatKeyConditionCapitalization:
    """_format_key_condition must emit PascalCase enum constructors for string values."""

    def test_lowercase_string_is_pascalized(self) -> None:
        assert _format_key_condition("household_type", "individual") == "household_type with pattern Individual"

    def test_snake_case_string_is_pascalized(self) -> None:
        assert _format_key_condition("household_type", "low_income") == "household_type with pattern LowIncome"

    def test_uppercase_initial_code_unchanged(self) -> None:
        """Codes like 'A1E' must not be mangled (snake_to_pascal('A1E') = 'A1e')."""
        assert _format_key_condition("household_type", "A1E") == "household_type with pattern A1E"

    def test_already_pascalcase_unchanged(self) -> None:
        assert _format_key_condition("status", "Individual") == "status with pattern Individual"


class TestBuildFieldTypeMapEnumCapitalization:
    """build_field_type_map must return PascalCase emit forms for enum variants."""

    def test_enum_type_values_are_pascalized(self) -> None:
        civil_doc = {
            "inputs": {"Household": {"fields": {
                "filing_status": {"type": "enum", "values": ["individual", "married", "dependent"]}
            }}}
        }
        _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
        assert enum_variants["filing_status"]["individual"] == "Individual"
        assert enum_variants["filing_status"]["married"] == "Married"
        assert enum_variants["filing_status"]["dependent"] == "Dependent"

    def test_enum_type_snake_case_values_are_pascalized(self) -> None:
        civil_doc = {
            "inputs": {"Household": {"fields": {
                "income_tier": {"type": "enum", "values": ["low_income", "high_income"]}
            }}}
        }
        _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
        assert enum_variants["income_tier"]["low_income"] == "LowIncome"
        assert enum_variants["income_tier"]["high_income"] == "HighIncome"

    def test_string_type_table_values_are_pascalized(self) -> None:
        civil_doc = {
            "inputs": {"Household": {"fields": {"household_type": {"type": "string"}}}},
            "tables": {"limits": {
                "key": ["household_type"],
                "value": ["amount"],
                "rows": [{"household_type": "individual", "amount": 100},
                         {"household_type": "couple", "amount": 200}],
            }},
        }
        _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
        assert enum_variants["household_type"]["individual"] == "Individual"
        assert enum_variants["household_type"]["couple"] == "Couple"

    def test_string_type_table_uppercase_codes_unchanged(self) -> None:
        """Uppercase-initial codes like 'A1E' must not be mangled."""
        civil_doc = {
            "inputs": {"Household": {"fields": {"household_type": {"type": "string"}}}},
            "tables": {"limits": {
                "key": ["household_type"],
                "value": ["amount"],
                "rows": [{"household_type": "A1E", "amount": 100},
                         {"household_type": "B1E", "amount": 200}],
            }},
        }
        _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
        assert enum_variants["household_type"]["A1E"] == "A1E"
        assert enum_variants["household_type"]["B1E"] == "B1E"

    def test_enum_type_upper_snake_values_unchanged(self) -> None:
        """UPPER_SNAKE_CASE enum values must be preserved, not mangled to PascalCase."""
        civil_doc = {
            "inputs": {"Household": {"fields": {
                "savings_category": {"type": "enum", "values": ["QMB", "SLMB", "SLMB_PLUS", "QDWI"]}
            }}}
        }
        _, _, enum_variants, _, _, _ = build_field_type_map(civil_doc)
        assert enum_variants["savings_category"]["QMB"] == "QMB"
        assert enum_variants["savings_category"]["SLMB"] == "SLMB"
        assert enum_variants["savings_category"]["SLMB_PLUS"] == "SLMB_PLUS"
        assert enum_variants["savings_category"]["QDWI"] == "QDWI"


class TestOutputsDecisionsPathCapitalization:
    """type:string output fields with values: must emit PascalCase constructors (C-01)."""

    def _doc_with_output_string_values(self, values: list[str]) -> dict:
        return {
            "module": "test_module.standards",
            "description": "Test",
            "version": "1.0",
            "effective": {"start": "2024-01-01"},
            "jurisdiction": {"level": "federal", "country": "US"},
            "inputs": {
                "Household": {"fields": {"income": {"type": "money"}}}
            },
            "outputs": {
                "decision": {"type": "string", "values": values},
                "result": {"type": "bool"},
            },
            "rules": [{"kind": "standard", "output": "result", "when": "true", "value": "true"}],
        }

    def test_lowercase_output_values_pascalized(self) -> None:
        doc = self._doc_with_output_string_values(["approved", "denied"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- Approved" in lines
        assert "  -- Denied" in lines

    def test_uppercase_initial_output_values_unchanged(self) -> None:
        """Codes like 'A1E' in an output field must not be mangled to 'A1e'."""
        doc = self._doc_with_output_string_values(["A1E", "B1E"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- A1E" in lines
        assert "  -- B1E" in lines
        assert "  -- A1e" not in lines

    def test_snake_case_output_values_pascalized(self) -> None:
        doc = self._doc_with_output_string_values(["low_benefit", "high_benefit"])
        lines = emit_declarations(doc, "StandardsDecision")
        assert "  -- LowBenefit" in lines
        assert "  -- HighBenefit" in lines


# =============================================================================
# Integration test — transpile()
# =============================================================================

class TestIndividualEnumCaseEndToEnd:
    def test_individual_enum_case_transpiles_without_error(self) -> None:
        """Replicate program_standards.civil.yaml:45 — 'individual' must not cause failure."""
        doc = _doc_with_enum_field(["individual", "married", "dependent"])
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_output.catala_en")
            civil_path = os.path.join(tmpdir, "test.civil.yaml")
            open(civil_path, "w").close()
            transpile(doc, output_path, "ProgramStandardsDecision", civil_path=civil_path)
            with open(output_path) as catala_file:
                catala_text = catala_file.read()
        assert "-- Individual" in catala_text
        assert "-- individual" not in catala_text


class TestUpperSnakeCaseEnumWhenClause:
    """UPPER_SNAKE_CASE values in when: expressions must be preserved (Fix #27).

    Regression for QMB/SLMB-class enums: when: category == "SLMB_PLUS"
    was being transpiled to `savings_category = SlmbPlus` instead of
    `savings_category = SLMB_PLUS`.

    Uses deny rules because the transpiler processes deny rule when: expressions
    through translate_condition_to_catala (standard rules are not processed).
    """

    def _doc_with_upper_snake_deny_rule(self, when_value: str) -> dict:
        return {
            "module": "test_module.standards",
            "description": "Test",
            "version": "1.0",
            "effective": {"start": "2024-01-01"},
            "jurisdiction": {"level": "federal", "country": "US"},
            "inputs": {
                "Household": {
                    "fields": {
                        "savings_category": {
                            "type": "string",
                            "values": ["QMB", "SLMB", "SLMB_PLUS", "QDWI", "NONE"],
                        }
                    }
                }
            },
            "outputs": {
                "result": {"type": "bool"},
                "reasons": {"type": "list"},
            },
            "rules": [
                {
                    "kind": "deny",
                    "id": "rule-1",
                    "when": f'savings_category == "{when_value}"',
                    "then": [{"add_reason": {"code": "INELIGIBLE"}}],
                }
            ],
        }

    def test_upper_snake_when_clause_preserved(self) -> None:
        """'SLMB_PLUS' in a when: clause must not be mangled to SlmbPlus."""
        doc = self._doc_with_upper_snake_deny_rule("SLMB_PLUS")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "out.catala_en")
            civil_path = os.path.join(tmpdir, "test.civil.yaml")
            open(civil_path, "w").close()
            transpile(doc, output_path, "StandardsDecision", civil_path=civil_path)
            catala_text = open(output_path).read()
        assert "SLMB_PLUS" in catala_text
        assert "SlmbPlus" not in catala_text

    def test_all_caps_when_clause_preserved(self) -> None:
        """'QMB' (all-caps, no underscores) in a when: clause must not become 'Qmb'."""
        doc = self._doc_with_upper_snake_deny_rule("QMB")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "out.catala_en")
            civil_path = os.path.join(tmpdir, "test.civil.yaml")
            open(civil_path, "w").close()
            transpile(doc, output_path, "StandardsDecision", civil_path=civil_path)
            catala_text = open(output_path).read()
        assert "QMB" in catala_text
        assert "Qmb" not in catala_text


class TestSubstituteRowStringEnumPath:
    """_substitute_row_into_expr must PascalCase string/enum consequence values (Fix #26).

    The path is: a computed field of type:string reads its value from a table
    value column. The consequence emitted per-row must be a valid Catala
    constructor (uppercase-initial).
    """

    def _doc_with_string_computed_from_table(self, row_values: list[str]) -> dict:
        """CIVIL doc where a computed field of type:string derives its value from a table."""
        rows = [{"household_type": f"hh_{idx}", "category_code": v}
                for idx, v in enumerate(row_values)]
        return {
            "module": "test_module.standards",
            "description": "Test",
            "version": "1.0",
            "effective": {"start": "2024-01-01"},
            "jurisdiction": {"level": "federal", "country": "US"},
            "inputs": {
                "Household": {
                    "fields": {
                        "household_type": {"type": "string"},
                    }
                }
            },
            "tables": {
                "categories": {
                    "key": ["household_type"],
                    "value": ["category_code"],
                    "rows": rows,
                }
            },
            "computed": {
                "resolved_category": {
                    "type": "string",
                    "expr": "table('categories', household_type).category_code",
                }
            },
            "outputs": {"result": {"type": "bool"}},
            "rules": [{"kind": "standard", "output": "result", "when": "true", "value": "true"}],
        }

    def test_lowercase_table_value_column_pascalized_in_rule(self) -> None:
        """When a table value column holds an enum variant, the consequence is PascalCased."""
        doc = self._doc_with_string_computed_from_table(["individual", "couple"])
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "out.catala_en")
            civil_path = os.path.join(tmpdir, "test.civil.yaml")
            open(civil_path, "w").close()
            transpile(doc, output_path, "StandardsDecision", civil_path=civil_path)
            catala_text = open(output_path).read()
        assert "Individual" in catala_text or "Couple" in catala_text
        assert " individual\n" not in catala_text
        assert " couple\n" not in catala_text

    def test_uppercase_initial_table_value_column_unchanged(self) -> None:
        """Uppercase-initial codes in a table value column must not be mangled."""
        doc = self._doc_with_string_computed_from_table(["A1E", "B1E"])
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "out.catala_en")
            civil_path = os.path.join(tmpdir, "test.civil.yaml")
            open(civil_path, "w").close()
            transpile(doc, output_path, "StandardsDecision", civil_path=civil_path)
            catala_text = open(output_path).read()
        assert "A1E" in catala_text
        assert "B1E" in catala_text
        assert "A1e" not in catala_text
