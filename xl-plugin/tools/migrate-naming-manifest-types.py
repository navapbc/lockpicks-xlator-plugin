#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
One-shot migration: rewrite naming-manifest.yaml legacy CIVIL `type:` values to
Catala-native names and bump `version:` to '2.0'.

Fixed name map (per docs/brainstorms/2026-05-29-002-...-requirements.md, R8):

    bool   -> boolean
    int    -> integer
    float  -> decimal
    str    -> string
    set    -> list
    object -> structure

Pass-through (already Catala-native or unchanged): money, date, enum, list,
integer, decimal, boolean, duration, string, structure.

This script is intentionally short-lived: it lands in the same PR that tightens
the merge-naming-manifest validator to reject the legacy names, runs once
against the in-tree manifests, then is deleted from the repo (R9). No
permanent `xlator` subcommand wires it in.

Usage:
    uv run xl-plugin/tools/migrate-naming-manifest-types.py <domain> [--check-only]
    uv run xl-plugin/tools/migrate-naming-manifest-types.py --all [--check-only]

Output (stdout): JSON header line, sentinel divider, human summary body.

Exit codes:
    0 - success
    1 - unrecoverable failure (unknown legacy type, YAML parse error, IO error)
    2 - pre-flight failure (missing domain directory, missing manifest for
        a specifically-named domain). `--all` silently skips domains without
        a manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml


_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_HEADER_SENTINEL = "--- MIGRATE-NAMING-MANIFEST-TYPES-HEADER-END ---"
_TARGET_VERSION = "2.0"

_LEGACY_TO_CATALA = {
    "bool": "boolean",
    "int": "integer",
    "float": "decimal",
    "str": "string",
    "set": "list",
    "object": "structure",
}

_CATALA_NATIVE = {
    "integer", "decimal", "money", "boolean", "date",
    "duration", "string", "enum", "list", "structure",
}


class MigrationError(Exception):
    """Raised on unknown `type:` value or other unrecoverable failure."""


def _rewrite_type(raw: Any, path_for_error: str) -> tuple[Any, bool]:
    """Return (new_value, rewritten?). Unknown strings raise MigrationError.

    `None` and absent values pass through unchanged. Catala-native values pass
    through. Legacy values are mapped per `_LEGACY_TO_CATALA`."""
    if raw is None:
        return raw, False
    if not isinstance(raw, str):
        raise MigrationError(
            f"{path_for_error}: `type:` must be a string or null; got {raw!r}"
        )
    if raw in _CATALA_NATIVE:
        return raw, False
    if raw in _LEGACY_TO_CATALA:
        return _LEGACY_TO_CATALA[raw], True
    raise MigrationError(
        f"{path_for_error}: unknown `type:` value {raw!r}; "
        f"expected one of {sorted(_CATALA_NATIVE)} or a legacy name "
        f"({sorted(_LEGACY_TO_CATALA)})"
    )


def _walk_and_rewrite(manifest: dict, manifest_path: Path) -> int:
    """Mutate manifest in place; return count of entries rewritten."""
    rewritten = 0

    inputs = manifest.get("inputs")
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if not isinstance(fields, dict):
                continue
            for field_name, entry in fields.items():
                if not isinstance(entry, dict) or "type" not in entry:
                    continue
                where = f"{manifest_path}: inputs.{entity}.{field_name}.type"
                new_val, changed = _rewrite_type(entry["type"], where)
                if changed:
                    entry["type"] = new_val
                    rewritten += 1

    for section in ("computed", "outputs"):
        sec = manifest.get(section)
        if not isinstance(sec, dict):
            continue
        for name, entry in sec.items():
            if not isinstance(entry, dict) or "type" not in entry:
                continue
            where = f"{manifest_path}: {section}.{name}.type"
            new_val, changed = _rewrite_type(entry["type"], where)
            if changed:
                entry["type"] = new_val
                rewritten += 1

    return rewritten


def _yaml_setup() -> None:
    """Register OrderedDict representer so dumps preserve key order."""
    def _represent_ordered(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())
    yaml.SafeDumper.add_representer(OrderedDict, _represent_ordered)


def _serialize_yaml(doc: Any) -> str:
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


def _atomic_write(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


def _load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise MigrationError(f"{path}: top-level YAML must be a mapping")
    return raw


def migrate_one(manifest_path: Path, check_only: bool) -> dict:
    """Migrate a single manifest. Returns a per-file counter dict."""
    manifest = _load_manifest(manifest_path)

    prior_version = manifest.get("version")
    version_bumped = False
    if prior_version != _TARGET_VERSION:
        manifest["version"] = _TARGET_VERSION
        version_bumped = True

    rewritten = _walk_and_rewrite(manifest, manifest_path)

    if not check_only and (version_bumped or rewritten > 0):
        _yaml_setup()
        _atomic_write(manifest_path, _serialize_yaml(manifest))

    return {
        "path": str(manifest_path),
        "entries_rewritten": rewritten,
        "version_bumped": version_bumped,
        "prior_version": prior_version,
    }


def _resolve_targets(domain: str | None, all_: bool,
                     domains_root: Path) -> tuple[list[Path], list[str]]:
    """Return (existing_manifest_paths, warnings)."""
    warnings: list[str] = []
    if all_:
        glob_results = sorted(domains_root.glob(f"*/{_NAMING_MANIFEST_REL}"))
        return glob_results, warnings

    assert domain is not None
    domain_dir = domains_root / domain
    if not domain_dir.is_dir():
        raise FileNotFoundError(f"domain directory not found: {domain_dir}")
    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    return [manifest_path], warnings


def run(domain: str | None, all_: bool, check_only: bool,
        domains_root: Path) -> int:
    try:
        targets, warnings = _resolve_targets(domain, all_, domains_root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    per_file: list[dict] = []
    try:
        for path in targets:
            per_file.append(migrate_one(path, check_only))
    except (MigrationError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: IO failure: {exc}", file=sys.stderr)
        return 1

    files_processed = len(per_file)
    entries_rewritten = sum(p["entries_rewritten"] for p in per_file)
    version_bumped_any = any(p["version_bumped"] for p in per_file)

    header = {
        "mode": "check_only" if check_only else "write",
        "files_processed": files_processed,
        "entries_rewritten": entries_rewritten,
        "version_bumped": version_bumped_any,
        "warnings": warnings,
        "per_file": per_file,
    }
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    print(json.dumps(header))
    print(_HEADER_SENTINEL)
    if check_only:
        print(f"Would rewrite {entries_rewritten} `type:` entries across "
              f"{files_processed} manifest(s).")
    else:
        print(f"Rewrote {entries_rewritten} `type:` entries across "
              f"{files_processed} manifest(s).")
    for p in per_file:
        bump = " (version bumped)" if p["version_bumped"] else ""
        print(f"  {p['path']}: {p['entries_rewritten']} rewritten{bump}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite naming-manifest.yaml legacy CIVIL type names to Catala-"
            "native names and bump version to '2.0'. One-shot."
        )
    )
    parser.add_argument("domain", nargs="?", help="Domain name (e.g. snap)")
    parser.add_argument("--all", action="store_true", dest="all_",
                        help="Migrate every domain that has a manifest")
    parser.add_argument("--check-only", action="store_true",
                        help="Compute the diff without writing any file")
    args = parser.parse_args()

    if args.all_ and args.domain:
        parser.error("provide either <domain> or --all, not both")
    if not args.all_ and not args.domain:
        parser.error("provide either <domain> or --all")

    domains_root_raw = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root_raw:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2
    domains_root = Path(domains_root_raw)

    return run(args.domain, args.all_, args.check_only, domains_root)


if __name__ == "__main__":
    sys.exit(main())
