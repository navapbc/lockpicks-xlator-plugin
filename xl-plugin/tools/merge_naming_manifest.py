#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator merge-naming-manifest: deterministic writer for specs/naming-manifest.yaml.

Consumes an analyst-approved Name Inventory JSON (R10 shape) and applies the
six load-bearing merge rules of /extract-ruleset Step 7.

The inventory + manifest schema carries Catala-native type metadata:
  * `type` accepts only Catala-native names: `integer`, `decimal`, `money`,
    `boolean`, `date`, `duration`, `string`, `enum`, `list`, `structure`.
  * `optional: bool` flags `Optional<T>` wrapping in the Catala emission.
  * `enum_variants: [str]` carries the list of constructor names for enum
    types (`["Eligible", "Denied"]`). Distinct from `values:`, the older
    string-values list; `enum_variants:` carries the Catala-side names.

The new fields follow the same `preserve-non-null + analyst-authoritative`
semantics as `policy_phrase`/`source_doc`/`section`.

  * preserve-non-null:        every analyst-supplied non-null field on the
                              existing entry wins; inventory fills nulls.
  * rename-via-synonyms:      when prior_name is set and matches an existing
                              key, replace that entry under the new name,
                              carry its synonyms forward, and append a
                              rename-anchor `{name: <prior_name>}` synonym.
  * rename-anchor idempotence: skip the rename-anchor append when the prior
                              key is already in the carried synonyms list.
  * drop-on-rename:           the existing entry under the prior key is
                              removed from the file.
  * carry-forward synonyms:   the new entry inherits the full synonyms list
                              before the rename-anchor is appended.
  * seeded-entry gap-fill:    entries with null provenance get filled only
                              when the inventory supplies a non-null value.

Other invariants enforced:
  * `role_hint:` is never written (section placement encodes role).
  * `version: "1.0"` is always a string.
  * `inputs.<Entity>.<field>` is 3-level; `computed:` and `outputs:` are flat.
  * Output ordering: top-level (version, inputs, computed, outputs); entity
    keys under inputs alphabetical; field keys within each entity alphabetical;
    computed/outputs entries alphabetical by name.
  * Atomicity: built in memory; written via tmp + os.replace.

Usage:
    xlator merge-naming-manifest <domain> <program> --inventory <path>
                                 [--check-only] [--preserve-unmentioned]

Output (stdout): JSON header line, sentinel divider, human summary body.

Exit codes:
    0 — success
    2 — pre-flight failure (missing folder, missing inventory)
    1 — inventory schema violation, unrecoverable conflict, or IO error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_HEADER_SENTINEL = "--- MERGE-NAMING-MANIFEST-HEADER-END ---"

_MANIFEST_VERSION = "2.0"

# Strict Catala-native vocabulary.
_VALID_TYPES = {
    "integer", "decimal", "money", "boolean", "date",
    "duration", "string", "enum", "list", "structure",
}

_SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_CAMEL_CASE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_INPUTS_SECTION_RE = re.compile(r"^inputs\.([A-Z][A-Za-z0-9]*)$")

# Optional fields on a manifest entry, in canonical write order (after the
# four core provenance fields). preserve-non-null applies to each.
#
# `type`, `optional`, and `enum_variants` carry Catala-native type metadata.
# Consumers (`/catala-emit-tests`, the test-creation skills) fall back to
# `string` and surface a clear "needs type" warning when a field declared
# in a Catala source has no `type:` in the manifest.
_OPTIONAL_FIELDS = (
    "description",
    "type",
    "optional",
    "values",
    "enum_variants",
    "policy_phrase",
    "source_doc",
    "section",
    "synonyms",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InventoryError(Exception):
    """Raised on inventory schema violation."""


class MergeConflict(Exception):
    """Raised on pathological merge state (e.g., name + prior_name both exist)."""


# ---------------------------------------------------------------------------
# Inventory schema validation (R10)
# ---------------------------------------------------------------------------

def _err(i: int, field: str, reason: str) -> InventoryError:
    return InventoryError(f"inventory[{i}].{field}: {reason}")


def _is_non_empty_str(v: Any) -> bool:
    return isinstance(v, str) and len(v) > 0


def _validate_entry(i: int, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise InventoryError(f"inventory[{i}]: expected object")

    # name (required, snake_case)
    name = entry.get("name")
    if not _is_non_empty_str(name):
        raise _err(i, "name", "required field missing")
    if not _SNAKE_CASE_RE.match(name):
        raise _err(i, "name", f"must be snake_case, got {name!r}")

    # section (required, parseable)
    section = entry.get("section")
    if not _is_non_empty_str(section):
        raise _err(i, "section", "required field missing")
    if section in ("computed", "outputs"):
        pass
    elif _INPUTS_SECTION_RE.match(section):
        pass
    else:
        raise _err(
            i, "section",
            f"must be one of 'inputs.<CamelCase>', 'computed', 'outputs'; "
            f"got {section!r}",
        )

    # policy_phrase / source_doc / section_text — required keys, nullable values
    for field in ("policy_phrase", "source_doc", "section_text"):
        if field not in entry:
            raise _err(i, field, "required field missing (use null when unknown)")
        v = entry[field]
        if v is not None and not _is_non_empty_str(v):
            raise _err(i, field, "must be non-empty string or null")

    # Provenance must be all-or-nothing.
    pp = entry.get("policy_phrase")
    sd = entry.get("source_doc")
    st = entry.get("section_text")
    provenance_set = [v is not None for v in (pp, sd, st)]
    if any(provenance_set) and not all(provenance_set):
        raise _err(
            i, "policy_phrase",
            "provenance must be all-or-nothing — when policy_phrase is set, "
            "source_doc and section_text must also be set (and vice versa)",
        )

    # prior_name (required key, nullable)
    if "prior_name" not in entry:
        raise _err(i, "prior_name", "required field missing (use null when not a rename)")
    pn = entry["prior_name"]
    if pn is not None:
        if not _is_non_empty_str(pn):
            raise _err(i, "prior_name", "must be non-empty string or null")
        if not _SNAKE_CASE_RE.match(pn):
            raise _err(i, "prior_name", f"must be snake_case, got {pn!r}")
        if pn == name:
            raise _err(i, "prior_name", "must differ from name (renaming to itself is illegal)")

    # description (optional, nullable)
    if "description" in entry and entry["description"] is not None:
        if not _is_non_empty_str(entry["description"]):
            raise _err(i, "description", "must be non-empty string or null")

    # type (optional, nullable, in enum)
    if "type" in entry and entry["type"] is not None:
        t = entry["type"]
        if not _is_non_empty_str(t):
            raise _err(i, "type", "must be a non-empty string or null")
        if t not in _VALID_TYPES:
            raise _err(
                i, "type",
                f"must be one of {sorted(_VALID_TYPES)}; got {t!r}",
            )

    # values (optional). When supplied for type:enum, each entry must be a
    # non-empty string. Absent `values` on type:enum is permitted (the
    # existing convention in some domains is to declare the type without
    # enumerating values); the merge tool does not enforce enumeration.
    values = entry.get("values")
    if values is not None:
        if not isinstance(values, list):
            raise _err(i, "values", "must be a list or null")
        for j, val in enumerate(values):
            if not _is_non_empty_str(val):
                raise _err(i, f"values[{j}]", "must be non-empty string")

    # optional (U7, post-pivot). Boolean flag indicating the field is
    # `Optional<T>` in the Catala emission. Nullable; absent → treated as
    # `false` by consumers but the absence is preserved (preserve-non-null).
    if "optional" in entry and entry["optional"] is not None:
        if not isinstance(entry["optional"], bool):
            raise _err(i, "optional", "must be a boolean or null")

    # enum_variants (U7, post-pivot). List of variant names, one per
    # constructor on an enum type. Nullable; supplied only for enum-typed
    # fields. Each variant name must be a non-empty string (PascalCase by
    # Catala convention but the merge tool does not enforce casing).
    enum_variants = entry.get("enum_variants")
    if enum_variants is not None:
        if not isinstance(enum_variants, list):
            raise _err(i, "enum_variants", "must be a list or null")
        for j, val in enumerate(enum_variants):
            if not _is_non_empty_str(val):
                raise _err(i, f"enum_variants[{j}]", "must be non-empty string")

    # observed_synonyms (optional, nullable)
    obs = entry.get("observed_synonyms")
    if obs is not None:
        if not isinstance(obs, list):
            raise _err(i, "observed_synonyms", "must be a list or null")
        for j, syn in enumerate(obs):
            if not isinstance(syn, dict):
                raise _err(
                    i, f"observed_synonyms[{j}]",
                    "must be an object with at least a 'name' field",
                )
            if not _is_non_empty_str(syn.get("name")):
                raise _err(
                    i, f"observed_synonyms[{j}].name",
                    "required field missing",
                )
            for opt in ("source_doc", "section"):
                if opt in syn and syn[opt] is not None and not _is_non_empty_str(syn[opt]):
                    raise _err(
                        i, f"observed_synonyms[{j}].{opt}",
                        "must be non-empty string or null",
                    )


def validate_inventory(inventory: Any) -> None:
    if not isinstance(inventory, list):
        raise InventoryError("inventory: top-level value must be a list")
    for i, entry in enumerate(inventory):
        _validate_entry(i, entry)


# ---------------------------------------------------------------------------
# Section parsing / location helpers
# ---------------------------------------------------------------------------

def _parse_section(section: str) -> tuple[str, str | None]:
    """Return ('inputs', '<Entity>'), ('computed', None), or ('outputs', None)."""
    if section == "computed":
        return ("computed", None)
    if section == "outputs":
        return ("outputs", None)
    m = _INPUTS_SECTION_RE.match(section)
    if m:
        return ("inputs", m.group(1))
    raise ValueError(f"unparseable section: {section!r}")


def _lookup_existing(manifest: dict, section: str, entity: str | None,
                     name: str) -> dict | None:
    """Return the existing entry at the given location, or None."""
    if section == "inputs":
        return manifest.get("inputs", {}).get(entity, {}).get(name)
    return manifest.get(section, {}).get(name)


def _remove_existing(manifest: dict, section: str, entity: str | None,
                     name: str) -> dict | None:
    """Remove and return the entry at the given location, or None."""
    if section == "inputs":
        entity_map = manifest.get("inputs", {}).get(entity)
        if isinstance(entity_map, dict) and name in entity_map:
            return entity_map.pop(name)
        return None
    sec_map = manifest.get(section)
    if isinstance(sec_map, dict) and name in sec_map:
        return sec_map.pop(name)
    return None


def _put_existing(manifest: dict, section: str, entity: str | None,
                  name: str, value: dict) -> None:
    """Insert an entry at the given location, creating sub-dicts as needed."""
    if section == "inputs":
        manifest.setdefault("inputs", {})
        manifest["inputs"].setdefault(entity, {})
        manifest["inputs"][entity][name] = value
    else:
        manifest.setdefault(section, {})
        manifest[section][name] = value


def _find_name_anywhere(manifest: dict, name: str) -> tuple[str, str | None] | None:
    """Return (section, entity) where `name` currently exists, or None.

    Used to detect cross-section moves and the pathological case where the
    inventory's `name` collides with an entry in a different section than
    the inventory's target.
    """
    inputs = manifest.get("inputs", {})
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if isinstance(fields, dict) and name in fields:
                return ("inputs", entity)
    for sec in ("computed", "outputs"):
        sec_map = manifest.get(sec)
        if isinstance(sec_map, dict) and name in sec_map:
            return (sec, None)
    return None


# ---------------------------------------------------------------------------
# Synonym helpers
# ---------------------------------------------------------------------------

def _is_rename_anchor(syn: dict) -> bool:
    """A synonym is a rename-anchor when it has no `source_doc` / `section`."""
    return (
        isinstance(syn, dict)
        and "source_doc" not in syn
        and "section" not in syn
    )


def _synonym_names(synonyms: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for s in synonyms or []:
        if isinstance(s, dict) and isinstance(s.get("name"), str):
            out.add(s["name"])
    return out


def _merge_synonyms(
    existing_synonyms: list[dict] | None,
    rename_anchor_name: str | None,
    observed_synonyms: list[dict] | None,
) -> tuple[list[dict], int, int]:
    """Build the merged synonyms list and return (list, anchor_added, observed_added).

    Order: existing (in original order), then new rename-anchor (if any), then
    new observed-phrasing synonyms (in inventory order). Dedup by `name`."""
    out: list[dict] = []
    seen: set[str] = set()
    if existing_synonyms:
        for s in existing_synonyms:
            if not isinstance(s, dict):
                continue
            n = s.get("name")
            if not isinstance(n, str):
                continue
            if n in seen:
                continue
            out.append(s)
            seen.add(n)

    anchor_added = 0
    if rename_anchor_name and rename_anchor_name not in seen:
        out.append({"name": rename_anchor_name})
        seen.add(rename_anchor_name)
        anchor_added = 1

    observed_added = 0
    for s in observed_synonyms or []:
        if not isinstance(s, dict):
            continue
        n = s.get("name")
        if not isinstance(n, str) or n in seen:
            continue
        new_entry: dict = {"name": n}
        if isinstance(s.get("source_doc"), str):
            new_entry["source_doc"] = s["source_doc"]
        if isinstance(s.get("section"), str):
            new_entry["section"] = s["section"]
        out.append(new_entry)
        seen.add(n)
        observed_added += 1

    return out, anchor_added, observed_added


# ---------------------------------------------------------------------------
# Per-entry build
# ---------------------------------------------------------------------------

def _preserve_non_null(existing: dict | None, inventory_value: Any) -> Any:
    """preserve-non-null: existing wins when non-null; inventory fills null."""
    if existing is not None:
        return existing
    return inventory_value


def _build_entry(
    inventory_entry: dict,
    existing: dict | None,
    rename_anchor: str | None,
    carried_synonyms: list[dict] | None,
) -> tuple[dict, dict[str, int]]:
    """Construct the merged entry dict.

    Returns (entry, counters) where counters has anchor_added and observed_added
    counts. `role_hint:` is never written. Synonyms list is omitted when empty.
    """
    if existing is None:
        existing = {}

    inv_pp = inventory_entry.get("policy_phrase")
    inv_sd = inventory_entry.get("source_doc")
    inv_st = inventory_entry.get("section_text")

    pp = _preserve_non_null(existing.get("policy_phrase"), inv_pp)
    sd = _preserve_non_null(existing.get("source_doc"), inv_sd)
    sec = _preserve_non_null(existing.get("section"), inv_st)

    description = _preserve_non_null(
        existing.get("description"), inventory_entry.get("description")
    )
    type_ = _preserve_non_null(
        existing.get("type"), inventory_entry.get("type")
    )
    optional_ = _preserve_non_null(
        existing.get("optional"), inventory_entry.get("optional")
    )
    values = _preserve_non_null(
        existing.get("values"), inventory_entry.get("values")
    )
    enum_variants = _preserve_non_null(
        existing.get("enum_variants"), inventory_entry.get("enum_variants")
    )

    existing_synonyms = existing.get("synonyms")
    if not isinstance(existing_synonyms, list):
        existing_synonyms = carried_synonyms or []

    obs = inventory_entry.get("observed_synonyms")
    if not isinstance(obs, list):
        obs = []

    synonyms_list, anchor_added, observed_added = _merge_synonyms(
        existing_synonyms, rename_anchor, obs
    )

    # Write fields in canonical order (matches existing hand-authored manifests).
    entry: dict = {}
    if pp is not None:
        entry["policy_phrase"] = pp
    if sd is not None:
        entry["source_doc"] = sd
    if sec is not None:
        entry["section"] = sec
    if description is not None:
        entry["description"] = description
    if type_ is not None:
        entry["type"] = type_
    if optional_ is not None:
        entry["optional"] = optional_
    if values is not None:
        entry["values"] = values
    if enum_variants is not None:
        entry["enum_variants"] = enum_variants
    if synonyms_list:
        entry["synonyms"] = synonyms_list

    return entry, {"anchor_added": anchor_added, "observed_added": observed_added}


# ---------------------------------------------------------------------------
# Merge orchestration
# ---------------------------------------------------------------------------

def _merge_one(
    i: int,
    inv_entry: dict,
    manifest: dict,
    touched_keys: set[tuple[str, str | None, str]],
    counters: dict[str, int],
    warnings: list[str],
) -> None:
    """Apply the R11 decision matrix for a single inventory entry,
    mutating `manifest` in place."""
    name = inv_entry["name"]
    section_str = inv_entry["section"]
    section, entity = _parse_section(section_str)
    target_key = (section, entity, name)

    prior_name = inv_entry.get("prior_name")

    existing_in_target = _lookup_existing(manifest, section, entity, name)
    prior_in_target = (
        _lookup_existing(manifest, section, entity, prior_name)
        if prior_name else None
    )

    # Cross-section detection: if entry with same name lives in a different
    # section than target, treat as drop-and-add (rename-like).
    cross_section_loc: tuple[str, str | None] | None = None
    if existing_in_target is None and prior_in_target is None:
        loc = _find_name_anywhere(manifest, name)
        if loc is not None and loc != (section, entity):
            cross_section_loc = loc

    # --- Matrix branches ---

    if prior_name is None:
        if existing_in_target is not None:
            # Row 2: MERGE preserve-non-null (no rename-anchor synonym).
            new_entry, _ = _build_entry(
                inv_entry, existing_in_target,
                rename_anchor=None, carried_synonyms=None,
            )
            _put_existing(manifest, section, entity, name, new_entry)
            counters["entries_preserved"] += 1
            counters["synonyms_appended_observed"] += _.get("observed_added", 0)
            touched_keys.add(target_key)
            return

        if cross_section_loc is not None:
            # Row 6: cross-section move — drop from old section, add to new.
            old_section, old_entity = cross_section_loc
            existing_old = _remove_existing(manifest, old_section, old_entity, name)
            warnings.append(
                f"{name} moved from "
                f"{_format_loc(old_section, old_entity)} to "
                f"{_format_loc(section, entity)}"
            )
            new_entry, c = _build_entry(
                inv_entry, existing_old,
                rename_anchor=None, carried_synonyms=None,
            )
            _put_existing(manifest, section, entity, name, new_entry)
            counters["entries_added"] += 1
            counters["synonyms_appended_observed"] += c["observed_added"]
            touched_keys.add(target_key)
            # Also mark the old location as touched so --preserve-unmentioned
            # doesn't resurrect it.
            touched_keys.add((old_section, old_entity, name))
            return

        # Row 1: APPEND new entry, no synonyms from this writer.
        new_entry, c = _build_entry(
            inv_entry, None,
            rename_anchor=None, carried_synonyms=None,
        )
        _put_existing(manifest, section, entity, name, new_entry)
        counters["entries_added"] += 1
        counters["synonyms_appended_observed"] += c["observed_added"]
        touched_keys.add(target_key)
        return

    # prior_name is set from here down.

    if existing_in_target is not None and prior_in_target is not None:
        # Row 3: PATHOLOGICAL — both name and prior_name exist separately.
        raise MergeConflict(
            f"inventory[{i}] would conflict — both name {name!r} and "
            f"prior_name {prior_name!r} exist as separate entries in "
            f"{_format_loc(section, entity)}"
        )

    if prior_in_target is not None:
        # Row 4: RENAME.
        old = _remove_existing(manifest, section, entity, prior_name)
        carried = (old.get("synonyms") if isinstance(old, dict) else None) or []
        existing_synonym_names = _synonym_names(carried)
        anchor_name = prior_name if prior_name not in existing_synonym_names else None
        new_entry, c = _build_entry(
            inv_entry, old,
            rename_anchor=anchor_name, carried_synonyms=list(carried),
        )
        _put_existing(manifest, section, entity, name, new_entry)
        counters["entries_renamed"] += 1
        counters["synonyms_appended_rename_anchor"] += c["anchor_added"]
        counters["synonyms_appended_observed"] += c["observed_added"]
        touched_keys.add(target_key)
        # Mark old prior_name location as touched so it isn't resurrected
        # under --preserve-unmentioned.
        touched_keys.add((section, entity, prior_name))
        return

    if existing_in_target is not None:
        # prior_name set but it doesn't exist; the new name already does.
        # Treat as merge by name with a stale-prior_name warning.
        warnings.append(
            f"inventory[{i}].prior_name {prior_name!r} references a "
            f"non-existent entry (the entry under {name!r} already exists)"
        )
        new_entry, c = _build_entry(
            inv_entry, existing_in_target,
            rename_anchor=None, carried_synonyms=None,
        )
        _put_existing(manifest, section, entity, name, new_entry)
        counters["entries_preserved"] += 1
        counters["synonyms_appended_observed"] += c["observed_added"]
        touched_keys.add(target_key)
        return

    # Row 5: APPEND + WARN (prior_name set but no match by either).
    warnings.append(
        f"inventory[{i}].prior_name {prior_name!r} references non-existent entry"
    )
    new_entry, c = _build_entry(
        inv_entry, None,
        rename_anchor=None, carried_synonyms=None,
    )
    _put_existing(manifest, section, entity, name, new_entry)
    counters["entries_added"] += 1
    counters["synonyms_appended_observed"] += c["observed_added"]
    touched_keys.add(target_key)


def _format_loc(section: str, entity: str | None) -> str:
    if section == "inputs":
        return f"inputs.{entity}"
    return section


# ---------------------------------------------------------------------------
# Output ordering
# ---------------------------------------------------------------------------

def _sort_manifest(manifest: dict) -> dict:
    """Return a new dict with stable canonical ordering.

    Top-level: version, inputs, computed, outputs.
    inputs.<Entity>: entities sorted alphabetically; fields within each
    sorted alphabetically.
    computed:/outputs:: fields sorted alphabetically.
    """
    out: dict = OrderedDict()
    version = manifest.get("version", _MANIFEST_VERSION)
    if not isinstance(version, str):
        version = _MANIFEST_VERSION
    out["version"] = version

    inputs_raw = manifest.get("inputs", {})
    if not isinstance(inputs_raw, dict):
        inputs_raw = {}
    inputs_sorted: dict = OrderedDict()
    for entity in sorted(inputs_raw.keys()):
        fields = inputs_raw[entity]
        if not isinstance(fields, dict):
            inputs_sorted[entity] = {}
            continue
        inputs_sorted[entity] = OrderedDict(
            (k, fields[k]) for k in sorted(fields.keys())
        )
    out["inputs"] = inputs_sorted

    for sec in ("computed", "outputs"):
        sec_raw = manifest.get(sec, {})
        if not isinstance(sec_raw, dict):
            sec_raw = {}
        out[sec] = OrderedDict((k, sec_raw[k]) for k in sorted(sec_raw.keys()))

    return out


# ---------------------------------------------------------------------------
# Drop-unmentioned (default for /extract-ruleset; reversed for /update-ruleset)
# ---------------------------------------------------------------------------

def _enumerate_all_keys(manifest: dict) -> list[tuple[str, str | None, str]]:
    keys: list[tuple[str, str | None, str]] = []
    inputs = manifest.get("inputs", {})
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if isinstance(fields, dict):
                for name in fields.keys():
                    keys.append(("inputs", entity, name))
    for sec in ("computed", "outputs"):
        sec_map = manifest.get(sec, {})
        if isinstance(sec_map, dict):
            for name in sec_map.keys():
                keys.append((sec, None, name))
    return keys


def _drop_unmentioned(manifest: dict,
                      touched_keys: set[tuple[str, str | None, str]]) -> int:
    """Remove every entry whose location is not in touched_keys. Return the
    number removed."""
    removed = 0
    for section, entity, name in _enumerate_all_keys(manifest):
        if (section, entity, name) in touched_keys:
            continue
        _remove_existing(manifest, section, entity, name)
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------

def _yaml_setup() -> None:
    """Register dumper-side representers so OrderedDict serializes as a
    plain YAML mapping (preserving our explicit key order)."""
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


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


# ---------------------------------------------------------------------------
# Existing manifest load
# ---------------------------------------------------------------------------

def _load_existing_manifest(path: Path) -> tuple[dict, list[str]]:
    """Return (manifest_dict, warnings).

    When absent, treat as `{version: '1.0', inputs: {}, computed: {}, outputs: {}}`.
    When `version:` is a number (not a string), coerce to string and warn."""
    warnings: list[str] = []
    default: dict = {
        "version": _MANIFEST_VERSION,
        "inputs": {},
        "computed": {},
        "outputs": {},
    }
    if not path.is_file():
        return default, warnings
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return default, warnings
    if not isinstance(raw, dict):
        return default, warnings

    version = raw.get("version", _MANIFEST_VERSION)
    if not isinstance(version, str):
        warnings.append(
            f"existing manifest 'version' was {version!r} (not a string); "
            f"coercing to '{_MANIFEST_VERSION}'"
        )
        version = _MANIFEST_VERSION

    manifest: dict = {
        "version": version,
        "inputs": raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {},
        "computed": raw.get("computed") if isinstance(raw.get("computed"), dict) else {},
        "outputs": raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {},
    }
    return manifest, warnings


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(
    domain_dir: Path,
    program: str,  # unused at the file level but reserved for future scoping
    inventory_path: Path,
    check_only: bool,
    preserve_unmentioned: bool,
) -> int:
    if not domain_dir.is_dir():
        print(f"Error: Domain directory not found: {domain_dir}", file=sys.stderr)
        return 2
    if not inventory_path.is_file():
        print(f"Error: inventory file not found: {inventory_path}", file=sys.stderr)
        return 2

    try:
        with inventory_path.open(encoding="utf-8") as f:
            inventory = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: inventory file is not valid JSON: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: cannot read inventory file: {exc}", file=sys.stderr)
        return 1

    try:
        validate_inventory(inventory)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    manifest, version_warnings = _load_existing_manifest(manifest_path)

    counters = {
        "entries_added": 0,
        "entries_renamed": 0,
        "entries_preserved": 0,
        "synonyms_appended_rename_anchor": 0,
        "synonyms_appended_observed": 0,
    }
    touched: set[tuple[str, str | None, str]] = set()
    warnings: list[str] = list(version_warnings)

    try:
        for i, entry in enumerate(inventory):
            _merge_one(i, entry, manifest, touched, counters, warnings)
    except MergeConflict as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    entries_dropped = 0
    if not preserve_unmentioned:
        entries_dropped = _drop_unmentioned(manifest, touched)

    sorted_manifest = _sort_manifest(manifest)
    _yaml_setup()
    serialized = _serialize_yaml(sorted_manifest)

    mode = "check_only" if check_only else "write"
    header = {
        "mode": mode,
        "entries_added": counters["entries_added"],
        "entries_renamed": counters["entries_renamed"],
        "entries_preserved": counters["entries_preserved"],
        "entries_dropped": entries_dropped,
        "synonyms_appended_rename_anchor":
            counters["synonyms_appended_rename_anchor"],
        "synonyms_appended_observed": counters["synonyms_appended_observed"],
        "warnings": warnings,
    }

    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    if not check_only:
        try:
            _atomic_write(manifest_path, serialized)
        except OSError as exc:
            print(f"ERROR: write failed: {exc}", file=sys.stderr)
            return 1

    print(json.dumps(header))
    print(_HEADER_SENTINEL)
    if check_only:
        print(f"Would write specs/naming-manifest.yaml (no changes made).")
    else:
        print(f"Wrote specs/naming-manifest.yaml.")
    print(f"  {counters['entries_added']} new entries")
    print(f"  {counters['entries_renamed']} renames "
          f"(rename-anchor synonyms appended: "
          f"{counters['synonyms_appended_rename_anchor']})")
    print(f"  {counters['entries_preserved']} preserved "
          f"(preserve-non-null applied to optional fields)")
    if entries_dropped:
        print(f"  {entries_dropped} dropped (not referenced by inventory)")
    if counters["synonyms_appended_observed"]:
        print(f"  {counters['synonyms_appended_observed']} observed-phrasing "
              f"synonyms appended")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply Step-7 deterministic merge rules to specs/naming-manifest.yaml "
            "from an analyst-approved Name Inventory JSON file. Enforces "
            "preserve-non-null, rename-via-synonyms-append (idempotent), "
            "drop-on-rename, and seeded-entry provenance gap-fill."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("program", help="Program name (e.g. eligibility)")
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to the analyst-approved inventory JSON file.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and compute the diff without writing the manifest.",
    )
    parser.add_argument(
        "--preserve-unmentioned",
        action="store_true",
        help=(
            "Keep existing entries not referenced by the inventory. Used by "
            "/update-ruleset Step 9 (which adds new fields without "
            "re-presenting the full inventory). Without this flag, "
            "unmentioned entries are dropped."
        ),
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    inventory_path = Path(args.inventory)
    return run(
        domain_dir,
        args.program,
        inventory_path,
        args.check_only,
        args.preserve_unmentioned,
    )


if __name__ == "__main__":
    sys.exit(main())
