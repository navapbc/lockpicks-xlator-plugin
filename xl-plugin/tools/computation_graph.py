#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
CIVIL Computation Graph Generator

Reads a CIVIL DSL YAML file and produces two artifacts in the same directory:
  <program>.graph.yaml — dependency-ordered graph of all nodes (inputs, computed, rules)
  <program>.mmd        — Raw Mermaid diagram (loadable directly in any Mermaid viewer)

Each node records its kind, type, location (YAML path), depends_on list, and
used_by list (the reverse index of depends_on).

Usage (via xlator CLI):
    xlator graph <domain> <module>

    Example:
        xlator graph snap eligibility

Exit codes:
    0 — success (both output files written)
    1 — error (file not found, YAML parse error, expression parse error)
"""

import datetime
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from civil_expr import ExprRefs, extract_refs, extract_refs_from_computed  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_civil(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        fail(f"File not found: {path}")
    except yaml.YAMLError as e:
        fail(f"YAML parse error in {path}: {e}")


def _python_type(value: object) -> str:
    """Map a Python constant value to a CIVIL-style type string."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "unknown"


def _dedupe(items: list[str]) -> list[str]:
    """Remove duplicates while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _refs_to_dep_list(refs: ExprRefs) -> list[str]:
    return _dedupe(
        refs.entity_fields
        + refs.computed_refs
        + refs.constant_refs
        + refs.table_refs
    )


# ---------------------------------------------------------------------------
# Mermaid generation
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9]")


def _mermaid_id(key: str, used: dict[str, str]) -> str:
    """Return a Mermaid-safe node ID for a node key, deduplicating collisions."""
    base = _SAFE_ID_RE.sub("_", key)
    if key in used:
        return used[key]
    candidate = base
    suffix = 2
    while candidate in used.values():
        candidate = f"{base}_{suffix}"
        suffix += 1
    used[key] = candidate
    return candidate


def _mermaid_label(key: str, node: dict) -> str:
    kind = node["kind"]
    ntype = node.get("type", "")
    # Use custom label for invoke: nodes (shows sub-module reference)
    display = node.get("label", key)
    label_text = f"{display}<br/>{ntype}" if ntype else display
    if kind == "input":
        return f'(["{label_text}"])'
    if kind == "computed":
        return f'["{label_text}"]'
    # rule
    rule_kind = node.get("rule_kind", "")
    label_text = f"{display}<br/>{rule_kind}" if rule_kind else display
    return "{" + "{" + f'"{label_text}"' + "}" + "}"


def build_mermaid(nodes: dict[str, dict]) -> str:
    lines = ["graph TD"]
    id_map: dict[str, str] = {}

    # Node declarations
    for key, node in nodes.items():
        nid = _mermaid_id(key, id_map)
        lines.append(f"  {nid}{_mermaid_label(key, node)}")

    # Edges
    for key, node in nodes.items():
        nid = _mermaid_id(key, id_map)
        for dep in node.get("depends_on", []):
            if dep in id_map:
                dep_id = id_map[dep]
            else:
                dep_id = _mermaid_id(dep, id_map)
            lines.append(f"  {dep_id} --> {nid}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main graph builder
# ---------------------------------------------------------------------------

def build_graph(civil_path: str) -> tuple[dict, str]:
    """Return (graph_data_dict, mermaid_string) for the given CIVIL YAML path."""
    doc = load_civil(civil_path)

    p = pathlib.Path(civil_path)
    # stem is e.g. "eligibility.civil"; remove ".civil" suffix
    stem = p.stem  # "eligibility.civil"
    program = stem[: -len(".civil")] if stem.endswith(".civil") else stem
    # domain: civil files live in <domain>/specs/
    try:
        domain = p.parent.parent.name
    except Exception:
        domain = "unknown"

    # Known name sets
    computed_names: set[str] = set((doc.get("computed") or {}).keys())
    table_names: set[str] = set((doc.get("tables") or {}).keys())

    nodes: dict[str, dict] = {}

    # --- INPUT nodes: fact fields ---
    for entity_name, entity_def in (doc.get("inputs") or {}).items():
        for field_name, field_def in (entity_def.get("fields") or {}).items():
            key = f"{entity_name}.{field_name}"
            nodes[key] = {
                "kind": "input",
                "type": field_def.get("type", "unknown"),
                "location": f"inputs.{entity_name}.{field_name}",
                "depends_on": [],
                "used_by": [],
            }

    # --- INPUT nodes: constants ---
    for name, value in (doc.get("constants") or {}).items():
        nodes[name] = {
            "kind": "input",
            "type": _python_type(value),
            "location": f"constants.{name}",
            "depends_on": [],
            "used_by": [],
        }

    # --- INPUT nodes: tables ---
    for name in (doc.get("tables") or {}):
        nodes[name] = {
            "kind": "input",
            "type": "table",
            "location": f"tables.{name}",
            "depends_on": [],
            "used_by": [],
        }

    # --- COMPUTED nodes (already in topological declaration order) ---
    for field_name, field_def in (doc.get("computed") or {}).items():
        node: dict = {
            "kind": "computed",
            "type": field_def.get("type", "unknown"),
            "location": f"computed.{field_name}",
            "depends_on": [],
            "used_by": [],
        }
        if field_def.get("description"):
            node["description"] = field_def["description"]
        # CIVIL v4: annotate invoke: nodes with the sub-module name
        if field_def.get("invoke") and field_def.get("module"):
            node["invoke_module"] = field_def["module"]
            node["label"] = f"{field_name} [→ {field_def['module']}]"
        nodes[field_name] = node

    # --- RULE nodes ---
    for rule in doc.get("rules") or []:
        nodes[rule["id"]] = {
            "kind": "rule",
            "rule_kind": rule.get("kind", "deny"),
            "location": f"rules[{rule['id']}]",
            "depends_on": [],
            "used_by": [],
        }

    # --- Build depends_on ---
    for field_name, field_def in (doc.get("computed") or {}).items():
        try:
            refs = extract_refs_from_computed(field_def, computed_names, table_names)
        except ValueError as exc:
            fail(f"computed.{field_name}: {exc}")
        nodes[field_name]["depends_on"] = _refs_to_dep_list(refs)

    for rule in doc.get("rules") or []:
        when = rule.get("when", "true")
        if when == "true":
            deps: list[str] = []
        else:
            try:
                refs = extract_refs(when, computed_names, table_names)
            except ValueError as exc:
                fail(f"rules[{rule['id']}].when: {exc}")
            deps = _refs_to_dep_list(refs)
        nodes[rule["id"]]["depends_on"] = deps

    # --- Build used_by (reverse index) ---
    for node_key, node in nodes.items():
        for dep in node["depends_on"]:
            if dep in nodes:
                nodes[dep]["used_by"].append(node_key)

    graph_data = {
        "domain": domain,
        "program": program,
        "generated": str(datetime.date.today()),
        "nodes": nodes,
    }

    mermaid = build_mermaid(nodes)
    return graph_data, mermaid


# ---------------------------------------------------------------------------
# YAML serialisation helpers
# ---------------------------------------------------------------------------

class _LiteralStr(str):
    pass


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


def _dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a computation graph from a CIVIL DSL YAML file."
    )
    parser.add_argument("civil_yaml", help="Path to the .civil.yaml file")
    args = parser.parse_args()

    graph_data, mermaid = build_graph(args.civil_yaml)

    p = pathlib.Path(args.civil_yaml)
    stem = p.stem
    program = stem[: -len(".civil")] if stem.endswith(".civil") else stem
    out_dir = p.parent

    graph_yaml_path = out_dir / f"{program}.graph.yaml"
    mmd_path = out_dir / f"{program}.mmd"

    # Write YAML — preserve insertion order, no sorting
    yaml_dumper = yaml.Dumper
    yaml_dumper.add_representer(dict, _dict_representer)

    with open(graph_yaml_path, "w") as f:
        yaml.dump(graph_data, f, Dumper=yaml_dumper, sort_keys=False, allow_unicode=True)

    # Write raw Mermaid (no markdown wrapper)
    with open(mmd_path, "w") as f:
        f.write(mermaid)
        f.write("\n")

    print(f"✓ Graph written: {graph_yaml_path}")
    print(f"✓ Diagram:       {mmd_path}")


if __name__ == "__main__":
    main()
