#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
CIVIL YAML Tests → Catala Test File Transpiler

Converts a YAML test file (<domain>/specs/tests/<module>_*_tests.yaml)
to a Catala test file (<domain>/output/tests/<module>_*_tests.catala_en)
using the Catala #[test] assertion pattern.

Each YAML test case becomes a #[test] scope that calls the main scope via
  output of <Scope> with { -- field: value ... }
and asserts on the output fields.

Usage (via xlator CLI):
    xlator catala-test-transpile <domain> <module>

Example:
    xlator catala-test-transpile snap eligibility

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

import os
import pathlib
import re
import sys
import argparse
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from transpile_to_catala import build_cross_module_enums, _to_catala_constructor  # noqa: E402


# =============================================================================
# UTILITIES (copied from transpile_to_catala.py)
# =============================================================================

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_yaml_file(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        fail(f"File not found: {path}")
    except yaml.YAMLError as e:
        fail(f"YAML parse error in {path}: {e}")


def snake_to_pascal(name: str) -> str:
    """Convert snake_case or kebab-case to PascalCase."""
    return "".join(word.capitalize() for word in re.split(r"[_-]", name) if word)


def entity_to_var_name(entity_name: str) -> str:
    """Convert PascalCase entity name to Catala snake_case variable name.

    'ClientIncome' → 'client_income', 'DOLIncome' → 'd_o_l_income'
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", entity_name).lower()


def reason_code_to_pascal(code: str) -> str:
    """Convert UPPER_SNAKE_CASE reason code to PascalCase variant name.

    'GROSS_INCOME_EXCEEDS_LIMIT' → 'GrossIncomeExceedsLimit'
    """
    return "".join(word.capitalize() for word in code.split("_"))


def money_literal(value) -> str:
    """Format a number as a Catala money literal.

    1696 → '$1,696', 1707.50 → '$1,707.50', -500.25 → '-$500.25'
    Preserves cents when the fractional part is non-zero.
    """
    try:
        float_val = float(value)
        frac = float_val % 1
        negative = float_val < 0
        abs_val = abs(float_val)
        if frac != 0:
            # Format with two decimal places, comma-separate the integer part
            int_part = int(abs_val)
            cents = round(abs_val - int_part, 2)
            cents_str = f"{cents:.2f}"[1:]  # ".50"
            formatted = f"${int_part:,}{cents_str}"
        else:
            formatted = f"${int(abs_val):,}"
        return f"-{formatted}" if negative else formatted
    except (TypeError, ValueError):
        return None  # caller handles non-numeric values


# =============================================================================
# FIELD TYPE HELPERS
# =============================================================================

def build_field_type_map(civil_doc: dict, sub_module_docs: dict = None) -> dict:
    """Build field type/optional/enum maps and an ordered entity→fields map.

    sub_module_docs: {module_name: civil_doc} for sibling modules (mirrors main
    transpiler's parameter). Used to resolve cross-module string enum fields —
    those declared `type: string` locally but keyed in a sub-module's tables:.

    Returns:
        types: {field_name: civil_type}
        optional_flags: {field_name: bool}
        enum_variants: {field_name: {raw_value: emit_form}} — raw_value is the
            value as-it-appears in CIVIL inputs; emit_form is the Catala enum
            variant identifier to emit. Table-derived variants emit raw;
            values:-declared variants are PascalCased to match the enum
            declaration emitted by transpile_to_catala.emit_declarations.
            Cross-module variants emit as ModulePrefix.VariantName.
        entity_fields: {entity_name: [(field_name, civil_type, is_optional)]}
        computed_field_types: {field_name: civil_type} for computed: fields
        table_key_defaults: {field_name: first_row_raw_value} — first-seen value
            for every table key column, regardless of type. Used as a default for
            optional or missing fields so the emitted test picks a value that
            matches at least one rule (first-row-wins).
    """
    types = {}
    optional_flags = {}
    enum_variants = {}
    entity_fields = {}
    tables = civil_doc.get("tables", {})
    sub_module_docs = sub_module_docs or {}
    cross_module_enums = build_cross_module_enums(sub_module_docs)
    for entity_name, entity_def in civil_doc.get("inputs", {}).items():
        fields = []
        for field_name, field_def in entity_def.get("fields", {}).items():
            civil_type = field_def.get("type", "int")
            is_optional = bool(field_def.get("optional", False))
            # For list/set fields, preserve the item type so defaults and value
            # serialisation can emit `[]` and `[ $456; $398; $430 ]` respectively.
            if civil_type in ("list", "set"):
                item_type = field_def.get("item", "money")
                civil_type = f"list:{item_type}"
            types[field_name] = civil_type
            optional_flags[field_name] = is_optional
            if civil_type == "enum" and "values" in field_def:
                enum_variants[field_name] = {str(v): _to_catala_constructor(str(v)) for v in field_def["values"]}
            elif civil_type == "string":
                # Collect enum variants from table key columns for string fields.
                # Table-derived: emit as Catala constructor (uppercase-initial; matches
                # the `-- _to_catala_constructor(v)` declaration in emit_declarations).
                table_variants: list = []
                for table_def in tables.values():
                    if field_name in table_def.get("key", []):
                        for row in table_def.get("rows", []):
                            val = row.get(field_name)
                            if val is not None and isinstance(val, str) and val not in table_variants:
                                table_variants.append(val)
                if table_variants:
                    enum_variants[field_name] = {v: _to_catala_constructor(v) for v in table_variants}
                elif "values" in field_def:
                    # Declared values: PascalCase emit form to match the enum declaration.
                    enum_variants[field_name] = {
                        str(v): _to_catala_constructor(str(v)) for v in field_def.get("values", [])
                    }
                elif field_name in cross_module_enums:
                    # Enum declared in a sub-module via a table key column.
                    # Catala resolves enum constructors by name alone (no module prefix needed
                    # in struct literals), so emit bare variant names (e.g. A1E, not
                    # Program_standards_lookup.A1E). Apply _to_catala_constructor so
                    # lowercase sub-module values are PascalCased consistently.
                    _, variants = cross_module_enums[field_name]
                    enum_variants[field_name] = {v: _to_catala_constructor(v) for v in variants}
            fields.append((field_name, civil_type, is_optional))
        entity_fields[entity_name] = fields
    # Output decision fields: string-with-values: declarations also map to Catala
    # enums; their {raw → emit} mapping is needed when emitting test assertions.
    for field_name, field_def in civil_doc.get("outputs", {}).items():
        if field_def.get("type") == "string" and field_def.get("values"):
            enum_variants[field_name] = {
                str(v): _to_catala_constructor(str(v)) for v in field_def["values"]
            }
    # Only include computed fields tagged [expose] — these become scope outputs.
    # Internal computed fields are inaccessible outside the scope and cannot be asserted.
    computed_field_types = {
        fname: fdef.get("type")
        for fname, fdef in civil_doc.get("computed", {}).items()
        if isinstance(fdef, dict) and "type" in fdef
        and "expose" in (fdef.get("tags") or [])
    }
    # Pick a default for every table key column that satisfies every table sharing
    # that key. Picking the first-seen first-row value can produce a default that
    # is absent from another table's coverage, making outputs derived from that
    # other table unsatisfiable in every test scope.
    all_tables_to_scan = list(tables.values()) + [
        sub_table
        for sub_doc in sub_module_docs.values()
        for sub_table in sub_doc.get("tables", {}).values()
    ]
    per_key_value_sets: dict = {}
    for table_def in all_tables_to_scan:
        table_rows = table_def.get("rows", [])
        if not table_rows:
            continue
        for key_col in table_def.get("key", []):
            values = {row[key_col] for row in table_rows if key_col in row}
            if values:
                per_key_value_sets.setdefault(key_col, []).append(values)
    table_key_defaults = {}
    for key_col, value_sets in per_key_value_sets.items():
        common = set.intersection(*value_sets) if value_sets else set()
        if common:
            table_key_defaults[key_col] = pick_representative(common)
        else:
            table_key_defaults[key_col] = pick_representative(max(value_sets, key=len))
            print(
                f"  WARN  key '{key_col}' has no value common to all tables that use it; "
                f"tests that exercise multiple of those tables may fail.",
                file=sys.stderr,
            )
    return types, optional_flags, enum_variants, entity_fields, computed_field_types, table_key_defaults


def pick_representative(values: set):
    """Pick a deterministic representative element from a set of table-key values.

    Numeric (int/float) sets return the maximum — for year keys this picks the
    most-recent year, which is the value test authors typically intend when an
    optional field is omitted. Other sets fall back to lexicographic minimum.
    """
    if not values:
        raise ValueError("pick_representative requires a non-empty set")
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return max(values)
    return min(values, key=str)


def default_value_for_type(civil_type: str) -> str:
    """Return the Catala zero/default literal for an optional field not in test inputs."""
    if civil_type == "money":
        return "$0"
    elif civil_type == "bool":
        return "false"
    elif civil_type == "int":
        return "0"
    elif civil_type == "float":
        return "0.0"
    elif civil_type == "date":
        return "|2020-01-01|"
    elif civil_type.startswith("list:") or civil_type.startswith("set:"):
        return "[]"
    else:
        return "0"


def value_to_catala(value, civil_type: str, valid_enum_variants=None):
    """Convert a YAML test input value to a Catala literal.

    valid_enum_variants is a {raw_value: emit_form} dict (or None). When
    provided, the raw input must match a key — the emitted enum variant
    identifier comes from the value side of the map.

    Returns None if the value cannot be represented (e.g. non-numeric money,
    non-numeric int, or unrecognised enum variant).
    """
    if civil_type == "money":
        return money_literal(value)  # may return None for non-numeric values
    elif civil_type == "bool":
        return "true" if value else "false"
    elif civil_type == "enum":
        str_val = str(value)
        if valid_enum_variants is not None and str_val not in valid_enum_variants:
            return None  # invalid variant — caller will default and warn
        return valid_enum_variants[str_val] if valid_enum_variants else str_val
    elif civil_type == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return None  # non-numeric — caller will default and warn
    elif civil_type == "float":
        # Catala decimal literals require a decimal point; bare `20` is parsed as
        # integer and triggers a type mismatch against a `decimal` declaration.
        try:
            return repr(float(value))
        except (TypeError, ValueError):
            return None  # non-numeric — caller will default and warn
    elif civil_type == "string" and valid_enum_variants:
        # String field with declared/derived enum variants — must map to a
        # Catala enum constructor, never a free-form identifier.
        str_val = str(value)
        if str_val not in valid_enum_variants:
            return None  # invalid variant — caller will default and warn
        return valid_enum_variants[str_val]
    elif civil_type.startswith("list:") or civil_type.startswith("set:"):
        # Catala list literal: `[ elem; elem; elem ]` for non-empty, `[]` for empty.
        # Element type is the suffix after `list:` (e.g. `list:money` → money).
        if not isinstance(value, list):
            return None  # non-list value supplied for a list field
        if not value:
            return "[]"
        elem_type = civil_type.split(":", 1)[1]
        elem_literals = []
        for element in value:
            elem_lit = value_to_catala(element, elem_type)
            if elem_lit is None:
                return None
            elem_literals.append(elem_lit)
        return "[ " + "; ".join(elem_literals) + " ]"
    elif civil_type == "date":
        # Catala date literal: |YYYY-MM-DD|
        return f"|{value}|"
    else:
        return str(value)


# =============================================================================
# SCOPE NAME HELPERS
# =============================================================================

def case_id_to_scope_name(case_id: str) -> str:
    """Convert case_id to test scope name: allow_001 → TestAllow001"""
    return "Test" + snake_to_pascal(case_id)


# =============================================================================
# EMITTERS
# =============================================================================

def emit_field_value(
    case_id: str,
    field_name: str,
    civil_type: str,
    is_optional: bool,
    input_val,
    enum_variants: dict,
    note_prefix: str = "",
    table_key_defaults: dict = None,
) -> tuple:
    """Resolve a field's Catala value and an optional warning note.

    Returns (catala_val: str, note: str | None).
    note is non-None when the value was defaulted or has a representability issue.
    """
    # string fields that map to Catala enums (table-derived) need variant lookup too
    valid_variants = enum_variants.get(field_name) if civil_type in ("enum", "string") else None

    if civil_type == "string" and not valid_variants:
        if is_optional:
            # Optional string with no enum variants — main transpiler omits this field
            # from the Catala scope declaration (ticket 11 rule). Match that here.
            return None, None
        # Required string with no local or cross-module enum variants. The main transpiler
        # raises ValueError at transpile time for this case, so it should never reach the
        # test transpiler. If it does, sub-module docs were not loaded — fail loudly.
        raise ValueError(
            f"Test transpiler cannot emit field '{field_name}' (required string, no variants "
            f"found). Sub-module CIVIL docs may not have been loaded — verify that the "
            f"entrypoint passes sub_module_docs to build_field_type_map()."
        )

    effective_type = civil_type

    if input_val is not None:
        catala_val = value_to_catala(input_val, effective_type, valid_variants)
        if catala_val is None:
            default = next(iter(valid_variants.values())) if valid_variants else default_value_for_type(effective_type)
            print(
                f"  WARN  case '{case_id}': field '{note_prefix}{field_name}' has non-representable "
                f"value '{input_val}'; defaulting to {default}",
                file=sys.stderr,
            )
            return default, "non-representable input value; defaulted"
        return catala_val, None
    elif is_optional:
        if valid_variants:
            default = next(iter(valid_variants.values()))
        elif table_key_defaults and field_name in table_key_defaults:
            default = (
                value_to_catala(table_key_defaults[field_name], effective_type, None)
                or default_value_for_type(effective_type)
            )
        else:
            default = default_value_for_type(effective_type)
        return default, None
    else:
        if valid_variants:
            default = next(iter(valid_variants.values()))
        elif table_key_defaults and field_name in table_key_defaults:
            default = (
                value_to_catala(table_key_defaults[field_name], effective_type, None)
                or default_value_for_type(effective_type)
            )
        else:
            default = default_value_for_type(effective_type)
        print(
            f"  WARN  case '{case_id}': required field '{note_prefix}{field_name}' not in inputs; "
            f"defaulting to {default}",
            file=sys.stderr,
        )
        return default, "required field defaulted (not in test inputs)"


def emit_test_scope(
    case: dict,
    scope_name: str,
    all_fields: list,
    field_types: dict,
    optional_flags: dict,
    bool_decision_fields: list,
    denial_field: str,
    enum_variants: dict = None,
    entity_fields: dict = None,
    catala_module_name: str = "",
    string_decision_fields: list = None,
    numeric_decision_fields: list = None,
    computed_field_types: dict = None,
    invoke_bound_entities: set = None,
    table_key_defaults: dict = None,
) -> list:
    """Emit Catala lines for one #[test] scope.

    all_fields: ordered list of (field_name, civil_type, is_optional) for scope inputs.
      Used only in single-entity mode (one input facts entity).
    entity_fields: {entity_name: [(field_name, civil_type, is_optional)]}.
      When more than one entity is present, struct-literal input emission is used instead
      of flat field assignment (multi-entity mode).
    bool_decision_fields: list of decision field names with type 'bool' to assert.
    string_decision_fields: list of decision field names with type 'string' + values: to assert.
    numeric_decision_fields: list of (field_name, civil_type) for money/int/float decisions.
    computed_field_types: {field_name: civil_type} for computed: fields — used to assert any
      remaining expected: keys not covered by decisions.
    catala_module_name: e.g. 'Eligibility' — used to qualify struct type names in
      multi-entity mode (e.g. Eligibility.ClientIncome).
    """
    if enum_variants is None:
        enum_variants = {}
    if entity_fields is None:
        entity_fields = {}
    case_id = case.get("case_id", "unknown")
    test_scope = case_id_to_scope_name(case_id)
    inputs = case.get("inputs", {})
    expected = case.get("expected", {})

    known_input_names = {field_name for field_name, _, _ in all_fields}
    for entity_name, fields in (entity_fields or {}).items():
        for field_name, _, _ in fields:
            known_input_names.add(field_name)
            known_input_names.add(f"{entity_name}.{field_name}")
    for input_key in inputs:
        if input_key not in known_input_names:
            print(
                f"  WARN  case '{case_id}': input field '{input_key}' is not declared "
                f"in CIVIL inputs — value will be ignored. Known inputs: {sorted(known_input_names)}",
                file=sys.stderr,
            )

    lines = []

    # --- Declaration ---
    # Use sub-scope syntax: `result scope Module.ScopeName`
    # NOT `output result content Module.ScopeName` — scopes are not types in Catala 1.1.0,
    # and the `output of Module.Scope with { ... }` call form fails cross-module at runtime
    # (it cannot resolve the compiled .cmxs export).  Sub-scope input assignment works correctly.
    lines.append("#[test]")
    lines.append(f"declaration scope {test_scope}:")
    lines.append(f"  result scope {scope_name}")
    lines.append("")

    # --- Scope body ---
    lines.append(f"scope {test_scope}:")

    # Multi-entity (struct-literal) mode applies only to root modules that pass
    # entity structs to sub-scopes. Sub-modules with multiple entities but no
    # invoke-bind have their fields flattened by the main transpiler, so tests
    # must use flat field assignment (PLUGIN_IMPROVEMENTS.md #8a).
    multi_entity = bool(invoke_bound_entities)

    if multi_entity:
        # Multi-entity mode: inputs are keyed as 'EntityName.field_name' in the YAML.
        # Emit one struct literal per entity:
        #   definition result.var_name equals Module.EntityName { -- field: value ... }
        for entity_name, fields in entity_fields.items():
            var_name = entity_to_var_name(entity_name)
            type_ref = f"{catala_module_name}.{entity_name}" if catala_module_name else entity_name
            struct_lines = []
            for field_name, civil_type, is_optional in fields:
                prefixed_key = f"{entity_name}.{field_name}"
                # Prefer entity-qualified key; fall back to bare field name for tests
                # that use flat (non-prefixed) input keys.
                if prefixed_key in inputs:
                    input_val = inputs[prefixed_key]
                elif field_name in inputs:
                    input_val = inputs[field_name]
                else:
                    input_val = None
                catala_val, note = emit_field_value(
                    case_id, field_name, civil_type, is_optional,
                    input_val, enum_variants, note_prefix=f"{entity_name}.",
                    table_key_defaults=table_key_defaults,
                )
                if catala_val is None:
                    continue
                suffix = f"  # NOTE: {note}" if note else ""
                struct_lines.append(f"    -- {field_name}: {catala_val}{suffix}")
            lines.append(f"  definition result.{var_name} equals {type_ref} {{")
            lines.extend(struct_lines)
            lines.append("  }")
    else:
        # Single-entity mode: inputs are keyed by bare field_name. Emit flat assignments.
        for field_name, civil_type, is_optional in all_fields:
            raw_val = inputs.get(field_name)
            input_val = raw_val if field_name in inputs else None
            catala_val, note = emit_field_value(
                case_id, field_name, civil_type, is_optional,
                input_val, enum_variants,
                table_key_defaults=table_key_defaults,
            )
            if catala_val is None:
                continue
            suffix = f"  # NOTE: {note}" if note else ""
            lines.append(f"  definition result.{field_name} equals {catala_val}{suffix}")

    # --- Assertions ---
    # Bool decisions (e.g. manual_verification_required)
    for field in bool_decision_fields:
        val = expected.get(field)
        if val is not None:
            catala_bool = "true" if val else "false"
            lines.append(f"  assertion (result.{field} = {catala_bool})")

    # String-enum decisions (e.g. eligible: "deny" → Deny)
    for field in (string_decision_fields or []):
        val = expected.get(field)
        if val is None:
            continue
        valid_variants = (enum_variants or {}).get(field)
        str_val = str(val)
        if valid_variants is not None and str_val not in valid_variants:
            print(
                f"  WARN  case '{case_id}': expected.{field} value '{str_val}' is not in "
                f"declared values {list(valid_variants)} — skipping assertion",
                file=sys.stderr,
            )
            continue
        catala_variant = (
            valid_variants[str_val] if valid_variants else snake_to_pascal(str_val)
        )
        lines.append(f"  assertion (result.{field} = {catala_variant})")

    # Denial reasons list
    denial_reasons = expected.get(denial_field)
    if denial_reasons is not None:
        if len(denial_reasons) == 0:
            lines.append(f"  assertion (result.{denial_field} = [])")
        else:
            variants = [reason_code_to_pascal(r["code"]) for r in denial_reasons]
            list_str = "[ " + "; ".join(variants) + " ]"
            lines.append(f"  assertion (result.{denial_field} = {list_str})")

    # Numeric decisions (money, int, float)
    for field, civil_type in (numeric_decision_fields or []):
        val = expected.get(field)
        if val is not None:
            catala_val = value_to_catala(val, civil_type)
            if catala_val is not None:
                lines.append(f"  assertion (result.{field} = {catala_val})")

    # Computed: fields referenced in expected: — assert any not already covered by decisions
    handled = (
        set(bool_decision_fields)
        | set(string_decision_fields or [])
        | {f for f, _ in (numeric_decision_fields or [])}
        | {denial_field}
    )
    for field, val in expected.items():
        if field in handled or val is None:
            continue
        civil_type = (computed_field_types or {}).get(field)
        if civil_type is None:
            print(
                f"  WARN  case '{case_id}': expected: field '{field}' not found in "
                f"decisions or computed; skipping",
                file=sys.stderr,
            )
            continue
        catala_val = value_to_catala(val, civil_type)
        if catala_val is not None:
            lines.append(f"  assertion (result.{field} = {catala_val})")

    return lines


def catala_block(lines: list) -> list:
    """Wrap lines in a catala fenced code block."""
    return ["```catala"] + lines + ["```"]


# =============================================================================
# MAIN TRANSPILATION
# =============================================================================

def transpile(tests_path: str, output_path: str, scope_name: str, civil_spec_path: str):
    tests_doc = load_yaml_file(tests_path)
    civil_doc = load_yaml_file(civil_spec_path)

    # Load sibling sub-module docs for cross-module enum resolution (mirrors main transpiler).
    sub_module_docs: dict = {}
    for field_def in (civil_doc.get("computed") or {}).values():
        if isinstance(field_def, dict) and field_def.get("invoke") and field_def.get("module"):
            sub_name = field_def["module"]
            if sub_name not in sub_module_docs:
                sub_path = os.path.join(
                    os.path.dirname(os.path.abspath(civil_spec_path)),
                    f"{sub_name}.civil.yaml",
                )
                if os.path.exists(sub_path):
                    sub_module_docs[sub_name] = load_yaml_file(sub_path)

    field_types, optional_flags, enum_variants, entity_fields, computed_field_types, table_key_defaults = build_field_type_map(civil_doc, sub_module_docs)

    # Identify invoke-bound entities — these are the only ones the main transpiler
    # emits as Catala struct inputs. Sub-modules with multiple entities but no
    # invoke-bind have their fields flattened, so the tests must do flat field
    # assignments rather than struct-literal assignments (PLUGIN_IMPROVEMENTS.md #8a).
    invoke_bound_entities: set = set()
    for field_def in (civil_doc.get("computed") or {}).values():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            invoke_field = field_def["invoke"]
            bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
            invoke_bound_entities.update(bind.values())

    # Ordered list of (field_name, civil_type, is_optional) for the scope call (single-entity mode)
    all_fields = [
        (fname, ftype, optional_flags.get(fname, False))
        for fname, ftype in field_types.items()
    ]

    # All bool decision fields (e.g. manual_verification_required) — asserted in order
    bool_decision_fields = [
        fname for fname, fdef in civil_doc.get("outputs", {}).items()
        if fdef.get("type") == "bool"
    ]

    # String-enum decision fields (e.g. eligible with values: [approve, deny, ...]) — asserted in order
    string_decision_fields = [
        fname for fname, fdef in civil_doc.get("outputs", {}).items()
        if fdef.get("type") == "string" and fdef.get("values")
    ]

    # Numeric decision fields (money, int, float) — asserted in order
    numeric_decision_fields = [
        (fname, fdef.get("type"))
        for fname, fdef in civil_doc.get("outputs", {}).items()
        if fdef.get("type") in ("money", "int", "float")
    ]

    # Denial reasons field
    denial_field = "reasons"
    for fname, fdef in civil_doc.get("outputs", {}).items():
        if fdef.get("type") == "list":
            denial_field = fname
            break

    tests = tests_doc.get("tests", [])
    if not tests:
        # Intentionally-empty test files (e.g. auto-generated derived suites where
        # no derivable scenarios remain after deduplication) are valid — emit a
        # placeholder Catala file with no scopes and skip transpilation so the
        # pipeline can proceed to other test files.
        print(f"WARN  No tests in {tests_path}; emitting empty placeholder.", file=sys.stderr)
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        civil_basename_skip = os.path.basename(civil_spec_path)
        module_name_skip = civil_basename_skip.replace(".civil.yaml", "")
        catala_module_name_skip = module_name_skip[0].upper() + module_name_skip[1:]
        with open(output_path, "w") as f:
            f.write(f"> Using {catala_module_name_skip}\n\n# Tests: (empty)\n")
        print(f"OK  Wrote 0 test scope(s) → {output_path}")
        return

    # Detect duplicate scope names (case_id collision)
    seen: dict = {}
    for case in tests:
        cid = case.get("case_id", "")
        sname = case_id_to_scope_name(cid)
        if sname in seen:
            fail(
                f"Duplicate test scope name '{sname}' — case_ids '{seen[sname]}' and '{cid}' "
                f"produce the same scope name. Rename one case_id."
            )
        seen[sname] = cid

    civil_basename = os.path.basename(civil_spec_path)  # e.g. eligibility.civil.yaml
    module_name = civil_basename.replace(".civil.yaml", "")
    catala_module_name = module_name[0].upper() + module_name[1:]  # e.g. Eligibility, Earned_income
    qualified_scope_name = f"{catala_module_name}.{scope_name}"

    test_suite = tests_doc.get("test_suite", {})
    description = test_suite.get("description", os.path.basename(tests_path))

    # Build output
    md_lines = []

    md_lines.append(f"> Using {catala_module_name}")
    # Emit > Using for sub-modules that define enum types used in this module's structs.
    # Catala does not transitively expose enum constructors — each defining module must
    # be explicitly imported so bare constructor names (e.g. A1E) resolve correctly.
    cross_enums = build_cross_module_enums(sub_module_docs)
    cross_module_providers = sorted(set(
        qualified_type.split(".")[0]
        for qualified_type, _ in cross_enums.values()
    ))
    for provider in cross_module_providers:
        md_lines.append(f"> Using {provider}")
    md_lines.append("")
    md_lines.append(f"# Tests: {description}")
    md_lines.append("")

    for case in tests:
        case_id = case.get("case_id", "unknown")
        desc = case.get("description", case_id)

        md_lines.append(f"## Test: {case_id} — {desc}")
        md_lines.append("")

        scope_lines = emit_test_scope(
            case, qualified_scope_name, all_fields, field_types, optional_flags,
            bool_decision_fields, denial_field, enum_variants,
            entity_fields=entity_fields,
            catala_module_name=catala_module_name,
            string_decision_fields=string_decision_fields,
            numeric_decision_fields=numeric_decision_fields,
            computed_field_types=computed_field_types,
            invoke_bound_entities=invoke_bound_entities,
            table_key_defaults=table_key_defaults,
        )
        md_lines.extend(catala_block(scope_lines))
        md_lines.append("")

    # Write output
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"OK  Wrote {len(tests)} test scope(s) → {output_path}")


def main():
    parser = argparse.ArgumentParser(
        prog="transpile_to_catala_tests",
        description="Convert YAML test cases to Catala #[test] file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
example:
  xlator catala-test-transpile snap eligibility
""",
    )
    parser.add_argument("tests_yaml", help="Input YAML test file")
    parser.add_argument("output_catala", help="Output Catala file path")
    parser.add_argument(
        "--scope",
        required=True,
        help="Catala scope name to test (e.g. EligibilityDecision)",
    )
    parser.add_argument(
        "--civil-spec",
        required=True,
        dest="civil_spec",
        help="CIVIL YAML path — used to read input field types and decision field name",
    )

    args = parser.parse_args()

    basename = os.path.basename(args.tests_yaml)
    if basename.endswith("_null_input_expanded_tests.yaml"):
        print(f"SKIP  {args.tests_yaml} — null-input tests cannot be encoded in Catala; skipping.")
        sys.exit(0)

    transpile(args.tests_yaml, args.output_catala, args.scope, args.civil_spec)


if __name__ == "__main__":
    main()
