---
name: create-tests
description: Draft or Update Test Cases
---

# Draft or Update Test Cases

Create or update the test suite for a Catala module based on its current specs and the type-extended naming manifest.

## Input

```
/create-tests [<domain>]                  # auto-detect program or prompt if ambiguous
/create-tests [<domain> <program>]        # target a specific <program>.catala_en
```

**Resolving `<domain>` and `<program>`** (when args are missing or ambiguous, render the prompt inside a `:::user_input` fence per `xl-plugin/CLAUDE.md` multi-choice convention):

- **`<domain>` missing** — list every directory under `$DOMAINS_DIR/` that contains at least one `specs/*.catala_en`. Label each option `[a]`, `[b]`, `[c]`, ... in lowercase, one per line, in alphabetical order. End the list with `(or type in a different response)`.
- **`<program>` missing or ambiguous** (domain resolved, more than one `specs/*.catala_en` under it) — list each `<program>.catala_en` stem. Label `[a]`, `[b]`, `[c]`, ... in lowercase, in alphabetical order. Append `[<next-letter>] all` as the final labelled option to run the skill against every program. End the list with `(or type in a different response)`.
- **Single program found** — auto-detect and proceed without prompting.

Example program-ambiguity prompt for a domain with three programs:

```
:::user_input
Which program to create tests for?
[a] deductions
[b] income_tests
[c] passes_income
[d] all
(or type in a different response)
:::
```

On `all`, run the skill end-to-end once per program in the order shown (alphabetical).

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain `<domain>` not found. Run `/extract-ruleset <domain>` first.
   :::
   Stop.
2. **Catala source exists?**
   - `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` missing → Print:
     :::error
     No Catala source found. Run `/extract-ruleset <domain>` first.
     :::
     Stop.
3. **Naming manifest exists?**
   - `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` missing → Print:
     :::error
     specs/naming-manifest.yaml not found. Run `/extract-ruleset <domain>` first.
     :::
     Stop.
4. **`specs/tests/` directory exists?** — NO → create `$DOMAINS_DIR/<domain>/specs/tests/` silently.

## Step 0: Extract Policy Examples

After pre-flight, before mode detection, check for input documents and `extracted-tests.yaml`.

```bash
ls $DOMAINS_DIR/<domain>/policy_facets/compressed/**/* 2>/dev/null   # any input docs?
ls $DOMAINS_DIR/<domain>/policy_facets/extracted-tests.yaml 2>/dev/null
```

**If `extracted-tests.yaml` already exists:**
:::user_input
Found `extracted-tests.yaml` with N cases (last extracted from M documents). Use existing extracted tests, re-extract from input docs, or skip?
Options: `[u]se` / `[r]e-extract` / `[s]kip`
:::

- `[u]se`: proceed to Mode Detection using the existing file
- `[r]e-extract`: run `/extract-test-cases <domain> <program>`, then proceed to Mode Detection
- `[s]kip`: proceed to Mode Detection without extracted tests

**If `extracted-tests.yaml` does not exist but `$DOMAINS_DIR/<domain>/policy_facets/compressed/` contains documents:**
:::user_input
Found M policy documents in `$DOMAINS_DIR/<domain>/policy_facets/compressed/`. Extract concrete examples from them to seed tests? (recommended)
Options: `[y/n]`
:::

- `y`: run `/extract-test-cases <domain> <program>`, then proceed to Mode Detection
- `n`: proceed to Mode Detection without extracted tests

**If `$DOMAINS_DIR/<domain>/policy_facets/compressed/` is empty or absent:** proceed to Mode Detection without extracted tests.

## Mode Detection

```bash
ls $DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml 2>/dev/null
```

| Result | Mode |
|--------|------|
| File absent | **CREATE mode** — write new test suite from Catala source + naming manifest |
| File present | **UPDATE mode** — update stale test cases |

---

## Process — CREATE Mode

Read `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` AND `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` to understand:
- All input fields — names come from the manifest's `inputs.<Entity>.<field>` entries; per-field type / optionality / enum variants come from each entry's `type:`, `optional:`, and `enum_variants:` fields (U7 type extension). The Catala source provides the scope-declaration structure and entity grouping.
- All decisions — `outputs:` entries in the manifest (e.g., `eligible`, `reasons`, money/int/string decision fields) with their `type:` and `enum_variants:` driving expected-value shapes.
- All deny rules and their conditions — found in the Catala source.
- All computed fields involved in eligibility thresholds — `computed:` entries in the manifest provide names and types; the Catala source provides definitions.
- All tables and constants referenced in rules — read from the Catala source.

**Naming manifest is the authority for types.** When you need to know whether a field is `money`, `boolean`, an `Optional<T>`, or an enum (with what variants), consult `naming-manifest.yaml` — not the Catala source. Manifest entries without `type:` default to `string` and surface a `WARN` from downstream tools; flag any missing types in your summary.

**If extracted tests are available** (from Step 0), copy all of them into the test suite — preserve their `ext_*` `case_id`s and `source:` fields so provenance is visible in the main test file. Rename variables to match those in the manifest. Note which of the 6 coverage tags they already satisfy.

**If `$DOMAINS_DIR/<domain>/specs/guidance/sample-tests.yaml` exists and has a non-empty `sample_tests:` key**, load those cases and include them after any extracted tests:

1. For each entry in `sample_tests:`, validate every key in `inputs` against the input fields declared in the naming manifest. Collect unrecognised keys.
2. Copy the entry into the test suite. If unrecognised input keys exist, append a `notes:` field to the entry: `"Unrecognised inputs: <keys> — verify against naming-manifest input field names"`. All other fields (`case_id`, `short_description`, `description`, `tags`) are preserved; if the sample entry has no `short_description`, synthesize a unique one from its intent.
3. Accumulate the `tags` from all sample test entries and include them in the 6-tag coverage tally.

:::important
Seeded N sample test(s) from guidance/sample-tests.yaml (M field name warning(s)).
:::
— or skip silently if `guidance/sample-tests.yaml` is absent or `sample_tests` is empty/missing.

Draft additional synthetic cases from Catala-source reasoning (rules + denial conditions + threshold values) to reach the 6-tag coverage minimum for any tags **not** already covered by extracted cases or sample tests:

| Tag | What to cover |
|-----|---------------|
| `allow` | All conditions comfortably met (happy path) |
| `deny` + primary threshold | Fails primary threshold test (if one exists) |
| `deny` + adjusted threshold | Passes primary threshold, fails adjusted threshold (if one exists) |
| `allow` + exemption | Exemption or special path active (if one exists) |
| `allow` + boundary | Value exactly at a threshold (≤ limit = pass) |
| `deny` + edge | Extreme values: maximum count, all-zero numeric inputs, or other extreme |

**Every test case requires a `short_description`** — a concise, human-readable gist of the longer `description` that can stand in for the `case_id` (e.g. `Deny — gross income test failed`, `Approve — income eligible`, `Elderly / disabled exemption`). It must be **unique within the program's test set** (this baseline plus any expanded files). For extracted (`ext_*`) and sample cases copied in from `extracted-tests.yaml` / `guidance/sample-tests.yaml`, preserve an existing `short_description` if the source has one; otherwise synthesize one at copy-in time from the case's intent.

**Test format** (inputs always flat key-value, never nested by entity name):

```yaml
test_suite:
  spec: "<program>.catala_en"
  description: "..."
  version: "1.0"

tests:
  # Extracted cases (if any) come first, preserving ext_* IDs and source: fields
  - case_id: "ext_001"
    short_description: "Approve — within 10% compatibility band"
    description: "..."
    source:
      file: "$DOMAINS_DIR/<domain>/policy_facets/compressed/..."
      section: "Example 1"
    inputs:
      household_size: 3
      gross_monthly_income: 1800
    expected:
      eligible: true
      reasons: []
    tags: ["extracted", "allow"]

  # Sample tests from guidance/sample-tests.yaml (if any) come next, preserving their case_ids
  - case_id: "allow_001"
    short_description: "Approve — income eligible"
    description: "..."
    inputs:
      household_size: 2
      gross_monthly_income: 1200
    expected:
      eligible: true
    tags: ["allow"]

  # Synthetic cases follow
  - case_id: "deny_gross_001"
    short_description: "Deny — gross income test failed"
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
    short_description: "Compute — adjusted income after exclusions"
    description: "..."
    inputs:
      # ... flat key-value
    expected:
      adjusted_income: 1234.56  # float tolerance ±0.005 applied automatically
```

**Reference:** See `../../core/tests/eligibility_tests.yaml` for a complete working example.

Write to `$DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml`.

Then emit the Catala test fixture peer for the YAML:

> **Run `/catala-emit-tests <domain> <program>`.** Skip pre-flight — already verified above. The sub-skill reads the Catala source's scope-input declarations directly to infer the struct-literal shape, then emits `specs/tests/<program>_tests.catala_en` and self-checks via `clerk typecheck`. If the sub-skill returns `:::user_input` (unresolved clerk-loop), relay the user's response back to the sub-skill before continuing.

After the sub-skill returns successfully, validate the written test cases deterministically:

```bash
xlator validate-tests <domain> <program>
```

This enforces that every case has a `case_id`, `short_description`, and `description`, and that `short_description` is unique across the program's test set. If it exits non-zero, emit `:::error` with the captured output — when the failure is a cross-file duplicate, the message names both offending file/case_id pairs (the colliding label may live in an expanded file this run did not write) — fix the duplicate or missing field and re-run, and **do not** proceed to `record-tier-manifest`.

Then record the tests-tier manifest so `/check-freshness` can later detect drift between `specs/*.catala_en` and this skill's outputs (the manifest now captures both the YAML and its `.catala_en` peer in a single write):

```bash
xlator record-tier-manifest <domain> --tier tests
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop.

---

## Process — UPDATE Mode

### Step 1: Load Stale-Case Hints

Check for `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml`:

- **If present** (written by `/extract-ruleset` in this session): load the stale case list from it. These are cases whose `inputs` contain values that matched old table boundaries or constants now changed.
- **If absent** (standalone run after a manual Catala edit): run `xlator detect-stale-cases <domain> <program>` and parse the JSON header (everything before the `--- DETECT-STALE-CASES-HEADER-END ---` sentinel). Treat each entry in `stale_cases:` as a stale case; the `diff:` field names the divergent outputs. The Catala evaluator (U3) catches value-boundary changes AND logic-only changes (operator shifts, new exception clauses, restructured precedence) — no manual review caveat needed.

  If the tool exits non-zero, surface the stderr in a `:::error` block and stop.

### Step 2: Update Stale Cases

For each stale case:
- Update threshold values in `inputs` and `expected` to match the current Catala source's tables and constants
- Preserve all other fields unchanged (`case_id`, `short_description`, `description`, `tags`)

If no stale cases were identified:
:::important
No stale cases detected. Review manually for logic-only rule changes.
:::
Proceed to Step 3.

### Step 3: Add New Coverage

Read the current Catala source. For any deny rules, computed fields, or exemption paths not covered by an existing test case, add new cases to fill coverage gaps. Aim to maintain the 6-tag coverage from CREATE mode.

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

Load `sample_tests` from `$DOMAINS_DIR/<domain>/specs/guidance/sample-tests.yaml` (same existence check as CREATE mode). For each entry, compare by `case_id` against the current test suite:

- If the `case_id` is already present: skip (do not overwrite).
- If the `case_id` is absent: validate input fields against the naming manifest and append the entry, adding a `notes:` field if unrecognised input keys are found.

Print (or skip silently if `guidance/sample-tests.yaml` is absent or `sample_tests` is empty/missing):
- If N > 0:
  :::important
  Added N sample test(s) from guidance/sample-tests.yaml not previously in the test suite.
  :::
- If N = 0:
  :::important
  All sample tests already present.
  :::

### Step 5: Write Updated Test File

Overwrite `$DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml` with the updated suite.

### Step 6: Emit Catala Test Fixtures

Run `/catala-emit-tests <domain> <program>`. Skip pre-flight — already verified above. The sub-skill refreshes `specs/tests/<program>_tests.catala_en` and any other expanded test peers from the updated YAML, and self-checks via `clerk typecheck`. If the sub-skill returns `:::user_input` (unresolved clerk-loop), relay the user's response back to the sub-skill before continuing.

### Step 7: Clean Up Sidecar

Delete `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` if it exists (prevents stale hints on the next standalone run).

### Step 7b: Validate Test Cases

Validate the updated suite deterministically before recording the manifest:

```bash
xlator validate-tests <domain> <program>
```

This enforces required `case_id` / `short_description` / `description` and program-wide `short_description` uniqueness. If it exits non-zero, emit `:::error` with the captured output (cross-file duplicates name both offending file/case_id pairs — the collision may live in an expanded file this run did not edit), fix it, and re-run before proceeding. Do **not** continue to Step 8 while validation fails.

### Step 8: Record Tests-Tier Manifest

Record the tests-tier manifest so `/check-freshness` can later detect drift between `specs/*.catala_en` and the updated test suite:

```bash
xlator record-tier-manifest <domain> --tier tests
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop.

---

## Common Mistakes to Avoid

- **Don't nest inputs by entity name** — inputs are always flat key-value
- **Don't change `case_id` values** when updating stale cases — preserve existing IDs
- **Don't delete cases that aren't stale** — only update or add; removal is a human decision
- **Omit optional input fields** that aren't relevant to a test case — only include inputs the test actually depends on
- **Don't skip the `/catala-emit-tests` step** — the YAML and Catala companion must stay in sync, and the post-v14.0.0 pipeline expects the `.catala_en` peer to exist under `specs/tests/`
- **Don't reuse a `short_description` across cases** — it must be unique within the program's test set; `xlator validate-tests` will hard-fail on duplicates
- **Don't omit `short_description`** — it is required on every case, including extracted and sample cases (synthesize one if the source lacks it)
