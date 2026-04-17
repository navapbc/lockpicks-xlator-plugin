#!/usr/bin/env python3
"""
CIVIL Expression Reference Extractor

Parses CIVIL DSL expression strings using Python's ast module and returns
the set of entity fields, computed fields, constants, and tables referenced.

Usage (as a library):
    from civil_expr import extract_refs, extract_refs_from_computed, ExprRefs

    refs = extract_refs(
        "Household.earned_income * EARNED_INCOME_DEDUCTION_RATE",
        computed_names={"earned_income_deduction"},
        table_names={"standard_deductions"},
    )
    # refs.entity_fields  → ["Household.earned_income"]
    # refs.constant_refs  → ["EARNED_INCOME_DEDUCTION_RATE"]
    # refs.computed_refs  → []
    # refs.table_refs     → []
"""

import ast
import re
from dataclasses import dataclass, field

# Function names in CIVIL expressions that are not data references.
# These appear as ast.Name nodes (func.id) in Call nodes and must be filtered.
_CIVIL_FUNCTIONS = {"max", "min", "exists", "is_null", "between", "in_", "table"}

# Pre-parse fixes for CIVIL operators that differ from Python syntax.
_IN_FN_RE = re.compile(r"\bin\(")  # 'in' is a Python keyword when used as a fn name


def _civil_to_python(expr: str) -> str:
    """Translate CIVIL boolean/logical operators to Python equivalents for ast.parse.

    CIVIL uses C-style operators: || → or, && → and, !x → not x.
    Also rewrites in(...) → in_(...) since 'in' is a Python keyword.
    """
    expr = expr.replace("||", " or ")
    expr = expr.replace("&&", " and ")
    # Replace '!' with 'not ' but preserve '!='
    expr = re.sub(r"!(?!=)", "not ", expr)
    expr = _IN_FN_RE.sub("in_(", expr)
    return expr


@dataclass
class ExprRefs:
    """Categorized references extracted from a single CIVIL expression."""

    entity_fields: list[str] = field(default_factory=list)
    """Fact field references in 'Entity.field_name' form."""

    computed_refs: list[str] = field(default_factory=list)
    """Bare identifiers matching a known computed field name."""

    constant_refs: list[str] = field(default_factory=list)
    """UPPER_SNAKE_CASE identifiers not matching a table or computed name."""

    table_refs: list[str] = field(default_factory=list)
    """Table names from table('name', ...) calls or bare table-name references."""


def extract_refs(
    expr: str,
    computed_names: set[str],
    table_names: set[str],
) -> ExprRefs:
    """Walk the AST of a CIVIL expression and return categorized references.

    Two-pass approach:
      Pass 1 — collect entity names (PascalCase identifiers used as Attribute
                node values) so they are suppressed in the Name pass.
      Pass 2 — classify all remaining Name, Attribute, and Call nodes.

    Guards:
    - ast.Attribute where node.value is a Call (e.g. table(...).column) is skipped
      in entity_fields collection to prevent a crash on node.value.id.
    - 'in(...)' is rewritten to 'in_(...)' before parsing to avoid SyntaxError.
    - CIVIL boolean operators (||, &&, !) are translated to Python equivalents.
    """
    expr = _civil_to_python(expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse CIVIL expression: {expr!r}") from exc

    refs = ExprRefs()

    # Pass 1: collect entity names (left-hand side of Attribute nodes that are
    # simple Names — i.e., PascalCase entity names like 'Household').
    attribute_value_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attribute_value_ids.add(node.value.id)

    # Pass 2: collect refs.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # Only capture Entity.field — skip table(...).column and other Attribute nodes
            # where node.value is not a bare Name.
            if isinstance(node.value, ast.Name):
                refs.entity_fields.append(f"{node.value.id}.{node.attr}")

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "table" and node.args:
                try:
                    table_name = ast.literal_eval(node.args[0])
                    if isinstance(table_name, str):
                        refs.table_refs.append(table_name)
                except (ValueError, TypeError):
                    pass

        elif isinstance(node, ast.Name):
            name = node.id
            # Skip entity names (captured via Attribute) and known CIVIL function names.
            if name in attribute_value_ids or name in _CIVIL_FUNCTIONS:
                continue
            if name in computed_names:
                refs.computed_refs.append(name)
            elif name in table_names:
                refs.table_refs.append(name)
            elif name == name.upper() and len(name) > 1:
                refs.constant_refs.append(name)

    return refs


def extract_refs_from_computed(
    field_def: dict,
    computed_names: set[str],
    table_names: set[str],
) -> ExprRefs:
    """Extract refs from a computed field definition.

    Handles 'expr' (single expression), 'conditional' (if/then/else), and
    'invoke:' (CIVIL v4 sub-ruleset call). invoke: fields have no inline
    expression — they reference a sub-module; return empty refs.
    """
    # CIVIL v4: invoke: fields have no inline expression to parse
    if field_def.get("invoke"):
        return ExprRefs()

    # CIVIL v7: table_lookup fields — only the table name is a ref
    if field_def.get("table_lookup"):
        lookup = field_def["table_lookup"]
        return ExprRefs(table_refs=[lookup["table"]])

    if field_def.get("expr"):
        return extract_refs(field_def["expr"], computed_names, table_names)

    cond = field_def["conditional"]
    r1 = extract_refs(cond["if"],   computed_names, table_names)
    r2 = extract_refs(cond["then"], computed_names, table_names)
    r3 = extract_refs(cond["else"], computed_names, table_names)
    return ExprRefs(
        entity_fields=r1.entity_fields + r2.entity_fields + r3.entity_fields,
        computed_refs=r1.computed_refs  + r2.computed_refs  + r3.computed_refs,
        constant_refs=r1.constant_refs  + r2.constant_refs  + r3.constant_refs,
        table_refs=   r1.table_refs     + r2.table_refs     + r3.table_refs,
    )


# =============================================================================
# CIVIL v7: table_lookup resolver and doc normalizer
# =============================================================================


def resolve_table_lookup_expr(
    lookup: dict,
    tables: dict,
    computed_names: list[str],
    entities: list[dict],
) -> str:
    """Convert a table_lookup block to the equivalent CIVIL expr string.

    Resolves each key column name against:
    1. Known computed field names (bare name)
    2. Entity fields across all entities (Entity.field)

    Raises ValueError on ambiguity or missing key name.
    """
    table_name = lookup["table"]
    key_cols = lookup["key"]
    value_col = lookup.get("value")

    table_def = tables[table_name]
    if value_col is None:
        value_col = table_def["value"][0]  # validator ensures single-col when omitted

    key_exprs = []
    for col in key_cols:
        if col in computed_names:
            key_exprs.append(col)
            continue
        matches = [
            e_name
            for e_name, e_def in entities.items()
            for f_name in (e_def.get("fields") or {})
            if f_name == col
        ]
        if not matches:
            raise ValueError(
                f"table_lookup key '{col}' not found in computed fields or any entity"
            )
        if len(matches) > 1:
            raise ValueError(
                f"table_lookup key '{col}' is ambiguous: found in entities {matches}"
            )
        key_exprs.append(f"{matches[0]}.{col}")

    args = ", ".join([f"'{table_name}'"] + key_exprs)
    return f"table({args}).{value_col}"


def normalize_computed_doc(doc: dict) -> dict:
    """Return a copy of doc with all table_lookup computed fields converted to expr: fields.

    Called at the top of each transpiler's transpile() function so that
    downstream emit functions receive only expr:/conditional:/invoke: variants.
    """
    computed = doc.get("computed")
    if not computed:
        return doc

    tables = doc.get("tables", {})
    entities = doc.get("facts", {})
    computed_names = list(computed.keys())

    normalized = {}
    for field_name, field_def in computed.items():
        if isinstance(field_def, dict) and field_def.get("table_lookup"):
            expr_str = resolve_table_lookup_expr(
                field_def["table_lookup"],
                tables,
                computed_names,
                entities,
            )
            field_def = {k: v for k, v in field_def.items() if k != "table_lookup"}
            field_def["expr"] = expr_str
        normalized[field_name] = field_def

    return {**doc, "computed": normalized}
