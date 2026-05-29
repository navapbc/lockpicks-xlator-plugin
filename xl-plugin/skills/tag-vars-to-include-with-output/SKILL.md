---
name: tag-vars-to-include-with-output
description: Tag Variables to Include with Output
---

# Tag Variables to Include with Output

Identify which intermediate computed variables should be exposed in the API's `ComputedBreakdown` response and write them to `guidance/include-with-output.yaml`. Runs non-interactively — no mid-run prompting.

## Purpose

`include_with_output` variables appear alongside the final decision in the API response. Their purpose is **explainability**: they surface the key intermediate values that led to the output so that callers can understand *why* the ruleset reached its conclusion — not just *what* it decided.

Good candidates are variables that a person would need to see to understand the decision:
- Sub-scope result bindings (e.g., `client_result`, `dol_result`) — the sub-scope outputs that the parent scope reads via `<subvar>.<output_field>` access
- Pivotal computed values referenced in `under condition` clauses (e.g., `is_compatible`, `income_limit`, `after_half`) — the thresholds and comparisons the ruleset used to decide
- Variables named in denial or adjustment reasons — the quantities that triggered a specific outcome

Poor candidates are purely internal chain steps with no standalone interpretive value (e.g., `after_eitc` as an intermediate step toward `adjusted_earned_income` when `adjusted_earned_income` itself is the meaningful quantity). When in doubt, favor inclusion — callers can filter, but cannot see what is not exposed.

Best run **after `/extract-sample-rules`** — that command may generate Catala snippets containing scope-call dot-access expressions (`<subvar>.<output_field>`) and `under condition` decision predicates not yet visible in the skeleton's `computations:` list. Because all writes are merge-safe, it is also safe to run earlier and re-run after.

Read `../../core/output-fencing.md` now.

## Input

```
/tag-vars-to-include-with-output [<domain>]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance/metadata.yaml` files as a numbered menu and prompt:

:::user_input
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
:::

Await the user's response and use it as `<domain>`. Then continue.

## Process

Run the deterministic detection tool:

```bash
xlator tag-vars-include-output <domain>
```

The tool runs all pre-flight checks (domain folder exists, `specs/naming-manifest.yaml` exists), executes the three detection passes, merges with any existing entries, and writes `specs/guidance/include-with-output.yaml`.

Open a `:::important` fence. Relay the tool's stdout verbatim. Close the fence.

If the tool exits non-zero, emit `:::error` with the captured stderr and stop.

Then record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

```bash
xlator record-tier-manifest <domain> --tier guidance
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

Then suggest next steps:

:::next_step
Next: Run /create-sample-tests <domain> to generate sample test cases used to assess correctness of the generated ruleset
:::

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance/include-with-output.yaml` | Created (first run) or merged (subsequent runs) |

## Common Mistakes to Avoid

- **Tag for explainability, not completeness** — the goal is to surface variables that help callers understand the decision; not every computed variable needs to be exposed
- **Do not remove existing entries** from `include-with-output.yaml` — the tool only adds; removal is a manual edit
- **Idempotent** — running this command twice must produce no duplicates and no changes on the second run
