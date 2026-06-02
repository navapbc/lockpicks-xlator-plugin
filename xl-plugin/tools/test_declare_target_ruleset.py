# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Tests for declare_target_ruleset.py — covers field-mapping rules, the
seeded-entry invariant, the constraints seed verbatim contract, and all
pre-flight paths."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(__file__))

import declare_target_ruleset as dtr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_suggestion(
    tmp: Path,
    domain: str,
    ruleset_name: str,
    payload: dict,
) -> Path:
    """Write a suggestion file at `<tmp>/<domain>/specs/suggested_targets/<name>.yaml`.
    Returns the domain directory."""
    domain_dir = tmp / domain
    suggestion_path = (
        domain_dir / "specs" / "suggested_targets" / f"{ruleset_name}.yaml"
    )
    _write_yaml(suggestion_path, payload)
    return domain_dir


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _run_tool(tmp_path: Path, domain: str, ruleset_name: str) -> tuple[int, str, str]:
    """Invoke the script as a subprocess (exercises main() + argparse +
    env-var pre-flight). Returns (returncode, stdout, stderr)."""
    script = Path(__file__).parent / "declare_target_ruleset.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = str(tmp_path)
    proc = subprocess.run(
        ["uv", "run", str(script), domain, ruleset_name],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _full_payload() -> dict:
    """Minimal end-to-end payload exercising every branch of the field map."""
    return {
        "ruleset_name": "sample_ruleset",
        "display_name": "Sample Ruleset",
        "description": "A sample ruleset for testing.",
        "role": "You are a test analyst.",
        "scope": "Convert test policy into test rules.",
        "inputs": {
            "Household": {
                "size": {"type": "integer", "description": "Household size."},
                "income": {"type": "money", "description": "Monthly income."},
            },
            "Applicant": {
                "age": {"type": "integer", "description": "Applicant age."},
                "is_disabled": {"type": "boolean", "description": "Disability."},
            },
        },
        "computed": {
            "net_income": {"type": "money", "description": "Net income."},
            "passes_gross_test": {"type": "boolean", "description": "Gross test pass."},
        },
        "outputs": {
            "eligible": {"type": "boolean", "description": "Final eligibility."},
            "denial_reason": {"type": "string", "description": "Reason."},
            "warning": {"type": "string", "description": "Optional warning."},
        },
        "standards": ["Use monthly amounts.", "Express money in dollars."],
        "guidance": ["Look for chained deductions.", "Check gross and net tests."],
    }


# ---------------------------------------------------------------------------
# Happy path — full payload
# ---------------------------------------------------------------------------

def test_happy_path_writes_three_files():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", _full_payload())
        rc = dtr.run(domain_dir, "sample_ruleset")
        assert rc == 0
        assert (domain_dir / "specs" / "naming-manifest.yaml").exists()
        assert (domain_dir / "specs" / "guidance" / "metadata.yaml").exists()
        assert (domain_dir / "specs" / "guidance" / "prompt-context.yaml").exists()


def test_happy_path_manifest_version():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", _full_payload())
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["version"] == "3.0"


def test_happy_path_inputs_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", _full_payload())
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["inputs"]["Household"]["size"] == {
            "type": "integer",
            "description": "Household size.",
        }
        assert manifest["inputs"]["Applicant"]["age"] == {
            "type": "integer",
            "description": "Applicant age.",
        }


# ---------------------------------------------------------------------------
# Outputs round-trip type and description
# ---------------------------------------------------------------------------

def test_outputs_preserve_type_and_description():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", _full_payload())
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["outputs"]["eligible"] == {
            "type": "boolean",
            "description": "Final eligibility.",
        }


# ---------------------------------------------------------------------------
# observations: list passthrough (v3.0 — replaces scalar-provenance tests)
# ---------------------------------------------------------------------------

def test_observations_passed_through_when_suggested():
    """When the suggestion file carries `observations:` lists, the tool
    passes them through verbatim to the manifest. Replaces the pre-3.0
    'provenance never appears on seeded entries' rule."""
    payload = _full_payload()
    payload["inputs"]["Household"]["size"]["observations"] = [
        {"policy_phrase": "household size", "source_doc": "input/policy_docs/a.md",
         "section": "# Household"},
    ]
    payload["computed"]["net_income"]["observations"] = [
        {"policy_phrase": "net monthly income", "source_doc": "input/policy_docs/a.md",
         "section": "## Net"},
    ]
    payload["outputs"]["eligible"]["observations"] = [
        {"policy_phrase": "is eligible", "source_doc": "input/policy_docs/a.md",
         "section": "# Decision"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", payload)
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["inputs"]["Household"]["size"]["observations"] == [
            {"policy_phrase": "household size", "source_doc": "input/policy_docs/a.md",
             "section": "# Household"},
        ]
        assert manifest["computed"]["net_income"]["observations"] == [
            {"policy_phrase": "net monthly income", "source_doc": "input/policy_docs/a.md",
             "section": "## Net"},
        ]
        assert manifest["outputs"]["eligible"]["observations"] == [
            {"policy_phrase": "is eligible", "source_doc": "input/policy_docs/a.md",
             "section": "# Decision"},
        ]


def test_seed_entries_passthrough_multi_observation():
    """A suggestion entry with multi-observation list preserves order
    and content verbatim."""
    payload = _full_payload()
    payload["inputs"]["Household"]["income"]["observations"] = [
        {"policy_phrase": "household income", "source_doc": "input/policy_docs/a.md",
         "section": "# Income"},
        {"policy_phrase": "monthly household income", "source_doc": "input/policy_docs/b.md",
         "section": "# Income Sources"},
        {"policy_phrase": "gross income", "source_doc": "input/policy_docs/c.md",
         "section": "## Gross"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", payload)
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        obs = manifest["inputs"]["Household"]["income"]["observations"]
        assert len(obs) == 3
        assert obs[0]["policy_phrase"] == "household income"
        assert obs[1]["policy_phrase"] == "monthly household income"
        assert obs[2]["policy_phrase"] == "gross income"


def test_seed_entries_drop_legacy_scalar_provenance():
    """Stale scalar policy_phrase / source_doc / section keys from pre-3.0
    suggestion files are silently dropped — matches the existing
    'every other key is dropped' rule."""
    payload = _full_payload()
    payload["inputs"]["Household"]["size"]["policy_phrase"] = "household size"
    payload["inputs"]["Household"]["size"]["source_doc"] = "policy.md"
    payload["inputs"]["Household"]["size"]["section"] = "1.2"
    payload["inputs"]["Household"]["size"]["synonyms"] = ["hh size"]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "sample_ruleset", payload)
        dtr.run(domain_dir, "sample_ruleset")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        entry = manifest["inputs"]["Household"]["size"]
        assert "policy_phrase" not in entry
        assert "source_doc" not in entry
        assert "section" not in entry
        assert "synonyms" not in entry
        # type/description retained as expected
        assert entry["type"] == "integer"
        assert entry["description"] == "Household size."


# ---------------------------------------------------------------------------
# type/description omission rules
# ---------------------------------------------------------------------------

def test_field_with_only_type():
    payload = {
        "display_name": "x", "description": "x", "role": "x", "scope": "x",
        "inputs": {"E": {"f": {"type": "integer"}}},
        "outputs": {"o": {"type": "boolean"}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["inputs"]["E"]["f"] == {"type": "integer"}
        assert "description" not in manifest["inputs"]["E"]["f"]


def test_field_with_only_description():
    payload = {
        "display_name": "x", "description": "x", "role": "x", "scope": "x",
        "inputs": {"E": {"f": {"description": "the f."}}},
        "outputs": {"o": {"description": "the o."}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["inputs"]["E"]["f"] == {"description": "the f."}
        assert "type" not in manifest["inputs"]["E"]["f"]


def test_field_with_neither_type_nor_description():
    """A bare field key in the suggestion produces a bare key in the
    manifest (entry is `{}`)."""
    payload = {
        "display_name": "x", "description": "x", "role": "x", "scope": "x",
        "inputs": {"E": {"f": {}}},
        "outputs": {"o": {}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["inputs"]["E"]["f"] == {}


def test_u7_optional_and_enum_variants_seeded_when_suggested():
    """U7: when a suggestion entry carries `optional:` and/or
    `enum_variants:`, declare-target-ruleset seeds them into the manifest
    (nullable initial values — analyst confirms in /extract-ruleset Step 7).
    """
    payload = {
        "display_name": "x", "description": "x", "role": "x", "scope": "x",
        "inputs": {
            "Applicant": {
                "veteran_flag": {
                    "type": "boolean",
                    "optional": True,
                },
            },
        },
        "outputs": {
            "status": {
                "type": "string",
                "enum_variants": ["Eligible", "Denied"],
            },
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        veteran = manifest["inputs"]["Applicant"]["veteran_flag"]
        assert veteran["type"] == "boolean"
        assert veteran["optional"] is True
        status = manifest["outputs"]["status"]
        assert status["type"] == "string"
        assert status["enum_variants"] == ["Eligible", "Denied"]


def test_seed_entries_omit_observations_when_suggestion_omits():
    """When the suggestion supplies no `observations:` key, the manifest entry
    has no `observations:` key either — matches the synthesized-output case
    where a variable has no source observation to record."""
    payload = {
        "display_name": "x", "description": "x", "role": "x", "scope": "x",
        "inputs": {"E": {"f": {"type": "money", "optional": False}}},
        "outputs": {"o": {"type": "boolean"}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        for entry in (manifest["inputs"]["E"]["f"], manifest["outputs"]["o"]):
            assert "observations" not in entry
            # Stale scalar provenance fields also absent
            for prov_field in ("policy_phrase", "source_doc", "section"):
                assert prov_field not in entry


# ---------------------------------------------------------------------------
# computed: block omission
# ---------------------------------------------------------------------------

def test_computed_block_omitted_when_absent():
    payload = _full_payload()
    del payload["computed"]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert "computed" not in manifest


def test_computed_block_omitted_when_empty():
    payload = _full_payload()
    payload["computed"] = {}
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert "computed" not in manifest


# ---------------------------------------------------------------------------
# constraints seed verbatim
# ---------------------------------------------------------------------------

def test_constraints_seed_is_six_entries():
    assert len(dtr._CONSTRAINTS_SEED) == 6


def test_constraints_seed_written_verbatim():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["constraints"] == list(dtr._CONSTRAINTS_SEED)
        # Character-for-character check on the first and last entries.
        assert pc["constraints"][0] == (
            "Do not interpret beyond the text; "
            "do not add requirements that aren't stated."
        )
        assert pc["constraints"][5] == (
            "Ensure no rule introduces concepts not present in the policy."
        )


# ---------------------------------------------------------------------------
# standards / guidance copied verbatim
# ---------------------------------------------------------------------------

def test_standards_and_guidance_verbatim():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["standards"] == ["Use monthly amounts.", "Express money in dollars."]
        assert pc["guidance"] == ["Look for chained deductions.", "Check gross and net tests."]


def test_empty_standards_writes_empty_list():
    payload = _full_payload()
    payload["standards"] = []
    payload["guidance"] = []
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["standards"] == []
        assert pc["guidance"] == []


def test_missing_standards_and_guidance_keys_default_empty():
    payload = _full_payload()
    del payload["standards"]
    del payload["guidance"]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["standards"] == []
        assert pc["guidance"] == []


# ---------------------------------------------------------------------------
# edge_cases always empty
# ---------------------------------------------------------------------------

def test_edge_cases_always_empty():
    payload = _full_payload()
    payload["edge_cases"] = ["should be ignored", "and this too"]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["edge_cases"] == []


# ---------------------------------------------------------------------------
# metadata.yaml content
# ---------------------------------------------------------------------------

def test_metadata_content():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        dtr.run(domain_dir, "r")
        md = _load_yaml(domain_dir / "specs" / "guidance" / "metadata.yaml")
        assert md["display_name"] == "Sample Ruleset"
        assert md["description"] == "A sample ruleset for testing."


# ---------------------------------------------------------------------------
# role / scope content
# ---------------------------------------------------------------------------

def test_role_and_scope_verbatim():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        dtr.run(domain_dir, "r")
        pc = _load_yaml(domain_dir / "specs" / "guidance" / "prompt-context.yaml")
        assert pc["role"] == "You are a test analyst."
        assert pc["scope"] == "Convert test policy into test rules."


# ---------------------------------------------------------------------------
# Pre-flight failures (subprocess: exercises main() + argparse + env)
# ---------------------------------------------------------------------------

def test_missing_domain_folder_exit_2():
    with tempfile.TemporaryDirectory() as tmp:
        rc, _, stderr = _run_tool(Path(tmp), "nonexistent_dom", "anything")
        assert rc == 2
        assert "Domain not found" in stderr


def test_missing_suggestion_file_exit_2():
    with tempfile.TemporaryDirectory() as tmp:
        # Build the domain dir but NOT the suggestion file.
        domain_dir = Path(tmp) / "test_dom"
        (domain_dir / "specs" / "suggested_targets").mkdir(parents=True)
        rc, _, stderr = _run_tool(Path(tmp), "test_dom", "nope")
        assert rc == 2
        assert "Ruleset file not found" in stderr


def test_missing_suggestion_file_lists_alternatives():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "test_dom", "exists_a", _full_payload())
        _write_yaml(
            domain_dir / "specs" / "suggested_targets" / "exists_b.yaml",
            _full_payload(),
        )
        rc, _, stderr = _run_tool(Path(tmp), "test_dom", "wrong_name")
        assert rc == 2
        assert "Ruleset file not found" in stderr
        # At least one alternative should be listed.
        assert "exists_a" in stderr or "exists_b" in stderr


def test_unset_domains_fullpath_exit_2():
    script = Path(__file__).parent / "declare_target_ruleset.py"
    env = {k: v for k, v in os.environ.items() if k != "DOMAINS_FULLPATH"}
    proc = subprocess.run(
        ["uv", "run", str(script), "d", "r"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "DOMAINS_FULLPATH" in proc.stderr


def test_argparse_missing_positional_exit_2():
    """Both <domain> and <ruleset_name> are required."""
    script = Path(__file__).parent / "declare_target_ruleset.py"
    env = os.environ.copy()
    env["DOMAINS_FULLPATH"] = "/tmp"
    proc = subprocess.run(
        ["uv", "run", str(script), "only_one_arg"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# Malformed YAML
# ---------------------------------------------------------------------------

def test_malformed_suggestion_yaml_exit_1():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = Path(tmp) / "test_dom"
        suggestion_path = (
            domain_dir / "specs" / "suggested_targets" / "broken.yaml"
        )
        suggestion_path.parent.mkdir(parents=True)
        suggestion_path.write_text("not: valid: yaml: : :\n  - [\n", encoding="utf-8")
        rc, _, stderr = _run_tool(Path(tmp), "test_dom", "broken")
        assert rc == 1
        assert "failed to parse" in stderr


# ---------------------------------------------------------------------------
# Overwrite behavior
# ---------------------------------------------------------------------------

def test_overwrite_pre_existing_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        # Pre-create the three output files with arbitrary content.
        (domain_dir / "specs" / "naming-manifest.yaml").write_text(
            "old: stale\n", encoding="utf-8"
        )
        (domain_dir / "specs" / "guidance").mkdir(parents=True, exist_ok=True)
        (domain_dir / "specs" / "guidance" / "metadata.yaml").write_text(
            "old: stale\n", encoding="utf-8"
        )
        (domain_dir / "specs" / "guidance" / "prompt-context.yaml").write_text(
            "old: stale\n", encoding="utf-8"
        )
        rc = dtr.run(domain_dir, "r")
        assert rc == 0
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        # The new content is present; old content is replaced.
        assert manifest["version"] == "3.0"
        assert "old" not in manifest
        md = _load_yaml(domain_dir / "specs" / "guidance" / "metadata.yaml")
        assert md["display_name"] == "Sample Ruleset"


# ---------------------------------------------------------------------------
# Atomicity — failure on a later write leaves prior writes intact
# ---------------------------------------------------------------------------

def test_atomicity_partial_write_does_not_corrupt_prior_files():
    """When the second write fails, the first file is still on disk; the
    third was never attempted. (Documents the partial-write risk —
    re-running the tool overwrites cleanly.)"""
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        real_replace = os.replace
        call_count = {"n": 0}

        def fake_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated failure on second write")
            return real_replace(src, dst)

        with mock.patch.object(dtr.os, "replace", side_effect=fake_replace):
            try:
                dtr.run(domain_dir, "r")
            except OSError:
                pass
        # First write (manifest) succeeded.
        assert (domain_dir / "specs" / "naming-manifest.yaml").exists()
        manifest = _load_yaml(domain_dir / "specs" / "naming-manifest.yaml")
        assert manifest["version"] == "3.0"
        # Third write was never attempted — file should not exist.
        assert not (domain_dir / "specs" / "guidance" / "prompt-context.yaml").exists()


# ---------------------------------------------------------------------------
# Stdout shape — binding contract from plan
# ---------------------------------------------------------------------------

def test_stdout_shape_matches_binding_contract():
    """5 summary lines, blank line, 3 Created lines."""
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        rc, stdout, _ = _run_tool(Path(tmp), "d", "r")
        assert rc == 0
        lines = stdout.rstrip("\n").split("\n")
        # 6 summary lines (Ruleset, Description, Inputs, Computed, Output,
        # Secondary outputs), 1 blank, 3 Created lines = 10 lines total.
        assert len(lines) == 10, f"got {len(lines)} lines:\n{stdout}"
        assert lines[0].startswith("Ruleset: ")
        assert lines[1].startswith("Description: ")
        assert lines[2].startswith("Inputs: ")
        assert lines[3].startswith("Computed: ")
        assert lines[4].startswith("Output: ")
        assert lines[5].startswith("Secondary outputs: ")
        assert lines[6] == ""
        for i in (7, 8, 9):
            assert re.match(r"^Created /.*\.yaml$", lines[i]), lines[i]


def test_stdout_summary_field_values():
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        rc, stdout, _ = _run_tool(Path(tmp), "d", "r")
        assert rc == 0
        assert "Ruleset: Sample Ruleset" in stdout
        assert "Inputs: Household, Applicant" in stdout
        assert "Computed: net_income, passes_gross_test" in stdout
        assert "Output: eligible (boolean)" in stdout
        assert "Secondary outputs: denial_reason, warning" in stdout


def test_stdout_computed_none_when_absent():
    payload = _full_payload()
    del payload["computed"]
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        rc, stdout, _ = _run_tool(Path(tmp), "d", "r")
        assert rc == 0
        assert "Computed: none" in stdout


def test_stdout_secondary_outputs_none_when_only_one_output():
    payload = _full_payload()
    payload["outputs"] = {
        "eligible": {"type": "boolean", "description": "x"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", payload)
        rc, stdout, _ = _run_tool(Path(tmp), "d", "r")
        assert rc == 0
        assert "Secondary outputs: none" in stdout


# ---------------------------------------------------------------------------
# Output order preservation
# ---------------------------------------------------------------------------

def test_outputs_preserve_declaration_order():
    """Manifest outputs must preserve declaration order from the suggestion
    (load-bearing for /extract-ruleset Step 7's enumeration order)."""
    with tempfile.TemporaryDirectory() as tmp:
        domain_dir = _build_suggestion(Path(tmp), "d", "r", _full_payload())
        dtr.run(domain_dir, "r")
        manifest_text = (domain_dir / "specs" / "naming-manifest.yaml").read_text()
        idx_e = manifest_text.find("eligible:")
        idx_d = manifest_text.find("denial_reason:")
        idx_w = manifest_text.find("warning:")
        assert 0 < idx_e < idx_d < idx_w


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
