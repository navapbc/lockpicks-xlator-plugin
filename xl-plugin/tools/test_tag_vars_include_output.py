# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for tag_vars_include_output.py — covers detection passes, merge
order, idempotence, atomicity, and pre-flight error paths."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import tag_vars_include_output as tvio  # noqa: E402


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_domain(
    tmp: Path,
    name: str = "test_dom",
    *,
    manifest: dict | None = None,
    skeleton: dict | None = None,
    ruleset_modules: dict | None = None,
    sample_artifacts: dict | None = None,
    existing: list | None = None,
) -> Path:
    """Build a minimal domain tree under `tmp/<name>/`. Every input file is
    optional except the naming manifest (pre-flight requires it). Pass
    `manifest=None` to test the missing-manifest pre-flight case."""
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        _write_yaml(domain / "specs" / "naming-manifest.yaml", manifest)
    if skeleton is not None:
        _write_yaml(domain / "specs" / "guidance" / "skeleton.yaml", skeleton)
    if ruleset_modules is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "ruleset-modules.yaml",
            ruleset_modules,
        )
    if sample_artifacts is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "sample-artifacts.yaml",
            sample_artifacts,
        )
    if existing is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "include-with-output.yaml",
            existing,
        )
    return domain


def _read_output(domain: Path) -> list[str]:
    path = domain / "specs" / "guidance" / "include-with-output.yaml"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or []


def _run_tool(tmp_path: Path, domain: str) -> tuple[int, str, str]:
    """Invoke the script as a subprocess (exercises main() + argparse +
    env-var pre-flight). Returns (returncode, stdout, stderr)."""
    script = Path(__file__).parent / "tag_vars_include_output.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    proc = subprocess.run(
        ["uv", "run", str(script), domain],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Pass 1 — skeleton dot-notation
# ---------------------------------------------------------------------------

def test_pass1_extracts_dot_notation_base_name():
    """skeleton.computations[*].exprs values: dot-notation LHS is surfaced."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            skeleton={
                "skeleton": {
                    "computations": [
                        {
                            "stage": "s1",
                            "exprs": {
                                "adjusted": "client_result.adjusted_earned_income",
                            },
                        },
                    ],
                },
            },
        )
        tvio.run(domain)
        assert _read_output(domain) == ["client_result"]


def test_pass1_ignores_bare_identifiers():
    """No-dot expressions contribute nothing from Pass 1."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            skeleton={
                "skeleton": {
                    "computations": [
                        {"exprs": {"x": "a + b * c"}},
                    ],
                },
            },
        )
        tvio.run(domain)
        assert _read_output(domain) == []


def test_pass1_captures_multiple_bases_in_one_expression():
    """Each dot-notation occurrence in one expression yields its base."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            skeleton={
                "skeleton": {
                    "computations": [
                        {"exprs": {
                            "x": "client_result.gross + dol_result.gross",
                        }},
                    ],
                },
            },
        )
        tvio.run(domain)
        assert _read_output(domain) == ["client_result", "dol_result"]


# ---------------------------------------------------------------------------
# Pass 2a — civil-snippet dot-notation
# ---------------------------------------------------------------------------

def test_pass2a_extracts_dot_notation_from_ruleset_modules():
    civil = (
        "computed:\n"
        "  - name: x\n"
        "    expr: dol_result.gross_earned\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            ruleset_modules={
                "ruleset_modules": [
                    {
                        "name": "m1",
                        "sample_rules": [
                            {"id": "r1", "civil": civil},
                        ],
                    },
                ],
            },
        )
        tvio.run(domain)
        assert "dol_result" in _read_output(domain)


def test_pass2a_extracts_dot_notation_from_sample_artifacts():
    civil = (
        "computed:\n"
        "  - name: x\n"
        "    expr: client_result.adjusted\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        assert "client_result" in _read_output(domain)


def test_pass2a_whitespace_around_dot_tolerated():
    civil = (
        "computed:\n"
        "  - name: x\n"
        "    expr: 'client_result . adjusted_earned'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        assert "client_result" in _read_output(domain)


# ---------------------------------------------------------------------------
# Pass 2b — when-clause tokenization
# ---------------------------------------------------------------------------

def test_pass2b_tokenizes_when_clause():
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'is_compatible and household_size > 0'\n"
        "    then: {decision: approve}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "is_compatible" in out
        assert "household_size" in out


def test_pass2b_filters_keywords():
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'if x and y or not z'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "x" in out
        assert "y" in out
        assert "z" in out
        for kw in ("if", "and", "or", "not"):
            assert kw not in out


def test_pass2b_dot_notation_rhs_not_surfaced_as_bare_ident():
    """`client_result.income > 100`: `client_result` is captured by Pass 2a
    via the dot-notation regex; `income` (the RHS member) is NOT a bare
    identifier and must not surface from Pass 2b."""
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'client_result.income > 100'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "client_result" in out
        assert "income" not in out


def test_pass2b_string_literals_filtered():
    """Identifiers inside quoted strings must NOT surface; only the
    bare-identifier on the LHS of the comparison should."""
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'code == \"DENY_INCOME\"'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "code" in out
        assert "DENY_INCOME" not in out


def test_pass2b_when_list_form_supported():
    """CIVIL emits `when:` as a list of conjunctive conditions; each element
    is a separate string to tokenize."""
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when:\n"
        "      - a_flag == true\n"
        "      - b_flag == false\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "a_flag" in out
        assert "b_flag" in out


def test_pass2b_yaml_parse_failure_warns_and_skips():
    """A civil snippet that fails YAML parsing emits a WARN to stderr,
    Pass 2a (raw-string regex) still runs, and Pass 2b is skipped for that
    snippet only."""
    bad_civil = "rules:\n  - id: r1\n    when: : : : not valid yaml :\n"
    good_civil = "rules:\n  - id: r2\n    when: 'good_flag == true'\n"
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [
                    {"id": "bad", "civil": bad_civil},
                    {"id": "good", "civil": good_civil},
                ],
            },
        )
        # Capture stderr from the in-process run.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tvio.run(domain)
        err = buf.getvalue()
        assert "WARN" in err
        assert "bad" in err
        out = _read_output(domain)
        # Good snippet's Pass 2b still ran.
        assert "good_flag" in out


# ---------------------------------------------------------------------------
# Pass 3 — declared outputs
# ---------------------------------------------------------------------------

def test_pass3_collects_naming_manifest_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {
                    "eligible": {"type": "bool"},
                    "denial_reason": {"type": "string"},
                },
            },
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert "eligible" in out
        assert "denial_reason" in out


# ---------------------------------------------------------------------------
# Merge precedence — first-write-wins
# ---------------------------------------------------------------------------

def test_merge_pass1_wins_over_pass3():
    """Same name from Pass 1 and Pass 3: Pass 1 reason wins."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"shared": {"type": "bool"}},
            },
            skeleton={
                "skeleton": {
                    "computations": [
                        {"exprs": {"x": "shared.member"}},
                    ],
                },
            },
        )
        # Capture stdout via in-process run; assert reason ordering by
        # checking that `shared` precedes any Pass 3 entries in output.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tvio.run(domain)
        lines = buf.getvalue().splitlines()
        shared_line = next(l for l in lines if "shared" in l and l.startswith("  "))
        assert tvio._REASON_SKELETON in shared_line


def test_merge_pass2a_wins_over_pass2b():
    """Same name from Pass 2a and Pass 2b: Pass 2a reason wins."""
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'shared_var > 0'\n"
        "computed:\n"
        "  - name: x\n"
        "    expr: shared_var.field\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
        )
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tvio.run(domain)
        lines = buf.getvalue().splitlines()
        line = next(l for l in lines if "shared_var" in l and l.startswith("  "))
        assert tvio._REASON_CIVIL_SNIPPET in line


# ---------------------------------------------------------------------------
# Existing entries — preserved
# ---------------------------------------------------------------------------

def test_existing_only_entry_preserved_with_existing_reason():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
            existing=["after_half"],
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert out == ["after_half"]


def test_existing_entry_also_detected_gets_detection_reason():
    """When an existing entry is also surfaced by a detection pass, the
    detection reason wins (not 'existing')."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"eligible": {"type": "bool"}},
            },
            existing=["eligible"],
        )
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tvio.run(domain)
        lines = buf.getvalue().splitlines()
        line = next(l for l in lines if "eligible" in l and l.startswith("  "))
        assert tvio._REASON_OUTPUT in line
        assert "(existing)" not in line


# ---------------------------------------------------------------------------
# Output order
# ---------------------------------------------------------------------------

def test_output_order_pass1_then_pass3_then_existing():
    """Detected names appear in detection-pass order; existing-only names
    appended last in their prior file order."""
    civil = (
        "rules:\n"
        "  - id: r1\n"
        "    when: 'snippet_when > 0'\n"
        "computed:\n"
        "  - name: x\n"
        "    expr: snippet_dot.member\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"output_one": {}, "output_two": {}},
            },
            skeleton={
                "skeleton": {
                    "computations": [
                        {"exprs": {"x": "skeleton_base.member"}},
                    ],
                },
            },
            sample_artifacts={
                "sample_rules": [{"id": "r1", "civil": civil}],
            },
            existing=["legacy_one", "legacy_two"],
        )
        tvio.run(domain)
        out = _read_output(domain)
        assert out == [
            "skeleton_base",
            "snippet_dot",
            "snippet_when",
            "output_one",
            "output_two",
            "legacy_one",
            "legacy_two",
        ]


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_idempotent_second_run_skips_write():
    """Second run on identical inputs prints the no-op header and does not
    call `os.replace`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"out_a": {}, "out_b": {}},
            },
        )
        tvio.run(domain)  # first run writes the file
        with mock.patch("tag_vars_include_output.os.replace") as repl:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tvio.run(domain)
            assert rc == 0
            assert repl.call_count == 0
            assert "up to date" in buf.getvalue()


# ---------------------------------------------------------------------------
# Empty case
# ---------------------------------------------------------------------------

def test_empty_case_writes_empty_list():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={"version": "1.0", "outputs": {}},
        )
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tvio.run(domain)
        assert "No variables auto-detected" in buf.getvalue()
        out_path = domain / "specs" / "guidance" / "include-with-output.yaml"
        assert out_path.exists()
        assert out_path.read_text() == "[]\n"


# ---------------------------------------------------------------------------
# Pre-flight failures (subprocess — exercises main())
# ---------------------------------------------------------------------------

def test_preflight_missing_domain_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        rc, stdout, stderr = _run_tool(Path(tmp), "nonexistent")
        assert rc == 2
        assert "Domain not found" in stderr


def test_preflight_missing_naming_manifest_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        # Create the domain folder but no naming-manifest.yaml.
        (Path(tmp) / "bare_domain" / "specs").mkdir(parents=True)
        rc, stdout, stderr = _run_tool(Path(tmp), "bare_domain")
        assert rc == 2
        assert "specs/naming-manifest.yaml not found" in stderr
        assert "Run /declare-target-ruleset bare_domain first" in stderr


def test_preflight_unset_domains_fullpath_exits_2():
    script = Path(__file__).parent / "tag_vars_include_output.py"
    env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    proc = subprocess.run(
        ["uv", "run", str(script), "any_domain"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "DOMAINS_FULLPATH" in proc.stderr


# ---------------------------------------------------------------------------
# Optional-file robustness
# ---------------------------------------------------------------------------

def test_missing_skeleton_yaml_does_not_error():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"out_only": {}},
            },
        )
        rc = tvio.run(domain)
        assert rc == 0
        assert _read_output(domain) == ["out_only"]


def test_missing_ruleset_modules_and_sample_artifacts_does_not_error():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"out_only": {}},
            },
            skeleton={
                "skeleton": {
                    "computations": [{"exprs": {"x": "base.member"}}],
                },
            },
        )
        rc = tvio.run(domain)
        assert rc == 0
        assert _read_output(domain) == ["base", "out_only"]


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

def test_atomicity_failed_write_leaves_prior_file_intact():
    """If os.replace raises mid-write, the prior include-with-output.yaml
    bytes are untouched (tmp file is never swapped in)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            manifest={
                "version": "1.0",
                "outputs": {"out_a": {}},
            },
            existing=["legacy_entry"],
        )
        out_path = domain / "specs" / "guidance" / "include-with-output.yaml"
        original_bytes = out_path.read_bytes()
        with mock.patch(
            "tag_vars_include_output.os.replace",
            side_effect=OSError("simulated failure"),
        ):
            try:
                tvio.run(domain)
            except OSError:
                pass
        # Prior content survives the failed write.
        assert out_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# Self-runner (for direct `uv run` invocation)
# ---------------------------------------------------------------------------

def main() -> int:
    failed = 0
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
            else:
                passed += 1
                print(f"ok {name}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
