# Draft or Update Test Cases

Create or update the test suite for a CIVIL module based on its current specs.

## Input

```
/create-tests [<domain>]                  # auto-detect program or prompt if ambiguous
/create-tests [<domain> <program>]        # target a specific <program>.civil.yaml
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/*.civil.yaml` files and prompt the user to choose.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain `<domain>` not found. Run `/xl:extract-ruleset <domain>` first.
   :::
   Stop.
2. **CIVIL file exists?**
   - `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` missing → Print:
     :::error
     No CIVIL file found. Run `/xl:extract-ruleset <domain>` first.
     :::
     Stop.
3. **`specs/tests/` directory exists?** — NO → create `$DOMAINS_DIR/<domain>/specs/tests/` silently.

## Step 0: Extract Policy Examples

After pre-flight, before mode detection, check for input documents and `extracted-tests.yaml`.

```bash
ls $DOMAINS_DIR/<domain>/input/**/* 2>/dev/null   # any input docs?
ls $DOMAINS_DIR/<domain>/specs/extracted-tests.yaml 2>/dev/null
```

**If `extracted-tests.yaml` already exists:**
:::user_input
Found `extracted-tests.yaml` with N cases (last extracted from M documents). Use existing extracted tests, re-extract from input docs, or skip?
Options: `[u]se` / `[r]e-extract` / `[s]kip`
:::

- `[u]se`: proceed to Mode Detection using the existing file
- `[r]e-extract`: run `/xl:extract-test-cases <domain> <program>`, then proceed to Mode Detection
- `[s]kip`: proceed to Mode Detection without extracted tests

**If `extracted-tests.yaml` does not exist but `$DOMAINS_DIR/<domain>/input/` contains documents:**
:::user_input
Found M policy documents in `$DOMAINS_DIR/<domain>/input/`. Extract concrete examples from them to seed tests? (recommended)
Options: `y` / `n`
:::

- `y`: run `/xl:extract-test-cases <domain> <program>`, then proceed to Mode Detection
- `n`: proceed to Mode Detection without extracted tests

**If `input/` is empty or absent:** proceed to Mode Detection without extracted tests.

## Mode Detection

```bash
ls $DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml 2>/dev/null
```

| Result | Mode |
|--------|------|
| File absent | **CREATE mode** — write new test suite from CIVIL file |
| File present | **UPDATE mode** — update stale test cases |

---

## Process — CREATE Mode

Read `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` to understand:
- All input fields (types, optionality)
- All decisions (e.g., `eligible`, `reasons`, or any `expr:`-driven money/int/string decision fields)
- All deny rules and their conditions
- All computed fields involved in eligibility thresholds
- All tables and constants referenced in rules

**If extracted tests are available** (from Step 0), copy them into the test suite as-is — preserve their `ext_*` `case_id`s and `source:` fields so provenance is visible in the main test file. Note which of the 6 coverage tags they already satisfy.

**If `$DOMAINS_DIR/<domain>/specs/guidance.yaml` exists and has a non-empty `sample_tests:` key**, load those cases and include them after any extracted tests:

1. For each entry in `sample_tests:`, validate every key in `inputs` against the input fields declared in the CIVIL file. Collect unrecognised keys.
2. Copy the entry into the test suite. If unrecognised input keys exist, append a `notes:` field to the entry: `"Unrecognised inputs: <keys> — verify against CIVIL field names"`. All other fields (`case_id`, `description`, `inputs`, `expected`, `tags`) are preserved as-is.
3. Accumulate the `tags` from all sample test entries and include them in the 6-tag coverage tally.

:::important
Seeded N sample test(s) from guidance.yaml (M field name warning(s)).
:::
— or skip silently if `guidance.yaml` is absent or `sample_tests` is empty/missing.

Draft additional synthetic cases from CIVIL reasoning to reach the 6-tag coverage minimum for any tags **not** already covered by extracted cases or sample tests:

| Tag | What to cover |
|-----|---------------|
| `allow` | All conditions comfortably met (happy path) |
| `deny` + primary threshold | Fails primary threshold test (if one exists) |
| `deny` + adjusted threshold | Passes primary threshold, fails adjusted threshold (if one exists) |
| `allow` + exemption | Exemption or special path active (if one exists) |
| `allow` + boundary | Value exactly at a threshold (≤ limit = pass) |
| `deny` + edge | Extreme values: maximum count, all-zero numeric inputs, or other extreme |

**Test format** (inputs always flat key-value, never nested by entity name):

```yaml
test_suite:
  spec: "<program>.civil.yaml"
  description: "..."
  version: "1.0"

tests:
  # Extracted cases (if any) come first, preserving ext_* IDs and source: fields
  - case_id: "ext_001"
    description: "..."
    source:
      file: "$DOMAINS_DIR/<domain>/input/..."
      section: "Example 1"
    inputs:
      household_size: 3
      gross_monthly_income: 1800
    expected:
      eligible: true
      reasons: []
    tags: ["extracted", "allow"]

  # Sample tests from guidance.yaml (if any) come next, preserving their case_ids
  - case_id: "allow_001"
    description: "..."
    inputs:
      household_size: 2
      gross_monthly_income: 1200
    expected:
      eligible: true
    tags: ["allow"]

  # Synthetic cases follow
  - case_id: "deny_gross_001"
    description: "..."
    inputs:
      household_size: 3
      gross_monthly_income: 3500
      # ... flat key-value
    expected:
      eligible: false
      reasons:
        - code: "GROSS_INCOME_EXCEEDS_LIMIT"
    tags: ["deny", "gross_test"]

  # For modules with money/int/string expr:-driven decision fields:
  - case_id: "calc_001"
    description: "..."
    inputs:
      # ... flat key-value
    expected:
      adjusted_income: 1234.56  # float tolerance ±0.005 applied automatically
```

**Reference:** See `$CLAUDE_PLUGIN_ROOT/core/tests/eligibility_tests.yaml` for a complete working example.

Write to `$DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml`.

---

## Process — UPDATE Mode

### Step 1: Load Stale-Case Hints

Check for `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml`:

- **If present** (written by `/xl:extract-ruleset` in this session): load the stale case list from it. These are cases whose `inputs` contain values that matched old table boundaries or constants now changed.
- **If absent** (standalone run after a manual CIVIL edit): compare each test case's `inputs` values against all current `tables:` rows and `constants:` values in the CIVIL file. Flag any case where an input value exactly matches a value that no longer appears in any table row or constant.

  :::important
  No `.stale-cases.yaml` found — using table/constant comparison to detect stale cases. Logic-only rule changes (e.g., operator changes, new conditions) will not be detected; review manually.
  :::

### Step 2: Update Stale Cases

For each stale case:
- Update threshold values in `inputs` and `expected` to match the current CIVIL tables and constants
- Preserve all other fields unchanged (`case_id`, `description`, `tags`)

If no stale cases were identified:
:::important
No stale cases detected. Review manually for logic-only rule changes.
:::
Proceed to Step 3.

### Step 3: Add New Coverage

Read the current CIVIL file. For any deny rules, computed fields, or exemption paths not covered by an existing test case, add new cases to fill coverage gaps. Aim to maintain the 6-tag coverage from CREATE mode.

### Step 4: Reconcile Extracted Tests

If extracted tests are available from Step 0 (existing `extracted-tests.yaml` was used, or a re-extract was run):

- Compare each case in `extracted-tests.yaml` against the current suite, matching by `case_id` (all extracted cases have `ext_*` IDs)
- For any `ext_*` case not already present in `<program>_tests.yaml`, append it with its `source:` field intact
- Report:
  - If N > 0:
    :::important
    Added N extracted cases not previously in the test suite.
    :::
  - If N = 0:
    :::important
    All extracted cases already present.
    :::

Skip this step silently if no extracted tests are available.

### Step 4b: Reconcile Sample Tests

Load `sample_tests` from `$DOMAINS_DIR/<domain>/specs/guidance.yaml` (same existence check as CREATE mode). For each entry, compare by `case_id` against the current test suite:

- If the `case_id` is already present: skip (do not overwrite).
- If the `case_id` is absent: validate input fields against the CIVIL file and append the entry, adding a `notes:` field if unrecognised input keys are found.

Print (or skip silently if `guidance.yaml` is absent or `sample_tests` is empty/missing):
- If N > 0:
  :::important
  Added N sample test(s) from guidance.yaml not previously in the test suite.
  :::
- If N = 0:
  :::important
  All sample tests already present.
  :::

### Step 5: Write Updated Test File

Overwrite `$DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml` with the updated suite.

### Step 6: Clean Up Sidecar

Delete `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` if it exists (prevents stale hints on the next standalone run).

---

## Common Mistakes to Avoid

- **Don't nest inputs by entity name** — inputs are always flat key-value
- **Don't change `case_id` values** when updating stale cases — preserve existing IDs
- **Don't delete cases that aren't stale** — only update or add; removal is a human decision
- **Omit optional input fields** that aren't relevant to a test case — only include inputs the test actually depends on
