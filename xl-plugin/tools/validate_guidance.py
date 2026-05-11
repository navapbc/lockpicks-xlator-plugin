#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator validate-guidance: assert every name_ref in `<domain>/specs/guidance/`
files resolves to an entry in `<domain>/specs/naming-manifest.yaml`, that any
`type:`/`values:` field on a guidance entry agrees with the same field on the
corresponding manifest entry, and that every `constants_and_tables[]` entry
carries `source_file:` and `source_section:`.

Reads the manifest and every guidance file with name-ref or provenance content
(`output-variables.yaml`, `input-variables.yaml`, `include-with-output.yaml`,
`constants-and-tables.yaml`) and reports:
  - missing name-refs (referenced in a guidance file but absent from manifest)
  - type/values mismatches (guidance entry contradicts manifest entry)
  - missing required provenance fields on `constants-and-tables.yaml` entries
  - orphan manifest entries (in manifest but not referenced anywhere)

Exits 0 on clean alignment, 1 on any failure.

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
      "mismatches": [{"file": "...", "name_ref": "...", "field": "type",
                      "guidance": "...", "manifest": "..."}],
      "missing_fields": [{"file": "...", "entry": "<name>", "field": "source_file"}],
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


_OUTPUT_VARIABLES_FILE = "output-variables.yaml"
_INPUT_VARIABLES_FILE = "input-variables.yaml"
_INCLUDE_WITH_OUTPUT_FILE = "include-with-output.yaml"
_CONSTANTS_AND_TABLES_FILE = "constants-and-tables.yaml"
_CONSTANTS_AND_TABLES_REQUIRED = ("source_file", "source_section")


def _load_yaml(path: Path) -> Any:
    """Return parsed YAML or None when missing/malformed (caller handles)."""
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None


def _collect_manifest_entries(manifest: dict) -> dict[str, dict]:
    """Return `{variable_name → manifest_entry_dict}` collected from inputs,
    computed, and outputs blocks. The entry dict is the leaf-keyed value
    (carrying optional `type`, `values`, `description`, etc.)."""
    entries: dict[str, dict] = {}
    if not isinstance(manifest, dict):
        return entries

    inputs = manifest.get("inputs") or {}
    if isinstance(inputs, dict):
        for _entity, fields in inputs.items():
            if not isinstance(fields, dict):
                continue
            for field_name, entry in fields.items():
                entries[str(field_name)] = entry if isinstance(entry, dict) else {}

    for top_key in ("computed", "outputs"):
        block = manifest.get(top_key) or {}
        if isinstance(block, dict):
            for name, entry in block.items():
                entries[str(name)] = entry if isinstance(entry, dict) else {}

    return entries


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


def _check_field_agreement(
    guidance_entry: dict,
    manifest_entry: dict,
    name_ref: str,
    file_rel: str,
    mismatches: list[dict],
) -> None:
    """Compare `type:` and `values:` on a guidance entry against the manifest
    entry. A mismatch is recorded only when both sides supply the field and
    the values differ. Absent on either side is not a mismatch."""
    for field in ("type", "values"):
        g_val = guidance_entry.get(field)
        m_val = manifest_entry.get(field)
        if g_val is None or m_val is None:
            continue
        if g_val != m_val:
            mismatches.append({
                "file": file_rel,
                "name_ref": name_ref,
                "field": field,
                "guidance": g_val,
                "manifest": m_val,
            })


def _check_output_variables(
    data: Any,
    manifest_entries: dict[str, dict],
    file_rel: str,
    missing: list[dict],
    mismatches: list[dict],
    referenced: set[str],
) -> None:
    """`output-variables.yaml` is flat keyed by name; each entry may carry
    `name_ref`, `type`, `values`, etc."""
    if not isinstance(data, dict):
        return
    for _key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        name_ref = entry.get("name_ref")
        if not isinstance(name_ref, str):
            continue
        referenced.add(name_ref)
        manifest_entry = manifest_entries.get(name_ref)
        if manifest_entry is None:
            missing.append({"file": file_rel, "name_ref": name_ref})
            continue
        _check_field_agreement(entry, manifest_entry, name_ref, file_rel, mismatches)


def _check_input_variables(
    data: Any,
    manifest_entries: dict[str, dict],
    file_rel: str,
    missing: list[dict],
    mismatches: list[dict],
    referenced: set[str],
) -> None:
    """`input-variables.yaml` is `categories: [{fields: [{name_ref, type, ...}]}]`."""
    if not isinstance(data, dict):
        return
    categories = data.get("categories") or []
    if not isinstance(categories, list):
        return
    for category in categories:
        if not isinstance(category, dict):
            continue
        for field in category.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name_ref = field.get("name_ref")
            if not isinstance(name_ref, str):
                continue
            referenced.add(name_ref)
            manifest_entry = manifest_entries.get(name_ref)
            if manifest_entry is None:
                missing.append({"file": file_rel, "name_ref": name_ref})
                continue
            _check_field_agreement(field, manifest_entry, name_ref, file_rel, mismatches)


def _check_constants_and_tables(
    data: Any,
    file_rel: str,
    missing_fields: list[dict],
) -> None:
    """Every `constants_and_tables[]` entry must carry `source_file:` and
    `source_section:` as non-empty strings."""
    if not isinstance(data, dict):
        return
    entries = data.get("constants_and_tables") or []
    if not isinstance(entries, list):
        return
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        entry_name = entry.get("name", f"<index {idx}>")
        for field in _CONSTANTS_AND_TABLES_REQUIRED:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                missing_fields.append({
                    "file": file_rel,
                    "entry": str(entry_name),
                    "field": field,
                })


def cmd_validate(domain_dir: Path) -> dict:
    """Validate alignment between specs/naming-manifest.yaml and
    specs/guidance/ name-ref-bearing files. Returns a summary dict."""
    summary: dict = {
        "domain": domain_dir.name,
        "ok": True,
        "missing": [],
        "mismatches": [],
        "missing_fields": [],
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

    manifest_entries = _collect_manifest_entries(manifest)
    referenced: set[str] = set()
    guidance_dir = domain_dir / "specs" / "guidance"

    # output-variables.yaml
    ov_path = guidance_dir / _OUTPUT_VARIABLES_FILE
    if ov_path.exists():
        data = _load_yaml(ov_path)
        if data is None:
            summary["errors"].append(
                f"{ov_path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
        else:
            _check_output_variables(
                data,
                manifest_entries,
                str(ov_path.relative_to(domain_dir)),
                summary["missing"],
                summary["mismatches"],
                referenced,
            )

    # input-variables.yaml
    iv_path = guidance_dir / _INPUT_VARIABLES_FILE
    if iv_path.exists():
        data = _load_yaml(iv_path)
        if data is None:
            summary["errors"].append(
                f"{iv_path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
        else:
            _check_input_variables(
                data,
                manifest_entries,
                str(iv_path.relative_to(domain_dir)),
                summary["missing"],
                summary["mismatches"],
                referenced,
            )

    # include-with-output.yaml — name refs only, no type/values to check.
    iwo_path = guidance_dir / _INCLUDE_WITH_OUTPUT_FILE
    if iwo_path.exists():
        data = _load_yaml(iwo_path)
        if data is None:
            summary["errors"].append(
                f"{iwo_path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
        else:
            file_rel = str(iwo_path.relative_to(domain_dir))
            for ref in _collect_include_with_output(data):
                referenced.add(ref)
                if ref not in manifest_entries:
                    summary["missing"].append({"file": file_rel, "name_ref": ref})

    # constants-and-tables.yaml — required provenance fields per entry.
    cat_path = guidance_dir / _CONSTANTS_AND_TABLES_FILE
    if cat_path.exists():
        data = _load_yaml(cat_path)
        if data is None:
            summary["errors"].append(
                f"{cat_path.relative_to(domain_dir)}: malformed or unreadable; skipping"
            )
        else:
            _check_constants_and_tables(
                data,
                str(cat_path.relative_to(domain_dir)),
                summary["missing_fields"],
            )

    if summary["missing"] or summary["mismatches"] or summary["missing_fields"]:
        summary["ok"] = False

    # Orphans: names in manifest but never referenced. Non-fatal at v1.
    summary["orphans"] = sorted(set(manifest_entries.keys()) - referenced)

    return summary


def _print_human(summary: dict, quiet: bool) -> None:
    if (
        summary["ok"]
        and not summary["missing"]
        and not summary["mismatches"]
        and not summary["missing_fields"]
    ):
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
    for mm in summary["mismatches"]:
        print(
            f"  {mm['file']}: name_ref '{mm['name_ref']}' "
            f"{mm['field']}={mm['guidance']!r} disagrees with "
            f"manifest {mm['field']}={mm['manifest']!r}",
            file=sys.stderr,
        )
    for mf in summary["missing_fields"]:
        print(
            f"  {mf['file']}: entry '{mf['entry']}' is missing required field "
            f"'{mf['field']}'",
            file=sys.stderr,
        )
    for err in summary["errors"]:
        print(f"  {err}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate name-ref alignment and type/values agreement between "
            "specs/naming-manifest.yaml and specs/guidance/ files."
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
