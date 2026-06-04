---
name: expand-tests
description: Expand Test Coverage
---

# Expand Test Coverage

Generate additional test cases for an existing Catala module test suite without duplicating existing cases.

## Input

```
/expand-tests [<domain>]                  # auto-detect program or prompt if ambiguous
/expand-tests [<domain> <program>]        # target a specific <program>.catala_en
```

**Resolving `<domain>` and `<program>`** (when args are missing or ambiguous, render the prompt inside a `:::user_input` fence per `xl-plugin/CLAUDE.md` multi-choice convention):

- **`<domain>` missing** — list every directory under `$DOMAINS_DIR/` that contains at least one `specs/*.catala_en`. Label each option `[a]`, `[b]`, `[c]`, ... in lowercase, one per line, in alphabetical order. End the list with `(or type in a different response)`.
- **`<program>` missing or ambiguous** (domain resolved, more than one `specs/*.catala_en` under it) — list each `<program>.catala_en` stem. Label `[a]`, `[b]`, `[c]`, ... in lowercase, in alphabetical order. Append `[<next-letter>] all` as the final labelled option to run the skill against every program. End the list with `(or type in a different response)`.
- **Single program found** — auto-detect and proceed without prompting.

Example program-ambiguity prompt for a domain with three programs:

```
:::user_input
Which program to expand tests for?
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

1. **Domain folder exists?** — NO →
   :::error
   Domain `<domain>` not found. Run `/extract-ruleset <domain>` first.
   :::
   Stop.
2. **Catala source exists?**
   - `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` missing →
     :::error
     No Catala source found. Run `/extract-ruleset <domain>` first.
     :::
     Stop.
3. **Naming manifest exists?**
   - `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` missing →
     :::error
     specs/naming-manifest.yaml not found. Run `/extract-ruleset <domain>` first.
     :::
     Stop.
4. **`specs/tests/` directory exists?** — NO → create `$DOMAINS_DIR/<domain>/specs/tests/` silently.
5. **Baseline tests exist?** — `$DOMAINS_DIR/<domain>/specs/tests/<program>_tests.yaml` missing → Print: "⚠️ No baseline test file found. Expanded tests will still be generated, but consider running `/create-tests <domain>` first to establish a baseline." Continue.
6. **Existing expanded files?** — If any of the four output files already exist → Prompt:
   :::user_input
   Expanded test files already exist in `$DOMAINS_DIR/<domain>/specs/tests/`. Overwrite and regenerate? [y/n]
   :::
   If `n`, stop.

## Phase 1: Build Coverage Map

Read `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` (and any `> Using <SubModule>` imports it pulls in) and `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` and build a map of all testable thresholds and boundaries. Do not generate any tests yet.

### 1a. Extract Thresholds

Collect all numeric boundary points from the Catala source:

1. **Table-like list-of-record constants** — Catala expresses lookup tables as `definition <table_name> equals [ <row>; <row>; ... ]` over a record type. For each such definition, iterate the rows and extract every numeric field value (e.g., income limits per household size). Note the last explicit row; check for a separate computed scope variable (or comment) that extends the formula past the last row (e.g., "Size 9+: add $X per additional person above Y").

2. **Named constants** — `definition <NAME> equals <literal>` at scope level. Each constant is a candidate boundary point.

3. **Exception conditions** — Parse `under condition <expr>` clauses on rules and `definition ... under condition ...` for comparison operators (`>`, `<`, `>=`, `<=`). Extract the right-hand side value (literal or referenced constant). Resolve constant references to their numeric values.

### 1b. Cross-Reference Existing Tests

Load all test files currently in `$DOMAINS_DIR/<domain>/specs/tests/`:
- `<program>_tests.yaml` (baseline, if present)
- Any previously generated `<program>_*_expanded_tests.yaml` files

For each existing test, collect the `inputs:` map. Build a set of all input values already tested for each relevant field.

**Coverage gaps = thresholds from 1a not already present as input values in any existing test.**

Print the coverage map before generating (for transparency):

:::detail
Coverage map for <program>:
  Thresholds found:   [1066, 1311, 1830, 2311, 2792, 3273, 3754, 4235]
  Already covered:    [1830, 3500]
  Boundary gaps:      [1066, 1311, 2311, 2792, 3273, 3754, 4235]
:::

## Phase 2: Generate Tests

For each category below, emit only cases not already covered by any existing test. Before adding any case, verify no existing test file has an identical `inputs:` map (compare the full input map as written — a case that omits optional fields is distinct from one that includes them).

All tests are **blackbox only**: `inputs:` uses only declared input fields (from the manifest's `inputs.<Entity>.<field>` block); `expected:` contains only the domain's declared `outputs:` fields. No `computed:` field assertions.

### Computing `expected:` via the Catala evaluator (U3)

For every generated test case (drv_*, bnd_*, edg_*), compute the `expected:` block by running the Catala evaluator (U3's `catala_eval` wrapper) on the synthesized `inputs:`. Do not derive expected outcomes by mentally tracing rule logic.

1. Write the case's `inputs:` map to a temporary JSON file.
2. Run `xlator evaluate-catala <domain> <program> --inputs <tmpfile>`.
3. Parse stdout as JSON. The contract is `{outputs, computed, reasons, debug}`. Use `result.outputs` for the `expected:` block, filtered to the declared `outputs:` fields.

If the evaluator exits non-zero, surface the stderr in a `:::error` block and skip the case. Two non-fatal cases worth handling explicitly:

- **Missing required input** (Phase 2c null-input scenarios deliberately do this) — the evaluator exits 1. For null/malformed cases, set `expected:` to the fully-denied state (e.g., default of each declared output, plus an empty `reasons: []` when present).
- **Multi-module scope-call resolution failure** — surface the stderr; the `Using` declaration may need re-checking against the manifest. Fall back to manual derivation for that case and note it in the file header.

### Output file format

Each output file follows the same structure as `<program>_tests.yaml`:

```yaml
# Auto-generated by /expand-tests. Do not edit directly.
# Re-run /expand-tests to regenerate.
test_suite:
  spec: "<program>.catala_en"
  description: "<category> expanded tests — generated <YYYY-MM-DD>"
  version: "1.0"

tests:
  - case_id: "<prefix>_001"
    description: "..."
    inputs:
      <field>: <value>    # flat key-value only, never nested
    expected:
      <decision_field>: <value>
    tags: ["<tag1>", "<tag2>"]
```

---

### 2a. Derived from extracted-tests.yaml → `<program>_derived_from_extracted_tests.yaml`

**Case ID prefix:** `drv_NNN`

Read `$DOMAINS_DIR/<domain>/policy_facets/extracted-tests.yaml`. Mine it for test scenarios even when `extracted_tests: []`:

- **From list entries with `notes:`** — Unmappable values in `notes:` often describe computed results (e.g., "net income after deductions = $320"). Use them to infer plausible boundary inputs to test.
- **From `extracted_tests:` entries (non-empty list)** — Each entry is a concrete policy example. If it is not already in `<program>_tests.yaml` (match by `case_id` OR by identical `inputs:` map), include it here as a `drv_*` case. Preserve any explicit `expected:` values from the source narrative; when the narrative is silent on a field, fill it via the Catala evaluator (see "Computing `expected:`" above).
- **From header comments** — Comments above `extracted_tests:` document partial examples and unmappable fields. Use this narrative to construct test inputs; derive `expected:` via the Catala evaluator.

**This is best-effort derivation, not exhaustive coverage.** State this clearly in the file header:

```yaml
# NOTE: Tests in this file are inferred from narrative context in extracted-tests.yaml
# (header comments, notes: fields, partial examples). They are best-effort and may
# need manual review. Do not treat this file as a complete specification.
```

If no derived tests can be inferred (file is completely empty with no comments or notes), write:

```yaml
tests: []
# No derivable scenarios found in extracted-tests.yaml.
```

---

### 2b. Boundary Tests → `<program>_boundary_expanded_tests.yaml`

**Case ID prefix:** `bnd_NNN`

For each gap identified in the coverage map, generate three cases per threshold value `N`:

| Case | Input value | Expected outcome | Tags |
|------|-------------|-----------------|------|
| Just below limit | `N - 1` | Pass or fail per rule operator | `["boundary", "allow"\|"deny", ...]` |
| Exactly at limit | `N` | Pass or fail per rule operator | `["boundary", "allow"\|"deny", ...]` |
| Just above limit | `N + 1` | Pass or fail per rule operator | `["boundary", "allow"\|"deny", ...]` |

**For table-keyed thresholds** (e.g., threshold value varies by categorical key): generate one N-1/N/N+1 set **per table row**, not one set across all keys.

**For extended table rows** (formula rows in `computed:` scope variables in the Catala source): calculate the actual limit for at least one key beyond the last table row (e.g., count = last row + 1) and generate boundary cases for that value.

Derive `expected:` via the Catala evaluator (see "Computing `expected:`" above).

---

### 2c. Null Input Tests → `<program>_null_input_expanded_tests.yaml`

**Case ID prefix:** `nil_NNN`

Generate one test per scenario below for each **required** input fact field declared in the naming manifest. Required fields are those without `optional: true` on their manifest entry; do not generate null tests for optional fields.

| Scenario | What to set | Tags |
|----------|-------------|------|
| Required field omitted | Omit the field entirely from `inputs:` | `["null_input", "deny"]` |
| Required field set to null | `field: null` | `["null_input", "deny"]` |
| Wrong type (number field given string) | `field: "not-a-number"` | `["malformed", "deny"]` |
| Negative value on a non-negative field | `field: -1` | `["malformed", "deny"]` |

All null/malformed tests assert denial. The Catala evaluator will exit 1 with a missing-required-input error for omitted-field cases; treat that as confirmation and set `expected:` to the fully-denied state (per "Computing `expected:`" above). For typed-wrong / negative-value cases that the evaluator can execute, use its output.

---

### 2d. Edge Case Tests → `<program>_edge_case_expanded_tests.yaml`

**Case ID prefix:** `edg_NNN`

Generate tests for extreme values and unusual combinations not already covered by boundary tests. Adapt to the fields declared in the manifest's `inputs.<Entity>.<field>` blocks — skip any scenario that references a field not present.

| Scenario | Description |
|----------|-------------|
| All-zero numeric inputs | All numeric fields set to 0; minimum entity count (1) |
| Maximum count value | Largest categorical key in the Catala source's lookup tables + 2 (to exercise the formula extension) |
| All numeric inputs combined | All numeric fact fields set to non-zero values simultaneously |
| All adjustments combined | All adjustment-related fact fields set to their maximum values simultaneously |
| All exemptions active | All exemption-related boolean fields set to true, if such fields exist |
| Minimum entity count | Smallest possible count with a key value exactly at the applicable limit |
| Extreme numeric value | Primary numeric field set to 10× the highest table limit |

For each edge case, derive `expected:` via the Catala evaluator (see "Computing `expected:`" above).

---

## Output

After generating all four files, print a summary:

:::important
/expand-tests complete for <domain>/<program>:

  <program>_derived_from_extracted_tests.yaml    — N cases (drv_*)
  <program>_boundary_expanded_tests.yaml          — N cases (bnd_*)
  <program>_null_input_expanded_tests.yaml        — N cases (nil_*)
  <program>_edge_case_expanded_tests.yaml         — N cases (edg_*)

  Total new cases: N
  Skipped (duplicates): N
:::

Then emit the Catala test fixture peers for each non-null-input YAML written above:

> **Run `/catala-emit-tests <domain> <program>`.** Skip pre-flight — already verified above. The sub-skill enumerates every `<program>*_tests.yaml` under `specs/tests/`, emits a typechecking `.catala_en` peer for each non-null-input file, and self-checks via `clerk typecheck`. `*_null_input_expanded_tests.yaml` is skipped (null inputs aren't Catala-encodable). If the sub-skill returns `:::user_input` (unresolved clerk-loop), relay the user's response back to the sub-skill before continuing.

After the sub-skill returns successfully, record the tests-tier manifest so `/check-freshness` can later detect drift between `specs/*.catala_en` and the expanded test files (the manifest captures both YAML and `.catala_en` peers in a single write):

```bash
xlator record-tier-manifest <domain> --tier tests
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

Note: the manifest records the current `specs/*.catala_en` SHA at this run's write time. The baseline `<program>_tests.yaml` (read by Phase 1 but not rewritten by this skill) may have been generated against a different Catala-source SHA. If you need full tests-tier consistency, run `/create-tests <domain>` to refresh the baseline first.

:::next_step
Run `xlator catala-pipeline <domain> <program>` to verify the emitted Catala tests via `clerk test`.
:::

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/tests/<program>_derived_from_extracted_tests.yaml` | Created / Overwritten |
| `$DOMAINS_DIR/<domain>/specs/tests/<program>_boundary_expanded_tests.yaml` | Created / Overwritten |
| `$DOMAINS_DIR/<domain>/specs/tests/<program>_null_input_expanded_tests.yaml` | Created / Overwritten |
| `$DOMAINS_DIR/<domain>/specs/tests/<program>_edge_case_expanded_tests.yaml` | Created / Overwritten |

## Common Mistakes to Avoid

- **Don't nest inputs by entity name** — `inputs:` is always flat key-value (`household_size: 3`, never `household: {size: 3}`)
- **Don't assert computed fields** — `expected:` contains only declared `outputs:` fields, never `computed:` fields like `gross_limit` or `net_income`
- **Don't generate one bnd_* case per threshold** — for table-keyed limits (e.g., limit varies by household size), generate N-1/N/N+1 sets for **each table row**, not just one set overall
- **Don't duplicate existing tests** — always cross-check the full `inputs:` map against all files in `specs/tests/` before emitting a case
- **Don't treat derived tests as exhaustive** — the `drv_*` file is best-effort; include the caveat comment in the file header
- **Don't generate null tests for optional fields** — only required input fields (those without `optional: true` in the naming manifest) get null/missing-input tests
- **Don't hard-code domain-specific field names** — read `inputs:` and `outputs:` from the naming manifest; do not assume fields like `eligible` or `reasons` exist in every domain
