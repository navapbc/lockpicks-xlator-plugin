#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""
xlator review-buckets: deterministic Step-2 bucket partitioning for
`/review-ruleset`.

Parses `specs/<program>.catala_en`, finds every `<!-- review: ... -->`
HTML-comment block paired with the immediately-following ` ```catala `
(or ` ```catala-metadata `) fenced block, and partitions the resulting
items by their `review:` scores. Emits a single-line JSON header plus a
formatted analyst-facing body.

Why HTML-comment blocks? Catala has no native annotation field on a
`rule`/`definition`. The AI-authoring convention (documented in
`xl-plugin/skills/extract-ruleset/SKILL.md`) puts a `<!-- review: ... -->`
block on the prose line immediately above each fenced block:

    <!-- review:
           extraction_fidelity: 5
           source_clarity: 5
           logic_complexity: 1
           policy_complexity: 1
           notes: "..."
    -->

    ```catala
    scope EligibilityDecision:
      rule rule_name
        under condition X
        consequence fulfilled
    ```

The Catala parser ignores HTML comments (they live outside fences) so
they have no compile-time effect; markdown renderers also drop them.

Partitioning (priorities applied top-down):
  Unscored  — `review:` block absent above the fence.
  Uncertain — `extraction_fidelity` <= 2 OR `source_clarity` <= 2.
  Complex   — (`logic_complexity` >= 4 OR `policy_complexity` >= 4)
              AND not already in Uncertain.
  Verified  — everything else.

Items meeting both Uncertain and Complex predicates appear ONLY under
Uncertain; their Flagged-for line includes the high-complexity flags
alongside the low-fidelity/clarity flags.

Output transport (stdout):
  1. Single-line JSON header carrying bucket counts and per-bucket item
     IDs (raw, no display-prefix).
  2. Sentinel divider line: `--- REVIEW-BUCKETS-HEADER-END ---`.
  3. Human-readable body the skill relays inside `:::detail`.

Usage:
    xlator review-buckets <domain> <program>

Exit codes:
    0 — success
    2 — pre-flight failure (missing domain, missing Catala source,
        unset DOMAINS_FULLPATH)
    1 — unexpected error (IO error, malformed review block)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

_HEADER_SENTINEL = "--- REVIEW-BUCKETS-HEADER-END ---"
_ITEM_SEPARATOR = "─" * 65
_ALL_VERIFIED_MESSAGE = "All items verified — no uncertain or complex items."

# Score thresholds (locked at v1 — bumping requires a rubric decision).
_FIDELITY_LOW = 2
_CLARITY_LOW = 2
_LOGIC_HIGH = 4
_POLICY_HIGH = 4

_SCORE_FIELDS = (
    "extraction_fidelity",
    "source_clarity",
    "logic_complexity",
    "policy_complexity",
)

# Markdown / Catala recognition patterns.
_HEADING_RE = re.compile(r"^#+\s+(.*?)\s*$")
_SOURCE_LINE_RE = re.compile(r"^\s*\*Source:\s*(.+?)\s*\*\s*$")
_REVIEW_OPEN_RE = re.compile(r"^\s*<!--\s*review:\s*$")
_REVIEW_CLOSE_RE = re.compile(r"^\s*-->\s*$")
_FENCE_OPEN_RE = re.compile(r"^\s*```(catala(?:-metadata)?)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")
_SCORE_LINE_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$")

# First-identifier patterns inside a fence body. The leading `scope <Name>:`
# line is skipped; the next non-blank line that matches one of these
# becomes the item identifier.
_RULE_RE = re.compile(r"^\s*rule\s+([A-Za-z_][A-Za-z0-9_]*)")
_DEFINITION_RE = re.compile(r"^\s*definition\s+([A-Za-z_][A-Za-z0-9_]*)")
_LABEL_RE = re.compile(r"^\s*label\s+([A-Za-z_][A-Za-z0-9_]*)")
_EXCEPTION_RE = re.compile(r"^\s*exception(?:\s+([A-Za-z_][A-Za-z0-9_]*))?")


# ---------------------------------------------------------------------------
# Item normalization
# ---------------------------------------------------------------------------


class Item:
    __slots__ = (
        "raw_id",
        "display_id",
        "kind",
        "description",
        "scores",
        "source_str",
        "catala_str",
        "notes",
    )

    def __init__(
        self,
        raw_id: str,
        display_id: str,
        kind: str,
        description: str,
        scores: Optional[dict[str, int]],
        source_str: str,
        catala_str: str,
        notes: Optional[str],
    ) -> None:
        self.raw_id = raw_id
        self.display_id = display_id
        self.kind = kind  # "rule" | "definition"
        self.description = description
        self.scores = scores
        self.source_str = source_str
        self.catala_str = catala_str
        self.notes = notes


def _parse_review_block(lines: list[str]) -> tuple[Optional[dict[str, int]], Optional[str]]:
    """Parse the body of an HTML review comment into (scores, notes).

    `lines` excludes the opening `<!-- review:` and the closing `-->`.
    Each score line is `<key>: <int>`; notes is a quoted string. Missing
    individual scores default to 3 (mid-range) to surface partially-scored
    entries without forcing them into Unscored."""
    scores: dict[str, int] = {f: 3 for f in _SCORE_FIELDS}
    notes: Optional[str] = None
    any_score = False
    for ln in lines:
        m = _SCORE_LINE_RE.match(ln)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key in _SCORE_FIELDS:
            try:
                scores[key] = int(value)
                any_score = True
            except ValueError:
                continue
        elif key == "notes":
            v = value.strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            if v:
                notes = v
    if not any_score and notes is None:
        return None, None
    return scores, notes


def _classify_fence(fence_lines: list[str]) -> tuple[str, str, str]:
    """Inspect a fence body and return (kind, identifier, catala_str).

    - kind is "rule" or "definition" (Catala's `rule` vs `definition`).
    - identifier is the first rule/definition name found.
    - catala_str is the first non-trivial code line for display.

    Labels and `exception <label>` annotations are skipped — the
    identifier comes from the next `rule`/`definition` line.
    """
    kind = "definition"
    identifier = ""
    display_line = ""
    for raw in fence_lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("scope "):
            continue
        if _LABEL_RE.match(line) or _EXCEPTION_RE.match(line) and "definition" not in line and "rule" not in line:
            continue
        if not display_line:
            display_line = stripped
        m_rule = _RULE_RE.match(line)
        if m_rule:
            kind = "rule"
            identifier = m_rule.group(1)
            break
        m_def = _DEFINITION_RE.match(line)
        if m_def:
            kind = "definition"
            identifier = m_def.group(1)
            break
    return kind, identifier, display_line


def collect_items(source_text: str) -> list[Item]:
    """Walk a `.catala_en` source and return one Item per fenced block.

    State-machine pass: track the most recent `## Heading`, the most
    recent `*Source: ...*` line, and any pending `<!-- review: -->`
    block. When a fence opens, snapshot those into an Item; when the
    fence closes, append the Item.
    """
    lines = source_text.splitlines()
    items: list[Item] = []

    last_heading = ""
    last_source = ""
    pending_scores: Optional[dict[str, int]] = None
    pending_notes: Optional[str] = None
    pending_has_review = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        m_heading = _HEADING_RE.match(line)
        if m_heading:
            last_heading = m_heading.group(1).strip()
            i += 1
            continue

        m_source = _SOURCE_LINE_RE.match(line)
        if m_source:
            last_source = m_source.group(1).strip()
            i += 1
            continue

        if _REVIEW_OPEN_RE.match(line):
            body: list[str] = []
            i += 1
            while i < n and not _REVIEW_CLOSE_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # consume the closing -->
            pending_scores, pending_notes = _parse_review_block(body)
            pending_has_review = True
            continue

        if _FENCE_OPEN_RE.match(line):
            fence_body: list[str] = []
            i += 1
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                fence_body.append(lines[i])
                i += 1
            i += 1  # consume closing ```
            kind, ident, catala_str = _classify_fence(fence_body)
            if not ident:
                # No rule/definition inside this fence — likely a pure
                # declaration block (struct/enum/scope-declaration); skip.
                pending_scores = None
                pending_notes = None
                pending_has_review = False
                continue
            display_id = ident if kind == "rule" else f"computed: {ident}"
            items.append(Item(
                raw_id=ident,
                display_id=display_id,
                kind=kind,
                description=last_heading or "",
                scores=pending_scores if pending_has_review else None,
                source_str=last_source or "(no source)",
                catala_str=catala_str,
                notes=pending_notes,
            ))
            pending_scores = None
            pending_notes = None
            pending_has_review = False
            continue

        i += 1

    return items


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def _is_uncertain(scores: dict[str, int]) -> bool:
    return (
        scores["extraction_fidelity"] <= _FIDELITY_LOW
        or scores["source_clarity"] <= _CLARITY_LOW
    )


def _is_complex(scores: dict[str, int]) -> bool:
    return (
        scores["logic_complexity"] >= _LOGIC_HIGH
        or scores["policy_complexity"] >= _POLICY_HIGH
    )


def partition(items: list[Item]) -> dict[str, list[Item]]:
    buckets: dict[str, list[Item]] = {
        "uncertain": [],
        "complex": [],
        "verified": [],
        "unscored": [],
    }
    for item in items:
        if item.scores is None:
            buckets["unscored"].append(item)
            continue
        if _is_uncertain(item.scores):
            buckets["uncertain"].append(item)
            continue
        if _is_complex(item.scores):
            buckets["complex"].append(item)
            continue
        buckets["verified"].append(item)
    return buckets


# ---------------------------------------------------------------------------
# Body formatting
# ---------------------------------------------------------------------------


def _flagged_for_uncertain(scores: dict[str, int]) -> str:
    flags: list[str] = []
    if scores["extraction_fidelity"] <= _FIDELITY_LOW:
        flags.append('"low extraction fidelity"')
    if scores["source_clarity"] <= _CLARITY_LOW:
        flags.append('"low source clarity"')
    if scores["logic_complexity"] >= _LOGIC_HIGH:
        flags.append('"high logic complexity"')
    if scores["policy_complexity"] >= _POLICY_HIGH:
        flags.append('"high policy complexity"')
    return " and ".join(flags) if flags else "(none)"


def _flagged_for_complex(scores: dict[str, int]) -> str:
    flags: list[str] = []
    if scores["logic_complexity"] >= _LOGIC_HIGH:
        flags.append('"high logic complexity"')
    if scores["policy_complexity"] >= _POLICY_HIGH:
        flags.append('"high policy complexity"')
    return " and ".join(flags) if flags else "(none)"


def _format_scores_line(scores: dict[str, int]) -> str:
    return (
        f"fidelity:{scores['extraction_fidelity']} "
        f"clarity:{scores['source_clarity']} "
        f"logic:{scores['logic_complexity']} "
        f"policy:{scores['policy_complexity']}"
    )


def _format_item_block(item: Item, emoji: str, label: str, flagged_for: str) -> str:
    notes = item.notes if item.notes is not None else "(none)"
    return "\n".join(
        [
            _ITEM_SEPARATOR,
            f"{emoji}  {label}: {item.display_id}",
            f"    Scores: {_format_scores_line(item.scores or {})}",
            f"    Flagged for: {flagged_for}",
            f'    Policy: "{item.source_str}"',
            f"    Catala: {item.catala_str}",
            f"    Notes:  {notes}",
            _ITEM_SEPARATOR,
        ]
    )


def _format_compact_list(items: list[Item], heading: str) -> str:
    lines = [heading]
    for item in items:
        if item.description:
            lines.append(f"    • {item.display_id} — {item.description}")
        else:
            lines.append(f"    • {item.display_id}")
    return "\n".join(lines)


def _format_summary_header(buckets: dict[str, list[Item]], total: int) -> str:
    return (
        f"Review summary: {len(buckets['uncertain'])} uncertain, "
        f"{len(buckets['complex'])} complex, "
        f"{len(buckets['verified'])} verified  ({total} items total)"
    )


def _format_body(buckets: dict[str, list[Item]]) -> str:
    total = sum(len(b) for b in buckets.values())

    if total == 0:
        return _ALL_VERIFIED_MESSAGE
    if (
        not buckets["uncertain"]
        and not buckets["complex"]
        and not buckets["unscored"]
    ):
        return _ALL_VERIFIED_MESSAGE

    sections: list[str] = [_format_summary_header(buckets, total)]

    if buckets["uncertain"]:
        sections.append("")
        item_blocks = [
            _format_item_block(
                item,
                "⚠️",
                "UNCERTAIN",
                _flagged_for_uncertain(item.scores or {}),
            )
            for item in buckets["uncertain"]
        ]
        sections.append("\n".join(item_blocks))

    if buckets["complex"]:
        sections.append("")
        item_blocks = [
            _format_item_block(
                item,
                "🔍",
                "COMPLEX",
                _flagged_for_complex(item.scores or {}),
            )
            for item in buckets["complex"]
        ]
        sections.append("\n".join(item_blocks))

    if buckets["verified"]:
        sections.append("")
        heading = (
            f"✅  VERIFIED ({len(buckets['verified'])} items — "
            "not uncertain, not complex)"
        )
        sections.append(_format_compact_list(buckets["verified"], heading))

    if buckets["unscored"]:
        sections.append("")
        heading = (
            f"📝  UNSCORED ({len(buckets['unscored'])} items — "
            "review: block absent)"
        )
        sections.append(_format_compact_list(buckets["unscored"], heading))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# JSON header
# ---------------------------------------------------------------------------


def _format_json_header(buckets: dict[str, list[Item]]) -> str:
    total = sum(len(b) for b in buckets.values())
    payload = {
        "summary": {
            "uncertain": len(buckets["uncertain"]),
            "complex": len(buckets["complex"]),
            "verified": len(buckets["verified"]),
            "unscored": len(buckets["unscored"]),
            "total": total,
        },
        "item_ids": {
            "uncertain": [it.raw_id for it in buckets["uncertain"]],
            "complex": [it.raw_id for it in buckets["complex"]],
            "verified": [it.raw_id for it in buckets["verified"]],
            "unscored": [it.raw_id for it in buckets["unscored"]],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------


def run(domain_dir: Path, program: str) -> int:
    catala_path = domain_dir / "specs" / f"{program}.catala_en"
    if not catala_path.is_file():
        print(f"Catala source not found: {catala_path}", file=sys.stderr)
        return 2

    source_text = catala_path.read_text(encoding="utf-8")
    items = collect_items(source_text)
    buckets = partition(items)

    print(_format_json_header(buckets))
    print(_HEADER_SENTINEL)
    print(_format_body(buckets))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Partition a Catala ruleset's rules/definitions into "
            "Uncertain / Complex / Verified / Unscored buckets by their "
            "HTML-comment review: scores; emit a JSON header plus a "
            "formatted body for /review-ruleset Step 2."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "program",
        help="Program name — selects specs/<program>.catala_en",
    )
    args = parser.parse_args()

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        print(f"Domain not found: {domain_dir}", file=sys.stderr)
        return 2

    return run(domain_dir, args.program)


if __name__ == "__main__":
    sys.exit(main())
