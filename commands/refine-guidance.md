# Refine Ruleset Guidance for a Domain

Create or update `guidance.yaml` for a domain — an ruleset guidance file that shapes how `/extract-ruleset` uses policy documents and creates rules. On first run (CREATE), guides the user through guidance template selection and optional doc-aware Q&A to produce a new file. On subsequent runs (UPDATE), loads the existing `guidance.yaml` file and refines it based on user input.

The **guidance template** (in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` and `$DOMAINS_DIR/guidance-templates/`) provides an initial ruleset guidance that is then customized per domain in `$DOMAINS_DIR/<domain>/specs/guidance.yaml`.

## Input

```
/refine-guidance <domain>
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/` directories as a numbered menu, prompt the user to choose, await their response, and use it as `<domain>` before continuing.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     ```
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     ```
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     ```
     Domain not found: $DOMAINS_DIR/<domain>/
     Run: /new-domain <domain>
     ```
     Then stop. Do not scaffold a new domain here — that's `/new-domain`'s job.

3. **Detect mode** — check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:
   - **Present** → **UPDATE mode**
   - **Absent** → **CREATE mode**

4. **Input index required?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - **ABSENT** → Print:
     ```
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /index-inputs <domain> first, then re-run /refine-guidance <domain>.
     ```
     Stop.
   - **EXISTS** → Proceed.

---

## Process

### Step 1 [CREATE]: Ruleset Guidance template selection

Scan `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/*.yaml` and `$DOMAINS_DIR/guidance-templates/*.yaml` for all available guidance template files, reading only the top 5 lines to get the `template_id`, `display_name`, and `description` for each file.

- Present a list for the user to choose one where each option shows: "`<template_id>`: <display_name> (<full_file_path>)".
- Instead of "(Type in another answer)", present "(or paste path of file to use as the ruleset guidance template)".

Print a summary of the selected file's content for the user to review.

After the user confirms the selected guidance template, copy the guidance template to `$DOMAINS_DIR/<domain>/specs/guidance.yaml` and add the following fields as metadata at the top of the file:
```yaml
source_template: <guidance template file (without extension)>
generated_at: <today YYYY-MM-DD>
```

The `source_template` and `generated_at` fields are inserted immediately after `template_id:` so the file reads top-to-bottom: metadata → scope → guidance → variables.

Print: `Created $DOMAINS_DIR/<domain>/specs/guidance.yaml`

### Step 1 [UPDATE]: Load existing file

Read `$DOMAINS_DIR/<domain>/specs/guidance.yaml`. Print a summary:
```
File: $DOMAINS_DIR/<domain>/specs/guidance.yaml
Current guidance: <display_name> (source: <source_template>, updated: <generated_at>)
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: <N> computations across <N> intermediate categories, <N> example rules
```
(Show `Skeleton: none` if no `computations:` fields are present yet in `intermediate_variables` and no `example_rules:` section exists.)

---

### Step 2: Doc analysis

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`.
Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — the index is the sole source of doc signals.

The guidance (`$DOMAINS_DIR/<domain>/specs/guidance.yaml`) will be used as the prompt for an AI to generate a ruleset.
Extend the guidance by extracting relevant signals from the index:

- **Topic tags** across all sections → cluster to find prominent domain areas
- **Section headings** → reveals statutory structure (e.g., income tests, deduction chains)
- **File summaries** → reveals program scope and terminology
- **Computation hints** → collect all `computations:` entries from sections that have the field; trace variable chains (a variable that is the last item in one entry's `variables` list and appears earlier in another entry's `variables` list is an intermediate computed variable); collect `expr_hint` values keyed by their output variable (last item in `variables`). If the index has no `computations:` entries, skip this signal.

For each of the four guidance sections (`constraints`, `standards`, `guidance`, `edge_cases`), generate proposed additions grounded in these index signals. Use computation hints to enrich `guidance` and `standards` proposals with concrete variable names and formula patterns (e.g., "The CIVIL ruleset should define `earned_income_deduction` as a `computed:` field equal to `earned_income * 0.20`").

Merge the doc-derived proposals into `$DOMAINS_DIR/<domain>/specs/guidance.yaml` immediately:
- For each of the four sections, append the proposed items into the section's current list
- Deduplicate: do not add items that are substantively identical to existing items
- Write the updated file to disk

This step runs identically in CREATE and UPDATE modes — no gap-detection branch, no separate proposals display.

### Step 3: Computation skeleton

*Runs in both CREATE and UPDATE modes.*

Build and display the skeleton using:

- **ruleset guidance** — `input_variables`, `intermediate_variables`, `output_variables` categories provide the structure and group names (from `guidance.yaml`, which always exists by this point)
- **Step 2 doc signals** (in-memory index signals) — topic tags, section headings, and file summaries enrich variable names; computation hints from signal 4 provide concrete variable names (prefer these over generic `examples` from the guidance template) and expression hints (show as `≈ <expr_hint>` when available from the index, `= ?` when not inferable)

Display the skeleton:

```
**Computation skeleton for [display_name]:**

**Inputs:**
- [variable names from input_variables categories, enriched with domain-specific names]
- ...

**Output:**
- [primary output field] ([type])
- [secondary_decisions fields] ([type])

**Computed:** *(how to get the Output from the Inputs)*

*[category name — category description]:*
- `[variable]` = [expression hint, or `= ?` if not inferable]
- ...

[repeat for each intermediate_variables category]

---
`Confirm` this computation skeleton, or describe what to add, remove, change, or rename.
```

Include an ASCII computation flow diagram only when the dependency graph is non-trivial (more than one path from inputs to output). For simple linear chains with less than 3 steps, omit it.

**On confirm** ("confirm", "yes", "looks good"): Write the skeleton to `guidance.yaml`:

1. Update `input_variables`, `output_variables`, and `intermediate_variables` sections with the confirmed category structure.
2. For each `intermediate_variables` category, rewrite `examples:` with the confirmed variable names from the skeleton display, in display order (replacing any generic template names).
3. For each `intermediate_variables` category, write a `computations:` list — one entry per variable that has a non-null expr hint (shown as `≈ <expr>` in the skeleton display). Each entry has `name:` (the variable name) and `expr:` (the expr hint). Variables shown as `= ?` are omitted from `computations:`. Entries are written in the order they appeared in the skeleton display.

### Step 4: Sub-Ruleset Candidate Detection

*Runs immediately after skeleton confirmation.*

Scan the confirmed skeleton (variable names and category structure) for each heuristic in priority order in order to identify sub-rulesets, which will be implemented as CIVIL sub-modules:

| Priority | Heuristic | Rationale value | Test |
|----------|-----------|-----------------|------|
| 1 | Reuse across entities | `reuse_across_entities` | 2+ entity names in `facts:` (inferred from skeleton input categories) where a common computation prefix would apply to each — e.g., `yyy_earned_income` and `zzz_earned_income` suggest the same `earned_income` sub-module bound to two entities |
| 2 | Policy structure | `policy_structure` | Named sub-section in `input-index.yaml` headings (from Step 2 signals) covers ≥3 intermediate variables in the skeleton |
| 3 | Depth threshold | `depth_threshold` | ≥5 variable names in the skeleton whose names suggest sequential dependence (e.g., `after_*` chain, or `net_*` derived from `gross_*` derived from `total_*`) |
| 4 | User hint | `user_hint` | `sub_rulesets:` already populated in `guidance.yaml` — load existing entries as pre-confirmed |

**If one or more candidates are detected**, display the confirmation table:

```
Sub-Ruleset Candidates
─────────────────────────────────────────────────────────────────────────
  # │ Sub-Module Name   │ Bound Entities          │ Heuristic
  1 │ earned_income     │ YyyData, ZzzRecord      │ reuse_across_entities
  2 │ deduction_chain   │ Household               │ depth_threshold
─────────────────────────────────────────────────────────────────────────
[C] confirm all,  [D] dismiss all,
Per-item: [a] add missed candidate, [r] remove candidate, [e] edit name/entities  
```

In UPDATE mode, existing `sub_rulesets:` entries are displayed as pre-confirmed (marked `[confirmed]`). Only newly detected candidates require a user decision. The user may still add, remove, or edit any entry including pre-confirmed ones.

After the user's response:
- For each confirmed candidate, write it to `$DOMAINS_DIR/<domain>/specs/guidance.yaml` under `sub_rulesets:` (placed after `edge_cases:`, before `example_rules:`). Each entry has `name:`, `description:`, `bound_entities:`, and `rationale:`.
- For dismissed candidates, do not write them.
- For edited entries, write the user-edited values.
- After all decisions, overwrite the `sub_rulesets:` key in the file with the final confirmed list (replacing any prior content under that key).

**If zero candidates are detected** (all heuristics return no results), emit:
```
No sub-ruleset candidates identified. Proceeding with single-module extraction.
```
Clear the `sub_rulesets:` key. (Existing entries in UPDATE mode are preserved.)

**On adjustment response** (user adds, removes, or renames items): Update the skeleton in memory, re-display the full updated skeleton, and re-ask the checkpoint question. No limit on iterations.

**On unrecognized input**: Re-display the skeleton and re-prompt the checkpoint question.

### Step 5: Workflow Stage Elicitation (CIVIL v6)

**Purpose:** Propose `workflow_stages` — named evaluation phases that `rule.group:` annotations will reference. These give rules a home that makes policies scannable and reviewable.

**(a) Scan for phase headings.**
Read `specs/input-index.yaml` (or the source policy document if available). Look for:
- Section headings that name a test phase (e.g. "Income Test", "Household Size Verification", "Categorical Eligibility")
- Logical groupings of rules or conditions described in the policy

**(b) Propose workflow stages.**
Convert detected headings to `snake_case` names and propose a list. Format:

```
Proposed workflow stages
────────────────────────────────────────────────
  1. income_test          — Income eligibility tests
  2. household_test       — Household size and composition tests
  3. categorical_test     — Categorical eligibility checks

[A] accept all or describe the what to edit
(e.g. "add asset_test — Asset limit checks", "remove 2", "rename 1 to gross_income_test"):
```

If no phase headings are found, propose a single catch-all stage based on the module name (e.g. `eligibility`) and note it can be refined later.

**(c) User approval.**
Accept the list as-is, or apply edits. Re-display after each edit. Accept when the user presses Enter or types "ok".

**(d) Write to `guidance.yaml`.**
Write the confirmed stages as a top-level `workflow_stages:` list:

```yaml
workflow_stages:
  - name: income_test
    description: Income eligibility tests
  - name: household_test
    description: Household size and composition tests
```

**UPDATE MODE:** If `workflow_stages:` already exists in `guidance.yaml`, show the existing list and ask:
```
workflow_stages already defined:
  1. income_test — Income eligibility tests
  2. household_test — Household size and composition tests

Keep / Replace / Merge?  (default: Keep)
```
- **Keep**: skip this sub-step, leave file unchanged.
- **Replace**: overwrite with newly proposed list.
- **Merge**: combine existing and new entries, deduplicated by name (new descriptions win on conflict).

### Step 6: Output tag selection

**Auto-detect invoke-derived variables:** scan all `computations:` entries confirmed in Step 3 (and expr_hints retained from Step 2 in-memory signals) for expressions containing dot-notation (`<identifier>.<identifier>`). These variables compute their value by accessing a field on a sub-ruleset result object (e.g., `client_result.adjusted_earned_income`). Collect their names as `auto_tagged`.

Build the display list of remaining intermediate variables from all `examples:` values across every `intermediate_variables` category, excluding names already in `auto_tagged`.

Display:

```
Output tag selection
────────────────────────────────────────────────
Auto-tagged (invoke-derived — expose sub-ruleset results):
  ✓ client_adjusted_income     (expr: client_result.adjusted_earned_income)
  ✓ dol_avg_monthly_adjusted   (expr: dol_result.adjusted_earned_income)

Additional computed variables:
  [income_tests]
    gross_income, net_income, deduction_total
  [exclusion_chain]
    after_student, after_65, after_half, adjusted_earned_income

Which additional variables should also be tagged `tags: [output]`
(to be included as part of the ruleset execution output -- appears in the API's ComputedBreakdown response)?
(Enter variable names, comma-separated, or press Enter to skip):
```

If `auto_tagged` is empty, omit the "Auto-tagged" section.

**In UPDATE mode:** if `include_with_output:` already exists in `guidance.yaml`, display the current value before prompting:
```
Currently tagged: [client_adjusted_income, dol_avg_monthly_adjusted, income_standard]
  (auto-tagged: client_adjusted_income, dol_avg_monthly_adjusted)
Update additional selection (Enter to keep as-is):
```

**Validation:** after user input, check each entered name against the union of all `examples:` values across all `intermediate_variables` categories. If any names are unrecognized, display them and re-prompt:
```
Unknown names: [after_halff, income_totaal]
These don't match any confirmed variable. Check spelling.
Re-enter selection (or 'f' to force-accept):
```
On `f`: accept and save as-is (allows forward-referencing names not yet in examples:).

**Write behavior:** `include_with_output` = `auto_tagged` ∪ user-entered names. Write to `guidance.yaml` under `intermediate_variables.include_with_output` immediately:
```yaml
intermediate_variables:
  include_with_output: [client_adjusted_income, dol_avg_monthly_adjusted, income_standard]
  categories:
    ...
```

- On Enter (no user input): if CREATE mode, write `include_with_output: [<auto_tagged names>]`. If UPDATE mode and `include_with_output:` already exists, preserve the existing value unchanged (re-run auto-detection and merge, keeping any user-added names from previous runs).
- `include_with_output: []` is a valid value when the user explicitly enters nothing and there are no auto-tagged variables.

### Step 7: Q&A refinement

Present all four section contents from `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:

```
Current guidance sections for <domain>:

[1] constraints (<N> items)
    - <item>
    - ...

[2] standards (<N> items)
    - <item>
    - ...

[3] guidance (<N> items)
    - <item>
    - ...

[4] edge_cases (<N> items)
    - <item>
    - ...

Which section would you like to update? [1–4] or 'p' to proceed:
```

**If the user chooses a section** — show the current content for that section and the section's key question:

| Section | Key question |
|---|---|
| `constraints` | "What should I *not* infer or assume in this domain?" |
| `standards` | "Are there normalization rules specific to this program? (units, categories, naming)" |
| `guidance` | "What non-obvious rule patterns should I look for?" |
| `edge_cases` | "What special populations or situations does this program treat differently?" |

After the user answers, update that section in `$DOMAINS_DIR/<domain>/specs/guidance.yaml` immediately, then return to the section menu.

**If the user presses Enter** (no section chosen) — proceed to Step 8.

The section menu repeats after each update. The loop terminates when the user presses Enter or selects `[q] Quit`.

**On `[q] Quit`:** Print:
```
Exiting. guidance.yaml saved at $DOMAINS_DIR/<domain>/specs/guidance.yaml
Run /refine-guidance <domain> to continue refining.
```
Stop.

### Step 8: Preview Gate

`guidance.yaml` was written after each Q&A update in Step 7 and is fully current.
Print: `guidance.yaml is up to date at $DOMAINS_DIR/<domain>/specs/guidance.yaml`

Using in-memory index signals from Step 2 (topic clusters, section headings, policy excerpts, and computation hints from `input-index.yaml`), synthesize 2–3 illustrative CIVIL rules this guidance would shape the AI to extract. Select examples spanning different rule types (categorical, computed, table-lookup) where the policy supports it. For `computed:` examples, use `expr_hint` values from computation hints to populate concrete `expr:` values rather than placeholders.

**When `sub_rulesets:` in `guidance.yaml` is non-empty:** One of the 2–3 examples must demonstrate `invoke:` field access. Use the first entry in `sub_rulesets:` and the first bound entity as the example. Use placeholder field names with a `# illustrative — final names confirmed at extraction` comment. The total example count stays at 2–3 — replace the table-lookup example type if needed (prefer dropping table-lookup over categorical or computed).

If Step 2 observations are no longer in context (large docs), re-read `$DOMAINS_DIR/<domain>/specs/input-index.yaml` silently to reconstruct — do NOT read files under `$DOMAINS_DIR/<domain>/input/`.

Present:

─────────────────────────────────────────────
Preview: Rules this guidance would extract
─────────────────────────────────────────────

Rule 1 — [rule name / topic area]
  Source: "[quoted sentence from input-index.yaml section summary]"
  CIVIL:
    rules:
      - id: ...
        when: ...
        then: ...

Rule 2 — [rule name / topic area]
  Source: "..."
  CIVIL:
    computed:
      - name: ...
        ...

[Rule 3 if a third distinct type is identifiable — otherwise 2 is sufficient]

*(Illustrative samples — run `/extract-ruleset` for the full validated ruleset.)*
─────────────────────────────────────────────
Do these look right?
  [a] Accept
  [1] Refine constraints
  [2] Refine standards
  [3] Refine guidance
  [4] Refine edge_cases
  [m] More rules
  [q] Quit (keep file as-is)

**On [a]:** Write all displayed rules to `guidance.yaml` under a top-level `example_rules:` section (placed after `edge_cases:`). Append to any existing entries and deduplicate by `id:`. Each entry has:
- `id:` — snake_case identifier for the rule
- `rule_type:` — one of `categorical`, `computed`, or `table-lookup`
- `source:` — quoted sentence from the relevant `input-index.yaml` section summary that the rule is grounded in
- `civil:` — the full CIVIL snippet as a literal block scalar (`|`)

Then proceed to Step 9.

**On [m]:** Generate 2–3 additional illustrative rules. Prioritize types not yet shown (e.g., add a table-lookup example if only categorical and computed have been displayed). Append the new rules to the displayed list. Re-present the full gate with the expanded rule set. No limit on `[m]` iterations.

**On [1]–[4]:** Re-ask only that section's Q&A question, showing the current content for that section as the pre-filled default:
```
Current [<section>]: (N items)
  - ...
<section key question> (Enter to keep as-is):
```
After the user answers, update `guidance.yaml` immediately, regenerate the preview, and return to this step. Do not continue through the other sections automatically.

**On [q]:** Do not write `example_rules:` — rules have not been user-approved. Print:
```
Exiting. guidance.yaml saved at $DOMAINS_DIR/<domain>/specs/guidance.yaml
Run /refine-guidance <domain> to continue refining.
```
Stop.

**On unrecognized input:** Re-display the gate options and re-prompt.

### Step 9: Confirm

Print:
```
[CREATE: Created / UPDATE: Updated] $DOMAINS_DIR/<domain>/specs/guidance.yaml

Next: Run /extract-ruleset <domain> to extract the CIVIL ruleset.
      Re-run /refine-guidance <domain> at any time to update guidance.
```

If `sub_rulesets:` in `guidance.yaml` is non-empty, append to the confirmation message:
```
(N sub-ruleset candidates: name1, name2, ...)
```
where N is the count and the names are the `name:` values from `sub_rulesets:`, comma-separated.

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [CREATED or UPDATED]
```

## Common Mistakes to Avoid

- Do not add `edge_cases:` to ruleset guidance template files in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` — they are domain-agnostic; `edge_cases:` belongs only in per-domain `guidance.yaml`
- Do not rewrite sections the user did not change — preserve exact wording of unchanged sections
- `source_template` is never updated after initial creation — it records which guidance template the file was originally created from
- Do not create or scaffold a domain folder here — if the domain doesn't exist, stop and refer to `/new-domain`
- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-index.yaml` is the sole source of doc signals
- `guidance.yaml` is created in Step 1 [CREATE], not deferred to Q&A — it always exists before Step 2 begins
