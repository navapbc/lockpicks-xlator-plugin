# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""
CIVIL DSL Evaluator

Tree-walking evaluator that executes a parsed CIVIL ruleset against an inputs
dict and produces outputs + computed-field values + accumulated reasons. Built
on top of civil_expr.py's parser shim — same source of truth for CIVIL syntax
quirks (`||`/`&&`/`!`/`in(...)`).

Library API:
    from civil_eval import evaluate_civil, detect_stale, EvaluationError

    result = evaluate_civil(civil_doc, inputs)
    # result.outputs   → {"eligible": True, "reasons": [...]}
    # result.computed  → {"net_income": 1680.0, ...}
    # result.reasons   → [{"code": "...", "message": "...", "citations": [...]}]
    # result.debug     → {"rules_fired": ["FED-SNAP-DENY-001"]}

    diff = detect_stale(civil_doc, test_case)
    # diff is None when current expected: matches recomputed; otherwise a
    # StaleCaseDiff{current_expected, recomputed_expected, diff}.

v1 surface:
    - Arithmetic (+, -, *, /), parentheses
    - Comparison (<, <=, >, >=, ==, !=)
    - Boolean (and/or/not/&&/||/!)
    - Function calls: max, min, in_, between, is_null, exists, count, table
    - Field access: Entity.field and bare field (single-entity)
    - Computed fields with expr:/conditional:/table_lookup:
    - String enum comparison
    - Rule firing by priority (ascending); mutex_group semantics
    - Output evaluation via expr:/conditional:/default

v1 non-goals (raise EvaluationError):
    - invoke: blocks (sub-module composition)
    - Date arithmetic / time-varying logic
    - then: actions other than add_reason: and set:
"""

from __future__ import annotations

import ast
import graphlib
from dataclasses import dataclass, field
from typing import Any

import civil_expr

# Numeric tolerance used by detect_stale for numeric expected: comparisons.
# Matches the test-suite convention documented in skills/create-tests/SKILL.md.
FLOAT_TOLERANCE = 0.005


class EvaluationError(Exception):
    """Raised when CIVIL evaluation fails. Carries structured {context, message}."""

    def __init__(self, context: str, message: str):
        self.context = context
        self.message = message
        super().__init__(f"{context}: {message}" if context else message)


@dataclass
class EvaluationResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    computed: dict[str, Any] = field(default_factory=dict)
    reasons: list[dict] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "outputs": self.outputs,
            "computed": self.computed,
            "reasons": self.reasons,
            "debug": self.debug,
        }


@dataclass
class StaleCaseDiff:
    current_expected: dict
    recomputed_expected: dict
    diff: dict[str, dict[str, Any]]


@dataclass
class _Context:
    inputs: dict[str, Any]          # flat: keyed by "Entity.field"; bare for single-entity
    entities: dict[str, dict]       # civil_doc["inputs"]
    constants: dict[str, Any]       # civil_doc["constants"]
    tables: dict[str, dict]         # civil_doc["tables"]
    computed: dict[str, Any]        # populated during topo walk
    reasons: list[dict]             # accumulated during rule firing
    set_outputs: dict[str, Any]     # rule-set values (used when no expr:/conditional:)
    fired_mutex_groups: set[str]    # tracks which groups have fired
    multi_entity: bool


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------


class _Evaluator(ast.NodeVisitor):
    """AST visitor that evaluates a CIVIL expression to a Python value."""

    def __init__(self, ctx: _Context, label: str = ""):
        self.ctx = ctx
        self.label = label

    def eval_expr(self, expr: str) -> Any:
        py = civil_expr._civil_to_python(expr)
        try:
            tree = ast.parse(py, mode="eval")
        except SyntaxError as exc:
            raise EvaluationError(self.label, f"parse error in {expr!r}: {exc}") from exc
        return self.visit(tree.body)

    # --- terminal nodes --------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        name = node.id
        # CIVIL literals lowercased
        if name == "true":
            return True
        if name == "false":
            return False
        if name in ("null", "None"):
            return None
        # Special: reasons list reference
        if name == "reasons":
            return self.ctx.reasons
        # Lookup chain
        if name in self.ctx.computed:
            return self.ctx.computed[name]
        if name in self.ctx.constants:
            return self.ctx.constants[name]
        # Single-entity: bare input field is allowed
        if not self.ctx.multi_entity and self.ctx.entities:
            (entity_name,) = self.ctx.entities.keys()
            qualified = f"{entity_name}.{name}"
            if qualified in self.ctx.inputs:
                return self.ctx.inputs[qualified]
            if name in self.ctx.inputs:
                return self.ctx.inputs[name]
            field_def = (self.ctx.entities[entity_name].get("fields") or {}).get(name)
            if field_def is not None:
                if field_def.get("optional"):
                    return field_def.get("default")
                raise EvaluationError(self.label, f"missing required input {name!r}")
        # Table reference returns table descriptor (rare; usually called via table())
        if name in self.ctx.tables:
            return self.ctx.tables[name]
        raise EvaluationError(self.label, f"undefined identifier {name!r}")

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        # Entity.field: resolve via inputs binding
        if isinstance(node.value, ast.Name) and node.value.id in self.ctx.entities:
            entity_name = node.value.id
            field_name = node.attr
            key = f"{entity_name}.{field_name}"
            if key in self.ctx.inputs:
                return self.ctx.inputs[key]
            if not self.ctx.multi_entity and field_name in self.ctx.inputs:
                return self.ctx.inputs[field_name]
            field_def = (self.ctx.entities[entity_name].get("fields") or {}).get(field_name)
            if field_def is None:
                raise EvaluationError(self.label, f"unknown field {key!r}")
            if field_def.get("optional"):
                return field_def.get("default")
            raise EvaluationError(self.label, f"missing required input {key!r}")
        # Otherwise: evaluate node.value (e.g. a Call returning a row dict) and access .attr
        value = self.visit(node.value)
        if isinstance(value, dict):
            if node.attr in value:
                return value[node.attr]
            raise EvaluationError(self.label, f"row has no column {node.attr!r}")
        raise EvaluationError(self.label, f"cannot access {node.attr!r} on {type(value).__name__}")

    # --- operators -------------------------------------------------------

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise EvaluationError(self.label, "division by zero")
            return left / right
        raise EvaluationError(self.label, f"unsupported binary operator {type(node.op).__name__}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise EvaluationError(self.label, f"unsupported unary operator {type(node.op).__name__}")

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = self.visit(v)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for v in node.values:
                result = self.visit(v)
                if result:
                    return result
            return result
        raise EvaluationError(self.label, f"unsupported boolean operator {type(node.op).__name__}")

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            else:
                raise EvaluationError(self.label, f"unsupported comparison {type(op).__name__}")
            if not ok:
                return False
            left = right
        return True

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)

    def visit_List(self, node: ast.List) -> list:
        return [self.visit(elt) for elt in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> list:
        return [self.visit(elt) for elt in node.elts]

    # --- function calls --------------------------------------------------

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise EvaluationError(self.label, "unsupported call form (only bare-name functions allowed)")
        fn = node.func.id

        if fn == "table":
            if not node.args:
                raise EvaluationError(self.label, "table() requires at least 1 argument")
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                table_name = first.value
            elif isinstance(first, ast.Name):
                table_name = first.id
            else:
                raise EvaluationError(self.label, "table() first arg must be a string literal or table name")
            if table_name not in self.ctx.tables:
                raise EvaluationError(self.label, f"unknown table {table_name!r}")
            keys = [self.visit(arg) for arg in node.args[1:]]
            return self._table_lookup(table_name, keys)

        args = [self.visit(arg) for arg in node.args]
        if fn == "max":
            if len(args) == 1 and isinstance(args[0], list):
                return max(args[0])
            return max(args)
        if fn == "min":
            if len(args) == 1 and isinstance(args[0], list):
                return min(args[0])
            return min(args)
        if fn == "in_":
            if len(args) != 2:
                raise EvaluationError(self.label, f"in_() expects 2 args, got {len(args)}")
            return args[0] in args[1]
        if fn == "between":
            if len(args) != 3:
                raise EvaluationError(self.label, f"between() expects 3 args, got {len(args)}")
            return args[1] <= args[0] <= args[2]
        if fn == "count":
            if len(args) != 1:
                raise EvaluationError(self.label, f"count() expects 1 arg, got {len(args)}")
            value = args[0]
            if value is None:
                return 0
            try:
                return len(value)
            except TypeError as exc:
                raise EvaluationError(
                    self.label,
                    f"count() requires a sized value, got {type(value).__name__}",
                ) from exc
        if fn == "is_null":
            if len(args) != 1:
                raise EvaluationError(self.label, f"is_null() expects 1 arg, got {len(args)}")
            return args[0] is None
        if fn == "exists":
            if len(args) != 1:
                raise EvaluationError(self.label, f"exists() expects 1 arg, got {len(args)}")
            return args[0] is not None
        if fn == "sum":
            if len(args) == 1 and isinstance(args[0], list):
                return sum(args[0])
            return sum(args)
        if fn == "abs":
            if len(args) != 1:
                raise EvaluationError(self.label, f"abs() expects 1 arg, got {len(args)}")
            return abs(args[0])
        raise EvaluationError(self.label, f"unknown function {fn!r}")

    def _table_lookup(self, table_name: str, keys: list[Any]) -> dict:
        table = self.ctx.tables[table_name]
        key_cols = list(table.get("key") or [])
        rows = list(table.get("rows") or [])
        if len(keys) != len(key_cols):
            raise EvaluationError(
                self.label,
                f"table {table_name!r} expects {len(key_cols)} key(s) {key_cols}, got {len(keys)}",
            )
        for row in rows:
            if all(row.get(col) == kv for col, kv in zip(key_cols, keys)):
                return row
        raise EvaluationError(
            self.label,
            f"no row in table {table_name!r} matching keys {dict(zip(key_cols, keys))}",
        )

    # --- fallback --------------------------------------------------------

    def generic_visit(self, node: ast.AST) -> Any:
        raise EvaluationError(self.label, f"unsupported AST node {type(node).__name__}")


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


def evaluate_civil(civil_doc: dict, inputs: dict) -> EvaluationResult:
    """Evaluate a CIVIL ruleset against an inputs dict.

    Steps:
      1. Normalize: resolve table_lookup: blocks into expr: form.
      2. Bind inputs into a flat keyed dict.
      3. Topologically sort computed: fields by reference dependencies, evaluate.
      4. Sort rules by priority (ascending), evaluate when:, fire then: actions.
      5. Evaluate outputs: by expr:/conditional:/default.

    Raises:
        EvaluationError: on missing inputs, unsupported features (invoke:),
        cycles in computed fields, or any expression-evaluation error.
    """
    if not isinstance(civil_doc, dict):
        raise EvaluationError("", f"civil_doc must be a dict, got {type(civil_doc).__name__}")

    # Resolve table_lookup: computed fields into expr: form (single source of truth).
    civil_doc = civil_expr.normalize_computed_doc(civil_doc)

    # Reject invoke: blocks (v1 non-goal).
    for name, fdef in (civil_doc.get("computed") or {}).items():
        if isinstance(fdef, dict) and fdef.get("invoke"):
            raise EvaluationError(f"computed {name}", "invoke: not supported in v1")

    entities = civil_doc.get("inputs") or {}
    multi_entity = len(entities) > 1

    bound_inputs = dict(inputs or {})

    # Normalize bare keys → Entity.field form for single-entity modules.
    if not multi_entity and entities:
        (entity_name,) = entities.keys()
        entity_fields = (entities[entity_name].get("fields") or {})
        for k, v in list(inputs.items() if inputs else []):
            if "." not in k and k in entity_fields:
                bound_inputs[f"{entity_name}.{k}"] = v

    ctx = _Context(
        inputs=bound_inputs,
        entities=entities,
        constants=dict(civil_doc.get("constants") or {}),
        tables=dict(civil_doc.get("tables") or {}),
        computed={},
        reasons=[],
        set_outputs={},
        fired_mutex_groups=set(),
        multi_entity=multi_entity,
    )
    evaluator = _Evaluator(ctx)

    # ----- Computed fields: topological sort + evaluate -----
    computed_block = civil_doc.get("computed") or {}
    computed_names = set(computed_block.keys())
    table_names = set(ctx.tables.keys())

    deps: dict[str, set[str]] = {}
    for name, fdef in computed_block.items():
        refs = civil_expr.extract_refs_from_computed(fdef, computed_names, table_names)
        deps[name] = set(refs.computed_refs) - {name}

    sorter = graphlib.TopologicalSorter(deps)
    try:
        topo_order = list(sorter.static_order())
    except graphlib.CycleError as ce:
        cycle = ce.args[1] if len(ce.args) > 1 else []
        raise EvaluationError("computed", f"cycle detected: {' -> '.join(cycle)}") from ce

    for name in topo_order:
        fdef = computed_block[name]
        evaluator.label = f"computed {name}"
        ctx.computed[name] = _eval_field_def(evaluator, fdef, name)

    # ----- Rules: sort by priority ascending; evaluate when:, fire then: -----
    rules = list(civil_doc.get("rules") or [])
    rules.sort(key=lambda r: r.get("priority", 0))
    rules_fired: list[str] = []

    for rule in rules:
        rule_id = rule.get("id", "?")
        evaluator.label = f"rule {rule_id}"
        when_expr = rule.get("when")
        if when_expr is None:
            raise EvaluationError(evaluator.label, "missing when: condition")
        when_value = evaluator.eval_expr(when_expr)
        if not when_value:
            continue
        mutex_group = rule.get("mutex_group")
        if mutex_group and mutex_group in ctx.fired_mutex_groups:
            continue
        if mutex_group:
            ctx.fired_mutex_groups.add(mutex_group)
        rules_fired.append(rule_id)
        for action in rule.get("then") or []:
            _apply_action(action, ctx, evaluator.label)

    # ----- Outputs -----
    outputs_block = civil_doc.get("outputs") or {}
    final_outputs: dict[str, Any] = {}
    for name, odef in outputs_block.items():
        evaluator.label = f"output {name}"
        if not isinstance(odef, dict):
            final_outputs[name] = odef
            continue
        if odef.get("expr"):
            final_outputs[name] = evaluator.eval_expr(odef["expr"])
        elif odef.get("conditional"):
            cond = odef["conditional"]
            branch = cond["then"] if evaluator.eval_expr(cond["if"]) else cond["else"]
            final_outputs[name] = evaluator.eval_expr(branch)
        elif name in ctx.set_outputs:
            final_outputs[name] = ctx.set_outputs[name]
        elif odef.get("type") in ("list", "set") and odef.get("item") == "Reason":
            final_outputs[name] = list(ctx.reasons)
        else:
            final_outputs[name] = odef.get("default")

    return EvaluationResult(
        outputs=final_outputs,
        computed=dict(ctx.computed),
        reasons=list(ctx.reasons),
        debug={"rules_fired": rules_fired},
    )


def _eval_field_def(evaluator: _Evaluator, fdef: dict, name: str) -> Any:
    """Evaluate a computed-field definition (expr / conditional). table_lookup
    has already been normalized away by normalize_computed_doc()."""
    if not isinstance(fdef, dict):
        raise EvaluationError(f"computed {name}", "field definition must be a mapping")
    if fdef.get("expr"):
        return evaluator.eval_expr(fdef["expr"])
    if fdef.get("conditional"):
        cond = fdef["conditional"]
        branch = cond["then"] if evaluator.eval_expr(cond["if"]) else cond["else"]
        return evaluator.eval_expr(branch)
    raise EvaluationError(f"computed {name}", "no expr: or conditional: block")


def _apply_action(action: dict, ctx: _Context, label: str) -> None:
    """Apply one then: action. v1 supports add_reason: and set:."""
    if not isinstance(action, dict):
        raise EvaluationError(label, f"action must be a mapping, got {type(action).__name__}")
    add_reason = action.get("add_reason")
    if add_reason is not None:
        reason: dict[str, Any] = {"code": add_reason.get("code")}
        if add_reason.get("message"):
            reason["message"] = add_reason["message"]
        if add_reason.get("citations"):
            reason["citations"] = add_reason["citations"]
        ctx.reasons.append(reason)
        return
    set_block = action.get("set")
    if set_block is not None:
        for k, v in set_block.items():
            ctx.set_outputs[k] = v
        return
    # Other action types are not supported in v1.
    present = [k for k in ("add_instruction", "add_to_set", "append_to_list") if action.get(k) is not None]
    if present:
        raise EvaluationError(label, f"then: action(s) not supported in v1: {present}")
    raise EvaluationError(label, f"unknown or empty then: action: {list(action.keys())}")


# ---------------------------------------------------------------------------
# Stale-case detection
# ---------------------------------------------------------------------------


def detect_stale(civil_doc: dict, test_case: dict) -> StaleCaseDiff | None:
    """Re-evaluate one test case against current CIVIL. Returns None when the
    current expected: still matches the recomputed result; otherwise a
    StaleCaseDiff describing the field-by-field divergence.

    Raises EvaluationError if the test case fails to evaluate.
    """
    inputs = test_case.get("inputs") or {}
    current_expected = dict(test_case.get("expected") or {})
    result = evaluate_civil(civil_doc, inputs)
    recomputed = _build_recomputed_expected(current_expected, result)
    diff = _diff_expected(current_expected, recomputed)
    if not diff:
        return None
    return StaleCaseDiff(
        current_expected=current_expected,
        recomputed_expected=recomputed,
        diff=diff,
    )


def _build_recomputed_expected(current: dict, result: EvaluationResult) -> dict:
    """Recompute the expected: shape using the same fields the current expected:
    declares. For fields not present in the result, fall back to the result's
    flat dict (so unexpected fields surface as diffs)."""
    recomputed: dict[str, Any] = {}
    for key in current:
        if key in result.outputs:
            recomputed[key] = result.outputs[key]
        elif key == "reasons":
            recomputed[key] = [{"code": r.get("code")} for r in result.reasons]
        else:
            recomputed[key] = result.outputs.get(key)
    # Also surface output fields not declared in current expected: (rare)
    for key, value in result.outputs.items():
        if key not in recomputed:
            recomputed[key] = value
    return recomputed


def _diff_expected(current: dict, recomputed: dict) -> dict[str, dict[str, Any]]:
    """Return field-keyed diff: {field: {current, recomputed}} for fields that
    differ. For numeric fields, applies ±FLOAT_TOLERANCE. For reason lists,
    compares codes in declaration order."""
    diff: dict[str, dict[str, Any]] = {}
    all_keys = set(current) | set(recomputed)
    for key in all_keys:
        c = current.get(key)
        r = recomputed.get(key)
        if _values_equal(c, r):
            continue
        diff[key] = {"current": c, "recomputed": r}
    return diff


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    # Numeric tolerance (exclude bools which are a numeric subtype in Python)
    if (
        isinstance(a, (int, float)) and not isinstance(a, bool)
        and isinstance(b, (int, float)) and not isinstance(b, bool)
    ):
        return abs(a - b) <= FLOAT_TOLERANCE
    # Reason-list comparison: match codes in order, ignore optional metadata in current
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_reason_equal(x, y) for x, y in zip(a, b))
    return a == b


def _reason_equal(a: Any, b: Any) -> bool:
    """Compare two reason entries by code, plus any other fields the current
    expected: declares. Recomputed reasons may carry extra metadata
    (message, citations) — those are ignored when current omits them."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key, val in a.items():
            if key not in b or b[key] != val:
                return False
        return True
    return a == b
