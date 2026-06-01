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
  Pass 2a  ruleset-modules.yaml + sample-artifacts.yaml `sample_rules[*].catala`
           literal blocks, dot-notation base names (Catala scope-call output
           field access: `<subvar>.<field>`)
           → reason "scope-call derived: sample rule Catala snippet"
  Pass 2b  same Catala snippets, snake_case identifiers appearing in
           `under condition <expr>` clauses (excluding dot-notation LHS,
           Catala keywords, and string literals)
           → reason "decision condition: under-condition clause in scope rule"
  Pass 3   naming-manifest.yaml `outputs:` keys
           → reason "output variable: naming-manifest.yaml outputs"
  Existing entries from include-with-output.yaml are appended last with
           reason "existing".

Comprehension / quantifier loop-variable filter:
  Passes 1, 2a, and 2b strip ephemeral loop bindings before emitting names.
  Catala forms: `exists <v> among ...`, `for all <v> among ...`,
  `list of <v> among ...`, `map each <v> among ...`,
  `combine all <v> among ...`, `content of <v> among ...`. Pseudo-Python
  sketch form (skeleton expressions): `for <v> in <list>`. A loop variable
  is local to the iteration expression and is never a real computed
  variable or sub-scope binding.

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
_REASON_CATALA_SNIPPET = "scope-call derived: sample rule Catala snippet"
_REASON_UNDER_CONDITION = "decision condition: under-condition clause in scope rule"
_REASON_OUTPUT = "output variable: naming-manifest.yaml outputs"
_REASON_EXISTING = "existing"

# Dot-notation: capture the base identifier on the LHS of `<base>.<member>`.
# Whitespace around the dot is tolerated. In Catala, this captures the
# sub-scope binding variable in `<subvar>.<output_field>` access.
_DOT_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\.\s*[a-z_]")

# Bare snake_case identifier: not preceded by an alnum/underscore/dot,
# not followed by a dot (dot-notation LHS belongs to Pass 2a).
_IDENT_RE = re.compile(r"(?<![a-zA-Z0-9_.])([a-z_][a-z0-9_]*)(?!\.)")

# Catala keywords filtered out of bare-identifier tokenization. Covers the
# keyword surface that appears in `under condition` bodies (boolean ops,
# comparisons, literal forms, comprehensions, conditionals, modules).
# Capital-letter idents (type/scope/enum names) are excluded by the snake
# case regex itself.
_KEYWORDS = frozenset({
    # boolean / control
    "if", "then", "else", "and", "or", "not", "xor",
    # aggregates / comprehensions
    "min", "max", "sum", "count", "of", "among", "in", "to",
    "map", "each", "list", "such", "that", "for", "all", "we", "have",
    "exists", "contains", "combine", "initially", "with", "number",
    "maximum", "minimum", "content", "is",
    # rules / conditions
    "under", "condition", "consequence", "fulfilled", "equals",
    "definition", "rule", "scope", "exception",
    "the", "this", "true", "false",
    # type literals / constructors
    "money", "decimal", "integer", "boolean", "date", "duration",
    # misc
    "as", "any", "anything", "match", "pattern", "matches",
})

# Strip single- and double-quoted string literals from a snippet before
# identifier tokenization so quoted enum tags don't surface as variable
# names.
_STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

# Catala money literals look like `$1,255` or `$1,704.50`. Strip them
# before identifier tokenization so commas/digits don't confuse the
# downstream scanner.
_MONEY_LITERAL_RE = re.compile(r"\$[0-9][0-9,]*(?:\.[0-9]+)?")

# Catala percent literals: `200%`, `12.5%`.
_PERCENT_LITERAL_RE = re.compile(r"\b[0-9]+(?:\.[0-9]+)?%")

# `under condition <expr> consequence` — capture the expression between
# the `under condition` lead-in and the next `consequence`/`equals`
# terminator (or end of fenced block). Multi-line via DOTALL; non-greedy
# so multiple rules in one snippet don't merge.
_UNDER_CONDITION_RE = re.compile(
    r"under\s+condition\s+(.*?)(?=\s+consequence\b|\s+equals\b|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# Comprehension / quantifier loop-variable binders. Catala forms all use
# `<keyword> <var> among <expr>`: `exists`, `for all`, `list of`,
# `map each`, `combine all`, `content of`. The pseudo-Python sketch form
# `for <var> in <list>` also appears in skeleton expressions. The loop
# variable name is ephemeral (scope-local iteration binding) and must NOT
# be surfaced as an include-with-output entry — it is not a real
# computed variable or sub-scope binding.
_LOOP_VAR_RE = re.compile(
    r"(?:exists|for\s+all|list\s+of|map\s+each|combine\s+all|content\s+of)"
    r"\s+([a-z_][a-z0-9_]*)\s+among\b"
    r"|\bfor\s+([a-z_][a-z0-9_]*)\s+in\b",
    re.IGNORECASE,
)

# Catala 1.1.0 `#[<key> = <value>]` attribute annotations. The key form
# `error.message`, `test.expected`, etc., is dot-notated but is a
# compiler-directed annotation — not a scope-call binding. Strip these
# blocks before dot-notation tokenization so the attribute namespace
# (e.g., `error`, `test`) doesn't surface as a sub-scope binding name.
_CATALA_ATTRIBUTE_RE = re.compile(r"#\[[^\]]*\]")


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


def _tokenize_condition_expr(expr: str) -> list[str]:
    """Return snake_case identifiers from a Catala condition expression.

    String literals, money literals, and percent literals are stripped first;
    keywords and dot-notation LHS are filtered out by the regex itself.
    """
    if not isinstance(expr, str) or not expr:
        return []
    stripped = _STRING_LITERAL_RE.sub("", expr)
    stripped = _MONEY_LITERAL_RE.sub("", stripped)
    stripped = _PERCENT_LITERAL_RE.sub("", stripped)
    return [m for m in _IDENT_RE.findall(stripped) if m not in _KEYWORDS]


def _extract_under_condition_exprs(catala_text: str) -> list[str]:
    """Return the body of every `under condition <expr> consequence` clause
    in a Catala snippet. Multiple rules in one snippet yield multiple
    entries. Returns `[]` for non-Catala text (no matches).
    """
    if not isinstance(catala_text, str) or not catala_text:
        return []
    return [m.strip() for m in _UNDER_CONDITION_RE.findall(catala_text) if m.strip()]


def _extract_loop_vars(text: str) -> set[str]:
    """Return the set of comprehension / quantifier loop-variable names
    bound in `text`. These are ephemeral iteration bindings and must be
    excluded from include-with-output detection — they are not computed
    variables or sub-scope bindings.
    """
    if not isinstance(text, str) or not text:
        return set()
    loop_vars: set[str] = set()
    for m in _LOOP_VAR_RE.finditer(text):
        for group in m.groups():
            if group:
                loop_vars.add(group)
    return loop_vars


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
            if not isinstance(expr_value, str):
                continue
            loop_vars = _extract_loop_vars(expr_value)
            names.extend(
                n for n in _scan_dot_notation(expr_value) if n not in loop_vars
            )
    return names


def _iter_catala_snippets(domain_dir: Path) -> list[tuple[str, str, str]]:
    """Yield (source_file_rel, rule_path, catala_text) for every
    `sample_rules[*].catala` literal block in ruleset-modules.yaml and
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
                    catala = rule.get("catala")
                    if not isinstance(catala, str) or not catala:
                        continue
                    rule_id = rule.get("id", f"<index {r_idx}>")
                    snippets.append((
                        _RULESET_MODULES,
                        f"ruleset_modules[{module_name}].sample_rules[{rule_id}]",
                        catala,
                    ))

    sa_data = _load_yaml(domain_dir / _SAMPLE_ARTIFACTS)
    if isinstance(sa_data, dict):
        sample_rules = sa_data.get("sample_rules")
        if isinstance(sample_rules, list):
            for r_idx, rule in enumerate(sample_rules):
                if not isinstance(rule, dict):
                    continue
                catala = rule.get("catala")
                if not isinstance(catala, str) or not catala:
                    continue
                rule_id = rule.get("id", f"<index {r_idx}>")
                snippets.append((
                    _SAMPLE_ARTIFACTS,
                    f"sample_rules[{rule_id}]",
                    catala,
                ))

    return snippets


def _collect_pass2(
    snippets: list[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    """Pass 2a + 2b over a pre-collected snippet list.

    Pass 2a runs on the raw Catala string (dot-notation regex captures
    sub-scope binding bases from `<subvar>.<output_field>`). Pass 2b
    scans the same text for `under condition <expr> consequence` clauses
    and tokenizes bare identifiers in each condition body.
    """
    pass2a: list[str] = []
    pass2b: list[str] = []
    for _source_file, _rule_path, catala in snippets:
        # Strip `#[...]` attribute annotations first so attribute keys like
        # `error.message` don't surface as sub-scope binding base names.
        scrubbed = _CATALA_ATTRIBUTE_RE.sub("", catala)
        loop_vars = _extract_loop_vars(scrubbed)
        pass2a.extend(
            n for n in _scan_dot_notation(scrubbed) if n not in loop_vars
        )
        for cond_expr in _extract_under_condition_exprs(scrubbed):
            pass2b.extend(
                n
                for n in _tokenize_condition_expr(cond_expr)
                if n not in loop_vars
            )
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
        (pass2a, _REASON_CATALA_SNIPPET),
        (pass2b, _REASON_UNDER_CONDITION),
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
    snippets = _iter_catala_snippets(domain_dir)
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
