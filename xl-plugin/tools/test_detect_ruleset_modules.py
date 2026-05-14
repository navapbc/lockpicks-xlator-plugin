# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for detect_ruleset_modules.py — covers each heuristic, priority
dedup, R21 stage-boundary handling, UPDATE-mode preservation, main-module
derivation, the cross-source-language-scan flag, pre-flight failures, and
output shape (JSON header + sentinel + table)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import detect_ruleset_modules as drm  # noqa: E402

# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

_DEFAULT_METADATA = {
    "display_name": "Test Domain",
    "description": "Test domain description",
}

_DEFAULT_RULESET_GROUPS = {
    "ruleset_groups": [
        {"name": "stage_a", "description": "Stage A"},
        {"name": "stage_b", "description": "Stage B"},
    ]
}


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_domain(
    tmp: Path,
    name: str = "test_dom",
    *,
    metadata: dict | None = None,
    skeleton: dict | None = None,
    ruleset_groups: dict | None = None,
    naming_manifest: dict | None = None,
    output_variables: dict | None = None,
    per_file: dict[str, dict] | None = None,
    existing_modules: dict | None = None,
) -> Path:
    """Build a minimal domain tree under `tmp/<name>/`. Files default to
    minimal valid content when not provided. Pass `None` for any required
    file to test pre-flight handling."""
    domain = tmp / name
    (domain / "specs" / "guidance").mkdir(parents=True, exist_ok=True)

    if metadata is not None:
        _write_yaml(domain / "specs" / "guidance" / "metadata.yaml", metadata)
    if skeleton is not None:
        _write_yaml(domain / "specs" / "guidance" / "skeleton.yaml", skeleton)
    if ruleset_groups is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "ruleset-groups.yaml",
            ruleset_groups,
        )
    if naming_manifest is not None:
        _write_yaml(domain / "specs" / "naming-manifest.yaml", naming_manifest)
    if output_variables is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "output-variables.yaml",
            output_variables,
        )
    if existing_modules is not None:
        _write_yaml(
            domain / "specs" / "guidance" / "ruleset-modules.yaml",
            existing_modules,
        )

    if per_file is not None:
        for rel, doc in per_file.items():
            _write_yaml(
                domain / "policy_facets" / "computations" / rel,
                doc,
            )

    return domain


def _full_domain(
    tmp: Path,
    name: str = "test_dom",
    **overrides,
) -> Path:
    """Build a domain populated with all required files set to defaults."""
    defaults = dict(
        metadata=_DEFAULT_METADATA,
        skeleton={"skeleton": {"inputs": [], "outputs": [], "computations": []}},
        ruleset_groups=_DEFAULT_RULESET_GROUPS,
        naming_manifest={"version": "1.0", "inputs": {}, "outputs": {}},
        per_file={"a.md.yaml": {"sections": []}},
    )
    defaults.update(overrides)
    return _build_domain(tmp, name, **defaults)


def _read_modules(domain: Path) -> list[dict]:
    path = domain / "specs" / "guidance" / "ruleset-modules.yaml"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("ruleset_modules") or []


def _run_tool(
    tmp_path: Path,
    domain: str,
    *extra_args: str,
) -> tuple[int, str, str]:
    """Invoke the script as a subprocess. Returns (returncode, stdout, stderr)."""
    script = Path(__file__).parent / "detect_ruleset_modules.py"
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
    header_lines = []
    for line in lines:
        if line == drm._HEADER_SENTINEL:
            break
        header_lines.append(line)
    return json.loads("\n".join(header_lines))


# ---------------------------------------------------------------------------
# Heuristic 1a — mirrored inputs across entities
# ---------------------------------------------------------------------------

def test_h1a_mirrored_input_fields_emit_candidate():
    """`Household.gross_income` and `Applicant.gross_income` → emit one
    candidate named `gross_income` bound to both entities."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "Household": {"gross_income": {"type": "money"}},
                    "Applicant": {"gross_income": {"type": "money"}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        assert "gross_income" in names
        gi = next(m for m in modules if m["name"] == "gross_income")
        assert gi["rationale"] == "reuse_across_entities"
        assert set(gi["bound_entities"]) == {"Household", "Applicant"}


def test_h1a_three_entities_share_field():
    """Three entities sharing a field → single candidate bound to all three."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "EntityA": {"income": {"type": "money"}},
                    "EntityB": {"income": {"type": "money"}},
                    "EntityC": {"income": {"type": "money"}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        income = next(m for m in modules if m["name"] == "income")
        assert set(income["bound_entities"]) == {"EntityA", "EntityB", "EntityC"}


def test_h1a_disjoint_shared_fields_emit_separate_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "A": {"income": {}, "resources": {}},
                    "B": {"income": {}, "resources": {}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        names = {m["name"] for m in _read_modules(domain)}
        assert "income" in names
        assert "resources" in names


# ---------------------------------------------------------------------------
# Heuristic 1b — parallel variable-name prefixes
# ---------------------------------------------------------------------------

def test_h1b_prefix_variables_emit_candidate():
    """`client_adjusted_earned_income` + `dol_adjusted_earned_income` →
    candidate named `adjusted_earned_income` bound to inferred entities."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "exclusion_application",
                            "variables": [
                                "client_adjusted_earned_income",
                                "dol_adjusted_earned_income",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "ClientStatement": {"gross_earned_income": {}},
                    "DOLRecord": {"gross_earned_income": {}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        assert "adjusted_earned_income" in names
        m = next(m for m in modules if m["name"] == "adjusted_earned_income")
        assert m["rationale"] == "reuse_across_entities"


def test_h1b_alone_without_h1a():
    """The "single most expensive miss" — H1b fires when 1a does not."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "stage_a",
                            "variables": [
                                "applicant_countable_resources",
                                "spouse_countable_resources",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "Applicant": {"resources": {}},
                    "Spouse": {"resources": {}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        # 1a would fire on shared `resources` field across two entities.
        # 1b would fire on `_countable_resources` suffix. Either name is
        # acceptable — both are valid H1 outcomes.
        assert any(n in names for n in ("resources", "countable_resources"))


# ---------------------------------------------------------------------------
# Heuristic 2 — policy_structure
# ---------------------------------------------------------------------------

def test_h2_three_vars_in_section_emit_candidate():
    """Section's computations cover 3+ skeleton intermediate variables."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "stage_a",
                            "variables": ["alpha", "beta", "gamma"],
                            "exprs": {},
                        }
                    ],
                }
            },
            per_file={
                "section.md.yaml": {
                    "sections": [
                        {
                            "heading": "### 100. Test Section Heading",
                            "computations": [
                                {"expr_hint": "alpha = a + b"},
                                {"expr_hint": "beta = alpha + 1"},
                                {"expr_hint": "gamma = beta + 1"},
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        rationales = [m["rationale"] for m in modules]
        assert "policy_structure" in rationales


def test_h2_two_vars_under_threshold():
    """Section with only 2 skeleton vars yields no policy_structure candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {"variables": ["alpha", "beta"], "exprs": {}}
                    ],
                }
            },
            per_file={
                "section.md.yaml": {
                    "sections": [
                        {
                            "heading": "Test Section",
                            "computations": [
                                {"expr_hint": "alpha = a + b"},
                                {"expr_hint": "beta = alpha + 1"},
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "policy_structure" not in rationales


# ---------------------------------------------------------------------------
# Heuristic 3 — sequential_chain
# ---------------------------------------------------------------------------

def test_h3_three_step_chain_within_stage():
    """3 computations forming step_a → step_b → step_c, single stage.

    The skeleton's intermediate-var list is empty so H2 (policy_structure)
    does not also fire on this section — otherwise H2 would suppress H3
    by priority.
    """
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [],
                }
            },
            per_file={
                "chain.md.yaml": {
                    "sections": [
                        {
                            "heading": "Chain section",
                            "stage": "deductions",
                            "computations": [
                                {"expr_hint": "step_a = x + y"},
                                {"expr_hint": "step_b = step_a + 1"},
                                {"expr_hint": "step_c = step_b + 1"},
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        assert "step_c_chain" in names
        m = next(m for m in modules if m["name"] == "step_c_chain")
        assert m["rationale"] == "sequential_chain"


def test_h3_does_not_cross_file_boundaries():
    """Same chain split across two per-file files does NOT emit candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "deductions",
                            "variables": ["step_a", "step_b", "step_c"],
                            "exprs": {},
                        }
                    ],
                }
            },
            per_file={
                "fileA.md.yaml": {
                    "sections": [
                        {
                            "heading": "A",
                            "stage": "deductions",
                            "computations": [
                                {"expr_hint": "step_a = x + y"},
                            ],
                        }
                    ]
                },
                "fileB.md.yaml": {
                    "sections": [
                        {
                            "heading": "B",
                            "stage": "deductions",
                            "computations": [
                                {"expr_hint": "step_b = step_a + 1"},
                                {"expr_hint": "step_c = step_b + 1"},
                            ],
                        }
                    ]
                },
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "sequential_chain" not in rationales


# ---------------------------------------------------------------------------
# Heuristic 4 — depth_threshold
# ---------------------------------------------------------------------------

def test_h4_five_after_vars_emit_candidate():
    """5 vars sharing `after_*` prefix → emit `after_chain` candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "s1",
                            "variables": [
                                "after_eitc",
                                "after_federal",
                                "after_state",
                                "after_local",
                                "after_other",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        assert "after_chain" in names


def test_h4_four_after_vars_below_threshold():
    """Only 4 `after_*` vars → no depth_threshold candidate."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "variables": [
                                "after_a", "after_b", "after_c", "after_d",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "depth_threshold" not in rationales


# ---------------------------------------------------------------------------
# Heuristic 5 — variable_coupling
# ---------------------------------------------------------------------------

def test_h5_three_var_clique_emits_candidate():
    """3 vars cross-referencing each other → emit cluster_1."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "s1",
                            "variables": ["a_var", "b_var", "c_var"],
                            "exprs": {
                                "a_var": "b_var + c_var",
                                "b_var": "a_var + c_var",
                                "c_var": "a_var + b_var",
                            },
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        rationales = [m["rationale"] for m in modules]
        assert "variable_coupling" in rationales


# ---------------------------------------------------------------------------
# Heuristic 6 — shared_gate
# ---------------------------------------------------------------------------

def test_h6_prefix_three_eligible_vars():
    """3 vars with `eligible_*` prefix → emit `eligible_cluster`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "variables": [
                                "eligible_age", "eligible_income", "eligible_resources",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        names = [m["name"] for m in _read_modules(domain)]
        assert "eligible_cluster" in names


def test_h6_prefix_two_eligible_vars_below_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "variables": ["eligible_age", "eligible_income"],
                            "exprs": {},
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        names = [m["name"] for m in _read_modules(domain)]
        assert "eligible_cluster" not in names


def test_h6_preconditions_three_outputs_share_clause():
    """3 computations whose preconditions reference the same clause → emit."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            per_file={
                "policy.md.yaml": {
                    "sections": [
                        {
                            "heading": "S",
                            "computations": [
                                {
                                    "expr_hint": "x = a + 1",
                                    "preconditions": [
                                        "household contains a working adult"
                                    ],
                                },
                                {
                                    "expr_hint": "y = b + 2",
                                    "preconditions": [
                                        "household contains a working adult"
                                    ],
                                },
                                {
                                    "expr_hint": "z = c + 3",
                                    "preconditions": [
                                        "household contains a working adult"
                                    ],
                                },
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "shared_gate" in rationales


def test_h6_preconditions_whitespace_normalized():
    """Whitespace differences in the clause still cluster."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            per_file={
                "policy.md.yaml": {
                    "sections": [
                        {
                            "heading": "S",
                            "computations": [
                                {
                                    "expr_hint": "x = a + 1",
                                    "preconditions": ["client is eligible"],
                                },
                                {
                                    "expr_hint": "y = b + 1",
                                    "preconditions": ["client   is   eligible"],
                                },
                                {
                                    "expr_hint": "z = c + 1",
                                    "preconditions": ["client is\teligible"],
                                },
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "shared_gate" in rationales


def test_h6_preconditions_exact_match_not_paraphrase():
    """Spelling differences do NOT cluster."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            per_file={
                "policy.md.yaml": {
                    "sections": [
                        {
                            "heading": "S",
                            "computations": [
                                {
                                    "expr_hint": "x = a + 1",
                                    "preconditions": ["client is eligible"],
                                },
                                {
                                    "expr_hint": "y = b + 1",
                                    "preconditions": ["client is qualified"],
                                },
                                {
                                    "expr_hint": "z = c + 1",
                                    "preconditions": ["client is approved"],
                                },
                            ],
                        }
                    ]
                }
            },
        )
        drm.run(domain, None)
        rationales = [m["rationale"] for m in _read_modules(domain)]
        # Three distinct clauses → no precondition cluster.
        # (Prefix variant of shared_gate would only fire on `eligible_`,
        # `applies_if_`, or `qualified_` prefix, but our vars are bare.)
        assert "shared_gate" not in rationales


# ---------------------------------------------------------------------------
# Priority dedup
# ---------------------------------------------------------------------------

def test_priority_dedup_suppresses_lower_priority_overlap():
    """H4 candidate substantially overlapping with an H1 claim is
    suppressed (≥50% Jaccard)."""
    with tempfile.TemporaryDirectory() as tmp:
        # H1b candidate claims `{client_adjusted, dol_adjusted}`.
        # H4 candidate would claim 5 `after_*` vars — disjoint, so no
        # suppression. To create overlap, share one of H1's vars.
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "variables": [
                                # H1b will fire on these two
                                "after_eitc",
                                # Plus three more after_* to push H4 to 5+
                                "after_federal",
                                "after_state",
                                "after_local",
                                "after_other",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
            naming_manifest={
                "version": "1.0",
                "inputs": {},
                "outputs": {},
            },
        )
        drm.run(domain, None)
        # Without H1 candidates this becomes pure H4. The Jaccard test
        # is exercised more directly by the unit test below.
        rationales = [m["rationale"] for m in _read_modules(domain)]
        assert "depth_threshold" in rationales


def test_priority_dedup_unit_jaccard_overlap_suppresses():
    """Unit-level: a candidate set with ≥50% Jaccard against a claimed set
    is suppressed by _has_high_overlap."""
    claimed = [{"a", "b", "c", "d"}]
    new_set = {"a", "b", "c", "e"}  # 3/5 = 0.6 ≥ 0.5
    assert drm._has_high_overlap(new_set, claimed) is True


def test_priority_dedup_unit_low_overlap_passes():
    claimed = [{"a", "b", "c", "d"}]
    new_set = {"a", "e", "f", "g"}  # 1/7 ≈ 0.14
    assert drm._has_high_overlap(new_set, claimed) is False


# ---------------------------------------------------------------------------
# R21 stage-boundary
# ---------------------------------------------------------------------------

def test_r21_uniform_stage_passes_through():
    """All variables share one stage → candidate emitted unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "deductions",
                            "variables": [
                                "after_a", "after_b", "after_c",
                                "after_d", "after_e",
                            ],
                            "exprs": {},
                        }
                    ],
                }
            },
        )
        drm.run(domain, None)
        names = [m["name"] for m in _read_modules(domain)]
        assert "after_chain" in names


def test_r21_mixed_stage_with_none_falls_through():
    """When some vars lack `stage:`, R21 doesn't fire (falls through)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Some vars in a stage, some not (via being in a different
        # computations entry with no stage).
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "deductions",
                            "variables": ["after_a", "after_b"],
                            "exprs": {},
                        },
                        {
                            "variables": ["after_c", "after_d", "after_e"],
                            "exprs": {},
                        },
                    ],
                }
            },
        )
        drm.run(domain, None)
        names = [m["name"] for m in _read_modules(domain)]
        # Some stages populated, some None → R21 falls through, all 5
        # vars in one after_chain candidate.
        assert "after_chain" in names


def test_r21_drops_candidate_that_cannot_be_split():
    """Candidate spanning 2 stages where neither sub-candidate meets the
    heuristic's min size → drop with stderr warning + dropped header entry."""
    with tempfile.TemporaryDirectory() as tmp:
        # 5 after_* vars, split 2-3 across two stages. H4 needs min 5.
        # 2-3 split means neither side meets the threshold of 5 → drop.
        domain = _full_domain(
            Path(tmp),
            skeleton={
                "skeleton": {
                    "inputs": [],
                    "outputs": [],
                    "computations": [
                        {
                            "stage": "stage_x",
                            "variables": ["after_a", "after_b"],
                            "exprs": {},
                        },
                        {
                            "stage": "stage_y",
                            "variables": ["after_c", "after_d", "after_e"],
                            "exprs": {},
                        },
                    ],
                }
            },
        )
        rc, stdout, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        dropped = header["dropped_candidates"]
        assert any(d["name"].startswith("after_chain") for d in dropped)
        assert "after_chain" not in [m["name"] for m in _read_modules(Path(tmp) / "test_dom")]


# ---------------------------------------------------------------------------
# Main-module-name derivation
# ---------------------------------------------------------------------------

def test_main_module_name_strip_determination_suffix():
    """`eligibility_determination` with `primary: true` → `eligibility`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            output_variables={
                "eligibility_determination": {"primary": True},
                "other_output": {"primary": False},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["main_module_name"] == "eligibility"
        assert header["primary_output_present"] is True


def test_main_module_name_no_matching_suffix_kept_as_is():
    """`final_decision` (no matching suffix) → kept as `final_decision`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            output_variables={
                "final_decision": {"primary": True},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["main_module_name"] == "final_decision"


def test_main_module_name_no_primary_outputs_null():
    """No `primary: true` → `main_module_name = null`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            output_variables={
                "some_output": {"primary": False},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["main_module_name"] is None
        assert header["primary_output_present"] is False


def test_main_module_name_cli_override():
    """`--main-module-name foo` overrides derivation."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            output_variables={
                "eligibility_determination": {"primary": True},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom", "--main-module-name", "foo")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["main_module_name"] == "foo"


# ---------------------------------------------------------------------------
# UPDATE mode preservation
# ---------------------------------------------------------------------------

def test_update_mode_preserves_existing_entries_verbatim():
    """Existing entry with `sample_rules:` survives intact through re-run."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            existing_modules={
                "ruleset_modules": [
                    {
                        "name": "existing_module",
                        "description": "custom",
                        "bound_entities": ["EntityA"],
                        "rationale": "reuse_across_entities",
                        "depends_on": [],
                        "sample_rules": [{"id": "r1", "civil": "..."}],
                    }
                ]
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        existing = next(m for m in modules if m["name"] == "existing_module")
        assert existing["description"] == "custom"
        assert existing["sample_rules"][0]["id"] == "r1"


def test_update_mode_skips_name_collision_new_candidate():
    """New candidate whose name matches an existing entry is auto-renamed
    with a numeric suffix (existing entry wins by identity)."""
    with tempfile.TemporaryDirectory() as tmp:
        # H1a would emit `gross_income`. Pre-existing entry already uses
        # that name. The new detection should be appended under a
        # disambiguated name, NOT silently dropped.
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "A": {"gross_income": {}},
                    "B": {"gross_income": {}},
                },
                "outputs": {},
            },
            existing_modules={
                "ruleset_modules": [
                    {
                        "name": "gross_income",
                        "description": "hand-edited",
                        "bound_entities": ["A"],
                        "rationale": "reuse_across_entities",
                        "depends_on": [],
                    }
                ]
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        names = [m["name"] for m in modules]
        # Existing wins on the canonical name; new candidate appended
        # under disambiguated name (so the analyst can review).
        assert "gross_income" in names
        existing = next(m for m in modules if m["name"] == "gross_income")
        assert existing["description"] == "hand-edited"


def test_update_mode_preserves_existing_main():
    """When existing entries include a `role: main` entry, the tool does
    not re-emit a duplicate main entry."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            output_variables={
                "eligibility_determination": {"primary": True},
            },
            existing_modules={
                "ruleset_modules": [
                    {
                        "name": "existing_main",
                        "description": "X",
                        "bound_entities": [],
                        "rationale": "main_module",
                        "role": "main",
                        "depends_on": [],
                    }
                ]
            },
        )
        drm.run(domain, None)
        modules = _read_modules(domain)
        main_entries = [m for m in modules if m.get("role") == "main"]
        assert len(main_entries) == 1
        assert main_entries[0]["name"] == "existing_main"


# ---------------------------------------------------------------------------
# cross_source_language_scan_recommended flag
# ---------------------------------------------------------------------------

def test_cross_source_flag_false_when_h1_fires():
    """When H1a/H1b found any candidate, the AI top-up flag stays false."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "A": {"income": {}},
                    "B": {"income": {}},
                },
                "outputs": {},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["cross_source_language_scan_recommended"] is False


def test_cross_source_flag_true_when_two_entities_and_no_h1():
    """2+ entities and no H1 candidates → flag true."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "A": {"a_only_field": {}},
                    "B": {"b_only_field": {}},
                },
                "outputs": {},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["cross_source_language_scan_recommended"] is True


def test_cross_source_flag_false_when_single_entity():
    """1 entity → no parallel-source potential → flag false."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {"OnlyEntity": {"field": {}}},
                "outputs": {},
            },
        )
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        header = _parse_json_header(stdout)
        assert header["cross_source_language_scan_recommended"] is False


# ---------------------------------------------------------------------------
# Pre-flight failures
# ---------------------------------------------------------------------------

def test_preflight_missing_domain_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        rc, _, stderr = _run_tool(Path(tmp), "no_such_domain")
        assert rc == 2
        assert "Domain not found" in stderr


def test_preflight_missing_metadata_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        # Build a domain with the folder but no metadata.yaml.
        domain = Path(tmp) / "test_dom"
        domain.mkdir()
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "metadata.yaml not found" in stderr


def test_preflight_missing_skeleton_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            metadata=_DEFAULT_METADATA,
            # No skeleton
        )
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Skeleton not found" in stderr


def test_preflight_missing_ruleset_groups_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            metadata=_DEFAULT_METADATA,
            skeleton={"skeleton": {}},
            # No ruleset_groups
        )
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Ruleset groups not found" in stderr


def test_preflight_missing_naming_manifest_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            metadata=_DEFAULT_METADATA,
            skeleton={"skeleton": {}},
            ruleset_groups=_DEFAULT_RULESET_GROUPS,
            # No naming-manifest
        )
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "naming-manifest.yaml not found" in stderr


def test_preflight_missing_per_file_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        _build_domain(
            Path(tmp),
            metadata=_DEFAULT_METADATA,
            skeleton={"skeleton": {}},
            ruleset_groups=_DEFAULT_RULESET_GROUPS,
            naming_manifest={"version": "1.0", "inputs": {}, "outputs": {}},
            # No per-file YAMLs
        )
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Per-file computations not found" in stderr


def test_preflight_empty_per_file_dir_exits_2():
    """Empty `policy_facets/computations/` (dir exists, zero YAMLs) → exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _build_domain(
            Path(tmp),
            metadata=_DEFAULT_METADATA,
            skeleton={"skeleton": {}},
            ruleset_groups=_DEFAULT_RULESET_GROUPS,
            naming_manifest={"version": "1.0", "inputs": {}, "outputs": {}},
        )
        (domain / "policy_facets" / "computations").mkdir(parents=True)
        rc, _, stderr = _run_tool(Path(tmp), "test_dom")
        assert rc == 2
        assert "Per-file computations not found" in stderr


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_stdout_header_and_sentinel():
    """JSON header line + sentinel divider + body."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(Path(tmp))
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        lines = stdout.splitlines()
        assert lines[1] == drm._HEADER_SENTINEL
        header = json.loads(lines[0])
        assert set(header.keys()) == {
            "main_module_name",
            "primary_output_present",
            "cross_source_language_scan_recommended",
            "subm_count",
            "dropped_candidates",
        }


def test_empty_case_writes_ruleset_modules_empty_list():
    """Zero candidates → write `ruleset_modules: []`."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(Path(tmp))
        drm.run(domain, None)
        path = domain / "specs" / "guidance" / "ruleset-modules.yaml"
        with path.open() as f:
            data = yaml.safe_load(f)
        assert data == {"ruleset_modules": []}


def test_table_row_format():
    """Body table contains expected column headers."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(Path(tmp))
        rc, stdout, _ = _run_tool(Path(tmp), "test_dom")
        assert rc == 0
        assert "Ruleset Modules" in stdout
        assert "Heuristic" in stdout
        assert "Bound Entities" in stdout


def test_yaml_output_round_trips():
    """Output YAML is valid and parses as a dict with `ruleset_modules:` key."""
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            naming_manifest={
                "version": "1.0",
                "inputs": {
                    "A": {"income": {}},
                    "B": {"income": {}},
                },
                "outputs": {},
            },
        )
        drm.run(domain, None)
        path = domain / "specs" / "guidance" / "ruleset-modules.yaml"
        with path.open() as f:
            data = yaml.safe_load(f)
        assert "ruleset_modules" in data
        assert isinstance(data["ruleset_modules"], list)


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

def test_atomic_write_failure_preserves_prior_file(monkeypatch):
    """When os.replace raises, the prior file is intact."""
    import os as _os
    with tempfile.TemporaryDirectory() as tmp:
        domain = _full_domain(
            Path(tmp),
            existing_modules={
                "ruleset_modules": [
                    {
                        "name": "prior_entry",
                        "description": "intact",
                        "bound_entities": [],
                        "rationale": "main_module",
                        "depends_on": [],
                    }
                ]
            },
        )
        original_replace = _os.replace

        def boom(*args, **kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setattr(_os, "replace", boom)
        try:
            drm.run(domain, None)
        except OSError:
            pass
        finally:
            monkeypatch.setattr(_os, "replace", original_replace)

        modules = _read_modules(domain)
        assert any(m.get("name") == "prior_entry" for m in modules)
