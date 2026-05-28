# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for scan_ruleset_groups.py — covers the (1a) stage-derived branch,
description humanization, the catch-all branch, every mode (create/replace/
merge), all merge-precedence rules, --heading-derived-candidates passthrough,
the heading_text_fallback_recommended flag, pre-flight failures, output
shape (JSON header + sentinel + table), and atomic write semantics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import scan_ruleset_groups as srg  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DEFAULT_METADATA = {
    "display_name": "Determine Sample Eligibility",
    "description": "Sample domain description",
}

_DEFAULT_SKELETON = {"skeleton": {"inputs": [], "outputs": [], "computations": []}}


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_domain(
    tmp: Path,
    name: str = "test_dom",
    *,
    metadata: dict | None = _DEFAULT_METADATA,
    skeleton: dict | None = _DEFAULT_SKELETON,
    per_file: dict[str, dict] | None = None,
    existing_groups: dict | None = None,
    include_metadata: bool = True,
    include_skeleton: bool = True,
    include_per_file_dir: bool = True,
) -> Path:
    """Build a minimal domain tree. Defaults supply a passing pre-flight."""
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True, exist_ok=True)

    if include_metadata and metadata is not None:
        _write_yaml(domain / "specs" / "guidance" / "metadata.yaml", metadata)
    if include_skeleton and skeleton is not None:
        _write_yaml(domain / "specs" / "guidance" / "skeleton.yaml", skeleton)
    if existing_groups is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "ruleset-groups.yaml",
            existing_groups,
        )

    if include_per_file_dir:
        per_file_dir = domain / "policy_facets" / "computations"
        per_file_dir.mkdir(parents=True, exist_ok=True)
        if per_file is None:
            # Default: at least one .md.yaml present so pre-flight passes.
            per_file = {"_default.md.yaml": {"sections": []}}
        for rel, doc in per_file.items():
            _write_yaml(per_file_dir / rel, doc)

    return domain


def _read_groups(domain: Path) -> list[dict]:
    path = domain / "specs" / "guidance" / "ruleset-groups.yaml"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("ruleset_groups") or []


def _run_tool(
    tmp_path: Path,
    domain: str,
    *extra_args: str,
) -> tuple[int, str, str]:
    """Invoke the script as a subprocess. Returns (returncode, stdout, stderr)."""
    script = Path(__file__).parent / "scan_ruleset_groups.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    proc = subprocess.run(
        ["uv", "run", str(script), domain, *extra_args],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _parse_json_header(stdout: str) -> dict:
    """Parse the JSON header line before the sentinel divider."""
    lines = stdout.splitlines()
    header_lines: list[str] = []
    for line in lines:
        if line == srg._HEADER_SENTINEL:
            break
        header_lines.append(line)
    return json.loads("\n".join(header_lines))


def _sections(*stages_per_section: str | None) -> list[dict]:
    """Build a `sections:` list from a sequence of stage values."""
    out: list[dict] = []
    for stage in stages_per_section:
        section: dict = {"heading": "### section", "computations": []}
        if stage is not None:
            section["stage"] = stage
        out.append(section)
    return out


# ---------------------------------------------------------------------------
# (1a) Stage-derived candidates
# ---------------------------------------------------------------------------

def test_stage_two_files_same_value_emit_one_candidate():
    """Two per-file YAMLs, both with `stage: initial_screening` →
    one deduped candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {"sections": _sections("initial_screening")},
                "b.md.yaml": {"sections": _sections("initial_screening")},
            },
        )
        rc = srg.run(domain, "create", None)
        assert rc == 0
        groups = _read_groups(domain)
        assert [g["name"] for g in groups] == ["initial_screening"]
        assert groups[0]["description"] == "Initial Screening"


def test_stage_suffix_normalization_collapses_income_test_with_income():
    """`income_test` and `income` collapse to `income` via suffix
    normalization; `deductions` stays distinct."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {"sections": _sections("income_test")},
                "b.md.yaml": {"sections": _sections("income")},
                "c.md.yaml": {"sections": _sections("deductions")},
            },
        )
        rc = srg.run(domain, "create", None)
        assert rc == 0
        names = [g["name"] for g in _read_groups(domain)]
        assert names == ["income", "deductions"]


def test_stage_case_insensitive_dedup():
    """`stage: GROSS_CHECK` and `stage: gross` collapse to one canonical
    `gross` candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {"sections": _sections("GROSS_CHECK")},
                "b.md.yaml": {"sections": _sections("gross")},
            },
        )
        rc = srg.run(domain, "create", None)
        assert rc == 0
        names = [g["name"] for g in _read_groups(domain)]
        assert names == ["gross"]


def test_stage_missing_field_ignored():
    """A section without `stage:` contributes nothing; sections with `stage:`
    in the same file still surface."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": _sections("alpha", None, "beta"),
                },
            },
        )
        rc = srg.run(domain, "create", None)
        assert rc == 0
        names = [g["name"] for g in _read_groups(domain)]
        assert set(names) == {"alpha", "beta"}


def test_no_stage_no_existing_no_heading_triggers_catch_all():
    """When zero sections carry `stage:` AND no existing entries AND no
    --heading-derived-candidates supplied, the display_name catch-all fires
    and the flag stays false."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "Determine Eligibility", "description": ""},
            per_file={"a.md.yaml": {"sections": _sections(None, None)}},
        )
        rc = srg.run(domain, "create", None)
        assert rc == 0
        groups = _read_groups(domain)
        assert [g["name"] for g in groups] == ["eligibility"]
        assert groups[0]["description"] == "Determine Eligibility"


# ---------------------------------------------------------------------------
# Description humanization
# ---------------------------------------------------------------------------

def test_humanize_no_acronym_preservation():
    """`ebt_eligibility` → `"Ebt Eligibility"` (acronym NOT preserved —
    matches the existing skill rule)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("ebt_eligibility")}},
        )
        srg.run(domain, "create", None)
        groups = _read_groups(domain)
        assert groups[0]["description"] == "Ebt Eligibility"


def test_humanize_underscores_to_title_case():
    """Underscores become spaces; result is title-cased."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("initial_screening")}},
        )
        srg.run(domain, "create", None)
        groups = _read_groups(domain)
        assert groups[0]["description"] == "Initial Screening"


# ---------------------------------------------------------------------------
# Catch-all branch
# ---------------------------------------------------------------------------

def test_catch_all_determine_eligibility():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "Determine Eligibility", "description": ""},
        )
        srg.run(domain, "create", None)
        groups = _read_groups(domain)
        assert groups == [{"name": "eligibility", "description": "Determine Eligibility"}]


def test_catch_all_calculate_benefit_amount():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "Calculate Benefit Amount", "description": ""},
        )
        srg.run(domain, "create", None)
        groups = _read_groups(domain)
        assert groups == [
            {"name": "benefit_amount", "description": "Calculate Benefit Amount"}
        ]


def test_catch_all_no_matching_verb_keeps_full_phrase():
    """`Process Asset Review` has no leading verb in the strip list →
    snake_case the entire phrase."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "Process Asset Review", "description": ""},
        )
        srg.run(domain, "create", None)
        groups = _read_groups(domain)
        assert groups == [
            {"name": "process_asset_review", "description": "Process Asset Review"}
        ]


def test_catch_all_empty_display_name_triggers_fallback_flag():
    """Empty `display_name` → catch-all does not fire; fallback flag set
    true; no file is written."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "", "description": "x"},
        )
        rc, stdout, _stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is True
        assert header["catch_all_fired"] is False
        # No write occurred.
        assert not (domain / "specs" / "guidance" / "ruleset-groups.yaml").exists()


def test_catch_all_missing_display_name_triggers_fallback_flag():
    """Absent `display_name` key behaves identically to empty string."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"description": "x"},
        )
        rc, stdout, _stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is True


# ---------------------------------------------------------------------------
# Mode: create
# ---------------------------------------------------------------------------

def test_create_mode_writes_when_file_absent():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
        )
        rc, _, _ = _run_tool(Path(tmp), "test_dom", "--mode", "create")
        assert rc == 0
        assert (domain / "specs" / "guidance" / "ruleset-groups.yaml").exists()


def test_create_mode_collision_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
            existing_groups={"ruleset_groups": [{"name": "x", "description": "y"}]},
        )
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "--mode", "create")
        assert rc == 2
        assert "Ruleset groups already exist." in stderr


# ---------------------------------------------------------------------------
# Mode: replace
# ---------------------------------------------------------------------------

def test_replace_mode_overwrites_existing():
    """Replace mode wipes existing entries and writes the new candidates only."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "old_one", "description": "old"},
                    {"name": "old_two", "description": "older"},
                ]
            },
        )
        rc, _, _ = _run_tool(Path(tmp), "test_dom", "--mode", "replace")
        assert rc == 0
        names = [g["name"] for g in _read_groups(domain)]
        assert names == ["alpha"]


# ---------------------------------------------------------------------------
# Mode: merge — stage-derived sticky preservation
# ---------------------------------------------------------------------------

def test_merge_stage_collision_preserves_analyst_description():
    """Existing `{name: income, description: "Custom"}` + new stage-derived
    `income_test` (normalizes to `income`) → existing description preserved."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("income_test")}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "income", "description": "Custom analyst description"},
                ]
            },
        )
        rc, _, _ = _run_tool(Path(tmp), "test_dom", "--mode", "merge")
        assert rc == 0
        groups = _read_groups(domain)
        assert groups == [
            {"name": "income", "description": "Custom analyst description"}
        ]


def test_merge_heading_collision_new_wins():
    """Heading-derived candidate collides with existing → new description wins."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections(None)}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "income", "description": "Custom"},
                ]
            },
        )
        heading_path = Path(tmp) / "heading.json"
        heading_path.write_text(json.dumps(
            [{"name": "income", "description": "New from AI"}]
        ))
        rc, _, _ = _run_tool(
            Path(tmp), "test_dom",
            "--mode", "merge",
            "--heading-derived-candidates", str(heading_path),
        )
        assert rc == 0
        groups = _read_groups(domain)
        assert groups == [{"name": "income", "description": "New from AI"}]


def test_merge_no_collision_appends():
    """Stage-derived candidate with no name collision → both entries
    present, existing first."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("deductions")}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "income", "description": "Custom"},
                ]
            },
        )
        rc, _, _ = _run_tool(Path(tmp), "test_dom", "--mode", "merge")
        assert rc == 0
        groups = _read_groups(domain)
        assert groups == [
            {"name": "income", "description": "Custom"},
            {"name": "deductions", "description": "Deductions"},
        ]


def test_merge_output_order():
    """Existing first (input order), then new stage-derived (alphabetical),
    then new heading-derived (input order)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {"sections": _sections("eligibility", "assets")},
            },
            existing_groups={
                "ruleset_groups": [
                    {"name": "income", "description": "1"},
                    {"name": "deductions", "description": "2"},
                ]
            },
        )
        heading_path = Path(tmp) / "heading.json"
        heading_path.write_text(json.dumps(
            [{"name": "verification", "description": "V"}]
        ))
        rc, _, _ = _run_tool(
            Path(tmp), "test_dom",
            "--mode", "merge",
            "--heading-derived-candidates", str(heading_path),
        )
        assert rc == 0
        names = [g["name"] for g in _read_groups(domain)]
        assert names == ["income", "deductions", "assets", "eligibility", "verification"]


def test_merge_existing_entry_not_in_candidates_preserved():
    """Existing entries whose names don't appear in any new candidate
    survive verbatim."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "legacy", "description": "Hand-curated"},
                ]
            },
        )
        rc, _, _ = _run_tool(Path(tmp), "test_dom", "--mode", "merge")
        assert rc == 0
        groups = _read_groups(domain)
        assert {"name": "legacy", "description": "Hand-curated"} in groups
        assert any(g["name"] == "alpha" for g in groups)


# ---------------------------------------------------------------------------
# --heading-derived-candidates passthrough
# ---------------------------------------------------------------------------

def test_heading_derived_passthrough():
    """A JSON file with one heading-derived entry → merged into output."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections(None)}},
        )
        heading_path = Path(tmp) / "heading.json"
        heading_path.write_text(json.dumps(
            [{"name": "verification", "description": "Verification checks"}]
        ))
        rc, _, _ = _run_tool(
            Path(tmp), "test_dom",
            "--mode", "create",
            "--heading-derived-candidates", str(heading_path),
        )
        assert rc == 0
        groups = _read_groups(domain)
        assert groups == [
            {"name": "verification", "description": "Verification checks"}
        ]


def test_heading_derived_collision_with_stage_loses():
    """Stage-derived beats heading-derived on name collision."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("income")}},
        )
        heading_path = Path(tmp) / "heading.json"
        heading_path.write_text(json.dumps(
            [{"name": "income", "description": "Inferred description"}]
        ))
        rc, _, _ = _run_tool(
            Path(tmp), "test_dom",
            "--mode", "create",
            "--heading-derived-candidates", str(heading_path),
        )
        assert rc == 0
        groups = _read_groups(domain)
        assert groups == [{"name": "income", "description": "Income"}]


def test_heading_derived_malformed_not_list_exits_1():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
        )
        bad_path = Path(tmp) / "bad.json"
        bad_path.write_text(json.dumps({"name": "x", "description": "y"}))
        rc, _, stderr = _run_tool(
            Path(tmp), "test_dom",
            "--heading-derived-candidates", str(bad_path),
        )
        assert rc == 1
        assert "list" in stderr.lower()


def test_heading_derived_malformed_missing_name_exits_1():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
        )
        bad_path = Path(tmp) / "bad.json"
        bad_path.write_text(json.dumps([{"description": "no name"}]))
        rc, _, stderr = _run_tool(
            Path(tmp), "test_dom",
            "--heading-derived-candidates", str(bad_path),
        )
        assert rc == 1
        assert "name" in stderr.lower()


def test_heading_derived_invalid_json_exits_1():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
        )
        bad_path = Path(tmp) / "bad.json"
        bad_path.write_text("{not json")
        rc, _, stderr = _run_tool(
            Path(tmp), "test_dom",
            "--heading-derived-candidates", str(bad_path),
        )
        assert rc == 1
        assert "json" in stderr.lower()


# ---------------------------------------------------------------------------
# Heading-text fallback flag — combinations
# ---------------------------------------------------------------------------

def test_flag_false_when_stage_candidates_present():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha")}},
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is False
        assert header["stage_derived_count"] == 1


def test_flag_false_when_existing_entries_present():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections(None)}},
            existing_groups={"ruleset_groups": [{"name": "x", "description": "y"}]},
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom", "--mode", "merge")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is False
        assert header["existing_entries_count"] == 1


def test_flag_false_when_catch_all_fires():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            metadata={"display_name": "Determine X", "description": ""},
            per_file={"a.md.yaml": {"sections": _sections(None)}},
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is False
        assert header["catch_all_fired"] is True


def test_flag_true_when_nothing_can_be_derived():
    """Zero stage + zero existing + zero heading + empty display_name →
    flag fires; no write."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "", "description": ""},
            per_file={"a.md.yaml": {"sections": _sections(None)}},
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["heading_text_fallback_recommended"] is True
        assert not (domain / "specs" / "guidance" / "ruleset-groups.yaml").exists()


# ---------------------------------------------------------------------------
# Pre-flight failures
# ---------------------------------------------------------------------------

def test_preflight_missing_domain():
    with tempfile.TemporaryDirectory() as tmp:
        rc, _, stderr = _run_tool(Path(tmp), "nonexistent")
        assert rc == 2
        assert "Domain not found:" in stderr


def test_preflight_missing_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(Path(tmp), include_metadata=False)
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "metadata.yaml not found" in stderr


def test_preflight_missing_skeleton():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(Path(tmp), include_skeleton=False)
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Skeleton not found" in stderr


def test_preflight_missing_computations_dir():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(Path(tmp), include_per_file_dir=False)
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Per-file computations not found" in stderr


def test_preflight_empty_computations_dir():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(Path(tmp), per_file={})
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Per-file computations not found" in stderr


def test_preflight_unset_domains_fullpath():
    script = Path(__file__).parent / "scan_ruleset_groups.py"
    env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    proc = subprocess.run(
        ["uv", "run", str(script), "any"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "DOMAINS_FULLPATH" in proc.stderr


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_output_shape_header_sentinel_table():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha", "beta")}},
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        lines = stdout.splitlines()
        # First line: single-line JSON header.
        header = json.loads(lines[0])
        assert isinstance(header, dict)
        assert "candidate_count" in header
        # Second line: sentinel divider.
        assert lines[1] == srg._HEADER_SENTINEL
        # Subsequent lines: proposal table.
        assert any("Proposed ruleset groups" in ln for ln in lines[2:])
        assert any("alpha" in ln for ln in lines[2:])


def test_yaml_round_trip():
    """Emitted YAML parses back via yaml.safe_load."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("alpha", "beta")}},
        )
        srg.run(domain, "create", None)
        path = domain / "specs" / "guidance" / "ruleset-groups.yaml"
        doc = yaml.safe_load(path.read_text())
        assert isinstance(doc, dict)
        assert "ruleset_groups" in doc
        assert all(set(g.keys()) == {"name", "description"} for g in doc["ruleset_groups"])


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

def test_atomic_write_failure_preserves_prior_file():
    """When os.replace raises mid-write, the prior file content stays intact."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {"sections": _sections("new_stage")}},
            existing_groups={
                "ruleset_groups": [
                    {"name": "prior", "description": "Prior content"},
                ]
            },
        )
        prior_path = domain / "specs" / "guidance" / "ruleset-groups.yaml"
        prior_content = prior_path.read_text()
        with mock.patch.object(srg.os, "replace", side_effect=OSError("simulated")):
            try:
                srg.run(domain, "replace", None)
            except OSError:
                pass
        # Prior file unchanged.
        assert prior_path.read_text() == prior_content
