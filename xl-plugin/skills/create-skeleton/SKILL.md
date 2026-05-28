---
name: create-skeleton
description: Build Computation Skeleton for a Domain
---

# Build Computation Skeleton for a Domain

Extract doc signals from the per-file files under `policy_facets/computations/` and merge proposals into the four guidance sections of `guidance/prompt-context.yaml`, then build and confirm the computation skeleton. Writes `guidance/skeleton.yaml` (computation structure including intermediate variables) plus three descriptive guidance files: `guidance/output-variables.yaml`, `guidance/input-variables.yaml`, and `guidance/constants-and-tables.yaml`. Structural variable data lives in `specs/naming-manifest.yaml`.

## Input

```
/create-skeleton <domain>
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
     :::
     Then stop.

3. **`guidance/prompt-context.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml`
   - ABSENT → Print:
     :::error
     guidance/prompt-context.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml
     Run /declare-target-ruleset <domain> first.
     :::
     Then stop.

4. **Per-file computations present?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - ABSENT or empty → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first.
     :::
     Then stop.

## Mode Detection

After pre-flight, check whether `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` exists:

- **Present** → **UPDATE mode**. Display existing skeleton summary and offer:
  :::user_input
  Skeleton already exists: <N> computations across <M> categories (confirmed: <confirmed_at>)
  [a] accept — keep as-is and exit
  [b] replace — re-run full Step 2+3 flow, overwrite skeleton
  [c] revise — show existing skeleton for editing
  :::
  - `a` → Print the Step 1 state summary (same format as Step 1 below) and exit. Suggest next step: `/create-ruleset-groups <domain>`. Do not write anything.
  - `b` → Run the full process below (Steps 1–4).
  - `c` → Skip Steps 1–3. Show the existing skeleton (same display format as Step 3). Enter the confirm/adjust loop at Step 4.

- **Absent** → **CREATE mode**. Run the full process below (Steps 1–4).

---

## Process

**This command has 4 steps.** Show the step checklist after each step completion.

Step checklist format (show at end of each step, checking off completed steps):
```
Steps:
  [✓] Step 1: Load current state
  [ ] Step 2: Extract doc signals and update guidance sections
  [ ] Step 3: Build computation skeleton
  [ ] Step 4: Write computation skeleton
```

---

### Step 1: Load current state

Read `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` and `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml`. Print a summary:

```
Folder: $DOMAINS_DIR/<domain>/specs/guidance/
Current guidance: <display_name>
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: none
```

If `skeleton.yaml` already exists in the `guidance/` folder (this occurs when `[c] revise` was selected in UPDATE mode), show instead:

```
Skeleton: <N> computations across <M> categories, confirmed <confirmed_at>
```

Then show the step checklist with Step 1 checked off:

:::progress
Steps:
  [✓] Step 1: Load current state
  [ ] Step 2: Extract doc signals and update guidance sections
  [ ] Step 3: Build computation skeleton
  [ ] Step 4: Write computation skeleton
:::

---

### Step 2: Extract doc signals and update guidance sections

Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML map. Read `data["sections"]` as the list of section blocks.
Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of doc signals.

Source-path mapping: a section appearing in `policy_facets/computations/<rel>.md.yaml` describes the source at `input/policy_docs/<rel>.md`. Strip the trailing `.yaml` from the per-file file's relative path under `policy_facets/computations/` and prefix with `input/policy_docs/` to reconstruct `path:`.

**`expr_hint:` parse rule** (uniform across consumer skills): when a computation carries `expr_hint:`, split on the first `=`; the LHS (whitespace-trimmed) is the snake_case **output name** for that computation, and the RHS is the expression. Tokenize the RHS for snake_case identifiers (skipping numeric literals, string literals, and built-in keywords like `if`, `else`, `and`, `or`, `not`, `min`, `max`, `sum`) — those identifiers are the **input names**. When `expr_hint:` is absent (descriptive-only computation), fall back to scanning `description:` prose for variable names mentioned in the source's terminology.

Extract the following signals (hold in memory for Step 3):

- **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
- **Section headings** — collect all `heading:` values; reveals statutory structure (e.g., income tests, deduction chains)
- **File summaries** — collect all `summary:` values; reveals program scope and terminology
- **Computation hints** — collect all `computations:` entries from sections that have the field; for each entry apply the `expr_hint:` parse rule above to recover (output name, input names). A variable that is the LHS output of one entry and appears as an RHS input of another entry is an **intermediate computed variable**. Collect `expr_hint:` RHS values keyed by their LHS output name (the bare expression for that computation, with the `<output> =` prefix stripped); collect `preconditions:` expressions keyed by their computation's output name. A computation with non-empty `preconditions:` is a **conditional computation** — when emitting it in Step 4's `skeleton.computations[].exprs:` map, prefer the form `"if <rendered preconditions> then <expr_hint RHS> else ?"` over a bare expression so the conditional gating is preserved into `/extract-ruleset`. The rendering rule for `preconditions:` is: top-level list joins with AND; `{all_of: [...]}` joins with AND; `{any_of: [...]}` joins with OR; nesting permitted. If no entry has `computations:`, skip this signal.
- **Stage membership** — collect each section's `stage:` value (when present); index every computation in that section under the section's `stage:`. Apply the same suffix-stripping normalization as `/create-ruleset-groups` (drop a trailing `_test` / `_check` / `_evaluation`) so stage identifiers match the canonical names that `/create-ruleset-groups` writes to `ruleset-groups.yaml`. The stage index drives Step 4's `skeleton.computations[].stage:` field — a computation whose source section has `stage: deductions` is categorized under `deductions`. This keeps `skeleton.computations[*].stage` consistent with `ruleset_groups[*].name` so that `/create-ruleset-modules`'s R21 stage-boundary check (now extended to require `stage:` agreement) doesn't disagree with skeleton categorization. If no section has `stage:`, skip this signal and fall back to existing name-pattern-based categorization unchanged.

For each of the four guidance sections (`constraints`, `standards`, `guidance`, `edge_cases`), generate proposed additions grounded in these index signals. Use computation hints to enrich `guidance` and `standards` proposals with concrete variable names and formula patterns. Phrase the proposals in Catala terms — name the scope, use Catala expression idioms, and reference the `definition <var> equals <expr>` form per `../../core/catala-authoring-quickref.md` (e.g., "The Catala scope should define `earned_income_deduction` via `definition earned_income_deduction equals earned_income * 20%`" — note `20%` rather than `0.20`, the preferred Catala decimal-as-percent form).

Merge the doc-derived proposals into `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` immediately:
- For each of the four sections (`constraints`, `standards`, `guidance`, `edge_cases`), append the proposed items into the section's current list
- Deduplicate: do not add items that are substantively identical to existing items
- Write the updated file to disk; preserve all other fields in `prompt-context.yaml` exactly

Print:
:::important
Updated guidance sections.
:::

Then show the step checklist with Steps 1–2 checked off:

:::progress
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [ ] Step 3: Build computation skeleton
  [ ] Step 4: Write computation skeleton
:::

---

### Step 3: Build computation skeleton

Build and display the skeleton using:

- **`guidance/input-variables.yaml`** — input categories provide structure and group names; **`guidance/output-variables.yaml`** — output entries with primary flag; **`specs/naming-manifest.yaml`** — structural variable data (names + types)
- **Step 2 signals (in-memory)** — topic tags, section headings, and file summaries enrich variable names; computation hints provide concrete variable names (prefer these over generic `examples` from the guidance template) and bare-expression values (the `expr_hint:` RHS with the `<output> =` prefix stripped — show as `≈ <expression>` when available, `= ?` when not inferable); **stage membership drives `stage:` assignment when present** — a computation whose source section has `stage:` adopts the (post-normalization) stage value as its category, overriding name-pattern-based categorization. Computations whose source sections lack `stage:` fall through to existing name-pattern categorization unchanged.

**Parallel-entity reuse signal.** Before listing intermediate variables in any stage, check `specs/naming-manifest.yaml`'s `inputs:` block for two or more entities with **mirrored field schemas** — i.e., the same field name appearing under different `<EntityName>` keys (e.g., `ClientStatement.gross_earned_income` AND `DOLRecord.gross_earned_income`). When this pattern is present:

- **Do not flatten the parallel runs into a single black-box step.** Listing only one aggregate variable (e.g., `adjusted_earned_income`) hides the reuse pattern and starves `/create-ruleset-modules`'s `reuse_across_entities` heuristic.
- **Expand the stage's `variables:` list to show the parallel computations per entity**, using `<entity_prefix>_<output>` naming (snake_case of the entity name as prefix). Example: a stage that applies the same exclusion chain to `ClientStatement` and `DOLRecord` should list both `client_adjusted_earned_income` and `dol_adjusted_earned_income` (plus per-entity intermediate steps when material), not a single merged `adjusted_earned_income`.
- **Both runs share the same `expr_hint:` shape**, differing only by entity-prefixed input names — record each entity's run with its own `exprs:` entry so the parallelism is explicit in the YAML.

This expansion is what enables `/create-ruleset-modules`'s `reuse_across_entities` heuristic (priority 1) to detect the shared sub-module candidate. If the upstream `/suggest-target-ruleset` correctly applied Entity Inference Rule 0 (cross-source reuse pattern), the parallel entities are already in `naming-manifest.yaml`; this step's job is to make them visible in the skeleton.

Display format:

:::detail
**Computation skeleton for <display_name>:**

**Inputs:**
- [variable names from input_variables categories, enriched with domain-specific names]
- ...

**Output:**
- [primary output field] ([type])
- [secondary_decisions fields] ([type])

**Computed:** *(how to get the Output from the Inputs)*

*[stage name — stage description]:*
- `[variable]` = [expression hint, or `= ?` if not inferable]
- ...

[repeat for each intermediate_variables stage]
:::

Include an ASCII computation flow diagram.

Then show the step checklist with Steps 1–3 checked off:

:::progress
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [✓] Step 3: Build computation skeleton
  [ ] Step 4: Write computation skeleton
:::

---

### Step 4: Write computation skeleton + descriptive guidance files

Write four files into `$DOMAINS_DIR/<domain>/specs/guidance/`:

1. **Write `guidance/skeleton.yaml`** — schema:
   ```yaml
   skeleton:
     inputs: [<flat list of confirmed input variable names>]
     outputs: [<flat list of confirmed output variable names>]
     computations:
       - stage: <stage_name>
         variables: [<variable1>, <variable2>, ...]    # intermediate variables in this stage
         exprs:
           <variable>: "<expression>"
           # The key is the computation's output variable (the LHS of expr_hint:);
           # the value is the bare expression (the RHS of expr_hint: with the
           # `<output> =` prefix stripped). Only variables with non-null
           # expressions are listed; `= ?` variables are omitted.
     flow_diagram: |
       # (ASCII computation flow diagram)
   ```

   **Intermediate variables live here, not in a separate file.** Their structure (which variables are computed, their expression hints, their stage grouping) IS the computation skeleton. There is no `guidance/intermediate-variables.yaml`.

2. **Write `guidance/output-variables.yaml`** — flat keyed by name, mirroring `specs/naming-manifest.yaml`'s `outputs:` shape (the key IS the manifest reference):
   ```yaml
   <output_name>:
     description: "<analyst-curated description>"
     examples: ["<sample value 1>", "<sample value 2>"]   # optional; sample values, not synonym names
     primary: true | false             # exactly one entry has primary: true per ruleset
   # repeat for each output (primary + secondaries)
   ```
   `examples:` carries **sample values** (concrete instance data), NOT synonym names. Synonyms live in `naming-manifest.yaml`'s `synonyms:` row list.

   **Determining `primary:`.** Treat the **first** entry in `specs/naming-manifest.yaml`'s `outputs:` block as `primary: true` and every other entry as `primary: false`. The manifest preserves declaration order from `suggested_targets/<ruleset>.yaml`, where `/suggest-target-ruleset` lists the candidate's main decision first. Do not re-read the suggestion file and do not infer primary from semantics — the order in `naming-manifest.yaml` is the contract.

3. **Write `guidance/input-variables.yaml`** — input categories with descriptive metadata + per-category provenance:
   ```yaml
   categories:
     - category: <category_name>
       description: "<category description>"
       examples: ["<sample value 1>", "<sample value 2>"]   # optional; sample values
       fields:
         - name_ref: <field_name>      # references inputs.<Entity>.<field_name> in naming-manifest.yaml
         - name_ref: <field_name>
       # optional per-category provenance:
       source_file: "<rel>.md"
       source_section: "<heading or §-citation>"
       exact_phrase: "<verbatim phrase>"
   ```

4. **Write `guidance/constants-and-tables.yaml`** — non-variable named tables/constants:
   ```yaml
   constants_and_tables:
     - name: <constant_or_table_name>
       description: "<analyst-readable description>"
       source_file: "input/policy_docs/<rel>.md"
       source_section: "<heading or §-citation>"
   ```
   Skill extracts candidate constants/tables from per-file YAML and writes a draft. Analyst refines.

   **`source_file:` and `source_section:` are required on every entry.** `source_file:` is the per-file YAML file's reconstituted source path (`policy_facets/computations/<rel>.md.yaml` → `input/policy_docs/<rel>.md`), and `source_section:` is the surfacing section's `heading:` value. When the same constant/table is surfaced from multiple per-file sections, point both fields at the section that principally defines the value (typically the first occurrence or the section that introduces it as a named concept). Do not emit an entry without both fields — drop the candidate instead and log a warning so the analyst can confirm the source manually.

5. **Update `guidance/prompt-context.yaml`** is not written in Step 4 — Step 2 already wrote it. Do not touch it here.

**Re-run preservation:** when any of the four files already exists with analyst edits (descriptions, examples, names), preserve the existing content unchanged — only fill in fields the analyst left blank or empty. Same preserve-non-null discipline as `/extract-ruleset` Step 7.

Print:
:::important
$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/input-variables.yaml [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml [CREATED]
:::

Then record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

```bash
xlator record-tier-manifest <domain> --tier guidance
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to the step checklist or `:::next_step`.

Then show the final step checklist (all steps checked):

:::progress
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [✓] Step 3: Build computation skeleton
  [✓] Step 4: Write computation skeleton
:::

Then suggest the next step:

:::next_step
Next: Run /create-ruleset-groups <domain> to propose ruleset groups.
:::

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml             [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml     [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/input-variables.yaml      [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml       [UPDATED in Step 2]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `policy_facets/computations/` is the sole source of doc signals
- Do not rewrite sections the user did not change — preserve exact wording of unchanged items; only append new proposals in Step 2
- Do not write `generated_at` — git tracks version history
- Variables shown as `= ?` in the skeleton are omitted from `computations:` entries — only variables with a non-null bare expression (derived from the `expr_hint:` RHS) get a `computations:` entry
- **Do not flatten parallel-entity runs into a single black-box variable** — when `naming-manifest.yaml` declares two or more entities with mirrored field schemas (e.g., `ClientStatement.gross_earned_income` AND `DOLRecord.gross_earned_income`), the skeleton's stage `variables:` list MUST show the per-entity runs (e.g., `client_adjusted_earned_income`, `dol_adjusted_earned_income`), not a single merged `adjusted_earned_income`. Flattening hides the reuse signal that `/create-ruleset-modules`'s `reuse_across_entities` heuristic needs.
- In UPDATE mode "accept", exit without writing — do not overwrite any existing content
- Step 2 runs in both CREATE and UPDATE mode (when `[b] replace` is selected or the full flow runs) — do not skip it even when guidance sections already have content; deduplication prevents double-adding
- Show the step checklist after EVERY step (4 steps total) — do not skip it
- When `[c] revise` is selected in UPDATE mode, skip Steps 1–3 and go directly to the Step 4 confirm/adjust loop displaying the existing skeleton — do not re-run Step 2 extraction
- Step 2 writes `prompt-context.yaml`; Step 4 writes `skeleton.yaml`, `output-variables.yaml`, `input-variables.yaml`, and `constants-and-tables.yaml` — do not conflate them
- **`output-variables.yaml`'s `examples:` carries sample values, not synonym names** — synonyms are tracked in `naming-manifest.yaml`'s `synonyms:` row list. Do not duplicate.
- **`output-variables.yaml`'s `primary:` flag is derived from declaration order, not inferred** — the first output in `naming-manifest.yaml`'s `outputs:` block is `primary: true`; every other is `primary: false`. Do not re-evaluate which output is "most important" by reading descriptions or policy text.
- Re-runs preserve analyst edits — only fill in fields the analyst left blank. Match `/extract-ruleset` Step 7's preserve-non-null discipline.
- **When a section has an explicit `stage:` value, that stage wins over name-pattern categorization** — do not override an explicit doc signal with a heuristic guess. Apply the same suffix-stripping normalization as `/create-ruleset-groups` so stages match `ruleset_groups[*].name` exactly
- **Do not write `stage:` or modify it** — `stage:` is single-owner; only `/extract-computations` writes the field. This skill reads it
