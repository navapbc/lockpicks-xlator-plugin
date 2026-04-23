# CIVIL Test DSL — Authoring Quick Reference

<!-- Last verified against tools/rego-run_tests.py + tools/transpile_to_catala_tests.py: 2026-03-19 -->

This is a **Claude authoring cheat sheet** for writing valid CIVIL test YAML files.
Test files drive both the OPA runner (`tools/rego-run_tests.py`) and the Catala test transpiler
(`tools/transpile_to_catala_tests.py`).

---

## File Naming and Location

```
$DOMAINS_DIR/<domain>/specs/tests/<module>[_<qualifier>]_tests.yaml
```

Examples:
- `$DOMAINS_DIR/snap/specs/tests/eligibility_tests.yaml`
- `$DOMAINS_DIR/ak_doh/specs/tests/earned_income_exclusion_chain_tests.yaml`
- `$DOMAINS_DIR/ak_doh/specs/tests/eligibility_tests.yaml`

> ⚠️ **Files ending in `_null_input_expanded_tests.yaml` are skipped by the Catala transpiler.**
> Use this suffix for tests that rely on OPA's undefined-result behavior (not encodable in Catala).

---

## Top-Level Structure

```yaml
test_suite:
  spec: "eligibility.civil.yaml"
  description: "..."
  version: "1.0"

tests:
  - case_id: "..."
    ...
```

### `test_suite:` Header

| Field | Required | Notes |
|-------|----------|-------|
| `spec` | ✅ | Basename of the CIVIL spec being tested, e.g. `"eligibility.civil.yaml"` |
| `description` | ✅ | Human-readable suite description. Shown as the test run header by `rego-run_tests.py`. |
| `version` | ✅ | Schema version, e.g. `"1.0"` |

---

## Test Case Fields

| Field | Required | Notes |
|-------|----------|-------|
| `case_id` | ✅ | Unique slug in `snake_case`. Used as the Catala scope name (`allow_001` → `TestAllow001`). Must be unique within the file. |
| `description` | ✅ | One-line human description. Shown in test runner output. |
| `inputs` | ✅ | Input field values. See **Input Key Format** below. |
| `expected` | ✅ | Expected output values to assert. See **Expected Field Types** below. |
| `source` | — | Policy document provenance. See **`source:` block** below. |
| `tags` | — | List of string labels for categorization, e.g. `["allow", "size_3"]` |
| `notes` | — | Free-text annotation for reviewers (not used by tooling) |

---

## Input Key Format

### Single-entity modules

Inputs are keyed by bare field name:

```yaml
inputs:
  household_size: 3
  gross_monthly_income: 1800
  has_elderly_member: false
```

### Multi-entity modules (two or more `inputs:` entities)

Inputs are keyed as `EntityName.field_name` (dot notation):

```yaml
inputs:
  ClientIncome.gross_earned_income: 3500
  ClientIncome.remaining_general_exclusion: 20
  ClientIncome.benefit_year: 2025
  DOLIncome.gross_earned_income: 3733.33
  DOLIncome.dol_data_available: true
  DOLIncome.remaining_general_exclusion: 20
```

> ⚠️ **Entity prefix must exactly match the entity name in `inputs:`.** Mismatches silently omit the field.

### Sparse inputs

Fields not listed in `inputs:` are **defaulted** by the Catala transpiler based on their CIVIL type:

| CIVIL type | Default |
|-----------|---------|
| `money` | `$0` |
| `bool` | `false` |
| `int` | `0` |
| `float` | `0.0` |

Fields marked `optional: true` in the CIVIL spec are silently defaulted. Required fields that are missing
produce a `WARN` on stderr from the transpiler.

> ⚠️ **The OPA runner sends only the listed inputs to OPA.** Omitted fields are absent from `input`, not
> zero-valued. Rules that access missing fields return undefined, not 0/false.

---

## Expected Field Types

The `expected:` block maps output field names to expected values. Supported assertions:

### Boolean
```yaml
expected:
  eligible: true
  manual_verification_required: false
```

### Numeric (with tolerance)
```yaml
expected:
  client_adjusted_income: 1707.50
  income_standard: 1795
```
The OPA runner applies **±0.005 tolerance** for all numeric comparisons (half-cent for money fields).

### Reasons list — empty (eligible)
```yaml
expected:
  reasons: []
```

### Reasons list — one or more denials
```yaml
expected:
  reasons:
    - code: "GROSS_INCOME_EXCEEDS_LIMIT"
    - code: "NET_INCOME_EXCEEDS_LIMIT"
```
The OPA runner checks that each expected `code` is **present** in the returned reasons list.
Extra reasons in the OPA response do not cause failure.

> ⚠️ **Reason codes in `expected:` must be objects with a `code:` key**, not bare strings.

---

## `source:` Block (optional)

Records the policy document section from which the test case was extracted.

| Field | Required | Notes |
|-------|----------|-------|
| `file` | ✅ (if `source:` present) | Repo-relative path to the source policy doc |
| `section` | ✅ (if `source:` present) | Section heading or identifier within the doc |

```yaml
source:
  file: "$DOMAINS_DIR/ak_doh/input/policy_docs/adltc_manual/523 MEDICAID EXCEPTIONS TO APA INCOME POLICY.md"
  section: "523 B — Action 4 and Example"
```

---

## Canonical Templates

### Single-entity module — allow case
```yaml
- case_id: "allow_001"
  description: "Household of 3 with low income passes both tests"
  inputs:
    household_size: 3
    gross_monthly_income: 1800
    has_elderly_member: false
  expected:
    eligible: true
    reasons: []
  tags: ["allow", "happy_path", "size_3"]
```

### Single-entity module — deny case
```yaml
- case_id: "deny_gross_001"
  description: "Gross income above 130% FPL limit → deny"
  inputs:
    household_size: 3
    gross_monthly_income: 3200
    has_elderly_member: false
  expected:
    eligible: false
    reasons:
      - code: "GROSS_INCOME_EXCEEDS_LIMIT"
  tags: ["deny", "gross_test", "size_3"]
```

### Multi-entity module — extracted case with numeric assertion
```yaml
- case_id: "ext_003"
  description: "523 B Example — Action 4: client below standard, DOL above but compatible"
  source:
    file: "$DOMAINS_DIR/ak_doh/input/policy_docs/adltc_manual/523 MEDICAID EXCEPTIONS TO APA INCOME POLICY.md"
    section: "523 B — Action 4 and Example"
  inputs:
    ClientIncome.gross_earned_income: 3500
    ClientIncome.remaining_general_exclusion: 20
    ClientIncome.benefit_year: 2025
    ClientIncome.household_type: "A1E"
    DOLIncome.gross_earned_income: 3733.33
    DOLIncome.dol_data_available: true
    DOLIncome.remaining_general_exclusion: 20
    DOLIncome.benefit_year: 2025
  expected:
    eligible: true
    manual_verification_required: false
    client_adjusted_income: 1707.50
    dol_avg_monthly_adjusted: 1824.17
    income_standard: 1795
    reasons: []
  tags: ["extracted", "allow", "compatible_below_standard"]
  notes: "±0.005 tolerance applies to dol_avg_monthly_adjusted"
```

---

## Catala Transpiler Behavior

`transpile_to_catala_tests.py` reads a `*_tests.yaml` file and emits a `.catala_en` file with one
`#[test]` scope per test case.

| YAML element | Catala output |
|-------------|---------------|
| `case_id: "allow_001"` | `declaration scope TestAllow001:` |
| `--scope EligibilityDecision` CLI arg | `result scope Module.EligibilityDecision` |
| Single-entity inputs | `definition result.<field> equals <value>` (flat) |
| Multi-entity inputs (`Entity.field`) | `definition result.<entity_var> equals Module.Entity { -- field: value }` |
| `expected:` bool field | `assertion (result.<field> = true/false)` |
| `expected: reasons: []` | `assertion (result.reasons = [])` |
| `expected: reasons: [{code: FOO_BAR}]` | `assertion (result.reasons = [ FooBar ])` |
| `expected:` numeric field | **Not asserted** — numeric outputs are not yet emitted as Catala assertions |

### Entity variable name mapping (multi-entity)

Entity names are converted to Catala variable names via `PascalCase → snake_case`:

| CIVIL entity name | Catala variable |
|------------------|----------------|
| `ClientIncome` | `client_income` |
| `DOLIncome` | `d_o_l_income` |
| `Household` | `household` |

> ⚠️ **Abbreviations expand letter-by-letter** — `DOL` → `d_o_l`, not `dol`. This is a Catala naming
> constraint, not a bug in the transpiler.

### Reason code mapping

Denial reason codes are converted to Catala enum variant names:

| YAML reason code | Catala variant |
|-----------------|---------------|
| `GROSS_INCOME_EXCEEDS_LIMIT` | `GrossIncomeExceedsLimit` |
| `NET_INCOME_EXCEEDS_LIMIT` | `NetIncomeExceedsLimit` |

---

## Common Gotchas

1. **`case_id` must be unique** — duplicate `case_id` values that map to the same scope name (e.g. `allow_001` and `allow_001b` both → `TestAllow001`) cause the transpiler to abort.
2. **Multi-entity keys must use exact entity names** — `clientincome.gross` silently defaults instead of binding the field.
3. **Reason codes must be objects** — `reasons: ["GROSS_INCOME_EXCEEDS_LIMIT"]` is invalid; use `reasons: [{code: "GROSS_INCOME_EXCEEDS_LIMIT"}]`.
4. **Numeric assertions use ±0.005 tolerance in OPA only** — the Catala transpiler does not emit numeric assertions at all; verify computed outputs via the OPA runner.
5. **Omitted inputs behave differently per runner** — OPA runner: field absent from `input` (can trigger undefined). Catala transpiler: field zero-defaulted per type.
6. **`_null_input_expanded_tests.yaml` files are skipped by the Catala transpiler** — use this suffix for OPA-only test files.
