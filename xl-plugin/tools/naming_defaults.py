#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator naming-defaults: deterministic merge of per-file naming_manifest blocks
into <domain>/policy_facets/naming-defaults.yaml, with auto-picked canonical
names, recorded synonyms, per-file variables: rewrites to canonical, and a
canonical-churn report against the prior file.

This is the merge half of the naming-defaults pipeline (U4). The AI half lives
in /extract-computations (U2), which emits per-file naming_manifest blocks.
This tool is purely arithmetic over those blocks plus optional specs/ authority.

Usage:
    xlator naming-defaults <domain> --build
    xlator naming-defaults <domain> --build --dry-run

--build:
  - Reads optional <domain>/specs/naming-manifest.yaml as authority overrides.
  - Globs <domain>/policy_facets/computations/**/*.md.yaml.
  - For each (file, variable_name, policy_phrase, role_hint?) tuple, groups by
    normalize(policy_phrase) and picks a canonical name per the rules below.
  - Writes <domain>/policy_facets/naming-defaults.yaml atomically.
  - Rewrites each per-file file's sections[*].computations[*].variables list
    when canonicalization changes a name, in place atomically.

--dry-run:
  - Computes the plan but writes nothing. Useful for previewing churn.

Output (JSON on stdout):
    {
      "merged":              <N>,
      "synonyms_collapsed":  <M>,
      "files_rewritten":     <K>,
      "canonicals_changed":  [{"phrase": "...", "from": "...", "to": "..."}, ...],
      "errors":              [<warning strings>]
    }

Exit codes:
    0 — success (warnings on stderr; partial-source files don't fail the run)
    1 — fatal error (missing domain, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import string
import sys
from pathlib import Path

import yaml


_POLICY_FACETS = "policy_facets"
_COMPUTATIONS = "policy_facets/computations"
_DEFAULTS = "policy_facets/naming-defaults.yaml"
_SPECS = "specs/naming-manifest.yaml"

_ROLE_RANK = {"computed": 3, "output": 2, "input": 1}
_TYPE_VOCABULARY = frozenset({
    "money", "bool", "int", "float", "string", "enum", "list", "date",
})
_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_PUNCT_TABLE = str.maketrans({c: " " for c in string.punctuation})


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


def _set_in_order(d: dict, key: str, value, before: tuple[str, ...]) -> None:
    """Insert/replace `key=value` in dict `d` while keeping `key` ordered before
    any of the keys in `before` that are already present. Preserves the
    insertion-order convention used for naming-defaults.yaml output entries.
    """
    if key in d:
        d[key] = value
        return
    # Build a new dict with `key` inserted in the right position.
    new = {}
    inserted = False
    for k, v in d.items():
        if not inserted and k in before:
            new[key] = value
            inserted = True
        new[k] = v
    if not inserted:
        new[key] = value
    d.clear()
    d.update(new)


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Used by the convergence-warning surface to detect seeded names that are
    similar (but not identical) to auto-picked canonicals.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Standard DP table; small strings (variable names), O(len(a)*len(b)) is fine.
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost, # substitution
            )
        prev = curr
    return prev[-1]


def normalize_phrase(phrase: str) -> str:
    """Canonicalize a policy phrase for synonym grouping.

    Steps: lowercase → strip leading articles (a/an/the) → strip ASCII
    punctuation → collapse internal whitespace runs → strip.

    Stable across re-runs: this is the group key for the merge tool's synonym
    detection, and worker authority-chain matching uses the same function.
    """
    if not isinstance(phrase, str):
        return ""
    s = phrase.lower()
    # Strip leading articles repeatedly in case multiple were prepended.
    while True:
        match = _ARTICLE_RE.match(s)
        if not match:
            break
        s = s[match.end():]
    s = s.translate(_PUNCT_TABLE)
    s = " ".join(s.split())
    return s.strip()


# ---------------------------------------------------------------------------
# Specs flattening
# ---------------------------------------------------------------------------


def _flatten_specs(
    specs: dict,
    per_file_relpaths: set[str],
    errors: list[str],
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Walk inputs.<Entity>.<field>, computed.<name>, outputs.<name> and produce
    two maps:
      * `specs_lookup` — phrase-keyed `{normalized_phrase → {name, role_hint?,
        source_doc, section, _entity?, description?, type?, values?}}` for
        entries that carry a `policy_phrase` (collision-resolved per the
        existing tiebreak rules).
      * `seeded_unobserved` — name-keyed `{name → payload}` for phraseless
        seeded entries — entries the analyst declared via /declare-target-ruleset
        before extraction surfaced any observation. These are routed through
        pass 2 of the merge in cmd_build.

    On phrase collision, prefer entries whose source_doc is referenced by a
    per-file file in the glob; deterministic alphabetical-by-entity tiebreak
    after that.
    """
    if not isinstance(specs, dict):
        errors.append("specs/naming-manifest.yaml: not a map; ignoring")
        return {}, {}

    entries: list[dict] = []
    seeded_unobserved: dict[str, dict] = {}

    def _classify(name: str, payload: dict, entity: str) -> None:
        """Route a payload to either `entries` (phrased) or `seeded_unobserved`
        (phraseless seeded)."""
        phrase = payload.get("policy_phrase", "")
        common = {
            "name": name,
            "role_hint": payload.get("role_hint"),
            "source_doc": payload.get("source_doc"),
            "section": payload.get("section"),
            "_entity": entity,
            "description": payload.get("description"),
            "type": payload.get("type"),
            "values": payload.get("values"),
        }
        if isinstance(phrase, str) and phrase:
            entries.append({**common, "policy_phrase": phrase})
        else:
            # Phraseless seeded entry. Last-write-wins on duplicate names
            # (rare; specs schema has each name unique within its block).
            seeded_unobserved[name] = {**common, "policy_phrase": ""}

    inputs = specs.get("inputs") or {}
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if not isinstance(fields, dict):
                continue
            for field_name, payload in fields.items():
                if not isinstance(payload, dict):
                    continue
                _classify(str(field_name), payload, str(entity))

    for top_key in ("computed", "outputs"):
        block = specs.get(top_key) or {}
        if not isinstance(block, dict):
            continue
        for name, payload in block.items():
            if not isinstance(payload, dict):
                continue
            _classify(str(name), payload, "")  # flat sections have no entity name

    # Group phrased entries by normalized phrase, applying collision rules.
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        key = normalize_phrase(entry["policy_phrase"])
        grouped.setdefault(key, []).append(entry)

    # Build the per-file basename set from the glob list (e.g. "applicant_form.md"
    # or "sub/foo.md" — match by trailing path component since source_doc in
    # specs is conventionally a basename like "eligibility.md").
    relpath_basenames = set()
    for rel in per_file_relpaths:
        # rel is like "applicant_form.md" or "sub/foo.md".
        relpath_basenames.add(rel)
        relpath_basenames.add(Path(rel).name)

    flattened: dict[str, dict] = {}
    for norm_key, candidates in grouped.items():
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            # Prefer entries whose source_doc matches a per-file basename.
            matched = [c for c in candidates if c.get("source_doc") in relpath_basenames]
            pool = matched if matched else candidates
            # Deterministic alphabetical tiebreak by entity name.
            pool_sorted = sorted(pool, key=lambda c: c.get("_entity") or "")
            chosen = pool_sorted[0]
        flattened[norm_key] = {
            "name": chosen["name"],
            "role_hint": chosen.get("role_hint"),
            "source_doc": chosen.get("source_doc"),
            "section": chosen.get("section"),
            "policy_phrase": chosen["policy_phrase"],
            "description": chosen.get("description"),
            "type": chosen.get("type"),
            "values": chosen.get("values"),
        }
    return flattened, seeded_unobserved


# ---------------------------------------------------------------------------
# Per-file reads
# ---------------------------------------------------------------------------


def _list_per_file_files(domain_dir: Path) -> list[Path]:
    root = domain_dir / _COMPUTATIONS
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md.yaml") if p.is_file())


def _per_file_relpath(domain_dir: Path, abs_path: Path) -> str:
    """Return a path string used as the file: column in sources:.

    Strips the trailing .yaml suffix and prefixes with input/policy_docs/ to
    match the source-of-truth path the per-file file describes. Mirrors the
    convention from xl-plugin/CLAUDE.md "Index path keys vs content reads".
    """
    sub_rel = abs_path.relative_to(domain_dir / _COMPUTATIONS)
    sub_str = str(sub_rel)
    if sub_str.endswith(".yaml"):
        sub_str = sub_str[: -len(".yaml")]
    return f"input/policy_docs/{sub_str}"


def _read_per_file_payload(path: Path, errors: list[str]) -> dict | None:
    """Parse a per-file map-shape YAML. Returns None when unusable.

    - Malformed YAML or list-shape (legacy) → log warning, return None.
    - Map shape missing naming_manifest: → log warning, return the map (caller
      will skip naming_manifest extraction but may still need sections).
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"{path}: malformed YAML: {exc}; skipping")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: legacy list-shape or non-map; skipping")
        return None
    return data


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_yaml(path: Path, preamble_lines: list[str], body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for line in preamble_lines:
                f.write(line + "\n")
            yaml.safe_dump(
                body,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp, path)
    except Exception:
        # Clean up tmp on any failure so no partial file is left behind.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Prior naming-defaults read (for stability heuristic + churn report)
# ---------------------------------------------------------------------------


def _read_prior_defaults(domain_dir: Path) -> dict[str, dict]:
    """Return `{normalized_phrase → {canonical, raw_entry}}` from the prior file.

    Returns {} when the prior file is absent or unparseable. Canonical is keyed
    by normalized policy_phrase, NOT by the canonical name itself, so the
    stability heuristic and churn report can join across runs.
    """
    path = domain_dir / _DEFAULTS
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    variables = data.get("variables") or {}
    if not isinstance(variables, dict):
        return {}

    result: dict[str, dict] = {}
    for canonical_name, entry in variables.items():
        if not isinstance(entry, dict):
            continue
        phrase = entry.get("policy_phrase", "")
        norm = normalize_phrase(phrase) if isinstance(phrase, str) else ""
        if not norm:
            continue
        result[norm] = {"canonical": str(canonical_name), "entry": entry}
    return result


# ---------------------------------------------------------------------------
# Canonical pick
# ---------------------------------------------------------------------------


def _pick_canonical(
    observations: list[dict],
    specs_entry: dict | None,
    prior_canonical: str | None,
) -> tuple[str, str]:
    """Return (canonical_name, source_kind).

    `source_kind` is "specs" when specs override fired, "frequency", "shortest",
    "alphabetical", or "stability" — the rule that won.

    `observations` is a list of `{name, policy_phrase, role_hint?, file, section}`
    dicts (one per per-file appearance of a name within this normalized-phrase
    group; multiple appearances of the same name appear multiple times).
    """
    if specs_entry is not None:
        return specs_entry["name"], "specs"

    # Frequency count.
    freq: dict[str, int] = {}
    for obs in observations:
        freq[obs["name"]] = freq.get(obs["name"], 0) + 1
    max_freq = max(freq.values())
    top = sorted(name for name, count in freq.items() if count == max_freq)
    if len(top) == 1:
        return top[0], "frequency"

    # Tie 1: shortest length.
    min_len = min(len(n) for n in top)
    shortest = sorted(n for n in top if len(n) == min_len)
    if len(shortest) == 1:
        return shortest[0], "shortest"

    # Tie 2: alphabetical … but Tie 3: stability.
    alphabetical_winner = shortest[0]
    if (
        prior_canonical is not None
        and prior_canonical in shortest
        and prior_canonical != alphabetical_winner
    ):
        return prior_canonical, "stability"
    return alphabetical_winner, "alphabetical"


def _resolve_role_hint(
    canonical_name: str,
    observations: list[dict],
) -> str | None:
    """Resolve role_hint per the merge-time rule: prefer specific over absent.

    When any observation in the group carries a hint, pick the most specific
    one across the whole group (computed > output > input). When no observation
    carries a hint, return None.

    This implements the Key Technical Decisions rule: "When synonyms disagree
    (one says computed, another says input), prefer the most specific in the
    order computed > output > input." The U4 test scenario for two-file
    disagreement makes clear the rule applies whether or not the canonical
    name's contributor has a hint — the most specific signal wins.
    """
    best: tuple[int, str] | None = None
    for obs in observations:
        hint = obs.get("role_hint")
        if hint in _ROLE_RANK:
            rank = _ROLE_RANK[hint]
            if best is None or rank > best[0]:
                best = (rank, hint)
    return best[1] if best else None


def _resolve_description(
    canonical_name: str,
    observations: list[dict],
    specs_entry: dict | None,
) -> str | None:
    """Resolve description per: specs override > canonical-name contributor > any synonym > None.

    Mirrors `_canonical_policy_phrase` ordering for the canonical-contributor preference,
    then falls back to specific-over-absent (any synonym's description) when the canonical
    contributor lacks one. Specs override beats both.
    """
    if specs_entry is not None and specs_entry.get("description"):
        return specs_entry["description"]
    canonical_desc: str | None = None
    fallback_desc: str | None = None
    for obs in observations:
        desc = obs.get("description")
        if not desc:
            continue
        if obs["name"] == canonical_name and canonical_desc is None:
            canonical_desc = desc
        elif fallback_desc is None:
            fallback_desc = desc
    return canonical_desc or fallback_desc


def _resolve_type(
    canonical_name: str,
    observations: list[dict],
    specs_entry: dict | None,
    errors: list[str],
) -> str | None:
    """Resolve type per: specs override > observed agreement > None.

    Specs always wins when present. Otherwise, collect every observation's type and:
      - If all observations either omit or agree on a single type, use it.
      - On disagreement, log a warning to errors and omit type from the canonical.
    """
    if specs_entry is not None and specs_entry.get("type"):
        return specs_entry["type"]
    observed_types = {obs["type"] for obs in observations if obs.get("type")}
    if not observed_types:
        return None
    if len(observed_types) == 1:
        return next(iter(observed_types))
    errors.append(
        f"variable '{canonical_name}': observed types disagree "
        f"({sorted(observed_types)}); omitting type: in defaults"
    )
    return None


def _resolve_values(
    canonical_type: str | None,
    observations: list[dict],
    specs_entry: dict | None,
) -> list[str] | None:
    """Resolve values per: specs override > sorted union of observed values.

    Only meaningful when `canonical_type == "enum"`. Returns None when no source supplied
    a values list, in which case the canonical entry has `type: enum` with no `values:` —
    acceptable shape; the analyst fills via specs override on the next run.
    """
    if canonical_type != "enum":
        return None
    if specs_entry is not None and specs_entry.get("values"):
        return list(specs_entry["values"])
    union: set[str] = set()
    for obs in observations:
        vs = obs.get("values")
        if isinstance(vs, list):
            union.update(v for v in vs if isinstance(v, str))
    return sorted(union) if union else None


def _canonical_policy_phrase(
    canonical_name: str,
    observations: list[dict],
    specs_entry: dict | None,
) -> str:
    """Return the verbatim policy_phrase from the canonical-name contributor.

    When specs picked the canonical, prefer specs's verbatim phrase — that's
    the analyst-confirmed form. Otherwise pick the first per-file observation
    whose name == canonical_name (deterministic by file ordering).
    """
    if specs_entry is not None and specs_entry.get("policy_phrase"):
        return specs_entry["policy_phrase"]
    for obs in observations:
        if obs["name"] == canonical_name:
            return obs.get("policy_phrase", "")
    # Fallback: any observation's phrase.
    return observations[0].get("policy_phrase", "") if observations else ""


# ---------------------------------------------------------------------------
# Build (the main entry point)
# ---------------------------------------------------------------------------


def cmd_build(domain_dir: Path, dry_run: bool = False) -> dict:
    """Merge per-file naming_manifest blocks into naming-defaults.yaml.

    Returns the JSON-shaped summary dict.
    """
    if not domain_dir.is_dir():
        raise RuntimeError(f"Domain directory not found: {domain_dir}")

    errors: list[str] = []

    # 1. Read per-file files and collect observations.
    per_file_paths = _list_per_file_files(domain_dir)
    per_file_relpaths: set[str] = set()
    per_file_payloads: dict[Path, dict] = {}  # path → parsed map
    observations_by_norm: dict[str, list[dict]] = {}

    for path in per_file_paths:
        rel = _per_file_relpath(domain_dir, path)
        # Strip the input/policy_docs/ prefix for the specs-collision check
        # (specs source_doc is a bare basename like "eligibility.md").
        rel_under_docs = rel
        if rel_under_docs.startswith("input/policy_docs/"):
            rel_under_docs = rel_under_docs[len("input/policy_docs/"):]
        per_file_relpaths.add(rel_under_docs)

        data = _read_per_file_payload(path, errors)
        if data is None:
            continue
        per_file_payloads[path] = data

        naming_manifest = data.get("naming_manifest")
        if naming_manifest is None:
            errors.append(
                f"{path}: map-shape file missing naming_manifest:; "
                f"contributing zero entries"
            )
            continue
        if not isinstance(naming_manifest, dict):
            errors.append(
                f"{path}: naming_manifest is not a map ({type(naming_manifest).__name__}); skipping"
            )
            continue
        variables = naming_manifest.get("variables") or {}
        if not isinstance(variables, dict):
            errors.append(
                f"{path}: naming_manifest.variables is not a map; skipping"
            )
            continue

        for name, entry in variables.items():
            if not isinstance(entry, dict):
                errors.append(f"{path}: naming_manifest.variables.{name} not a map; skipping")
                continue
            phrase = entry.get("policy_phrase", "")
            if not isinstance(phrase, str) or not phrase.strip():
                errors.append(f"{path}: variables.{name} missing or empty policy_phrase; skipping")
                continue
            norm = normalize_phrase(phrase)
            if not norm:
                errors.append(f"{path}: variables.{name} empty after normalization; skipping")
                continue
            # Detect legacy `source_section:` field — renamed to `section:`.
            # When present without the new `section:` key, surface an
            # actionable warning telling the analyst to regenerate the
            # per-file file. The merge tool continues with empty `section`.
            legacy_source_section = entry.get("source_section")
            new_section = entry.get("section")
            if legacy_source_section is not None and new_section is None:
                errors.append(
                    f"{path}: per-file file uses legacy 'source_section:' schema "
                    f"on variable '{name}' — delete and re-run /extract-computations "
                    f"to regenerate."
                )
            section_value = new_section if new_section is not None else ""

            # Read source_doc verbatim from the per-file entry (no path-derivation).
            # Drift sanity check: when the worker-written source_doc disagrees
            # with the file-location-derived equivalent, surface a warning;
            # use the worker's value verbatim regardless.
            entry_source_doc = entry.get("source_doc")
            if entry_source_doc and isinstance(entry_source_doc, str):
                if entry_source_doc != rel:
                    errors.append(
                        f"{path}: variable '{name}' source_doc '{entry_source_doc}' "
                        f"disagrees with file location '{rel}'"
                    )
                obs_source_doc = entry_source_doc
            else:
                # Missing source_doc on a legacy file — fall back to the path-derived
                # equivalent so output stays well-formed; warning already emitted by
                # legacy-source_section detection above when applicable.
                if legacy_source_section is None and new_section is None:
                    # Not a legacy file — explicit missing source_doc is its own issue.
                    errors.append(
                        f"{path}: variable '{name}' missing 'source_doc:' field; "
                        f"falling back to file location"
                    )
                obs_source_doc = rel

            obs = {
                "name": str(name),
                "policy_phrase": phrase,
                "role_hint": entry.get("role_hint"),
                "file": rel,
                "source_doc": obs_source_doc,
                "section": section_value,
                "description": entry.get("description"),
                "type": entry.get("type"),
                "values": entry.get("values"),
            }
            observations_by_norm.setdefault(norm, []).append(obs)

    # 2. Detect unknown names referenced in sections[*].computations[*].variables
    #    (defensive — U2 emitter enforces the cross-block invariant, but hand
    #    edits could break it). Log warnings.
    for path, data in per_file_payloads.items():
        sections = data.get("sections") or []
        if not isinstance(sections, list):
            continue
        naming_manifest = data.get("naming_manifest") or {}
        manifest_keys = set()
        if isinstance(naming_manifest, dict):
            v = naming_manifest.get("variables") or {}
            if isinstance(v, dict):
                manifest_keys = set(v.keys())
        for section in sections:
            if not isinstance(section, dict):
                continue
            for comp in section.get("computations") or []:
                if not isinstance(comp, dict):
                    continue
                for var in comp.get("variables") or []:
                    if isinstance(var, str) and var not in manifest_keys:
                        errors.append(
                            f"{path}: section variable '{var}' not in naming_manifest.variables; "
                            f"skipping canonicalization for it"
                        )

    # 3. Read specs (after we know per_file_relpaths for the collision rule).
    specs_path = domain_dir / _SPECS
    specs_lookup: dict[str, dict] = {}
    seeded_unobserved: dict[str, dict] = {}
    if specs_path.exists():
        try:
            with specs_path.open(encoding="utf-8") as f:
                specs_data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{specs_path}: malformed; treating as empty: {exc}")
            specs_data = None
        if specs_data is not None:
            specs_lookup, seeded_unobserved = _flatten_specs(
                specs_data, per_file_relpaths, errors
            )

    # 4. Read prior naming-defaults for stability heuristic + churn.
    prior = _read_prior_defaults(domain_dir)
    prior_existed = (domain_dir / _DEFAULTS).exists()

    # 5. Pick canonical per group.
    variables_out: dict[str, dict] = {}
    canonicals_changed: list[dict] = []
    rename_map: dict[str, dict[str, str]] = {}  # file → {synonym → canonical}

    for norm_key in sorted(observations_by_norm.keys()):
        observations = observations_by_norm[norm_key]
        specs_entry = specs_lookup.get(norm_key)
        prior_entry = prior.get(norm_key)
        prior_canonical = prior_entry["canonical"] if prior_entry else None

        canonical_name, _source_kind = _pick_canonical(observations, specs_entry, prior_canonical)

        role_hint = _resolve_role_hint(canonical_name, observations)
        policy_phrase = _canonical_policy_phrase(canonical_name, observations, specs_entry)
        description = _resolve_description(canonical_name, observations, specs_entry)
        type_value = _resolve_type(canonical_name, observations, specs_entry, errors)
        values = _resolve_values(type_value, observations, specs_entry)

        # Pick the canonical's source row and build synonym rows.
        # Sort observations by (file, section) so the first matching the canonical
        # name wins the top-level source_doc/section deterministically.
        sorted_obs = sorted(
            observations, key=lambda o: (o["file"], o.get("section", ""))
        )
        canonical_obs_index = next(
            (i for i, o in enumerate(sorted_obs) if o["name"] == canonical_name),
            None,
        )

        if canonical_obs_index is not None:
            canonical_obs = sorted_obs[canonical_obs_index]
            canonical_source_doc = canonical_obs["source_doc"]
            canonical_section = canonical_obs.get("section", "")
            synonym_obs = [
                o for i, o in enumerate(sorted_obs) if i != canonical_obs_index
            ]
        else:
            # Specs-picked canonical never observed in a per-file file. Fall back
            # to the specs entry's source_doc/section. R6 mandates full paths;
            # specs convention is a bare basename like "eligibility.md", so
            # prepend input/policy_docs/ when no slash is present.
            specs_source_doc = (specs_entry or {}).get("source_doc") or ""
            if specs_source_doc and "/" not in specs_source_doc:
                specs_source_doc = f"input/policy_docs/{specs_source_doc}"
            canonical_source_doc = specs_source_doc
            canonical_section = (specs_entry or {}).get("section") or ""
            synonym_obs = list(sorted_obs)

        synonym_rows: list[dict] = []
        seen_triples: set[tuple[str, str, str]] = set()
        for obs in synonym_obs:
            triple = (obs["name"], obs["source_doc"], obs.get("section", ""))
            if triple in seen_triples:
                continue
            seen_triples.add(triple)
            row: dict = {"name": obs["name"], "source_doc": obs["source_doc"]}
            section = obs.get("section")
            if section:
                row["section"] = section
            synonym_rows.append(row)
        synonym_rows.sort(
            key=lambda r: (r["name"], r["source_doc"], r.get("section", ""))
        )

        # Field order is load-bearing: yaml.safe_dump(sort_keys=False) preserves
        # insertion order, so this is what readers see in naming-defaults.yaml.
        out_entry: dict = {}
        if description:
            out_entry["description"] = description
        out_entry["policy_phrase"] = policy_phrase
        if type_value:
            out_entry["type"] = type_value
        if values:
            out_entry["values"] = values
        if role_hint:
            out_entry["role_hint"] = role_hint
        if canonical_source_doc:
            out_entry["source_doc"] = canonical_source_doc
        if canonical_section:
            out_entry["section"] = canonical_section
        if synonym_rows:
            out_entry["synonyms"] = synonym_rows
        variables_out[canonical_name] = out_entry

        # Churn detection (only when prior file existed).
        if prior_existed and prior_entry and prior_entry["canonical"] != canonical_name:
            canonicals_changed.append({
                "phrase": policy_phrase,
                "from": prior_entry["canonical"],
                "to": canonical_name,
            })

        # Rename map: every observed name that is NOT canonical needs to be
        # rewritten in the per-file file(s) where it appears.
        for obs in observations:
            if obs["name"] != canonical_name:
                rename_map.setdefault(obs["file"], {})[obs["name"]] = canonical_name

    # 5b. Pass 2: phraseless seeded entries.
    # Each name in seeded_unobserved either matches a pass-1 canonical (merge
    # seed-supplied type/values/description into the existing entry, with the
    # seeded value winning on conflict per the three-tier authority rule) or
    # surfaces as a standalone canonical with no top-level provenance and no
    # synonyms list (R6).
    for seeded_name, seed_payload in sorted(seeded_unobserved.items()):
        if seeded_name in variables_out:
            existing = variables_out[seeded_name]
            # Seeded values win when the seed supplied them.
            seed_type = seed_payload.get("type")
            seed_values = seed_payload.get("values")
            seed_description = seed_payload.get("description")
            if seed_type:
                if existing.get("type") and existing["type"] != seed_type:
                    errors.append(
                        f"variable '{seeded_name}': seeded type '{seed_type}' "
                        f"overrides observed type '{existing['type']}'"
                    )
                # Insert/replace `type` while preserving relative field order
                # (description, policy_phrase, type, values, role_hint, source_doc, section, synonyms).
                _set_in_order(existing, "type", seed_type, before=("values", "role_hint", "source_doc", "section", "synonyms"))
            if seed_values and seed_type == "enum":
                _set_in_order(existing, "values", list(seed_values), before=("role_hint", "source_doc", "section", "synonyms"))
            if seed_description and not existing.get("description"):
                _set_in_order(existing, "description", seed_description, before=("policy_phrase", "type", "values", "role_hint", "source_doc", "section", "synonyms"))
        else:
            # New standalone canonical: no top-level provenance, no synonyms.
            out_entry: dict = {}
            if seed_payload.get("description"):
                out_entry["description"] = seed_payload["description"]
            # `policy_phrase` is empty for seeded-unobserved; omit the key entirely.
            if seed_payload.get("type"):
                out_entry["type"] = seed_payload["type"]
            if seed_payload.get("values") and seed_payload.get("type") == "enum":
                out_entry["values"] = list(seed_payload["values"])
            if seed_payload.get("role_hint"):
                out_entry["role_hint"] = seed_payload["role_hint"]
            variables_out[seeded_name] = out_entry

    # 5c. Convergence warning: when a seeded standalone name is similar to a
    # pass-1 auto-picked canonical (Levenshtein distance ≤ 2, OR ≤ 25% of name
    # length when the longer name has more than 8 characters), surface a
    # warning so the analyst can confirm or rename in /extract-ruleset Step 3b.
    standalone_seeded_names = {
        name for name in seeded_unobserved
        # Only standalone (not merged into a pass-1 canonical) trigger warnings.
        if name in variables_out and variables_out[name].get("source_doc") is None
        and variables_out[name].get("section") is None
        and variables_out[name].get("synonyms") is None
    }
    pass1_canonical_names = set(variables_out.keys()) - standalone_seeded_names
    for seed_name in sorted(standalone_seeded_names):
        for canonical_name in pass1_canonical_names:
            if canonical_name == seed_name:
                continue
            distance = _levenshtein(seed_name, canonical_name)
            longer_len = max(len(seed_name), len(canonical_name))
            threshold = max(2, longer_len // 4) if longer_len > 8 else 2
            if 0 < distance <= threshold:
                errors.append(
                    f"seeded '{seed_name}' has no observation; observed canonical "
                    f"'{canonical_name}' is similar (edit distance {distance}) — "
                    f"confirm or rename in /extract-ruleset Step 3b"
                )

    # 6. Sort variables alphabetically by canonical name.
    sorted_variables = {k: variables_out[k] for k in sorted(variables_out.keys())}

    # 7. Per-file rewrites of sections[*].computations[*].variables.
    files_rewritten = 0
    if not dry_run:
        for path, data in per_file_payloads.items():
            rel = _per_file_relpath(domain_dir, path)
            renames = rename_map.get(rel, {})
            if not renames:
                continue
            rewritten = _rewrite_section_variables(data, renames)
            if not rewritten:
                continue
            # Preserve preamble lines from the original file.
            preamble = _read_preamble_lines(path)
            try:
                _atomic_write_yaml(path, preamble, data)
                files_rewritten += 1
            except OSError as exc:
                errors.append(f"{path}: rewrite failed: {exc}")

    # 8. Write the defaults file (atomic).
    body = {"variables": sorted_variables}
    defaults_path = domain_dir / _DEFAULTS
    if not dry_run:
        _atomic_write_yaml(
            defaults_path,
            ["# Auto-generated by xlator naming-defaults --build — do not edit manually"],
            body,
        )

    # 9. Build summary.
    # Count unique non-canonical names per entry (not row count): self-synonym
    # rows and multi-source synonym duplicates would inflate a row-based count
    # and break comparability across schemas. (R10)
    synonyms_collapsed = sum(
        len({row["name"] for row in entry.get("synonyms", [])} - {canonical})
        for canonical, entry in sorted_variables.items()
    )
    summary = {
        "merged": len(sorted_variables),
        "synonyms_collapsed": synonyms_collapsed,
        "files_rewritten": files_rewritten,
        "canonicals_changed": canonicals_changed,
        "errors": errors,
    }

    # Echo warnings to stderr so they're visible during interactive runs.
    for err in errors:
        print(f"# warning: {err}", file=sys.stderr)

    return summary


def _rewrite_section_variables(data: dict, renames: dict[str, str]) -> bool:
    """Mutate data in place to apply renames inside sections[*].computations[*].variables.

    Returns True when at least one variable was renamed. naming_manifest.variables
    keys are intentionally NOT rewritten (per the U4 algorithm spec).
    """
    sections = data.get("sections")
    if not isinstance(sections, list):
        return False
    changed = False
    for section in sections:
        if not isinstance(section, dict):
            continue
        comps = section.get("computations")
        if not isinstance(comps, list):
            continue
        for comp in comps:
            if not isinstance(comp, dict):
                continue
            variables = comp.get("variables")
            if not isinstance(variables, list):
                continue
            new_variables: list[str] = []
            for var in variables:
                if isinstance(var, str) and var in renames:
                    new_variables.append(renames[var])
                    changed = True
                else:
                    new_variables.append(var)
            comp["variables"] = new_variables
    return changed


def _read_preamble_lines(path: Path) -> list[str]:
    """Return the leading `# …` comment lines + a single blank line of an existing
    YAML file, so per-file rewrites preserve their original preamble style.

    When the original file has no preamble, return a minimal one matching the
    emitter convention.
    """
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ["# Auto-generated by /extract-computations — do not edit manually"]

    preamble: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("#"):
            preamble.append(line)
            continue
        if line.strip() == "":
            # One blank line is a separator the emitter writes; preserve it.
            if preamble:
                # _atomic_write_yaml writes "\n" after each preamble line, so
                # an empty string here yields a blank line.
                preamble.append("")
            continue
        break
    if not preamble:
        return ["# Auto-generated by /extract-computations — do not edit manually"]
    return preamble


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge per-file naming_manifest blocks into policy_facets/naming-defaults.yaml."
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "--build", action="store_true", required=True,
        help="Run the merge (writes naming-defaults.yaml and per-file rewrites).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Plan without writing any files.",
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        sys.exit(1)
    domain_dir = Path(domains_root) / args.domain

    try:
        summary = cmd_build(domain_dir, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
