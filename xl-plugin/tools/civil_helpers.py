"""
Shared CIVIL spec helpers for CSV-based test case authoring tools.

Provides FieldSpec and build_csv_field_specs() — the single implementation
of CIVIL-to-CSV column mapping used by export_test_template, export_test_cases,
and import_tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

RESERVED_COLUMNS = frozenset({"case_id", "description", "tags", "notes"})


@dataclass
class FieldSpec:
    """Describes one CSV column derived from a CIVIL spec field."""

    column_name: str          # e.g. "ClientData.gross_earned_income" or bare "household_size"
    civil_type: str           # "money", "bool", "int", "float", "string", "enum", "list", "date"
    optional: bool
    enum_values: Optional[list[str]]   # only for enum-type fields
    item_type: Optional[str]           # only for list/set decisions (e.g. "Reason")
    description: Optional[str]
    is_decision: bool = False          # True for expected_<name> columns
    decision_name: Optional[str] = None  # original decision key (e.g. "eligible")


def _make_column_name(entity_name: str, field_name: str, multi_entity: bool,
                      used_names: set[str]) -> tuple[str, bool]:
    """Return (column_name, forced_prefix) for an input fact field.

    Forces EntityName. prefix if:
    - multi_entity is True, OR
    - bare field_name collides with a reserved column name
    """
    bare = field_name
    if bare in RESERVED_COLUMNS or bare.startswith("expected_"):
        # Collision with reserved name — force prefix and warn
        forced = f"{entity_name}.{field_name}"
        print(
            f"WARN: input fact field '{field_name}' collides with reserved column name "
            f"— using '{forced}' instead",
            file=sys.stderr,
        )
        return forced, True
    if multi_entity:
        return f"{entity_name}.{field_name}", False
    return bare, False


def build_csv_field_specs(civil_doc: dict) -> list[FieldSpec]:
    """Return ordered FieldSpec list: input facts fields then output decision fields.

    - Fact fields: entity declaration order, then field order within entity
    - Multi-entity modules: EntityName.field_name column names
    - Single-entity modules: bare field_name (unless reserved name collision)
    - computed: fields are excluded
    - Decision fields: named expected_<decision_name>, in decisions: declaration order
    """
    facts: dict = civil_doc.get("inputs", {})
    decisions: dict = civil_doc.get("outputs", {})

    multi_entity = len(facts) > 1
    specs: list[FieldSpec] = []
    used_names: set[str] = set()

    # --- Fact fields ---
    for entity_name, entity_def in facts.items():
        fields_def = entity_def.get("fields", {}) if isinstance(entity_def, dict) else {}
        for field_name, field_def in fields_def.items():
            if not isinstance(field_def, dict):
                continue
            civil_type = field_def.get("type", "string")
            # Normalise: "enum" when values: present and type is "string"
            enum_values = None
            if civil_type == "enum" or (civil_type == "string" and field_def.get("values")):
                civil_type = "enum"
                enum_values = [str(v) for v in (field_def.get("values") or [])]
            optional = bool(field_def.get("optional", False))
            description = field_def.get("description")

            col_name, _ = _make_column_name(entity_name, field_name, multi_entity, used_names)
            used_names.add(col_name)

            specs.append(FieldSpec(
                column_name=col_name,
                civil_type=civil_type,
                optional=optional,
                enum_values=enum_values,
                item_type=None,
                description=description,
                is_decision=False,
                decision_name=None,
            ))

    # --- Decision fields ---
    for dec_name, dec_def in decisions.items():
        if not isinstance(dec_def, dict):
            continue
        civil_type = dec_def.get("type", "string")
        enum_values = None
        if dec_def.get("values"):
            civil_type = "enum"
            enum_values = [str(v) for v in dec_def["values"]]
        item_type = dec_def.get("item")  # for list/set

        col_name = f"expected_{dec_name}"
        specs.append(FieldSpec(
            column_name=col_name,
            civil_type=civil_type,
            optional=(civil_type in ("list", "set")),  # list/set decisions optional (empty==[])
            enum_values=enum_values,
            item_type=item_type,
            description=dec_def.get("description"),
            is_decision=True,
            decision_name=dec_name,
        ))

    return specs


# ---------------------------------------------------------------------------
# Description helpers used by template generation
# ---------------------------------------------------------------------------

def field_description_hint(spec: FieldSpec) -> str:
    """Return a human-readable hint string for the CSV descriptions row."""
    parts: list[str] = []

    if spec.civil_type == "money":
        parts.append("Money (e.g. $1,500 or 1500)")
    elif spec.civil_type == "bool":
        parts.append("Boolean (true/false)")
    elif spec.civil_type == "int":
        parts.append("Integer")
    elif spec.civil_type == "float":
        parts.append("Decimal number")
    elif spec.civil_type == "date":
        parts.append("Date (YYYY-MM-DD)")
    elif spec.civil_type == "enum" and spec.enum_values:
        opts = ", ".join(spec.enum_values)
        parts.append(f"One of: {opts}")
    elif spec.civil_type in ("list", "set"):
        parts.append("Semicolon-separated values (e.g. CODE_A;CODE_B) or empty for []")
    else:
        parts.append("Text")

    if spec.optional:
        parts.append("Optional (leave blank to omit)")
    else:
        parts.append("Required")

    if spec.description:
        parts.append(spec.description)

    return ". ".join(parts)


# ---------------------------------------------------------------------------
# YAML file loading helper
# ---------------------------------------------------------------------------

def load_civil_yaml(path: Path) -> dict:
    """Load a CIVIL YAML file; exit 1 with a clear error on failure."""
    if not path.exists():
        print(f"ERROR: CIVIL spec file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"ERROR: YAML parse error in {path}: {e}", file=sys.stderr)
        sys.exit(1)
