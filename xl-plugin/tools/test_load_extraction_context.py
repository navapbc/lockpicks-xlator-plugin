# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for load_extraction_context.py — covers the five in-memory
structures, SHA maps, drift check, work-list resolution, program
auto-detection, and pre-flight failure paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))

import load_extraction_context as lec  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_init(repo_root: Path) -> None:
    """Init a git repo at repo_root so `git hash-object` works."""
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )


def _git_hash_object(repo_root: Path, path: Path) -> str:
    result = subprocess.run(
        ["git", "hash-object", str(path)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _minimal_domain(domain_dir: Path) -> None:
    """Write the minimum required-files set so pre-flight passes."""
    _write_yaml(
        domain_dir / "specs" / "guidance" / "metadata.yaml",
        {"display_name": "Test Ruleset", "description": "Test."},
    )
    _write_yaml(
        domain_dir / "specs" / "guidance" / "prompt-context.yaml",
        {"role": "tester", "scope": "test"},
    )
    _write_yaml(
        domain_dir / "specs" / "naming-manifest.yaml",
        {
            "version": "1.0",
            "inputs": {"Household": {"size": {"type": "int"}}},
            "outputs": {"eligible": {"type": "bool"}},
        },
    )
    _write_yaml(
        domain_dir / "specs" / "guidance" / "output-variables.yaml",
        {"eligible": {"description": "Eligibility", "primary": True}},
    )
    _write_yaml(
        domain_dir / "policy_facets" / "input-index.yaml",
        {"files": {}},
    )


# ---------------------------------------------------------------------------
# Per-builder unit tests
# ---------------------------------------------------------------------------

def test_confirmed_exprs_flatten_multi_stage():
    skeleton = {
        "skeleton": {
            "computations": [
                {"stage": "s1", "exprs": {"x": "a + b", "y": "x * 2"}},
                {"stage": "s2", "exprs": {"z": "y - 1"}},
            ]
        }
    }
    out = lec._build_confirmed_exprs(skeleton)
    assert out == {"x": "a + b", "y": "x * 2", "z": "y - 1"}


def test_confirmed_exprs_skeleton_absent():
    assert lec._build_confirmed_exprs(None) == {}
    assert lec._build_confirmed_exprs({}) == {}
    assert lec._build_confirmed_exprs({"skeleton": {}}) == {}


def test_confirmed_exprs_skips_non_dict_exprs():
    skeleton = {"skeleton": {"computations": [{"variables": ["x"]}]}}
    assert lec._build_confirmed_exprs(skeleton) == {}


def test_example_rules_passthrough():
    doc = {
        "sample_rules": [
            {"id": "r1", "rule_type": "computed", "source": "...", "civil": "yaml"},
            {"id": "r2", "rule_type": "table-lookup", "source": "...", "civil": "yaml"},
        ],
        "missing_info": ["x"],  # not consumed
    }
    out = lec._build_example_rules(doc)
    assert len(out) == 2
    assert out[0]["id"] == "r1"


def test_example_rules_absent():
    assert lec._build_example_rules(None) == []
    assert lec._build_example_rules({}) == []


def test_guidance_output_set_flat_list():
    assert lec._build_guidance_output_set(["a", "b", "c"]) == ["a", "b", "c"]


def test_guidance_output_set_absent():
    assert lec._build_guidance_output_set(None) == []
    # File is a top-level list, not a dict — a dict means malformed.
    assert lec._build_guidance_output_set({}) == []


def test_constants_tables_seed_full_entries():
    doc = {
        "constants_and_tables": [
            {
                "name": "t1",
                "description": "d1",
                "source_file": "input/policy_docs/x.md",
                "source_section": "§1",
            },
            {
                "name": "t2",
                "description": "d2",
                "source_file": "input/policy_docs/y.md",
                "source_section": "§2",
            },
        ]
    }
    seed, warnings = lec._build_constants_tables_seed(doc)
    assert len(seed) == 2
    assert warnings == []


def test_constants_tables_seed_drops_missing_source_file():
    doc = {
        "constants_and_tables": [
            {"name": "t1", "description": "d", "source_section": "§"},
        ]
    }
    seed, warnings = lec._build_constants_tables_seed(doc)
    assert seed == []
    assert len(warnings) == 1
    assert "source_file" in warnings[0]


def test_constants_tables_seed_absent():
    assert lec._build_constants_tables_seed(None) == ([], [])


def test_per_module_sample_rules_subset():
    doc = {
        "ruleset_modules": [
            {"name": "m1", "sample_rules": [{"id": "r1"}]},
            {"name": "m2"},  # no sample_rules
            {"name": "m3", "sample_rules": []},  # empty
        ]
    }
    out = lec._build_per_module_sample_rules(doc)
    assert list(out.keys()) == ["m1"]
    assert out["m1"][0]["id"] == "r1"


def test_per_module_sample_rules_absent():
    assert lec._build_per_module_sample_rules(None) == {}


# ---------------------------------------------------------------------------
# Drift-check unit tests
# ---------------------------------------------------------------------------

def test_drift_no_drift_when_shas_match(tmp_path: Path):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    source = domain / "input" / "policy_docs" / "foo.md"
    _write_text(source, "hello\n")
    real_sha = _git_hash_object(tmp_path, source)
    index = {"files": {"input/policy_docs/foo.md": {"sha": real_sha,
                                                     "md_quality": {"score": 100}}}}
    shas, drifted, missing = lec._check_input_index_drift(domain, index)
    assert shas == {"input/policy_docs/foo.md": real_sha}
    assert drifted == []
    assert missing == []


def test_drift_detected_when_working_tree_edited(tmp_path: Path):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    source = domain / "input" / "policy_docs" / "foo.md"
    _write_text(source, "hello\n")
    stale = "0" * 40
    index = {"files": {"input/policy_docs/foo.md": {"sha": stale,
                                                     "md_quality": {"score": 100}}}}
    _, drifted, missing = lec._check_input_index_drift(domain, index)
    assert drifted == ["input/policy_docs/foo.md"]
    assert missing == []


def test_drift_skips_rejected_entries(tmp_path: Path):
    """Entries with md_quality.score < threshold (rejected) are skipped."""
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    # No source file on disk — would normally report missing — but rejected.
    index = {"files": {"input/policy_docs/foo.md": {"sha": "abc",
                                                     "md_quality": {"score": 10}}}}
    shas, drifted, missing = lec._check_input_index_drift(domain, index)
    assert shas == {}
    assert drifted == []
    assert missing == []


def test_drift_reports_missing_source(tmp_path: Path):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    domain.mkdir(parents=True, exist_ok=True)
    index = {"files": {"input/policy_docs/foo.md": {"sha": "abc",
                                                     "md_quality": {"score": 100}}}}
    _, drifted, missing = lec._check_input_index_drift(domain, index)
    assert missing == ["input/policy_docs/foo.md"]


def test_drift_skips_untracked_recorded_sha(tmp_path: Path):
    """When the index records 'untracked', drift comparison is skipped."""
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    source = domain / "input" / "policy_docs" / "foo.md"
    _write_text(source, "hello\n")
    index = {"files": {"input/policy_docs/foo.md": {"sha": "untracked",
                                                     "md_quality": {"score": 100}}}}
    _, drifted, missing = lec._check_input_index_drift(domain, index)
    assert drifted == []
    assert missing == []


# ---------------------------------------------------------------------------
# Guidance SHA map
# ---------------------------------------------------------------------------

def test_guidance_shas_covers_yaml_files_and_manifest(tmp_path: Path):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _write_yaml(domain / "specs" / "guidance" / "skeleton.yaml", {"skeleton": {}})
    _write_yaml(domain / "specs" / "guidance" / "metadata.yaml", {"x": 1})
    _write_text(domain / "specs" / "guidance" / ".facets-manifest.yaml", "files: {}\n")
    _write_yaml(domain / "specs" / "naming-manifest.yaml", {"version": "1.0"})

    shas = lec._build_guidance_shas(domain)
    keys = set(shas.keys())
    assert "specs/guidance/skeleton.yaml" in keys
    assert "specs/guidance/metadata.yaml" in keys
    # Dot-prefixed file skipped:
    assert "specs/guidance/.facets-manifest.yaml" not in keys
    assert "specs/naming-manifest.yaml" in keys
    # SHA is real:
    skel_sha = _git_hash_object(tmp_path, domain / "specs" / "guidance" / "skeleton.yaml")
    assert shas["specs/guidance/skeleton.yaml"] == skel_sha


def test_guidance_shas_empty_when_no_guidance(tmp_path: Path):
    domain = tmp_path / "dom"
    domain.mkdir(parents=True)
    assert lec._build_guidance_shas(domain) == {}


# ---------------------------------------------------------------------------
# Work-list resolution
# ---------------------------------------------------------------------------

def test_work_list_no_ruleset_modules_with_program(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    wl = lec._build_work_list(domain, "prog", None)
    assert wl == [{
        "name": "prog",
        "role": "main",
        "action": "generate",
        "civil_file": "specs/prog.civil.yaml",
    }]


def test_work_list_civil_exists_marks_reference(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(domain / "specs" / "prog.civil.yaml", {"name": "prog"})
    wl = lec._build_work_list(domain, "prog", None)
    assert wl[0]["action"] == "reference"


def test_work_list_no_program_no_modules(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    assert lec._build_work_list(domain, None, None) == []


def test_work_list_sub_modules_before_main(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    doc = {
        "ruleset_modules": [
            {"name": "sub_a", "bound_entities": ["E1"]},
            {"name": "sub_b"},
            {"name": "main_x", "role": "main"},
        ]
    }
    wl = lec._build_work_list(domain, "main_x", doc)
    assert [e["name"] for e in wl] == ["sub_a", "sub_b", "main_x"]
    assert wl[0]["role"] == "sub"
    assert wl[-1]["role"] == "main"
    assert wl[0]["bound_entities"] == ["E1"]


def test_work_list_existing_sub_module_marks_reference(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(domain / "specs" / "sub_a.civil.yaml", {"name": "sub_a"})
    doc = {
        "ruleset_modules": [
            {"name": "sub_a"},
            {"name": "sub_b"},
            {"name": "main_x", "role": "main"},
        ]
    }
    wl = lec._build_work_list(domain, "main_x", doc)
    by_name = {e["name"]: e for e in wl}
    assert by_name["sub_a"]["action"] == "reference"
    assert by_name["sub_b"]["action"] == "generate"
    assert by_name["main_x"]["action"] == "generate"


# ---------------------------------------------------------------------------
# Program auto-detection
# ---------------------------------------------------------------------------

def test_program_resolves_to_role_main(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    doc = {"ruleset_modules": [{"name": "x"}, {"name": "main_x", "role": "main"}]}
    prog, candidates = lec._resolve_program(domain, None, doc)
    assert prog == "main_x"
    assert candidates == []


def test_program_resolves_to_arg_when_no_role_main(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    prog, _ = lec._resolve_program(domain, "from_arg", None)
    assert prog == "from_arg"


def test_program_auto_detect_single_civil(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(domain / "specs" / "only_one.civil.yaml", {})
    prog, candidates = lec._resolve_program(domain, None, None)
    assert prog == "only_one"
    assert candidates == []


def test_program_auto_detect_none(tmp_path: Path):
    domain = tmp_path / "dom"
    (domain / "specs").mkdir(parents=True)
    prog, candidates = lec._resolve_program(domain, None, None)
    assert prog is None
    assert candidates == []


def test_program_auto_detect_ambiguous(tmp_path: Path):
    domain = tmp_path / "dom"
    _write_yaml(domain / "specs" / "a.civil.yaml", {})
    _write_yaml(domain / "specs" / "b.civil.yaml", {})
    prog, candidates = lec._resolve_program(domain, None, None)
    assert prog is None
    assert candidates == ["a", "b"]


# ---------------------------------------------------------------------------
# End-to-end run() tests
# ---------------------------------------------------------------------------

def test_run_happy_path_emits_json(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)

    rc = lec.run(domain, "prog", "extract")
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["domain"] == "dom"
    assert payload["program"] == "prog"
    assert payload["mode"] == "extract"
    # Five in-memory structures are always present (possibly empty):
    assert payload["confirmed_exprs"] == {}
    assert payload["example_rules"] == []
    assert payload["guidance_output_set"] == []
    assert payload["constants_tables_seed"] == []
    assert payload["per_module_sample_rules"] == {}
    assert payload["input_index_shas"] == {}
    assert "guidance_shas" in payload
    assert payload["work_list"] == [{
        "name": "prog",
        "role": "main",
        "action": "generate",
        "civil_file": "specs/prog.civil.yaml",
    }]


def test_run_preflight_missing_domain(tmp_path: Path, capsys):
    rc = lec.run(tmp_path / "nope", "prog", "extract")
    assert rc == 2
    err = capsys.readouterr().err
    assert "Domain directory not found" in err


def test_run_preflight_missing_input_index(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _minimal_domain(domain)
    # Remove the input-index that _minimal_domain wrote.
    (domain / "policy_facets" / "input-index.yaml").unlink()

    rc = lec.run(domain, "prog", "extract")
    assert rc == 2
    err = capsys.readouterr().err
    assert "/index-inputs" in err


@pytest.mark.parametrize(
    "rel_to_remove",
    [
        "specs/guidance/metadata.yaml",
        "specs/guidance/prompt-context.yaml",
        "specs/naming-manifest.yaml",
        "specs/guidance/output-variables.yaml",
    ],
)
def test_run_preflight_each_required_file(tmp_path: Path, capsys, rel_to_remove):
    domain = tmp_path / "dom"
    _minimal_domain(domain)
    (domain / rel_to_remove).unlink()

    rc = lec.run(domain, "prog", "extract")
    assert rc == 2
    err = capsys.readouterr().err
    assert rel_to_remove in err or "required file missing" in err


def test_run_drift_exits_one(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)
    # Add a source doc with stale SHA in the index.
    _write_text(domain / "input" / "policy_docs" / "foo.md", "new content\n")
    _write_yaml(
        domain / "policy_facets" / "input-index.yaml",
        {"files": {"input/policy_docs/foo.md": {"sha": "0" * 40,
                                                 "md_quality": {"score": 100}}}},
    )

    rc = lec.run(domain, "prog", "extract")
    assert rc == 1
    err = capsys.readouterr().err
    assert "Working-tree drift" in err
    assert "input/policy_docs/foo.md" in err


def test_run_missing_source_in_index_exits_one(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)
    # Index references a file that doesn't exist on disk.
    _write_yaml(
        domain / "policy_facets" / "input-index.yaml",
        {"files": {"input/policy_docs/ghost.md": {"sha": "abc",
                                                   "md_quality": {"score": 100}}}},
    )

    rc = lec.run(domain, "prog", "extract")
    assert rc == 1
    err = capsys.readouterr().err
    assert "source missing" in err
    assert "ghost.md" in err


def test_run_payload_pulls_full_guidance_docs(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)
    # Populate skeleton + sample-artifacts + include-with-output + constants
    # + ruleset-modules and verify each lands in the payload.
    _write_yaml(
        domain / "specs" / "guidance" / "skeleton.yaml",
        {
            "skeleton": {
                "computations": [
                    {"stage": "s1", "exprs": {"x": "a + b"}},
                ]
            }
        },
    )
    _write_yaml(
        domain / "specs" / "guidance" / "sample-artifacts.yaml",
        {"sample_rules": [{"id": "r1", "rule_type": "computed",
                            "source": "...", "civil": "yaml"}]},
    )
    (domain / "specs" / "guidance" / "include-with-output.yaml").write_text(
        yaml.safe_dump(["x", "y"], default_flow_style=False), encoding="utf-8"
    )
    _write_yaml(
        domain / "specs" / "guidance" / "constants-and-tables.yaml",
        {
            "constants_and_tables": [
                {"name": "t", "description": "d",
                 "source_file": "input/policy_docs/x.md",
                 "source_section": "§"},
            ]
        },
    )
    _write_yaml(
        domain / "specs" / "guidance" / "ruleset-modules.yaml",
        {
            "ruleset_modules": [
                {"name": "sub_a", "sample_rules": [{"id": "sr1"}]},
                {"name": "main_x", "role": "main"},
            ]
        },
    )

    rc = lec.run(domain, None, "extract")
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["confirmed_exprs"] == {"x": "a + b"}
    assert payload["example_rules"][0]["id"] == "r1"
    assert payload["guidance_output_set"] == ["x", "y"]
    assert payload["constants_tables_seed"][0]["name"] == "t"
    assert payload["per_module_sample_rules"]["sub_a"][0]["id"] == "sr1"
    # Program resolves to role: main even with no CLI arg.
    assert payload["program"] == "main_x"
    # Work-list orders sub before main.
    assert [e["name"] for e in payload["work_list"]] == ["sub_a", "main_x"]


def test_run_existing_extraction_manifest_included(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)
    _write_yaml(
        domain / "specs" / "extraction-manifest.yaml",
        {"programs": {"prog": {"civil_file": "x"}}},
    )
    rc = lec.run(domain, "prog", "extract")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["existing_extraction_manifest"] == {
        "programs": {"prog": {"civil_file": "x"}}
    }


def test_run_existing_extraction_manifest_null_when_absent(tmp_path: Path, capsys):
    domain = tmp_path / "dom"
    _git_init(tmp_path)
    _minimal_domain(domain)
    rc = lec.run(domain, "prog", "extract")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["existing_extraction_manifest"] is None


# ---------------------------------------------------------------------------
# Subprocess integration: full main() + argparse
# ---------------------------------------------------------------------------

def test_subprocess_happy_path():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _git_init(tmp_path)
        domain = tmp_path / "dom"
        _minimal_domain(domain)

        script = Path(__file__).parent / "load_extraction_context.py"
        env = os.environ.copy()
        env["DOMAINS_FULLPATH"] = str(tmp_path)
        proc = subprocess.run(
            ["uv", "run", str(script), "dom", "prog", "--mode", "extract"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["program"] == "prog"
        assert payload["mode"] == "extract"


def test_subprocess_missing_domains_env():
    script = Path(__file__).parent / "load_extraction_context.py"
    env = os.environ.copy()
    env.pop("DOMAINS_FULLPATH", None)
    proc = subprocess.run(
        ["uv", "run", str(script), "anything"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "DOMAINS_FULLPATH" in proc.stderr
