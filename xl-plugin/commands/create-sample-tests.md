# Create Sample Tests

Generate pre-extraction test scaffolding from `guidance.yaml` content alone — before a CIVIL file exists. Writes test cases to a `sample_tests:` field inside `guidance.yaml`. Runs non-interactively — no mid-run prompting.

These tests serve as planning scaffolding to validate coverage intent before running `/xl:extract-ruleset`. They are not a substitute for the validated test suite produced by `/xl:create-tests` after extraction.

See `$CLAUDE_PLUGIN_ROOT/core/tests/eligibility_tests.yaml` for a complete working example of test case structure.

## Input

```
/create-sample-tests [<domain>]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance.yaml` files as a numbered menu and prompt:

:::user_input
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
:::

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **`guidance.yaml` exists?**
   - NO → Print:
     :::error
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:suggest-target-ruleset <domain> first.
     :::
     Stop.

4. **Sample rules available?** Check whether any of the following is present and non-empty in `guidance.yaml`:
   - Any `ruleset_modules[]` entry has a non-empty `sample_rules:` sub-key, **or**
   - `sample_rules:` is a non-empty top-level list

   If neither is present:
   :::error
   No sample rules found in guidance.yaml.
   Run /xl:extract-sample-rules <domain> first to generate sample rules.
   :::
   Stop.

**Degraded mode:** If pre-flight step 4 passes but only `sample_rules:` is present (no `ruleset_modules[].sample_rules`), print:

```
⚠ No sample_rules found in ruleset_modules — test inputs derived from input_variables only.
  Run /xl:extract-sample-rules <domain> for richer test data.
```

Continue in degraded mode.

---

## Process

### Step 1: Derive input and output field names

Read from `guidance.yaml`:

**Input field names** — collect from two sources:
1. All names in `input_variables.categories[].examples` lists (flat variable names)
2. (Non-degraded mode only) Variable names used as input bindings in `ruleset_modules[].sample_rules[].civil` CIVIL snippets — parse `inputs:` field names and `with:` binding keys

**Expected field names and value sets** — collect from:
- `output_variables.primary`: name, type, and (if enum) allowed values list
- `output_variables.secondary_decisions[]`: name and type for each
- `intermediate_variables.include_with_output`: names of computed fields to include in `expected` for cases that exercise those paths

**Thresholds and constants** — scan `constants_and_tables:` in `guidance.yaml` for named tables or constants that might yield concrete boundary values for test inputs.

Show step checklist:
:::progress
Steps:
  [✓] 1. Derive input and output field names
  [ ] 2. Generate test cases
  [ ] 3. Merge into guidance.yaml
:::

### Step 2: Generate test cases

Generate test cases targeting the 6-tag coverage minimum from `/xl:create-tests`. For each tag, generate one case if the tag is applicable given the guidance content. Do not force a tag that has no basis in the guidance.

**`inputs:` are always flat key-value** — never nest by entity name. Use only the input field names derived in Step 1.

**`expected:` values** are derived from `output_variables` declarations:
- `bool` type → `true` / `false`
- `enum` type → use values from `output_variables.primary.values[]` (e.g., `approve`, `deny`, `manual_verification`)
- `money` / `int` / `str` → use illustrative values consistent with the guidance; note them in `assumptions:` in guidance.yaml if no concrete value is available

Float tolerance ±0.005 applies automatically to numeric `expected` fields.

**Coverage targets:**

| Tag | Case to generate | When applicable |
|-----|-----------------|-----------------|
| `allow` | All threshold conditions comfortably met — happy path | Always |
| `deny` + `primary_threshold` | Primary input value exceeds the threshold | When a primary threshold is present in guidance |
| `deny` + `adjusted_threshold` | Passes primary threshold, fails after adjustments | When adjustment logic is in `sample_rules` |
| `allow` + `exemption` | Exemption or special path active | Only when `edge_cases` or `sample_rules` mention an exemption |
| `allow` + `boundary` | Input value exactly at the threshold (≤ limit = pass) | When a concrete threshold value is available |
| `deny` + `edge` | Extreme input: maximum count value, all-zero numeric values, etc. | Always |

**`case_id` format:** `<primary_tag>_<NNN>` — e.g., `allow_001`, `deny_gross_001`, `allow_boundary_001`.

**Test case format:**
```yaml
- case_id: "allow_001"
  description: "<one sentence: what this case tests>"
  inputs:
    <field_name>: <value>
    # flat key-value only — never nested
  expected:
    <output_field>: <value>
    # include fields from include_with_output when relevant
  tags: ["allow"]
```

For `deny` cases, include a `reasons:` field in `expected` if `denial_reason` or equivalent is in `output_variables.secondary_decisions`:
```yaml
  expected:
    eligible: deny
    reasons:
      - code: "<DENIAL_CODE_FROM_GUIDANCE>"
```

If a threshold value is unknown (no concrete number available in guidance), use a plausible illustrative value and record: `"Assumed <field> threshold of <value> — confirm against policy"` in `assumptions:` in `guidance.yaml`.

Show updated step checklist.

### Step 3: Merge into guidance.yaml

Read the current `guidance.yaml`. Merge the generated test cases into the top-level `sample_tests:` list:
- If `sample_tests:` does not exist, add it after `sample_rules:` (or after `edge_cases:` if `sample_rules:` is absent)
- Append only entries whose `case_id:` is not already present in the existing `sample_tests:` list
- Do not overwrite or remove existing entries

Write the updated `guidance.yaml`.

Show final step checklist (all complete).

Print:
:::important
sample_tests written to guidance.yaml:
  allow_001           (allow)
  deny_gross_001      (deny, gross_test)
  deny_net_001        (deny, net_test)
  allow_boundary_001  (allow, boundary)
  deny_edge_001       (deny, edge)
:::

:::next_step
Next: Run /xl:extract-ruleset <domain> to extract the CIVIL ruleset.
      After extraction, run /xl:create-tests <domain> for the validated test suite.
:::

---

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Updated — `sample_tests:` entries merged; `assumptions:` may have new entries |

---

## Common Mistakes to Avoid

- **Do not nest inputs by entity name** — `inputs:` must always be flat key-value (e.g., `client_gross_earned: 1800`, never `client: {gross_earned: 1800}`)
- **Do not require a CIVIL file** — this command runs before `/xl:extract-ruleset`; field names come from `guidance.yaml`, not from a `.civil.yaml` file
- **Do not overwrite existing `sample_tests:` entries** — merge by `case_id:` only; preserve manually authored test cases
- **Do not force coverage tags with no basis in guidance** — if `allow + exemption` has no exemption path in the guidance content, omit that case rather than fabricating it
- **Do not confuse `sample_tests:` with `specs/tests/<program>_tests.yaml`** — these are pre-extraction scaffolding cases in `guidance.yaml`; the validated test suite is produced separately by `/xl:create-tests` after extraction
- **`case_id` values must not change on re-run** — merge is keyed by `case_id`; changing a case_id on re-run creates a duplicate
