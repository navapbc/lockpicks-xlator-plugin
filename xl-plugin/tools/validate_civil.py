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
from civil_schema import CivilModule  # noqa: E402

from pydantic import ValidationError  # noqa: E402


def _collect_expressions(doc: dict) -> list[str]:
    """Collect all CIVIL expression strings from computed, decisions, and rule when: clauses."""
    exprs = []
    for field_def in (doc.get("computed") or {}).values():
        if not isinstance(field_def, dict):
            continue
        if "expr" in field_def:
            exprs.append(str(field_def["expr"]))
        if "conditional" in field_def and isinstance(field_def["conditional"], dict):
            for key in ("if", "then", "else"):
                if key in field_def["conditional"]:
                    exprs.append(str(field_def["conditional"][key]))
    for field_def in (doc.get("outputs") or {}).values():
        if not isinstance(field_def, dict):
            continue
        if "expr" in field_def:
            exprs.append(str(field_def["expr"]))
        if "conditional" in field_def and isinstance(field_def["conditional"], dict):
            for key in ("if", "then", "else"):
                if key in field_def["conditional"]:
                    exprs.append(str(field_def["conditional"][key]))
    for rule in (doc.get("rules") or []):
        if isinstance(rule, dict) and "when" in rule:
            exprs.append(str(rule["when"]))
    return exprs


def validate_enum_decisions(path: str, data: dict) -> tuple[list[str], list[str]]:
    """Warn when CIVIL primary decision type mismatches guidance.yaml declaration."""
    warnings = []
    specs_dir = pathlib.Path(path).parent
    guidance_path = specs_dir / "guidance.yaml"
    if not guidance_path.exists():
        return [], []
    try:
        with open(guidance_path) as f:
            guidance = yaml.safe_load(f)
    except Exception:
        return [], []
    primary = (guidance.get("output_variables") or {}).get("primary") or {}
    declared_name = primary.get("name")
    declared_type = primary.get("type")
    if not declared_name or not declared_type:
        return [], []
    decisions = data.get("outputs") or {}
    if declared_name not in decisions:
        return [], []
    actual_type = (decisions[declared_name] or {}).get("type", "")
    if declared_type in ("enum", "string") and actual_type == "bool":
        warnings.append(
            f"decisions → {declared_name}: type is 'bool' but guidance.yaml "
            f"output_variables.primary.type is '{declared_type}' — "
            f"consider using type: string with values: {primary.get('values', [])}"
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
        all_exprs = " ".join(_collect_expressions(doc))
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
