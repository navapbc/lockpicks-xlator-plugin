#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# ///
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
#
# count and exists are dual-mode: function-call form here, comprehension-head form
# consumed by _rewrite_comprehensions_for_ast pre-parse rewrite before ast.parse sees them.
_CIVIL_FUNCTIONS = {
    "max", "min", "exists", "is_null", "between", "in_", "table",
    "count", "len", "any", "sum",
}

# Pre-parse fixes for CIVIL operators that differ from Python syntax.
_IN_FN_RE = re.compile(r"\bin\(")  # 'in' is a Python keyword when used as a fn name

# Identifier regex for comprehension scanner.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _string_literal_positions(s: str) -> frozenset[int]:
    """Return the set of byte offsets in `s` that lie inside a single- or
    double-quoted string literal — opening quote, body chars, closing quote,
    AND escape-sequence chars. Backslash escapes (`\\'`, `\\"`, `\\\\`) are
    honored: the backslash and its escaped char are both treated as inside.

    Single source of truth for string-awareness across the parser, validator,
    and transpiler. Callers do `if i in literal_positions:` to skip in-string
    positions during their own char-by-char walks.
    """
    positions: set[int] = set()
    in_sq = False
    in_dq = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if in_sq:
            positions.add(i)
            if c == "\\" and i + 1 < n:
                positions.add(i + 1)
                i += 2
                continue
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            positions.add(i)
            if c == "\\" and i + 1 < n:
                positions.add(i + 1)
                i += 2
                continue
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == "'":
            in_sq = True
            positions.add(i)
            i += 1
            continue
        if c == '"':
            in_dq = True
            positions.add(i)
            i += 1
            continue
        i += 1
    return frozenset(positions)


def _find_outside_strings(s: str, needle: str, start: int = 0) -> int:
    """Return the index of the first occurrence of `needle` in `s` at or after
    `start` that is NOT inside a string literal. Returns -1 if not found.

    Thin wrapper over `_string_literal_positions` — provided for sites that
    only need substring search.
    """
    literal_positions = _string_literal_positions(s)
    n = len(s)
    needle_len = len(needle)
    for i in range(start, n - needle_len + 1):
        if i in literal_positions:
            continue
        if s.startswith(needle, i):
            return i
    return -1


def _scan_comprehension_args(s: str, start: int) -> tuple[str, str, str, int] | None:
    """Scan a comprehension argument list starting just after a `count(` or `exists(`.

    Expects the shape `<ident> in <ident> where <pred>` and walks forward tracking:
      - paren depth (incremented on `(`, decremented on `)`)
      - single-quote string state (inside, parens / `in` / `where` are ignored)
      - double-quote string state (same)

    Backslash-escaped quotes inside string literals are honored.

    Args:
        s: the full expression string.
        start: offset immediately after the opening `(` of `count(...)` / `exists(...)`.

    Returns:
        `(var, coll, pred, end_offset)` where `end_offset` is the index of the
        matching closing `)` (so the caller can splice from `<head>(` to `end_offset+1`).
        Returns None if the shape doesn't match.
    """
    n = len(s)

    # Skip leading whitespace.
    i = start
    while i < n and s[i].isspace():
        i += 1

    # Parse the bound variable identifier.
    m = _IDENT_RE.match(s, i)
    if not m:
        return None
    var = m.group(0)
    i = m.end()

    # Expect whitespace, then `in`, then whitespace.
    if i >= n or not s[i].isspace():
        return None
    while i < n and s[i].isspace():
        i += 1
    if not s.startswith("in", i) or i + 2 >= n or not s[i + 2].isspace():
        return None
    i += 2
    while i < n and s[i].isspace():
        i += 1

    # Parse the collection reference. Accepts a bare identifier OR a dotted
    # attribute chain (e.g. `v.items` for nested comprehensions over an
    # iterated-row field). The string-literal-aware scanner downstream still
    # handles parens correctly; we just need a slightly richer collection shape.
    m = _IDENT_RE.match(s, i)
    if not m:
        return None
    coll_start = m.end()
    i = coll_start
    # Allow zero-or-more `.ident` continuations.
    while i < n and s[i] == ".":
        m2 = _IDENT_RE.match(s, i + 1)
        if not m2:
            return None
        i = m2.end()
    coll = s[m.start():i]

    # Expect whitespace, then `where`, then whitespace.
    if i >= n or not s[i].isspace():
        return None
    while i < n and s[i].isspace():
        i += 1
    if not s.startswith("where", i) or i + 5 >= n or not s[i + 5].isspace():
        return None
    i += 5
    while i < n and s[i].isspace():
        i += 1

    # Walk the predicate until the matching closing `)` of the outer call.
    # String-literal awareness via the shared primitive.
    pred_start = i
    depth = 0
    literal_positions = _string_literal_positions(s)
    while i < n:
        if i in literal_positions:
            i += 1
            continue
        ch = s[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                # End of outer call.
                pred = s[pred_start:i].rstrip()
                if not pred:
                    return None
                return (var, coll, pred, i)
            depth -= 1
            i += 1
            continue
        i += 1

    # Reached EOF without closing paren.
    return None


def _rewrite_comprehensions_for_ast(expr: str) -> str:
    """Rewrite CIVIL `count(... where ...)` / `exists(... where ...)` to Python comprehensions.

    Walks `expr` left-to-right looking for `count(` and `exists(`; for each, attempts
    `_scan_comprehension_args`. On success, rewrites the matched span:

        count(v in coll where pred) → len([v for v in coll if pred])
        exists(v in coll where pred) → any(v for v in coll if pred)

    On `None`, leaves the substring untouched so the existing flat-form
    (`count(<list>)` / `exists(<field>)`) path runs.

    Defensive guard: if any `count(...)` or `exists(...)` substring contains both
    ` in ` and ` where ` keywords after rewrite, raises ValueError — that pattern
    indicates the scanner failed to consume an apparent comprehension.
    """
    out_parts: list[str] = []
    i = 0
    n = len(expr)
    literal_positions = _string_literal_positions(expr)
    while i < n:
        # Inside a string literal — copy verbatim and advance.
        if i in literal_positions:
            out_parts.append(expr[i])
            i += 1
            continue

        # Outside strings — try to match a head. Token-boundary check ensures
        # we don't match `recount(`, `_exists(`, etc.
        head = None
        head_len = 0
        if (
            expr.startswith("count(", i)
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
        ):
            head = "count"
            head_len = 6  # len("count(")
        elif (
            expr.startswith("exists(", i)
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
        ):
            head = "exists"
            head_len = 7  # len("exists(")

        if head is None:
            out_parts.append(expr[i])
            i += 1
            continue

        scan = _scan_comprehension_args(expr, i + head_len)
        if scan is None:
            # Not a comprehension shape — leave the head + `(` untouched and continue
            # past the `(`. The flat-form path will pick it up at ast.parse time.
            out_parts.append(expr[i : i + head_len])
            i += head_len
            continue

        var, coll, pred, end = scan
        # Recursively rewrite the predicate so nested comprehensions are lowered too.
        pred_rewritten = _rewrite_comprehensions_for_ast(pred)
        if head == "count":
            replacement = f"len([{var} for {var} in {coll} if {pred_rewritten}])"
        else:  # exists
            replacement = f"any({var} for {var} in {coll} if {pred_rewritten})"
        out_parts.append(replacement)
        i = end + 1  # skip the matching `)`

    result = "".join(out_parts)

    # Defensive guard: every remaining `count(...)` / `exists(...)` substring
    # should be either flat-form (no ` in ` / ` where `) or already rewritten.
    # If we still see both keywords inside such a substring, the scanner left
    # a comprehension partially consumed. String-aware via the shared primitive
    # so heads buried inside string literals are not re-scanned.
    result_literal_positions = _string_literal_positions(result)
    for fn in ("count", "exists"):
        head_re = re.compile(rf"\b{fn}\(")
        for m in head_re.finditer(result):
            if m.start() in result_literal_positions:
                continue
            # Find the matching `)` (paren-depth tracking, string-aware).
            depth = 0
            j = m.end() - 1  # position of the `(`
            close = None
            while j < len(result):
                if j in result_literal_positions:
                    j += 1
                    continue
                ch = result[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        close = j
                        break
                j += 1
            if close is None:
                continue
            inner = result[m.end():close]
            if " in " in inner and " where " in inner:
                raise ValueError(
                    f"partial comprehension rewrite — likely scanner bug: {expr!r}"
                )

    return result


def _civil_to_python(expr: str) -> str:
    """Translate CIVIL boolean/logical operators to Python equivalents for ast.parse.

    CIVIL uses C-style operators: || → or, && → and, !x → not x.
    Also rewrites in(...) → in_(...) since 'in' is a Python keyword.

    Comprehension forms (`count(v in coll where ...)` / `exists(v in coll where ...)`)
    are rewritten to Python list/generator comprehensions BEFORE the operator
    translation runs, so the predicate's CIVIL operators get translated in the
    next steps.
    """
    expr = _rewrite_comprehensions_for_ast(expr)
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

    bound_names: list[str] = field(default_factory=list)
    """Comprehension bound names (e.g. the `v` in `count(v in coll where ...)`)."""


def extract_refs(
    expr: str,
    computed_names: set[str],
    table_names: set[str],
) -> ExprRefs:
    """Walk the AST of a CIVIL expression and return categorized references.

    Two-pass approach:
      Pass 1 — collect entity names (PascalCase identifiers used as Attribute
                node values) so they are suppressed in the Name pass.
      Pass 2 — recursive `_visit(node, bound_names)` that classifies all
                remaining Name, Attribute, and Call nodes while scoping
                comprehension bound names per ListComp/GeneratorExp.

    Guards:
    - ast.Attribute where node.value is a Call (e.g. table(...).column) is skipped
      in entity_fields collection to prevent a crash on node.value.id.
    - 'in(...)' is rewritten to 'in_(...)' before parsing to avoid SyntaxError.
    - CIVIL boolean operators (||, &&, !) are translated to Python equivalents.
    - Comprehension forms are rewritten to Python list/generator comprehensions
      before parsing; bound names are scoped per comprehension and iterated-row
      field accesses are suppressed.
    - Inside a comprehension predicate, any bare Name that is not the bound
      iterator and not a PascalCase entity name raises ValueError — qualified
      `<bound>.<field>` access is required.
    """
    expr = _civil_to_python(expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse CIVIL expression: {expr!r}") from exc

    refs = ExprRefs()

    # Pass 1: collect entity names (left-hand side of Attribute nodes that are
    # simple Names — i.e., PascalCase entity names like 'Household'). Walks the
    # entire tree (including inside comprehensions) so iterated-row PascalCase
    # entity values, if any, are still recognized.
    attribute_value_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attribute_value_ids.add(node.value.id)

    # Pass 2: recursive visit with bound-name scoping.
    def _visit(node: ast.AST, bound_names: frozenset[str]) -> None:
        # ListComp / GeneratorExp: introduce bound names for the elt + ifs;
        # walk the iter in the outer scope.
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            new_bound = set(bound_names)
            for gen in node.generators:
                # Walk the iterable in the OLD bound scope (the iterable lives outside).
                _visit(gen.iter, bound_names)
                # Add target names. Targets are typically a single Name; tuple
                # targets are not produced by our rewrite, but handle defensively.
                if isinstance(gen.target, ast.Name):
                    new_bound.add(gen.target.id)
                    if gen.target.id not in refs.bound_names:
                        refs.bound_names.append(gen.target.id)
                elif isinstance(gen.target, ast.Tuple):
                    for elt in gen.target.elts:
                        if isinstance(elt, ast.Name):
                            new_bound.add(elt.id)
                            if elt.id not in refs.bound_names:
                                refs.bound_names.append(elt.id)
            new_bound_fs = frozenset(new_bound)
            # Walk the comprehension's `if` predicates and the element expression
            # with the NEW bound scope.
            for gen in node.generators:
                for if_clause in gen.ifs:
                    _visit(if_clause, new_bound_fs)
            _visit(node.elt, new_bound_fs)
            return

        # Attribute: classify or suppress based on the value.
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                # Suppress iterated-row field accesses: <bound>.<field> is not a graph ref.
                if node.value.id in bound_names:
                    return
                refs.entity_fields.append(f"{node.value.id}.{node.attr}")
            else:
                # E.g. table(...).column — recurse into the value so any nested
                # refs (like table('name', ...) Call args) are picked up.
                for child in ast.iter_child_nodes(node):
                    _visit(child, bound_names)
            return

        # Call: table('name', ...) emits a table ref; recurse into func and args.
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "table" and node.args:
                try:
                    table_name = ast.literal_eval(node.args[0])
                    if isinstance(table_name, str):
                        refs.table_refs.append(table_name)
                except (ValueError, TypeError):
                    pass
            for child in ast.iter_child_nodes(node):
                _visit(child, bound_names)
            return

        # Name: classify or suppress.
        if isinstance(node, ast.Name):
            name = node.id
            # Suppress the bound iterator itself.
            if name in bound_names:
                return
            # Suppress entity names (captured via Attribute) and known CIVIL function names.
            if name in attribute_value_ids or name in _CIVIL_FUNCTIONS:
                return
            # Strict qualified-access enforcement: inside a predicate, any bare Name
            # that is neither bound nor a PascalCase entity must be raised.
            if bound_names:
                raise ValueError(
                    f"comprehension predicate references bare name '{name}' — "
                    f"qualified <bound>.<field> access is required, or hoist this "
                    f"reference outside the predicate"
                )
            if name in computed_names:
                refs.computed_refs.append(name)
            elif name in table_names:
                refs.table_refs.append(name)
            elif name == name.upper() and len(name) > 1:
                refs.constant_refs.append(name)
            return

        # Default: recurse into all children with the current bound_names.
        for child in ast.iter_child_nodes(node):
            _visit(child, bound_names)

    _visit(tree, frozenset())

    return refs


def extract_refs_from_computed(
    field_def: dict,
    computed_names: set[str],
    table_names: set[str],
) -> ExprRefs:
    """Extract refs from a computed field definition.

    Handles 'expr' (single expression), 'conditional' (if/then/else), and
    'invoke:' (CIVIL v4 ruleset module call). invoke: fields have no inline
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
        bound_names=  r1.bound_names    + r2.bound_names    + r3.bound_names,
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
    entities = doc.get("inputs", {})
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
