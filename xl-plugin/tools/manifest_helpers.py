# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
Manifest-driven test-CSV column helpers.

The type-extended `naming-manifest.yaml` is the authority for per-field
Catala primitive type, optionality, and enum-variant metadata. This module
provides the CSV-column derivation helpers consumed by:

  * export_test_template.py — CSV column derivation
  * export_test_cases.py    — CSV column derivation
  * import_tests.py         — CSV column derivation + type coercion

`FieldSpec` exposes: `column_name`, `leaf_type` (the internal leaf-type
vocabulary — `money|bool|int|float|string|enum|list|date`), `optional`,
`enum_values`, `item_type`, `description`, `is_decision`, `decision_name`.

Type-name normalization (Catala-native name → internal leaf type) — only
the 10-item Catala-native vocabulary is accepted (plan 2026-06-01-002):
  integer        → int
  decimal        → float
  boolean        → bool
  duration       → string
  structure      → string
  money/date/enum/list/string → identity-mapped to their internal leaf
Unknown values fall back to `_DEFAULT_LEAF_TYPE` ('string').
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


RESERVED_COLUMNS = frozenset({"case_id", "description", "tags", "notes"})


@dataclass
class FieldSpec:
    """Describes one CSV column derived from a manifest entry."""

    column_name: str
    leaf_type: str        # internal leaf type: money|bool|int|float|string|enum|list|date
    optional: bool
    enum_values: Optional[list[str]]
    item_type: Optional[str]
    description: Optional[str]
    is_decision: bool = False
    decision_name: Optional[str] = None


# Map Catala-native type names to the internal leaf type used by the rest
# of the test-CSV machinery. Only the 10-item Catala-native vocabulary is
# accepted; legacy CIVIL names were retired by plan 2026-06-01-002.
# `structure` maps to the internal `string` leaf — structure-typed entity
# fields don't surface as CSV columns directly, but the alias prevents the
# `_DEFAULT_LEAF_TYPE` warning path from firing on a valid Catala-native
# value.
_TYPE_ALIASES = {
    "integer": "int",
    "decimal": "float",
    "boolean": "bool",
    "duration": "string",
    "money": "money",
    "string": "string",
    "enum": "enum",
    "list": "list",
    "date": "date",
    "structure": "string",
}

_DEFAULT_LEAF_TYPE = "string"


def _normalize_type(raw_type) -> str:
    """Normalize a manifest `type:` value to the internal leaf-type name.

    Unknown strings (struct refs like `Household`) fall back to `string`.
    """
    if raw_type is None:
        return _DEFAULT_LEAF_TYPE
    return _TYPE_ALIASES.get(str(raw_type), _DEFAULT_LEAF_TYPE)


def _collect_enum_values(entry: dict) -> Optional[list[str]]:
    """Return enum-variant list for a manifest entry, or None when not enum.

    Priority: U7 `enum_variants:` (Catala-native), then legacy `values:`.
    """
    if not isinstance(entry, dict):
        return None
    ev = entry.get("enum_variants")
    if isinstance(ev, list) and ev:
        return [str(v) for v in ev]
    values = entry.get("values")
    if isinstance(values, list) and values:
        return [str(v) for v in values]
    return None


def load_naming_manifest(path: Path) -> dict:
    """Load `specs/naming-manifest.yaml`; exit 1 with a clear error on failure."""
    if not path.exists():
        print(f"ERROR: naming-manifest.yaml not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"ERROR: YAML parse error in {path}: {e}", file=sys.stderr)
        sys.exit(1)
    return doc if isinstance(doc, dict) else {}


def _make_column_name(entity_name: str, field_name: str, multi_entity: bool) -> str:
    """Return the CSV column name for an input fact field.

    - Multi-entity manifests use `EntityName.field_name`.
    - Single-entity manifests use the bare field_name.
    - Reserved column names (case_id/description/tags/notes) force the
      `Entity.field` prefix even in single-entity mode (with a warn).
    """
    if field_name in RESERVED_COLUMNS or field_name.startswith("expected_"):
        forced = f"{entity_name}.{field_name}"
        print(
            f"WARN: input fact field '{field_name}' collides with a reserved column name "
            f"— using '{forced}' instead",
            file=sys.stderr,
        )
        return forced
    if multi_entity:
        return f"{entity_name}.{field_name}"
    return field_name


def build_csv_field_specs(manifest_doc: dict) -> list[FieldSpec]:
    """Return ordered FieldSpec list from a `naming-manifest.yaml` doc.

    Order:
      1. Input fact fields — entity declaration order, then field order
         within each entity.
      2. Decision (output) fields — declaration order.

    `computed:` entries are excluded (matches pre-pivot behavior — they are
    not asserted from CSV-authored cases; the test-creation skills supply
    expected: values directly).

    Empty / malformed manifests return [].
    """
    inputs_block = manifest_doc.get("inputs") if isinstance(manifest_doc, dict) else {}
    outputs_block = manifest_doc.get("outputs") if isinstance(manifest_doc, dict) else {}

    if not isinstance(inputs_block, dict):
        inputs_block = {}
    if not isinstance(outputs_block, dict):
        outputs_block = {}

    multi_entity = len(inputs_block) > 1
    specs: list[FieldSpec] = []

    # --- Fact fields ---
    for entity_name, fields_map in inputs_block.items():
        if not isinstance(fields_map, dict):
            continue
        for field_name, entry in fields_map.items():
            if not isinstance(entry, dict):
                entry = {}
            raw_type = entry.get("type")
            leaf = _normalize_type(raw_type)
            enum_values = _collect_enum_values(entry)
            if enum_values:
                # Field has enum variants — treat as enum even if the type:
                # name was 'string' (legacy short name) or absent.
                leaf = "enum"
            if raw_type is None:
                print(
                    f"WARN: field '{entity_name}.{field_name}' has no `type:` in "
                    f"naming-manifest.yaml; defaulting CSV column to '{_DEFAULT_LEAF_TYPE}'",
                    file=sys.stderr,
                )
            optional = bool(entry.get("optional", False))
            description = entry.get("description") if isinstance(entry.get("description"), str) else None
            col_name = _make_column_name(entity_name, field_name, multi_entity)
            specs.append(FieldSpec(
                column_name=col_name,
                leaf_type=leaf,
                optional=optional,
                enum_values=enum_values,
                item_type=None,
                description=description,
                is_decision=False,
                decision_name=None,
            ))

    # --- Decision fields ---
    for dec_name, entry in outputs_block.items():
        if not isinstance(entry, dict):
            entry = {}
        raw_type = entry.get("type")
        leaf = _normalize_type(raw_type)
        enum_values = _collect_enum_values(entry)
        if enum_values:
            leaf = "enum"
        item_type = entry.get("item") if isinstance(entry.get("item"), str) else None
        description = entry.get("description") if isinstance(entry.get("description"), str) else None
        col_name = f"expected_{dec_name}"
        specs.append(FieldSpec(
            column_name=col_name,
            leaf_type=leaf,
            optional=(leaf in ("list", "set")),
            enum_values=enum_values,
            item_type=item_type,
            description=description,
            is_decision=True,
            decision_name=dec_name,
        ))

    return specs


def field_description_hint(spec: FieldSpec) -> str:
    """Return a human-readable hint string for the CSV descriptions row.

    Catala type names render with their canonical leaf name.
    """
    parts: list[str] = []
    if spec.leaf_type == "money":
        parts.append("Money (e.g. $1,500 or 1500)")
    elif spec.leaf_type == "bool":
        parts.append("Boolean (true/false)")
    elif spec.leaf_type == "int":
        parts.append("Integer")
    elif spec.leaf_type == "float":
        parts.append("Decimal number")
    elif spec.leaf_type == "date":
        parts.append("Date (YYYY-MM-DD)")
    elif spec.leaf_type == "enum" and spec.enum_values:
        opts = ", ".join(spec.enum_values)
        parts.append(f"One of: {opts}")
    elif spec.leaf_type in ("list", "set"):
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
