#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator validate-guidance: assert every name_ref in `<domain>/specs/guidance/`
files resolves to an entry in `<domain>/specs/naming-manifest.yaml`.

Reads the manifest and every guidance file with name-ref content
(`output-variables.yaml`, `input-variables.yaml`, `include-with-output.yaml`)
and reports:
  - missing name-refs (referenced in a guidance file but absent from manifest)
  - orphan manifest entries (in manifest but not referenced anywhere)

Exits 0 on clean alignment, 1 on alignment failure.

Usage:
    xlator validate-guidance <domain>
    xlator validate-guidance <domain> --json
    xlator validate-guidance <domain> --quiet

Output (human-readable, default):
    Validating <domain> ...
    OK / ERROR with per-file details

Output (--json):
    {
      "domain": "<name>",
      "ok": true|false,
      "missing": [{"file": "...", "name_ref": "..."}],
      "orphans": ["<name>", ...]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml


_GUIDANCE_FILES_WITH_NAME_REFS = (
    "output-variables.yaml",
    "input-variables.yaml",
)
_INCLUDE_WITH_OUTPUT_FILE = "include-with-output.yaml"


def _load_yaml(path: Path) -> Any:
    """Return parsed YAML or None when missing/malformed (caller handles)."""
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None


def _collect_manifest_names(manifest: dict) -> set[str]:
    """Return every variable name from inputs/computed/outputs blocks."""
    names: set[str] = set()
    if not isinstance(manifest, dict):
        return names

    inputs = manifest.get("inputs") or {}
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if not isinstance(fields, dict):
                continue
            names.update(str(field) for field in fields.keys())

    for top_key in ("computed", "outputs"):
        block = manifest.get(top_key) or {}
        if isinstance(block, dict):
            names.update(str(name) for name in block.keys())

    return names


def _collect_name_refs(data: Any) -> list[str]:
    """Walk a YAML structure and collect every value associated with a
    `name_ref:` key (at any depth)."""
    refs: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "name_ref" and isinstance(v, str):
                    refs.append(v)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return refs


def _collect_include_with_output(data: Any) -> list[str]:
    """Extract name strings from include-with-output.yaml. Accepts:
       - flat list of strings: ["foo", "bar"]
       - dict with `include_with_output:` key holding a list of strings
       - list of objects with `name_ref:` key
    """
    refs: list[str] = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                refs.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name_ref"), str):
                refs.append(item["name_ref"])
        return refs

    if isinstance(data, dict):
        block = data.get("include_with_output")
        if isinstance(block, list):
            for item in block:
                if isinstance(item, str):
                    refs.append(item)
                elif isinstance(item, dict) and isinstance(item.get("name_ref"), str):
                    refs.append(item["name_ref"])
        # Also catch any nested name_refs.
        refs.extend(_collect_name_refs(data))

    return refs


def cmd_validate(domain_dir: Path) -> dict:
    """Validate alignment between specs/naming-manifest.yaml and
    specs/guidance/ name-ref-bearing files. Returns a summary dict."""
    summary: dict = {
        "domain": domain_dir.name,
        "ok": True,
        "missing": [],
        "orphans": [],
        "errors": [],
    }

    manifest_path = domain_dir / "specs" / "naming-manifest.yaml"
    if not manifest_path.exists():
        summary["ok"] = False
        summary["errors"].append(
            f"specs/naming-manifest.yaml not found at {manifest_path}"
        )
        return summary

    manifest = _load_yaml(manifest_path)
    if manifest is None:
        summary["ok"] = False
        summary["errors"].append(
            f"specs/naming-manifest.yaml at {manifest_path} is malformed or unreadable"
        )
        return summary

    manifest_names = _collect_manifest_names(manifest)
    referenced: set[str] = set()
    guidance_dir = domain_dir / "specs" / "guidance"

    # Check the two name_ref-bearing guidance files.
    for filename in _GUIDANCE_FILES_WITH_NAME_REFS:
        path = guidance_dir / filename
        if not path.exists():
            continue  # missing guidance files are not errors at v1
        data = _load_yaml(path)
        if data is None:
            summary["errors"].append(
                f"{path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
            continue
        for ref in _collect_name_refs(data):
            referenced.add(ref)
            if ref not in manifest_names:
                summary["missing"].append({
                    "file": str(path.relative_to(domain_dir)),
                    "name_ref": ref,
                })
                summary["ok"] = False

    # Check include-with-output.yaml.
    iwo_path = guidance_dir / _INCLUDE_WITH_OUTPUT_FILE
    if iwo_path.exists():
        data = _load_yaml(iwo_path)
        if data is None:
            summary["errors"].append(
                f"{iwo_path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
        else:
            for ref in _collect_include_with_output(data):
                referenced.add(ref)
                if ref not in manifest_names:
                    summary["missing"].append({
                        "file": str(iwo_path.relative_to(domain_dir)),
                        "name_ref": ref,
                    })
                    summary["ok"] = False

    # Orphans: names in manifest but never referenced. Non-fatal at v1.
    summary["orphans"] = sorted(manifest_names - referenced)

    return summary


def _print_human(summary: dict, quiet: bool) -> None:
    if summary["ok"] and not summary["missing"]:
        if not quiet:
            print(f"validate-guidance: OK ({summary['domain']})")
            if summary["orphans"]:
                print(
                    f"  warning: {len(summary['orphans'])} manifest entries "
                    f"unreferenced by any guidance file: "
                    f"{', '.join(summary['orphans'][:5])}"
                    f"{'...' if len(summary['orphans']) > 5 else ''}"
                )
        return
    print(f"validate-guidance: FAIL ({summary['domain']})", file=sys.stderr)
    for entry in summary["missing"]:
        print(
            f"  {entry['file']}: name_ref '{entry['name_ref']}' has no matching "
            f"entry in specs/naming-manifest.yaml",
            file=sys.stderr,
        )
    for err in summary["errors"]:
        print(f"  {err}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate name-ref alignment between specs/naming-manifest.yaml and "
            "specs/guidance/ files."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "--json", action="store_true", help="Emit structured JSON instead of human prose."
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress success output."
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(2)
    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        print(f"Error: Domain directory not found: {domain_dir}", file=sys.stderr)
        sys.exit(2)

    summary = cmd_validate(domain_dir)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary, args.quiet)

    sys.exit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
