#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator tag-vars-include-output: deterministic detection of intermediate
computed variables to expose in the API's `ComputedBreakdown` response.

Replaces the AI-driven detection prose in
`xl-plugin/skills/tag-vars-to-include-with-output/SKILL.md` (passes 1, 2a,
2b, 3) with three pure YAML traversals plus regex tokenization. Writes a
deduplicated, order-stable flat YAML list to
`specs/guidance/include-with-output.yaml`. Pre-existing entries are
preserved with the `(existing)` reason label; no entry is ever removed by
this tool.

Detection passes:
  Pass 1   skeleton.yaml `computations[*].exprs` dot-notation base names
           → reason "invoke-derived: skeleton computations"
  Pass 2a  ruleset-modules.yaml + sample-artifacts.yaml `sample_rules[*].civil`
           literal blocks, dot-notation base names
           → reason "invoke-derived: sample rule CIVIL snippet"
  Pass 2b  same civil blocks, parsed as YAML, snake_case identifiers in
           any `when:` clause (excluding dot-notation LHS, keywords,
           and string literals)
           → reason "decision condition: when: clause in categorical rule"
  Pass 3   naming-manifest.yaml `outputs:` keys
           → reason "output variable: naming-manifest.yaml outputs"
  Existing entries from include-with-output.yaml are appended last with
           reason "existing".

When a name surfaces from multiple passes, the first reason wins
(Pass 1 → 2a → 2b → 3 → existing). Output order is deterministic and
stable across re-runs.

Usage:
    xlator tag-vars-include-output <domain>

Exit codes:
    0 — success or no-op (file already up to date)
    2 — pre-flight failure (missing domain folder, missing naming manifest,
        unset DOMAINS_FULLPATH)
    1 — unexpected error (parse failure, IO error) — propagates as an
        uncaught exception with a stack trace
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

_NAMING_MANIFEST = "specs/naming-manifest.yaml"
_SKELETON = "specs/guidance/skeleton.yaml"
_RULESET_MODULES = "specs/guidance/ruleset-modules.yaml"
_SAMPLE_ARTIFACTS = "specs/guidance/sample-artifacts.yaml"
_INCLUDE_WITH_OUTPUT = "specs/guidance/include-with-output.yaml"

_REASON_SKELETON = "invoke-derived: skeleton computations"
_REASON_CIVIL_SNIPPET = "invoke-derived: sample rule CIVIL snippet"
_REASON_WHEN_CLAUSE = "decision condition: when: clause in categorical rule"
_REASON_OUTPUT = "output variable: naming-manifest.yaml outputs"
_REASON_EXISTING = "existing"

# Dot-notation: capture the base identifier on the LHS of `<base>.<member>`.
# Whitespace around the dot is tolerated (CIVIL formatting tolerance).
_DOT_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\.\s*[a-z_]")

# Bare snake_case identifier: not preceded by an alnum/underscore/dot,
# not followed by a dot (dot-notation LHS belongs to Pass 2a).
_IDENT_RE = re.compile(r"(?<![a-zA-Z0-9_.])([a-z_][a-z0-9_]*)(?!\.)")

# Mirrors the tokenizer keyword filter described in /extract-sample-rules
# Step 2 and /create-ruleset-modules Step 1. Kept in sync there in prose
# until a second scripted consumer materializes (see plan: deferred work).
_KEYWORDS = frozenset({
    "if", "else", "and", "or", "not",
    "min", "max", "sum", "count",
    "true", "false", "null",
})

# Strip single- and double-quoted string literals from a `when:` value
# before identifier tokenization so quoted enum tags don't surface as
# variable names.
_STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _load_yaml(path: Path) -> Any:
    """Return parsed YAML, or None when the file is missing.

    Parse errors propagate — the tool exits 1 with a stack trace, matching
    `emit_per_file_yaml.py`'s convention for malformed input that the caller
    should fix at the source.
    """
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _scan_dot_notation(text: str) -> list[str]:
    """Return base names found in `<base>.<member>` patterns within `text`."""
    if not isinstance(text, str) or not text:
        return []
    return _DOT_RE.findall(text)


def _tokenize_when_value(value: Any) -> list[str]:
    """Return snake_case identifiers from a `when:` value.

    Accepts a single string or a list of strings (CIVIL emits both forms —
    a single condition or a list of conjunctive conditions). String literals
    are stripped first; keywords and dot-notation LHS are filtered out by
    the regex itself.
    """
    if isinstance(value, list):
        return [
            ident
            for item in value
            for ident in _tokenize_when_value(item)
        ]
    if not isinstance(value, str) or not value:
        return []
    stripped = _STRING_LITERAL_RE.sub("", value)
    return [m for m in _IDENT_RE.findall(stripped) if m not in _KEYWORDS]


def _walk_when_clauses(node: Any) -> list[Any]:
    """Collect every value of a `when:` key at any depth in `node`."""
    found: list[Any] = []

    def _walk(n: Any) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "when":
                    found.append(v)
                else:
                    _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return found


def _collect_pass1(domain_dir: Path) -> list[str]:
    """Pass 1: skeleton.yaml `computations[*].exprs` value dot-notation."""
    data = _load_yaml(domain_dir / _SKELETON)
    if not isinstance(data, dict):
        return []
    skeleton = data.get("skeleton")
    if not isinstance(skeleton, dict):
        return []
    computations = skeleton.get("computations")
    if not isinstance(computations, list):
        return []
    names: list[str] = []
    for entry in computations:
        if not isinstance(entry, dict):
            continue
        exprs = entry.get("exprs")
        if not isinstance(exprs, dict):
            continue
        for expr_value in exprs.values():
            names.extend(_scan_dot_notation(expr_value))
    return names


def _iter_civil_snippets(domain_dir: Path) -> list[tuple[str, str, str]]:
    """Yield (source_file_rel, rule_path, civil_text) for every
    `sample_rules[*].civil` literal block in ruleset-modules.yaml and
    sample-artifacts.yaml. `rule_path` describes the YAML location for
    stderr WARN diagnostics on parse failure.
    """
    snippets: list[tuple[str, str, str]] = []

    rm_data = _load_yaml(domain_dir / _RULESET_MODULES)
    if isinstance(rm_data, dict):
        modules = rm_data.get("ruleset_modules")
        if isinstance(modules, list):
            for m_idx, module in enumerate(modules):
                if not isinstance(module, dict):
                    continue
                module_name = module.get("name", f"<index {m_idx}>")
                sample_rules = module.get("sample_rules")
                if not isinstance(sample_rules, list):
                    continue
                for r_idx, rule in enumerate(sample_rules):
                    if not isinstance(rule, dict):
                        continue
                    civil = rule.get("civil")
                    if not isinstance(civil, str) or not civil:
                        continue
                    rule_id = rule.get("id", f"<index {r_idx}>")
                    snippets.append((
                        _RULESET_MODULES,
                        f"ruleset_modules[{module_name}].sample_rules[{rule_id}]",
                        civil,
                    ))

    sa_data = _load_yaml(domain_dir / _SAMPLE_ARTIFACTS)
    if isinstance(sa_data, dict):
        sample_rules = sa_data.get("sample_rules")
        if isinstance(sample_rules, list):
            for r_idx, rule in enumerate(sample_rules):
                if not isinstance(rule, dict):
                    continue
                civil = rule.get("civil")
                if not isinstance(civil, str) or not civil:
                    continue
                rule_id = rule.get("id", f"<index {r_idx}>")
                snippets.append((
                    _SAMPLE_ARTIFACTS,
                    f"sample_rules[{rule_id}]",
                    civil,
                ))

    return snippets


def _collect_pass2(
    snippets: list[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    """Pass 2a + 2b over a pre-collected snippet list.

    Pass 2a runs on the raw civil string (dot-notation regex). Pass 2b
    requires the snippet to parse as YAML; parse failures log a WARN to
    stderr and skip 2b for that snippet only.
    """
    pass2a: list[str] = []
    pass2b: list[str] = []
    for source_file, rule_path, civil in snippets:
        pass2a.extend(_scan_dot_notation(civil))
        try:
            parsed = yaml.safe_load(civil)
        except yaml.YAMLError as exc:
            print(
                f"WARN: civil snippet in {source_file} at {rule_path} "
                f"failed to parse: {exc}",
                file=sys.stderr,
            )
            continue
        for when_value in _walk_when_clauses(parsed):
            pass2b.extend(_tokenize_when_value(when_value))
    return pass2a, pass2b


def _collect_pass3(domain_dir: Path) -> list[str]:
    """Pass 3: naming-manifest.yaml `outputs:` block keys."""
    data = _load_yaml(domain_dir / _NAMING_MANIFEST)
    if not isinstance(data, dict):
        return []
    outputs = data.get("outputs")
    if not isinstance(outputs, dict):
        return []
    return [str(k) for k in outputs.keys()]


def _collect_existing(domain_dir: Path) -> list[str]:
    """Existing entries from include-with-output.yaml (flat list of strings)."""
    data = _load_yaml(domain_dir / _INCLUDE_WITH_OUTPUT)
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, str)]


def _merge(
    pass1: list[str],
    pass2a: list[str],
    pass2b: list[str],
    pass3: list[str],
    existing: list[str],
) -> OrderedDict[str, str]:
    """Merge detection passes into an ordered dict, first-write-wins.

    Order: detected names first in Pass 1 → 2a → 2b → 3 source order,
    then existing-only names in their prior file order.
    """
    merged: OrderedDict[str, str] = OrderedDict()
    for names, reason in (
        (pass1, _REASON_SKELETON),
        (pass2a, _REASON_CIVIL_SNIPPET),
        (pass2b, _REASON_WHEN_CLAUSE),
        (pass3, _REASON_OUTPUT),
    ):
        for name in names:
            if name not in merged:
                merged[name] = reason
    for name in existing:
        if name not in merged:
            merged[name] = _REASON_EXISTING
    return merged


def _format_summary(merged: OrderedDict[str, str], header: str) -> str:
    """Build the stdout block: header line followed by aligned name/reason
    lines. Padding width is `max name length + 2`."""
    if not merged:
        return header
    width = max(len(name) for name in merged) + 2
    lines = [header]
    for name, reason in merged.items():
        if reason == _REASON_EXISTING:
            lines.append(f"  {name:<{width}}(existing)")
        else:
            lines.append(f"  {name:<{width}}({reason})")
    return "\n".join(lines)


def _serialize(names: list[str]) -> str:
    """Serialize the merged name list to YAML (flat list of strings)."""
    if not names:
        return "[]\n"
    return yaml.safe_dump(names, default_flow_style=False, sort_keys=False)


def _atomic_write(dest: Path, content: str) -> None:
    """Write `content` to `dest` via `tmp + os.replace` so a failed write
    leaves the prior file intact."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, dest)


def run(domain_dir: Path) -> int:
    """Execute detection passes, merge, and write. Returns process exit code."""
    pass1 = _collect_pass1(domain_dir)
    snippets = _iter_civil_snippets(domain_dir)
    pass2a, pass2b = _collect_pass2(snippets)
    pass3 = _collect_pass3(domain_dir)
    existing = _collect_existing(domain_dir)

    merged = _merge(pass1, pass2a, pass2b, pass3, existing)

    dest = domain_dir / _INCLUDE_WITH_OUTPUT
    new_content = _serialize(list(merged.keys()))

    if not merged:
        # No detections and no existing entries — write an empty list and
        # emit the dedicated empty-case message. Re-runs with the same
        # state short-circuit via the byte-identical check below.
        existing_bytes = dest.read_bytes() if dest.exists() else None
        if existing_bytes != new_content.encode("utf-8"):
            _atomic_write(dest, new_content)
        print(
            "No variables auto-detected. "
            "Empty list written to guidance/include-with-output.yaml."
        )
        return 0

    existing_bytes = dest.read_bytes() if dest.exists() else None
    if existing_bytes == new_content.encode("utf-8"):
        header = "include-with-output up to date (no changes):"
    else:
        _atomic_write(dest, new_content)
        header = "include-with-output written to guidance/include-with-output.yaml:"

    print(_format_summary(merged, header))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect intermediate computed variables to include with the "
            "ruleset's API response and write them to "
            "specs/guidance/include-with-output.yaml."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print(
            "Error: DOMAINS_FULLPATH not set in environment.",
            file=sys.stderr,
        )
        return 2

    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        print(f"Domain not found: {domain_dir}", file=sys.stderr)
        return 2

    naming_manifest = domain_dir / _NAMING_MANIFEST
    if not naming_manifest.exists():
        print(
            f"specs/naming-manifest.yaml not found: {naming_manifest}\n"
            f"Run /declare-target-ruleset {args.domain} first.",
            file=sys.stderr,
        )
        return 2

    return run(domain_dir)


if __name__ == "__main__":
    sys.exit(main())
