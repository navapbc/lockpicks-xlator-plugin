"""Tests for export_test_results metadata parsing (plan 2026-06-11-001, U2/U3).

Parse-level only — exercises find_tests (description heading) and find_metadata
(case_id / short_description / tags from the bounded per-block comment scan)
without invoking the Catala toolchain.
"""

from __future__ import annotations

from pathlib import Path

import export_test_results as etr

# Two-case fixture in the shape /catala-emit-tests emits: `## Test:` heading
# outside the fence; `# <label>:` comments inside the fence above #[test].
# Block 2 deliberately omits `# tags:` to exercise the no-leakage guard.
_FIXTURE = """\
> Using Elig

## Test: Deny when gross income exceeds the limit

```catala
# case_id: deny_gross_001
# short_description: Deny — gross income test failed
# tags: deny, gross_test
#[test]
declaration scope TestDenyGross001:
  result scope Elig.EligibilityDecision

scope TestDenyGross001:
  definition result.gross_monthly_income equals $3,500
  assertion (result.eligible = false)
```

## Test: Approve when income eligible

```catala
# case_id: allow_001
# short_description: Approve — income eligible
#[test]
declaration scope TestAllow001:
  result scope Elig.EligibilityDecision

scope TestAllow001:
  definition result.gross_monthly_income equals $1,200
  assertion (result.eligible = true)
```
"""


def _fixture(tmp_path: Path, text: str = _FIXTURE) -> Path:
    p = tmp_path / "elig_tests.catala_en"
    p.write_text(text, encoding="utf-8")
    return p


# --- find_metadata --------------------------------------------------------

def test_metadata_extracted_per_block(tmp_path):
    meta = etr.find_metadata(_fixture(tmp_path))
    assert meta["TestDenyGross001"]["case_id"] == "deny_gross_001"
    assert meta["TestDenyGross001"]["short_description"] == "Deny — gross income test failed"
    assert meta["TestDenyGross001"]["tags"] == "deny, gross_test"


def test_omitted_tags_does_not_leak_from_prior_block(tmp_path):
    """Block 2 omits `# tags:` — its tags must be blank, NOT back-filled from
    block 1 (the bounded-scan regression guard)."""
    meta = etr.find_metadata(_fixture(tmp_path))
    assert meta["TestAllow001"]["tags"] == ""
    assert meta["TestAllow001"]["case_id"] == "allow_001"


def test_tags_normalized_comma_joined(tmp_path):
    text = _FIXTURE.replace("# tags: deny, gross_test", "# tags: deny ,  gross_test , edge")
    meta = etr.find_metadata(_fixture(tmp_path, text))
    assert meta["TestDenyGross001"]["tags"] == "deny, gross_test, edge"


def test_label_value_with_colon_preserved(tmp_path):
    text = _FIXTURE.replace(
        "# short_description: Deny — gross income test failed",
        "# short_description: Deny: gross income — limit exceeded",
    )
    meta = etr.find_metadata(_fixture(tmp_path, text))
    # Split on the first ':' only — the colon in the value survives.
    assert meta["TestDenyGross001"]["short_description"] == "Deny: gross income — limit exceeded"


def test_decoy_comment_in_prior_block_body_does_not_leak(tmp_path):
    """A `# case_id:`-looking comment inside block 1's scope body must NOT be
    picked up as block 2's metadata — the scan stops at block 2's fence opener."""
    text = """\
## Test: First

```catala
# case_id: real_001
#[test]
declaration scope TestOne:
  result scope Elig.EligibilityDecision

scope TestOne:
  # case_id: decoy_should_not_leak
  assertion (result.eligible = true)
```

## Test: Second

```catala
#[test]
declaration scope TestTwo:
  result scope Elig.EligibilityDecision

scope TestTwo:
  assertion (result.eligible = false)
```
"""
    meta = etr.find_metadata(_fixture(tmp_path, text))
    assert meta["TestOne"]["case_id"] == "real_001"
    # block 2 has no label of its own; the decoy in block 1's body must not leak.
    assert meta["TestTwo"]["case_id"] == ""


def test_single_tag_normalizes(tmp_path):
    text = _FIXTURE.replace("# tags: deny, gross_test", "# tags: deny")
    meta = etr.find_metadata(_fixture(tmp_path, text))
    assert meta["TestDenyGross001"]["tags"] == "deny"


def test_case_id_value_with_colon_preserved(tmp_path):
    text = _FIXTURE.replace("# case_id: deny_gross_001", "# case_id: ns:deny:001")
    meta = etr.find_metadata(_fixture(tmp_path, text))
    assert meta["TestDenyGross001"]["case_id"] == "ns:deny:001"


def test_no_test_blocks_returns_empty(tmp_path):
    meta = etr.find_metadata(_fixture(tmp_path, "> Using Elig\n\nNo tests here.\n"))
    assert meta == {}


def test_input_fieldnames_metadata_columns_first():
    fields = etr.input_fieldnames(["household_size", "gross_monthly_income"])
    assert fields[:5] == ["test_name", "case_id", "short_description", "description", "tags"]
    assert fields[5:] == ["household_size", "gross_monthly_income"]


def test_missing_metadata_is_blank_not_error(tmp_path):
    text = """\
## Test: A heading but no labels

```catala
#[test]
declaration scope TestBare:
  result scope Elig.EligibilityDecision

scope TestBare:
  assertion (result.eligible = true)
```
"""
    meta = etr.find_metadata(_fixture(tmp_path, text))
    assert meta["TestBare"] == {"case_id": "", "short_description": "", "tags": ""}


# --- find_tests (description heading) -------------------------------------

def test_description_heading_associated_with_block(tmp_path):
    tests = etr.find_tests(_fixture(tmp_path))
    by_scope = {scope: desc for scope, _, desc in tests}
    assert by_scope["TestDenyGross001"] == "Deny when gross income exceeds the limit"
    assert by_scope["TestAllow001"] == "Approve when income eligible"


# --- round-trip (U3) ------------------------------------------------------

def test_round_trip_all_four_fields_both_cases(tmp_path):
    """YAML metadata → emitted .catala_en → parsed back: all four fields land on
    the right case, with no cross-block contamination."""
    fix = _fixture(tmp_path)
    tests = etr.find_tests(fix)
    meta = etr.find_metadata(fix)
    desc_by_scope = {scope: desc for scope, _, desc in tests}

    combined = {
        scope: {
            "case_id": meta[scope]["case_id"],
            "short_description": meta[scope]["short_description"],
            "description": desc_by_scope[scope],
            "tags": meta[scope]["tags"],
        }
        for scope in desc_by_scope
    }

    assert combined["TestDenyGross001"] == {
        "case_id": "deny_gross_001",
        "short_description": "Deny — gross income test failed",
        "description": "Deny when gross income exceeds the limit",
        "tags": "deny, gross_test",
    }
    assert combined["TestAllow001"] == {
        "case_id": "allow_001",
        "short_description": "Approve — income eligible",
        "description": "Approve when income eligible",
        "tags": "",
    }
