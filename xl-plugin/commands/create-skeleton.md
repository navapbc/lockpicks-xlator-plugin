# Build Computation Skeleton for a Domain

Extract doc signals from `input-index.yaml` and merge proposals into the four guidance sections of `guidance.yaml`, then build and confirm the computation skeleton. Then, writes the `skeleton:` key and updates the variable sections.

## Input

```
/create-skeleton <domain>
```

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

3. **`guidance.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`
   - ABSENT → Print:
     :::error
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:declare-target-ruleset <domain> first.
     :::
     Then stop.

4. **`input-index.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     :::error
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /xl:index-inputs <domain> first.
     :::
     Then stop.

## Mode Detection

After pre-flight, check whether the `skeleton:` key already exists in `guidance.yaml`:

- **Present** → **UPDATE mode**. Display existing skeleton summary and offer:
  :::user_input
  Skeleton already exists: <N> computations across <M> categories (confirmed: <confirmed_at>)
  a. accept — keep as-is and exit
  b. replace — re-run full Step 2+3 flow, overwrite skeleton
  c. revise — show existing skeleton for editing
  :::
  - `a` → Print the Step 1 state summary (same format as Step 1 below) and exit. Suggest next step: `/xl:create-ruleset-groups <domain>`. Do not write anything.
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

Read `$DOMAINS_DIR/<domain>/specs/guidance.yaml`. Print a summary:

```
File: $DOMAINS_DIR/<domain>/specs/guidance.yaml
Current guidance: <display_name> (source: <source_template>, updated: <generated_at>)
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: none
```

If `skeleton:` already exists in `guidance.yaml` (this occurs when `c. revise` was selected in UPDATE mode), show instead:

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

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`.
Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — the index is the sole source of doc signals.

Extract the following signals (hold in memory for Step 3):

- **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
- **Section headings** — collect all `heading:` values; reveals statutory structure (e.g., income tests, deduction chains)
- **File summaries** — collect all `summary:` values; reveals program scope and terminology
- **Computation hints** — collect all `computations:` entries from sections that have the field; trace variable chains (a variable that is the last item in one entry's `variables` list and appears earlier in another entry's `variables` list is an intermediate computed variable); collect `expr_hint` values keyed by their output variable (last item in `variables`). If the index has no `computations:` entries, skip this signal.

For each of the four guidance sections (`constraints`, `standards`, `guidance`, `edge_cases`), generate proposed additions grounded in these index signals. Use computation hints to enrich `guidance` and `standards` proposals with concrete variable names and formula patterns (e.g., "The CIVIL ruleset should define `earned_income_deduction` as a `computed:` field equal to `earned_income * 0.20`").

Merge the doc-derived proposals into `$DOMAINS_DIR/<domain>/specs/guidance.yaml` immediately:
- For each of the four sections, append the proposed items into the section's current list
- Deduplicate: do not add items that are substantively identical to existing items
- Write the updated file to disk

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

- **`guidance.yaml`** — `input_variables`, `intermediate_variables`, `output_variables` categories provide structure and group names
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

Write to `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:

1. **Write `skeleton:` key** — insert as a top-level key after `scope:` (before `constraints:`). Schema:
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

2. **Update `input_variables`** — for each `input_variables` category, rewrite `examples:` with the confirmed variable names from the skeleton display, in display order. Replace any generic placeholder names.

3. **Update `output_variables`** — rewrite `output_variables.primary` and `output_variables.secondary_decisions` with confirmed output variable names and types from the skeleton display.

4. **Update `intermediate_variables`** — for each intermediate variable category:
   - Rewrite `examples:` with confirmed variable names from the skeleton display, in display order. Replace any generic placeholder names.
   - Write a `computations:` list — one entry per variable that has a non-null expr hint (shown as `≈ <expr>` in the skeleton display). Each entry has `name:` (the variable name) and `expr:` (the expr hint string). Variables shown as `= ?` are omitted from `computations:`. Write entries in display order.

Print:
:::important
$DOMAINS_DIR/<domain>/specs/guidance.yaml [UPDATED]
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
Next: Run /xl:create-ruleset-groups <domain> to propose ruleset groups.
:::

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [UPDATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-index.yaml` is the sole source of doc signals
- Do not rewrite sections the user did not change — preserve exact wording of unchanged items; only append new proposals in Step 2
- The `skeleton:` key is inserted after `scope:` (before `constraints:`), not at the end of the file
- Variables shown as `= ?` in the skeleton are omitted from `computations:` entries — only variables with actual `expr_hint` values get a `computations:` entry
- In UPDATE mode "accept", exit without writing — do not overwrite any existing content
- Step 2 runs in both CREATE and UPDATE mode (when `b. replace` is selected or the full flow runs) — do not skip it even when guidance sections already have content; deduplication prevents double-adding
- Show the step checklist after EVERY step (4 steps total) — do not skip it
- When `c. revise` is selected in UPDATE mode, skip Steps 1–3 and go directly to the Step 4 confirm/adjust loop displaying the existing skeleton — do not re-run Step 2 extraction
- The `skeleton:` key insertion position is after `scope:` and before `constraints:` — preserve all existing top-level key ordering for other keys
- `intermediate_variables.categories` must be updated with the new category structure from the skeleton — if categories were empty before, populate them; if they existed, rewrite `examples:` with confirmed names
