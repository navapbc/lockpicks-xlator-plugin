#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic>=2.0", "pyyaml>=6.0"]
# ///
"""
CIVIL DSL Structure Validator

Validates that a CIVIL YAML file conforms to the CIVIL DSL specification.
Schema is defined in tools/civil_schema.py (Pydantic v2 models).

Usage (via xlator CLI):
    xlator validate <domain> <module>

Exit codes:
    0 — valid
    1 — invalid (errors printed to stderr)
"""

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from civil_expr import _scan_comprehension_args, extract_refs  # noqa: E402
from civil_schema import CivilModule  # noqa: E402

from pydantic import ValidationError  # noqa: E402


def _collect_expressions(doc: dict) -> list[tuple[str, str]]:
    """Collect all CIVIL expressions from computed, decisions, and rule when: clauses.

    Yields `(field_id, expr_str)` tuples, where `field_id` is a dotted path
    like `computed.severity_d_escalation.expr`, `outputs.eligible.conditional.if`,
    or `rules.FED-SNAP-DENY-001.when`. The field-id prefix is consumed by the
    expression validator's error-message format (`ERROR: <module>.<field>: ...`).
    """
    exprs: list[tuple[str, str]] = []
    for field_name, field_def in (doc.get("computed") or {}).items():
        if not isinstance(field_def, dict):
            continue
        if "expr" in field_def:
            exprs.append((f"computed.{field_name}.expr", str(field_def["expr"])))
        if "conditional" in field_def and isinstance(field_def["conditional"], dict):
            for key in ("if", "then", "else"):
                if key in field_def["conditional"]:
                    exprs.append(
                        (f"computed.{field_name}.conditional.{key}",
                         str(field_def["conditional"][key]))
                    )
    for field_name, field_def in (doc.get("outputs") or {}).items():
        if not isinstance(field_def, dict):
            continue
        if "expr" in field_def:
            exprs.append((f"outputs.{field_name}.expr", str(field_def["expr"])))
        if "conditional" in field_def and isinstance(field_def["conditional"], dict):
            for key in ("if", "then", "else"):
                if key in field_def["conditional"]:
                    exprs.append(
                        (f"outputs.{field_name}.conditional.{key}",
                         str(field_def["conditional"][key]))
                    )
    for rule in (doc.get("rules") or []):
        if isinstance(rule, dict) and "when" in rule:
            rule_id = rule.get("id", "<unknown>")
            exprs.append((f"rules.{rule_id}.when", str(rule["when"])))
    return exprs


def validate_enum_decisions(path: str, data: dict) -> tuple[list[str], list[str]]:
    """Warn when CIVIL primary decision type mismatches the type declared in
    `specs/naming-manifest.yaml` for the primary output. The primary output is
    identified via `specs/guidance/output-variables.yaml`'s `primary: true` flag;
    the type itself is read from `specs/naming-manifest.yaml`'s `outputs:` block.
    """
    warnings = []
    specs_dir = pathlib.Path(path).parent

    # Identify the primary output name from output-variables.yaml.
    output_vars_path = specs_dir / "guidance" / "output-variables.yaml"
    if not output_vars_path.exists():
        return [], []
    try:
        with open(output_vars_path) as f:
            output_vars = yaml.safe_load(f) or {}
    except Exception:
        return [], []
    primary_name = None
    if isinstance(output_vars, dict):
        for name, entry in output_vars.items():
            if isinstance(entry, dict) and entry.get("primary") is True:
                primary_name = name
                break
    if not primary_name:
        return [], []

    # Read the declared type from naming-manifest.yaml's outputs block.
    manifest_path = specs_dir / "naming-manifest.yaml"
    if not manifest_path.exists():
        return [], []
    try:
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f) or {}
    except Exception:
        return [], []
    outputs = manifest.get("outputs") or {}
    declared_entry = outputs.get(primary_name) or {}
    declared_type = declared_entry.get("type")
    if not declared_type:
        return [], []

    decisions = data.get("outputs") or {}
    if primary_name not in decisions:
        return [], []
    actual_type = (decisions[primary_name] or {}).get("type", "")
    if declared_type in ("enum", "string") and actual_type == "bool":
        warnings.append(
            f"decisions → {primary_name}: type is 'bool' but specs/naming-manifest.yaml "
            f"outputs.{primary_name}.type is '{declared_type}' — "
            f"consider using type: string with values: {declared_entry.get('values', [])}"
        )
    return [], warnings


def validate_invoke_references(module_path: str, doc: dict) -> tuple[list[str], list[str]]:
    """Validate all invoke: fields: file resolution, bind: entities, cycle detection.

    Returns (errors, warnings). Errors are hard failures; warnings are best-effort hints.
    Only performs cross-module checks; Pydantic handles field-level validation.
    """
    errors = []
    warnings = []
    specs_dir = pathlib.Path(module_path).parent

    # Build invocation graph for cycle detection (DFS)
    root_path = str(pathlib.Path(module_path).resolve())
    visited: set[str] = {root_path}
    stack: list[str] = [root_path]

    def _check_module(path: str) -> None:
        if path in stack:
            cycle = " → ".join(stack + [path])
            errors.append(f"Circular invocation detected: {cycle}")
            return
        if path in visited:
            return
        visited.add(path)
        stack.append(path)
        try:
            with open(path) as f:
                sub_doc = yaml.safe_load(f)
        except FileNotFoundError:
            errors.append(f"invoke: sub-module not found: {path}")
            stack.pop()
            return
        except Exception as exc:
            errors.append(f"invoke: sub-module YAML error in {path}: {exc}")
            stack.pop()
            return
        # Recurse into sub-module's invoke: fields
        for field_def in (sub_doc.get("computed") or {}).values():
            if isinstance(field_def, dict) and "invoke" in field_def and field_def.get("module"):
                sub_path = str(specs_dir / f"{field_def['module']}.civil.yaml")
                _check_module(sub_path)
        stack.pop()

    parent_entities = set((doc.get("inputs") or {}).keys())
    for field_name, field_def in (doc.get("computed") or {}).items():
        if not isinstance(field_def, dict):
            continue
        if "invoke" not in field_def or not field_def.get("module"):
            continue
        sub_module_name = field_def["module"]
        sub_path = str(specs_dir / f"{sub_module_name}.civil.yaml")

        # Resolve file + cycle detection
        _check_module(sub_path)

        # Validate bind: entities against sub-module and parent input facts
        try:
            with open(sub_path) as f:
                sub_doc = yaml.safe_load(f)
            sub_entities = set((sub_doc.get("inputs") or {}).keys())
            bind = field_def.get("invoke") or {}
            if isinstance(bind, dict):
                bind_dict = bind.get("bind", {})
            else:
                bind_dict = {}
            for sub_entity, parent_entity in bind_dict.items():
                if sub_entity not in sub_entities:
                    errors.append(
                        f"computed → {field_name} → invoke.bind: "
                        f"'{sub_entity}' not in sub-module '{sub_module_name}' facts"
                    )
                if parent_entity not in parent_entities:
                    errors.append(
                        f"computed → {field_name} → invoke.bind: "
                        f"'{parent_entity}' not in parent module facts"
                    )
        except (FileNotFoundError, Exception):
            continue  # already reported by _check_module; skip tags check for missing files

        # Warn when parent expressions reference a sub-module computed field that
        # lacks tags: [output] — such fields are inaccessible at transpile time.
        sub_computed = sub_doc.get("computed") or {}
        all_exprs = " ".join(expr_str for _, expr_str in _collect_expressions(doc))
        pattern = re.compile(rf"\b{re.escape(field_name)}\.(\w+)\b")
        seen_attrs: set[str] = set()
        for match in pattern.finditer(all_exprs):
            attr = match.group(1)
            if attr in seen_attrs:
                continue
            seen_attrs.add(attr)
            if attr not in sub_computed:
                continue  # decisions fields are always accessible; let transpiler catch unknowns
            sub_field = sub_computed[attr]
            tags = sub_field.get("tags", []) if isinstance(sub_field, dict) else []
            if "expose" not in tags:
                warnings.append(
                    f"computed → {field_name}: references '{attr}' on sub-module "
                    f"'{sub_module_name}' but '{attr}' lacks tags: [expose] — "
                    f"add tags: [expose] to the sub-module field or it will fail at transpile time"
                )

    return errors, warnings


def validate_table_lookup_references(doc: dict) -> tuple[list[str], list[str]]:
    """Validate table_lookup: fields in computed: (CIVIL v7).

    Checks:
    - table name exists in tables:
    - each key column exists in the table's key: list
    - value column (if specified) exists in the table's value: list
    - value column omitted only when the table has exactly one value column

    Returns (errors, warnings).
    """
    errors = []
    warnings = []
    tables = doc.get("tables") or {}

    for field_name, field_def in (doc.get("computed") or {}).items():
        if not isinstance(field_def, dict):
            continue
        lookup = field_def.get("table_lookup")
        if not lookup:
            continue

        table_name = lookup.get("table", "")
        if table_name not in tables:
            errors.append(
                f"computed → {field_name} → table_lookup: "
                f"table '{table_name}' not found in tables:"
            )
            continue

        table_def = tables[table_name]
        table_keys = table_def.get("key", [])
        table_values = table_def.get("value", [])

        for col in (lookup.get("key") or []):
            if col not in table_keys:
                errors.append(
                    f"computed → {field_name} → table_lookup.key: "
                    f"'{col}' is not a key column of table '{table_name}' "
                    f"(key columns: {table_keys})"
                )

        value_col = lookup.get("value")
        if value_col is not None:
            if value_col not in table_values:
                errors.append(
                    f"computed → {field_name} → table_lookup.value: "
                    f"'{value_col}' is not a value column of table '{table_name}' "
                    f"(value columns: {table_values})"
                )
        else:
            if len(table_values) != 1:
                errors.append(
                    f"computed → {field_name} → table_lookup: "
                    f"table '{table_name}' has {len(table_values)} value columns {table_values}; "
                    f"specify 'value:' to select one"
                )

    return errors, warnings


def validate_group_assignments(module_path: str, module: "CivilModule") -> tuple[list[str], list[str]]:
    """Validate rule group: values against rule_set.ruleset_groups (CIVIL v6).

    Returns (errors, warnings).
    - If ruleset_groups is empty: emit a warning for each rule with group set (not an error).
    - If ruleset_groups is non-empty: error for each rule whose group is not in stage names.
    """
    errors = []
    warnings = []

    stage_names = {s.name for s in module.rule_set.ruleset_groups}

    if not stage_names:
        for rule in module.rules:
            if rule.group is not None:
                warnings.append(
                    f"rules → {rule.id}: group='{rule.group}' is set but "
                    f"rule_set.ruleset_groups is empty — define ruleset_groups "
                    f"to enable group validation"
                )
        return errors, warnings

    for rule in module.rules:
        if rule.group is not None and rule.group not in stage_names:
            errors.append(
                f"rules → {rule.id}: group='{rule.group}' is not in "
                f"rule_set.ruleset_groups names: {sorted(stage_names)}"
            )

    return errors, warnings


def validate_mutex_group_consistency(module: "CivilModule") -> tuple[list[str], list[str]]:
    """Validate mutex_group: rules have unique priorities within each group (CIVIL v6).

    Returns (errors, warnings).
    - Singleton mutex_group (only one member): warning.
    - Duplicate priority values within a mutex_group: error.
    """
    errors = []
    warnings = []

    # Group rules by mutex_group name (skip rules with no mutex_group)
    groups: dict[str, list] = {}
    for rule in module.rules:
        if rule.mutex_group is not None:
            groups.setdefault(rule.mutex_group, []).append(rule)

    for group_name, rules_in_group in groups.items():
        if len(rules_in_group) == 1:
            warnings.append(
                f"mutex_group '{group_name}' has only one member "
                f"(rule '{rules_in_group[0].id}') — singleton groups have no enforcement effect"
            )
            continue

        priorities = [r.priority for r in rules_in_group]
        if len(priorities) != len(set(priorities)):
            # Find which priorities are duplicated for a helpful message
            seen: set[int] = set()
            dupes: set[int] = set()
            for p in priorities:
                if p in seen:
                    dupes.add(p)
                seen.add(p)
            errors.append(
                f"mutex_group '{group_name}' has duplicate priority values {sorted(dupes)} — "
                f"each rule in a mutex_group must have a unique priority"
            )

    return errors, warnings


def _build_name_inventory(doc: dict) -> dict[str, tuple[str, str]]:
    """Build a name → (kind, qualified_name) inventory for shadowing checks.

    Returns a dict mapping every bare identifier in the module's name-space to
    a tuple `(kind, qualified_name)` where `kind` is one of:
      - "entity"        (PascalCase entity name from inputs:)
      - "entity field"  (snake_case field name from inputs.<E>.fields, value = "<E>.<f>")
      - "computed"      (snake_case computed field name)
      - "constant"      (UPPER_SNAKE_CASE name from constants:)
      - "table"         (table name from tables:)

    A bound name that collides with ANY of these is flagged by the shadowing check.
    The "constant" kind also includes the existing-name for the friendlier error
    message.
    """
    inventory: dict[str, tuple[str, str]] = {}
    for ename, edef in (doc.get("inputs") or {}).items():
        inventory.setdefault(ename, ("entity", ename))
        if isinstance(edef, dict):
            for fname in (edef.get("fields") or {}):
                inventory.setdefault(fname, ("entity field", f"{ename}.{fname}"))
    for cname in (doc.get("computed") or {}):
        inventory.setdefault(cname, ("computed", cname))
    for kname in (doc.get("constants") or {}):
        inventory.setdefault(kname, ("constant", kname))
    for tname in (doc.get("tables") or {}):
        inventory.setdefault(tname, ("table", tname))
    return inventory


def _lookup_collection_type(coll: str, doc: dict) -> tuple[str, str] | None:
    """Look up an iterated collection reference's declared type.

    Returns `(qualified_name, type)` for a found ref, or None if the ref is
    not resolvable in the module's name-space (e.g., it's a bound name from
    an outer comprehension scope — `v.items` style nested-comprehension iter).

    Resolution order:
      1. Computed fields → ("<name>", type)
      2. Input entity fields (snake_case) → ("<Entity>.<field>", type)
      3. Constants → ("<name>", "constant")
    """
    # Computed
    for cname, cdef in (doc.get("computed") or {}).items():
        if cname == coll and isinstance(cdef, dict):
            return (cname, cdef.get("type", "unknown"))
    # Entity fields (bare snake_case → look up across all entities)
    for ename, edef in (doc.get("inputs") or {}).items():
        if not isinstance(edef, dict):
            continue
        for fname, fdef in (edef.get("fields") or {}).items():
            if fname == coll and isinstance(fdef, dict):
                return (f"{ename}.{fname}", fdef.get("type", "unknown"))
    # Constants (unusual but cover the case)
    constants = doc.get("constants") or {}
    if coll in constants:
        val = constants[coll]
        return (coll, "list" if isinstance(val, list) else "constant")
    return None


def _scan_comprehension_iterables(expr: str) -> list[str]:
    """Walk `expr` left-to-right and collect every comprehension iterable name.

    Uses U1's shared `_scan_comprehension_args` to find each `count(...)` /
    `exists(...)` comprehension's `coll` token, mirroring the rewrite logic in
    `_rewrite_comprehensions_for_ast`. Recurses into the predicate so nested
    comprehensions are captured too.

    The collection token may be a bare identifier (the common case) OR a dotted
    chain (e.g., `v.items`); we keep the bare-identifier-or-prefix form here so
    the caller can decide whether to look it up.
    """
    iterables: list[str] = []
    i = 0
    n = len(expr)
    while i < n:
        head_len = 0
        if (
            expr.startswith("count(", i)
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
        ):
            head_len = 6
        elif (
            expr.startswith("exists(", i)
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
        ):
            head_len = 7
        if head_len == 0:
            i += 1
            continue
        scan = _scan_comprehension_args(expr, i + head_len)
        if scan is None:
            i += head_len
            continue
        _var, coll, pred, end = scan
        iterables.append(coll)
        # Recurse into predicate for nested comprehensions.
        iterables.extend(_scan_comprehension_iterables(pred))
        i = end + 1
    return iterables


def _validate_expressions(doc: dict, module_name: str) -> tuple[list[str], list[str]]:
    """Expression-aware validation pass (CIVIL v11 — comprehension support).

    For every collected `(field_id, expr_str)` tuple:
      1. Call `extract_refs` — catch `ValueError` and emit a parse / qualified-access /
         empty-predicate error with field-id prefix.
      2. On success, inspect `ExprRefs.bound_names`:
         - Bound-name shadowing — each bound name is checked against the module's
           name inventory (entities, entity fields, computed, constants, tables).
           Any collision emits `ERROR: <module>.<field>: comprehension bound name
           '<b>' shadows a known <kind> ('<existing>'); rename the bound name`.
      3. Non-list collection — re-scan the expression with `_scan_comprehension_args`
         to identify the iterated collection name; if it resolves to a scalar
         (not `list` / `set`), emit `ERROR: ... iterates over non-list '<coll>'
         (type: <type>)`. Refs that don't resolve (e.g., `v.items` where `v` is
         an outer-scope bound name) are skipped — those are validated by U1's
         walker via `bound_names` scoping.

    Returns `(errors, warnings)`. Warnings are unused at present but the return
    shape mirrors the sibling validators for consistency.
    """
    errors: list[str] = []
    warnings: list[str] = []

    computed_names = set((doc.get("computed") or {}).keys())
    table_names = set((doc.get("tables") or {}).keys())
    inventory = _build_name_inventory(doc)

    # Comprehension-shape detector — `count(... in ... where ...)` or
    # `exists(... in ... where ...)`. Used to scope parse-error reporting to
    # comprehension-related failures only. CIVIL DSL also supports `if/then/else`
    # ternaries and other constructs that `civil_expr.extract_refs` does not yet
    # parse; emitting parse errors for those would regress non-comprehension
    # modules (e.g., ak_doh). U2's mandate is comprehension support — broader
    # parse-error coverage is a follow-up.
    _COMPREHENSION_SHAPE_RE = re.compile(
        r"\b(?:count|exists)\s*\([^)]*\bin\b[^)]*\bwhere\b"
    )

    for field_id, expr_str in _collect_expressions(doc):
        is_comprehension = bool(_COMPREHENSION_SHAPE_RE.search(expr_str))
        try:
            refs = extract_refs(expr_str, computed_names, table_names)
        except ValueError as exc:
            # Only surface parse failures on comprehension-shaped expressions.
            # Other parse failures (e.g., CIVIL `if x then y else z` ternaries
            # not yet supported by civil_expr.py) are silently skipped here and
            # picked up downstream by the transpilers.
            if is_comprehension:
                errors.append(f"{module_name}.{field_id}: {exc}")
            continue

        # Bound-name shadowing — only relevant kinds for the shadow error message.
        # We surface entity, entity-field, computed, and constant collisions.
        shadow_kinds = {"entity", "entity field", "computed", "constant", "table"}
        for b in refs.bound_names:
            entry = inventory.get(b)
            if entry is None:
                continue
            kind, qualified = entry
            if kind not in shadow_kinds:
                continue
            errors.append(
                f"{module_name}.{field_id}: comprehension bound name '{b}' "
                f"shadows a known {kind} ('{qualified}'); rename the bound name"
            )

        # Non-list collection — re-scan to find each comprehension's iterable token.
        # Skip dotted iterables (e.g., `v.items` from nested comprehensions) because
        # the head identifier is an outer-scope bound name, not a module-level ref.
        for coll in _scan_comprehension_iterables(expr_str):
            if "." in coll:
                continue
            lookup = _lookup_collection_type(coll, doc)
            if lookup is None:
                continue
            qualified, ctype = lookup
            if ctype not in ("list", "set"):
                errors.append(
                    f"{module_name}.{field_id}: comprehension iterates over "
                    f"non-list '{coll}' (type: {ctype})"
                )

    return errors, warnings


def validate(path: str) -> bool:
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        return False
    except yaml.YAMLError as e:
        print(f"ERROR: YAML parse error: {e}", file=sys.stderr)
        return False

    try:
        module = CivilModule.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            loc = " → ".join(str(x) for x in err["loc"]) if err["loc"] else "(root)"
            print(f"ERROR: {loc}: {err['msg']}", file=sys.stderr)
        print(f"\n{e.error_count()} error(s) found in {path}", file=sys.stderr)
        return False

    # Cross-module validation for invoke: fields (only if Pydantic passes)
    _, enum_warnings = validate_enum_decisions(path, data)
    for warn in enum_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)

    invoke_errors, invoke_warnings = validate_invoke_references(path, data)
    for warn in invoke_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)
    if invoke_errors:
        for err in invoke_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"\n{len(invoke_errors)} cross-module error(s) found in {path}", file=sys.stderr)
        return False

    # CIVIL v7: table_lookup field validation
    tl_errors, tl_warnings = validate_table_lookup_references(data)
    for warn in tl_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)
    if tl_errors:
        for err in tl_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"\n{len(tl_errors)} table_lookup error(s) found in {path}", file=sys.stderr)
        return False

    # CIVIL v11: expression-aware validation (parse errors, comprehension scope checks).
    expr_errors, expr_warnings = _validate_expressions(data, module.module)
    for warn in expr_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)
    if expr_errors:
        for err in expr_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"\n{len(expr_errors)} expression error(s) found in {path}", file=sys.stderr)
        return False

    # CIVIL v6: maintainability annotation validation
    group_errors, group_warnings = validate_group_assignments(path, module)
    for warn in group_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)
    mutex_errors, mutex_warnings = validate_mutex_group_consistency(module)
    for warn in mutex_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)

    v6_errors = group_errors + mutex_errors
    if v6_errors:
        for err in v6_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"\n{len(v6_errors)} v6 annotation error(s) found in {path}", file=sys.stderr)
        return False

    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: xlator validate <domain> <module>", file=sys.stderr)
        sys.exit(1)

    if validate(sys.argv[1]):
        print(f"✓ {sys.argv[1]} is valid CIVIL")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
