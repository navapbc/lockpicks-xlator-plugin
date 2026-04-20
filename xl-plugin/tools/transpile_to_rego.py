#!/usr/bin/env python3
"""
CIVIL → OPA/Rego Transpiler

Converts any CIVIL DSL YAML module to an OPA/Rego policy file.

All domain-specific values (package name, tables, constants, computed fields,
rules, and the decision object shape) are derived from the CIVIL YAML itself.
The only external input is the OPA package name, supplied via --package.

Usage (via xlator CLI):
    xlator rego-transpile <domain> <module>

Example:
    xlator rego-transpile snap eligibility

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

import re
import sys
import os
import pathlib
import argparse
import subprocess
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from civil_expr import normalize_computed_doc  # noqa: E402


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_civil(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        fail(f"File not found: {path}")
    except yaml.YAMLError as e:
        fail(f"YAML parse error: {e}")


def validate_before_transpile(path):
    """Run the CIVIL validator first. Exits 1 if invalid."""
    validator = os.path.join(os.path.dirname(__file__), "validate_civil.py")
    ret = subprocess.run([sys.executable, validator, path], capture_output=True).returncode
    if ret != 0:
        # Re-run with output visible
        subprocess.run([sys.executable, validator, path])
        fail(f"CIVIL validation failed for {path}. Fix errors above before transpiling.")


def _key_repr(k):
    return f'"{k}"' if isinstance(k, str) else str(k)


def table_to_rego_dict(table_name, table_def, value_col):
    """Emit a Rego object literal from a CIVIL table.

    Single-key tables → flat dict: { k: v, ... }
    Multi-key tables  → nested dict: { k1: { k2: v, ... }, ... }
    """
    rows = table_def.get("rows", [])
    key_cols = table_def.get("key", [])

    if len(key_cols) == 1:
        key_col = key_cols[0]
        lines = [f"{table_name} := {{"]
        for row in rows:
            lines.append(f"    {_key_repr(row[key_col])}: {row[value_col]},")
        lines.append("}")
        return "\n".join(lines)

    # Multi-key: build nested dicts (preserving insertion order)
    nested = {}
    key_order = []
    for row in rows:
        k1 = row[key_cols[0]]
        k2 = row[key_cols[1]]
        if k1 not in nested:
            nested[k1] = {}
            key_order.append(k1)
        nested[k1][k2] = row[value_col]

    lines = [f"{table_name} := {{"]
    for k1 in key_order:
        lines.append(f"    {_key_repr(k1)}: {{")
        for k2, val in nested[k1].items():
            lines.append(f"        {_key_repr(k2)}: {val},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def _split_top_level_comma(args_str):
    """Split 'a, b' on the first comma not inside nested parentheses."""
    depth = 0
    for i, ch in enumerate(args_str):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            return args_str[:i].strip(), args_str[i + 1:].strip()
    raise ValueError(f"No top-level comma found in: {args_str!r}")


def _replace_binary_fn(expr, fn_name):
    """Replace fn_name(a, b) with fn_name([a, b]) using balanced-paren parsing."""
    result = []
    i = 0
    pattern = fn_name + "("
    while i < len(expr):
        idx = expr.find(pattern, i)
        if idx == -1:
            result.append(expr[i:])
            break
        result.append(expr[i:idx])
        start = idx + len(pattern)
        depth = 1
        j = start
        while j < len(expr) and depth > 0:
            if expr[j] == "(":
                depth += 1
            elif expr[j] == ")":
                depth -= 1
            j += 1
        args_str = expr[start:j - 1]
        a, b = _split_top_level_comma(args_str)
        result.append(f"{fn_name}([{a}, {b}])")
        i = j
    return "".join(result)


def translate_expr(expr, constants=None, optional_fields=None, all_input_fields=None, invoke_bound_entities=None):
    """
    Translate a CIVIL expression string to an equivalent Rego expression string.

    Transformations applied:
    1. table('name', k1, ...).col  →  name[k1]...  (column name dropped)
    2. Entity.field                →  input.field  (flat entities)
       Entity.field (invoke-bound) →  input.entity_var.field  (nested input path)
                                      (optional fields → object.get(input, "field", default))
    2b. bare field_name            →  input.field_name  (for known input fields without entity prefix)
    3. max(a, b)                   →  max([a, b])
    4. min(a, b)                   →  min([a, b])
    5. CONSTANT_NAME               →  literal value (inline substitution)
    """
    result = expr
    invoke_bound_entities = invoke_bound_entities or set()

    # Step 2b: bare field_name → input.field_name for known input fields.
    # Handles CIVIL modules that omit the Entity. prefix in expressions.
    # Runs first so later steps (table, Entity.field) don't create strings that
    # could be spuriously re-matched.
    # Uses a negative lookbehind for '.' to skip Entity.field and input.field patterns.
    if all_input_fields:
        for field_name in all_input_fields:
            def _make_replacer(fname):
                def _replace(m):
                    if m.start() > 0 and result[m.start() - 1] == ".":
                        return m.group(0)
                    if optional_fields and fname in optional_fields:
                        default = optional_fields[fname]
                        if isinstance(default, bool):
                            default_str = "false" if not default else "true"
                        elif isinstance(default, str):
                            default_str = f'"{default}"'
                        else:
                            default_str = str(default)
                        return f'object.get(input, "{fname}", {default_str})'
                    return f"input.{fname}"
                return _replace
            result = re.sub(rf"\b{re.escape(field_name)}\b", _make_replacer(field_name), result)

    # Step 1: table('name', k1, k2, ...).col  →  name[k1][k2]...
    def replace_table(m):
        tname = m.group(1)
        key_expr = m.group(2).strip()
        keys = [k.strip() for k in key_expr.split(",")]
        translated = [re.sub(r"\b([A-Z][a-zA-Z]*)\.(\w+)", r"input.\2", k) for k in keys]
        return tname + "".join(f"[{k}]" for k in translated)

    result = re.sub(
        r"table\('(\w+)',\s*([^)]+)\)\.\w+",
        replace_table,
        result
    )

    # Step 2: Entity.field  →  input.field (flat) or input.entity_var.field (invoke-bound).
    # Invoke-bound entities are declared as nested input objects; their fields are accessed
    # as input.entity_var.field_name (not the flat input.field_name form).
    # Optional fields use object.get(input, "field", default) so absent values
    # don't cause undefined cascades through computed rules.
    def replace_field(m):
        entity_name = m.group(1)
        field_name = m.group(2)
        if entity_name in invoke_bound_entities:
            # Nested input path: input.entity_var.field
            entity_var = re.sub(r"(?<!^)(?=[A-Z])", "_", entity_name).lower()
            return f"input.{entity_var}.{field_name}"
        if optional_fields and field_name in optional_fields:
            default = optional_fields[field_name]
            if isinstance(default, bool):
                default_str = "false" if not default else "true"
            elif isinstance(default, str):
                default_str = f'"{default}"'
            else:
                default_str = str(default)
            return f'object.get(input, "{field_name}", {default_str})'
        return f"input.{field_name}"

    result = re.sub(r"\b([A-Z][a-zA-Z]*)\.(\w+)", replace_field, result)

    # Step 3: max(a, b)  →  max([a, b])
    result = _replace_binary_fn(result, "max")

    # Step 4: min(a, b)  →  min([a, b])
    result = _replace_binary_fn(result, "min")

    # Step 5: Substitute UPPER_SNAKE_CASE constants with literal values
    if constants:
        for name, value in constants.items():
            result = re.sub(rf"\b{re.escape(name)}\b", str(value), result)

    return result


def _split_top_level(expr, op):
    """Split expr on op ('&&' or '||') at the top level (not inside parentheses)."""
    parts = []
    depth = 0
    current = []
    i = 0
    op_len = len(op)
    while i < len(expr):
        if expr[i] == "(":
            depth += 1
            current.append(expr[i])
        elif expr[i] == ")":
            depth -= 1
            current.append(expr[i])
        elif expr[i:i + op_len] == op and depth == 0:
            parts.append("".join(current).strip())
            current = []
            i += op_len
            continue
        else:
            current.append(expr[i])
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _split_on_and(expr):
    return _split_top_level(expr, "&&")


def _split_on_or(expr):
    return _split_top_level(expr, "||")


def translate_when_to_rego_body(when_expr, constants=None, optional_fields=None, all_input_fields=None):
    """
    Translate a CIVIL when: expression to a list of Rego body condition strings.

    &&  → split into separate conditions (each on its own line)
    !x  → not x
    Entity.field → input.field (via translate_expr)
    table(...) → table_name[key] (via translate_expr)
    """
    if when_expr.strip() == "true":
        return ["true"]

    clauses = _split_on_and(when_expr)
    result = []
    for clause in clauses:
        clause = clause.strip()
        if clause.startswith("!"):
            inner = translate_expr(clause[1:].strip(), constants, optional_fields, all_input_fields)
            result.append(f"not {inner}")
        else:
            result.append(translate_expr(clause, constants, optional_fields, all_input_fields))
    return result


def _emit_computed_field_rego(lines, field_name, field_def, constants=None, optional_fields=None, all_input_fields=None, invoke_bound_entities=None, emit_bool_default=True):
    """
    Emit Rego rules for a single expr:/conditional: field into `lines`.

    - expr (non-bool):  field_name := <translated_expr>
    - expr (bool):      [default field_name := false]  (if emit_bool_default)
                        field_name if { <translated_expr> }
    - conditional:      field_name := <then> if { <if> } else := <else>

    `emit_bool_default`: set False when the caller has already emitted `default`.
    """
    ftype = field_def.get("type")
    description = field_def.get("description", "")
    has_cond = "conditional" in field_def

    if description:
        lines.append(f"# {description}")

    if has_cond:
        cond = field_def["conditional"]
        raw_if = cond["if"]
        then_expr = translate_expr(cond["then"], constants, optional_fields, all_input_fields, invoke_bound_entities)
        else_expr = translate_expr(cond["else"], constants, optional_fields, all_input_fields, invoke_bound_entities)

        # Split on top-level || (OR → multiple rule heads).
        # For each OR branch, split on && (AND → separate body conditions).
        # Rego rule bodies support neither || nor &&.
        or_branches = []
        for raw_clause in _split_on_or(raw_if):
            and_parts = _split_on_and(raw_clause)
            translated = []
            for part in and_parts:
                part = part.strip()
                if part.startswith("!"):
                    inner = translate_expr(part[1:].strip(), constants, optional_fields, all_input_fields, invoke_bound_entities)
                    translated.append(f"not {inner}")
                else:
                    translated.append(translate_expr(part, constants, optional_fields, all_input_fields, invoke_bound_entities))
            or_branches.append(translated)

        if len(or_branches) > 1:
            # Multiple OR branches: emit a separate rule head for each
            if then_expr == "true" and else_expr == "false":
                if emit_bool_default:
                    lines.append(f"default {field_name} := false")
                for branch in or_branches:
                    if len(branch) == 1:
                        lines.append(f"{field_name} := true if {{ {branch[0]} }}")
                    else:
                        lines.append(f"{field_name} := true if {{")
                        for cond_str in branch:
                            lines.append(f"    {cond_str}")
                        lines.append("}")
            else:
                helper = f"_{field_name}_cond"
                lines.append(f"default {helper} := false")
                for branch in or_branches:
                    if len(branch) == 1:
                        lines.append(f"{helper} if {{ {branch[0]} }}")
                    else:
                        lines.append(f"{helper} if {{")
                        for cond_str in branch:
                            lines.append(f"    {cond_str}")
                        lines.append("}")
                lines.append(f"{field_name} := {then_expr} if {{ {helper} }}")
                lines.append(f"default {field_name} := {else_expr}")
        else:
            # Single OR branch (may still have multiple AND conditions)
            branch = or_branches[0]
            if len(branch) == 1:
                lines.append(f"{field_name} := {then_expr} if {{ {branch[0]} }} else := {else_expr}")
            else:
                lines.append(f"{field_name} := {then_expr} if {{")
                for cond_str in branch:
                    lines.append(f"    {cond_str}")
                lines.append(f"}} else := {else_expr}")
    else:
        expr = field_def["expr"]
        rego_expr = translate_expr(expr, constants, optional_fields, all_input_fields, invoke_bound_entities)
        if ftype == "bool":
            # Rego rule bodies support neither || nor && — split both
            if emit_bool_default:
                lines.append(f"default {field_name} := false")
            for or_clause in _split_on_or(expr):
                and_parts = _split_on_and(or_clause)
                translated_parts = []
                for part in and_parts:
                    part = part.strip()
                    if part.startswith("!"):
                        inner = translate_expr(part[1:].strip(), constants, optional_fields, all_input_fields, invoke_bound_entities)
                        translated_parts.append(f"not {inner}")
                    else:
                        translated_parts.append(translate_expr(part, constants, optional_fields, all_input_fields, invoke_bound_entities))
                if len(translated_parts) == 1:
                    lines.append(f"{field_name} if {{ {translated_parts[0]} }}")
                else:
                    lines.append(f"{field_name} if {{")
                    for cond_str in translated_parts:
                        lines.append(f"    {cond_str}")
                    lines.append("}")
        else:
            lines.append(f"{field_name} := {rego_expr}")


def emit_computed_section(computed_fields, constants=None, skip=None, optional_fields=None, all_input_fields=None, invoke_bound_entities=None, sub_module_imports=None):
    """
    Emit Rego rules for all fields in the computed: section.

    - expr (non-bool):  field_name := <translated_expr>
    - expr (bool):      default field_name := false
                        field_name if { <translated_expr> }
    - conditional:      field_name := <then> if { <if> } else := <else>
    - invoke:           field_name := data.<domain>.<module>.decision with input as input.<entity_var>

    Fields in `skip` are noted with a comment and skipped (already emitted elsewhere).
    Returns a list of Rego source lines.
    """
    skip = skip or set()
    invoke_bound_entities = invoke_bound_entities or set()
    sub_module_imports = sub_module_imports or {}  # field_name → "data.domain.module"
    lines = [
        "# =============================================================================",
        "# COMPUTED VALUES (from CIVIL v2 computed: section)",
        "# =============================================================================",
        "",
    ]

    for field_name, field_def in computed_fields.items():
        if field_name in skip:
            lines.append(f"# {field_name}: handled elsewhere")
            lines.append("")
            continue

        # CIVIL v4: invoke: fields — emit with-based Rego assignment
        if isinstance(field_def, dict) and field_def.get("invoke"):
            invoke_field = field_def["invoke"]
            bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
            pkg_path = sub_module_imports.get(field_name, "")
            description = field_def.get("description", "")
            if description:
                lines.append(f"# {description}")
            # Each bind entry maps sub_entity → parent_entity.
            # Pass input.entity_var as the sub-module's input.
            # (Assumes single-entity bind for the primary use case; first bind entry wins.)
            for sub_entity, parent_entity in bind.items():
                entity_var = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_entity).lower()
                lines.append(f"{field_name} := {pkg_path}.decision with input as input.{entity_var}")
                break  # one with clause per invoke: field
            lines.append("")
            continue

        _emit_computed_field_rego(
            lines, field_name, field_def,
            constants=constants, optional_fields=optional_fields,
            all_input_fields=all_input_fields, invoke_bound_entities=invoke_bound_entities,
        )
        lines.append("")

    return lines


def transpile(doc, output_path, package):
    """
    Generic CIVIL → Rego transpiler.

    Derives all domain-specific values from the CIVIL YAML:
    - constants:  → inline-substituted in expressions
    - tables:     → emitted as Rego object literals
    - computed:   → emitted as Rego derived rules
    - rules:      → deny-kind rules emitted as reasons contains ...
    - decisions:  → bool decisions use expr: (e.g. count(reasons) == 0)
    """
    # CIVIL v7: normalize table_lookup fields → expr: before processing
    doc = normalize_computed_doc(doc)

    tables = doc.get("tables", {})
    constants = doc.get("constants", {})
    computed = doc.get("computed", {})
    rules = doc.get("rules", [])
    decisions = doc.get("decisions", {})

    # Build optional_fields map: field_name → Rego default value for absent inputs.
    # Optional money/int/float fields default to 0; bool fields default to False.
    # Also build all_input_fields set for bare-name translation (modules that omit Entity. prefix).
    _type_defaults = {"money": 0, "int": 0, "float": 0, "bool": False, "string": ""}
    optional_fields = {}
    all_input_fields = set()
    for entity_def in doc.get("facts", {}).values():
        for field_name, field_def in entity_def.get("fields", {}).items():
            all_input_fields.add(field_name)
            if field_def.get("optional"):
                ftype = field_def.get("type", "money")
                optional_fields[field_name] = _type_defaults.get(ftype, 0)

    # CIVIL v4: compute invoke-bound entities and build sub-module import map
    # Invoke-bound entities have nested input paths: input.entity_var.field
    invoke_bound_entities: set = set()
    sub_module_imports: dict = {}  # field_name → "data.domain.sub_module"
    domain = package.split(".")[0]
    for field_name, field_def in (computed or {}).items():
        if not isinstance(field_def, dict) or not field_def.get("invoke"):
            continue
        invoke_field = field_def["invoke"]
        bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
        invoke_bound_entities.update(bind.values())
        sub_module_name = field_def.get("module", "")
        if sub_module_name:
            sub_module_imports[field_name] = f"data.{domain}.{sub_module_name}"

    civil_path = sys.argv[1]

    # Build import lines for sub-modules (deduplicated)
    sub_module_packages = sorted(set(sub_module_imports.values()))

    lines = [
        f"# Generated by tools/transpile_to_rego.py from {os.path.basename(civil_path)}",
        f"# Module: {doc.get('module')}",
        f"# Description: {doc.get('description')}",
        f"# Version: {doc.get('version')}",
        f"# Effective: {doc.get('effective', {}).get('start')} – {doc.get('effective', {}).get('end')}",
        "#",
        "# DO NOT EDIT — regenerate with:",
        "#   xlator rego-transpile <domain> <module>",
        "",
        f"package {package}",
        "",
        "import future.keywords.if",
        "import future.keywords.contains",
    ]
    for pkg in sub_module_packages:
        lines.append(f"import {pkg}")
    lines.append("")

    # Tables
    if tables:
        lines += [
            "# =============================================================================",
            "# LOOKUP TABLES (from CIVIL tables:)",
            "# =============================================================================",
            "",
        ]
        for table_name, table_def in tables.items():
            desc = table_def.get("description", "")
            value_col = table_def.get("value", [None])[0]
            if not value_col:
                fail(f"Table '{table_name}' missing 'value:' column definition")
            if desc:
                lines.append(f"# {desc}")
            lines += table_to_rego_dict(table_name, table_def, value_col).split("\n")
            lines.append("")

    # Computed section
    if computed:
        lines += emit_computed_section(
            computed, constants=constants, optional_fields=optional_fields,
            all_input_fields=all_input_fields, invoke_bound_entities=invoke_bound_entities,
            sub_module_imports=sub_module_imports,
        )

    # Deny rules → list/set decision field (e.g. reasons)
    deny_rules = [r for r in rules if r.get("kind") == "deny"]
    if deny_rules:
        lines += [
            "# =============================================================================",
            "# DENY RULES (from CIVIL rules:)",
            "# =============================================================================",
            "",
        ]
        for rule in deny_rules:
            rule_id = rule.get("id", "")
            desc = rule.get("description", "")
            when = rule.get("when", "true")
            actions = rule.get("then", [])

            if desc:
                lines.append(f"# {rule_id}: {desc}")

            when_body = translate_when_to_rego_body(when, constants, optional_fields, all_input_fields)

            for action in actions:
                if "add_reason" in action:
                    reason_def = action["add_reason"]
                    code = reason_def["code"]
                    message = reason_def["message"]
                    citations = reason_def.get("citations", [])
                    citation = citations[0]["label"] if citations else ""

                    reasons_field = next(
                        (k for k, v in decisions.items() if v.get("type") in ("list", "set")),
                        "reasons",
                    )
                    lines.append(f"{reasons_field} contains reason if {{")
                    for cond in when_body:
                        lines.append(f"    {cond}")
                    lines.append("    reason := {")
                    lines.append(f'        "code": "{code}",')
                    lines.append(f'        "message": "{message}",')
                    if citation:
                        lines.append(f'        "citation": "{citation}"')
                    lines.append("    }")
                    lines.append("}")
                    lines.append("")

    # Expression-driven decisions (bool + all scalar types with expr:/conditional:)
    expr_decisions = {
        k: v for k, v in decisions.items()
        if v.get("type") not in ("list", "set")
    }
    if expr_decisions:
        lines += [
            "# =============================================================================",
            "# DECISION RULES (from CIVIL decisions: section)",
            "# =============================================================================",
            "",
        ]
        for field_name, field_def in expr_decisions.items():
            ftype = field_def.get("type", "bool")
            if ftype == "bool":
                lines.append(f"default {field_name} := false")
            _emit_computed_field_rego(
                lines, field_name, field_def,
                constants=constants, optional_fields=optional_fields,
                all_input_fields=all_input_fields, invoke_bound_entities=invoke_bound_entities,
                emit_bool_default=False,
            )
            lines.append("")

    # Structured decision object
    lines += [
        "# =============================================================================",
        "# STRUCTURED DECISION OBJECT",
        "# =============================================================================",
        "",
        "decision := {",
    ]
    for field_name, field_def in decisions.items():
        ftype = field_def.get("type")
        if ftype == "bool":
            lines.append(f'    "{field_name}": {field_name},')
        elif ftype in ("list", "set"):
            lines.append(f'    "{field_name}": [r | r := {field_name}[_]],')
        elif field_def.get("expr") or field_def.get("conditional"):
            lines.append(f'    "{field_name}": {field_name},')
    if computed:
        computed_keys = list(computed.keys())
        lines.append('    "computed": {')
        for i, cfield in enumerate(computed_keys):
            comma = "," if i < len(computed_keys) - 1 else ""
            lines.append(f'        "{cfield}": {cfield}{comma}')
        lines.append("    }")
    lines.append("}")

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✓ Transpiled to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Transpile a CIVIL DSL YAML module to OPA/Rego"
    )
    parser.add_argument("civil_yaml", help="Path to the CIVIL YAML module")
    parser.add_argument("output_rego", help="Path for the generated Rego file")
    parser.add_argument(
        "--package",
        required=True,
        help="OPA package name, e.g. snap.eligibility",
    )
    args = parser.parse_args()

    validate_before_transpile(args.civil_yaml)
    doc = load_civil(args.civil_yaml)
    transpile(doc, args.output_rego, package=args.package)


if __name__ == "__main__":
    main()
