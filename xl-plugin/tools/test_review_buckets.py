# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Tests for review_buckets.py.

Exercises:
- HTML-comment review-block parsing on `.catala_en` source
- Bucket boundaries (Uncertain / Complex / Verified / Unscored)
- Cross-bucket overlap
- Output transport shape (JSON header + sentinel + body)
- Pre-flight failures

Run: uv run pytest xl-plugin/tools/test_review_buckets.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import review_buckets  # noqa: E402


_SENTINEL = "--- REVIEW-BUCKETS-HEADER-END ---"


# ---------------------------------------------------------------------------
# Catala-source fixture builders
# ---------------------------------------------------------------------------


def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "specs").mkdir(parents=True)
    return domain


def _write_catala(domain: Path, program: str, source: str) -> Path:
    path = domain / "specs" / f"{program}.catala_en"
    path.write_text(source)
    return path


def _review_block(ef: int = 5, sc: int = 5, lc: int = 1, pc: int = 1,
                  notes: str | None = None) -> str:
    """Render an HTML review comment with the four score fields."""
    lines = [
        "<!-- review:",
        f"       extraction_fidelity: {ef}",
        f"       source_clarity: {sc}",
        f"       logic_complexity: {lc}",
        f"       policy_complexity: {pc}",
    ]
    if notes is not None:
        lines.append(f'       notes: "{notes}"')
    lines.append("-->")
    return "\n".join(lines)


def _rule_block(
    name: str,
    *,
    heading: str | None = None,
    source: str | None = None,
    review: str | None = None,
    when: str = "true",
) -> str:
    parts: list[str] = []
    if heading is not None:
        parts.append(f"## {heading}\n")
    if source is not None:
        parts.append(f"*Source: {source}*\n")
    if review is not None:
        parts.append(review + "\n")
    parts.append("```catala")
    parts.append("scope EligibilityDecision:")
    parts.append(f"  rule {name}")
    parts.append(f"    under condition {when}")
    parts.append("    consequence fulfilled")
    parts.append("```\n")
    return "\n".join(parts)


def _definition_block(
    name: str,
    *,
    heading: str | None = None,
    source: str | None = None,
    review: str | None = None,
    expr: str = "$1,000",
) -> str:
    parts: list[str] = []
    if heading is not None:
        parts.append(f"## {heading}\n")
    if source is not None:
        parts.append(f"*Source: {source}*\n")
    if review is not None:
        parts.append(review + "\n")
    parts.append("```catala")
    parts.append("scope EligibilityDecision:")
    parts.append(f"  definition {name} equals {expr}")
    parts.append("```\n")
    return "\n".join(parts)


def _module_prelude() -> str:
    return "> Module Eligibility\n\n# Synthetic test program\n\n"


def _capture_output(domain: Path, program: str) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = review_buckets.run(domain, program)
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _parse_stdout(stdout: str) -> tuple[dict, str]:
    assert _SENTINEL in stdout
    head, _, body = stdout.partition(f"\n{_SENTINEL}\n")
    return json.loads(head), body


# ---------------------------------------------------------------------------
# Review-block parsing
# ---------------------------------------------------------------------------


class TestReviewBlockParsing:
    def test_parses_all_four_score_fields(self):
        source = _module_prelude() + _rule_block(
            "r1",
            heading="Rule one",
            source="policy.md — Section 1",
            review=_review_block(ef=3, sc=4, lc=2, pc=5),
        )
        items = review_buckets.collect_items(source)
        assert len(items) == 1
        assert items[0].raw_id == "r1"
        assert items[0].scores == {
            "extraction_fidelity": 3,
            "source_clarity": 4,
            "logic_complexity": 2,
            "policy_complexity": 5,
        }

    def test_missing_review_block_yields_none(self):
        source = _module_prelude() + _rule_block("r1", heading="No review here")
        items = review_buckets.collect_items(source)
        assert len(items) == 1
        assert items[0].scores is None

    def test_notes_string_is_captured(self):
        source = _module_prelude() + _rule_block(
            "r1",
            review=_review_block(notes="ambiguous source text"),
        )
        items = review_buckets.collect_items(source)
        assert items[0].notes == "ambiguous source text"

    def test_heading_propagates_as_description(self):
        source = _module_prelude() + _rule_block(
            "r1", heading="Gross income test",
            review=_review_block(),
        )
        items = review_buckets.collect_items(source)
        assert items[0].description == "Gross income test"

    def test_source_line_propagates(self):
        source = _module_prelude() + _rule_block(
            "r1", source="policy.md — § 273.9(a)",
            review=_review_block(),
        )
        items = review_buckets.collect_items(source)
        assert items[0].source_str == "policy.md — § 273.9(a)"

    def test_definition_kind_classified(self):
        source = _module_prelude() + _definition_block(
            "income_limit", review=_review_block(),
        )
        items = review_buckets.collect_items(source)
        assert items[0].kind == "definition"
        assert items[0].display_id == "computed: income_limit"

    def test_rule_kind_classified(self):
        source = _module_prelude() + _rule_block("r1", review=_review_block())
        items = review_buckets.collect_items(source)
        assert items[0].kind == "rule"
        assert items[0].display_id == "r1"

    def test_declaration_only_fence_is_skipped(self):
        source = _module_prelude() + textwrap.dedent("""\
            ## Declarations

            ```catala-metadata
            declaration structure Household:
              data size content integer
            ```
            """)
        items = review_buckets.collect_items(source)
        assert items == []


# ---------------------------------------------------------------------------
# Bucket partitioning
# ---------------------------------------------------------------------------


class TestPartitioning:
    def test_verified_when_scores_high_and_low(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=5, sc=5, lc=1, pc=1))
        )
        b = review_buckets.partition(items)
        assert [i.raw_id for i in b["verified"]] == ["r1"]
        assert not b["uncertain"]
        assert not b["complex"]

    def test_uncertain_on_low_fidelity(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=2, sc=5, lc=1, pc=1))
        )
        assert review_buckets.partition(items)["uncertain"][0].raw_id == "r1"

    def test_uncertain_on_low_clarity(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=5, sc=2, lc=1, pc=1))
        )
        assert review_buckets.partition(items)["uncertain"][0].raw_id == "r1"

    def test_complex_on_high_logic(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=5, sc=5, lc=4, pc=1))
        )
        assert review_buckets.partition(items)["complex"][0].raw_id == "r1"

    def test_complex_on_high_policy(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=5, sc=5, lc=1, pc=4))
        )
        assert review_buckets.partition(items)["complex"][0].raw_id == "r1"

    def test_uncertain_takes_priority_over_complex(self):
        # Low fidelity AND high logic — appears only in Uncertain.
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1", review=_review_block(ef=2, sc=5, lc=5, pc=5))
        )
        b = review_buckets.partition(items)
        assert [i.raw_id for i in b["uncertain"]] == ["r1"]
        assert not b["complex"]

    def test_unscored_when_review_block_absent(self):
        items = review_buckets.collect_items(
            _module_prelude() + _rule_block("r1")
        )
        b = review_buckets.partition(items)
        assert [i.raw_id for i in b["unscored"]] == ["r1"]

    def test_mixed_four_bucket_population(self):
        source = _module_prelude() + "\n".join([
            _rule_block("verified_r", review=_review_block(ef=5, sc=5, lc=1, pc=1)),
            _rule_block("uncertain_r", review=_review_block(ef=2, sc=5, lc=1, pc=1)),
            _rule_block("complex_r", review=_review_block(ef=5, sc=5, lc=4, pc=2)),
            _rule_block("unscored_r"),
        ])
        b = review_buckets.partition(review_buckets.collect_items(source))
        assert [i.raw_id for i in b["verified"]] == ["verified_r"]
        assert [i.raw_id for i in b["uncertain"]] == ["uncertain_r"]
        assert [i.raw_id for i in b["complex"]] == ["complex_r"]
        assert [i.raw_id for i in b["unscored"]] == ["unscored_r"]


# ---------------------------------------------------------------------------
# Output transport
# ---------------------------------------------------------------------------


class TestOutputTransport:
    def test_json_header_then_sentinel_then_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp))
            _write_catala(domain, "elig", _module_prelude() + _rule_block(
                "r1", review=_review_block(ef=2, sc=5, lc=1, pc=1)
            ))
            rc, out, _ = _capture_output(domain, "elig")
        assert rc == 0
        header, body = _parse_stdout(out)
        assert header["summary"]["uncertain"] == 1
        assert "UNCERTAIN: r1" in body

    def test_all_verified_message_when_no_uncertain_complex_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp))
            _write_catala(domain, "elig", _module_prelude() + _rule_block(
                "r1", review=_review_block(ef=5, sc=5, lc=1, pc=1)
            ))
            rc, out, _ = _capture_output(domain, "elig")
        _, body = _parse_stdout(out)
        assert "All items verified" in body

    def test_empty_source_yields_all_verified_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = _make_domain(Path(tmp))
            _write_catala(domain, "elig", _module_prelude())
            rc, out, _ = _capture_output(domain, "elig")
        assert rc == 0
        _, body = _parse_stdout(out)
        assert "All items verified" in body


# ---------------------------------------------------------------------------
# Pre-flight failures (live subprocess)
# ---------------------------------------------------------------------------


def _run_main(args: list[str], env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    here = Path(__file__).resolve().parent
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        ["uv", "run", str(here / "review_buckets.py"), *args],
        env=env, capture_output=True, text=True, check=False,
    )


class TestPreflight:
    def test_missing_domain_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_main(["nope", "elig"], {"DOMAINS_FULLPATH": tmp})
        assert r.returncode == 2

    def test_missing_catala_source_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain = Path(tmp) / "test_dom" / "specs"
            domain.mkdir(parents=True)
            r = _run_main(["test_dom", "elig"], {"DOMAINS_FULLPATH": tmp})
        assert r.returncode == 2

    def test_missing_domains_fullpath_exits_2(self):
        env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
        here = Path(__file__).resolve().parent
        r = subprocess.run(
            ["uv", "run", str(here / "review_buckets.py"), "any", "p"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert r.returncode == 2
