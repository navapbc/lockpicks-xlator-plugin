# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lazy `[[target]]` block injector for `output/clerk.toml`.

Per plan `docs/plans/2026-06-04-002-fix-catala-to-python-bugs-plan.md` U1.

Reads `output/clerk.toml`, returns the `target_dir` declared there (defaults
to `_targets`, matching clerk's own default and `clerk_toml_defaults.SPEC_TIER`),
and appends a `[[target]]` block when none matches the requested target name.

Append-only: never rewrites or reorders existing blocks. Creates the file from
`clerk_toml_defaults.SPEC_TIER` when absent so a fresh `xlator new-domain`
reaches a working state on first invocation (origin R5).

CLI
---
``clerk_target_inject.py <output_dir> <target_name> <module_name> <specs_dir>``

- ``output_dir`` — directory containing (or where to create) ``clerk.toml``.
- ``target_name`` — value of ``[[target]] name`` to ensure exists.
- ``module_name`` — snake_case top-level Catala module that seeds the
  dependency walk (e.g., ``passes_income`` → maps to CamelCase
  ``Passes_income``).
- ``specs_dir`` — directory containing ``<module>.catala_en`` source files.

Stdout: a single line, the ``target_dir`` value to use.
Stderr: informational and error messages.
Exit 1 on any error.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from clerk_toml_defaults import SPEC_TIER  # noqa: E402

_MODULE_RE = re.compile(r"^>\s*Module\s+([A-Z][A-Za-z0-9_]*)\s*$", re.MULTILINE)
_USING_RE = re.compile(r"^>\s*Using\s+([A-Z][A-Za-z0-9_]*)\s*$", re.MULTILINE)


def _camel_module(name: str) -> str:
    """snake_case → Catala CamelCase: first letter upper, rest preserved.

    ``"passes_income"`` → ``"Passes_income"``. Matches the convention in
    ``xl-plugin/core/catala-authoring-quickref.md`` §"File preamble".
    """
    if not name:
        return name
    return name[:1].upper() + name[1:]


def _parse_module_graph(specs_dir: Path) -> dict[str, list[str]]:
    """Return ``{ModuleName: [DepName, ...]}`` for every ``> Module`` in specs.

    Raises ``ValueError`` (with a stderr-ready message) on multi-module files
    or duplicate module declarations.
    """
    graph: dict[str, list[str]] = {}
    file_for_module: dict[str, Path] = {}
    for cat_file in sorted(specs_dir.glob("*.catala_en")):
        text = cat_file.read_text(encoding="utf-8")
        mods = _MODULE_RE.findall(text)
        if len(mods) == 0:
            continue
        if len(mods) > 1:
            raise ValueError(
                f"{cat_file} declares more than one > Module directive ({mods})"
            )
        module_name = mods[0]
        if module_name in file_for_module:
            raise ValueError(
                f"Module {module_name!r} declared in both "
                f"{file_for_module[module_name]} and {cat_file}"
            )
        file_for_module[module_name] = cat_file
        graph[module_name] = _USING_RE.findall(text)
    return graph


def _topo_order(graph: dict[str, list[str]], root: str) -> list[str]:
    """Post-order DFS from ``root``: leaves first, ``root`` last.

    Detects cycles and missing referenced modules and raises ``ValueError``.
    Cycle messages include the full participant set so the user can find the
    chain; missing-module messages include the parent ``> Using`` site so
    the file to fix is unambiguous.
    """
    if root not in graph:
        raise ValueError(
            f"Module {root!r} not found in specs/. "
            f"Available: {sorted(graph)}"
        )
    order: list[str] = []
    visited: set[str] = set()
    in_progress: set[str] = set()

    def visit(mod: str, via: str | None = None) -> None:
        if mod in visited:
            return
        if mod in in_progress:
            raise ValueError(
                f"Cyclic module dependency at {mod!r}; "
                f"participants: {sorted(in_progress)}"
            )
        if mod not in graph:
            ctx = f" (referenced by {via!r})" if via else ""
            raise ValueError(
                f"Module {mod!r} referenced via > Using but not declared "
                f"in any specs/*.catala_en{ctx}"
            )
        in_progress.add(mod)
        for dep in graph[mod]:
            visit(dep, via=mod)
        in_progress.discard(mod)
        visited.add(mod)
        order.append(mod)

    visit(root)
    return order


def _read_clerk_toml(clerk_toml_path: Path) -> dict:
    """Parse ``clerk.toml`` once and surface a clean ``ValueError`` on failure.

    Single source of truth for TOML parsing in this module — callers reuse
    the returned dict instead of re-opening the file.
    """
    try:
        with open(clerk_toml_path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"{clerk_toml_path} is not valid TOML: {e}") from None


def _target_dir_from(data: dict) -> str:
    """Return ``[project] target_dir``; default to ``_targets`` when absent."""
    return data.get("project", {}).get("target_dir", "_targets")


def _has_target_block(data: dict, target_name: str) -> bool:
    """True iff ``data`` contains a ``[[target]]`` block with the given name."""
    for entry in data.get("target", []):
        if entry.get("name") == target_name:
            return True
    return False


def _append_target_block(
    clerk_toml_path: Path, target_name: str, modules: list[str]
) -> None:
    """Append a ``[[target]]`` block; ensure trailing newline first."""
    current = clerk_toml_path.read_text(encoding="utf-8")
    if current and not current.endswith("\n"):
        current = current + "\n"
    modules_repr = ", ".join(f'"{m}"' for m in modules)
    block = (
        "\n"
        "[[target]]\n"
        f'name = "{target_name}"\n'
        f"modules = [{modules_repr}]\n"
        'backends = ["python"]\n'
    )
    clerk_toml_path.write_text(current + block, encoding="utf-8")


def ensure_target_injected(
    output_dir: Path,
    target_name: str,
    module_name: str,
    specs_dir: Path,
) -> str:
    """Idempotently inject a ``[[target]]`` block; return ``target_dir``."""
    clerk_toml = output_dir / "clerk.toml"

    if not clerk_toml.exists():
        clerk_toml.write_text(SPEC_TIER, encoding="utf-8")
        print(
            f"Created {clerk_toml} (spec-tier default from clerk_toml_defaults.SPEC_TIER)",
            file=sys.stderr,
        )

    data = _read_clerk_toml(clerk_toml)
    target_dir = _target_dir_from(data)

    if _has_target_block(data, target_name):
        return target_dir

    graph = _parse_module_graph(specs_dir)
    root_module = _camel_module(module_name)
    modules_in_order = _topo_order(graph, root_module)
    _append_target_block(clerk_toml, target_name, modules_in_order)
    print(
        f"Injected [[target]] name={target_name!r} "
        f"modules={modules_in_order} into {clerk_toml}",
        file=sys.stderr,
    )
    return target_dir


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "Usage: clerk_target_inject.py <output_dir> <target_name> "
            "<module_name> <specs_dir>",
            file=sys.stderr,
        )
        return 2
    output_dir = Path(argv[0])
    target_name = argv[1]
    module_name = argv[2]
    specs_dir = Path(argv[3])
    if not output_dir.is_dir():
        print(
            f"Error: output_dir not found or not a directory: {output_dir}",
            file=sys.stderr,
        )
        return 1
    if not specs_dir.is_dir():
        print(
            f"Error: specs_dir not found or not a directory: {specs_dir}",
            file=sys.stderr,
        )
        return 1
    try:
        target_dir = ensure_target_injected(
            output_dir, target_name, module_name, specs_dir
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(target_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
