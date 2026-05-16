# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for skeleton_signals.py — covers signal extraction across tags,
headings, summaries, expr_hints, intermediate_variables, stage_index,
entities, mirrored_fields, outputs_in_manifest, output_primary_hint,
candidate_constants_and_tables, metadata, prompt_context_existing,
existing_files, plus all pre-flight error paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import skeleton_signals as ss  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DEFAULT_METADATA = {
    "display_name": "Determine Sample Eligibility",
    "description": "Sample domain description",
}

_DEFAULT_PROMPT_CONTEXT = {
    "constraints": [],
    "standards": [],
    "guidance": [],
    "edge_cases": [],
}

_DEFAULT_NAMING_MANIFEST = {
    "inputs": {
        "Household": {
            "fields": {
                "gross_earned_income": {"type": "money"},
                "household_size": {"type": "int"},
            }
        }
    },
    "outputs": {
        "eligible": {"type": "bool", "description": "Whether the household qualifies"},
    },
}


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_domain(
    tmp: Path,
    name: str = "test_dom",
    *,
    metadata=_DEFAULT_METADATA,
    prompt_context=_DEFAULT_PROMPT_CONTEXT,
    naming_manifest=_DEFAULT_NAMING_MANIFEST,
    per_file: dict[str, dict] | None = None,
    suggested_targets: dict[str, dict] | None = None,
    existing_outputs: dict[str, dict] | None = None,
    include_metadata: bool = True,
    include_prompt_context: bool = True,
    include_manifest: bool = True,
    include_per_file_dir: bool = True,
) -> Path:
    """Build a minimal domain tree. Defaults supply a passing pre-flight."""
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True, exist_ok=True)

    if include_metadata and metadata is not None:
        _write_yaml(domain / "specs" / "guidance" / "metadata.yaml", metadata)
    if include_prompt_context and prompt_context is not None:
        _write_yaml(domain / "specs" / "guidance" / "prompt-context.yaml", prompt_context)
    if include_manifest and naming_manifest is not None:
        _write_yaml(domain / "specs" / "naming-manifest.yaml", naming_manifest)

    if suggested_targets is not None:
        st_dir = domain / "specs" / "suggested_targets"
        st_dir.mkdir(parents=True, exist_ok=True)
        for rel, doc in suggested_targets.items():
            _write_yaml(st_dir / rel, doc)

    if existing_outputs is not None:
        for rel, doc in existing_outputs.items():
            _write_yaml(domain / "specs" / "guidance" / rel, doc)

    if include_per_file_dir:
        per_file_dir = domain / "policy_facets" / "computations"
        per_file_dir.mkdir(parents=True, exist_ok=True)
        if per_file is None:
            per_file = {"_default.md.yaml": {"sections": []}}
        for rel, doc in per_file.items():
            _write_yaml(per_file_dir / rel, doc)

    return domain


def _run_tool(tmp_path: Path, domain: str, *extra_args: str) -> tuple[int, str, str]:
    """Invoke the script as a subprocess. Returns (returncode, stdout, stderr)."""
    script = Path(__file__).parent / "skeleton_signals.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    proc = subprocess.run(
        ["uv", "run", str(script), domain, *extra_args],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def test_preflight_missing_domain():
    with tempfile.TemporaryDirectory() as tmp:
        rc = ss.run(Path(tmp) / "nonexistent")
        assert rc == 2


def test_preflight_missing_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp), include_metadata=False)
        rc = ss.run(domain)
        assert rc == 2


def test_preflight_missing_prompt_context():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp), include_prompt_context=False)
        rc = ss.run(domain)
        assert rc == 2


def test_preflight_missing_naming_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp), include_manifest=False)
        rc = ss.run(domain)
        assert rc == 2


def test_preflight_empty_computations_dir():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp), per_file={})
        rc = ss.run(domain)
        assert rc == 2


# ---------------------------------------------------------------------------
# Tags / headings / summaries
# ---------------------------------------------------------------------------

def test_tags_frequency_sorted():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {"heading": "## A", "tags": ["income", "deductions"]},
                        {"heading": "## B", "tags": ["income"]},
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        tags = signals["tags"]
        assert tags[0] == {"value": "income", "count": 2}
        assert tags[1] == {"value": "deductions", "count": 1}


def test_headings_preserve_order_and_level():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "441.md.yaml": {
                    "sections": [
                        {"heading": "## 441-1 Earned Income"},
                        {"heading": "### 441-1a Wages"},
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        headings = signals["headings"]
        assert headings[0]["text"] == "## 441-1 Earned Income"
        assert headings[0]["level"] == 2
        assert headings[0]["file"] == "input/policy_docs/441.md"
        assert headings[1]["level"] == 3


def test_summaries_collected():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "441.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "summary": "Income definition",
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        summaries = signals["summaries"]
        assert summaries == [{
            "file": "input/policy_docs/441.md",
            "section_heading": "## A",
            "text": "Income definition",
        }]


# ---------------------------------------------------------------------------
# expr_hint parsing
# ---------------------------------------------------------------------------

def test_expr_hints_parse_basic():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "stage": "deductions",
                            "computations": [
                                {"expr_hint": "x = a + b"},
                            ],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        eh = signals["expr_hints"]
        assert len(eh) == 1
        assert eh[0]["output"] == "x"
        assert eh[0]["expression"] == "a + b"
        assert eh[0]["rhs_tokens"] == ["a", "b"]
        assert eh[0]["stage"] == "deductions"
        assert eh[0]["stage_normalized"] == "deductions"


def test_expr_hints_no_expr_hint_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{"description": "no formula"}],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["expr_hints"] == []


def test_expr_hints_malformed_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{"expr_hint": "no equals sign"}],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["expr_hints"] == []


def test_expr_hints_preconditions_included():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{
                                "expr_hint": "x = a + b",
                                "preconditions": ["has_income"],
                            }],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["expr_hints"][0]["preconditions"] == ["has_income"]


# ---------------------------------------------------------------------------
# Stage normalization
# ---------------------------------------------------------------------------

def test_stage_index_suffix_strip():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "stage": "income_test",
                            "computations": [{"expr_hint": "x = a"}],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert "income" in signals["stage_index"]
        assert signals["stage_index"]["income"] == ["x"]


def test_stage_index_no_stage_omits():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{"expr_hint": "x = a"}],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["stage_index"] == {}


# ---------------------------------------------------------------------------
# Intermediate variables
# ---------------------------------------------------------------------------

def test_intermediate_variables_chain():
    """A: x = a + b, B: y = x + c → x is intermediate, y is not."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [
                                {"expr_hint": "x = a + b"},
                                {"expr_hint": "y = x + c"},
                            ],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["intermediate_variables"] == ["x"]


def test_intermediate_variables_no_chain():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [
                                {"expr_hint": "z = d + e"},
                            ],
                        }
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["intermediate_variables"] == []


# ---------------------------------------------------------------------------
# Mirrored fields
# ---------------------------------------------------------------------------

def test_mirrored_fields_emitted():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            naming_manifest={
                "inputs": {
                    "Household": {"fields": {"gross_income": {"type": "money"}}},
                    "Applicant": {"fields": {"gross_income": {"type": "money"}}},
                },
                "outputs": {"eligible": {"type": "bool"}},
            },
        )
        signals = ss.extract_signals(domain)
        mirrored = signals["mirrored_fields"]
        assert len(mirrored) == 1
        assert mirrored[0]["field"] == "gross_income"
        assert set(mirrored[0]["entities"]) == {"Household", "Applicant"}


def test_mirrored_fields_single_entity_not_listed():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        signals = ss.extract_signals(domain)
        assert signals["mirrored_fields"] == []


def test_mirrored_fields_empty_inputs():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            naming_manifest={"inputs": {}, "outputs": {"eligible": {"type": "bool"}}},
        )
        signals = ss.extract_signals(domain)
        assert signals["mirrored_fields"] == []


# ---------------------------------------------------------------------------
# output_primary_hint
# ---------------------------------------------------------------------------

def test_output_primary_hint_single_match():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            suggested_targets={
                "candidate1.yaml": {
                    "outputs": {"eligible": {"primary": True}},
                }
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["output_primary_hint"] == "eligible"


def test_output_primary_hint_no_suggested_targets():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        signals = ss.extract_signals(domain)
        assert signals["output_primary_hint"] is None


def test_output_primary_hint_multiple_matches_ambiguous():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            naming_manifest={
                "inputs": {"Household": {"fields": {"x": {"type": "int"}}}},
                "outputs": {
                    "eligible": {"type": "bool"},
                    "denial_reasons": {"type": "list"},
                },
            },
            suggested_targets={
                "a.yaml": {"outputs": {"eligible": {"primary": True}}},
                "b.yaml": {"outputs": {"denial_reasons": {"primary": True}}},
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["output_primary_hint"] is None


def test_output_primary_hint_mismatched_name():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            suggested_targets={
                "a.yaml": {"outputs": {"some_other_name": {"primary": True}}},
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["output_primary_hint"] is None


# ---------------------------------------------------------------------------
# Candidate constants/tables
# ---------------------------------------------------------------------------

def test_candidate_constants_title_case_surfaced():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [{
                        "heading": "## A",
                        "computations": [{
                            "description": "exceeds the Earned Income Limit",
                        }],
                    }]
                }
            },
        )
        signals = ss.extract_signals(domain)
        names = [c["name"] for c in signals["candidate_constants_and_tables"]]
        assert "Earned Income Limit" in names


def test_candidate_constants_upper_snake_surfaced():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [{
                        "heading": "## A",
                        "computations": [{
                            "description": "compare against MAX_INCOME threshold",
                        }],
                    }]
                }
            },
        )
        signals = ss.extract_signals(domain)
        names = [c["name"] for c in signals["candidate_constants_and_tables"]]
        assert "MAX_INCOME" in names


def test_candidate_constants_filters_known_variable_name():
    """`Gross Earned Income` in prose matches manifest var `gross_earned_income` → filtered."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            naming_manifest={
                "inputs": {
                    "Household": {"fields": {"gross_earned_income": {"type": "money"}}},
                },
                "outputs": {"eligible": {"type": "bool"}},
            },
            per_file={
                "a.md.yaml": {
                    "sections": [{
                        "heading": "## A",
                        "computations": [{
                            "description": "compute Gross Earned Income for the household",
                        }],
                    }]
                }
            },
        )
        signals = ss.extract_signals(domain)
        names = [c["name"] for c in signals["candidate_constants_and_tables"]]
        assert "Gross Earned Income" not in names


def test_candidate_constants_same_name_two_sections():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{"description": "the Earned Income Limit applies"}],
                        },
                        {
                            "heading": "## B",
                            "computations": [{"description": "see Earned Income Limit"}],
                        },
                    ]
                }
            },
        )
        signals = ss.extract_signals(domain)
        rows = [c for c in signals["candidate_constants_and_tables"]
                if c["name"] == "Earned Income Limit"]
        assert len(rows) == 2
        assert {r["source_section"] for r in rows} == {"## A", "## B"}


# ---------------------------------------------------------------------------
# existing_files
# ---------------------------------------------------------------------------

def test_existing_files_all_absent():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        signals = ss.extract_signals(domain)
        ef = signals["existing_files"]
        assert ef["skeleton.yaml"] is None
        assert ef["flow_diagram.yaml"] is None
        assert ef["output-variables.yaml"] is None
        assert ef["input-variables.yaml"] is None
        assert ef["constants-and-tables.yaml"] is None


def test_existing_files_all_present():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "skeleton.yaml": {"skeleton": {"inputs": ["a"]}},
                "flow_diagram.yaml": {"flow_diagram": "a -> b"},
                "output-variables.yaml": {"eligible": {"description": "x", "primary": True}},
                "input-variables.yaml": {"categories": []},
                "constants-and-tables.yaml": {"constants_and_tables": []},
            },
        )
        signals = ss.extract_signals(domain)
        ef = signals["existing_files"]
        assert ef["skeleton.yaml"] == {"skeleton": {"inputs": ["a"]}}
        assert ef["flow_diagram.yaml"] == {"flow_diagram": "a -> b"}
        assert ef["output-variables.yaml"] == {"eligible": {"description": "x", "primary": True}}
        assert ef["input-variables.yaml"] == {"categories": []}
        assert ef["constants-and-tables.yaml"] == {"constants_and_tables": []}


def test_existing_files_mixed():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "skeleton.yaml": {"skeleton": {}},
            },
        )
        signals = ss.extract_signals(domain)
        ef = signals["existing_files"]
        assert ef["skeleton.yaml"] == {"skeleton": {}}
        assert ef["flow_diagram.yaml"] is None
        assert ef["output-variables.yaml"] is None


# ---------------------------------------------------------------------------
# prompt_context_existing
# ---------------------------------------------------------------------------

def test_prompt_context_existing_populated():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            prompt_context={
                "constraints": ["Do not invent."],
                "standards": ["Use snake_case."],
                "guidance": [],
                "edge_cases": ["Zero income"],
            },
        )
        signals = ss.extract_signals(domain)
        pce = signals["prompt_context_existing"]
        assert pce["constraints"] == ["Do not invent."]
        assert pce["standards"] == ["Use snake_case."]
        assert pce["guidance"] == []
        assert pce["edge_cases"] == ["Zero income"]


def test_prompt_context_existing_missing_section_becomes_empty():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            prompt_context={
                "constraints": ["A"],
                "standards": [],
                "guidance": [],
                # edge_cases missing
            },
        )
        signals = ss.extract_signals(domain)
        assert signals["prompt_context_existing"]["edge_cases"] == []


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_metadata_copied():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata={"display_name": "Foo", "description": "Bar"},
        )
        signals = ss.extract_signals(domain)
        assert signals["metadata"] == {"display_name": "Foo", "description": "Bar"}


# ---------------------------------------------------------------------------
# Subprocess stdout shape
# ---------------------------------------------------------------------------

def test_stdout_is_valid_json():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(Path(tmp))
        rc, stdout, _stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        parsed = json.loads(stdout)
        # Spot-check schema shape.
        assert "tags" in parsed
        assert "expr_hints" in parsed
        assert "stage_index" in parsed
        assert "entities" in parsed
        assert "existing_files" in parsed


def test_section_without_sections_key():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={"a.md.yaml": {}, "b.md.yaml": {"sections": [{"heading": "## A"}]}},
        )
        signals = ss.extract_signals(domain)
        # a.md.yaml has no sections key but b does — only b contributes.
        assert len(signals["headings"]) == 1
        assert signals["headings"][0]["file"] == "input/policy_docs/b.md"
