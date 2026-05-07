---
name: create-skeleton
description: Build Computation Skeleton for a Domain
---

# Build Computation Skeleton for a Domain

Extract doc signals from `input-sections.yaml` and merge proposals into the four guidance sections of `guidance/prompt-context.yaml`, then build and confirm the computation skeleton. Writes `guidance/skeleton.yaml` and updates `guidance/variables.yaml` and `guidance/prompt-context.yaml`.

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

4. **`input-sections.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/policy_facets/input-sections.yaml`
   - ABSENT → Print:
     :::error
     Input sections not found: $DOMAINS_DIR/<domain>/policy_facets/input-sections.yaml
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
Current guidance: <display_name> (source: <source_template>)
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

Read `$DOMAINS_DIR/<domain>/policy_facets/input-sections.yaml`.
Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — the sections index is the sole source of doc signals.

Extract the following signals (hold in memory for Step 3):

- **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
- **Section headings** — collect all `heading:` values; reveals statutory structure (e.g., income tests, deduction chains)
- **File summaries** — collect all `summary:` values; reveals program scope and terminology
- **Computation hints** — collect all `computations:` entries from sections that have the field; trace variable chains (a variable that is the last item in one entry's `variables` list and appears earlier in another entry's `variables` list is an intermediate computed variable); collect `expr_hint` values keyed by their output variable (last item in `variables`). If the index has no `computations:` entries, skip this signal.

For each of the four guidance sections (`constraints`, `standards`, `guidance`, `edge_cases`), generate proposed additions grounded in these index signals. Use computation hints to enrich `guidance` and `standards` proposals with concrete variable names and formula patterns (e.g., "The CIVIL ruleset should define `earned_income_deduction` as a `computed:` field equal to `earned_income * 0.20`").

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

- **`guidance/variables.yaml`** — `input_variables`, `intermediate_variables`, `output_variables` categories provide structure and group names
- **Step 2 signals (in-memory)** — topic tags, section headings, and file summaries enrich variable names; computation hints provide concrete variable names (prefer these over generic `examples` from the guidance template) and `expr_hint` values (show as `≈ <expr_hint>` when available, `= ?` when not inferable)

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

*[category name — category description]:*
- `[variable]` = [expression hint, or `= ?` if not inferable]
- ...

[repeat for each intermediate_variables category]
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

### Step 4: Write computation skeleton

Write to the `guidance/` folder:

1. **Write `guidance/skeleton.yaml`** — schema:
   ```yaml
   skeleton:
     inputs: [<flat list of confirmed input variable names>]
     outputs: [<flat list of confirmed output variable names>]
     computations:
       - category: <category_name>
         variables: [<variable1>, <variable2>, ...]
         exprs:
           <variable>: "<expr_hint>"
           # (only variables with non-null expr_hints; = ? variables are omitted)
     flow_diagram: |
       # (ASCII computation flow diagram)
   ```

2. **Update `guidance/variables.yaml`** — update these sections in place, preserving all other fields:
   - **`input_variables`**: for each category, rewrite `examples:` with the confirmed variable names from the skeleton display, in display order.
   - **`output_variables`**: rewrite `primary` and `secondary_decisions` with confirmed output variable names and types.
   - **`intermediate_variables`**: for each category, rewrite `examples:` with confirmed names; write `computations:` list (one entry per variable with a non-null expr hint). Variables shown as `= ?` are omitted from `computations:`.
   - **Table lookup format:** `expr:` uses `table('table_name', key_var).value_col` — do **not** use bracket subscript notation (`table_name[key]`).

3. **Update `guidance/prompt-context.yaml`** is not written in Step 4 — Step 2 already wrote it. Do not touch it here.

Print:
:::important
$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/variables.yaml [UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml [UPDATED]
:::

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
$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml       [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/variables.yaml      [UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml [UPDATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-sections.yaml` is the sole source of doc signals
- Do not rewrite sections the user did not change — preserve exact wording of unchanged items; only append new proposals in Step 2
- Do not write `generated_at` — git tracks version history
- Variables shown as `= ?` in the skeleton are omitted from `computations:` entries — only variables with actual `expr_hint` values get a `computations:` entry
- In UPDATE mode "accept", exit without writing — do not overwrite any existing content
- Step 2 runs in both CREATE and UPDATE mode (when `[b] replace` is selected or the full flow runs) — do not skip it even when guidance sections already have content; deduplication prevents double-adding
- Show the step checklist after EVERY step (4 steps total) — do not skip it
- When `[c] revise` is selected in UPDATE mode, skip Steps 1–3 and go directly to the Step 4 confirm/adjust loop displaying the existing skeleton — do not re-run Step 2 extraction
- Step 2 writes `prompt-context.yaml`; Step 4 writes `skeleton.yaml` and `variables.yaml` — do not conflate them
- `intermediate_variables.categories` in `variables.yaml` must be updated with the new category structure from the skeleton — if categories were empty before, populate them; if they existed, rewrite `examples:` with confirmed names
