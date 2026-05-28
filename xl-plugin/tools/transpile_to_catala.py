#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
CIVIL → Catala 1.1.0 Transpiler

Converts any CIVIL DSL YAML module to a Catala literate program (.catala_en).
Output is valid Catala 1.1.0 ("bac d'Eloka") targeting the English keyword set.

Syntax reference: core/catala-quickref.md
Official examples: https://raw.githubusercontent.com/CatalaLang/catala/refs/heads/master/doc/syntax/syntax_en.catala_en

Usage (via xlator CLI):
    xlator catala-transpile <domain> <module>

Example:
    xlator catala-transpile snap eligibility

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

import datetime
import re
import sys
import os
import pathlib
import argparse
import subprocess
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from civil_expr import normalize_computed_doc  # noqa: E402


# =============================================================================
# UTILITIES (copied from transpile_to_rego.py)
# =============================================================================

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
        subprocess.run([sys.executable, validator, path])
        fail(f"CIVIL validation failed for {path}. Fix errors above before transpiling.")


def required_sub_fields(sub_doc: dict, sub_entity: str) -> dict:
    """Return {field_name: field_spec} for fields the sub-module's Catala scope reads.

    Excludes optional fields (parent may omit; wiring emits zero defaults) and fields
    the scope-input rule omits entirely (e.g. free-form optional strings with no
    enum variants).

    Pure helper shared by the transpiler's bind-forwarding check and the
    check_binds repair pass — keep them in lock step.
    """
    sub_tables = sub_doc.get("tables", {}) or {}
    entity_fields = (
        sub_doc.get("inputs", {})
        .get(sub_entity, {})
        .get("fields", {})
        or {}
    )
    required = {}
    for field_name, field_def in entity_fields.items():
        if not isinstance(field_def, dict):
            continue
        if field_def.get("optional"):
            continue
        if _scope_input_omits_field(field_def, tables=sub_tables, field_name=field_name):
            continue
        required[field_name] = field_def
    return required


def check_bind_forwarding(parent_doc: dict, sub_module_docs: dict, computed: dict) -> list[str]:
    """Return error strings for each bind where parent entity is missing sub-module fields.

    Pure calculation — no I/O, no sys.exit. Caller decides how to report.
    Empty list means all binds are valid.

    Optional fields in the sub-module are not required of the parent — the parent
    may omit them and the wiring layer will supply zero/empty defaults instead.

    field_bind: entries are also validated: each sub-field name must exist in the
    named sub-module entity's inputs. Fields named in field_bind: are excluded from
    the missing-field check — they are treated as explicitly provided by the caller.
    """
    errors = []
    for field_name, field_def in (computed or {}).items():
        if not isinstance(field_def, dict) or not field_def.get("invoke"):
            continue
        sub_module_name = field_def.get("module", "")
        invoke_field = field_def["invoke"]
        bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}

        sub_doc = sub_module_docs.get(sub_module_name, {})

        field_bind = invoke_field.get("field_bind", {}) or {}

        # Validate that field_bind sub-field names exist in the sub-module's scope inputs.
        for sub_entity_fb, sub_field_map in field_bind.items():
            sub_entity_fields_fb = sub_doc.get("inputs", {}).get(sub_entity_fb, {}).get("fields", {})
            for sub_field in sub_field_map:
                if sub_field not in sub_entity_fields_fb:
                    valid_fields = sorted(sub_entity_fields_fb.keys())
                    errors.append(
                        f"field_bind: '{sub_entity_fb}.{sub_field}' does not exist in "
                        f"sub-module {sub_module_name!r} entity '{sub_entity_fb}' inputs. "
                        f"Valid fields: {valid_fields}."
                    )

        for sub_entity, parent_entity in bind.items():
            required_names = set(required_sub_fields(sub_doc, sub_entity).keys())
            # field_bind: explicitly provides certain fields — exclude them from the missing check.
            field_bind_covered = set((field_bind.get(sub_entity) or {}).keys())
            parent_fields = set(
                parent_doc.get("inputs", {}).get(parent_entity, {}).get("fields", {}).keys()
            )
            missing = required_names - parent_fields - field_bind_covered
            if missing:
                errors.append(
                    f"Sub-module {sub_module_name!r} (bound as {sub_entity!r} → {parent_entity!r}) "
                    f"requires fields not declared on parent's "
                    f"inputs.{parent_entity}.fields: {sorted(missing)}. "
                    f"Either add the fields (with `optional: true` if appropriate) "
                    f"to the parent CIVIL spec, or remove the entity-to-entity bind "
                    f"and forward fields individually."
                )
    return errors


# =============================================================================
# HELPERS
# =============================================================================

def snake_to_pascal(name: str) -> str:
    """Convert snake_case or kebab-case to PascalCase."""
    return "".join(word.capitalize() for word in re.split(r"[_-]", name) if word)


def _to_catala_constructor(value: str) -> str:
    """Ensure a string value is a valid Catala constructor (uppercase-first identifier).

    Applies snake_to_pascal for lowercase-initial values (e.g. "individual" →
    "Individual", "low_income" → "LowIncome"). Leaves uppercase-initial values
    unchanged so existing codes like "A1E" are not mangled to "A1e".
    """
    if not value or value[0].isupper():
        return value
    return snake_to_pascal(value)


def reason_code_to_pascal(code: str) -> str:
    """Convert UPPER_SNAKE_CASE reason code to PascalCase variant name."""
    return "".join(word.capitalize() for word in code.split("_"))


def derive_scope_name(module_str: str) -> str:
    """Derive Catala scope name from CIVIL module string.

    'eligibility.snap_federal' → first segment 'eligibility' → 'EligibilityDecision'
    """
    first_segment = module_str.split(".")[0]
    return snake_to_pascal(first_segment) + "Decision"


def money_literal(value) -> str:
    """Format a number as a Catala money literal: 1696 → '$1,696', 609.34 → '$609.34'"""
    float_val = float(value)
    frac = float_val % 1
    negative = float_val < 0
    abs_val = abs(float_val)
    if frac != 0:
        int_part = int(abs_val)
        cents = round(abs_val - int_part, 2)
        cents_str = f"{cents:.2f}"[1:]  # ".34"
        formatted = f"${int_part:,}{cents_str}"
    else:
        formatted = f"${int(abs_val):,}"
    return f"-{formatted}" if negative else formatted


def percent_literal(value: float) -> str:
    """Format a float rate as a Catala percentage: 0.20 → '20%'"""
    pct = float(value) * 100
    if pct == int(pct):
        return f"{int(pct)}%"
    return f"{pct}%"


def _prose_block(description: str | None, source: str | None) -> str:
    """Return Markdown prose text for a CIVIL field's description and source.

    Empty/whitespace-only strings are treated the same as None — not emitted.
    Returns '' when both are absent.
    """
    parts = []
    desc = (description or "").strip()
    src = (source or "").strip()
    if desc:
        parts.append(desc)
    if src:
        parts.append(f"*Source: {src}*")
    return "\n\n".join(parts)


def _emit_prose_heading(md_lines: list, name: str, description: str | None, source: str | None):
    """Append H4 heading and optional prose block to md_lines.

    Always emits the H4 for Markdown anchor navigation. Prose body is only
    emitted when description or source is non-empty.
    """
    md_lines.append(f"#### {name}")
    md_lines.append("")
    prose = _prose_block(description, source)
    if prose:
        md_lines.append(prose)
        md_lines.append("")


def constant_to_catala(name: str, value) -> str:
    """Format a CIVIL constant value as a Catala literal.

    Dispatch rules:
      - float or name ends _RATE  → decimal with %
      - int and name ends _CAP, _LIMIT, _9PLUS, _EXCLUSION, _DEDUCTION  → money literal
      - otherwise  → integer
    """
    if isinstance(value, float) or name.endswith("_RATE"):
        return percent_literal(float(value))
    if isinstance(value, int) and any(
        name.endswith(s) for s in ("_CAP", "_LIMIT", "_9PLUS", "_EXCLUSION", "_DEDUCTION")
    ):
        return money_literal(value)
    return str(value)


def civil_type_to_catala(civil_type: str) -> str:
    """Map a CIVIL scalar field type to its Catala equivalent.

    For list/set types (which depend on an item: element type), use
    field_to_catala_type(field_def) which consults the declaration as a whole.
    The list fallback here is only reached when caller has no field_def context.
    """
    return {
        "int":    "integer",
        "float":  "decimal",
        "bool":   "boolean",
        "money":  "money",
        "date":   "date",
        "string": "text",
        "enum":   "enumeration",
        "list":   "list of integer",  # fallback when item: is unknown
        "set":    "list of integer",
    }.get(civil_type, civil_type)


def _default_catala_literal(civil_type: str) -> str:
    """Return the Catala zero/empty literal for a CIVIL scalar type.

    Used when a parent entity doesn't have an optional sub-module field — the
    wiring emits this default so the Catala struct is fully initialized.

    Raises ValueError for types that have no safe default (date, string, enum,
    object). These require explicit modelling in the parent entity or a manual
    default; auto-defaulting would emit a type-incorrect Catala literal.
    """
    defaults = {
        "money": "$0",
        "int": "0",
        "float": "0.0",
        "bool": "false",
        "list": "[]",
        "set": "[]",
    }
    if civil_type not in defaults:
        raise ValueError(
            f"_default_catala_literal: no zero/empty default for CIVIL type {civil_type!r}. "
            "Add this field to the parent entity, or supply an explicit default in the CIVIL spec."
        )
    return defaults[civil_type]


def field_to_catala_type(field_def: dict) -> str:
    """Map a CIVIL field declaration to its Catala type, honouring `item:` for lists.

    For `type: list, item: <T>` returns `list of <civil_type_to_catala(T)>`.
    For scalars defers to civil_type_to_catala. Defaults item to "money" because
    every list in current CIVIL specs carries monetary payments.
    """
    ftype = field_def.get("type", "money")
    if ftype in ("list", "set"):
        item_type = field_def.get("item", "money")
        return f"list of {civil_type_to_catala(item_type)}"
    return civil_type_to_catala(ftype)


def build_list_item_types(inputs_block: dict) -> dict:
    """Scan a CIVIL `inputs:` block and return {field_name: catala_element_type}.

    Used by translate_expr_to_catala()'s sum() rewrite to look up the element
    type that Catala requires after the `sum` keyword.
    """
    result: dict[str, str] = {}
    for entity_def in (inputs_block or {}).values():
        for field_name, field_def in entity_def.get("fields", {}).items():
            if isinstance(field_def, dict) and field_def.get("type") in ("list", "set"):
                item_type = field_def.get("item", "money")
                result[field_name] = civil_type_to_catala(item_type)
    return result


def _build_all_field_type_map(civil_doc: dict) -> dict[str, str]:
    """Return {bare_field_name: civil_type} for all inputs and computed fields.

    Used by the conditional branch type-check (Fix #23.2) to infer expression types
    from field references.  Keys are bare field names (no Entity. prefix).
    """
    result: dict[str, str] = {}
    for entity_def in (civil_doc.get("inputs") or {}).values():
        for field_name, field_def in (entity_def.get("fields") or {}).items():
            civil_type = field_def.get("type", "money") if isinstance(field_def, dict) else "money"
            result[field_name] = civil_type
    for field_name, field_def in (civil_doc.get("computed") or {}).items():
        civil_type = field_def.get("type", "money") if isinstance(field_def, dict) else "money"
        result[field_name] = civil_type
    return result


def _infer_civil_type(expr: str, field_type_map: dict[str, str]) -> str | None:
    """Infer the CIVIL type of a simple expression.

    Returns the CIVIL type string ("money", "int", "date", "bool", "string", …)
    or None when the expression is too complex to classify (arithmetic, function
    calls, nested conditionals, etc.).  Only literal patterns and single-identifier
    field references are classified; everything containing spaces or operators
    returns None so callers never produce false-positive type errors.
    """
    stripped = expr.strip()
    # Money literal: $N or $N,NNN or $N,NNN.CC
    if re.match(r'^\$[\d,]+(\.\d+)?$', stripped):
        return "money"
    # Integer literal
    if re.match(r'^\d+$', stripped):
        return "int"
    # Date literal: |YYYY-MM-DD|
    if re.match(r'^\|\d{4}-\d{2}-\d{2}\|$', stripped):
        return "date"
    # Boolean literals
    if stripped in ("true", "false"):
        return "bool"
    # Simple identifier (possibly Entity.field — strip one prefix level)
    parts = stripped.split(".")
    if len(parts) <= 2 and all(re.match(r'^[A-Za-z_]\w*$', part) for part in parts):
        bare = parts[-1]
        return field_type_map.get(bare)
    # Complex expression
    return None


def _check_cond_branch_type_compat(
    then_raw: str,
    else_raw: str,
    field_name: str,
    field_type_map: dict[str, str],
    field_type: str | None = None,
) -> None:
    """Raise ValueError when then/else branches have known, incompatible CIVIL types.

    Only fires when both branches resolve to a non-None type via _infer_civil_type.
    Complex expressions (arithmetic, nested if/then/else) return None and are silently
    skipped — false negatives are acceptable; false positives are not.

    In a money-typed field, bare integer literals (e.g. "0", "100") are treated as
    money-compatible because Step 11 / Step 13c will coerce them to $N during
    translate_expr_to_catala.  Integer *field references* are not exempted — they
    cannot be automatically coerced and represent a real type error.
    """
    then_type = _infer_civil_type(then_raw, field_type_map)
    else_type = _infer_civil_type(else_raw, field_type_map)
    if then_type is None or else_type is None:
        return
    # Coercion-aware exemption: bare integer literals in a money field are safe —
    # Step 11 converts a standalone "0" and Step 13c handles `then N` / `else N`.
    if field_type == "money":
        if then_type == "int" and re.match(r'^\d+$', then_raw.strip()):
            then_type = "money"
        if else_type == "int" and re.match(r'^\d+$', else_raw.strip()):
            else_type = "money"
    if then_type != else_type:
        raise ValueError(
            f"Field '{field_name}': conditional branches have incompatible types.\n"
            f"  then: \"{then_raw}\" → {then_type}\n"
            f"  else: \"{else_raw}\" → {else_type}\n"
            "Catala requires both branches of if…then…else to share the same type.\n"
            "Ensure both expressions reference fields of the same type."
        )


# =============================================================================
# EXPRESSION TRANSLATION
# =============================================================================

def _split_top_level_comma(args_str: str):
    """Split 'a, b' on the first top-level comma (not inside parentheses).

    Skips commas that are part of numeric literals (e.g. $2,410).
    """
    depth = 0
    for i, ch in enumerate(args_str):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            # Skip commas inside numeric literals: digit before and after the comma
            before = args_str[i - 1] if i > 0 else ""
            after = args_str[i + 1] if i + 1 < len(args_str) else ""
            if before.isdigit() and after.isdigit():
                continue
            return args_str[:i].strip(), args_str[i + 1:].strip()
    raise ValueError(f"No top-level comma in: {args_str!r}")


def _coerce_int_to_money_literal(s: str) -> str:
    """Convert a bare integer string to a Catala money literal when in a money context.

    Examples: '10' → '$10', '65' → '$65', '20' → '$20', 'after_65' → 'after_65' (unchanged).
    """
    stripped = s.strip()
    if re.match(r"^\d+$", stripped):
        return f"${int(stripped):,}"
    return s


def _rewrite_binary_fn_to_if(expr: str, fn_name: str, op: str, field_type: str = None) -> str:
    """Replace fn_name(a, b) with (if a OP b then a else b).

    If field_type is 'money', bare integer literals are converted to money literals ($N)
    so both operands have compatible types in the generated Catala expression.
    """
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
        if field_type == "money":
            a = _coerce_int_to_money_literal(a)
            b = _coerce_int_to_money_literal(b)
        result.append(f"(if {a} {op} {b} then {a} else {b})")
        i = j
    return "".join(result)


def _rewrite_abs(expr: str) -> str:
    """Replace abs(inner) with (if inner >= $0 then inner else $0 - (inner)).

    Used for money expressions where Catala has no built-in abs() function.
    """
    result = []
    i = 0
    pattern = "abs("
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
        inner = expr[start:j - 1]
        result.append(f"(if {inner} >= $0 then {inner} else $0 - ({inner}))")
        i = j
    return "".join(result)


def _rewrite_between(expr: str) -> str:
    """Replace between(val, low, high) with (low <= val and val <= high)."""
    result = []
    i = 0
    pattern = "between("
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
        args = []
        depth2 = 0
        buf = []
        for ch in args_str:
            if ch == "(":
                depth2 += 1
                buf.append(ch)
            elif ch == ")":
                depth2 -= 1
                buf.append(ch)
            elif ch == "," and depth2 == 0:
                args.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        if buf:
            args.append("".join(buf).strip())
        if len(args) == 3:
            val, low, high = args
            result.append(f"({low} <= {val} and {val} <= {high})")
        else:
            result.append(f"between({args_str})")
        i = j
    return "".join(result)


def negate_simple_condition(cond: str) -> str:
    """Flip a simple comparison operator: 'X <= N' → 'X > N', etc."""
    for op, neg in [("<=", ">"), (">=", "<"), (" < ", " >= "), (" > ", " <= ")]:
        if f" {op.strip()} " in cond or cond.endswith(f" {op.strip()}"):
            return cond.replace(op, neg, 1)
    return f"not ({cond})"


def translate_expr_to_catala(
    expr: str,
    constants: dict = None,
    field_type: str = None,
    tables: dict = None,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
    field_type_map: dict = None,
) -> str:
    """Translate a CIVIL expression to Catala syntax.

    Transformations (in order):
    0. Strip Entity. prefixes for non-invoke-bound entities (flat inputs).
       Rewrite Entity. → snake_case_var. for invoke-bound entities (struct inputs).
    1. Inline constants as Catala-formatted literals
    2. Resolve literal-key table lookups: table('name', INT).col → money literal
    3. Strip variable-key table lookups (handled by stacked defs in emit_table_section)
    4. max(a, b)  → (if a >= b then a else b)
    5. min(a, b)  → (if a <= b then a else b)
    6. &&  → and
    7. ||  → or
    8. !a  → not a  (guards against != being affected)
    9. ==  → =      (guards against != <= >= being affected)
    10. (int_expr) * $money → $money * (decimal of (int_expr))
    """
    result = expr

    # Step 0: Handle Entity. prefixes based on whether entity is invoke-bound or flat.
    # Invoke-bound entities are declared as struct inputs; their prefix must be rewritten
    # to the snake_case variable name (e.g. ClientData. → client_data.).
    # Non-invoke-bound entities are flattened: their prefix is stripped entirely.
    if fact_entities:
        for entity in fact_entities:
            if invoke_bound_entities and entity in invoke_bound_entities:
                # Rewrite to snake_case variable name (struct input)
                snake = re.sub(r"(?<!^)(?=[A-Z])", "_", entity).lower()
                result = re.sub(rf"\b{re.escape(entity)}\.", f"{snake}.", result)
            else:
                result = re.sub(rf"\b{re.escape(entity)}\.", "", result)

    # Step 1: Inline constants (longest name first to avoid partial substitution)
    if constants:
        for name, value in sorted(constants.items(), key=lambda x: -len(x[0])):
            catala_val = constant_to_catala(name, value)
            result = re.sub(rf"\b{re.escape(name)}\b", catala_val, result)

    # Step 2: Resolve literal-key table lookups: table('name', INT).col → value
    if tables:
        def replace_literal_table(m):
            tname = m.group(1)
            key_val = int(m.group(2))
            if tname not in tables:
                print(f"ERROR: table '{tname}' referenced in expression but not defined in tables:", file=sys.stderr)
                return m.group(0)
            key_col = tables[tname]["key"][0]
            val_col = tables[tname]["value"][0]
            for row in tables[tname].get("rows", []):
                if row[key_col] == key_val:
                    return money_literal(row[val_col])
            print(f"ERROR: table '{tname}' has no row where {key_col}={key_val} — leaving reference unchanged, will cause Catala syntax error", file=sys.stderr)
            return m.group(0)

        result = re.sub(
            r"table\('(\w+)',\s*(\d+)\)\.\w+",
            replace_literal_table,
            result,
        )

    # Step 3: Strip variable-key table lookups — these appear only in then: of
    # conditional fields processed by emit_table_section, not in expressions we translate.
    # As a safety fallback, strip them to avoid syntax errors.
    def _warn_strip_fn(m):
        print(f"WARNING: unexpected variable-key table lookup '{m.group(0)}' in translated expression — stripping to key only", file=sys.stderr)
        return m.group(1)

    result = re.sub(r"table\('\w+',\s*([^)]+)\)\.\w+", _warn_strip_fn, result)
    # Bracket subscript syntax: table_name[key] → key
    def _warn_strip_bracket(m):
        print(f"WARNING: bracket subscript table lookup '{m.group(0)}' is not valid CIVIL — use table('name', key).col syntax; stripping to key only", file=sys.stderr)
        return m.group(1)

    result = re.sub(r"\w+\[(\w+)\]", _warn_strip_bracket, result)

    # Step 3.5: between(val, low, high) → (low <= val and val <= high)
    result = _rewrite_between(result)

    # Step 3.55: abs(expr) → (if expr >= $0 then expr else $0 - (expr))
    result = _rewrite_abs(result)

    # Step 3.6: count(list) → (number of list)
    result = re.sub(r"\bcount\(([^)]+)\)", r"(number of \1)", result)

    # Step 3.65: sum(IDENT) → (sum <ELEM_TYPE> of IDENT)
    # Catala requires the element type after the `sum` keyword. The element type
    # comes from the `item:` field on the CIVIL list declaration, looked up via
    # list_item_types. Falls back to "money" when the field is unknown -- the
    # near-universal case in CIVIL rulesets (pay amounts, exclusions, deductions).
    # Parentheses ensure the aggregate scope ends at the list identifier so the
    # surrounding arithmetic (e.g. `/ number_of_payments`) is not absorbed into
    # the `sum ... of ...` form.
    def _rewrite_sum(match):
        list_field_ref = match.group(1).strip()
        bare_field = list_field_ref.split(".")[-1]
        element_type = (list_item_types or {}).get(bare_field, "money")
        return f"(sum {element_type} of {list_field_ref})"

    result = re.sub(r"\bsum\(\s*([a-zA-Z_][\w.]*)\s*\)", _rewrite_sum, result)

    # Steps 4–5: expand max/min iteratively until no nested calls remain.
    # A single pass expands outer calls but leaves inner ones in the arguments;
    # repeated passes catch those until the expression is fully expanded.
    prev = None
    while prev != result:
        prev = result
        result = _rewrite_binary_fn_to_if(result, "max", ">=", field_type)
        result = _rewrite_binary_fn_to_if(result, "min", "<=", field_type)

    # Step 6: &&  →  and
    result = re.sub(r"\s*&&\s*", " and ", result)

    # Step 7: ||  →  or
    result = re.sub(r"\s*\|\|\s*", " or ", result)

    # Step 8: !a → not a  (negative lookahead protects !=)
    result = re.sub(r"!(?!=)", "not ", result)

    # Step 9: == → =  (protected: does not touch !=, <=, >=)
    result = result.replace(" == ", " = ")

    # Step 10: Rewrite integer multiplication by money: (expr) * $N → $N * (decimal of (expr))
    result = re.sub(
        r"\(([^)]+)\)\s*\*\s*(\$[\d,]+)",
        r"\2 * (decimal of (\1))",
        result,
    )

    result = result.strip()

    # Step 10.5: In money context, rewrite `money_var * (int_var / int_var)` to safe decimal division.
    # Integer / integer division loses precision and fails at runtime when the denominator is 0.
    # Rewrite to: (if denom = 0 then $0 else money_var * (decimal of numer / decimal of denom))
    if field_type == "money":
        def _rewrite_int_ratio(m):
            money_expr = m.group(1)
            numerator = m.group(2).strip()
            denominator = m.group(3).strip()
            return (
                f"(if {denominator} = 0 then $0 "
                f"else {money_expr} * (decimal of {numerator} / decimal of {denominator}))"
            )
        # Only rewrite when numerator and denominator are field references (start with
        # a letter/underscore), not numeric literals like 2.0 or 3.0.
        result = re.sub(
            r"([a-zA-Z]\w*(?:\.\w+)*)\s*\*\s*\(([a-zA-Z_]\w*(?:\.\w+)*)\s*/\s*([a-zA-Z_]\w*(?:\.\w+)*)\)",
            _rewrite_int_ratio,
            result,
        )

    # Step 10.7: In money context, zero-guard a top-level division by an integer field.
    # Catala's test runner forces all scope outputs to evaluate, so an unused
    # computed field that divides by an int input will divide by 0 whenever the
    # test does not supply that input. Wrap the whole expression with a guard.
    # Matches `<paren-expr-or-simple-ident> / <bare-int-field>` only when it is
    # the entire expression -- divisions by integer literals (e.g. `... / 2`) and
    # nested infix arithmetic involving additional operators are left unchanged.
    if field_type == "money":
        div_match = re.match(
            r"^\s*(\([^()]*(?:\([^()]*\)[^()]*)*\)|[a-zA-Z_]\w*(?:\.\w+)*)"
            r"\s*/\s*"
            r"([a-zA-Z_]\w*(?:\.\w+)*)\s*$",
            result,
        )
        if div_match:
            numerator_expr = div_match.group(1).strip()
            int_denom = div_match.group(2).strip()
            result = (
                f"(if {int_denom} = 0 then $0 "
                f"else {numerator_expr} / {int_denom})"
            )

    # Step 11: Convert bare "0" to "$0" for money-typed fields
    if field_type == "money" and result == "0":
        result = "$0"

    # Step 12: Convert string literals to enum constructors (Pascal case).
    # Catala has no string/text type; any quoted identifier in a CIVIL expression
    # must be an enum variant (e.g. "deny" → Deny, "manual_verification" → ManualVerification).
    result = re.sub(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', lambda m: _to_catala_constructor(m.group(1)), result)

    # Step 12.5: in(VAR, [V1, V2, V3]) → [V1; V2; V3] contains VAR
    # CIVIL membership form. Catala has no `in` builtin; the equivalent is a list
    # literal (with `;` separators, not `,`) followed by `contains` and the value.
    def _rewrite_in_membership(m):
        var_expr = m.group(1).strip()
        items = [item.strip() for item in m.group(2).split(",") if item.strip()]
        return f"[{'; '.join(items)}] contains {var_expr}"

    result = re.sub(
        r"\bin\(\s*([a-zA-Z_][\w.]*)\s*,\s*\[([^\[\]]+)\]\s*\)",
        _rewrite_in_membership,
        result,
    )

    # Step 13: In money context, coerce bare integers in arithmetic positions to money literals.
    # Handles both left-side (`20 - expr` → `$20 - expr`) and right-side (`expr - 65` → `expr - $65`).
    # Guards: not preceded by $ or , (already a money literal or thousands-separator digit);
    # not followed by . , digit or % (decimal/formatted); not inside a variable name
    # (word-boundary \b ensures `after_general_20` is unaffected).
    if field_type == "money":
        # 13a: bare integer as LEFT operand of + / -
        # Also guard against preceding comma (e.g. the '867' in '$5,867' must not be re-prefixed)
        result = re.sub(
            r'(?<![,\$])(\b\d+\b)(?![,.\d%])\s*(?=[+\-])',
            lambda m: f'${int(m.group(1)):,}',
            result,
        )
        # 13b: bare integer as RIGHT operand of + / -
        # Guard: not followed by , . digit or % (to avoid mangling percentage literals like 50%)
        result = re.sub(
            r'([+\-])\s*(\d+)(?![,.\d%])',
            lambda m: f'{m.group(1)} ${int(m.group(2)):,}',
            result,
        )
        # 13c: bare integer following `then` or `else` (conditional branches in money context).
        # Catala requires both branches of `if … then X else Y` to share a type, so a bare
        # integer like `0` is rejected when the surrounding field is money. Coerce so policy
        # authors can write `else 0` in CIVIL and get the correct Catala emit. Guards mirror
        # Steps 13a/13b: not preceded by `$` (already money), not followed by `, . digit %`
        # (decimal, thousands sep, or percentage). `\b` ensures we don't match inside
        # identifiers like `else_value`.
        result = re.sub(
            r'(?<!\$)\b(then|else)\s+(\d+)\b(?![,.\d%])',
            lambda m: f'{m.group(1)} ${int(m.group(2)):,}',
            result,
        )
        # Step 13.9: Strip money-literal prefix ($) that Step 13b incorrectly added inside
        # `decimal of (...)` blocks generated by Step 10. Those blocks wrap integer arithmetic
        # used as multipliers — the sub-expressions are integer context, not money context.
        # e.g. `decimal of (household_size - $8)` → `decimal of (household_size - 8)`
        result = re.sub(
            r'decimal of \(([^)]+)\)',
            lambda m: 'decimal of (' + m.group(1).replace('$', '') + ')',
            result,
        )

    # Step 14: Detect incompatible branch types in inner if…then…else expressions.
    # Runs after all coercions so Step 13c has already resolved bare-int → $N, meaning
    # `else $0` already reads as money and won't trigger a false mismatch.
    # Only fires when field_type_map is provided (opt-in; backward compatible).
    # Scans for identifiers in `then`/`else` positions that are not the left-hand side
    # of arithmetic (negative lookahead). Collects all distinct known types; two or more
    # distinct types indicates an incompatible branch somewhere in the expression.
    if field_type_map:
        branch_ident_types: set[str] = set()
        for branch_ident in re.findall(
            r'\b(?:then|else)\s+([A-Za-z_]\w*)\b(?!\s*[+\-*/(])',
            result,
        ):
            inferred = _infer_civil_type(branch_ident, field_type_map)
            if inferred is not None:
                branch_ident_types.add(inferred)
        if len(branch_ident_types) > 1:
            types_str = " vs ".join(sorted(branch_ident_types))
            raise ValueError(
                f"Conditional expression has incompatible types: {types_str}.\n"
                f"  Expression: \"{expr}\"\n"
                "Catala requires both branches of if…then…else to share the same type.\n"
                "Ensure all then/else branches reference fields of the same type."
            )

    return result


def translate_condition_to_catala(when_expr: str, constants: dict = None, tables: dict = None, fact_entities: set = None, invoke_bound_entities: set = None, list_item_types: dict = None, field_type_map: dict = None) -> str:
    """Translate a CIVIL when: condition to a Catala condition expression string."""
    if when_expr.strip() == "true":
        return "true"
    return translate_expr_to_catala(when_expr, constants=constants, tables=tables, fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities, list_item_types=list_item_types, field_type_map=field_type_map)


# =============================================================================
# TABLE FIELD DETECTION
# =============================================================================

def _uses_variable_table(field_def: dict) -> bool:
    """Return True if this computed field uses a variable-key table lookup.

    Checks both expr: and conditional.then:.
    A 'variable key' is any key argument that is not a plain integer literal.
    """
    # Check expr: field
    if "expr" in field_def:
        # Function-call syntax: table('name', key1, key2).col_name
        m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.\w+", field_def["expr"])
        if m:
            keys = [k.strip() for k in m.group(2).split(",")]
            if any(not k.isdigit() for k in keys):
                return True
        # Bracket subscript syntax: table_name[key]
        m = re.search(r"(\w+)\[(\w+)\]", field_def["expr"])
        if m and not m.group(2).isdigit():
            return True
    # Check conditional.then:
    if "conditional" in field_def:
        then_expr = field_def["conditional"].get("then", "")
        # Function-call syntax: table('name', key1, key2).col_name
        m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.\w+", then_expr)
        if m:
            keys = [k.strip() for k in m.group(2).split(",")]
            if any(not k.isdigit() for k in keys):
                return True
        # Bracket subscript syntax: table_name[key]
        m = re.search(r"(\w+)\[(\w+)\]", then_expr)
        if m and not m.group(2).isdigit():
            return True
    return False


def _extract_table_info(field_def: dict) -> tuple:
    """Extract (table_name, key_exprs) where key_exprs is a list of variable names.

    Checks expr: first, then conditional.then:.
    Returns (None, None) if no variable table lookup found.
    """
    # Check expr: first
    if "expr" in field_def:
        # Function-call syntax: table('name', key1, key2).col
        m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.\w+", field_def["expr"])
        if m:
            keys = [k.strip() for k in m.group(2).split(",")]
            if any(not k.isdigit() for k in keys):
                return m.group(1), keys
        # Bracket subscript syntax: table_name[key]
        m = re.search(r"(\w+)\[(\w+)\]", field_def["expr"])
        if m and not m.group(2).isdigit():
            return m.group(1), [m.group(2)]
    # Check conditional.then:
    if "conditional" in field_def:
        then_expr = field_def["conditional"].get("then", "")
        # Function-call syntax: table('name', key1, key2).col
        m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.\w+", then_expr)
        if m:
            keys = [k.strip() for k in m.group(2).split(",")]
            if any(not k.isdigit() for k in keys):
                return m.group(1), keys
        # Bracket subscript syntax: table_name[key]
        m = re.search(r"(\w+)\[(\w+)\]", then_expr)
        if m and not m.group(2).isdigit():
            return m.group(1), [m.group(2)]
    return None, None


# =============================================================================
# EMITTERS
# =============================================================================

def _build_shared_enum_map(
    facts: dict,
    decisions: dict,
    tables: dict,
    cross_module_enums: dict = None,
) -> dict[frozenset, str]:
    """Map {frozenset(values): canonical_enum_type} so fields with identical value
    sets share a single Catala enum.

    Cross-module enums take precedence: when a sub-module already declares an enum
    with the same variants, the qualified type (e.g.
    "Program_standards.MedicaidCategoryRequested") is canonical so parent fields
    can bind-forward to the sub-module without type incompatibility.

    Otherwise the first-encountered local field name becomes canonical; subsequent
    fields with the same value-set reuse it. Walk order matches emit_declarations'
    enum-declaration order so canonical names correspond to the field that would
    have declared the enum first under the old (non-deduped) code path.

    Pure calculation — no I/O.
    """
    result: dict[frozenset, str] = {}

    # Cross-module enums win: their qualified type is the canonical one.
    for _field_name, (qualified_type, variants) in (cross_module_enums or {}).items():
        key = frozenset(str(v) for v in variants)
        if key and key not in result:
            result[key] = qualified_type

    def _register_local(field_name: str, values: list) -> None:
        key = frozenset(str(v) for v in values)
        if key and key not in result:
            result[key] = snake_to_pascal(field_name)

    for entity_def in (facts or {}).values():
        for field_name, field_def in (entity_def.get("fields") or {}).items():
            if isinstance(field_def, dict) and field_def.get("type") == "enum":
                _register_local(field_name, field_def.get("values") or [])
    for entity_def in (facts or {}).values():
        for field_name, field_def in (entity_def.get("fields") or {}).items():
            if not isinstance(field_def, dict) or field_def.get("type") != "string":
                continue
            vals = _collect_string_enum_values(field_name, tables) or field_def.get("values") or []
            _register_local(field_name, vals)
    for field_name, field_def in (decisions or {}).items():
        if isinstance(field_def, dict) and field_def.get("type") == "string":
            _register_local(field_name, field_def.get("values") or [])
    return result


def emit_declarations(doc: dict, scope_name: str, sub_module_docs: dict = None) -> list[str]:
    """Emit the Declarations catala block: enums, ReasonCode, scope decl.

    For invoke-bound entities (those that appear in any invoke: bind: value),
    emit a Catala structure declaration and declare a single struct input.
    Non-invoke-bound entities are flattened to individual scope inputs as before.
    Constants are NOT emitted as top-level declarations; they are inlined into
    expressions by translate_expr_to_catala.
    """
    lines = []
    facts = doc.get("inputs", {})
    computed = doc.get("computed", {})
    decisions = doc.get("outputs", {})
    deny_rules = [r for r in doc.get("rules", []) if r.get("kind") == "deny"]
    sub_module_docs = sub_module_docs or {}

    # Compute which entities are invoke-bound (appear as values in any bind: dict)
    invoke_bound_entities: set[str] = set()
    for field_def in computed.values():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            invoke_field = field_def["invoke"]
            bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
            invoke_bound_entities.update(bind.values())

    tables = doc.get("tables", {})

    # Build cross-module enum map: fields whose enum type is defined in a sub-module's
    # constant tables.  Used below to emit qualified type references instead of "integer"
    # and to skip re-declaring those enums locally (they live in the sub-module).
    cross_module_enums = build_cross_module_enums(sub_module_docs)

    # Enum dedup: fields with identical `values:` lists share one Catala enum.
    # Cross-module enums (declared in a sub-module) win — the canonical type is the
    # qualified "Module.EnumName" so parent fields bind-forward to sub-modules without
    # type incompatibility. Otherwise the first-encountered local field names the enum.
    shared_enum_map = _build_shared_enum_map(facts, decisions, tables, cross_module_enums)
    _emitted_enum_names: set[str] = set()

    def _canonical_enum_for(values: list) -> str | None:
        return shared_enum_map.get(frozenset(str(v) for v in values))

    def _is_cross_module(enum_type: str | None) -> bool:
        return bool(enum_type) and "." in enum_type

    # --- Enumeration declarations for ALL enum-typed fact fields (must precede structs) ---
    for entity_name, entity_def in facts.items():
        for field_name, field_def in entity_def.get("fields", {}).items():
            if field_def.get("type") == "enum":
                values = field_def.get("values", [])
                enum_name = _canonical_enum_for(values) or snake_to_pascal(field_name)
                if enum_name in _emitted_enum_names or _is_cross_module(enum_name):
                    continue
                lines.append(f"declaration enumeration {enum_name}:")
                for v in values:
                    lines.append(f"  -- {_to_catala_constructor(v)}")
                lines.append("")
                _emitted_enum_names.add(enum_name)

    # --- Enumeration declarations for string-typed fact fields used as table keys ---
    # Catala has no native text/string type; string fields that serve as table lookup
    # keys, or carry an explicit values: list, are represented as enumerations.
    # Fields whose enum is defined in a sub-module (cross_module_enums) are skipped here
    # because they use a qualified type reference and are already declared in the sub-module.
    _emitted_string_enums: set = set()
    for entity_name, entity_def in facts.items():
        for field_name, field_def in entity_def.get("fields", {}).items():
            if field_def.get("type") == "string" and field_name not in _emitted_string_enums:
                table_vals = _collect_string_enum_values(field_name, tables)
                decl_vals = field_def.get("values") or []
                values = table_vals or decl_vals
                # If a sub-module already declares this enum (same field name, matching variants),
                # the parent must NOT redeclare it — bind forwarding needs both sides to share
                # one Catala type, not two structurally-identical but distinct types.
                if field_name in cross_module_enums and (
                    not values or sorted(values) == sorted(cross_module_enums[field_name][1])
                ):
                    _emitted_string_enums.add(field_name)
                    continue
                if values:
                    enum_name = _canonical_enum_for(values) or snake_to_pascal(field_name)
                    if enum_name not in _emitted_enum_names and not _is_cross_module(enum_name):
                        lines.append(f"declaration enumeration {enum_name}:")
                        for v in values:
                            lines.append(f"  -- {_to_catala_constructor(v)}")
                        lines.append("")
                        _emitted_enum_names.add(enum_name)
                    _emitted_string_enums.add(field_name)

    # --- Enumeration declarations for string-typed decisions with values: (must precede scope decl) ---
    for field_name, field_def in decisions.items():
        if field_def.get("type") == "string" and field_def.get("values"):
            values = field_def["values"]
            enum_name = _canonical_enum_for(values) or snake_to_pascal(field_name)
            if enum_name in _emitted_enum_names or _is_cross_module(enum_name):
                continue
            lines.append(f"declaration enumeration {enum_name}:")
            for v in values:
                lines.append(f"  -- {_to_catala_constructor(v)}")
            lines.append("")
            _emitted_enum_names.add(enum_name)

    # --- Structure declarations for invoke-bound entities ---
    for entity_name, entity_def in facts.items():
        if entity_name not in invoke_bound_entities:
            continue
        lines.append(f"declaration structure {entity_name}:")
        for field_name, field_def in entity_def.get("fields", {}).items():
            ftype = field_def.get("type", "money")
            if ftype == "enum":
                catala_type = _canonical_enum_for(field_def.get("values") or []) or snake_to_pascal(field_name)
            elif ftype == "string":
                local_vals = _collect_string_enum_values(field_name, tables)
                local_decl = field_def.get("values")
                values = local_vals or local_decl
                # Prefer the sub-module's qualified enum when both sides agree on variants;
                # otherwise the parent's local enum and the sub-module's enum become
                # incompatible Catala types at bind-forwarding sites.
                if field_name in cross_module_enums and (
                    not values or sorted(values) == sorted(cross_module_enums[field_name][1])
                ):
                    catala_type = cross_module_enums[field_name][0]
                elif values:
                    catala_type = _canonical_enum_for(values) or snake_to_pascal(field_name)
                elif field_name in cross_module_enums:
                    catala_type = cross_module_enums[field_name][0]
                elif field_def.get("optional"):
                    # Free-form optional string with no enum variants — Catala has no usable
                    # text input type, so omit the field from the struct declaration entirely.
                    continue
                else:
                    raise ValueError(
                        f"Field '{field_name}' in structure '{entity_name}' has type 'string' "
                        f"with no enum variants (no table key, no values:, no cross-module enum) "
                        f"and is not marked optional. Either declare a values: list, use the "
                        f"field as a table key, mark it optional: true, or change the field type."
                    )
            else:
                catala_type = field_to_catala_type(field_def)
            lines.append(f"  data {field_name} content {catala_type}")
        lines.append("")

    # --- ReasonCode enumeration (for any rules with add_reason, or list-typed outputs) ---
    # Collect reason codes from ALL rules (allow and deny) so sub-modules with only allow
    # rules (e.g. pure computation chains) still get a valid ReasonCode enum declaration.
    all_rules = doc.get("rules", [])
    all_reason_codes = [
        action["add_reason"]["code"]
        for rule in all_rules
        for action in rule.get("then", [])
        if "add_reason" in action
    ]
    has_list_output = any(v.get("type") in ("list", "set") for v in decisions.values())
    if all_reason_codes or has_list_output:
        lines.append("declaration enumeration ReasonCode:")
        for code in all_reason_codes:
            lines.append(f"  -- {reason_code_to_pascal(code)}")
        lines.append("")

        # ReasonEntry structure is only needed when deny rules exist — it is the
        # intermediate triggered/code pair used to build the deny-reasons list.
        if deny_rules:
            lines.append("declaration structure ReasonEntry:")
            lines.append("  data triggered content boolean")
            lines.append("  data code content ReasonCode")
            lines.append("")

    # --- Scope declaration ---
    lines.append(f"declaration scope {scope_name}:")

    # Inputs: invoke-bound entities as struct inputs; others flattened
    for entity_name, entity_def in facts.items():
        if entity_name in invoke_bound_entities:
            # snake_case variable name for the struct input
            var_name = re.sub(r"(?<!^)(?=[A-Z])", "_", entity_name).lower()
            lines.append(f"  input {var_name} content {entity_name}")
        else:
            for field_name, field_def in entity_def.get("fields", {}).items():
                ftype = field_def.get("type", "money")
                if ftype == "enum":
                    catala_type = _canonical_enum_for(field_def.get("values") or []) or snake_to_pascal(field_name)
                elif ftype == "string":
                    table_vals = _collect_string_enum_values(field_name, tables)
                    decl_vals = field_def.get("values") or []
                    values = table_vals or decl_vals
                    if field_name in cross_module_enums and (
                        not values or sorted(values) == sorted(cross_module_enums[field_name][1])
                    ):
                        catala_type = cross_module_enums[field_name][0]
                    elif values:
                        catala_type = _canonical_enum_for(values) or snake_to_pascal(field_name)
                    elif field_name in cross_module_enums:
                        catala_type = cross_module_enums[field_name][0]
                    elif _scope_input_omits_field(field_def, tables=tables, field_name=field_name):
                        continue
                    else:
                        raise ValueError(
                            f"Field '{field_name}' (scope input in '{entity_name}') has type "
                            f"'string' with no enum variants (no table key, no values:, no "
                            f"cross-module enum) and is not marked optional. Either declare a "
                            f"values: list, use the field as a table key, mark it optional: true, "
                            f"or change the field type."
                        )
                else:
                    catala_type = field_to_catala_type(field_def)
                optional_note = "  # optional" if field_def.get("optional") else ""
                lines.append(f"  input {field_name} content {catala_type}{optional_note}")

    # Pass 1: internal computed fields (non-invoke, non-output-tagged)
    # CIVIL v3: fields with tags: [expose] are promoted to output — handled in pass 2.
    # bool fields with `expr:` use Catala `condition` syntax (rule/fulfilled) which cannot
    # be `output` unless tags: [expose], in which case `output content boolean` + definition/equals.
    # CIVIL v4: invoke: fields are subscope references — handled in pass 2.
    for field_name, field_def in computed.items():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            continue  # subscope output — pass 2
        ftype = field_def.get("type", "money")
        is_output = "expose" in (field_def.get("tags") or [])
        if is_output:
            continue  # tagged output — pass 2
        if ftype == "bool" and "expr" in field_def:
            # condition kind: uses rule/fulfilled syntax
            lines.append(f"  internal {field_name} condition")
        elif ftype == "bool":
            lines.append(f"  internal {field_name} content boolean")
        else:
            lines.append(f"  internal {field_name} content {civil_type_to_catala(ftype)}")

    # internals: one deny_rule_N_triggered condition per deny rule
    for i, _ in enumerate(deny_rules, 1):
        lines.append(f"  internal deny_rule_{i}_triggered condition")

    # internal all_reason_entries (if deny rules exist)
    if deny_rules:
        lines.append("  internal all_reason_entries content list of ReasonEntry")

    # Pass 2: computed output fields — subscopes first, then tags: [output] fields
    lines.append("  # ── Computed outputs ──")
    # 2a: subscope (invoke:) fields
    for field_name, field_def in computed.items():
        if not (isinstance(field_def, dict) and field_def.get("invoke")):
            continue
        sub_module_name = field_def.get("module", "")
        if not sub_module_name:
            print(f"ERROR: computed field '{field_name}' uses invoke: but has no module: — cannot emit scope declaration", file=sys.stderr)
            continue
        catala_mod_name = sub_module_name[0].upper() + sub_module_name[1:]
        sub_doc = sub_module_docs.get(sub_module_name, {})
        scope_decision = snake_to_pascal(sub_doc.get("module", sub_module_name).split(".")[0]) + "Decision"
        lines.append(f"  output {field_name} scope {catala_mod_name}.{scope_decision}")
    # 2b: tags: [expose] fields (in CIVIL order)
    for field_name, field_def in computed.items():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            continue  # already emitted in 2a
        ftype = field_def.get("type", "money")
        is_output = "expose" in (field_def.get("tags") or [])
        if not is_output:
            continue
        if ftype == "bool" and "expr" in field_def:
            # Output boolean with expr: use content boolean + definition/equals in body
            lines.append(f"  output {field_name} content boolean")
        elif ftype == "bool":
            lines.append(f"  output {field_name} content boolean")
        else:
            lines.append(f"  output {field_name} content {civil_type_to_catala(ftype)}")

    # Decisions: all output
    lines.append("  # ── Decisions ──")
    for field_name, field_def in decisions.items():
        ftype = field_def.get("type", "bool")
        if ftype == "bool":
            lines.append(f"  output {field_name} content boolean")
        elif ftype == "list":
            lines.append(f"  output {field_name} content list of ReasonCode")
        elif ftype == "string" and field_def.get("values"):
            output_enum = _canonical_enum_for(field_def["values"]) or snake_to_pascal(field_name)
            lines.append(f"  output {field_name} content {output_enum}")
        elif ftype == "string":
            pass  # Skip free-form string outputs — Catala has no native string type
        else:
            lines.append(f"  output {field_name} content {civil_type_to_catala(ftype)}")

    return lines


def emit_subscope_wiring(
    computed: dict,
    scope_name: str,
    sub_module_docs: dict,
    parent_inputs: dict = None,
    parent_context: dict = None,
) -> list[tuple]:
    """Emit wiring blocks for invoke: computed fields.

    Returns a list of (field_name, description, source, code_lines) tuples —
    one per invoke: field. Each code_lines list contains the scope block that
    maps parent entity struct fields to the subscope's input fields.

    When parent_inputs is provided, optional sub-module fields absent from the
    parent entity receive zero/empty defaults ($0, [], false) rather than a
    forwarding reference to a non-existent parent field.

    When parent_context is provided, field_bind: entries are translated via
    translate_expr_to_catala using the parent's constants, tables, fact_entities,
    and invoke_bound_entities.
    """
    chunks = []
    for field_name, invoke_field_def in computed.items():
        if not isinstance(invoke_field_def, dict) or not invoke_field_def.get("invoke"):
            continue
        sub_module_name = invoke_field_def.get("module", "")
        sub_doc = sub_module_docs.get(sub_module_name, {})
        invoke_field = invoke_field_def["invoke"]
        bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
        field_bind = (invoke_field.get("field_bind", {}) or {}) if isinstance(invoke_field, dict) else {}

        # Warn when field_bind entries are present but no parent context was supplied —
        # entity prefixes in field_bind expressions will not be stripped.
        if field_bind and parent_context is None:
            print(
                f"WARNING: emit_subscope_wiring: field_bind entries present for "
                f"{field_name!r} but parent_context is None — entity prefixes will "
                f"not be rewritten in field_bind expressions.",
                file=sys.stderr,
            )

        # Collect all definitions for this invoke field in ONE scope block.
        sub_tables = sub_doc.get("tables", {})
        definitions = []
        for sub_entity, parent_entity in bind.items():
            sub_entity_fields = (
                sub_doc.get("inputs", {}).get(sub_entity, {}).get("fields", {})
            )
            parent_entity_fields = (
                set((parent_inputs or {}).get(parent_entity, {}).get("fields", {}).keys())
                if parent_inputs is not None else None
            )
            parent_var = re.sub(r"(?<!^)(?=[A-Z])", "_", parent_entity).lower()
            # Fields covered by field_bind for THIS entity — scoped to avoid
            # suppressing same-named fields on other entities in multi-entity binds.
            entity_field_bind_covered = field_bind.get(sub_entity) or {}
            for field, sub_field_def in sub_entity_fields.items():
                if _scope_input_omits_field(sub_field_def, tables=sub_tables, field_name=field):
                    continue
                # Skip bind-forwarding for fields explicitly provided by field_bind:
                # emitting both would produce a Catala definition conflict.
                if field in entity_field_bind_covered:
                    continue
                if (
                    parent_entity_fields is not None
                    and field not in parent_entity_fields
                    and isinstance(sub_field_def, dict)
                    and sub_field_def.get("optional")
                ):
                    # Parent entity lacks this optional field — emit a zero/empty default
                    # so the Catala struct is fully initialized without a forward reference
                    # to a non-existent parent field.
                    civil_type = sub_field_def.get("type", "money")
                    default_val = _default_catala_literal(civil_type)
                    definitions.append(
                        f"  definition {field_name}.{field} equals {default_val}"
                    )
                else:
                    definitions.append(
                        f"  definition {field_name}.{field} equals {parent_var}.{field}"
                    )

        # Emit field_bind: explicit per-field forwarding lines.
        # sub_entity is the sub-module entity name — used only for CIVIL grouping;
        # Catala subscope field definitions are entity-independent at this level.
        ctx = parent_context or {}
        for sub_entity, sub_field_map in field_bind.items():
            for sub_field, parent_expr in sub_field_map.items():
                catala_expr = translate_expr_to_catala(
                    parent_expr,
                    constants=ctx.get("constants"),
                    tables=ctx.get("tables"),
                    fact_entities=ctx.get("fact_entities"),
                    invoke_bound_entities=ctx.get("invoke_bound_entities"),
                    list_item_types=ctx.get("list_item_types"),
                    field_type_map=ctx.get("field_type_map"),
                )
                definitions.append(
                    f"  definition {field_name}.{sub_field} equals {catala_expr}"
                )

        if definitions:
            code_lines = [f"scope {scope_name}:"] + definitions + [""]
            chunks.append((
                field_name,
                invoke_field_def.get("description"),
                invoke_field_def.get("source"),
                code_lines,
            ))

    return chunks


def _collect_string_enum_values(field_name: str, tables: dict) -> list:
    """Collect distinct string values for a field from table rows where it is a key column."""
    values: list = []
    for table_def in tables.values():
        if field_name in table_def.get("key", []):
            for row in table_def.get("rows", []):
                val = row.get(field_name)
                if val is not None and isinstance(val, str) and val not in values:
                    values.append(val)
    return values


def _scope_input_omits_field(field_def: dict, *, tables: dict, field_name: str) -> bool:
    """True when this field is omitted from Catala scope/struct declarations.

    Ticket 11 rule: optional + type:string + no enum variants
    (no values: list, no constants-table column).
    """
    if field_def.get("type") != "string":
        return False
    if not field_def.get("optional"):
        return False
    has_enum_values = bool(
        _collect_string_enum_values(field_name, tables)
        or field_def.get("values")
    )
    return not has_enum_values


def build_cross_module_enums(sub_module_docs: dict) -> dict[str, tuple[str, list]]:
    """Scan loaded sub-module CIVIL docs for string fields inferred as enums via table keys.

    Returns {field_name: (qualified_catala_type, variants)} for each field where at
    least one sub-module's constants table infers an enum.  The qualified type includes
    the Catala module prefix (e.g. "Program_standards_lookup.HouseholdType").

    Layer 2 divergence check: fails fast if two sub-modules infer DIFFERENT variant sets
    for the same field name, naming both modules and both variant sets.

    Pure calculation — no file I/O beyond what's already in sub_module_docs.
    """
    result: dict = {}
    for sub_name, sub_doc in (sub_module_docs or {}).items():
        catala_sub_name = sub_name[0].upper() + sub_name[1:]
        sub_tables = sub_doc.get("tables", {})
        sub_inputs = sub_doc.get("inputs", {})

        for entity_def in sub_inputs.values():
            for field_name, field_def in (entity_def.get("fields") or {}).items():
                if not isinstance(field_def, dict) or field_def.get("type") != "string":
                    continue
                local_vals = _collect_string_enum_values(field_name, sub_tables)
                if not local_vals:
                    local_vals = [str(v) for v in (field_def.get("values") or [])]
                if not local_vals:
                    continue

                qualified_type = f"{catala_sub_name}.{snake_to_pascal(field_name)}"

                if field_name in result:
                    existing_type, existing_vals = result[field_name]
                    if sorted(existing_vals) != sorted(local_vals):
                        existing_module = existing_type.split(".")[0]
                        fail(
                            f"Field {field_name!r} is inferred as different enums across sub-modules:\n"
                            f"  - {existing_module}: variants {sorted(existing_vals)}\n"
                            f"  - {catala_sub_name}: variants {sorted(local_vals)}\n"
                            f"Choose one variant set and declare explicit `values:` in any module "
                            f"that diverges, or rename one of the fields."
                        )
                else:
                    result[field_name] = (qualified_type, local_vals)

    return result


def _format_key_condition(key_var: str, key_val) -> str:
    """Format a single table key condition.

    String values use Catala pattern syntax: 'var with pattern Value'
    (for enumeration variants). Date values use Catala's `|YYYY-MM-DD|`
    date literal. Integer/float values use equality: 'var = N'.
    """
    if isinstance(key_val, str):
        return f"{key_var} with pattern {_to_catala_constructor(key_val)}"
    if isinstance(key_val, (datetime.date, datetime.datetime)):
        return f"{key_var} = |{key_val.isoformat()[:10]}|"
    if isinstance(key_val, (int, float)):
        return f"{key_var} = {key_val}"
    raise ValueError(
        f"_format_key_condition: unsupported key value type {type(key_val).__name__!r} "
        f"for {key_var}={key_val!r}. Expected str (enum variant), datetime.date, "
        f"or numeric. Fix the table row in CIVIL or extend this helper."
    )


def _substitute_row_into_expr(
    expr: str,
    table_name: str,
    table_def: dict,
    row: dict,
    field_type: str,
    constants: dict,
    tables: dict,
    fact_entities: set,
    invoke_bound_entities: set,
    list_item_types: dict = None,
) -> str:
    """Compute the Catala consequence for one table row given an expression.

    If expr is a bare table lookup (table('name', key).col), return the row value
    as a literal directly. Otherwise substitute all value-column table references
    with their row literals and translate the full expression — this handles cases
    where a table value is wrapped inside a larger expression (e.g. min(var, table(...))).
    """
    # Function-call syntax: table('name', key).col
    pure_m = re.match(r"^table\('\w+',\s*[^)]+\)\.(\w+)$", expr.strip())
    if pure_m:
        col_name = pure_m.group(1)
        raw = row.get(col_name)
        if field_type == "money":
            return money_literal(raw)
        if field_type == "string":
            return _to_catala_constructor(str(raw))
        return str(raw)
    # Bracket subscript syntax: table_name[key] — column is implicit (first value column)
    if re.match(r"^\w+\[\w+\]$", expr.strip()):
        col_name = table_def["value"][0]
        raw = row.get(col_name)
        if field_type == "money":
            return money_literal(raw)
        if field_type == "string":
            return _to_catala_constructor(str(raw))
        return str(raw)
    # Complex expression: substitute each value column with its row literal, then translate
    subst = expr
    for col_name in table_def.get("value", []):
        col_val = row.get(col_name)
        if col_val is None:
            print(f"ERROR: table '{table_name}' row is missing value for column '{col_name}' — table reference will remain in output and cause Catala syntax error", file=sys.stderr)
            continue
        if isinstance(col_val, (int, float)):
            col_lit = money_literal(col_val)
        elif isinstance(col_val, str):
            col_lit = _to_catala_constructor(col_val)
        else:
            col_lit = str(col_val)
        # Function-call syntax: table('name', key).col
        subst = re.sub(
            rf"table\('{re.escape(table_name)}',\s*[^)]+\)\.{re.escape(col_name)}",
            col_lit,
            subst,
        )
        # Bracket subscript syntax: table_name[key]
        subst = re.sub(rf"{re.escape(table_name)}\[\w+\]", col_lit, subst)
    return translate_expr_to_catala(
        subst, constants=constants, field_type=field_type, tables=tables,
        fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
        list_item_types=list_item_types,
    )


def emit_table_definition(
    field_name: str,
    field_def: dict,
    table_name: str,
    key_exprs: list,
    table_def: dict,
    scope_name: str,
    constants: dict,
    tables: dict,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
) -> list[str]:
    """Emit stacked 'under condition' definitions for one table-driven computed field.

    key_exprs is a list of scope variable names corresponding to the table key columns.
    Handles multi-key tables and enum key values (uses 'with pattern' syntax).

    When the field has a conditional: block, the if-guard is AND-ed with each row's key
    condition. If the else branch is also a table lookup, a second set of rows is emitted
    for the else branch (negated if-guard AND each key condition).
    """
    lines = []
    key_cols = table_def["key"]   # list of key column names from CIVIL YAML
    val_col = table_def["value"][0]
    rows = table_def.get("rows", [])
    field_type = field_def.get("type", "money")

    # Override val_col if conditional.then specifies a particular column via table('...').col
    if "conditional" in field_def:
        then_expr = field_def["conditional"].get("then", "")
        then_m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.(\w+)", then_expr)
        if then_m:
            val_col = then_m.group(3)

    # Translate the conditional.if guard (if any) — AND-ed into every primary row condition
    if_catala = None
    if "conditional" in field_def:
        if_expr_raw = field_def["conditional"].get("if", "true")
        if if_expr_raw != "true":
            if_catala = translate_expr_to_catala(
                if_expr_raw, constants=constants, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types,
            )

    # Source expression for each row's consequence (then: branch or expr:)
    source_expr = (
        field_def["conditional"]["then"] if "conditional" in field_def
        else field_def.get("expr", f"table('{table_name}', _).{val_col}")
    )

    for row in rows:
        catala_val = _substitute_row_into_expr(
            source_expr, table_name, table_def, row, field_type,
            constants, tables, fact_entities or set(), invoke_bound_entities or set(),
            list_item_types=list_item_types,
        )

        # Build compound condition: optional if-guard AND all key columns
        cond_parts = []
        if if_catala:
            cond_parts.append(if_catala)
        for i, key_col in enumerate(key_cols):
            key_val = row[key_col]
            key_var = key_exprs[i] if i < len(key_exprs) else key_col
            cond_parts.append(_format_key_condition(key_var, key_val))
        cond = " and ".join(cond_parts)

        lines.append(f"scope {scope_name}:")
        lines.append(f"  definition {field_name}")
        lines.append(f"    under condition {cond}")
        lines.append(f"    consequence equals {catala_val}")
        lines.append("")

    # Emit the fallback (else) case if the field is a conditional:
    if "conditional" in field_def:
        cond_block = field_def["conditional"]
        else_expr = cond_block.get("else", "")

        # Negate the if condition for the else branch
        else_cond = negate_simple_condition(if_catala) if if_catala else "true"

        # Check if else is also a variable table lookup.
        # Literal-key lookups (e.g. table('name', 8).col) are NOT row-iterated — they are
        # handled by translate_expr_to_catala (Step 2) which resolves them to a single value.
        # Function-call syntax: table('name', key).col
        else_table_m = re.search(r"table\('(\w+)',\s*([^)]+)\)\.(\w+)", else_expr)
        # Bracket subscript syntax: table_name[key]
        else_bracket_m = re.search(r"(\w+)\[(\w+)\]", else_expr) if not else_table_m else None
        is_variable_else_table = (
            (else_table_m and not else_table_m.group(2).strip().lstrip("-").isdigit()) or
            (else_bracket_m and not else_bracket_m.group(2).isdigit())
        )
        if is_variable_else_table:
            else_table_name = else_table_m.group(1) if else_table_m else else_bracket_m.group(1)
            else_table_def = tables.get(else_table_name, table_def)
            for row in else_table_def.get("rows", []):
                catala_val = _substitute_row_into_expr(
                    else_expr, else_table_name, else_table_def, row, field_type,
                    constants, tables, fact_entities or set(), invoke_bound_entities or set(),
                    list_item_types=list_item_types,
                )
                cond_parts = []
                if else_cond != "true":
                    cond_parts.append(else_cond)
                for i, key_col in enumerate(key_cols):
                    key_val = row[key_col]
                    key_var = key_exprs[i] if i < len(key_exprs) else key_col
                    cond_parts.append(_format_key_condition(key_var, key_val))
                cond = " and ".join(cond_parts)
                lines.append(f"scope {scope_name}:")
                lines.append(f"  definition {field_name}")
                lines.append(f"    under condition {cond}")
                lines.append(f"    consequence equals {catala_val}")
                lines.append("")
        else:
            # Simple else expression: one fallback definition
            else_catala = translate_expr_to_catala(
                else_expr,
                constants=constants,
                field_type=field_type,
                tables=tables,
                fact_entities=fact_entities,
                invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types,
            )
            lines.append(f"scope {scope_name}:")
            lines.append(f"  definition {field_name}")
            lines.append(f"    under condition {else_cond}")
            lines.append(f"    consequence equals {else_catala}")
            lines.append("")

    return lines


def emit_table_definition_elseif(
    field_name: str,
    field_def: dict,
    table_name: str,
    key_exprs: list,
    table_def: dict,
    scope_name: str,
    constants: dict,
    tables: dict,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
) -> list[str]:
    """Emit a single if/else if/else chain definition for a table-driven computed field.

    key_exprs is a list of scope variable names corresponding to the table key columns.
    """
    lines = []
    key_cols = table_def["key"]
    val_col = table_def["value"][0]
    rows = table_def.get("rows", [])
    field_type = field_def.get("type", "money")

    lines.append(f"scope {scope_name}:")
    lines.append(f"  definition {field_name} equals")

    source_expr = (
        field_def["conditional"]["then"] if "conditional" in field_def
        else field_def.get("expr", f"table('{table_name}', _).{val_col}")
    )

    for i, row in enumerate(rows):
        catala_val = _substitute_row_into_expr(
            source_expr, table_name, table_def, row, field_type,
            constants, tables, fact_entities or set(), invoke_bound_entities or set(),
            list_item_types=list_item_types,
        )
        # Build compound condition for all key columns
        cond_parts = []
        for j, key_col in enumerate(key_cols):
            key_val = row[key_col]
            key_var = key_exprs[j] if j < len(key_exprs) else key_col
            cond_parts.append(_format_key_condition(key_var, key_val))
        cond = " and ".join(cond_parts)
        if i == 0:
            lines.append(f"    if {cond} then {catala_val}")
        else:
            lines.append(f"    else if {cond} then {catala_val}")

    if "conditional" in field_def:
        cond = field_def["conditional"]
        else_expr = cond.get("else", "")
        else_catala = translate_expr_to_catala(
            else_expr,
            constants=constants,
            field_type=field_type,
            tables=tables,
            fact_entities=fact_entities,
            invoke_bound_entities=invoke_bound_entities,
            list_item_types=list_item_types,
        )
        lines.append(f"    else {else_catala}")

    lines.append("")
    return lines


def emit_table_section(doc: dict, scope_name: str, constants: dict, table_style: str = "stacked", list_item_types: dict = None) -> list[tuple]:
    """Emit table-driven computed field definitions for all table-driven computed fields.

    Returns a list of (field_name, description, source, code_lines) tuples — one per
    table-driven computed field. The table-level description/source are on the table
    definition itself; field_def description/source apply to the computed field.
    """
    tables = doc.get("tables", {})
    computed = doc.get("computed", {})
    fact_entities = set(doc.get("inputs", {}).keys())
    chunks = []

    # Compute invoke-bound entities (fields wired via invoke: bind:)
    invoke_bound_entities: set = set()
    for field_def in computed.values():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            invoke_field = field_def["invoke"]
            bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
            invoke_bound_entities.update(bind.values())

    for field_name, field_def in computed.items():
        if not _uses_variable_table(field_def):
            continue
        table_name, key_exprs = _extract_table_info(field_def)
        if not table_name or table_name not in tables:
            if table_name:
                print(f"WARNING: computed field '{field_name}' references table '{table_name}' which is not defined in tables: — skipping table section", file=sys.stderr)
            continue
        # Rewrite entity prefixes in key variable names:
        # invoke-bound entities → snake_case var name (e.g. ClientIncome. → client_income.)
        # non-invoke-bound entities → strip prefix (e.g. Household. → "")
        if fact_entities and key_exprs:
            processed = []
            for k in key_exprs:
                for e in fact_entities:
                    if e in invoke_bound_entities:
                        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", e).lower()
                        k = re.sub(rf"\b{re.escape(e)}\.", f"{snake}.", k)
                    else:
                        k = re.sub(rf"\b{re.escape(e)}\.", "", k)
                processed.append(k)
            key_exprs = processed
        table_def = tables[table_name]
        if table_style == "else-if":
            code_lines = emit_table_definition_elseif(
                field_name, field_def, table_name, key_exprs,
                table_def, scope_name, constants, tables,
                fact_entities=fact_entities,
                invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types,
            )
        else:
            code_lines = emit_table_definition(
                field_name, field_def, table_name, key_exprs,
                table_def, scope_name, constants, tables,
                fact_entities=fact_entities,
                invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types,
            )
        chunks.append((
            field_name,
            field_def.get("description") if isinstance(field_def, dict) else None,
            field_def.get("source") if isinstance(field_def, dict) else None,
            code_lines,
        ))

    return chunks


def _format_condition_block(cond_str: str, indent: str = "      ") -> list[str]:
    """Split a condition string on ' and ' / ' or ' for multi-line indented display.

    Returns lines for the condition body (without 'under condition' header).
    """
    # Split on ' and ' at the top level — simple split (not paren-aware, but sufficient for CIVIL)
    parts = [p.strip() for p in re.split(r"\s+and\s+", cond_str)]
    if len(parts) == 1:
        return [f"{indent}{cond_str}"]
    return [f"{indent}{part} and" if i < len(parts) - 1 else f"{indent}{part}"
            for i, part in enumerate(parts)]


def emit_computed_section_catala(
    computed: dict,
    scope_name: str,
    constants: dict,
    tables: dict,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
    field_type_map: dict = None,
) -> list[tuple]:
    """Emit definitions for computed fields not handled by emit_table_section.

    Returns a list of (field_name, description, source, code_lines) tuples — one per
    non-table, non-invoke computed field. Bool-condition fields (two scope blocks) have
    both blocks in a single code_lines list so they stay in one catala fence.
    """
    chunks = []

    for field_name, field_def in computed.items():
        # Skip invoke: fields — handled by emit_subscope_wiring
        if isinstance(field_def, dict) and field_def.get("invoke"):
            continue
        # Skip table-driven fields — they were handled by emit_table_section
        if _uses_variable_table(field_def):
            continue

        ftype = field_def.get("type", "money")
        lines = []

        if "conditional" in field_def:
            cond = field_def["conditional"]
            if field_type_map:
                _check_cond_branch_type_compat(
                    cond["then"], cond["else"], field_name, field_type_map,
                    field_type=ftype,
                )
            if_expr = translate_expr_to_catala(
                cond["if"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            then_expr = translate_expr_to_catala(
                cond["then"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            else_expr = translate_expr_to_catala(
                cond["else"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            lines.append(f"scope {scope_name}:")
            lines.append(f"  definition {field_name} equals")
            lines.append(f"    if {if_expr} then {then_expr}")
            lines.append(f"    else {else_expr}")
        elif "expr" in field_def:
            raw_expr = field_def["expr"]
            is_output = "expose" in (field_def.get("tags") or [])
            if ftype == "bool" and is_output:
                # Output boolean with expr: emit definition/equals (content boolean, not condition)
                catala_expr = translate_condition_to_catala(
                    raw_expr, constants=constants, tables=tables,
                    fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                    list_item_types=list_item_types, field_type_map=field_type_map,
                )
                lines.append(f"scope {scope_name}:")
                lines.append(f"  definition {field_name} equals")
                lines.append(f"    {catala_expr}")
            elif ftype == "bool":
                # Condition variable: default false, exception for the true case.
                # Order matters: base case first, then exception — avoids conflict when condition holds.
                # Both scope blocks belong to one CIVIL field → stay in one fence (one chunk).
                catala_cond = translate_condition_to_catala(
                    raw_expr, constants=constants, tables=tables,
                    fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                    list_item_types=list_item_types, field_type_map=field_type_map,
                )
                cond_lines = _format_condition_block(catala_cond)
                lines.append(f"scope {scope_name}:")
                lines.append(f"  rule {field_name} under condition true")
                lines.append("    consequence not fulfilled")
                lines.append("")
                lines.append(f"scope {scope_name}:")
                lines.append("  exception")
                lines.append(f"  rule {field_name}")
                lines.append("    under condition")
                lines += cond_lines
                lines.append("    consequence fulfilled")
            else:
                catala_expr = translate_expr_to_catala(
                    raw_expr, constants=constants, field_type=ftype, tables=tables,
                    fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                    list_item_types=list_item_types,
                )
                lines.append(f"scope {scope_name}:")
                lines.append(f"  definition {field_name} equals")
                lines.append(f"    {catala_expr}")

        lines.append("")
        chunks.append((
            field_name,
            field_def.get("description"),
            field_def.get("source"),
            lines,
        ))

    return chunks


def find_eligible_field_name(doc: dict) -> str:
    """Return the name of the boolean output decision field (the 'eligible' flag).

    Falls back to 'eligible' if no boolean decision field is found.
    """
    for field_name, field_def in doc.get("outputs", {}).items():
        if field_def.get("type") == "bool":
            return field_name
    return "eligible"


def emit_rules_section_catala(
    rules: list,
    scope_name: str,
    constants: dict,
    tables: dict,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
    field_type_map: dict = None,
) -> list[tuple]:
    """Emit condition variables for deny rules (deny_rule_N_triggered).

    Returns a list of (rule_id, description, source, code_lines) tuples — one per
    deny rule. Each code_lines list contains both the base-case and exception scope
    blocks (they must stay in one fence per the Catala condition variable pattern).
    """
    chunks = []
    deny_rules = [r for r in rules if r.get("kind") == "deny"]

    for i, rule in enumerate(deny_rules, 1):
        rule_id = rule.get("id", f"rule-{i}")
        desc = rule.get("description")
        source = rule.get("source")
        when = rule.get("when", "true")
        var_name = f"deny_rule_{i}_triggered"

        # Condition variable rule: default false, exception for the true case.
        # Order matters: base case first, then exception — avoids conflict when condition holds.
        catala_cond = translate_condition_to_catala(when, constants=constants, tables=tables, fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities, list_item_types=list_item_types, field_type_map=field_type_map)
        cond_lines = _format_condition_block(catala_cond)

        lines = []
        lines.append(f"scope {scope_name}:")
        lines.append(f"  rule {var_name} under condition true")
        lines.append("    consequence not fulfilled")
        lines.append("")
        lines.append(f"scope {scope_name}:")
        lines.append("  exception")
        lines.append(f"  rule {var_name}")
        lines.append("    under condition")
        lines += cond_lines
        lines.append("    consequence fulfilled")
        lines.append("")

        chunks.append((rule_id, desc, source, lines))

    return chunks


def emit_decision_section_catala(
    doc: dict,
    scope_name: str,
    constants: dict = None,
    tables: dict = None,
    fact_entities: set = None,
    invoke_bound_entities: set = None,
    list_item_types: dict = None,
    field_type_map: dict = None,
) -> tuple[list[tuple], list[str]]:
    """Emit decision definitions, all_reason_entries, and reasons filter.

    Returns (decision_chunks, reasons_code_lines) where:
    - decision_chunks: list of (field_name, description, None, code_lines) for each
      non-list/non-set decision field — one per field, suitable for prose+fence rendering.
    - reasons_code_lines: the combined all_reason_entries + reasons filter code, emitted
      as a single fence after the last decision field (empty list if no deny rules).
    """
    constants = constants or {}
    tables = tables or {}
    deny_rules = [r for r in doc.get("rules", []) if r.get("kind") == "deny"]
    decisions = doc.get("outputs", {})

    # --- Per-decision chunks ---
    decision_chunks = []
    for field_name, field_def in decisions.items():
        ftype = field_def.get("type", "bool")
        if ftype in ("list", "set"):
            # list/set outputs are the deny-reasons accumulator — not emitted as individual
            # decision definitions here. They are handled below as reasons_field_name,
            # producing the all_reason_entries + filter/map pipeline (or an empty list [ ]
            # when there are no deny rules).
            continue
        if ftype == "string" and not field_def.get("values"):
            print(f"ERROR: output field '{field_name}' is a free-form string with no values: — Catala has no native string type; add values: to make it an enumeration or remove the field", file=sys.stderr)
            continue
        lines = []
        if "conditional" in field_def:
            cond = field_def["conditional"]
            if field_type_map:
                _check_cond_branch_type_compat(
                    cond["then"], cond["else"], field_name, field_type_map,
                    field_type=ftype,
                )
            if_expr = translate_expr_to_catala(
                cond["if"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            then_expr = translate_expr_to_catala(
                cond["then"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            else_expr = translate_expr_to_catala(
                cond["else"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            lines.append(f"scope {scope_name}:")
            lines.append(f"  definition {field_name} equals")
            lines.append(f"    if {if_expr} then {then_expr}")
            lines.append(f"    else {else_expr}")
        elif "expr" in field_def:
            catala_expr = translate_expr_to_catala(
                field_def["expr"], constants=constants, field_type=ftype, tables=tables,
                fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
                list_item_types=list_item_types, field_type_map=field_type_map,
            )
            lines.append(f"scope {scope_name}:")
            lines.append(f"  definition {field_name} equals")
            lines.append(f"    {catala_expr}")
        lines.append("")
        # Decisions have no source field in the CIVIL schema
        decision_chunks.append((field_name, field_def.get("description"), None, lines))

    if not deny_rules:
        # If there's a list-typed output, define it as empty (no deny rules can populate it).
        reasons_field_name = next(
            (k for k, v in decisions.items() if v.get("type") in ("list", "set")),
            None,
        )
        if reasons_field_name:
            reasons_lines = [
                f"scope {scope_name}:",
                f"  definition {reasons_field_name} equals",
                "    [ ]",
                "",
            ]
            return decision_chunks, reasons_lines
        return decision_chunks, []

    # --- Reasons code: all_reason_entries + reasons filter (single combined fence) ---
    reasons_field_name = next(
        (k for k, v in decisions.items() if v.get("type") in ("list", "set")),
        None,
    )
    if reasons_field_name is None:
        print("WARNING: deny rules exist but no list/set output field found — defaulting field name to 'reasons'", file=sys.stderr)
        reasons_field_name = "reasons"
    reasons_field_def = decisions.get(reasons_field_name, {})

    reasons_lines = []
    # all_reason_entries list literal
    reasons_lines.append(f"scope {scope_name}:")
    reasons_lines.append("  definition all_reason_entries equals")

    entries = []
    for i, rule in enumerate(deny_rules, 1):
        var_name = f"deny_rule_{i}_triggered"
        for action in rule.get("then", []):
            if "add_reason" in action:
                code = action["add_reason"]["code"]
                variant = reason_code_to_pascal(code)
                entries.append(
                    f"    ReasonEntry {{ -- triggered: {var_name} -- code: {variant} }}"
                )

    if entries:
        reasons_lines.append(f"    [ {entries[0].strip()}")
        for entry in entries[1:]:
            reasons_lines.append(f"    ; {entry.strip()}")
        reasons_lines.append("    ]")

    reasons_lines.append("")

    # reasons: filter + map
    reasons_lines.append(f"scope {scope_name}:")
    reasons_lines.append(f"  definition {reasons_field_name} equals")
    reasons_lines.append("    map each entry among")
    reasons_lines.append("      (list of e among all_reason_entries such that e.triggered)")
    reasons_lines.append("    to entry.code")
    reasons_lines.append("")

    return decision_chunks, reasons_lines


# =============================================================================
# OUTPUT ASSEMBLY
# =============================================================================

def _catala_block(lines: list[str]) -> list[str]:
    """Wrap a list of lines in a fenced catala code block."""
    return ["```catala"] + lines + ["```"]


def _catala_metadata_block(lines: list[str]) -> list[str]:
    """Wrap a list of lines in a fenced catala-metadata code block.

    Use for the Declarations section so that types and the scope declaration
    are exported as public symbols in the compiled .cmxs module.  Plain
    ``catala`` blocks are private at runtime and invisible to cross-module
    callers even though they pass ``catala typecheck``.
    """
    return ["```catala-metadata"] + lines + ["```"]


def transpile(doc: dict, output_path: str, scope_name: str, civil_path: str, table_style: str = "stacked"):
    """Assemble the full literate Catala Markdown file and write it."""
    # CIVIL v7: normalize table_lookup fields → expr: before processing
    doc = normalize_computed_doc(doc)

    constants = doc.get("constants", {})
    computed = doc.get("computed", {})
    tables = doc.get("tables", {})
    rules = doc.get("rules", [])
    description = doc.get("description", "")
    version = doc.get("version", "")
    effective = doc.get("effective", {})
    jurisdiction = doc.get("jurisdiction", {})

    # Derive Catala module name from the output file basename so it always matches
    # what clerk expects (e.g. eligibility.catala_en → "Eligibility").
    output_basename = os.path.basename(output_path)
    target_name_base = re.sub(r"\.catala_en(\.md)?$", "", output_basename)
    catala_module_name = target_name_base[0].upper() + target_name_base[1:] if target_name_base else "Module"

    fact_entities = set(doc.get("inputs", {}).keys())
    # CIVIL list/set inputs → Catala element-type lookup for sum(...) rewriting
    list_item_types = build_list_item_types(doc.get("inputs", {}))
    # Field type map for conditional branch type-checking (Fix #23.2)
    field_type_map = _build_all_field_type_map(doc)

    # --- Load sub-module docs for invoke: fields (3f) ---
    sub_module_docs: dict = {}
    for invoke_field_name, field_def in (computed or {}).items():
        if isinstance(field_def, dict) and field_def.get("invoke") and field_def.get("module"):
            sub_name = field_def["module"]
            if sub_name not in sub_module_docs:
                sub_path = os.path.join(
                    os.path.dirname(os.path.abspath(civil_path)),
                    f"{sub_name}.civil.yaml",
                )
                if not os.path.exists(sub_path):
                    fail(
                        f"Sub-module {sub_name!r} not found at {sub_path!r}. "
                        f"Check that the module name in computed.{invoke_field_name}.module "
                        f"matches a .civil.yaml file in the same directory."
                    )
                sub_module_docs[sub_name] = load_civil(sub_path)

    # Compute invoke-bound entity set for step 0 prefix rewriting (3f)
    invoke_bound_entities: set[str] = set()
    for field_def in (computed or {}).values():
        if isinstance(field_def, dict) and field_def.get("invoke"):
            invoke_field = field_def["invoke"]
            bind = invoke_field.get("bind", {}) if isinstance(invoke_field, dict) else {}
            invoke_bound_entities.update(bind.values())

    # --- Fail-fast bind forwarding validation (ticket 14) ---
    # Runs before any file I/O so a broken .catala_en is never written.
    bind_errors = check_bind_forwarding(doc, sub_module_docs, computed)
    for bind_error in bind_errors:
        print(f"ERROR: {bind_error}", file=sys.stderr)
    if bind_errors:
        sys.exit(1)

    # Collect unique sub-module names for > Using directives (3a)
    sub_modules: list[str] = []
    for field_def in (computed or {}).values():
        if isinstance(field_def, dict) and field_def.get("invoke") and field_def.get("module"):
            sub_module_name = field_def["module"]
            catala_sub_name = sub_module_name[0].upper() + sub_module_name[1:]
            if catala_sub_name not in sub_modules:
                sub_modules.append(catala_sub_name)

    md_lines = []

    # --- Catala module directive (must be the very first line) ---
    md_lines.append(f"> Module {catala_module_name}")
    # > Using directives for each sub-module (3a)
    for catala_sub_name in sub_modules:
        md_lines.append(f"> Using {catala_sub_name}")
    md_lines.append("")

    # --- File header ---
    md_lines.append(f"# {catala_module_name}")
    md_lines.append("")
    md_lines.append(description)
    md_lines.append("")
    j_level = jurisdiction.get("level", "")
    j_country = jurisdiction.get("country", "")
    j_state = jurisdiction.get("state", "")
    j_str = f"{j_level.capitalize()} ({j_country}{', ' + j_state if j_state else ''})"
    eff_start = effective.get("start", "")
    eff_end = effective.get("end", "")
    eff_str = f"{eff_start} – {eff_end}" if eff_end else str(eff_start)
    md_lines.append(f"Module: `{catala_module_name}` | Version: `{version}` | Effective: {eff_str} | Jurisdiction: {j_str}")
    md_lines.append("")
    md_lines.append("DO NOT EDIT — regenerate with: `xlator catala-transpile <domain> <module>`")
    md_lines.append("")

    # --- Declarations ---
    # Emit one H4 + prose per input fact field before the single unified catala-metadata fence.
    # The catala-metadata fence cannot be split (Catala requires one declaration scope block).
    md_lines.append("## Declarations")
    md_lines.append("")
    for entity_name, entity_def in doc.get("inputs", {}).items():
        for field_name, field_def in entity_def.get("fields", {}).items():
            _emit_prose_heading(
                md_lines, field_name,
                field_def.get("description") if isinstance(field_def, dict) else None,
                field_def.get("source") if isinstance(field_def, dict) else None,
            )
    decl_lines = emit_declarations(doc, scope_name, sub_module_docs=sub_module_docs)
    md_lines += _catala_metadata_block(decl_lines)
    md_lines.append("")

    # --- Subscope Wiring — one fence per invoke: field, each with H4 + prose ---
    parent_context = {
        "constants": constants,
        "tables": tables,
        "fact_entities": fact_entities,
        "invoke_bound_entities": invoke_bound_entities,
        "list_item_types": list_item_types,
        "field_type_map": field_type_map,
    }
    wiring_chunks = emit_subscope_wiring(
        computed, scope_name, sub_module_docs,
        parent_inputs=doc.get("inputs", {}),
        parent_context=parent_context,
    )
    if wiring_chunks:
        md_lines.append("## Subscope Wiring")
        md_lines.append("")
        for name, desc, source, code_lines in wiring_chunks:
            _emit_prose_heading(md_lines, name, desc, source)
            md_lines += _catala_block(code_lines)
            md_lines.append("")

    # --- Table Lookups ---
    # Emit table-level description+source once as prose at the top of the section,
    # then one fence per computed field with its own H4 + prose.
    table_chunks = emit_table_section(doc, scope_name, constants, table_style=table_style, list_item_types=list_item_types)
    if table_chunks:
        md_lines.append("## Table Lookups")
        md_lines.append("")
        # Emit each referenced table's description+source once (deduplicated)
        seen_tables: set = set()
        for field_name, field_desc, field_source, _ in table_chunks:
            # Look up which table this computed field references
            field_def = computed.get(field_name, {})
            table_name, _ = _extract_table_info(field_def) if isinstance(field_def, dict) else (None, None)
            if table_name and table_name not in seen_tables:
                seen_tables.add(table_name)
                tdef = tables.get(table_name, {})
                t_desc = tdef.get("description") if isinstance(tdef, dict) else None
                t_source = tdef.get("source") if isinstance(tdef, dict) else None
                prose = _prose_block(t_desc, t_source)
                if prose:
                    md_lines.append(prose)
                    md_lines.append("")
        for name, desc, source, code_lines in table_chunks:
            _emit_prose_heading(md_lines, name, desc, source)
            md_lines += _catala_block(code_lines)
            md_lines.append("")

    # --- Computed Values — one fence per field, each with H4 + prose ---
    computed_chunks = emit_computed_section_catala(
        computed, scope_name, constants, tables,
        fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
        list_item_types=list_item_types, field_type_map=field_type_map,
    )
    if computed_chunks:
        md_lines.append("## Computed Values")
        md_lines.append("")
        for name, desc, source, code_lines in computed_chunks:
            _emit_prose_heading(md_lines, name, desc, source)
            md_lines += _catala_block(code_lines)
            md_lines.append("")

    # --- Rules — one fence per deny rule, each with H4 (rule id) + prose ---
    deny_rules = [r for r in rules if r.get("kind") == "deny"]
    if deny_rules:
        md_lines.append("## Rules")
        md_lines.append("")
        rule_chunks = emit_rules_section_catala(
            rules, scope_name, constants, tables,
            fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
            list_item_types=list_item_types, field_type_map=field_type_map,
        )
        for rule_id, desc, source, code_lines in rule_chunks:
            _emit_prose_heading(md_lines, rule_id, desc, source)
            md_lines += _catala_block(code_lines)
            md_lines.append("")

    # --- Decision — one fence per decision field + one fence for reasons derivation ---
    md_lines.append("## Decision")
    md_lines.append("")
    decision_chunks, reasons_lines = emit_decision_section_catala(
        doc, scope_name,
        constants=constants, tables=tables,
        fact_entities=fact_entities, invoke_bound_entities=invoke_bound_entities,
        list_item_types=list_item_types, field_type_map=field_type_map,
    )
    for name, desc, _source, code_lines in decision_chunks:
        _emit_prose_heading(md_lines, name, desc, None)
        md_lines += _catala_block(code_lines)
        md_lines.append("")
    if reasons_lines:
        md_lines += _catala_block(reasons_lines)
        md_lines.append("")

    # Write output
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"✓ Transpiled to {output_path}")

    # Write clerk.toml alongside the output file (3a — sub-modules first)
    output_basename = os.path.basename(output_path)
    target_name = re.sub(r"\.catala_en(\.md)?$", "", output_basename)
    clerk_toml_path = os.path.join(out_dir, "clerk.toml")
    new_modules = sub_modules + [catala_module_name]

    # Merge with existing modules in clerk.toml to avoid clobbering other modules
    # Always use the current target_name — never inherit a stale name from a dependency's run.
    existing_modules: list[str] = []
    if os.path.exists(clerk_toml_path):
        with open(clerk_toml_path) as f:
            existing_content = f.read()
        modules_match = re.search(r'^modules\s*=\s*\[([^\]]*)\]', existing_content, re.MULTILINE)
        if modules_match:
            existing_modules = re.findall(r'"([^"]+)"', modules_match.group(1))

    # Union: preserve existing order, append any new ones not already present
    merged = list(existing_modules)
    for m in new_modules:
        if m not in merged:
            merged.append(m)

    modules_list = ", ".join(f'"{m}"' for m in merged)
    clerk_toml_content = (
        f'[project]\n'
        f'target_dir = "targets"\n'
        f'include_dirs = ["."]\n'
        f'\n'
        f'[[target]]\n'
        f'name = "{target_name}"\n'
        f'modules = [{modules_list}]\n'
        f'tests = ["tests/"]\n'
        f'backends = ["python"]\n'
    )
    with open(clerk_toml_path, "w") as f:
        f.write(clerk_toml_content)

    print(f"✓ Wrote {clerk_toml_path}")

    # Generate <module>_meta.py sidecar — carries CIVIL field categories forward
    # so downstream consumers (demo, /create-demo) can distinguish decision fields
    # from computed intermediates without reading the CIVIL source.
    meta_path = os.path.join(out_dir, f"{target_name}_meta.py")
    computed_doc = doc.get("computed", {})
    decisions_doc = doc.get("outputs", {})

    subscope_fields = [
        k for k, v in computed_doc.items()
        if isinstance(v, dict) and v.get("invoke")
    ]
    computed_out_fields = [
        k for k, v in computed_doc.items()
        if not (isinstance(v, dict) and v.get("invoke"))
        and "expose" in (v.get("tags") or [])
    ]
    decision_field_names = list(decisions_doc.keys())

    meta_lines = [
        f"# {target_name}_meta.py  (transpiler-generated — do not edit)",
        f"# Field categories for {scope_name} scope.",
        '# "decision"        — primary outcome fields (outputs: section)',
        '# "computed_output" — intermediate values tagged output: (computed: section)',
        '# "subscope_output" — invoke: computed fields that are subscope references',
        "",
        "SCOPE_METADATA: dict[str, str] = {",
    ]
    if subscope_fields:
        meta_lines.append("    # Subscope outputs")
        for fn in subscope_fields:
            meta_lines.append(f'    "{fn}": "subscope_output",')
    if computed_out_fields:
        meta_lines.append("    # Computed outputs")
        for fn in computed_out_fields:
            meta_lines.append(f'    "{fn}": "computed_output",')
    if decision_field_names:
        meta_lines.append("    # Decisions")
        for fn in decision_field_names:
            meta_lines.append(f'    "{fn}": "decision",')
    meta_lines += [
        "}",
        "",
        'DECISION_FIELDS     = [k for k, v in SCOPE_METADATA.items() if v == "decision"]',
        'COMPUTED_OUT_FIELDS = [k for k, v in SCOPE_METADATA.items() if v == "computed_output"]',
        'SUBSCOPE_FIELDS     = [k for k, v in SCOPE_METADATA.items() if v == "subscope_output"]',
    ]
    with open(meta_path, "w") as f:
        f.write("\n".join(meta_lines) + "\n")
    print(f"✓ Wrote {meta_path}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Transpile a CIVIL DSL YAML module to a Catala 1.1.0 literate program"
    )
    parser.add_argument("civil_yaml", help="Path to the CIVIL YAML module")
    parser.add_argument("output_catala", help="Path for the generated .catala_en file")
    parser.add_argument(
        "--scope",
        default=None,
        help="Catala scope name (default: derived from module name, e.g. EligibilityDecision)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip CIVIL validation step",
    )
    parser.add_argument(
        "--table-style",
        choices=["stacked", "else-if"],
        default="stacked",
        help="Table mapping style: 'stacked' (one under-condition block per row, default) or 'else-if' (single if/else if/else chain)",
    )
    args = parser.parse_args()

    if not args.no_validate:
        validate_before_transpile(args.civil_yaml)

    doc = load_civil(args.civil_yaml)
    scope_name = args.scope or derive_scope_name(doc.get("module", "module"))
    try:
        transpile(doc, args.output_catala, scope_name, civil_path=args.civil_yaml, table_style=args.table_style)
    except ValueError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
