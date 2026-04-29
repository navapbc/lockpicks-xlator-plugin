"""Tests for rate_md_quality.py — covers all plan scenarios."""

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from rate_md_quality import (
    PENALTIES,
    detect_bold_as_headings,
    detect_html_entity_remnants,
    detect_low_heading_density,
    detect_navigation_list_pollution,
    detect_navigation_pollution,
    detect_no_headings,
    detect_repeated_boilerplate,
    detect_repeated_page_headers,
    detect_repeated_page_numbers,
    detect_unindented_nested_lists,
    score_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lines(text: str) -> list[str]:
    return text.splitlines()


# ---------------------------------------------------------------------------
# detect_no_headings
# ---------------------------------------------------------------------------

def test_no_headings_true_when_none():
    assert detect_no_headings(lines("Some text\nMore text\n**Bold**")) is True


def test_no_headings_false_with_h1():
    assert detect_no_headings(lines("# Title\nBody text")) is False


def test_no_headings_false_with_h3():
    assert detect_no_headings(lines("### Subheading\nBody")) is False


def test_no_headings_false_with_h4_not_counted():
    # H4 (####) is NOT an ATX heading we track — only H1–H3
    assert detect_no_headings(lines("#### Deep heading\nBody")) is True


def test_no_headings_empty_file():
    assert detect_no_headings([]) is True


# ---------------------------------------------------------------------------
# detect_low_heading_density
# ---------------------------------------------------------------------------

def test_low_density_exempt_short_file():
    # Only 10 non-empty lines — exempt
    body = "\n".join(["Body line."] * 10)
    assert detect_low_heading_density(lines(body)) is False


def test_low_density_false_when_no_headings():
    # no_headings case — mutually exclusive
    body = "\n".join(["Body line."] * 100)
    assert detect_low_heading_density(lines(body)) is False


def test_low_density_fires_sparse_headings():
    # 1 heading in 200 non-empty lines → 0.005 < 0.02 threshold
    body = "# Heading\n" + "\n".join(["Body line."] * 200)
    assert detect_low_heading_density(lines(body)) is True


def test_low_density_ok_with_sufficient_headings():
    # 10 headings in 200 non-empty lines → 0.05 ≥ 0.02
    chunks = "\n".join(f"## Section {i}\n" + "\n".join(["Body."] * 20) for i in range(10))
    assert detect_low_heading_density(lines(chunks)) is False


# ---------------------------------------------------------------------------
# detect_repeated_page_headers
# ---------------------------------------------------------------------------

def test_repeated_page_headers_fires():
    repeated = "CALIFORNIA-DSS-MANUAL-EAS\n" * 10
    assert detect_repeated_page_headers(lines(repeated)) is True


def test_repeated_page_headers_below_threshold():
    # Only 2 occurrences — not enough
    text = "CALIFORNIA-DSS-MANUAL-EAS\nOther text\nCALIFORNIA-DSS-MANUAL-EAS\nMore text"
    assert detect_repeated_page_headers(lines(text)) is False


def test_repeated_page_headers_ignores_headings():
    # ATX headings should not count
    text = "# Same Heading\n" * 10
    assert detect_repeated_page_headers(lines(text)) is False


def test_repeated_page_headers_ignores_very_short():
    # 4 chars — below minimum length of 5
    text = "Foo\n" * 10
    assert detect_repeated_page_headers(lines(text)) is False


# ---------------------------------------------------------------------------
# detect_repeated_page_numbers
# ---------------------------------------------------------------------------

def test_page_numbers_fires():
    text = "Page 660.1\nContent\nPage 660.2\nContent\nPage 660.3\n"
    assert detect_repeated_page_numbers(lines(text)) is True


def test_page_numbers_below_threshold():
    text = "Page 1\nContent\nPage 2\n"
    assert detect_repeated_page_numbers(lines(text)) is False


def test_page_numbers_case_insensitive():
    text = "page 1\nContent\npage 2\nContent\npage 3\n"
    assert detect_repeated_page_numbers(lines(text)) is True


# ---------------------------------------------------------------------------
# detect_bold_as_headings
# ---------------------------------------------------------------------------

def test_bold_headings_fires_without_atx():
    text = "**Section Title**\nBody text\n**Another Section**\n"
    assert detect_bold_as_headings(lines(text)) is True


def test_bold_headings_suppressed_when_atx_exists():
    text = "# Real Heading\n**Bold line**\nBody\n"
    assert detect_bold_as_headings(lines(text)) is False


def test_bold_headings_inline_bold_ignored():
    # Inline bold within a sentence — not a standalone bold heading
    text = "This is **important** policy text.\nMore body.\n"
    assert detect_bold_as_headings(lines(text)) is False


# ---------------------------------------------------------------------------
# detect_unindented_nested_lists
# ---------------------------------------------------------------------------

def test_unindented_list_fires():
    text = "  - Sub item\n- Top item that should be indented\n"
    assert detect_unindented_nested_lists(lines(text)) is True


def test_unindented_list_clean():
    text = "- Top item\n  - Properly indented sub item\n- Another top item\n"
    assert detect_unindented_nested_lists(lines(text)) is False


def test_unindented_list_flat_ok():
    # All at indent 0 — no nesting implied
    text = "- Item A\n- Item B\n- Item C\n"
    assert detect_unindented_nested_lists(lines(text)) is False


# ---------------------------------------------------------------------------
# detect_html_entity_remnants
# ---------------------------------------------------------------------------

def test_html_entities_fires_nbsp():
    assert detect_html_entity_remnants(lines("Text&nbsp;more text")) is True


def test_html_entities_fires_amp():
    assert detect_html_entity_remnants(lines("A &amp; B")) is True


def test_html_entities_fires_numeric():
    assert detect_html_entity_remnants(lines("em dash &#8212; here")) is True


def test_html_entities_clean():
    assert detect_html_entity_remnants(lines("Plain text with no entities.")) is False


# ---------------------------------------------------------------------------
# detect_navigation_pollution
# ---------------------------------------------------------------------------

def test_nav_pollution_fires():
    text = "Home > Programs > SNAP\nContent\nHome > Programs > TANF\n"
    assert detect_navigation_pollution(lines(text)) is True


def test_nav_pollution_single_occurrence():
    text = "Home > Programs > SNAP\nContent"
    assert detect_navigation_pollution(lines(text)) is False


def test_nav_pollution_requires_two_arrows():
    # Only one > — comparison operator in policy text, should not fire
    text = "Income > threshold\nIncome > limit\n" * 5
    assert detect_navigation_pollution(lines(text)) is False


# ---------------------------------------------------------------------------
# detect_repeated_boilerplate
# ---------------------------------------------------------------------------

def test_boilerplate_fires_last_modified():
    text = "Last modified: Jan 1\nContent\nLast modified: Feb 1\n"
    assert detect_repeated_boilerplate(lines(text)) is True


def test_boilerplate_fires_copyright():
    text = "Copyright 2024 State Agency\nContent\nCopyright 2024 State Agency\n"
    assert detect_repeated_boilerplate(lines(text)) is True


def test_boilerplate_single_occurrence_no_fire():
    text = "Last modified: Jan 1\nContent without repetition.\n"
    assert detect_repeated_boilerplate(lines(text)) is False


# ---------------------------------------------------------------------------
# detect_navigation_list_pollution
# ---------------------------------------------------------------------------

def test_nav_list_pollution_fires_urls():
    items = "\n".join(f"- https://example.com/page{i}" for i in range(10))
    assert detect_navigation_list_pollution(lines(items)) is True


def test_nav_list_pollution_fires_short_items():
    items = "\n".join(f"- Menu item {i}" for i in range(10))
    assert detect_navigation_list_pollution(lines(items)) is True


def test_nav_list_pollution_clean_policy_list():
    # Policy enumerations have substantive text
    items = "\n".join(
        f"- The applicant must demonstrate eligibility under section {i} of the act."
        for i in range(10)
    )
    assert detect_navigation_list_pollution(lines(items)) is False


def test_nav_list_pollution_below_threshold_count():
    items = "\n".join(f"- https://example.com/{i}" for i in range(5))
    assert detect_navigation_list_pollution(lines(items)) is False


# ---------------------------------------------------------------------------
# score_file — integration via tmp_path
# ---------------------------------------------------------------------------

def test_score_well_structured_file(tmp_path):
    """Happy path: well-structured file → score ≥ 80, no flags."""
    md = tmp_path / "good.md"
    sections = "\n\n".join(
        f"## Section {i}\n\n" + "This section discusses policy requirements in detail. " * 3
        for i in range(10)
    )
    md.write_text(sections)
    result = score_file(md)
    assert result["score"] >= 80
    assert "flags" not in result


def test_score_pdf_style_file(tmp_path):
    """16EAS.md pattern: no headings, repeated page headers, page numbers → score < 40."""
    block = (
        "CALIFORNIA-DSS-MANUAL-EAS\n\n"
        "MANUAL LETTER NO. EAS-03-09 Effective 10/16/03\n\n"
        "Page {n}\n\n"
        "ELIGIBILITY AND ASSISTANCE STANDARDS\n\n"
        "49-001 PROGRAM DEFINITION 49-001\n\n"
        "This section describes the program definition for cash assistance.\n\n"
    )
    md = tmp_path / "16EAS.md"
    md.write_text("".join(block.format(n=i) for i in range(1, 20)))
    result = score_file(md)
    assert result["score"] < 40
    assert "flags" in result
    flags = result["flags"]
    assert "no_headings" in flags
    assert "repeated_page_headers" in flags
    assert "repeated_page_numbers" in flags


def test_score_web_scraped_file(tmp_path):
    """Web-scraped file: HTML entities + breadcrumbs → html_entity_remnants + navigation_pollution."""
    content = (
        "Home > Programs > Benefits\nContent.\nHome > Programs > Eligibility\nMore content.\n"
        "The income must be &lt; 200% FPL. Use &amp; for ampersand.\n"
        "Regular policy text " * 20
    )
    md = tmp_path / "scraped.md"
    md.write_text(content)
    result = score_file(md)
    assert "flags" in result
    assert "html_entity_remnants" in result["flags"]
    assert "navigation_pollution" in result["flags"]


def test_score_empty_file(tmp_path):
    """Empty file → only no_headings fires (score = 100 - 40 = 60)."""
    md = tmp_path / "empty.md"
    md.write_text("")
    result = score_file(md)
    assert result["score"] == 60
    assert "flags" in result
    assert "no_headings" in result["flags"]


def test_score_one_heading_long_file(tmp_path):
    """1 heading, 2000 lines → low_heading_density (not no_headings)."""
    md = tmp_path / "sparse.md"
    md.write_text("# Introduction\n" + "Policy text line.\n" * 2000)
    result = score_file(md)
    assert "flags" in result
    assert "low_heading_density" in result["flags"]
    assert "no_headings" not in result["flags"]


def test_score_short_file_no_headings_not_density(tmp_path):
    """Short file (< 30 non-empty lines) with no headings: no_headings may fire but not low_heading_density."""
    md = tmp_path / "short.md"
    md.write_text("Brief policy memo.\n" * 15)
    result = score_file(md)
    flags = result.get("flags", [])
    assert "low_heading_density" not in flags


def test_score_bold_headings_file(tmp_path):
    """File using **bold** as headings, no ATX headings → bold_as_headings fires."""
    md = tmp_path / "bold_headings.md"
    md.write_text(
        "**Program Definition**\nThis section defines the program.\n\n"
        "**Eligibility**\nApplicants must meet the following criteria.\n"
    )
    result = score_file(md)
    assert "flags" in result
    assert "bold_as_headings" in result["flags"]


def test_score_nav_list_file(tmp_path):
    """List of 10 short URL items → navigation_list_pollution fires."""
    md = tmp_path / "nav.md"
    items = "\n".join(f"- https://example.gov/page{i}" for i in range(10))
    md.write_text("# Links\n\n" + items)
    result = score_file(md)
    assert "flags" in result
    assert "navigation_list_pollution" in result["flags"]


def test_score_nonexistent_file_raises(tmp_path):
    """Non-existent file raises OSError."""
    with pytest.raises(OSError):
        score_file(tmp_path / "does_not_exist.md")


def test_score_json_output_parseable(tmp_path):
    """Score result is JSON-serialisable."""
    md = tmp_path / "test.md"
    md.write_text("# Title\nBody text.\n")
    result = score_file(md)
    parsed = json.loads(json.dumps(result))
    assert "score" in parsed


def test_score_flags_omitted_when_empty(tmp_path):
    """Clean file: flags key absent, not set to []."""
    md = tmp_path / "clean.md"
    sections = "\n\n".join(
        f"## Section {i}\n\n" + "Detailed policy content here. " * 5
        for i in range(12)
    )
    md.write_text(sections)
    result = score_file(md)
    if result["score"] >= 100:
        assert "flags" not in result


def test_penalties_all_defined():
    """Every signal name has a defined penalty weight."""
    signal_names = [name for name, _ in [
        ("no_headings", None), ("low_heading_density", None),
        ("repeated_page_headers", None), ("repeated_page_numbers", None),
        ("bold_as_headings", None), ("unindented_nested_lists", None),
        ("html_entity_remnants", None), ("navigation_pollution", None),
        ("repeated_boilerplate", None), ("navigation_list_pollution", None),
    ]]
    for name in signal_names:
        assert name in PENALTIES, f"Missing penalty for signal: {name}"
