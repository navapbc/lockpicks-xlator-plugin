#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
YAML Tests → Catala Test File Transpiler (post-pivot, U7)

Converts a YAML test file (<domain>/specs/tests/<module>_*_tests.yaml)
to a Catala test file (<domain>/output/tests/<module>_*_tests.catala_en)
using the Catala #[test] assertion pattern.

Each YAML test case becomes a #[test] scope that calls the main scope via
  result scope <Module.Scope>
and asserts on the output fields.

Type metadata source (post-pivot):
    Type info is read from `<domain>/specs/naming-manifest.yaml` per R3
    extended in U7. The pre-pivot `--civil-spec` arg path is gone.

Six type-shaped lookups, all manifest-driven after U7:
    1. Field types (per scope input)         — manifest entry's `type:`
    2. Optionality (per scope input)         — manifest entry's `optional:` (default false)
    3. Enum variants (per enum-typed field)  — manifest entry's `enum_variants:` (with `values:` legacy fallback)
    4. Entity grouping (multi-entity mode)   — manifest's `inputs.<Entity>` structure
    5. Computed-field types (for assertions) — manifest's `computed:` block
    6. Output-type filtering (decisions)     — manifest's `outputs:` block, partitioned by `type:`

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
# UTILITIES
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
# TYPE NORMALIZATION
# =============================================================================

# Map Catala primitive type names to the canonical internal name used by the
# rest of this module. Legacy CIVIL type names map to the same internal name
# so callers can transparently consume pre-U7 manifests.
#
# Internal canonical names (preserved from pre-U7 for downstream code paths):
#   money | bool | int | float | string | enum | list | date
#
# `set` and `object` are not test-emittable leaf types — callers fall back
# to `string` for them and warn.
_TYPE_ALIASES = {
    # Catala-native (post-pivot)
    "integer": "int",
    "decimal": "float",
    "boolean": "bool",
    "duration": "string",   # treated opaquely in tests for now
    # CIVIL legacy (pre-pivot; map to themselves)
    "money": "money",
    "bool": "bool",
    "int": "int",
    "float": "float",
    "string": "string",
    "enum": "enum",
    "list": "list",
    "date": "date",
    "set": "string",
    "object": "string",
}

# Default leaf type used when a manifest entry lacks `type:`. The transpiler
# also emits a stderr WARN identifying the field so the gap is visible per
# the U7 "needs type" warning policy in ruleset-shared.md.
_DEFAULT_LEAF_TYPE = "string"


def _normalize_type(raw_type) -> str:
    """Normalize a manifest `type:` value to the internal leaf-type name.

    Unknown type strings (likely struct/enum type references like `Household`)
    fall back to `string` so emission proceeds; the entry's `enum_variants:`
    field, if present, switches the field into enum-handling mode separately.
    """
    if raw_type is None:
        return _DEFAULT_LEAF_TYPE
    s = str(raw_type)
    return _TYPE_ALIASES.get(s, _DEFAULT_LEAF_TYPE)


# =============================================================================
# MANIFEST-DRIVEN TYPE MAP
# =============================================================================

def build_field_type_map_from_manifest(manifest_doc: dict, scope_name: str) -> tuple:
    """Build field type/optional/enum maps and an ordered entity→fields map
    from `naming-manifest.yaml`.

    Replaces the pre-pivot `build_field_type_map(civil_doc)` function. Six
    type-shaped lookups are all manifest-resolved:

      1. types[field]            → leaf type (normalized to int/money/bool/...)
      2. optional_flags[field]   → bool
      3. enum_variants[field]    → {raw → emit_form} dict
      4. entity_fields[Entity]   → [(field, type, optional)] in manifest order
      5. computed_field_types    → only `output`/`expose` analogue
                                    (all computed entries are surfaced for
                                    assertion purposes; the manifest carries
                                    no `[expose]` tag — instead, the skill
                                    sets `output` vs `internal` in the Catala
                                    source. We assert any expected: key whose
                                    name appears in computed:.)
      6. output-type partitioning happens at the caller (per output type).

    Returns:
        (types, optional_flags, enum_variants, entity_fields, computed_field_types)
    """
    if not isinstance(manifest_doc, dict):
        manifest_doc = {}

    types: dict = {}
    optional_flags: dict = {}
    enum_variants: dict = {}
    entity_fields: dict = {}

    # Inputs are 3-level: inputs.<Entity>.<field>
    inputs_block = manifest_doc.get("inputs") or {}
    if isinstance(inputs_block, dict):
        for entity_name, fields_map in inputs_block.items():
            if not isinstance(fields_map, dict):
                continue
            fields_ordered: list = []
            for field_name, entry in fields_map.items():
                entry = entry if isinstance(entry, dict) else {}
                leaf_type = _normalize_type(entry.get("type"))
                is_optional = bool(entry.get("optional", False))
                # Warn on missing type — the U7 gap signal.
                if entry.get("type") is None:
                    print(
                        f"  WARN  field '{entity_name}.{field_name}' has no `type:` "
                        f"in naming-manifest.yaml; defaulting to {_DEFAULT_LEAF_TYPE}",
                        file=sys.stderr,
                    )
                types[field_name] = leaf_type
                optional_flags[field_name] = is_optional

                variants = _collect_enum_variants(entry)
                if variants is not None:
                    enum_variants[field_name] = variants
                    # When a field has declared enum constructors (either
                    # `enum_variants:` post-pivot or legacy `values:`),
                    # treat the field as enum so the literal lookup picks
                    # up the constructor map at emission time. The leaf
                    # type recorded in `types[]` switches to `enum` so
                    # downstream `value_to_catala` calls take the enum
                    # branch.
                    types[field_name] = "enum"

                fields_ordered.append(
                    (field_name, types[field_name], is_optional)
                )
            entity_fields[entity_name] = fields_ordered

    # Computed entries — all surfaced for assertion; the AI decides
    # `output` vs `internal` in the Catala source. We assert any expected:
    # key whose name appears in computed:.
    computed_block = manifest_doc.get("computed") or {}
    computed_field_types: dict = {}
    if isinstance(computed_block, dict):
        for name, entry in computed_block.items():
            entry = entry if isinstance(entry, dict) else {}
            leaf = _normalize_type(entry.get("type"))
            computed_field_types[name] = leaf
            variants = _collect_enum_variants(entry)
            if variants is not None:
                enum_variants[name] = variants

    # Outputs — collect enum variant info so assertion emission can
    # render PascalCase constructors for string-with-values decisions.
    outputs_block = manifest_doc.get("outputs") or {}
    if isinstance(outputs_block, dict):
        for name, entry in outputs_block.items():
            entry = entry if isinstance(entry, dict) else {}
            variants = _collect_enum_variants(entry)
            if variants is not None:
                enum_variants[name] = variants

    return types, optional_flags, enum_variants, entity_fields, computed_field_types


def _collect_enum_variants(entry: dict):
    """Return the {raw → emit_form} dict for an entry, or None when the
    entry is not enum-shaped.

    Priority (post-pivot):
      1. `enum_variants:` — Catala-native list of constructor names.
         Each variant is mapped to itself (analyst-authored constructor
         names are already in the emit form).
      2. `values:` (legacy CIVIL) — string values; PascalCase to form
         emit constructors (matches `transpile_to_catala`'s legacy
         declaration emission).

    Returns None when neither field is supplied — the field is not an
    enum.
    """
    if not isinstance(entry, dict):
        return None
    ev = entry.get("enum_variants")
    if isinstance(ev, list) and ev:
        return {str(v): str(v) for v in ev}
    values = entry.get("values")
    if isinstance(values, list) and values:
        # Legacy fallback: PascalCase the value strings.
        return {str(v): snake_to_pascal(str(v)) for v in values}
    return None


def partition_outputs_by_type(manifest_doc: dict) -> tuple:
    """Partition `outputs:` entries by type for decision-field emission.

    Returns (bool_decision_fields, string_decision_fields,
             numeric_decision_fields, denial_field).

    - bool_decision_fields: names with normalized leaf type `bool`.
    - string_decision_fields: names with leaf type `string` AND an
      enum-variant set (declared via `enum_variants:` or legacy `values:`).
      These render as PascalCase constructors in assertions.
    - numeric_decision_fields: list of (name, leaf_type) for
      money/int/float decisions.
    - denial_field: name of the first `list`-typed output, or 'reasons'
      as default.
    """
    bool_fields: list = []
    string_fields: list = []
    numeric_fields: list = []
    denial_field = "reasons"

    outputs = manifest_doc.get("outputs") or {}
    if not isinstance(outputs, dict):
        return bool_fields, string_fields, numeric_fields, denial_field

    seen_list_field = False
    for name, entry in outputs.items():
        entry = entry if isinstance(entry, dict) else {}
        raw_type = entry.get("type")
        leaf = _normalize_type(raw_type)
        if leaf == "bool":
            bool_fields.append(name)
        elif leaf in ("money", "int", "float"):
            numeric_fields.append((name, leaf))
        elif leaf == "string":
            # String with declared variants → enum-style decision.
            if _collect_enum_variants(entry) is not None:
                string_fields.append(name)
        elif leaf == "list":
            if not seen_list_field:
                denial_field = name
                seen_list_field = True
    return bool_fields, string_fields, numeric_fields, denial_field


def default_value_for_type(leaf_type: str) -> str:
    """Return the Catala zero/default literal for an optional field not in test inputs."""
    if leaf_type == "money":
        return "$0"
    elif leaf_type == "bool":
        return "false"
    elif leaf_type == "int":
        return "0"
    elif leaf_type == "float":
        return "0.0"
    else:
        return "0"


def value_to_catala(value, leaf_type: str, valid_enum_variants=None):
    """Convert a YAML test input value to a Catala literal.

    valid_enum_variants is a {raw_value: emit_form} dict (or None). When
    provided, the raw input must match a key — the emitted enum variant
    identifier comes from the value side of the map.

    Returns None if the value cannot be represented (e.g. non-numeric money,
    non-numeric int, or unrecognised enum variant).
    """
    if leaf_type == "money":
        return money_literal(value)  # may return None for non-numeric values
    elif leaf_type == "bool":
        return "true" if value else "false"
    elif leaf_type == "enum":
        str_val = str(value)
        if valid_enum_variants is not None and str_val not in valid_enum_variants:
            return None  # invalid variant — caller will default and warn
        return valid_enum_variants[str_val] if valid_enum_variants else str_val
    elif leaf_type in ("int", "float"):
        try:
            int(value) if leaf_type == "int" else float(value)
            return str(value)
        except (TypeError, ValueError):
            return None  # non-numeric string — caller will default and warn
    elif leaf_type == "string" and valid_enum_variants:
        # String field with declared/derived enum variants — must map to a
        # Catala enum constructor, never a free-form identifier.
        str_val = str(value)
        if str_val not in valid_enum_variants:
            return None  # invalid variant — caller will default and warn
        return valid_enum_variants[str_val]
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
    leaf_type: str,
    is_optional: bool,
    input_val,
    enum_variants: dict,
    note_prefix: str = "",
) -> tuple:
    """Resolve a field's Catala value and an optional warning note.

    Returns (catala_val: str, note: str | None).
    note is non-None when the value was defaulted or has a representability issue.
    """
    # string fields that map to Catala enums (declared variants) need variant lookup too
    valid_variants = enum_variants.get(field_name) if leaf_type in ("enum", "string") else None

    if input_val is not None:
        catala_val = value_to_catala(input_val, leaf_type, valid_variants)
        if catala_val is None:
            default = next(iter(valid_variants.values())) if valid_variants else default_value_for_type(leaf_type)
            print(
                f"  WARN  case '{case_id}': field '{note_prefix}{field_name}' has non-representable "
                f"value '{input_val}'; defaulting to {default}",
                file=sys.stderr,
            )
            return default, "non-representable input value; defaulted"
        return catala_val, None
    elif is_optional:
        default = next(iter(valid_variants.values())) if valid_variants else default_value_for_type(leaf_type)
        return default, None
    else:
        default = next(iter(valid_variants.values())) if valid_variants else default_value_for_type(leaf_type)
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

    all_fields: ordered list of (field_name, leaf_type, is_optional) for scope inputs.
      Used only in single-entity mode (one input facts entity).
    entity_fields: {entity_name: [(field_name, leaf_type, is_optional)]}.
      When more than one entity is present, struct-literal input emission is used instead
      of flat field assignment (multi-entity mode).
    bool_decision_fields: list of decision field names with type 'bool' to assert.
    string_decision_fields: list of decision field names with type 'string' + variants to assert.
    numeric_decision_fields: list of (field_name, leaf_type) for money/int/float decisions.
    computed_field_types: {field_name: leaf_type} for computed: fields — used to assert any
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
    lines.append("#[test]")
    lines.append(f"declaration scope {test_scope}:")
    lines.append(f"  result scope {scope_name}")
    lines.append("")

    # --- Scope body ---
    lines.append(f"scope {test_scope}:")

    multi_entity = len(entity_fields) > 1

    if multi_entity:
        # Multi-entity mode: inputs are keyed as 'EntityName.field_name' in the YAML.
        for entity_name, fields in entity_fields.items():
            var_name = entity_to_var_name(entity_name)
            type_ref = f"{catala_module_name}.{entity_name}" if catala_module_name else entity_name
            struct_lines = []
            for field_name, leaf_type, is_optional in fields:
                prefixed_key = f"{entity_name}.{field_name}"
                if prefixed_key in inputs:
                    input_val = inputs[prefixed_key]
                elif field_name in inputs:
                    input_val = inputs[field_name]
                else:
                    input_val = None
                catala_val, note = emit_field_value(
                    case_id, field_name, leaf_type, is_optional,
                    input_val, enum_variants, note_prefix=f"{entity_name}.",
                )
                suffix = f"  # NOTE: {note}" if note else ""
                struct_lines.append(f"    -- {field_name}: {catala_val}{suffix}")
            lines.append(f"  definition result.{var_name} equals {type_ref} {{")
            lines.extend(struct_lines)
            lines.append("  }")
    else:
        # Single-entity mode: inputs are keyed by bare field_name. Emit flat assignments.
        for field_name, leaf_type, is_optional in all_fields:
            raw_val = inputs.get(field_name)
            input_val = raw_val if field_name in inputs else None
            catala_val, note = emit_field_value(
                case_id, field_name, leaf_type, is_optional,
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
        if val is None:
            continue
        valid_variants = (enum_variants or {}).get(field)
        str_val = str(val)
        if valid_variants is not None and str_val not in valid_variants:
            print(
                f"  WARN  case '{case_id}': expected.{field} value '{str_val}' is not in "
                f"declared variants {list(valid_variants)} — skipping assertion",
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
    for field, leaf_type in (numeric_decision_fields or []):
        val = expected.get(field)
        if val is not None:
            catala_val = value_to_catala(val, leaf_type)
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
        leaf_type = (computed_field_types or {}).get(field)
        if leaf_type is None:
            print(
                f"  WARN  case '{case_id}': expected: field '{field}' not found in "
                f"decisions or computed; skipping",
                file=sys.stderr,
            )
            continue
        catala_val = value_to_catala(val, leaf_type)
        if catala_val is not None:
            lines.append(f"  assertion (result.{field} = {catala_val})")

    return lines


def catala_block(lines: list) -> list:
    """Wrap lines in a catala fenced code block."""
    return ["```catala"] + lines + ["```"]


# =============================================================================
# MAIN TRANSPILATION
# =============================================================================

def transpile(
    tests_path: str,
    output_path: str,
    scope_name: str,
    manifest_path: str,
    catala_module_name: str | None = None,
):
    """Transpile a YAML test suite to a Catala test file using `manifest_path`
    as the source of type metadata.

    `catala_module_name` is the CamelCase Catala module name to qualify
    sub-scope calls (e.g. `Eligibility`). When None, it's derived from the
    tests file's stem (matching pre-pivot behavior).
    """
    tests_doc = load_yaml_file(tests_path)
    manifest_doc = load_yaml_file(manifest_path) or {}

    field_types, optional_flags, enum_variants, entity_fields, computed_field_types = (
        build_field_type_map_from_manifest(manifest_doc, scope_name)
    )

    # Ordered list of (field_name, leaf_type, is_optional) for the scope call (single-entity mode)
    all_fields = [
        (fname, ftype, optional_flags.get(fname, False))
        for fname, ftype in field_types.items()
    ]

    # Partition outputs by type — manifest-driven.
    bool_decision_fields, string_decision_fields, numeric_decision_fields, denial_field = (
        partition_outputs_by_type(manifest_doc)
    )

    tests = tests_doc.get("tests", []) if isinstance(tests_doc, dict) else []
    if not tests:
        # Intentionally-empty test files are valid — emit a placeholder.
        print(f"WARN  No tests in {tests_path}; emitting empty placeholder.", file=sys.stderr)
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        # Derive module name from the test file when the caller didn't supply one
        if catala_module_name:
            module_name_for_emit = catala_module_name
        else:
            tests_basename = os.path.basename(tests_path)
            stem = tests_basename
            for suffix in ("_tests.yaml", ".yaml"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            # Best-effort: strip trailing _*_tests
            stem = re.sub(r"_(boundary|null_input|edge_case|derived_from_extracted)?$", "", stem)
            module_name_for_emit = stem[0].upper() + stem[1:] if stem else "Module"
        with open(output_path, "w") as f:
            f.write(f"> Using {module_name_for_emit}\n\n# Tests: (empty)\n")
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

    if catala_module_name is None:
        # Fall back: derive from the test file stem (e.g. `eligibility_tests.yaml`).
        tests_basename = os.path.basename(tests_path)
        stem = tests_basename
        for suffix in ("_tests.yaml", ".yaml"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        # Take the module-prefix portion (before the first _*_tests suffix).
        # Common test-file shapes:
        #   eligibility_tests.yaml → eligibility
        #   eligibility_boundary_expanded_tests.yaml → eligibility
        module_name = stem.split("_")[0] if "_" in stem else stem
        catala_module_name = module_name[0].upper() + module_name[1:]

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
        "--naming-manifest",
        required=True,
        dest="naming_manifest",
        help=(
            "Path to specs/naming-manifest.yaml — the U7 type-extended "
            "manifest. Drives field types, optionality, enum variants, "
            "entity grouping, computed-field types, and output-type "
            "partitioning. Replaces the pre-pivot --civil-spec arg."
        ),
    )
    parser.add_argument(
        "--module-name",
        default=None,
        dest="module_name",
        help=(
            "CamelCase Catala module name used to qualify sub-scope "
            "calls (e.g. 'Eligibility'). When omitted, derived from the "
            "tests file's stem."
        ),
    )

    args = parser.parse_args()

    basename = os.path.basename(args.tests_yaml)
    if basename.endswith("_null_input_expanded_tests.yaml"):
        print(f"SKIP  {args.tests_yaml} — null-input tests cannot be encoded in Catala; skipping.")
        sys.exit(0)

    transpile(
        args.tests_yaml,
        args.output_catala,
        args.scope,
        args.naming_manifest,
        catala_module_name=args.module_name,
    )


if __name__ == "__main__":
    main()
