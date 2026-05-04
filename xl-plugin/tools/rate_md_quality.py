#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# ///
"""
Markdown Quality Rater

Heuristically scores markdown quality (0–100) and returns named signal flags
indicating structural problems from PDF conversion or web scraping.

Usage (via xlator CLI):
    xlator rate-md-quality <path-to-markdown-file>
    xlator rate-md-quality <path-to-markdown-file> --format text

Options:
    --format {json,text}   Output format (default: json)

Output (JSON format):
    Score only — no structural problems detected:
        {"score": 85}

    Score with flags — each flag name identifies a signal that fired and
    reduced the score by its penalty weight:
        {"score": 20, "flags": ["no_headings", "repeated_page_headers", "repeated_page_numbers", "bold_as_headings"]}

    Score of 0 — multiple signals fired whose combined penalty reached 100:
        {"score": 0, "flags": ["no_headings", "repeated_page_headers", "repeated_page_numbers", "navigation_pollution", "repeated_boilerplate"]}

Output (text format, printed to stdout):
    16EAS.md  score: 20/100
      • no_headings  (-40)
      • repeated_page_headers  (-20)
      • repeated_page_numbers  (-10)
      • bold_as_headings  (-10)

Signal flags and their penalty weights:
    no_headings               -40   file has no ATX headings (# / ## / ###)
    low_heading_density       -20   fewer than 1 heading per 50 non-empty lines
    repeated_page_headers     -20   a non-heading line appears verbatim 3+ times
    navigation_pollution      -15   breadcrumb lines (A > B > C) appear 2+ times
    navigation_list_pollution -15   list block with 8+ URL-heavy or short items
    html_entity_remnants      -10   unescaped HTML entities (&amp;, &nbsp;, etc.)
    repeated_page_numbers     -10   "Page N" lines appear 3+ times
    bold_as_headings          -10   **bold** lines used structurally, no ATX headings
    repeated_boilerplate      -10   site-template phrases appear 2+ times
    unindented_nested_lists    -5   list block starts with indented items (no root)

Exit codes:
    0 — success
    1 — error (file not found, not a file, or unreadable)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from rich.console import Console as _Console
    _RICH = True
except ImportError:
    _RICH = False


# ---------------------------------------------------------------------------
# Signal penalty weights — clamped sum, final score in [0, 100]
# ---------------------------------------------------------------------------

PENALTIES: dict[str, int] = {
    "no_headings": 40,
    "low_heading_density": 20,
    "repeated_page_headers": 20,
    "repeated_page_numbers": 10,
    "bold_as_headings": 10,
    "unindented_nested_lists": 5,
    "html_entity_remnants": 10,
    "navigation_pollution": 15,
    "navigation_list_pollution": 15,
    "repeated_boilerplate": 10,
}

# Files with fewer non-empty lines than this are exempt from low_heading_density.
_DENSITY_MIN_LINES = 30

# 1 heading per this many non-empty lines is the minimum acceptable ratio.
_DENSITY_THRESHOLD = 1 / 50  # < 0.02 headings/line fires the signal


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_ATX_RE = re.compile(r"^#{1,3} ")
_PAGE_NUM_RE = re.compile(r"^\s*[Pp]age\s+\d+(\.\d+)?\s*$")
_BOLD_LINE_RE = re.compile(r"^\s*\*\*[^*\n]+\*\*\s*$")
_HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|[a-zA-Z]{2,6});")
# Breadcrumb: at least two " > " separators, whole line
_BREADCRUMB_RE = re.compile(r"^[\w][\w\s,().'-]*(?:\s*>\s*[\w][\w\s,().'-]*){2,}\s*$")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_LIST_RE = re.compile(r"^(\s*)-\s")

_BOILERPLATE = [
    re.compile(r"\blast\s+modified\b", re.IGNORECASE),
    re.compile(r"\bwas this (?:page|article) helpful\b", re.IGNORECASE),
    re.compile(r"\bshare this (?:page|article)\b", re.IGNORECASE),
    re.compile(r"\bprint this (?:page|article)\b", re.IGNORECASE),
    re.compile(r"\bcopyright\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\ball rights reserved\b", re.IGNORECASE),
    re.compile(r"\bskip to (?:main\s+)?content\b", re.IGNORECASE),
    re.compile(r"\bback to top\b", re.IGNORECASE),
    re.compile(r"\bsubscribe to (?:our )?newsletter\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Detectors — each accepts list[str] and returns bool
# ---------------------------------------------------------------------------

def detect_no_headings(lines: list[str]) -> bool:
    return not any(_ATX_RE.match(ln) for ln in lines)


def detect_low_heading_density(lines: list[str]) -> bool:
    non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) < _DENSITY_MIN_LINES:
        return False
    headings = sum(1 for ln in lines if _ATX_RE.match(ln))
    if headings == 0:
        return False  # covered by no_headings — mutually exclusive
    return (headings / len(non_empty)) < _DENSITY_THRESHOLD


def detect_repeated_page_headers(lines: list[str]) -> bool:
    """Non-heading lines 5–99 chars that appear verbatim 3+ times."""
    candidates = [
        ln.strip()
        for ln in lines
        if ln.strip()
        and not _ATX_RE.match(ln)
        and 5 <= len(ln.strip()) < 100
    ]
    counts = Counter(candidates)
    repeated = [text for text, count in counts.items() if count >= 3]
    if repeated:
        print(f"[detect_repeated_page_headers] repeated: {repeated}", file=sys.stderr)
    return bool(repeated)


def detect_repeated_page_numbers(lines: list[str]) -> bool:
    return sum(1 for ln in lines if _PAGE_NUM_RE.match(ln)) >= 3


def detect_bold_as_headings(lines: list[str]) -> bool:
    """Standalone **bold** lines used structurally when no ATX headings exist."""
    if any(_ATX_RE.match(ln) for ln in lines):
        return False
    return any(_BOLD_LINE_RE.match(ln) for ln in lines)


def detect_unindented_nested_lists(lines: list[str]) -> bool:
    """An indent-0 list item following an indented item when the block never had a root item.

    Valid Markdown allows returning to indent=0 after a sub-list when a root
    item preceded it. The problem pattern is a block that *starts* with indented
    items — those items look nested but have nothing to be nested under.
    """
    prev_indent: int | None = None
    seen_root_in_block = False
    for ln in lines:
        m = _LIST_RE.match(ln)
        if m:
            indent = len(m.group(1))
            if indent == 0:
                if prev_indent is not None and prev_indent > 0 and not seen_root_in_block:
                    return True
                seen_root_in_block = True
            prev_indent = indent
        elif ln.strip():
            prev_indent = None
            seen_root_in_block = False
    return False


def detect_html_entity_remnants(lines: list[str]) -> bool:
    return any(_HTML_ENTITY_RE.search(ln) for ln in lines)


def detect_navigation_pollution(lines: list[str]) -> bool:
    """Breadcrumb-style A > B > C lines appearing 2+ times."""
    return sum(1 for ln in lines if _BREADCRUMB_RE.match(ln.strip())) >= 2


def detect_repeated_boilerplate(lines: list[str]) -> bool:
    text = "\n".join(lines)
    return any(len(pat.findall(text)) >= 2 for pat in _BOILERPLATE)


def detect_navigation_list_pollution(lines: list[str]) -> bool:
    """List blocks with 8+ items where ≥50% contain URLs or ≥70% are very short."""

    def _nav_block(block: list[str]) -> bool:
        if len(block) < 8:
            return False
        url_hits = sum(1 for ln in block if _URL_RE.search(ln))
        short_hits = sum(1 for ln in block if len(ln.strip()) < 40)
        return (url_hits / len(block)) >= 0.5 or (short_hits / len(block)) >= 0.7

    block: list[str] = []
    for ln in lines:
        if _LIST_RE.match(ln):
            block.append(ln)
        else:
            if _nav_block(block):
                return True
            block = []
    return _nav_block(block)


# ---------------------------------------------------------------------------
# Ordered detector table — no_headings must precede low_heading_density
# ---------------------------------------------------------------------------

_DETECTORS: list[tuple[str, object]] = [
    ("no_headings", detect_no_headings),
    ("low_heading_density", detect_low_heading_density),
    ("repeated_page_headers", detect_repeated_page_headers),
    ("repeated_page_numbers", detect_repeated_page_numbers),
    ("bold_as_headings", detect_bold_as_headings),
    ("unindented_nested_lists", detect_unindented_nested_lists),
    ("html_entity_remnants", detect_html_entity_remnants),
    ("navigation_pollution", detect_navigation_pollution),
    ("repeated_boilerplate", detect_repeated_boilerplate),
    ("navigation_list_pollution", detect_navigation_list_pollution),
]


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def score_file(path: Path) -> dict[str, object]:
    """Return {"score": int} or {"score": int, "flags": [str, ...]}."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    fired: list[str] = []
    no_headings_fired = False

    for name, detector in _DETECTORS:
        if name == "low_heading_density" and no_headings_fired:
            continue
        if detector(lines):  # type: ignore[operator]
            fired.append(name)
            if name == "no_headings":
                no_headings_fired = True

    score = max(0, 100 - sum(PENALTIES[s] for s in fired))
    result: dict[str, object] = {"score": score}
    if fired:
        result["flags"] = fired
    return result


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def _print_text(path: Path, result: dict[str, object]) -> None:
    score = result["score"]
    flags = result.get("flags", [])
    if _RICH:
        console = _Console()
        color = "green" if int(score) >= 40 else "red"
        console.print(f"[bold]{path.name}[/bold]  score: [{color}]{score}/100[/{color}]")
        if flags:
            for flag in flags:  # type: ignore[union-attr]
                penalty = PENALTIES.get(str(flag), 0)
                console.print(f"  [yellow]•[/yellow] {flag}  (-{penalty})")
        else:
            console.print("  [green]No issues detected[/green]")
    else:
        print(f"{path.name}: score={score}/100")
        for flag in flags:  # type: ignore[union-attr]
            print(f"  - {flag}  (-{PENALTIES.get(str(flag), 0)})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rate markdown quality (0–100) with named signal flags."
    )
    parser.add_argument("file", help="Path to the markdown file to rate")
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if not path.is_file():
        print(f"Error: not a regular file: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = score_file(path)
    except OSError as exc:
        print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(result))
    else:
        _print_text(path, result)


if __name__ == "__main__":
    main()
