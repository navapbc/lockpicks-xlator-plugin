#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# ///
"""
Catala file or dependency-graph → Graphviz dot / Mermaid mmd / PNG converter

Accepts either a .catala_en source file or a pre-built .graph.json file.
When given a .catala_en file, runs `catala dependency-graph` to produce the
.graph.json alongside the source file, then converts it.

Usage:
    catala_depgraph.py <file.catala_en|file.graph.json> [--format dot|mmd|png] [--scope <scope>]

Output is always written to a file named <stem>-depgraph.<ext> next to the input file.

Examples:
    # From source (generates graph.json automatically)
    catala_depgraph.py <path_to>/snap/output/eligibility.catala_en

    # From pre-built graph.json
    catala_depgraph.py <path_to>/ak_doh/output/earned_income.graph.json

    # Mermaid, specific scope
    catala_depgraph.py <path_to>/snap/output/eligibility.catala_en \\
        --format mmd --scope EligibilityDecision

    # Render directly to PNG (requires Graphviz installed)
    catala_depgraph.py <path_to>/snap/output/eligibility.catala_en --format png

Exit codes:
    0 — success
    1 — error (message printed to stderr)
"""

import sys
import json
import argparse
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_id(name: str) -> str:
    """Return a dot/mmd-safe identifier (replace non-alphanum with _)."""
    return "".join(c if c.isalnum() else "_" for c in name)


def _intra_scope_graphs(data: dict, scope_filter: str | None) -> dict[str, tuple[dict, list]]:
    """Return {scope_name: (nodes_dict, edges_list)} for selected scopes."""
    intra = data.get("intra_scopes", {})
    result = {}
    for scope_name, scope in intra.items():
        if scope_filter and scope_name != scope_filter:
            continue
        result[scope_name] = (scope["nodes"], scope["edges"])
    return result


# ---------------------------------------------------------------------------
# Graphviz dot
# ---------------------------------------------------------------------------

def _dot_node_attrs(node_id: str, label: str, in_degree: int, out_degree: int) -> str:
    """Choose shape/style based on topology."""
    if in_degree == 0:
        # leaf input — plain box
        return f'[label="{label}" shape=box style=filled fillcolor="#e8f4f8"]'
    if out_degree == 0:
        # root output — double circle / bold
        return f'[label="{label}" shape=doublecircle style=filled fillcolor="#fde8e8"]'
    # intermediate computed
    return f'[label="{label}" shape=ellipse]'


def to_dot(data: dict, scope_filter: str | None = None) -> str:
    scopes = _intra_scope_graphs(data, scope_filter)
    lines = ["digraph computation_graph {", "    rankdir=LR;", "    node [fontname=Helvetica fontsize=11];", ""]

    for scope_name, (nodes, edges) in scopes.items():
        # compute degree maps
        in_deg: dict[str, int] = {nid: 0 for nid in nodes}
        out_deg: dict[str, int] = {nid: 0 for nid in nodes}
        for edge in edges:
            f, t = str(edge["from"]), str(edge["to"])
            out_deg[f] = out_deg.get(f, 0) + 1
            in_deg[t] = in_deg.get(t, 0) + 1

        safe_scope = _safe_id(scope_name)
        lines.append(f"    subgraph cluster_{safe_scope} {{")
        lines.append(f'        label="{scope_name}";')
        lines.append( "        style=rounded;")
        lines.append("")

        for nid, label in nodes.items():
            safe = f"{safe_scope}_{_safe_id(label)}"
            attrs = _dot_node_attrs(nid, label, in_deg.get(nid, 0), out_deg.get(nid, 0))
            lines.append(f"        {safe} {attrs};")

        lines.append("")
        for edge in edges:
            f = f"{safe_scope}_{_safe_id(nodes[str(edge['from'])])}"
            t = f"{safe_scope}_{_safe_id(nodes[str(edge['to'])])}"
            lines.append(f"        {f} -> {t};")

        lines.append("    }")
        lines.append("")

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------

def to_mmd(data: dict, scope_filter: str | None = None) -> str:
    scopes = _intra_scope_graphs(data, scope_filter)
    lines = ["flowchart LR"]

    for scope_name, (nodes, edges) in scopes.items():
        in_deg: dict[str, int] = {nid: 0 for nid in nodes}
        out_deg: dict[str, int] = {nid: 0 for nid in nodes}
        for edge in edges:
            f, t = str(edge["from"]), str(edge["to"])
            out_deg[f] = out_deg.get(f, 0) + 1
            in_deg[t] = in_deg.get(t, 0) + 1

        safe_scope = _safe_id(scope_name)
        lines.append(f"    subgraph {safe_scope}[\"{scope_name}\"]")

        for nid, label in nodes.items():
            safe = f"{safe_scope}_{_safe_id(label)}"
            ind = in_deg.get(nid, 0)
            outd = out_deg.get(nid, 0)
            if ind == 0:
                # input — rectangle
                lines.append(f"        {safe}[{label}]")
            elif outd == 0:
                # output — stadium / bold
                lines.append(f"        {safe}(({label}))")
            else:
                # intermediate — rounded
                lines.append(f"        {safe}({label})")

        for edge in edges:
            f = f"{safe_scope}_{_safe_id(nodes[str(edge['from'])])}"
            t = f"{safe_scope}_{_safe_id(nodes[str(edge['to'])])}"
            lines.append(f"        {f} --> {t}")

        lines.append("    end")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Catala source or graph.json to Graphviz dot or Mermaid mmd."
    )
    parser.add_argument("input", help="Path to <program>.catala_en or <program>.graph.json")
    parser.add_argument(
        "--format", choices=["dot", "mmd", "png"], default="dot",
        help="Output format: dot (Graphviz), mmd (Mermaid), or png (renders via dot). Default: dot"
    )
    parser.add_argument(
        "--scope", default=None,
        help="Render only this intra-scope subgraph (e.g. EligibilityDecision). Default: all"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.name.endswith(".catala_en"):
        graph_json_path = input_path.parent / input_path.name.replace(".catala_en", ".graph.json")
        print(f"running: catala dependency-graph {input_path}", file=sys.stderr)
        result = subprocess.run(
            ["catala", "dependency-graph", input_path.name],
            cwd=input_path.parent,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"error: catala dependency-graph failed:\n{result.stderr}", file=sys.stderr)
            return 1
        graph_json_path.write_text(result.stdout)
        print(f"wrote {graph_json_path}", file=sys.stderr)
        json_path = graph_json_path
    else:
        json_path = input_path

    try:
        data = json.loads(json_path.read_text())
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 1

    scopes = data.get("intra_scopes", {})
    if args.scope and args.scope not in scopes:
        available = ", ".join(scopes) or "(none)"
        print(f"error: scope '{args.scope}' not found. Available: {available}", file=sys.stderr)
        return 1

    if args.format in ("dot", "png"):
        output = to_dot(data, args.scope)
    else:
        output = to_mmd(data, args.scope)

    module = json_path.name.replace(".graph.json", "")

    if args.format == "png":
        dot_path = json_path.parent / f"{module}-depgraph.dot"
        png_path = json_path.parent / f"{module}-depgraph.png"
        dot_path.write_text(output)
        print(f"wrote {dot_path}", file=sys.stderr)
        result = subprocess.run(["dot", "-Tpng", str(dot_path), "-o", str(png_path)])
        if result.returncode != 0:
            return 1
        print(f"wrote {png_path}")
    elif args.format == "mmd":
        out_path = json_path.parent / f"{module}-depgraph.mmd"
        out_path.write_text(output)
        print(f"wrote {out_path}")
    else:
        out_path = json_path.parent / f"{module}-depgraph.dot"
        out_path.write_text(output)
        print(f"wrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
