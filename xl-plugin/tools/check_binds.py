# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Bind-consistency pass for the Xlator pipeline.

When a parent CIVIL module binds an entity to a sub-module via
`computed.<name>.invoke.bind: { SubEntity: ParentEntity }`, the parent's
`ParentEntity` declaration must contain every field the sub-module reads
on `SubEntity`. This module computes the diff and (optionally) applies it
to the parent CIVIL file in place.

Two layers:

- `compute_bind_repairs(parent_doc, sub_module_docs)` — pure: per-parent additions/conflicts.
- `compute_domain_repairs(civil_docs)` — pure: walks an entire domain.
- `apply_bind_repairs(civil_path, additions)` — I/O: appends fields to parent CIVIL,
  preserving surrounding comments and order.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

# Reuse the same scope-omission rule the transpiler applies, so the repair pass
# and the transpiler gate stay in lock step.
from transpile_to_catala import required_sub_fields


# =============================================================================
# Data classes
# =============================================================================

@dataclass(frozen=True)
class FieldAddition:
    parent_module: str
    parent_entity: str
    field_name: str
    field_spec: dict
    source_sub_module: str


@dataclass(frozen=True)
class FieldConflict:
    parent_module: str
    parent_entity: str
    field_name: str
    declarations: tuple  # tuple of (sub_module_name, field_spec) — frozen for hashing


# =============================================================================
# Pure computation — single parent
# =============================================================================

# Attributes that affect the field's transpiled Catala scope shape. Two sub-modules
# declaring the same field must agree on each of these — disagreement is a semantic
# conflict. Free-text attributes (description, source) don't drive scope shape and
# are not compared.
_WIRE_SPEC_KEYS = ("type", "currency", "values")


def _attrs_compatible(value_a, value_b, *, key: str) -> bool:
    """One pair of attribute values is compatible if either side is silent OR they
    agree. `values:` (enum variants) is compared as a set — order doesn't matter
    since Catala enums are sum types. Specifier wins; silent sub conforms.
    """
    if value_a is None or value_b is None:
        return True
    if key == "values":
        return frozenset(value_a) == frozenset(value_b)
    return value_a == value_b


def _field_specs_match(spec_a: dict, spec_b: dict) -> bool:
    """Compare specs for the purposes of bind repair.

    A sub-module that omits an attribute conforms to whatever a specifier declares;
    two specifiers must agree (with `values:` compared order-insensitively). This
    means `{type: money, currency: USD}` matches `{type: money}` — the silent sub
    is interpreted as "no opinion on currency" rather than "currency is None."
    """
    for key in _WIRE_SPEC_KEYS:
        if not _attrs_compatible(spec_a.get(key), spec_b.get(key), key=key):
            return False
    return True


def _combine_specs(declarers: list[tuple[str, dict]]) -> dict:
    """Merge declarers into one spec by taking the first non-None value per attribute.

    Assumes all declarers were already determined compatible (`_field_specs_match`).
    The first declarer wins for ordering of `values:`; specifier wins for currency
    when others are silent.
    """
    merged: dict = {}
    for key in _WIRE_SPEC_KEYS:
        for _sub_module, spec in declarers:
            value = spec.get(key)
            if value is not None:
                merged[key] = value
                break
    return merged


def compute_bind_repairs(
    parent_doc: dict,
    sub_module_docs: dict[str, dict],
) -> tuple[list[FieldAddition], list[FieldConflict]]:
    """For one parent doc, return (additions, conflicts).

    Pure: no I/O, no sys.exit.
    """
    parent_module = parent_doc.get("module", "")
    computed = parent_doc.get("computed", {}) or {}
    inputs = parent_doc.get("inputs", {}) or {}

    # First pass: gather candidate additions per (parent_entity, field_name).
    # Collisions across multiple sub-modules become conflicts if their specs disagree.
    candidates: dict[tuple[str, str], list[tuple[str, dict]]] = {}

    for computed_name, field_def in computed.items():
        if not isinstance(field_def, dict) or not field_def.get("invoke"):
            continue
        sub_module_name = field_def.get("module", "")
        sub_doc = sub_module_docs.get(sub_module_name)
        if sub_doc is None:
            # Caller may load these lazily; silently skip when absent.
            continue

        invoke = field_def["invoke"]
        bind = invoke.get("bind", {}) or {}
        field_bind = invoke.get("field_bind", {}) or {}

        for sub_entity, parent_entity in bind.items():
            sub_required = required_sub_fields(sub_doc, sub_entity)
            parent_fields = (
                inputs.get(parent_entity, {}).get("fields", {}) or {}
            )
            covered_by_field_bind = set((field_bind.get(sub_entity) or {}).keys())

            for field_name, sub_spec in sub_required.items():
                if field_name in parent_fields:
                    continue
                if field_name in covered_by_field_bind:
                    continue
                candidates.setdefault((parent_entity, field_name), []).append(
                    (sub_module_name, sub_spec)
                )

    # Second pass: convert candidates → additions or conflicts.
    additions: list[FieldAddition] = []
    conflicts: list[FieldConflict] = []

    for (parent_entity, field_name), declarers in candidates.items():
        first_sub, first_spec = declarers[0]
        all_match = all(
            _field_specs_match(first_spec, other_spec)
            for _, other_spec in declarers[1:]
        )
        if not all_match:
            conflicts.append(FieldConflict(
                parent_module=parent_module,
                parent_entity=parent_entity,
                field_name=field_name,
                declarations=tuple(declarers),
            ))
            continue
        # Build the spec for the new parent field: combine across declarers so a
        # specifier's currency/values wins when other declarers are silent. Force
        # optional=True. Carrying `values:` matters specifically because the
        # ticket-11 scope-input-omits rule otherwise drops optional-string-no-variants
        # fields from the Catala scope entirely.
        emitted_spec = _combine_specs(declarers)
        emitted_spec["optional"] = True
        additions.append(FieldAddition(
            parent_module=parent_module,
            parent_entity=parent_entity,
            field_name=field_name,
            field_spec=emitted_spec,
            source_sub_module=first_sub,
        ))

    return additions, conflicts


# =============================================================================
# Pure computation — domain
# =============================================================================

def compute_domain_repairs(
    civil_docs: dict[str, dict],
) -> dict[str, tuple[list[FieldAddition], list[FieldConflict]]]:
    """For a dict of {module_name: civil_doc}, return per-parent diffs.

    Treats every doc with `computed.<name>.invoke` entries as a potential parent.
    Sub-module docs are looked up by their `module:` field within the same dict.

    Returns only entries where additions or conflicts are non-empty.
    """
    diffs: dict[str, tuple[list[FieldAddition], list[FieldConflict]]] = {}
    sub_lookup: dict[str, dict] = {}
    seen_by_module: dict[str, str] = {}
    for name, doc in civil_docs.items():
        module_value = doc.get("module") or name
        if module_value in seen_by_module:
            raise ValueError(
                f"two CIVIL files declare module: {module_value!r} — "
                f"{seen_by_module[module_value]} and {name}. Bind lookup is ambiguous."
            )
        seen_by_module[module_value] = name
        sub_lookup[module_value] = doc
    for module_name, doc in civil_docs.items():
        computed = doc.get("computed", {}) or {}
        has_invoke = any(
            isinstance(field_def, dict) and field_def.get("invoke")
            for field_def in computed.values()
        )
        if not has_invoke:
            continue
        additions, conflicts = compute_bind_repairs(doc, sub_lookup)
        if additions or conflicts:
            diffs[module_name] = (additions, conflicts)
    return diffs


# =============================================================================
# I/O wrapper — apply additions to a parent CIVIL file
# =============================================================================

def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` atomically via tmp-file + os.replace.

    An interrupt between truncate and write-completion would otherwise leave
    a partial CIVIL on disk.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def _format_field_line(addition: FieldAddition, indent: int) -> str:
    """One-line flow-style field declaration with provenance comment."""
    spec_parts = [f"type: {addition.field_spec['type']}"]
    if "currency" in addition.field_spec:
        spec_parts.append(f"currency: {addition.field_spec['currency']}")
    if "values" in addition.field_spec:
        # Inline-flow list, matching the surrounding CIVIL idiom.
        values_repr = "[" + ", ".join(str(value) for value in addition.field_spec["values"]) + "]"
        spec_parts.append(f"values: {values_repr}")
    spec_parts.append("optional: true")
    spec = ", ".join(spec_parts)
    prefix = " " * indent
    return (
        f"{prefix}{addition.field_name}: {{ {spec} }}"
        f"  # auto-imported from {addition.source_sub_module}"
    )


def _find_fields_block_end(lines: list[str], entity: str) -> tuple[int, int]:
    """Return (insertion_line_index, child_indent) for inputs.<entity>.fields:.

    The insertion point is one past the last child of `fields:` — i.e. the first
    line at or shallower than fields:'s own indent (or EOF). `child_indent` is
    derived from the first real child of `fields:` when one exists, so the
    appended field matches the file's indent style (2, 4, or tabs). When `fields:`
    is empty, falls back to fields_indent + 2.

    Raises ValueError if the entity or its fields: sub-block is absent.
    """
    inputs_line = None
    inputs_indent = 0
    for line_index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0 and stripped.rstrip() == "inputs:":
            inputs_line = line_index
            inputs_indent = indent
            break
    if inputs_line is None:
        raise ValueError("no `inputs:` block found in CIVIL file")

    # Find `<entity>:` under inputs.
    entity_line = None
    entity_indent = None
    for line_index in range(inputs_line + 1, len(lines)):
        stripped = lines[line_index].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[line_index]) - len(stripped)
        if indent <= inputs_indent:
            break  # left the inputs block
        if entity_indent is None:
            entity_indent = indent
        if indent == entity_indent and stripped.rstrip() == f"{entity}:":
            entity_line = line_index
            break
    if entity_line is None:
        raise ValueError(f"no `inputs.{entity}:` block found in CIVIL file")

    # Find `fields:` directly under the entity.
    fields_line = None
    fields_indent = None
    for line_index in range(entity_line + 1, len(lines)):
        stripped = lines[line_index].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[line_index]) - len(stripped)
        if indent <= entity_indent:
            break  # left the entity block
        if fields_indent is None:
            fields_indent = indent
        if indent == fields_indent and stripped.rstrip() == "fields:":
            fields_line = line_index
            break
    if fields_line is None:
        raise ValueError(
            f"no `inputs.{entity}.fields:` block found in CIVIL file"
        )

    # Find the end of the fields block: first content line at indent <= fields_indent.
    # Also discover the actual child indent from the first existing child, so we
    # match the file's indent style (could be 2-space, 4-space, tabs, etc).
    child_indent = None
    insertion_index = len(lines)
    for line_index in range(fields_line + 1, len(lines)):
        stripped = lines[line_index].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[line_index]) - len(stripped)
        if indent <= fields_indent:
            insertion_index = line_index
            break
        if child_indent is None:
            child_indent = indent
    if child_indent is None:
        # Empty fields: block — fall back to a 2-space step.
        child_indent = fields_indent + 2

    # Walk back past trailing blank/comment lines so the insertion sits flush with
    # the last real child rather than after a trailing comment block.
    while insertion_index > fields_line + 1:
        previous = lines[insertion_index - 1]
        previous_stripped = previous.lstrip()
        if previous_stripped and not previous_stripped.startswith("#"):
            break
        insertion_index -= 1

    return insertion_index, child_indent


def apply_bind_repairs(civil_path: Path, additions: list[FieldAddition]) -> str:
    """Edit `civil_path` in place, appending each addition to the parent's fields block.

    Preserves order and comments. Preserves CRLF line endings if the source uses
    them. Writes atomically via os.replace so an interrupted write can't leave
    a half-truncated file on disk. Idempotent: any field name already present
    in the entity's fields block is skipped.

    Returns a human-readable diff summary.
    """
    civil_path = Path(civil_path)
    # Read in binary then decode so universal-newlines doesn't strip CR before
    # we can detect the line-ending style.
    raw_bytes = civil_path.read_bytes()
    raw_text = raw_bytes.decode("utf-8")
    line_ending = "\r\n" if "\r\n" in raw_text else "\n"
    text = raw_text.replace("\r\n", "\n") if line_ending == "\r\n" else raw_text

    parsed = yaml.safe_load(text) or {}
    existing = parsed.get("inputs", {}) or {}

    # Group additions by entity.
    by_entity: dict[str, list[FieldAddition]] = {}
    for addition in additions:
        by_entity.setdefault(addition.parent_entity, []).append(addition)

    applied: list[str] = []
    skipped: list[str] = []

    # Apply each entity's additions; re-read+rewrite lines per entity so each
    # entity's insertion respects the latest file state.
    for entity, entity_additions in by_entity.items():
        already_present = set(
            (existing.get(entity, {}).get("fields", {}) or {}).keys()
        )
        pending = [
            addition for addition in entity_additions
            if addition.field_name not in already_present
        ]
        skipped.extend(
            f"{entity}.{addition.field_name} (already present)"
            for addition in entity_additions if addition.field_name in already_present
        )
        if not pending:
            continue

        lines = text.split("\n")
        insertion_index, child_indent = _find_fields_block_end(lines, entity)
        new_lines = [_format_field_line(addition, child_indent) for addition in pending]
        text = "\n".join(lines[:insertion_index] + new_lines + lines[insertion_index:])
        applied.extend(
            f"{entity}.{addition.field_name} (from {addition.source_sub_module})"
            for addition in pending
        )

    output_text = text.replace("\n", line_ending) if line_ending == "\r\n" else text
    _atomic_write(civil_path, output_text)

    summary_parts = []
    if applied:
        summary_parts.append(
            f"Added {len(applied)} field(s) to {civil_path.name}:\n  - "
            + "\n  - ".join(applied)
        )
    if skipped:
        summary_parts.append(
            f"Skipped {len(skipped)} already-present field(s):\n  - "
            + "\n  - ".join(skipped)
        )
    if not summary_parts:
        summary_parts.append(f"{civil_path.name}: no changes needed.")
    return "\n".join(summary_parts)


# =============================================================================
# CLI
# =============================================================================

def _load_domain_civil_docs(domain_specs_dir: Path) -> dict[str, dict]:
    """Load every *.civil.yaml under domain_specs_dir.

    Returns {file_basename_without_extension: parsed_doc}.
    """
    docs: dict[str, dict] = {}
    for yaml_path in sorted(domain_specs_dir.glob("*.civil.yaml")):
        with yaml_path.open() as fh:
            parsed = yaml.safe_load(fh) or {}
        module_name = yaml_path.name.removesuffix(".civil.yaml")
        docs[module_name] = parsed
    return docs


def _print_diff(diffs: dict[str, tuple[list[FieldAddition], list[FieldConflict]]]) -> None:
    for parent_module, (additions, conflicts) in diffs.items():
        print(f"\n{parent_module}:")
        if additions:
            print(f"  Missing fields ({len(additions)}):")
            for addition in additions:
                print(
                    f"    - {addition.parent_entity}.{addition.field_name} "
                    f"({addition.field_spec.get('type')}, "
                    f"from {addition.source_sub_module})"
                )
        if conflicts:
            print(f"  Type conflicts ({len(conflicts)}):")
            for conflict in conflicts:
                declarers = ", ".join(
                    f"{sub}={spec.get('type')}"
                    for sub, spec in conflict.declarations
                )
                print(
                    f"    - {conflict.parent_entity}.{conflict.field_name}: "
                    f"{declarers}"
                )


def _resolve_domain_specs_dir(domain: str) -> Path:
    """Resolve the specs/ directory for a domain.

    Honors $DOMAINS_FULLPATH (used by the xlator shim); otherwise treats `domain`
    as a relative path under the current working directory.

    Rejects domain names containing path separators, `..`, or absolute paths so
    a caller can't escape the domains root (`xlator repair-binds ../other-domain`
    would otherwise silently mutate a sibling domain).
    """
    if not domain or "/" in domain or "\\" in domain or domain in ("..", "."):
        raise ValueError(
            f"invalid domain name: {domain!r} — must be a bare directory name, "
            f"not a path"
        )
    if Path(domain).is_absolute():
        raise ValueError(f"invalid domain name: {domain!r} — absolute paths not allowed")

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if domains_root:
        resolved = (Path(domains_root) / domain / "specs").resolve()
        root_resolved = Path(domains_root).resolve()
        if root_resolved not in resolved.parents and resolved != root_resolved:
            raise ValueError(
                f"domain path {resolved} escapes DOMAINS_FULLPATH={root_resolved}"
            )
        return resolved
    return Path(domain) / "specs"


def main_check_binds(domain: str) -> int:
    try:
        specs_dir = _resolve_domain_specs_dir(domain)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not specs_dir.is_dir():
        print(f"ERROR: specs directory not found: {specs_dir}", file=sys.stderr)
        return 2
    docs = _load_domain_civil_docs(specs_dir)
    diffs = compute_domain_repairs(docs)
    if not diffs:
        print(f"OK check-binds passed: {domain}")
        return 0
    print(f"FAIL check-binds: {domain}")
    _print_diff(diffs)
    return 1


def main_repair_binds(domain: str, *, dry_run: bool = False) -> int:
    try:
        specs_dir = _resolve_domain_specs_dir(domain)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not specs_dir.is_dir():
        print(f"ERROR: specs directory not found: {specs_dir}", file=sys.stderr)
        return 2
    docs = _load_domain_civil_docs(specs_dir)
    diffs = compute_domain_repairs(docs)
    if not diffs:
        print(f"OK repair-binds: {domain} (no changes needed)")
        return 0

    any_conflicts = any(conflicts for _, (_, conflicts) in diffs.items())
    if any_conflicts:
        print(f"FAIL repair-binds: {domain} (type conflicts must be resolved manually)")
        _print_diff(diffs)
        return 1

    if dry_run:
        print(f"DRY-RUN repair-binds: {domain}")
        _print_diff(diffs)
        return 1 if diffs else 0

    for parent_module, (additions, _) in diffs.items():
        civil_path = specs_dir / f"{parent_module}.civil.yaml"
        summary = apply_bind_repairs(civil_path, additions)
        print(summary)
    print(f"OK repair-binds: {domain}")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="check_binds",
        description="Bind-consistency pass for CIVIL parent/sub-module declarations.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    check_parser = subparsers.add_parser("check", help="Report bind diffs (read-only)")
    check_parser.add_argument("domain")

    repair_parser = subparsers.add_parser("repair", help="Apply bind diffs to parent CIVIL")
    repair_parser.add_argument("domain")
    repair_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.action == "check":
        return main_check_binds(args.domain)
    return main_repair_binds(args.domain, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
