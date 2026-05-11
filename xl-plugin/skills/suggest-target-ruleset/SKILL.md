---
name: suggest-target-ruleset
description: Suggest Target Rulesets given Policy Documents
---

# Suggest Target Rulesets given Policy Documents

Analyze a domain's `policy_facets/computations/` per-file files and suggest 1–3 candidate target rulesets based on all their information, including section headings, summary, topic tags, and computation hints. Saves suggestion files to `specs/suggested_targets/`. The user will select one of the suggestion files as the input to `/declare-target-ruleset`.

The optional `<hint>` argument (e.g., "eligibility" or "benefit calculation") narrows what kinds of rulesets to suggest — it prioritizes candidates that match the hint phrase but still reads the whole index. When no hint is provided, all distinct policy scopes found in the index are candidates with a preference for rulesets that cover more topics.

## Input

```
/suggest-target-ruleset <domain> [<hint>]
```

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     :::
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     Run: /new-domain <domain>
     :::
     Then stop.

3. **Per-file computations present?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - ABSENT or empty → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first, then re-run /suggest-target-ruleset <domain>.
     :::
     Then stop.

---

## Process

### Step 1: Analyze per-file computations

Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML map. Read `data["sections"]` as the list of `{heading, summary, tags, computations?}` section blocks. The source path of each section is encoded in the file's relative path under `policy_facets/computations/` — strip the trailing `.yaml` to recover `<rel>.md`, then prefix with `input/policy_docs/`. (A section in `policy_facets/computations/sub/foo.md.yaml` describes `input/policy_docs/sub/foo.md`.)

Legacy on-disk files may carry a top-level `naming_manifest:` key and per-computation `variables:` lists from prior versions; both are silently ignored. This skill reads `data["sections"]` only and identifies variables from `expr_hint:` and `description:` per the rules below.

Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of doc signals.

Cluster the index signals to identify 1–5 distinct policy scopes. For each scope, derive a candidate target ruleset:

**`expr_hint:` parse rule** (uniform across consumer skills): when a computation carries `expr_hint:`, split on the first `=`; the LHS (whitespace-trimmed) is the snake_case **output name** for that computation, and the RHS is the expression. Tokenize the RHS for snake_case identifiers (skipping numeric literals, string literals, and built-in keywords like `if`, `else`, `and`, `or`, `not`, `min`, `max`, `sum`) — those identifiers are the **input names**. When `expr_hint:` is absent (descriptive-only computation), fall back to scanning `description:` prose for variable names mentioned in the source's terminology.

**Signals to extract:**
- **Topic tags** across all sections → cluster to find prominent domain areas (e.g., "income", "eligibility", "household")
- **Section headings** → reveals statutory structure and sub-program scope
- **File summaries** → reveals program scope and terminology
- **Variable inventory** → for each section's computations, apply the `expr_hint:` parse rule above to collect (output name, input names) pairs. Variables appearing only on the RHS of any `expr_hint:` (and never on an LHS) are leaf inputs; variables appearing on an LHS are computed (intermediate or final) outputs. Variables that appear on an LHS in one computation and on the RHS of another are intermediate computed variables.
- **Computation hints** → collect all `computations:` entries from sections that have the field; trace variable chains via the `expr_hint:` parse rule above (LHS-of-one is RHS-of-another → intermediate computed); collect `expr_hint:` RHS values keyed by their LHS output name; collect `preconditions:` expressions keyed by their computation's output name. Recurring precondition clauses across many entries (e.g., a large cluster of computations all gated on `"applicant is over 65"`) signal a distinct policy scope and should yield a separate candidate ruleset rather than being mixed into a more general one. If the index has no `computations:` entries, skip this signal.
- **Stage membership** → collect each section's `stage:` value (when present). Apply the same suffix-stripping normalization as `/create-ruleset-groups` (drop a trailing `_test` / `_check` / `_evaluation`). When ≥1 section has a `stage:` value, treat distinct (post-normalization) stages as **primary clustering boundaries** for candidate target rulesets — distinct stages that span large variable counts are strong signals for distinct candidate rulesets, stronger than tag/heading similarity alone. Do not merge candidates across distinct `stage:` values; stage boundaries are explicit doc signals, while tag/heading clusters are inferred. When no section has `stage:`, fall through to existing tag/heading/computation-hint clustering unchanged.

**For each candidate, derive:**
- `ruleset_name` — snake_case base filename (e.g., `eligibility_check`, `income_calculation`)
- `display_name` — human-readable title (e.g., "Determine Program Eligibility")
- `description` — one sentence describing what the ruleset computes
- `role` — AI persona for extraction (e.g., "You are a policy-to-rules analyst for eligibility determination.")
- `scope` — extraction goal sentence (e.g., "Convert the provided policy text into explicit, testable eligibility rules that produce an eligibility decision.")
- `inputs.<EntityName>.<field>` — entity-grouped input variables. See "Entity inference" below for the rule. Each `<field>` is keyed by snake_case variable name and carries optional `{type, description}`.
- `computed.<field>` — flat block of computed (intermediate) variables. Variables identified as intermediate via the variable-chain analysis above (LHS-of-one, RHS-of-another) flow here. Each entry carries optional `{type, description}`.
- `outputs.<field>` — flat block of output variables. Each entry carries `{type, description, primary: true|false}`. Exactly one output has `primary: true` per ruleset (the candidate's main decision); all others are secondary (denial reasons, verification flags, etc.). Variables that appear on an LHS but never on a downstream RHS are terminal outputs.

**Entity inference for `inputs.<EntityName>.<field>`.** Inputs are the leaf variables — those that appear on the RHS of some `expr_hint:` or in `description:` prose but never on an LHS, plus variables in descriptive-only computations that the source treats as supplied rather than computed. For each input, determine its owning entity from policy doc context. Entities are CamelCase nouns representing the conceptual owner of their fields — common examples: `Applicant`, `Household`, `Income`, `Asset`, `Resource`. Use these signals in order:

1. Section heading and surrounding section text for the variable's source — a variable surfaced under a "Household composition" heading likely belongs to `Household`; a variable under "Applicant demographics" likely belongs to `Applicant`.
2. The variable's source policy phrase (the verbatim noun phrase the source uses for the concept) — phrasing like "applicant's age" → `Applicant.age`; "household size" → `Household.household_size`.
3. Variable name semantics — e.g., a variable whose name starts with a clear entity prefix may indicate ownership when section context is ambiguous, but do NOT rely on prefix alone — `gross_income` is not owned by a `Gross` entity.

When the entity is unclear or ambiguous after applying the above signals, emit the field under the fallback entity `Case`. Do not invent a one-off entity per variable to avoid the fallback — `Case` exists exactly for variables that don't have a clear conceptual owner. Analysts can regroup entities during `/declare-target-ruleset` confirm.

**Type inference.** For each variable, the AI may infer a type from policy doc context (e.g., "monthly amount" → `money`; a yes/no field → `bool`; "as of" date → `date`; a bulleted enumeration → `enum`). When inference fails, omit `type:` rather than guess — downstream consumers tolerate a missing type.

**Description.** Optional per field. Emit when there is a clear signal in the source policy text (e.g., a definition or contextual phrase). Omit when no signal exists rather than fabricate.

**When `<hint>` is provided:**
- Rank candidates by relevance to the hint phrase — prefer section headings, topic tags, and computation hints that match the hint
- Show a display header: `Suggestions focused on: <hint>`
- Still read every per-file file under `policy_facets/computations/` — the hint is a prioritization signal, not a filter that discards unrelated sections entirely
- If hint matches nothing strongly, show all candidates and note: `(No strong match found for "<hint>" — showing all candidates)`

**Display all candidates** in a numbered list:

:::detail
Suggestions focused on: <hint>            ← omit this line if no hint was provided

  1. <ruleset_name>
     <description>
     Inputs: <comma-separated entity names>
     Output: <primary_output_name> (<type>)

  2. <ruleset_name>
     <description>
     Inputs: <comma-separated entity names>
     Output: <primary_output_name> (<type>)

  3. <ruleset_name>   ← include only if a third distinct scope is identifiable
     ...
:::

### Step 2: Save

Save each candidate:

1. Ensure `$DOMAINS_DIR/<domain>/specs/suggested_targets/` directory exists. Create it if absent.

2. Write `$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml` using exactly this schema:

```yaml
# Auto-generated by /suggest-target-ruleset — do not edit manually
ruleset_name: <snake_case>
display_name: <string>
description: <string>
role: <string>
scope: <string>
inputs:
  <EntityName>:                 # CamelCase noun (Applicant, Household, Income, ...). Fallback: Case.
    <field_name>:               # snake_case variable name
      type: bool | money | int | float | string | enum | list | date  # optional; omit when no signal
      description: <string>     # optional
    # repeat for each field under this entity
  # repeat for each entity
computed:                       # flat — no entity grouping
  <field_name>:
    type: <type>                # optional
    description: <string>       # optional
outputs:                        # flat — exactly one entry has primary: true
  <field_name>:
    type: <type>
    description: <string>
    primary: true | false
  # repeat for each output
```

   YAML conventions:
   - Two-space indentation throughout
   - All `description:` and `display_name:` values as quoted strings
   - Exactly one `outputs.<field>.primary: true` per file — every other output has `primary: false`
   - Omit `type:` and `description:` when no signal exists rather than guessing
   - Use the fallback entity `Case` for input fields with no clear conceptual owner — do not invent one-off entities to avoid the fallback
   - `# Generated:` date as YYYY-MM-DD (today's date)

3. Confirm each file written:
   :::important
   Saved: $DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml
   :::

After all saves, suggest the next step:

:::next_step
Next: Run /declare-target-ruleset <domain> <ruleset_name> to create the guidance/ files from a suggestion file.
:::

---

## Output

:::important
$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml    [CREATED]
:::

(one line per saved file)

---

## Common Mistakes to Avoid

- **Do not include `intermediate_variables`, `constraints`, `standards`, `guidance`, `edge_cases`, `skeleton:`, `ruleset_groups:`, or `ruleset_modules:` in suggestion files** — those are written by later skills (`/create-skeleton`, `/create-ruleset-groups`, `/create-ruleset-modules`)
- **Do not read files under `$DOMAINS_DIR/<domain>/input/`** — `policy_facets/computations/` is the sole source of doc signals
- **Do not suggest a single monolithic ruleset when the index shows multiple distinct policy scopes** — identify separate scopes as separate candidates (e.g., an income exclusion chain and an eligibility determination are two distinct scopes)
- **Do not omit the `primary:` flag from any `outputs.<field>` entry** — every output entry must have `primary: true|false`, and exactly one per file must be `true`.
- **Do not guess `type:` when no signal exists** — omit the field instead. Same for `description:`.
- **Do not invent a one-off entity per variable to avoid the `Case` fallback** — `Case` is the correct entity for input fields with no clear conceptual owner. Splintering into `Misc1`, `Misc2`, etc. is worse than using `Case`.
- **Do not group computed variables under entities** — `computed:` is flat. Computed values are functions of multiple entities' inputs; they don't conceptually belong to one entity.
- **Do not use block-style lists for `type:` values** — `type: enum` not `type:\n  - approve\n  - deny`
- **Do not guess domain names or paths** — always expand `$DOMAINS_DIR` from `.xlator.local.env` if the variable is unknown
- **When hint matches nothing strongly, show all candidates** — do not suppress candidates because they don't match the hint; the hint is a ranking signal only
- **Do not merge candidates across distinct `stage:` values** — `stage:` is an explicit doc signal; merging two stage-tagged scopes into one candidate ruleset discards information the analyst already encoded in the source
- **Do not write `stage:` or modify it** — `stage:` is single-owner; only `/extract-computations` writes the field. This skill reads it
