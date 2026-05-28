#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator review-buckets: deterministic Step-2 bucket partitioning for
`/review-ruleset`.

Loads `specs/<program>.civil.yaml`, walks every `rules:` and `computed:`
entry, partitions them by their `review:` scores, and emits a formatted
analyst-facing review block plus a single-line JSON header the skill
parses to populate its summary.

Partitioning (priorities applied top-down):
  Unscored  — `review:` block absent.
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
    2 — pre-flight failure (missing domain, missing CIVIL file,
        unset DOMAINS_FULLPATH)
    1 — unexpected error (YAML parse failure, IO error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

_HEADER_SENTINEL = "--- REVIEW-BUCKETS-HEADER-END ---"

# 65-dash separator between per-item blocks. Matches the skill's prose.
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
        "civil_str",
        "notes",
    )

    def __init__(
        self,
        raw_id: str,
        display_id: str,
        kind: str,
        description: str,
        scores: dict[str, int] | None,
        source_str: str,
        civil_str: str,
        notes: str | None,
    ) -> None:
        self.raw_id = raw_id
        self.display_id = display_id
        self.kind = kind  # "rule" | "computed"
        self.description = description
        self.scores = scores
        self.source_str = source_str
        self.civil_str = civil_str
        self.notes = notes


def _render_source(source: Any) -> str:
    """Format a `source:` field. Accepts string (pre-joined) or dict."""
    if isinstance(source, str) and source:
        return source
    if isinstance(source, dict):
        file_v = source.get("file")
        section_v = source.get("section")
        file_s = str(file_v) if isinstance(file_v, str) and file_v else None
        section_s = str(section_v) if isinstance(section_v, str) and section_v else None
        if file_s and section_s:
            return f"{file_s} — {section_s}"
        if section_s:
            return section_s
        if file_s:
            return file_s
    return "(no source)"


def _render_conditional(node: dict[str, Any]) -> str:
    """Render a `conditional:` block as `if <if> then <then> else <else>`."""
    cond_if = node.get("if", "")
    cond_then = node.get("then", "")
    cond_else = node.get("else", "")
    return f"if {cond_if} then {cond_then} else {cond_else}"


def _render_civil_for_rule(entry: dict[str, Any]) -> str:
    when = entry.get("when")
    if isinstance(when, list):
        return " AND ".join(str(w) for w in when)
    if isinstance(when, str):
        return when
    return ""


def _render_civil_for_computed(entry: dict[str, Any]) -> str:
    expr = entry.get("expr")
    if isinstance(expr, str) and expr:
        return expr
    conditional = entry.get("conditional")
    if isinstance(conditional, dict):
        return _render_conditional(conditional)
    return ""


def _coerce_scores(review_block: Any) -> dict[str, int] | None:
    """Return a dict of the four score fields if a review block is present;
    None if the review block is absent. Missing individual score fields
    default to 3 (mid-range) — surfaces partially-scored entries without
    forcing them into Unscored."""
    if not isinstance(review_block, dict):
        return None
    scores: dict[str, int] = {}
    for field in _SCORE_FIELDS:
        v = review_block.get(field)
        if isinstance(v, int):
            scores[field] = v
        else:
            scores[field] = 3
    return scores


def _coerce_notes(review_block: Any) -> str | None:
    if not isinstance(review_block, dict):
        return None
    notes = review_block.get("notes")
    if isinstance(notes, str) and notes.strip():
        return notes
    return None


def _normalize_rule(entry: dict[str, Any]) -> Item:
    raw_id = str(entry.get("id", "<unidentified-rule>"))
    description = str(entry.get("description", "") or "")
    review_block = entry.get("review")
    return Item(
        raw_id=raw_id,
        display_id=raw_id,
        kind="rule",
        description=description,
        scores=_coerce_scores(review_block),
        source_str=_render_source(entry.get("source")),
        civil_str=_render_civil_for_rule(entry),
        notes=_coerce_notes(review_block),
    )


def _normalize_computed(name: str, entry: dict[str, Any]) -> Item:
    description = str(entry.get("description", "") or "")
    review_block = entry.get("review")
    return Item(
        raw_id=name,
        display_id=f"computed: {name}",
        kind="computed",
        description=description,
        scores=_coerce_scores(review_block),
        source_str=_render_source(entry.get("source")),
        civil_str=_render_civil_for_computed(entry),
        notes=_coerce_notes(review_block),
    )


def _collect_items(civil_data: dict[str, Any]) -> list[Item]:
    """Walk `rules:` (source order) then `computed:` (alphabetical) and
    return normalized Item records."""
    items: list[Item] = []

    rules = civil_data.get("rules")
    if isinstance(rules, list):
        for entry in rules:
            if isinstance(entry, dict):
                items.append(_normalize_rule(entry))

    computed = civil_data.get("computed")
    if isinstance(computed, dict):
        for name in sorted(computed.keys()):
            entry = computed[name]
            if isinstance(entry, dict):
                items.append(_normalize_computed(str(name), entry))

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


def _partition(items: list[Item]) -> dict[str, list[Item]]:
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
            f"    CIVIL:  {item.civil_str}",
            f"    Notes:  {notes}",
            _ITEM_SEPARATOR,
        ]
    )


def _format_compact_list(items: list[Item], heading: str) -> str:
    lines = [heading]
    for item in items:
        if item.kind == "rule":
            if item.description:
                lines.append(f"    • {item.display_id}: {item.description}")
            else:
                lines.append(f"    • {item.display_id}")
        else:
            if item.description:
                lines.append(f"    • {item.display_id} — {item.description}")
            else:
                lines.append(f"    • {item.display_id}")
    return "\n".join(lines)


def _format_summary_header(buckets: dict[str, list[Item]], total: int) -> str:
    # Preserve the two-space gap before the parenthetical — see R6.
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
    civil_path = domain_dir / "specs" / f"{program}.civil.yaml"
    if not civil_path.is_file():
        print(f"CIVIL file not found: {civil_path}", file=sys.stderr)
        return 2

    with civil_path.open(encoding="utf-8") as f:
        civil_data = yaml.safe_load(f) or {}

    if not isinstance(civil_data, dict):
        print(
            f"Unexpected CIVIL file shape (expected top-level mapping): {civil_path}",
            file=sys.stderr,
        )
        return 1

    items = _collect_items(civil_data)
    buckets = _partition(items)

    header_json = _format_json_header(buckets)
    body = _format_body(buckets)

    print(header_json)
    print(_HEADER_SENTINEL)
    print(body)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Partition a CIVIL ruleset's rules/computed entries into "
            "Uncertain / Complex / Verified / Unscored buckets by their "
            "review: scores; emit a JSON header plus a formatted body "
            "for /review-ruleset Step 2."
        )
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "program",
        help="Program name — selects specs/<program>.civil.yaml",
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
