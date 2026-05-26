# /// script
# requires-python = ">=3.14"
# ///
"""Unit tests for catala_pipeline_checks attribution functions (ticket 22).

Tests attribute_errors() and format_attribution_summary(), which classify OCaml
build failures by source module so catala-pipeline can tell the user whether
their requested module compiled cleanly or was itself the source of the error.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from catala_pipeline_checks import attribute_errors, format_attribution_summary

# ---------------------------------------------------------------------------
# Sample ninja/catala OCaml stderr (realistic multi-block)
# ---------------------------------------------------------------------------

SAMPLE_NINJA_STDERR = """\
ninja: Entering directory `_build'
[1/4] ocamlfind ocamlopt ...
FAILED: _build/ocaml/Resource_reasonable_compatibility.ml
┌─[ERROR]─ 1/4 ─
│  Syntax error at "(": the 'sum' operator must be followed by the type to be summed
├─➤ resource_reasonable_compatibility.catala_en:55.8-55.9:
│ 55 │     sum(client_stated_resources_per_account)
└─ Resource_reasonable_compatibility
┌─[ERROR]─ 2/4 ─
│  Unknown built-in type
├─➤ resource_reasonable_compatibility.catala_en:55.12-55.45:
│ 55 │     sum(client_stated_resources_per_account)
└─ Resource_reasonable_compatibility
┌─[ERROR]─ 3/4 ─
│  Syntax error at "-01": unexpected token
├─➤ program_standards_lookup.catala_en:428.14-428.17:
│ 428 │   | 2024-01-01 → benefit_rate_2024
└─ Program_standards_lookup
┌─[ERROR]─ 4/4 ─
│  Syntax error at "-01": unexpected token
├─➤ program_standards_lookup.catala_en:433.14-433.17:
│ 433 │   | 2024-06-01 → benefit_rate_2024_h2
└─ Program_standards_lookup
ninja: build stopped: cannot make progress due to previous errors.
"""

SINGLE_BLOCK_STDERR = """\
┌─[ERROR]─ 1/1 ─
│  Syntax error
├─➤ my_module.catala_en:10.5-10.6:
│ 10 │     bad syntax here
└─ My_module
"""

BLOCK_WITHOUT_POINTER = """\
┌─[ERROR]─ 1/1 ─
│  Some general error with no source pointer
└─ Unknown
"""

TWO_BLOCKS_SAME_MODULE = """\
┌─[ERROR]─ 1/2 ─
│  First error
├─➤ alpha.catala_en:1.0-1.1:
│ 1 │ line one
└─ Alpha
┌─[ERROR]─ 2/2 ─
│  Second error
├─➤ alpha.catala_en:2.0-2.1:
│ 2 │ line two
└─ Alpha
"""


# ---------------------------------------------------------------------------
# Tests — attribute_errors()
# ---------------------------------------------------------------------------


def test_attribute_errors_empty_string():
    assert attribute_errors("") == {}


def test_attribute_errors_single_block():
    result = attribute_errors(SINGLE_BLOCK_STDERR)
    assert list(result.keys()) == ["my_module"]
    assert len(result["my_module"]) == 1
    assert "Syntax error" in result["my_module"][0]


def test_attribute_errors_two_blocks_same_module():
    result = attribute_errors(TWO_BLOCKS_SAME_MODULE)
    assert list(result.keys()) == ["alpha"]
    assert len(result["alpha"]) == 2


def test_attribute_errors_two_blocks_different_modules():
    result = attribute_errors(TWO_BLOCKS_SAME_MODULE.replace("alpha", "alpha", 1))
    # Use SAMPLE_NINJA_STDERR for two different modules
    result = attribute_errors(SAMPLE_NINJA_STDERR)
    assert "resource_reasonable_compatibility" in result
    assert "program_standards_lookup" in result


def test_attribute_errors_block_without_pointer_line_dropped():
    result = attribute_errors(BLOCK_WITHOUT_POINTER)
    assert result == {}


def test_attribute_errors_sample_stderr_correct_counts():
    result = attribute_errors(SAMPLE_NINJA_STDERR)
    assert set(result.keys()) == {"resource_reasonable_compatibility", "program_standards_lookup"}
    assert len(result["resource_reasonable_compatibility"]) == 2
    assert len(result["program_standards_lookup"]) == 2


def test_attribute_errors_block_content_includes_all_lines():
    result = attribute_errors(SINGLE_BLOCK_STDERR)
    block = result["my_module"][0]
    assert "┌─[ERROR]" in block
    assert "├─➤" in block
    assert "└─" in block


# ---------------------------------------------------------------------------
# Tests — format_attribution_summary()
# ---------------------------------------------------------------------------


def test_format_summary_empty_errors_returns_empty_string():
    result = format_attribution_summary("my_module", {}, [])
    assert result == ""


def test_format_summary_requested_module_clean_contains_fencing():
    errors = {"sibling": ["┌─[ERROR]─ 1/1 ─\n│  bad\n├─➤ sibling.catala_en:1\n└─ Sibling"]}
    result = format_attribution_summary("my_module", errors, [])
    assert ":::important" in result
    assert ":::" in result


def test_format_summary_requested_module_clean_says_compiled_cleanly():
    errors = {"sibling": ["┌─[ERROR]─ 1/1 ─\n│  bad\n├─➤ sibling.catala_en:1\n└─ Sibling"]}
    result = format_attribution_summary("my_module", errors, [])
    assert "compiled cleanly" in result
    assert "my_module" in result


def test_format_summary_requested_module_clean_names_sibling():
    errors = {"sibling_a": ["block1"], "sibling_b": ["block2"]}
    result = format_attribution_summary("requested", errors, [])
    assert "sibling_a" in result
    assert "sibling_b" in result


def test_format_summary_requested_module_broken_no_compiled_cleanly():
    errors = {
        "requested": ["┌─[ERROR]─ 1/1 ─\n│  bad\n├─➤ requested.catala_en:1\n└─ Requested"],
        "sibling": ["┌─[ERROR]─ 1/1 ─\n│  bad\n├─➤ sibling.catala_en:1\n└─ Sibling"],
    }
    result = format_attribution_summary("requested", errors, [])
    assert "compiled cleanly" not in result
    assert "requested" in result


def test_format_summary_requested_module_only_broken():
    errors = {
        "requested": ["┌─[ERROR]─ 1/1 ─\n│  bad\n├─➤ requested.catala_en:5\n└─ Requested"],
    }
    result = format_attribution_summary("requested", errors, [])
    assert "compiled cleanly" not in result
    assert "requested" in result


def test_format_summary_mixed_errors_requested_first():
    errors = {
        "requested": ["req_block"],
        "sibling": ["sib_block"],
    }
    result = format_attribution_summary("requested", errors, [])
    req_pos = result.find("requested")
    sib_pos = result.find("sibling")
    assert req_pos < sib_pos, "requested module errors should appear before sibling errors"


def test_format_summary_mixed_errors_has_also_failing_heading():
    errors = {
        "requested": ["req_block"],
        "sibling": ["sib_block"],
    }
    result = format_attribution_summary("requested", errors, [])
    assert "Also failing" in result


def test_format_summary_lists_output_artifacts_when_clean():
    errors = {"sibling": ["sib_block"]}
    artifacts = ["output/my_module.catala_en", "output/my_module_meta.py"]
    result = format_attribution_summary("my_module", errors, artifacts)
    assert "output/my_module.catala_en" in result
    assert "output/my_module_meta.py" in result


def test_format_summary_no_artifacts_section_when_module_broken():
    errors = {"my_module": ["block"], "sibling": ["sib_block"]}
    artifacts = ["output/my_module.catala_en"]
    result = format_attribution_summary("my_module", errors, artifacts)
    # When module is broken, we should NOT show "artifacts are valid"
    assert "artifacts are valid" not in result


def test_format_summary_error_counts_shown():
    errors = {
        "sibling": ["block1", "block2", "block3"],
    }
    result = format_attribution_summary("requested", errors, [])
    assert "3" in result
