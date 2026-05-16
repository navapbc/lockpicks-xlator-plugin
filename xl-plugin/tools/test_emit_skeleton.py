# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for emit_skeleton.py — covers schema enforcement, every UPDATE-mode
branch, the preserve-non-null rule, pre-flight error paths, and atomicity."""

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

import emit_skeleton as es  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DEFAULT_METADATA = {
    "display_name": "Determine Sample Eligibility",
    "description": "Sample domain description",
}

_DEFAULT_PROMPT_CONTEXT = {
    "role": "policy analyst",
    "scope": "household eligibility",
    "constraints": ["Existing constraint"],
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
        "eligible": {"type": "bool"},
        "denial_reasons": {"type": "list"},
    },
}


def _minimal_enrichment(**overrides) -> dict:
    """Build a minimum-valid enrichment object; apply overrides."""
    base = {
        "prompt_context_additions": {
            "constraints": [],
            "standards": [],
            "guidance": [],
            "edge_cases": [],
        },
        "skeleton_flow_diagram": "gross_earned_income\n   |\n   v\neligible",
        "skeleton_inputs": ["gross_earned_income", "household_size"],
        "skeleton_outputs": ["eligible", "denial_reasons"],
        "output_variables": {
            "eligible": {"description": "Whether the household qualifies", "primary": True},
            "denial_reasons": {"description": "Reasons", "primary": False},
        },
        "input_variables": {
            "categories": [
                {
                    "category": "income",
                    "description": "Earned income sources",
                    "fields": [{"name_ref": "gross_earned_income"}],
                }
            ]
        },
        "constants_and_tables": {},
    }
    base.update(overrides)
    return base


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)


def _build_domain(
    tmp: Path,
    name: str = "test_dom",
    *,
    metadata=_DEFAULT_METADATA,
    prompt_context=_DEFAULT_PROMPT_CONTEXT,
    naming_manifest=_DEFAULT_NAMING_MANIFEST,
    per_file: dict[str, dict] | None = None,
    existing_outputs: dict[str, dict] | None = None,
) -> Path:
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True, exist_ok=True)

    if metadata is not None:
        _write_yaml(domain / "specs" / "guidance" / "metadata.yaml", metadata)
    if prompt_context is not None:
        _write_yaml(domain / "specs" / "guidance" / "prompt-context.yaml", prompt_context)
    if naming_manifest is not None:
        _write_yaml(domain / "specs" / "naming-manifest.yaml", naming_manifest)

    if existing_outputs is not None:
        for rel, doc in existing_outputs.items():
            _write_yaml(domain / "specs" / "guidance" / rel, doc)

    per_file_dir = domain / "policy_facets" / "computations"
    per_file_dir.mkdir(parents=True, exist_ok=True)
    if per_file is None:
        per_file = {
            "a.md.yaml": {
                "sections": [
                    {
                        "heading": "## 441-1 Earned Income",
                        "stage": "deductions",
                        "computations": [
                            {"expr_hint": "gross_earned_income = wages + tips"}
                        ],
                    }
                ]
            }
        }
    for rel, doc in per_file.items():
        _write_yaml(per_file_dir / rel, doc)

    return domain


def _write_enrichment(tmp: Path, enrichment: dict) -> Path:
    p = tmp / "enrichment.json"
    _write_json(p, enrichment)
    return p


def _read_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _run_tool(tmp_path: Path, domain: str, mode: str, enrichment_path: Path):
    script = Path(__file__).parent / "emit_skeleton.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    proc = subprocess.run(
        ["uv", "run", str(script), domain, "--mode", mode, "--enrichment", str(enrichment_path)],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _parse_header(stdout: str) -> dict:
    lines = stdout.splitlines()
    header_lines: list[str] = []
    for line in lines:
        if line == es._HEADER_SENTINEL:
            break
        header_lines.append(line)
    return json.loads("\n".join(header_lines))


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def test_preflight_missing_enrichment_file():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        rc = es.run(domain, "create", Path(tmp) / "nonexistent.json")
        assert rc == 2


def test_preflight_invalid_json():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        bad = Path(tmp) / "bad.json"
        bad.write_text("{ not valid json")
        rc = es.run(domain, "create", bad)
        assert rc == 1


def test_preflight_missing_domain():
    with tempfile.TemporaryDirectory() as tmp:
        enr = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(Path(tmp) / "nonexistent", "create", enr)
        assert rc == 2


# ---------------------------------------------------------------------------
# Enrichment schema validation
# ---------------------------------------------------------------------------

def test_enrichment_missing_skeleton_flow_diagram():
    enr = _minimal_enrichment()
    del enr["skeleton_flow_diagram"]
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1
        assert "enrichment.skeleton_flow_diagram" in stderr


def test_enrichment_output_variables_not_dict():
    enr = _minimal_enrichment(output_variables=[])
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1
        assert "enrichment.output_variables" in stderr


def test_enrichment_zero_primary_true():
    enr = _minimal_enrichment(output_variables={
        "eligible": {"description": "x", "primary": False},
        "denial_reasons": {"description": "y", "primary": False},
    })
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1
        assert "exactly one entry must have primary: true" in stderr


def test_enrichment_two_primary_true():
    enr = _minimal_enrichment(output_variables={
        "eligible": {"description": "x", "primary": True},
        "denial_reasons": {"description": "y", "primary": True},
    })
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1
        assert "exactly one entry must have primary: true" in stderr


def test_enrichment_constraints_not_a_list():
    enr = _minimal_enrichment()
    enr["prompt_context_additions"]["constraints"] = "not a list"
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1
        assert "prompt_context_additions.constraints" in stderr


def test_enrichment_primary_not_bool():
    enr = _minimal_enrichment()
    enr["output_variables"]["eligible"]["primary"] = "yes"
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 1


# ---------------------------------------------------------------------------
# Happy path: create mode
# ---------------------------------------------------------------------------

def test_create_mode_writes_all_six_files():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "create", enr_path)
        assert rc == 0
        for rel in (
            "specs/guidance/prompt-context.yaml",
            "specs/guidance/skeleton.yaml",
            "specs/guidance/flow_diagram.yaml",
            "specs/guidance/output-variables.yaml",
            "specs/guidance/input-variables.yaml",
            "specs/guidance/constants-and-tables.yaml",
        ):
            assert (domain / rel).exists(), rel


def test_create_mode_skeleton_shape():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/skeleton.yaml")
        assert "skeleton" in doc
        skel = doc["skeleton"]
        assert skel["inputs"] == ["gross_earned_income", "household_size"]
        assert skel["outputs"] == ["eligible", "denial_reasons"]
        comp = skel["computations"]
        # Single stage from default per-file fixture.
        assert len(comp) == 1
        assert comp[0]["stage"] == "deductions"
        assert "gross_earned_income" in comp[0]["variables"]
        assert comp[0]["exprs"]["gross_earned_income"] == "wages + tips"
        # flow_diagram has moved to its own file.
        assert "flow_diagram" not in skel


def test_create_mode_collision_fails():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={"skeleton.yaml": {"skeleton": {"inputs": []}}},
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 2
        assert "file exists" in stderr


# ---------------------------------------------------------------------------
# flow_diagram.yaml: shape, create-mode collision, replace/revise semantics
# ---------------------------------------------------------------------------

def test_flow_diagram_yaml_create_shape():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert list(doc.keys()) == ["flow_diagram"]
        assert doc["flow_diagram"] == "gross_earned_income\n   |\n   v\neligible"


def test_flow_diagram_yaml_serializes_as_literal_block():
    """Multi-line diagrams round-trip through `|` block style and preserve
    Unicode arrows."""
    enr = _minimal_enrichment(
        skeleton_flow_diagram="client_gross ──► exclusion ──► result\ndol_quarterly ──► /3 ──► result",
    )
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        raw = (domain / "specs/guidance/flow_diagram.yaml").read_text(encoding="utf-8")
        assert "flow_diagram: |" in raw
        assert "──►" in raw
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert (
            doc["flow_diagram"]
            == "client_gross ──► exclusion ──► result\ndol_quarterly ──► /3 ──► result"
        )


def test_flow_diagram_yaml_single_line_diagram_round_trips():
    enr = _minimal_enrichment(skeleton_flow_diagram="a -> b -> c")
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert doc["flow_diagram"] == "a -> b -> c"


def test_flow_diagram_yaml_create_collision_fails():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "flow_diagram.yaml": {"flow_diagram": "pre-existing"},
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 2
        assert "file exists" in stderr
        assert "flow_diagram.yaml" in stderr


def test_flow_diagram_yaml_replace_overwrites_analyst_content():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "flow_diagram.yaml": {"flow_diagram": "ANALYST EDITED"},
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "replace", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert doc["flow_diagram"] == "gross_earned_income\n   |\n   v\neligible"


def test_flow_diagram_yaml_revise_preserves_analyst_content():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "flow_diagram.yaml": {"flow_diagram": "ANALYST EDITED"},
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "revise", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert doc["flow_diagram"] == "ANALYST EDITED"


def test_flow_diagram_yaml_revise_fills_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))  # no existing flow_diagram.yaml
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "revise", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/flow_diagram.yaml")
        assert doc["flow_diagram"] == "gross_earned_income\n   |\n   v\neligible"


def test_skeleton_yaml_revise_drops_stale_flow_diagram():
    """A stale `flow_diagram:` left inside an existing skeleton.yaml must not
    be carried into the regenerated skeleton.yaml. It lives in flow_diagram.yaml now."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "skeleton.yaml": {
                    "skeleton": {
                        "inputs": ["analyst_input"],
                        "outputs": ["analyst_output"],
                        "computations": [{"stage": "analyst_stage", "variables": ["x"]}],
                        "flow_diagram": "STALE DIAGRAM INSIDE SKELETON",
                    },
                },
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "revise", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/skeleton.yaml")
        assert "flow_diagram" not in doc["skeleton"]
        # Analyst-edited spine fields still preserved.
        assert doc["skeleton"]["inputs"] == ["analyst_input"]


# ---------------------------------------------------------------------------
# Output-variables shape
# ---------------------------------------------------------------------------

def test_output_variables_with_examples():
    enr = _minimal_enrichment()
    enr["output_variables"]["eligible"]["examples"] = ["true", "false"]
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/output-variables.yaml")
        assert doc["eligible"]["examples"] == ["true", "false"]


# ---------------------------------------------------------------------------
# Replace mode
# ---------------------------------------------------------------------------

def test_replace_mode_overwrites_step4_files():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "skeleton.yaml": {"skeleton": {"inputs": ["stale"]}},
                "output-variables.yaml": {"eligible": {"description": "stale", "primary": True}},
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc = es.run(domain, "replace", enr_path)
        assert rc == 0
        skel = _read_yaml(domain / "specs/guidance/skeleton.yaml")
        assert skel["skeleton"]["inputs"] == ["gross_earned_income", "household_size"]
        ov = _read_yaml(domain / "specs/guidance/output-variables.yaml")
        assert ov["eligible"]["description"] == "Whether the household qualifies"


# ---------------------------------------------------------------------------
# Revise mode: preserve analyst edits
# ---------------------------------------------------------------------------

def test_revise_mode_preserves_analyst_description():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "output-variables.yaml": {
                    "eligible": {
                        "description": "Custom analyst description",
                        "primary": True,
                    },
                    "denial_reasons": {
                        "description": "Custom denial",
                        "primary": False,
                    },
                },
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "revise", enr_path)
        doc = _read_yaml(domain / "specs/guidance/output-variables.yaml")
        assert doc["eligible"]["description"] == "Custom analyst description"


def test_revise_mode_fills_missing_examples():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "output-variables.yaml": {
                    "eligible": {"description": "x", "primary": True},
                    "denial_reasons": {"description": "y", "primary": False},
                },
            },
        )
        enr = _minimal_enrichment()
        enr["output_variables"]["eligible"]["examples"] = ["true", "false"]
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "revise", enr_path)
        doc = _read_yaml(domain / "specs/guidance/output-variables.yaml")
        assert doc["eligible"]["examples"] == ["true", "false"]


def test_revise_mode_preserves_prompt_context_role_and_scope():
    """role: and scope: are written by /declare-target-ruleset; they must
    survive a revise pass."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "revise", enr_path)
        doc = _read_yaml(domain / "specs/guidance/prompt-context.yaml")
        assert doc["role"] == "policy analyst"
        assert doc["scope"] == "household eligibility"


# ---------------------------------------------------------------------------
# prompt-context additions dedup
# ---------------------------------------------------------------------------

def test_prompt_context_dedup_case_insensitive():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            prompt_context={
                "role": "x",
                "scope": "y",
                "constraints": ["Do not invent verification requirements."],
                "standards": [],
                "guidance": [],
                "edge_cases": [],
            },
        )
        enr = _minimal_enrichment()
        enr["prompt_context_additions"]["constraints"] = [
            "do not invent verification requirements.",  # case-different
        ]
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/prompt-context.yaml")
        # Should still be just one entry (dedup matched).
        assert doc["constraints"] == ["Do not invent verification requirements."]


def test_prompt_context_dedup_whitespace_normalized():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            prompt_context={
                "constraints": ["Do not invent verification requirements."],
                "standards": [],
                "guidance": [],
                "edge_cases": [],
            },
        )
        enr = _minimal_enrichment()
        enr["prompt_context_additions"]["constraints"] = [
            "Do not  invent  verification requirements.",  # extra spaces
        ]
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/prompt-context.yaml")
        assert doc["constraints"] == ["Do not invent verification requirements."]


def test_prompt_context_substantively_new_item_appended():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            prompt_context={
                "constraints": ["Existing."],
                "standards": [],
                "guidance": [],
                "edge_cases": [],
            },
        )
        enr = _minimal_enrichment()
        enr["prompt_context_additions"]["constraints"] = ["Something new."]
        enr_path = _write_enrichment(Path(tmp), enr)
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/prompt-context.yaml")
        assert doc["constraints"] == ["Existing.", "Something new."]


# ---------------------------------------------------------------------------
# Input-variables validation
# ---------------------------------------------------------------------------

def test_input_variables_warns_unknown_name_ref():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr = _minimal_enrichment()
        enr["input_variables"]["categories"][0]["fields"].append(
            {"name_ref": "nonexistent_field"}
        )
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 0
        assert "nonexistent_field" in stderr
        # Entry still emitted.
        doc = _read_yaml(domain / "specs/guidance/input-variables.yaml")
        names = [f["name_ref"] for f in doc["categories"][0]["fields"]]
        assert "nonexistent_field" in names


def test_input_variables_no_warning_for_known_field():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc, _stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 0
        assert "name_ref" not in stderr


# ---------------------------------------------------------------------------
# Constants and tables — provenance enforcement
# ---------------------------------------------------------------------------

def test_constants_emitted_with_provenance_from_signals():
    """Constant present in candidates → emitted with provenance copied."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "441.md.yaml": {
                    "sections": [
                        {
                            "heading": "## 441-1 Earned Income",
                            "stage": "deductions",
                            "computations": [{
                                "expr_hint": "x = a + b",
                                "description": "exceeds the Earned Income Limit"
                            }],
                        }
                    ]
                }
            },
        )
        enr = _minimal_enrichment()
        enr["constants_and_tables"] = {
            "Earned Income Limit": {"description": "Max allowable earned income"}
        }
        enr_path = _write_enrichment(Path(tmp), enr)
        rc = es.run(domain, "create", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/constants-and-tables.yaml")
        entries = doc["constants_and_tables"]
        assert len(entries) == 1
        e = entries[0]
        assert e["name"] == "Earned Income Limit"
        assert e["description"] == "Max allowable earned income"
        assert e["source_file"] == "input/policy_docs/441.md"
        assert e["source_section"] == "## 441-1 Earned Income"


def test_constants_invented_provenance_dropped():
    """Constant not in signals.candidates → dropped, listed in
    constants_dropped, with stderr warning."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr = _minimal_enrichment()
        enr["constants_and_tables"] = {
            "Made-Up Threshold": {"description": "Fabricated value"}
        }
        enr_path = _write_enrichment(Path(tmp), enr)
        rc, stdout, stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 0
        header = _parse_header(stdout)
        assert header["constants_dropped"] == [
            {"name": "Made-Up Threshold", "reason": "no matching candidate in signals"}
        ]
        assert "Made-Up Threshold" in stderr
        doc = _read_yaml(domain / "specs/guidance/constants-and-tables.yaml")
        assert doc["constants_and_tables"] == []


def test_constants_revise_preserves_existing_entry_with_provenance():
    """Revise mode: existing entry preserved verbatim even when not in candidates."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "constants-and-tables.yaml": {
                    "constants_and_tables": [
                        {
                            "name": "Hand-Added Constant",
                            "description": "Analyst added",
                            "source_file": "input/policy_docs/441.md",
                            "source_section": "## Some Section",
                        }
                    ]
                },
            },
        )
        enr = _minimal_enrichment()
        enr["constants_and_tables"] = {
            "Hand-Added Constant": {"description": "AI thinks differently"}
        }
        enr_path = _write_enrichment(Path(tmp), enr)
        rc = es.run(domain, "revise", enr_path)
        assert rc == 0
        doc = _read_yaml(domain / "specs/guidance/constants-and-tables.yaml")
        e = doc["constants_and_tables"][0]
        assert e["description"] == "Analyst added"
        assert e["source_file"] == "input/policy_docs/441.md"


# ---------------------------------------------------------------------------
# Skeleton: descriptive-only computations omitted from exprs
# ---------------------------------------------------------------------------

def test_skeleton_descriptive_only_excluded_from_exprs():
    """Computation with no expr_hint → not in any expr_hints record → not in exprs."""
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
                                {"description": "purely descriptive, no formula"},
                            ],
                        }
                    ]
                }
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/skeleton.yaml")
        comp = doc["skeleton"]["computations"]
        # Only `x` is in exprs.
        assert comp[0]["exprs"] == {"x": "a + b"}


def test_skeleton_unstaged_computations_get_no_stage_entry():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            per_file={
                "a.md.yaml": {
                    "sections": [
                        {
                            "heading": "## A",
                            "computations": [{"expr_hint": "x = a + b"}],
                        }
                    ]
                }
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "create", enr_path)
        doc = _read_yaml(domain / "specs/guidance/skeleton.yaml")
        comp = doc["skeleton"]["computations"]
        # Unstaged entry has no stage key.
        assert len(comp) == 1
        assert "stage" not in comp[0]
        assert comp[0]["variables"] == ["x"]
        assert comp[0]["exprs"] == {"x": "a + b"}


# ---------------------------------------------------------------------------
# Stdout shape
# ---------------------------------------------------------------------------

def test_stdout_header_json_and_sentinel():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        rc, stdout, _stderr = _run_tool(Path(tmp), "test_dom", "create", enr_path)
        assert rc == 0
        header = _parse_header(stdout)
        assert header["mode"] == "create"
        assert len(header["files_written"]) == 6
        assert "specs/guidance/skeleton.yaml" in header["files_written"]
        assert "specs/guidance/flow_diagram.yaml" in header["files_written"]
        # Human summary follows the sentinel.
        assert "Wrote 6 files:" in stdout
        assert "[CREATED]" in stdout


def test_stdout_files_written_round_trip_yaml():
    """All written files are valid YAML."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(Path(tmp))
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())
        es.run(domain, "create", enr_path)
        for rel in (
            "specs/guidance/prompt-context.yaml",
            "specs/guidance/skeleton.yaml",
            "specs/guidance/flow_diagram.yaml",
            "specs/guidance/output-variables.yaml",
            "specs/guidance/input-variables.yaml",
            "specs/guidance/constants-and-tables.yaml",
        ):
            doc = _read_yaml(domain / rel)
            assert doc is not None


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

def test_atomic_write_partial_failure_preserves_prior_state():
    """Mock os.replace to fail on the third call. First two writes complete;
    the remaining three are unchanged from their prior state."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            existing_outputs={
                "output-variables.yaml": {
                    "eligible": {"description": "PRIOR", "primary": True},
                    "denial_reasons": {"description": "PRIOR", "primary": False},
                },
                "input-variables.yaml": {
                    "categories": [
                        {
                            "category": "PRIOR_CAT",
                            "description": "Prior",
                            "fields": [],
                        }
                    ]
                },
            },
        )
        enr_path = _write_enrichment(Path(tmp), _minimal_enrichment())

        call_counter = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):
            call_counter["n"] += 1
            if call_counter["n"] == 3:
                raise OSError("simulated write failure")
            return real_replace(src, dst)

        with mock.patch("emit_skeleton.os.replace", side_effect=flaky_replace):
            rc = es.run(domain, "replace", enr_path)
        assert rc == 1
        # Third write (output-variables.yaml) failed — must keep PRIOR content.
        ov = _read_yaml(domain / "specs/guidance/output-variables.yaml")
        assert ov["eligible"]["description"] == "PRIOR"
        iv = _read_yaml(domain / "specs/guidance/input-variables.yaml")
        assert iv["categories"][0]["category"] == "PRIOR_CAT"
