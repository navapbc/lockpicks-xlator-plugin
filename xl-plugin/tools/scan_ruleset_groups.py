#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator scan-ruleset-groups: deterministic ruleset-group scan.

Replaces the (1a) explicit-`stage:` branch, the UPDATE-m merge precedence
rules, and the `display_name`-derived single-group catch-all of
`xl-plugin/skills/create-ruleset-groups/SKILL.md` Step 1.

The optional (1b) heading-text branch stays AI-driven in the skill; when
the tool detects the no-stage / no-existing / no-display_name corner case
it emits `heading_text_fallback_recommended: true` in its JSON header and
performs no write. The skill then AI-scans headings, writes a JSON file of
candidates, and re-invokes the tool with `--heading-derived-candidates`.

Inputs:
  - <domain>                      positional, resolved against $DOMAINS_FULLPATH
  - --mode {create,replace,merge} default `create`
  - --heading-derived-candidates  optional JSON file: list[{name, description}]

Output:
  - Atomic write of specs/guidance/ruleset-groups.yaml (modes create/replace/
    merge; skipped when the catch-all cannot fire and no candidates exist).
  - Stdout JSON header line, sentinel divider, human-readable proposal table.

Exit codes:
    0 — success (write or deliberate no-op when only the fallback flag is set)
    2 — pre-flight failure or create-mode collision
    1 — unexpected error (malformed --heading-derived-candidates, IO error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_METADATA_REL = "specs/guidance/metadata.yaml"
_SKELETON_REL = "specs/guidance/skeleton.yaml"
_RULESET_GROUPS_REL = "specs/guidance/ruleset-groups.yaml"
_PER_FILE_REL = "policy_facets/computations"

_HEADER_SENTINEL = "--- SCAN-RULESET-GROUPS-HEADER-END ---"

# Trailing suffixes stripped from a `stage:` value before cross-file
# comparison. Mirrors the suffix-stripping rule in `/create-ruleset-groups`
# Step 1 so stage identifiers compare equal across writer skills.
_STAGE_STRIP_SUFFIXES = ("_test", "_check", "_evaluation")


# ---------------------------------------------------------------------------
# policy_facets/computations/ helpers (inlined from civil_helpers.py;
# these helpers operate on the input-pipeline tier — unchanged by the
# CIVIL→Catala pivot — so the dependency on civil_helpers is dropped
# rather than carried forward into the deletion in U8).
# ---------------------------------------------------------------------------

def normalize_stage(stage: Any) -> Optional[str]:
    """Normalize a `stage:` value for cross-section comparison.

    Strips a single trailing `_test` / `_check` / `_evaluation`
    (case-insensitively) and lowercases the result. Returns `None` for
    `None` / non-string / empty input.
    """
    if stage is None:
        return None
    if not isinstance(stage, str):
        return None
    s = stage.strip().lower()
    if not s:
        return None
    for suffix in _STAGE_STRIP_SUFFIXES:
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def load_per_file_computations(domain_dir: Path) -> dict[str, dict]:
    """Glob every `policy_facets/computations/**/*.md.yaml` under
    `domain_dir` and return a dict keyed by relative path string
    (POSIX-style, relative to `policy_facets/computations/`) → parsed
    YAML mapping.

    Files that fail to parse as YAML or that don't parse to a mapping
    are skipped silently. Returns `{}` when the directory is absent.
    """
    base = domain_dir / "policy_facets" / "computations"
    if not base.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(base.rglob("*.md.yaml")):
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        rel = path.relative_to(base).as_posix()
        out[rel] = doc
    return out

# Leading display_name verbs stripped before snake_casing the catch-all
# group name. The remainder of `display_name` becomes the group name; the
# raw `display_name` becomes the description. Hard-coded list — the analyst
# may hand-edit `ruleset-groups.yaml` after the write if the strip is wrong.
_DISPLAY_NAME_LEADING_VERBS = (
    "determine",
    "assess",
    "compute",
    "evaluate",
    "calculate",
)


# ---------------------------------------------------------------------------
# Candidate data
# ---------------------------------------------------------------------------

class Candidate(dict):
    """A `{name, description, origin}` dict. Subclassing `dict` keeps the
    YAML-emit path unchanged: `{k: v for k, v in c.items() if k != 'origin'}`
    strips the bookkeeping field at serialize time."""


def _make_candidate(name: str, description: str, origin: str) -> Candidate:
    c = Candidate()
    c["name"] = name
    c["description"] = description
    c["origin"] = origin
    return c


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(domain_dir: Path) -> Optional[str]:
    """Return None when pre-flight passes; otherwise a stderr error message.

    Order matches the prior skill's error messages so smoke tests against
    existing skill outputs continue to assert the same strings.
    """
    if not domain_dir.is_dir():
        return f"Domain not found: {domain_dir}/"
    metadata_path = domain_dir / _METADATA_REL
    if not metadata_path.is_file():
        return (
            f"guidance/metadata.yaml not found: {metadata_path}\n"
            f"Run /declare-target-ruleset {domain_dir.name} first."
        )
    skeleton_path = domain_dir / _SKELETON_REL
    if not skeleton_path.is_file():
        return (
            f"Skeleton not found: {skeleton_path}\n"
            f"Run /create-skeleton {domain_dir.name} first."
        )
    per_file_dir = domain_dir / _PER_FILE_REL
    if not per_file_dir.is_dir():
        return (
            f"Per-file computations not found under: {per_file_dir}/\n"
            f"Run /index-inputs {domain_dir.name} first."
        )
    if not any(per_file_dir.rglob("*.md.yaml")):
        return (
            f"Per-file computations not found under: {per_file_dir}/\n"
            f"Run /index-inputs {domain_dir.name} first."
        )
    return None


# ---------------------------------------------------------------------------
# Stage-derived candidate detection (1a)
# ---------------------------------------------------------------------------

def _humanize_stage(name: str) -> str:
    """Underscores → spaces, then title-case the result.

    Matches the existing skill rule: no acronym preservation (so
    `ebt_eligibility` → `"Ebt Eligibility"`, not `"EBT Eligibility"`).
    """
    return name.replace("_", " ").title()


def _collect_stage_candidates(per_file: dict[str, dict]) -> list[Candidate]:
    """Walk every per-file YAML's `sections[*].stage:` value, normalize via
    `normalize_stage()`, dedup case-insensitively (first-seen casing wins),
    and emit one stage-derived candidate per distinct canonical value.

    Per-file iteration order is `load_per_file_computations` sort order
    (POSIX-style relative path), and within each file the section
    declaration order; both are deterministic.
    """
    seen: dict[str, str] = {}  # normalized → canonical-name-as-written
    order: list[str] = []
    for doc in per_file.values():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            stage_raw = section.get("stage")
            normalized = normalize_stage(stage_raw)
            if normalized is None:
                continue
            if normalized in seen:
                continue
            seen[normalized] = normalized
            order.append(normalized)
    return [
        _make_candidate(name, _humanize_stage(name), "stage")
        for name in order
    ]


# ---------------------------------------------------------------------------
# Heading-derived candidate loading (1b passthrough)
# ---------------------------------------------------------------------------

def _load_heading_candidates(path: Path) -> list[Candidate]:
    """Load --heading-derived-candidates JSON. Raises ValueError with a
    user-facing message on any structural problem so main() can map it to
    exit code 1.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"heading-derived candidates file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"heading-derived candidates file is not valid JSON: {path}: {exc}"
        )
    if not isinstance(data, list):
        raise ValueError(
            f"heading-derived candidates file must contain a JSON list of "
            f"{{name, description}} objects: {path}"
        )
    out: list[Candidate] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"heading-derived candidate at index {i} is not a JSON object"
            )
        name = entry.get("name")
        description = entry.get("description", "")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"heading-derived candidate at index {i} missing required "
                f"`name` string"
            )
        if not isinstance(description, str):
            raise ValueError(
                f"heading-derived candidate at index {i} has non-string "
                f"`description`"
            )
        out.append(_make_candidate(name, description, "heading"))
    return out


# ---------------------------------------------------------------------------
# Existing-entry loading
# ---------------------------------------------------------------------------

def _load_existing_groups(path: Path) -> list[dict[str, Any]]:
    """Load existing `specs/guidance/ruleset-groups.yaml`. Returns `[]` when
    the file is absent or malformed (callers gate behavior on file existence
    separately via `path.exists()`)."""
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    raw = doc.get("ruleset_groups") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.append({
            "name": name,
            "description": entry.get("description", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Catch-all (display_name-derived)
# ---------------------------------------------------------------------------

def _derive_catch_all(metadata: dict[str, Any]) -> Optional[Candidate]:
    """Derive a single catch-all group from `metadata.display_name`.

    Strips a leading verb from `_DISPLAY_NAME_LEADING_VERBS` (case-insensitive,
    followed by whitespace), snake_cases the remainder, and returns a
    Candidate with description = the original `display_name` verbatim.

    Returns `None` when `display_name` is missing, not a string, or empty
    after stripping. The caller treats `None` as the trigger for the
    heading-text fallback flag.
    """
    display_name_raw = metadata.get("display_name") if isinstance(metadata, dict) else None
    if not isinstance(display_name_raw, str):
        return None
    display_name = display_name_raw.strip()
    if not display_name:
        return None
    remainder = display_name
    lowered = display_name.lower()
    for verb in _DISPLAY_NAME_LEADING_VERBS:
        prefix = verb + " "
        if lowered.startswith(prefix):
            remainder = display_name[len(prefix):].strip()
            break
    if not remainder:
        # display_name was a bare verb (e.g. "Determine") — nothing to derive.
        return None
    snake = "_".join(remainder.lower().split())
    return _make_candidate(snake, display_name, "catch_all")


# ---------------------------------------------------------------------------
# Merge precedence
# ---------------------------------------------------------------------------

def _merge(
    mode: str,
    existing: list[dict[str, Any]],
    stage_cands: list[Candidate],
    heading_cands: list[Candidate],
    catch_all: Optional[Candidate],
) -> list[dict[str, Any]]:
    """Apply mode-specific merge logic. Returns the ordered list of entries
    to write (each a `{name, description}` dict; the `origin` bookkeeping
    field is dropped here)."""
    # Heading-vs-stage collision: stage wins (explicit doc signal beats
    # inference). Drop colliding heading candidates before the merge.
    stage_names = {c["name"] for c in stage_cands}
    heading_cands = [c for c in heading_cands if c["name"] not in stage_names]

    if mode == "create" or mode == "replace":
        # No existing entries participate. Catch-all only emits when no
        # other candidate exists, so it's mutually exclusive with stage/
        # heading.
        ordered: list[Candidate] = []
        ordered.extend(stage_cands)
        ordered.extend(heading_cands)
        if not ordered and catch_all is not None:
            ordered.append(catch_all)
        return [{"name": c["name"], "description": c["description"]} for c in ordered]

    # mode == "merge"
    # Output order: existing entries first (original order), then new
    # stage-derived entries (sorted alphabetically for determinism), then
    # new heading-derived entries (in input order).
    result: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in existing:
        name = entry["name"]
        if name in result:
            continue
        result[name] = {"name": name, "description": entry.get("description", "")}
        order.append(name)

    new_stage = [c for c in stage_cands if c["name"] not in result]
    new_stage.sort(key=lambda c: c["name"])
    for cand in new_stage:
        result[cand["name"]] = {
            "name": cand["name"],
            "description": cand["description"],
        }
        order.append(cand["name"])

    for cand in heading_cands:
        name = cand["name"]
        if name in result:
            # Legacy "new wins" rule for heading-derived candidates.
            result[name]["description"] = cand["description"]
            continue
        result[name] = {"name": name, "description": cand["description"]}
        order.append(name)

    # Stage-derived candidates that collide with an existing entry leave the
    # existing description untouched (analyst-edit sticky). No work needed:
    # `new_stage` already excluded them by `c["name"] not in result`.

    if not order and catch_all is not None:
        result[catch_all["name"]] = {
            "name": catch_all["name"],
            "description": catch_all["description"],
        }
        order.append(catch_all["name"])

    return [result[name] for name in order]


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _serialize(groups: list[dict[str, Any]]) -> str:
    doc = {"ruleset_groups": groups}
    return yaml.safe_dump(
        doc,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10_000,
    )


def _atomic_write(dest: Path, content: str) -> None:
    """Write `content` to `dest` via `tmp + os.replace`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


# ---------------------------------------------------------------------------
# Stdout
# ---------------------------------------------------------------------------

def _format_table(groups: list[dict[str, Any]]) -> str:
    """Match the existing skill's `:::detail` Step 2 format."""
    header = (
        "Proposed ruleset groups\n"
        "────────────────────────────────────────────────"
    )
    if not groups:
        return header + "\n  (none)"
    name_width = max((len(g["name"]) for g in groups), default=0)
    name_width = max(name_width, 4)
    lines = [header]
    for i, g in enumerate(groups, 1):
        lines.append(
            f"  {i}. {g['name']:<{name_width}}  — {g.get('description', '')}"
        )
    return "\n".join(lines)


def _format_header(
    candidate_count: int,
    stage_derived_count: int,
    heading_derived_count: int,
    catch_all_fired: bool,
    heading_text_fallback_recommended: bool,
    existing_entries_count: int,
) -> str:
    return json.dumps({
        "candidate_count": candidate_count,
        "stage_derived_count": stage_derived_count,
        "heading_derived_count": heading_derived_count,
        "catch_all_fired": catch_all_fired,
        "heading_text_fallback_recommended": heading_text_fallback_recommended,
        "existing_entries_count": existing_entries_count,
    })


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(
    domain_dir: Path,
    mode: str,
    heading_candidates_path: Optional[Path],
) -> int:
    preflight_err = _preflight(domain_dir)
    if preflight_err is not None:
        print(preflight_err, file=sys.stderr)
        return 2

    groups_path = domain_dir / _RULESET_GROUPS_REL
    if mode == "create" and groups_path.exists():
        print(
            "Ruleset groups already exist. Use --mode replace or --mode merge.",
            file=sys.stderr,
        )
        return 2

    # Load every signal source.
    per_file = load_per_file_computations(domain_dir)
    stage_cands = _collect_stage_candidates(per_file)

    heading_cands: list[Candidate] = []
    if heading_candidates_path is not None:
        try:
            heading_cands = _load_heading_candidates(heading_candidates_path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    metadata_doc: dict[str, Any] = {}
    try:
        with (domain_dir / _METADATA_REL).open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                metadata_doc = loaded
    except (OSError, yaml.YAMLError):
        # Pre-flight already verified the file exists; a read failure here
        # is unexpected. The catch-all derivation handles empty/missing
        # display_name gracefully.
        pass

    existing_entries: list[dict[str, Any]] = []
    if mode == "merge":
        existing_entries = _load_existing_groups(groups_path)

    # Catch-all only fires when nothing else contributes a candidate.
    catch_all_eligible = (
        not stage_cands
        and not heading_cands
        and not existing_entries
    )
    catch_all = _derive_catch_all(metadata_doc) if catch_all_eligible else None
    catch_all_fired = catch_all is not None

    # Heading-text fallback flag: tool produced nothing the skill can use,
    # so the skill should ask AI to scan headings and re-invoke. Set only
    # when there are no stage candidates, no existing entries, no heading
    # candidates supplied, AND the catch-all couldn't derive a name.
    heading_text_fallback_recommended = (
        catch_all_eligible and catch_all is None
    )

    merged = _merge(mode, existing_entries, stage_cands, heading_cands, catch_all)

    # When the fallback flag fires we deliberately do NOT write — the
    # downstream skill is expected to re-invoke with AI-derived candidates.
    if heading_text_fallback_recommended:
        header_json = _format_header(
            candidate_count=0,
            stage_derived_count=0,
            heading_derived_count=0,
            catch_all_fired=False,
            heading_text_fallback_recommended=True,
            existing_entries_count=len(existing_entries),
        )
        print(header_json)
        print(_HEADER_SENTINEL)
        print(_format_table([]))
        return 0

    content = _serialize(merged)
    _atomic_write(groups_path, content)

    header_json = _format_header(
        candidate_count=len(merged),
        stage_derived_count=len(stage_cands),
        heading_derived_count=len(heading_cands),
        catch_all_fired=catch_all_fired,
        heading_text_fallback_recommended=False,
        existing_entries_count=len(existing_entries),
    )
    print(header_json)
    print(_HEADER_SENTINEL)
    print(_format_table(merged))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a domain's policy_facets/computations/ for explicit "
            "`stage:` values, apply UPDATE-m merge precedence (preserve "
            "analyst-edited descriptions), and write "
            "specs/guidance/ruleset-groups.yaml. Emits a flag when the "
            "skill should AI-scan section headings as a fallback."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "--mode",
        choices=("create", "replace", "merge"),
        default="create",
        help=(
            "create (default): write ruleset-groups.yaml; fail if it "
            "already exists. replace: overwrite unconditionally. merge: "
            "preserve existing entries (analyst-edit-sticky on collision)."
        ),
    )
    parser.add_argument(
        "--heading-derived-candidates",
        dest="heading_derived_candidates",
        default=None,
        help=(
            "Path to a JSON file containing a list of {name, description} "
            "objects produced by the skill's AI heading-text scan. Merged "
            "into the candidate set after stage-derived candidates."
        ),
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    heading_path = (
        Path(args.heading_derived_candidates)
        if args.heading_derived_candidates is not None
        else None
    )
    return run(domain_dir, args.mode, heading_path)


if __name__ == "__main__":
    sys.exit(main())
