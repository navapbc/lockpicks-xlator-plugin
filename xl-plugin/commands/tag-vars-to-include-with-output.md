# Tag Variables to Include with Output

Identify which intermediate computed variables should be exposed in the API's `ComputedBreakdown` response and merge them into `guidance.yaml` under `intermediate_variables.include_with_output`. Runs non-interactively — no mid-run prompting.

## Purpose

`include_with_output` variables appear alongside the final decision in the API response. Their purpose is **explainability**: they surface the key intermediate values that led to the output so that callers can understand *why* the ruleset reached its conclusion — not just *what* it decided.

Good candidates are variables that a person would need to see to understand the decision:
- Sub-ruleset result objects (e.g., `client_result`, `dol_result`) — the intermediate module outputs feeding into the final decision
- Pivotal computed values referenced in decision conditions (e.g., `is_compatible`, `income_limit`, `after_half`) — the thresholds and comparisons the ruleset used to decide
- Variables named in denial or adjustment reasons — the quantities that triggered a specific outcome

Poor candidates are purely internal chain steps with no standalone interpretive value (e.g., `after_eitc` as an intermediate step toward `adjusted_earned_income` when `adjusted_earned_income` itself is the meaningful quantity). When in doubt, favor inclusion — callers can filter, but cannot see what is not exposed.

Best run **after `/xl:extract-sample-rules`** — that command may generate CIVIL snippets with `invoke:`-produced dot-access expressions and decision-condition variables not yet visible in the skeleton's `computations:` list. Because all writes are merge-safe, it is also safe to run earlier and re-run after.

## Input

```
/tag-vars-to-include-with-output [<domain>]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance.yaml` files as a numbered menu and prompt:

:::user_input
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
:::

Await the user's response and use it as `<domain>`. Then continue.

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO → Print:
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

---

## Process

### Detection pass 1 — invoke-derived variables (skeleton computations)

Scan all `computations:` entries across every `intermediate_variables.categories[]` entry in `guidance.yaml`. For each entry whose `expr:` value contains dot-notation (`<identifier>.<identifier>`), collect the **base name** — the portion before the first dot.

Example: `expr: "client_result.adjusted_earned_income"` → base name `client_result`.

These are ruleset module result objects whose contents explain how the parent module's inputs were processed. They are high-value explainability variables.

Collect all such base names as `auto_tagged_1`.

### Detection pass 2 — decision-condition and invoke-derived variables (sample rule CIVIL snippets)

Scan all CIVIL snippets in `ruleset_modules[].sample_rules[].civil:` and `sample_rules[].civil:` in `guidance.yaml`. Collect two categories of variable names:

**(a) Invoke-derived:** dot-notation access patterns (`<identifier>.<identifier>`) — collect the base name (before the first dot). Catches invoke-derived variables that `/xl:extract-sample-rules` generated but that were not yet in the skeleton's `computations:` list.

**(b) Decision-condition variables:** variable names that appear in `when:` conditions of `categorical:` rules. These are the pivotal computed values the ruleset evaluated to reach its decision — exactly the values a caller needs to understand the outcome. Scan for YAML keys `when:` and collect the variable name from each condition entry (e.g., `is_compatible: false` → `is_compatible`; `client_result.adjusted_earned_income > income_limit` → `income_limit`).

Collect all such names as `auto_tagged_2`.

### Detection pass 3 — declared output variables

Collect `output_variables.primary.name` and all `output_variables.secondary_decisions[].name` values from `guidance.yaml`. These are the primary decision outputs the module is designed to produce.

Collect all such names as `output_declared`.

### Merge and write

Compute: `new_include_with_output` = `auto_tagged_1` ∪ `auto_tagged_2` ∪ `output_declared` ∪ existing `intermediate_variables.include_with_output` values (if any).

Deduplicate. Preserve all existing names — never remove entries.

Write the merged list to `guidance.yaml` under `intermediate_variables.include_with_output`. Do not modify any other section of `guidance.yaml`.

### Print result

Print one line per name in the final `include_with_output` list, labeled with its detection reason:

:::important
include_with_output written to guidance.yaml:
  client_result   (invoke-derived: skeleton computations)
  dol_result      (invoke-derived: sample rule CIVIL snippet)
  is_compatible   (decision condition: when: clause in categorical rule)
  income_limit    (decision condition: when: clause in categorical rule)
  eligible        (output variable: output_variables.primary)
  denial_reason   (output variable: output_variables.secondary_decisions)
  after_half      (existing)
:::

If no variables were detected and no existing values were present, print:

:::important
No variables auto-detected. include_with_output: [] written to guidance.yaml.
:::

Then suggest next steps:

:::next_step
Next: Run /xl:create-sample-tests <domain> to generate sample test cases used to assess correctness of the generated ruleset
:::

---

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Updated — `intermediate_variables.include_with_output` merged |

---

## Common Mistakes to Avoid

- **Tag for explainability, not completeness** — the goal is to surface variables that help callers understand the decision; not every computed variable needs to be exposed
- **Do not remove existing entries** from `include_with_output` — this command only adds; removal is a manual edit
- **Do not modify any section other than `intermediate_variables.include_with_output`** — preserve all other guidance.yaml content verbatim
- **Base name only for dot-notation** — collect the identifier before the first dot (e.g., `client_result` from `client_result.adjusted_earned_income`), not the full expression
- **Idempotent** — running this command twice must produce no duplicates and no changes on the second run
