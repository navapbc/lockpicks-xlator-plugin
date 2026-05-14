# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for review_buckets.py — bucket boundaries, both-buckets overlap,
unscored handling, empty-section omission, all-verified short-circuit,
identifier disambiguation, source/civil/notes rendering, and pre-flight
errors.

Run: uv run pytest xl-plugin/tools/test_review_buckets.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import review_buckets  # noqa: E402


_SENTINEL = "--- REVIEW-BUCKETS-HEADER-END ---"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_domain(tmp: Path, name: str = "test_dom") -> Path:
    domain = tmp / name
    (domain / "specs").mkdir(parents=True)
    return domain


def _write_civil(domain: Path, program: str, payload: dict) -> Path:
    path = domain / "specs" / f"{program}.civil.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def _scores(ef: int = 5, sc: int = 5, lc: int = 1, pc: int = 1) -> dict:
    return {
        "extraction_fidelity": ef,
        "source_clarity": sc,
        "logic_complexity": lc,
        "policy_complexity": pc,
    }


def _rule(
    rule_id: str,
    *,
    when: str = "true",
    description: str = "rule desc",
    source: str | dict | None = None,
    review: dict | None = None,
) -> dict:
    entry: dict = {
        "id": rule_id,
        "kind": "deny",
        "description": description,
        "when": when,
    }
    if source is not None:
        entry["source"] = source
    if review is not None:
        entry["review"] = review
    return entry


def _computed(
    *,
    expr: str | None = None,
    conditional: dict | None = None,
    description: str = "computed desc",
    source: str | dict | None = None,
    review: dict | None = None,
) -> dict:
    entry: dict = {"type": "money", "description": description}
    if expr is not None:
        entry["expr"] = expr
    if conditional is not None:
        entry["conditional"] = conditional
    if source is not None:
        entry["source"] = source
    if review is not None:
        entry["review"] = review
    return entry


def _capture_output(domain: Path, program: str) -> tuple[int, str, str]:
    """Invoke run() with stdout/stderr redirection so tests can assert
    against the captured strings. Returns (exit_code, stdout, stderr)."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = review_buckets.run(domain, program)
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _parse_stdout(stdout: str) -> tuple[dict, str]:
    """Split stdout into JSON header and body."""
    assert _SENTINEL in stdout
    head, _, body = stdout.partition(f"\n{_SENTINEL}\n")
    return json.loads(head), body


# ---------------------------------------------------------------------------
# Happy path — all four buckets populated
# ---------------------------------------------------------------------------


def test_happy_path_all_four_buckets():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R-UNSCORED-1", review=None),
                _rule("R-UNCERTAIN-1", review=_scores(ef=2, sc=5, lc=1, pc=1)),
                _rule("R-UNCERTAIN-2", review=_scores(ef=5, sc=2, lc=1, pc=1)),
                _rule("R-COMPLEX-1", review=_scores(ef=5, sc=5, lc=4, pc=1)),
                _rule("R-VERIFIED-1", review=_scores()),
                _rule("R-VERIFIED-2", review=_scores()),
                _rule("R-VERIFIED-3", review=_scores()),
            ],
            "computed": {
                "vc_field": _computed(expr="a + b", review=_scores()),
            },
        })

        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["summary"] == {
            "uncertain": 2,
            "complex": 1,
            "verified": 4,
            "unscored": 1,
            "total": 8,
        }
        assert header["item_ids"]["uncertain"] == ["R-UNCERTAIN-1", "R-UNCERTAIN-2"]
        assert header["item_ids"]["complex"] == ["R-COMPLEX-1"]
        assert header["item_ids"]["verified"] == [
            "R-VERIFIED-1",
            "R-VERIFIED-2",
            "R-VERIFIED-3",
            "vc_field",
        ]
        assert header["item_ids"]["unscored"] == ["R-UNSCORED-1"]

        # Summary header line — preserves two-space gap before paren.
        assert "Review summary: 2 uncertain, 1 complex, 4 verified  (8 items total)" in body
        # All section headers present.
        assert "⚠️  UNCERTAIN: R-UNCERTAIN-1" in body
        assert "🔍  COMPLEX: R-COMPLEX-1" in body
        assert "✅  VERIFIED (4 items" in body
        assert "📝  UNSCORED (1 items" in body
        # Computed display uses prefix.
        assert "computed: vc_field" in body


# ---------------------------------------------------------------------------
# Threshold boundaries
# ---------------------------------------------------------------------------


def test_uncertain_threshold_boundary():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(ef=2, sc=5)),  # uncertain
                _rule("R2", review=_scores(ef=3, sc=2)),  # uncertain
                _rule("R3", review=_scores(ef=3, sc=3)),  # verified
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, _ = _parse_stdout(stdout)
        assert header["item_ids"]["uncertain"] == ["R1", "R2"]
        assert header["item_ids"]["verified"] == ["R3"]


def test_complex_threshold_boundary():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(lc=4, pc=1)),  # complex
                _rule("R2", review=_scores(lc=1, pc=4)),  # complex
                _rule("R3", review=_scores(lc=3, pc=3)),  # verified
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, _ = _parse_stdout(stdout)
        assert header["item_ids"]["complex"] == ["R1", "R2"]
        assert header["item_ids"]["verified"] == ["R3"]


# ---------------------------------------------------------------------------
# Both-buckets suppression
# ---------------------------------------------------------------------------


def test_both_buckets_overlap_appears_only_under_uncertain():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R-BOTH", review=_scores(ef=2, sc=5, lc=4, pc=1)),
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["item_ids"]["uncertain"] == ["R-BOTH"]
        assert header["item_ids"]["complex"] == []
        # Flagged-for line carries both low-fidelity and high-logic flags.
        assert '"low extraction fidelity"' in body
        assert '"high logic complexity"' in body
        # The item appears only once.
        assert body.count("R-BOTH") == 1


# ---------------------------------------------------------------------------
# Empty-section omission
# ---------------------------------------------------------------------------


def test_empty_uncertain_section_omitted():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(lc=4)),  # complex
                _rule("R2", review=_scores()),  # verified
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "⚠️  UNCERTAIN" not in body
        assert "🔍  COMPLEX" in body
        assert "✅  VERIFIED" in body
        assert "📝  UNSCORED" not in body


def test_empty_complex_section_omitted():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(ef=2)),  # uncertain
                _rule("R2", review=_scores()),  # verified
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "⚠️  UNCERTAIN" in body
        assert "🔍  COMPLEX" not in body
        assert "✅  VERIFIED" in body


def test_all_verified_short_circuit():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule(f"R{i}", review=_scores()) for i in range(5)
            ],
            "computed": {
                "vc": _computed(expr="x", review=_scores()),
            },
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["summary"]["verified"] == 6
        assert body.strip() == "All items verified — no uncertain or complex items."


def test_empty_civil_yields_all_verified_message():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {})
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["summary"]["total"] == 0
        assert body.strip() == "All items verified — no uncertain or complex items."


def test_null_rules_list_treated_as_empty():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {"rules": None, "computed": None})
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["summary"]["total"] == 0
        assert body.strip() == "All items verified — no uncertain or complex items."


# ---------------------------------------------------------------------------
# Unscored bucket
# ---------------------------------------------------------------------------


def test_unscored_bucket_emitted_when_review_block_absent():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=None),
                _rule("R2", review=None),
                _rule("R3", review=_scores()),  # verified
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        assert header["summary"]["unscored"] == 2
        assert "📝  UNSCORED (2 items" in body


def test_unscored_section_omitted_when_all_scored():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(ef=2)),  # uncertain
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "📝  UNSCORED" not in body


# ---------------------------------------------------------------------------
# Identifier disambiguation
# ---------------------------------------------------------------------------


def test_rule_id_displayed_verbatim_computed_gets_prefix():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("FED-SNAP-DENY-001", review=_scores(ef=2)),
            ],
            "computed": {
                "net_income": _computed(expr="g - d", review=_scores(ef=2)),
            },
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        header, body = _parse_stdout(stdout)
        # JSON header carries raw IDs only — no display prefix.
        assert "FED-SNAP-DENY-001" in header["item_ids"]["uncertain"]
        assert "net_income" in header["item_ids"]["uncertain"]
        # Body displays computed with the literal prefix.
        assert "UNCERTAIN: FED-SNAP-DENY-001" in body
        assert "UNCERTAIN: computed: net_income" in body


# ---------------------------------------------------------------------------
# source_str / civil_str / notes rendering
# ---------------------------------------------------------------------------


def test_source_dict_with_file_and_section():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule(
                    "R1",
                    review=_scores(ef=2),
                    source={
                        "file": "input/policy_docs/foo.md",
                        "section": "7 CFR § 273.9(a) — Income",
                    },
                ),
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert (
            'Policy: "input/policy_docs/foo.md — 7 CFR § 273.9(a) — Income"'
            in body
        )


def test_source_string_pre_joined():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule(
                    "R1",
                    review=_scores(ef=2),
                    source="input/policy_docs/foo.md — Section 7",
                ),
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert 'Policy: "input/policy_docs/foo.md — Section 7"' in body


def test_source_absent_renders_no_source():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", review=_scores(ef=2)),  # no source key
            ],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert 'Policy: "(no source)"' in body


def test_civil_str_renders_when_for_rules_and_expr_for_computed():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [
                _rule("R1", when="gross_income > 1000", review=_scores(ef=2)),
            ],
            "computed": {
                "vc": _computed(expr="a + b", review=_scores(ef=2)),
            },
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "CIVIL:  gross_income > 1000" in body
        assert "CIVIL:  a + b" in body


def test_civil_str_renders_conditional_for_computed():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "computed": {
                "vc": _computed(
                    conditional={"if": "x > 0", "then": "a", "else": "b"},
                    review=_scores(ef=2),
                ),
            },
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "CIVIL:  if x > 0 then a else b" in body


def test_notes_rendered_when_present():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        review = _scores(ef=2)
        review["notes"] = "Score 2 due to complex citation lookup"
        _write_civil(domain, "p", {
            "rules": [_rule("R1", review=review)],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "Notes:  Score 2 due to complex citation lookup" in body


def test_notes_renders_none_when_absent():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [_rule("R1", review=_scores(ef=2))],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        _, body = _parse_stdout(stdout)
        assert "Notes:  (none)" in body


# ---------------------------------------------------------------------------
# Pre-flight failures (via main() subprocess + DOMAINS_FULLPATH)
# ---------------------------------------------------------------------------


def _run_main(args: list[str], env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    here = Path(__file__).resolve().parent
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["uv", "run", str(here / "review_buckets.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_preflight_missing_domain_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_main(
            ["nonexistent_dom", "p"],
            env_overrides={"DOMAINS_FULLPATH": tmp},
        )
        assert result.returncode == 2
        assert "Domain not found" in result.stderr


def test_preflight_missing_civil_exits_2():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        _make_domain(tmp)
        result = _run_main(
            ["test_dom", "missing_program"],
            env_overrides={"DOMAINS_FULLPATH": str(tmp)},
        )
        assert result.returncode == 2
        assert "CIVIL file not found" in result.stderr


def test_preflight_missing_domains_fullpath_exits_2():
    env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    here = Path(__file__).resolve().parent
    result = subprocess.run(
        ["uv", "run", str(here / "review_buckets.py"), "any", "p"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "DOMAINS_FULLPATH" in result.stderr


# ---------------------------------------------------------------------------
# Output transport (sentinel divider + JSON parses)
# ---------------------------------------------------------------------------


def test_output_transport_shape():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        domain = _make_domain(tmp)
        _write_civil(domain, "p", {
            "rules": [_rule("R1", review=_scores())],
        })
        rc, stdout, _ = _capture_output(domain, "p")
        assert rc == 0
        lines = stdout.splitlines()
        # Line 1 is JSON, line 2 is sentinel, rest is body.
        header = json.loads(lines[0])
        assert lines[1] == _SENTINEL
        assert header["summary"]["total"] == 1
