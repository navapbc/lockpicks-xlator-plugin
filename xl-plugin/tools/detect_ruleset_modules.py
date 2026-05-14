#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator detect-ruleset-modules: deterministic ruleset-module detection.

Replaces the AI-interpreted 7-heuristic table in
`xl-plugin/skills/create-ruleset-modules/SKILL.md` Step 2 with a Python
implementation. Loads all signal sources (skeleton, ruleset-groups, per-file
computations, naming-manifest, output-variables), runs six purely mechanical
heuristics in priority order, applies the R21 stage-boundary constraint,
derives the main-module name when an output-variables.yaml `primary:` entry
exists, and writes `specs/guidance/ruleset-modules.yaml`.

Heuristics (priority order, highest first):
  H1 reuse_across_entities — (1a) mirrored input fields across entities,
                             (1b) parallel variable-name prefixes.
                             Heuristic 1c (cross-source comparison language)
                             is deferred to optional skill-side AI top-up.
  H2 policy_structure       — section heading covers >=3 intermediate vars.
  H3 sequential_chain       — per-file LHS-of-one / RHS-of-next chains of
                             >=3 computations within a single stage.
  H4 depth_threshold        — >=5 vars sharing a sequential-dependence name
                             prefix (after_, net_, gross_, total_, final_,
                             adjusted_).
  H5 variable_coupling      — >=3-var clique in the variable-dependency graph.
  H6 shared_gate            — >=3 vars sharing a guard-variable prefix or
                             >=3 vars whose preconditions reference the same
                             whitespace-normalized clause.

Priority dedup: each heuristic claims its variable set; a later candidate is
suppressed when its variable set has Jaccard similarity >=0.5 with any
already-claimed set.

R21 stage-boundary: when `stage:` is populated for every variable in a
candidate, all variables must share one post-normalization stage. Mixed-stage
candidates are split per stage; sub-candidates failing the heuristic's
minimum-size threshold are dropped (with a warning in the JSON header's
`dropped_candidates:` array).

Output:
  - Stdout JSON header line (single line), sentinel divider
    `--- DETECT-RULESET-MODULES-HEADER-END ---`, then a human-readable
    table the skill relays in :::detail.
  - Atomic write of `specs/guidance/ruleset-modules.yaml`.

Usage:
    xlator detect-ruleset-modules <domain> [--main-module-name <name>]

Exit codes:
    0 — success
    2 — pre-flight failure (missing domain, missing required files,
        unset DOMAINS_FULLPATH, argparse errors)
    1 — unexpected error (IO error, YAML parse failure)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

# Allow `python detect_ruleset_modules.py` and `uv run detect_ruleset_modules.py`
# to find the sibling civil_helpers module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from civil_helpers import (  # noqa: E402
    load_per_file_computations,
    normalize_stage,
    parse_expr_hint,
)


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_METADATA_REL = "specs/guidance/metadata.yaml"
_SKELETON_REL = "specs/guidance/skeleton.yaml"
_RULESET_GROUPS_REL = "specs/guidance/ruleset-groups.yaml"
_RULESET_MODULES_REL = "specs/guidance/ruleset-modules.yaml"
_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"
_OUTPUT_VARIABLES_REL = "specs/guidance/output-variables.yaml"
_PER_FILE_REL = "policy_facets/computations"

_HEADER_SENTINEL = "--- DETECT-RULESET-MODULES-HEADER-END ---"


# ---------------------------------------------------------------------------
# Heuristic constants
# ---------------------------------------------------------------------------

# Priority order from highest to lowest. The candidate emitted by a
# higher-priority heuristic "claims" its variable set, and lower-priority
# candidates with >= JACCARD_THRESHOLD overlap against any claimed set are
# suppressed.
_HEURISTIC_PRIORITY = (
    "reuse_across_entities",
    "policy_structure",
    "sequential_chain",
    "depth_threshold",
    "variable_coupling",
    "shared_gate",
)

# Minimum candidate sizes (number of variables). Used both at detection time
# and during R21 split-or-drop.
_MIN_SIZE = {
    "reuse_across_entities": 2,
    "policy_structure": 3,
    "sequential_chain": 3,
    "depth_threshold": 5,
    "variable_coupling": 3,
    "shared_gate": 3,
}

# Priority-dedup Jaccard threshold.
_JACCARD_SUPPRESS = 0.5

# Depth-threshold patterns: snake_case prefixes whose presence on >=5 vars
# signals a sequential-dependence chain.
_DEPTH_PREFIXES = ("after_", "net_", "gross_", "total_", "final_", "adjusted_")

# Shared-gate guard-variable prefixes (Heuristic 6a).
_GATE_PREFIXES = ("eligible_", "applies_if_", "qualified_")

# Primary-output suffix strips applied before deriving the main-module name.
_PRIMARY_OUTPUT_SUFFIXES = (
    "_check",
    "_determination",
    "_result",
    "_outcome",
    "_eligibility",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """An emitted detection candidate.

    `variables` is the canonical (ordered) list of variables the candidate
    binds. `stages` is the set of normalized stage strings present in those
    variables (empty when no `stage:` is populated on any source section).
    """

    name: str
    rationale: str
    variables: list[str]
    bound_entities: list[str]
    description: str
    stages: set[str] = field(default_factory=set)


@dataclass
class DomainContext:
    """All signals required to run detection on a domain."""

    domain_dir: Path
    skeleton: dict  # parsed skeleton.yaml
    ruleset_groups: dict  # parsed ruleset-groups.yaml
    naming_manifest: dict  # parsed naming-manifest.yaml
    output_variables: dict  # parsed output-variables.yaml (or {})
    metadata: dict  # parsed metadata.yaml
    per_file: dict[str, dict]  # rel-path → parsed per-file YAML

    # Derived indices (built from skeleton + per-file):
    skeleton_inputs: list[str]
    skeleton_outputs: list[str]
    skeleton_intermediate_vars: list[str]  # union of computations[*].variables
    var_to_stage: dict[str, Optional[str]]  # var → normalized stage (or None)
    var_to_entity: dict[str, str]  # var-name (any case) → CamelCase entity
    var_dep_graph: dict[str, set[str]]  # var → set of RHS-referenced vars
    var_rdep_graph: dict[str, set[str]]  # var → set of vars whose RHS references it

    # Existing on-disk modules (UPDATE-mode preservation set):
    existing_modules: list[dict]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    """Parse YAML at `path`. Returns `None` when the file is absent."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _preflight(domain_dir: Path) -> Optional[str]:
    """Return None when pre-flight passes; otherwise a stderr error message.

    Pre-flight order (matches the prior skill's error messages):
      1. Domain folder
      2. guidance/metadata.yaml
      3. guidance/skeleton.yaml
      4. guidance/ruleset-groups.yaml
      5. specs/naming-manifest.yaml
      6. policy_facets/computations/ exists and contains >=1 *.md.yaml
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
    groups_path = domain_dir / _RULESET_GROUPS_REL
    if not groups_path.is_file():
        return (
            f"Ruleset groups not found: {groups_path}\n"
            f"Run /create-ruleset-groups {domain_dir.name} first."
        )
    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    if not manifest_path.is_file():
        return (
            f"specs/naming-manifest.yaml not found: {manifest_path}\n"
            f"Run /declare-target-ruleset {domain_dir.name} first."
        )
    per_file_dir = domain_dir / _PER_FILE_REL
    if not per_file_dir.is_dir():
        return (
            f"Per-file computations not found under: {per_file_dir}/\n"
            f"Run /index-inputs {domain_dir.name} first."
        )
    has_any = any(per_file_dir.rglob("*.md.yaml"))
    if not has_any:
        return (
            f"Per-file computations not found under: {per_file_dir}/\n"
            f"Run /index-inputs {domain_dir.name} first."
        )
    return None


def _build_context(domain_dir: Path) -> DomainContext:
    """Read every signal source into a single DomainContext.

    Pre-flight is assumed to have run; required files are loaded
    unconditionally and missing-optional files (output-variables,
    ruleset-modules) default to `{}` / `[]`.
    """
    skeleton_doc = _load_yaml(domain_dir / _SKELETON_REL) or {}
    ruleset_groups_doc = _load_yaml(domain_dir / _RULESET_GROUPS_REL) or {}
    naming_manifest_doc = _load_yaml(domain_dir / _NAMING_MANIFEST_REL) or {}
    metadata_doc = _load_yaml(domain_dir / _METADATA_REL) or {}
    output_variables_doc = _load_yaml(domain_dir / _OUTPUT_VARIABLES_REL) or {}
    per_file = load_per_file_computations(domain_dir)

    existing = _load_yaml(domain_dir / _RULESET_MODULES_REL) or {}
    existing_modules: list[dict] = []
    if isinstance(existing, dict):
        raw_modules = existing.get("ruleset_modules") or []
        if isinstance(raw_modules, list):
            existing_modules = [m for m in raw_modules if isinstance(m, dict)]

    skeleton_block = (
        skeleton_doc.get("skeleton") if isinstance(skeleton_doc, dict) else None
    ) or {}
    inputs_raw = skeleton_block.get("inputs") or []
    outputs_raw = skeleton_block.get("outputs") or []
    skeleton_inputs = [str(x) for x in inputs_raw if isinstance(x, str)]
    skeleton_outputs = [str(x) for x in outputs_raw if isinstance(x, str)]
    intermediate_vars = _collect_skeleton_intermediate_vars(skeleton_block)

    var_to_stage = _build_var_to_stage(skeleton_block, per_file)
    var_to_entity = _build_var_to_entity(naming_manifest_doc)
    dep_graph, rdep_graph = _build_var_dep_graphs(skeleton_block, per_file)

    return DomainContext(
        domain_dir=domain_dir,
        skeleton=skeleton_block,
        ruleset_groups=ruleset_groups_doc if isinstance(ruleset_groups_doc, dict) else {},
        naming_manifest=naming_manifest_doc if isinstance(naming_manifest_doc, dict) else {},
        output_variables=output_variables_doc if isinstance(output_variables_doc, dict) else {},
        metadata=metadata_doc if isinstance(metadata_doc, dict) else {},
        per_file=per_file,
        skeleton_inputs=skeleton_inputs,
        skeleton_outputs=skeleton_outputs,
        skeleton_intermediate_vars=intermediate_vars,
        var_to_stage=var_to_stage,
        var_to_entity=var_to_entity,
        var_dep_graph=dep_graph,
        var_rdep_graph=rdep_graph,
        existing_modules=existing_modules,
    )


def _collect_skeleton_intermediate_vars(skeleton_block: dict) -> list[str]:
    """Flat ordered list of variables declared by `skeleton.computations[*].variables`.

    Preserves declaration order; duplicates dropped (first-occurrence wins).
    """
    out: list[str] = []
    seen: set[str] = set()
    computations = skeleton_block.get("computations") or []
    if not isinstance(computations, list):
        return out
    for entry in computations:
        if not isinstance(entry, dict):
            continue
        vars_list = entry.get("variables") or []
        if not isinstance(vars_list, list):
            continue
        for v in vars_list:
            if not isinstance(v, str):
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _build_var_to_stage(
    skeleton_block: dict,
    per_file: dict[str, dict],
) -> dict[str, Optional[str]]:
    """Map variable name → normalized stage value (or None).

    Sources, in order (later wins on overlap — per-file is authoritative):
      1. Skeleton's `computations[*]`: every variable in `variables:` is
         tagged with the entry's `stage:` value.
      2. Per-file `sections[*].computations[*].expr_hint` LHS: tagged with
         the section's `stage:` value (when populated).
    """
    out: dict[str, Optional[str]] = {}

    computations = skeleton_block.get("computations") or []
    if isinstance(computations, list):
        for entry in computations:
            if not isinstance(entry, dict):
                continue
            stage_norm = normalize_stage(entry.get("stage"))
            vars_list = entry.get("variables") or []
            if not isinstance(vars_list, list):
                continue
            for v in vars_list:
                if isinstance(v, str) and v not in out:
                    out[v] = stage_norm

    for doc in per_file.values():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            stage_norm = normalize_stage(section.get("stage"))
            if stage_norm is None:
                continue
            comps = section.get("computations") or []
            if not isinstance(comps, list):
                continue
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                parsed = parse_expr_hint(comp.get("expr_hint", ""))
                if parsed is None:
                    continue
                lhs, _, _ = parsed
                out[lhs] = stage_norm

    return out


def _build_var_to_entity(naming_manifest: dict) -> dict[str, str]:
    """Map field-name (case-insensitive lookup key) → owning CamelCase entity.

    Built from `naming_manifest.inputs.<Entity>.<field>` only. Variables
    that don't appear as a manifest input have no entry; callers consult
    this mapping when deriving `bound_entities` for non-reuse heuristics.

    When a field name is owned by multiple entities, the entry value
    becomes `__SHARED__` so the caller knows to fan out via a separate
    walk; this is what reuse_across_entities (1a) actually keys off of.
    """
    out: dict[str, str] = {}
    inputs = naming_manifest.get("inputs") if isinstance(naming_manifest, dict) else None
    if not isinstance(inputs, dict):
        return out
    for entity_name, fields in inputs.items():
        if not isinstance(fields, dict):
            continue
        for field_name in fields.keys():
            if not isinstance(field_name, str):
                continue
            if field_name not in out:
                out[field_name] = entity_name
            elif out[field_name] != entity_name:
                out[field_name] = "__SHARED__"
    return out


def _build_var_dep_graphs(
    skeleton_block: dict,
    per_file: dict[str, dict],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build (dep, rdep) graphs over declared variables.

    `dep[v]` = set of variables that appear on the RHS of v's expr.
    `rdep[v]` = set of variables whose RHS references v.

    Sources:
      - `skeleton.computations[*].exprs[lhs] = rhs`
      - per-file `sections[*].computations[*].expr_hint`
    """
    dep: dict[str, set[str]] = {}
    rdep: dict[str, set[str]] = {}

    def _record(lhs: str, rhs_tokens: Iterable[str]) -> None:
        bucket = dep.setdefault(lhs, set())
        for tok in rhs_tokens:
            if tok == lhs:
                continue
            bucket.add(tok)
            rdep.setdefault(tok, set()).add(lhs)

    computations = skeleton_block.get("computations") or []
    if isinstance(computations, list):
        for entry in computations:
            if not isinstance(entry, dict):
                continue
            exprs = entry.get("exprs")
            if not isinstance(exprs, dict):
                continue
            for lhs, rhs in exprs.items():
                if not isinstance(lhs, str) or not isinstance(rhs, str):
                    continue
                # Reuse parse_expr_hint by reassembling `lhs = rhs` so RHS
                # tokenization rules (keyword filter, string-literal strip)
                # are uniform with the per-file source.
                parsed = parse_expr_hint(f"{lhs} = {rhs}")
                if parsed is None:
                    continue
                _, _, tokens = parsed
                _record(lhs, tokens)

    for doc in per_file.values():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            comps = section.get("computations") or []
            if not isinstance(comps, list):
                continue
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                parsed = parse_expr_hint(comp.get("expr_hint", ""))
                if parsed is None:
                    continue
                lhs, _, tokens = parsed
                _record(lhs, tokens)

    return dep, rdep


# ---------------------------------------------------------------------------
# Heuristic 1: reuse_across_entities
# ---------------------------------------------------------------------------

def _detect_reuse_across_entities(ctx: DomainContext) -> list[Candidate]:
    """H1a + H1b combined.

    H1a: 2+ entities in naming-manifest.inputs share one or more field
         names. Module is named after the shared field (snake_case).
    H1b: 2+ variables in skeleton.computations[].variables share a
         common suffix, where each variable's name = `<entity_prefix>_<suffix>`
         and the prefix derives from an entity in naming-manifest.

    Both contribute candidates; the dedup pass collapses overlap between
    them. Emission order: 1a first (since 1a is the "expensive miss"
    signal — see plan), then 1b.
    """
    out: list[Candidate] = []
    out.extend(_detect_h1a_mirrored_inputs(ctx))
    out.extend(_detect_h1b_prefix_variables(ctx))
    return out


def _detect_h1a_mirrored_inputs(ctx: DomainContext) -> list[Candidate]:
    """Surface field names that appear on >=2 entities in naming-manifest."""
    inputs = ctx.naming_manifest.get("inputs") if isinstance(ctx.naming_manifest, dict) else None
    if not isinstance(inputs, dict):
        return []
    field_to_entities: dict[str, list[str]] = {}
    for entity_name, fields in inputs.items():
        if not isinstance(fields, dict):
            continue
        for field_name in fields.keys():
            if not isinstance(field_name, str):
                continue
            field_to_entities.setdefault(field_name, []).append(entity_name)

    out: list[Candidate] = []
    for field_name, entities in field_to_entities.items():
        if len(entities) < _MIN_SIZE["reuse_across_entities"]:
            continue
        # Variable list is the shared field name (one logical variable bound
        # to multiple entities). Stages for naming-manifest fields are not
        # tracked in skeleton.computations, so the stage set stays empty —
        # R21 falls through to "no stage constraint" for this candidate.
        out.append(
            Candidate(
                name=field_name,
                rationale="reuse_across_entities",
                variables=[field_name],
                bound_entities=sorted(set(entities)),
                description=f"Shared computation: {field_name}",
                stages=set(),
            )
        )
    return out


def _detect_h1b_prefix_variables(ctx: DomainContext) -> list[Candidate]:
    """Surface variable groups of form `<entity_prefix>_<suffix>` where >=2
    entity prefixes share one suffix and each prefix maps to a distinct
    naming-manifest entity (case-insensitive match against entity name)."""
    entity_prefixes = _entity_prefix_set(ctx)
    if len(entity_prefixes) < 2:
        return []

    # Sort longest-first so `client_statement_` is tried before `client_`.
    prefix_list = sorted(entity_prefixes.keys(), key=len, reverse=True)

    # For each variable, record (suffix, matched_prefix, entity_name).
    suffix_to_matches: dict[str, list[tuple[str, str, str]]] = {}
    for var in ctx.skeleton_intermediate_vars:
        for prefix in prefix_list:
            if var.startswith(prefix) and len(var) > len(prefix):
                suffix = var[len(prefix):]
                entity = entity_prefixes[prefix]
                suffix_to_matches.setdefault(suffix, []).append((var, prefix, entity))
                break

    out: list[Candidate] = []
    for suffix, matches in suffix_to_matches.items():
        # Require >=2 distinct entities (not just 2 vars under the same prefix).
        distinct_entities = {entity for _, _, entity in matches}
        if len(distinct_entities) < _MIN_SIZE["reuse_across_entities"]:
            continue
        vars_in_group = [m[0] for m in matches]
        stages = {
            s for v in vars_in_group
            if (s := ctx.var_to_stage.get(v)) is not None
        }
        out.append(
            Candidate(
                name=suffix,
                rationale="reuse_across_entities",
                variables=vars_in_group,
                bound_entities=sorted(distinct_entities),
                description=f"Shared computation: {suffix}",
                stages=stages,
            )
        )
    return out


def _entity_prefix_set(ctx: DomainContext) -> dict[str, str]:
    """Map snake_case entity prefix → CamelCase entity name.

    Derives multiple prefix candidates per entity to cover the common
    naming patterns:
      - `ClientStatement` → `client_statement_` AND `client_`
      - `DOLRecord` → `dol_record_` AND `dol_`
      - `Applicant` → `applicant_`
    """
    inputs = ctx.naming_manifest.get("inputs") if isinstance(ctx.naming_manifest, dict) else None
    if not isinstance(inputs, dict):
        return {}
    out: dict[str, str] = {}
    for entity_name in inputs.keys():
        if not isinstance(entity_name, str) or not entity_name:
            continue
        full = _camel_to_snake(entity_name) + "_"
        out.setdefault(full, entity_name)
        # First-word-only prefix (e.g., ClientStatement → client_,
        # DOLRecord → dol_) covers the common pattern where variables
        # don't include the full entity name. Acronym handling: a run of
        # >=2 uppercase letters followed by a Title-case word splits at
        # the boundary (DOL+Record, not DOLR+ecord).
        first_word_match = re.match(
            r"^([A-Z]+(?=[A-Z][a-z])|[A-Z][a-z]+|[A-Z]+)", entity_name
        )
        if first_word_match:
            short = first_word_match.group(1).lower() + "_"
            # Don't overwrite a longer prefix with a shorter one if the
            # longer one is already populated.
            out.setdefault(short, entity_name)
    return out


def _camel_to_snake(name: str) -> str:
    """Convert `CamelCase` / `CAMELCase` to `camel_case`."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


# ---------------------------------------------------------------------------
# Heuristic 2: policy_structure
# ---------------------------------------------------------------------------

def _detect_policy_structure(ctx: DomainContext) -> list[Candidate]:
    """Section headings that cover >=3 skeleton intermediate variables.

    A section's variable inventory comes from `expr_hint` LHSes
    (each computation's output). When the inventory's intersection with
    `skeleton.intermediate_vars` reaches the threshold, emit a candidate
    named after the section heading.
    """
    skeleton_var_set = set(ctx.skeleton_intermediate_vars)
    out: list[Candidate] = []

    for rel, doc in ctx.per_file.items():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = section.get("heading")
            if not isinstance(heading, str) or not heading:
                continue
            comps = section.get("computations") or []
            if not isinstance(comps, list):
                continue
            inventory: list[str] = []
            inventory_seen: set[str] = set()
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                parsed = parse_expr_hint(comp.get("expr_hint", ""))
                if parsed is None:
                    continue
                lhs = parsed[0]
                if lhs in skeleton_var_set and lhs not in inventory_seen:
                    inventory_seen.add(lhs)
                    inventory.append(lhs)
            if len(inventory) < _MIN_SIZE["policy_structure"]:
                continue
            stage_norm = normalize_stage(section.get("stage"))
            stages = {stage_norm} if stage_norm else set()
            for v in inventory:
                s = ctx.var_to_stage.get(v)
                if s is not None:
                    stages.add(s)
            entities = _entities_for_vars(ctx, inventory)
            module_name = _snakeify_heading(heading)
            out.append(
                Candidate(
                    name=module_name,
                    rationale="policy_structure",
                    variables=inventory,
                    bound_entities=entities,
                    description=f"Section: {heading}",
                    stages=stages,
                )
            )
    return out


def _snakeify_heading(heading: str) -> str:
    """Reduce a section heading to a snake_case identifier-shape.

    Strips markdown `#` prefixes, leading section numbering (`### 523 B. `),
    lowercases, and collapses non-alphanumeric runs to underscores.
    """
    s = heading.strip().lstrip("#").strip()
    # Strip leading section-numbering prefix like `523 B.` or `523 B. SECTION`
    s = re.sub(r"^[0-9]+\s*[A-Z]?\.?\s*", "", s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s:
        return "section"
    return s


# ---------------------------------------------------------------------------
# Heuristic 3: sequential_chain (per-file only)
# ---------------------------------------------------------------------------

def _detect_sequential_chain(ctx: DomainContext) -> list[Candidate]:
    """Per-file LHS-of-one / RHS-of-next chains of >=3 within a single stage.

    Strict per-file: source-order is the per-file YAML's `sections[*]` list
    flattened to `sections[*].computations[*]`. Chains do not cross files.
    Within a file, chains do not cross sections that have different
    (post-normalization) `stage:` values. Two parallel chains converging
    at a node ("Y-junction"): pick the longest spine; on length-tie, pick
    the spine whose head appears first in source order.
    """
    out: list[Candidate] = []
    for rel, doc in ctx.per_file.items():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        # Build per-file LHS-of-one-step → next-LHS map, partitioned by stage.
        stage_chains: dict[Optional[str], list[tuple[str, list[str]]]] = {}
        for section in sections:
            if not isinstance(section, dict):
                continue
            stage_norm = normalize_stage(section.get("stage"))
            comps = section.get("computations") or []
            if not isinstance(comps, list):
                continue
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                parsed = parse_expr_hint(comp.get("expr_hint", ""))
                if parsed is None:
                    continue
                lhs, _, tokens = parsed
                stage_chains.setdefault(stage_norm, []).append((lhs, list(tokens)))

        for stage, entries in stage_chains.items():
            chains = _longest_chains(entries)
            for chain in chains:
                if len(chain) < _MIN_SIZE["sequential_chain"]:
                    continue
                stages = {stage} if stage else set()
                entities = _entities_for_vars(ctx, chain)
                out.append(
                    Candidate(
                        name=f"{chain[-1]}_chain",
                        rationale="sequential_chain",
                        variables=chain,
                        bound_entities=entities,
                        description=f"Sequential chain ending at {chain[-1]}",
                        stages=stages,
                    )
                )
    return out


def _longest_chains(entries: list[tuple[str, list[str]]]) -> list[list[str]]:
    """Extract maximal linear chains from a sequence of (lhs, rhs-tokens).

    Builds a directed edge from each entry's LHS to every subsequent
    entry's LHS where the prior LHS appears in the subsequent's RHS
    tokens. Returns the longest source-order path for each unique
    spine-tail (Y-junction tiebreak: keep the longest path; on tie keep
    the earliest-starting one).
    """
    if not entries:
        return []
    lhs_index: dict[str, list[int]] = {}
    for i, (lhs, _) in enumerate(entries):
        lhs_index.setdefault(lhs, []).append(i)

    # Predecessor map: for index i, list of indices j<i whose LHS appears in
    # entries[i].rhs_tokens.
    preds: dict[int, list[int]] = {}
    for i, (_, rhs) in enumerate(entries):
        seen_preds: set[int] = set()
        for tok in rhs:
            for j in lhs_index.get(tok, []):
                if j < i:
                    seen_preds.add(j)
        if seen_preds:
            preds[i] = sorted(seen_preds)

    # Dynamic-program longest path ending at each node. `parent[i]` is the
    # predecessor giving the longest length (earliest tie-breaker).
    length: dict[int, int] = {i: 1 for i in range(len(entries))}
    parent: dict[int, int] = {}
    for i in range(len(entries)):
        best_parent: Optional[int] = None
        best_len = 0
        for j in preds.get(i, []):
            if length[j] > best_len:
                best_len = length[j]
                best_parent = j
        if best_parent is not None:
            length[i] = best_len + 1
            parent[i] = best_parent

    # Find maximal paths: nodes that are not predecessors of any longer path
    # AND have length >= min size. We collect one chain per "tail" node, and
    # then drop any chain entirely contained in another.
    nodes_sorted_by_length = sorted(
        length.items(), key=lambda kv: (-kv[1], kv[0])
    )
    used_nodes: set[int] = set()
    chains: list[list[int]] = []
    for tail, _ in nodes_sorted_by_length:
        if length[tail] < 1:
            continue
        # Reconstruct chain from tail back to root.
        path: list[int] = []
        cur: Optional[int] = tail
        while cur is not None:
            path.append(cur)
            cur = parent.get(cur)
        path.reverse()
        if any(n in used_nodes for n in path):
            continue
        used_nodes.update(path)
        chains.append(path)
    # Return chains as ordered LHS lists.
    return [[entries[i][0] for i in c] for c in chains]


# ---------------------------------------------------------------------------
# Heuristic 4: depth_threshold
# ---------------------------------------------------------------------------

def _detect_depth_threshold(ctx: DomainContext) -> list[Candidate]:
    """>=5 skeleton intermediate variables sharing a sequential-dependence
    name prefix (after_, net_, gross_, total_, final_, adjusted_)."""
    out: list[Candidate] = []
    for prefix in _DEPTH_PREFIXES:
        matches = [v for v in ctx.skeleton_intermediate_vars if v.startswith(prefix)]
        if len(matches) < _MIN_SIZE["depth_threshold"]:
            continue
        stages = {
            s for v in matches if (s := ctx.var_to_stage.get(v)) is not None
        }
        entities = _entities_for_vars(ctx, matches)
        out.append(
            Candidate(
                name=f"{prefix.rstrip('_')}_chain",
                rationale="depth_threshold",
                variables=matches,
                bound_entities=entities,
                description=f"Depth chain: {prefix}* variables",
                stages=stages,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Heuristic 5: variable_coupling
# ---------------------------------------------------------------------------

def _detect_variable_coupling(ctx: DomainContext) -> list[Candidate]:
    """>=3 variables forming a clique where each references >=2 of the
    others' outputs.

    Operates on the undirected reachability graph derived from
    `ctx.var_dep_graph`. Enumerates 3-cliques among skeleton intermediate
    variables; >=3-cliques expand to the maximum connected clique.
    """
    intermediate_set = set(ctx.skeleton_intermediate_vars)
    # Undirected adjacency limited to skeleton intermediates.
    adj: dict[str, set[str]] = {v: set() for v in ctx.skeleton_intermediate_vars}
    for v in ctx.skeleton_intermediate_vars:
        for dep in ctx.var_dep_graph.get(v, set()):
            if dep in intermediate_set and dep != v:
                adj[v].add(dep)
                adj[dep].add(v)

    # Enumerate maximal 3+ cliques. For an O(N^3) brute force on a
    # graph of intermediate-variable size (tens to low hundreds), this is
    # fine.
    nodes = list(ctx.skeleton_intermediate_vars)
    triangles: list[set[str]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if b not in adj[a]:
                continue
            for k in range(j + 1, len(nodes)):
                c = nodes[k]
                if c in adj[a] and c in adj[b]:
                    triangles.append({a, b, c})

    # Merge triangles that share >=2 nodes into larger cliques.
    cliques: list[set[str]] = []
    for tri in triangles:
        merged = False
        for clique in cliques:
            if len(clique & tri) >= 2 and all(
                v in adj[u] or v == u for u in clique for v in tri
            ):
                clique |= tri
                merged = True
                break
        if not merged:
            cliques.append(set(tri))

    out: list[Candidate] = []
    counter = 0
    for clique in cliques:
        if len(clique) < _MIN_SIZE["variable_coupling"]:
            continue
        counter += 1
        ordered = [v for v in ctx.skeleton_intermediate_vars if v in clique]
        stages = {
            s for v in ordered if (s := ctx.var_to_stage.get(v)) is not None
        }
        entities = _entities_for_vars(ctx, ordered)
        out.append(
            Candidate(
                name=f"cluster_{counter}",
                rationale="variable_coupling",
                variables=ordered,
                bound_entities=entities,
                description=f"Coupled variable cluster: {', '.join(ordered)}",
                stages=stages,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Heuristic 6: shared_gate
# ---------------------------------------------------------------------------

def _detect_shared_gate(ctx: DomainContext) -> list[Candidate]:
    """Two passes:
      (a) >=3 vars with a common guard-variable prefix.
      (b) >=3 computations whose preconditions reference the same
          (whitespace-normalized) clause.
    """
    out: list[Candidate] = []
    out.extend(_detect_shared_gate_prefix(ctx))
    out.extend(_detect_shared_gate_preconditions(ctx))
    return out


def _detect_shared_gate_prefix(ctx: DomainContext) -> list[Candidate]:
    """Pass 6a — guard-prefix scan over skeleton intermediates."""
    out: list[Candidate] = []
    for prefix in _GATE_PREFIXES:
        matches = [v for v in ctx.skeleton_intermediate_vars if v.startswith(prefix)]
        if len(matches) < _MIN_SIZE["shared_gate"]:
            continue
        stages = {
            s for v in matches if (s := ctx.var_to_stage.get(v)) is not None
        }
        entities = _entities_for_vars(ctx, matches)
        out.append(
            Candidate(
                name=f"{prefix.rstrip('_')}_cluster",
                rationale="shared_gate",
                variables=matches,
                bound_entities=entities,
                description=f"Co-activation cluster: {prefix}* variables",
                stages=stages,
            )
        )
    return out


def _detect_shared_gate_preconditions(ctx: DomainContext) -> list[Candidate]:
    """Pass 6b — preconditions clause clustering.

    For each computation in any per-file section, collect every clause
    appearing in `preconditions:` (as a flat string clause; `any_of:` and
    `all_of:` containers expand to their leaf strings). Whitespace
    normalize each clause to a single canonical key. When >=3 distinct
    computation outputs share the same key, emit a candidate.
    """
    clause_to_vars: dict[str, list[str]] = {}
    for doc in ctx.per_file.values():
        sections = doc.get("sections") if isinstance(doc, dict) else None
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict):
                continue
            comps = section.get("computations") or []
            if not isinstance(comps, list):
                continue
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                parsed = parse_expr_hint(comp.get("expr_hint", ""))
                if parsed is None:
                    continue
                lhs = parsed[0]
                clauses = _flatten_preconditions(comp.get("preconditions"))
                for clause in clauses:
                    key = _normalize_clause(clause)
                    if not key:
                        continue
                    bucket = clause_to_vars.setdefault(key, [])
                    if lhs not in bucket:
                        bucket.append(lhs)

    out: list[Candidate] = []
    for key, vars_in_clause in clause_to_vars.items():
        if len(vars_in_clause) < _MIN_SIZE["shared_gate"]:
            continue
        stages = {
            s for v in vars_in_clause
            if (s := ctx.var_to_stage.get(v)) is not None
        }
        entities = _entities_for_vars(ctx, vars_in_clause)
        # Derive module name from the first content word of the clause.
        first_word = next((w for w in re.split(r"[^a-zA-Z0-9_]+", key) if w), "clause")
        module_name = f"{first_word.lower()}_cluster"
        out.append(
            Candidate(
                name=module_name,
                rationale="shared_gate",
                variables=vars_in_clause,
                bound_entities=entities,
                description=f"Co-activation cluster: shared precondition '{key}'",
                stages=stages,
            )
        )
    return out


def _flatten_preconditions(node: Any) -> list[str]:
    """Walk a preconditions structure and return every leaf string clause.

    Handles:
      - a bare list of strings
      - a list of dicts with `any_of:` / `all_of:` containers
      - nested combinations of the above
    """
    out: list[str] = []

    def _walk(n: Any) -> None:
        if isinstance(n, str):
            out.append(n)
        elif isinstance(n, list):
            for item in n:
                _walk(item)
        elif isinstance(n, dict):
            for v in n.values():
                _walk(v)

    _walk(node)
    return out


def _normalize_clause(clause: str) -> str:
    """Collapse internal whitespace to a single space and strip."""
    return " ".join(clause.split())


# ---------------------------------------------------------------------------
# Bound-entities lookup
# ---------------------------------------------------------------------------

def _entities_for_vars(ctx: DomainContext, vars_: list[str]) -> list[str]:
    """Best-effort owning-entity list for a non-reuse candidate.

    Looks up each variable in naming-manifest.inputs by stripping any
    known entity-prefix and consulting `var_to_entity`. When a variable
    has no manifest entry (intermediate computed variable), it
    contributes no entity. Returns the sorted unique entity list.

    For naming-manifest fields owned by multiple entities ("__SHARED__"
    marker), every owning entity is added.
    """
    inputs = ctx.naming_manifest.get("inputs") if isinstance(ctx.naming_manifest, dict) else None
    out: set[str] = set()

    # Pre-build a per-field full owners map so __SHARED__ vars can expand.
    owners_map: dict[str, list[str]] = {}
    if isinstance(inputs, dict):
        for entity_name, fields in inputs.items():
            if not isinstance(fields, dict):
                continue
            for field_name in fields.keys():
                if not isinstance(field_name, str):
                    continue
                owners_map.setdefault(field_name, []).append(entity_name)

    entity_prefixes = _entity_prefix_set(ctx)
    prefix_list = sorted(entity_prefixes.keys(), key=len, reverse=True)

    for v in vars_:
        # Direct field name match (multi-entity friend handled below).
        if v in owners_map:
            for owner in owners_map[v]:
                out.add(owner)
            continue
        # Strip a known entity prefix; the field after the prefix may be
        # owned by that entity.
        for prefix in prefix_list:
            if v.startswith(prefix) and len(v) > len(prefix):
                suffix = v[len(prefix):]
                owners = owners_map.get(suffix, [])
                if entity_prefixes[prefix] in owners:
                    out.add(entity_prefixes[prefix])
                    break
                if owners:
                    for owner in owners:
                        out.add(owner)
                    break
                # Even when the suffix isn't in the manifest, the prefix
                # is a strong-enough hint to record the entity.
                out.add(entity_prefixes[prefix])
                break

    return sorted(out)


# ---------------------------------------------------------------------------
# Priority dedup + R21 stage-boundary
# ---------------------------------------------------------------------------

def _dedup_by_priority(candidates: list[Candidate]) -> list[Candidate]:
    """Suppress lower-priority candidates with Jaccard overlap >=0.5
    against any higher-priority claimed variable set.

    Candidates are processed in `_HEURISTIC_PRIORITY` order. Within a
    single heuristic, every candidate is kept (no intra-heuristic
    suppression — the heuristic is its own priority bucket).
    """
    by_rationale: dict[str, list[Candidate]] = {h: [] for h in _HEURISTIC_PRIORITY}
    for c in candidates:
        if c.rationale in by_rationale:
            by_rationale[c.rationale].append(c)

    claimed_sets: list[set[str]] = []
    kept: list[Candidate] = []
    for h in _HEURISTIC_PRIORITY:
        for cand in by_rationale[h]:
            var_set = set(cand.variables)
            if _has_high_overlap(var_set, claimed_sets):
                continue
            kept.append(cand)
        # After processing the bucket, every kept candidate of this
        # heuristic contributes its variable set to the claimed pool.
        for cand in kept:
            if cand.rationale != h:
                continue
            claimed_sets.append(set(cand.variables))
    return kept


def _has_high_overlap(s: set[str], pool: list[set[str]]) -> bool:
    """Return True when `s` has Jaccard >= _JACCARD_SUPPRESS against any
    set in `pool`."""
    if not s:
        return False
    for other in pool:
        if not other:
            continue
        denom = len(s | other)
        if denom == 0:
            continue
        if len(s & other) / denom >= _JACCARD_SUPPRESS:
            return True
    return False


def _apply_r21(
    candidates: list[Candidate],
    ctx: DomainContext,
) -> tuple[list[Candidate], list[dict[str, str]]]:
    """Enforce R21 stage-boundary.

    For each candidate whose vars carry >=2 distinct stages, attempt to
    split per stage. Each per-stage sub-candidate is kept only when it
    still satisfies the originating heuristic's minimum size; otherwise
    the original candidate is dropped with a warning record returned in
    the second tuple element.

    Candidates without populated `stages` (no `stage:` field anywhere
    they touched) fall through unchanged.
    """
    out: list[Candidate] = []
    dropped: list[dict[str, str]] = []

    for cand in candidates:
        # The candidate's `stages` is the union of stages observed on its
        # variables. R21 only fires when >=2 distinct stages are present
        # AND every variable in the candidate has a populated stage
        # (mixed populated/unpopulated falls through to single-stage
        # treatment per the plan).
        per_var_stages = [ctx.var_to_stage.get(v) for v in cand.variables]
        populated = [s for s in per_var_stages if s is not None]
        all_populated = len(populated) == len(cand.variables)
        distinct = set(populated)
        if len(distinct) < 2 or not all_populated:
            out.append(cand)
            continue
        # Try to split.
        min_size = _MIN_SIZE.get(cand.rationale, 1)
        sub_candidates: list[Candidate] = []
        for stage in sorted(distinct):
            sub_vars = [
                v for v in cand.variables
                if ctx.var_to_stage.get(v) == stage
            ]
            if len(sub_vars) < min_size:
                continue
            sub_entities = _entities_for_vars(ctx, sub_vars) if cand.rationale != "reuse_across_entities" else cand.bound_entities
            sub_candidates.append(
                Candidate(
                    name=f"{cand.name}__{stage}",
                    rationale=cand.rationale,
                    variables=sub_vars,
                    bound_entities=sub_entities,
                    description=f"{cand.description} (stage: {stage})",
                    stages={stage},
                )
            )
        if not sub_candidates:
            dropped.append({
                "name": cand.name,
                "reason": (
                    f"spans stages [{', '.join(sorted(distinct))}] "
                    f"and cannot be split"
                ),
            })
            continue
        out.extend(sub_candidates)
    return out, dropped


# ---------------------------------------------------------------------------
# Main-module-name derivation
# ---------------------------------------------------------------------------

def _derive_main_module_name(
    ctx: DomainContext,
    cli_override: Optional[str],
) -> tuple[Optional[str], bool]:
    """Return (main_module_name | None, primary_output_present_bool).

    Precedence: explicit CLI override > output-variables.yaml primary entry.
    When neither is present, returns (None, False) — the skill prompts the
    analyst and re-invokes with --main-module-name.
    """
    if cli_override:
        return cli_override, True
    ov = ctx.output_variables if isinstance(ctx.output_variables, dict) else {}
    primary_key: Optional[str] = None
    for key, entry in ov.items():
        if isinstance(entry, dict) and entry.get("primary") is True:
            primary_key = str(key)
            break
    if not primary_key:
        return None, False
    name = primary_key
    for suffix in _PRIMARY_OUTPUT_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name, True


# ---------------------------------------------------------------------------
# Merge with existing + write
# ---------------------------------------------------------------------------

def _merge_with_existing(
    existing: list[dict],
    new_candidates: list[Candidate],
    main_module: Optional[dict],
) -> list[dict]:
    """UPDATE-mode merge.

    - Every existing entry is preserved verbatim (in original order).
    - New candidates are appended last in detection order, with naming
      collisions auto-disambiguated (`<name>_<2>`, `<name>_<3>`).
    - When a `role: main` entry already exists, the new main_module
      argument is dropped (preserve existing main).
    """
    out: list[dict] = list(existing)
    existing_names = {m.get("name") for m in existing if isinstance(m, dict)}

    has_existing_main = any(
        isinstance(m, dict) and m.get("role") == "main" for m in existing
    )

    for cand in new_candidates:
        if cand.name in existing_names:
            # Disambiguate with a numeric suffix.
            n = 2
            while f"{cand.name}_{n}" in existing_names:
                n += 1
            cand.name = f"{cand.name}_{n}"
        existing_names.add(cand.name)
        out.append(_candidate_to_entry(cand))

    if main_module is not None and not has_existing_main:
        # Avoid duplicate name with an existing entry (rare).
        m_name = main_module.get("name", "")
        if m_name in existing_names:
            n = 2
            while f"{m_name}_{n}" in existing_names:
                n += 1
            main_module = dict(main_module)
            main_module["name"] = f"{m_name}_{n}"
        out.append(main_module)

    return out


def _candidate_to_entry(cand: Candidate) -> dict[str, Any]:
    """Serialize a sub-module Candidate to a YAML-emit-ready dict."""
    return {
        "name": cand.name,
        "description": cand.description,
        "bound_entities": list(cand.bound_entities),
        "rationale": cand.rationale,
        "depends_on": [],
    }


def _build_main_module_entry(
    ctx: DomainContext,
    main_name: Optional[str],
    sub_module_names: list[str],
) -> Optional[dict[str, Any]]:
    """Build the role:main entry when sub-modules exist and a main name
    is known."""
    if not main_name or not sub_module_names:
        return None
    display_name = ""
    if isinstance(ctx.metadata, dict):
        dn = ctx.metadata.get("display_name")
        if isinstance(dn, str):
            display_name = dn
    return {
        "name": main_name,
        "description": display_name,
        "bound_entities": [],
        "rationale": "main_module",
        "role": "main",
        "depends_on": list(sub_module_names),
    }


def _serialize(modules: list[dict]) -> str:
    """Emit `ruleset_modules:` at the top, then each module in order."""
    doc = {"ruleset_modules": modules}
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
# Stdout / header
# ---------------------------------------------------------------------------

def _format_table(modules: list[dict]) -> str:
    """Human-readable summary table the skill relays in :::detail."""
    header = (
        "Ruleset Modules\n"
        "─────────────────────────────────────────────────────────────────────────\n"
        "  # │ Name              │ Role │ Bound Entities          │ Heuristic"
    )
    if not modules:
        return (
            header
            + "\n  (none)\n"
            + "─────────────────────────────────────────────────────────────────────────"
        )
    lines = [header]
    for i, m in enumerate(modules, 1):
        name = str(m.get("name", ""))
        role = str(m.get("role", "sub"))
        bound = ", ".join(m.get("bound_entities") or []) or "—"
        rationale = str(m.get("rationale", ""))
        lines.append(
            f"  {i} │ {name:<17} │ {role:<4} │ {bound:<23} │ {rationale}"
        )
    lines.append(
        "─────────────────────────────────────────────────────────────────────────"
    )
    return "\n".join(lines)


def _format_header(
    main_module_name: Optional[str],
    primary_output_present: bool,
    cross_source_recommended: bool,
    subm_count: int,
    dropped: list[dict[str, str]],
) -> str:
    return json.dumps({
        "main_module_name": main_module_name,
        "primary_output_present": primary_output_present,
        "cross_source_language_scan_recommended": cross_source_recommended,
        "subm_count": subm_count,
        "dropped_candidates": dropped,
    })


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------

def _detect_all(ctx: DomainContext) -> list[Candidate]:
    """Run every heuristic and return their concatenated candidate list."""
    candidates: list[Candidate] = []
    candidates.extend(_detect_reuse_across_entities(ctx))
    candidates.extend(_detect_policy_structure(ctx))
    candidates.extend(_detect_sequential_chain(ctx))
    candidates.extend(_detect_depth_threshold(ctx))
    candidates.extend(_detect_variable_coupling(ctx))
    candidates.extend(_detect_shared_gate(ctx))
    return candidates


def _h1_found_anything(candidates: list[Candidate]) -> bool:
    return any(c.rationale == "reuse_across_entities" for c in candidates)


def _domain_entity_count(ctx: DomainContext) -> int:
    inputs = ctx.naming_manifest.get("inputs") if isinstance(ctx.naming_manifest, dict) else None
    if not isinstance(inputs, dict):
        return 0
    return len(inputs)


def run(domain_dir: Path, main_module_override: Optional[str]) -> int:
    """Top-level orchestration. Returns the process exit code."""
    preflight_err = _preflight(domain_dir)
    if preflight_err is not None:
        print(preflight_err, file=sys.stderr)
        return 2

    ctx = _build_context(domain_dir)

    raw_candidates = _detect_all(ctx)
    deduped = _dedup_by_priority(raw_candidates)
    finalized, dropped = _apply_r21(deduped, ctx)

    cross_source_recommended = (
        not _h1_found_anything(raw_candidates)
        and _domain_entity_count(ctx) >= 2
    )

    main_module_name, primary_present = _derive_main_module_name(
        ctx, main_module_override
    )

    sub_module_names = [c.name for c in finalized]

    # When CREATE mode (no existing modules) and we know main name AND
    # have sub-modules, append main entry; else skip and the skill
    # handles the prompt+re-invoke.
    main_entry: Optional[dict[str, Any]] = None
    if not ctx.existing_modules:
        main_entry = _build_main_module_entry(
            ctx, main_module_name, sub_module_names
        )

    new_entries = [_candidate_to_entry(c) for c in finalized]
    final_modules = _merge_with_existing(
        ctx.existing_modules, finalized, main_entry
    )

    # Write
    dest = domain_dir / _RULESET_MODULES_REL
    new_content = _serialize(final_modules)
    _atomic_write(dest, new_content)

    # Emit stdout
    header_json = _format_header(
        main_module_name=main_module_name,
        primary_output_present=primary_present,
        cross_source_recommended=cross_source_recommended,
        subm_count=len(finalized),
        dropped=dropped,
    )
    table = _format_table(final_modules)
    print(header_json)
    print(_HEADER_SENTINEL)
    print(table)

    for d in dropped:
        print(
            f"WARN: candidate {d['name']} {d['reason']} — dropped",
            file=sys.stderr,
        )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect ruleset modules for a domain by running six "
            "deterministic heuristics over the skeleton, ruleset-groups, "
            "per-file computations, naming-manifest, and output-variables. "
            "Writes specs/guidance/ruleset-modules.yaml."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "--main-module-name",
        dest="main_module_name",
        default=None,
        help=(
            "Override the derived main-module name. Used by the skill when "
            "no output-variables.yaml primary entry is declared and the "
            "analyst supplied a name interactively."
        ),
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    return run(domain_dir, args.main_module_name)


if __name__ == "__main__":
    sys.exit(main())
