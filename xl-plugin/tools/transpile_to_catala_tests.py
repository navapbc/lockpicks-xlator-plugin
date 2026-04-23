#!/usr/bin/env python3
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
import re
import sys
import argparse
import yaml


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

def build_field_type_map(civil_doc: dict) -> dict:
    """Build field type/optional/enum maps and an ordered entity→fields map.

    Returns:
        types: {field_name: civil_type}
        optional_flags: {field_name: bool}
        enum_variants: {field_name: [str]}
        entity_fields: {entity_name: [(field_name, civil_type, is_optional)]}
        computed_field_types: {field_name: civil_type} for computed: fields
    """
    types = {}
    optional_flags = {}
    enum_variants = {}
    entity_fields = {}
    for entity_name, entity_def in civil_doc.get("inputs", {}).items():
        fields = []
        for field_name, field_def in entity_def.get("fields", {}).items():
            civil_type = field_def.get("type", "int")
            is_optional = bool(field_def.get("optional", False))
            types[field_name] = civil_type
            optional_flags[field_name] = is_optional
            if civil_type == "enum" and "values" in field_def:
                enum_variants[field_name] = [str(v) for v in field_def["values"]]
            fields.append((field_name, civil_type, is_optional))
        entity_fields[entity_name] = fields
    computed_field_types = {
        fname: fdef.get("type")
        for fname, fdef in civil_doc.get("computed", {}).items()
        if isinstance(fdef, dict) and "type" in fdef
    }
    return types, optional_flags, enum_variants, entity_fields, computed_field_types


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
    else:
        return "0"


def value_to_catala(value, civil_type: str, valid_enum_variants: list = None):
    """Convert a YAML test input value to a Catala literal.

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
        return str_val
    elif civil_type in ("int", "float"):
        try:
            int(value) if civil_type == "int" else float(value)
            return str(value)
        except (TypeError, ValueError):
            return None  # non-numeric string — caller will default and warn
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
) -> tuple:
    """Resolve a field's Catala value and an optional warning note.

    Returns (catala_val: str, note: str | None).
    note is non-None when the value was defaulted or has a representability issue.
    """
    valid_variants = enum_variants.get(field_name) if civil_type == "enum" else None

    if input_val is not None:
        catala_val = value_to_catala(input_val, civil_type, valid_variants)
        if catala_val is None:
            default = default_value_for_type(civil_type)
            print(
                f"  WARN  case '{case_id}': field '{note_prefix}{field_name}' has non-representable "
                f"value '{input_val}'; defaulting to {default}",
                file=sys.stderr,
            )
            return default, "non-representable input value; defaulted"
        return catala_val, None
    elif is_optional:
        return default_value_for_type(civil_type), None
    else:
        default = default_value_for_type(civil_type)
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
) -> list:
    """Emit Catala lines for one #[test] scope.

    all_fields: ordered list of (field_name, civil_type, is_optional) for scope inputs.
      Used only in single-entity mode (one facts entity).
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

    multi_entity = len(entity_fields) > 1

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
                raw_val = inputs.get(prefixed_key)
                # Treat missing key (not in inputs) same as not-provided
                input_val = raw_val if prefixed_key in inputs else None
                catala_val, note = emit_field_value(
                    case_id, field_name, civil_type, is_optional,
                    input_val, enum_variants, note_prefix=f"{entity_name}.",
                )
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
            )
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
        if val is not None:
            catala_variant = snake_to_pascal(str(val))
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

    field_types, optional_flags, enum_variants, entity_fields, computed_field_types = build_field_type_map(civil_doc)

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
        fail(f"No tests found in {tests_path}")

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
