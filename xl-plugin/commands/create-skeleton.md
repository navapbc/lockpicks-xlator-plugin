# Build Computation Skeleton for a Domain

Extract doc signals from `input-index.yaml` and merge proposals into the four guidance sections of `guidance.yaml`, then build and confirm the computation skeleton. On confirmation, writes the `skeleton:` key and updates the variable sections. This command runs Step 2 (doc-signal extraction) and Step 3 (skeleton building) from `/refine-guidance` as a single independently-callable unit.

## Input

```
/create-skeleton <domain>
```

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
     ```
     Then stop.

3. **`guidance.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`
   - ABSENT → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /declare-ruleset-io <domain> first.
     ```
     Then stop.

4. **`input-index.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     ```
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /index-inputs <domain> first.
     ```
     Then stop.

## Mode Detection

After pre-flight, check whether the `skeleton:` key already exists in `guidance.yaml`:

- **Present** → **UPDATE mode**. Display existing skeleton summary and offer:
  ```
  Skeleton already exists: <N> computations across <M> categories (confirmed: <confirmed_at>)
  a. accept — keep as-is and exit
  b. replace — re-run full Step 2+3 flow, overwrite skeleton
  c. revise — show existing skeleton for editing
  ```
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
  [ ] Step 4: Confirm and write
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

```
Steps:
  [✓] Step 1: Load current state
  [ ] Step 2: Extract doc signals and update guidance sections
  [ ] Step 3: Build computation skeleton
  [ ] Step 4: Confirm and write
```

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
```
Updated guidance sections.
```

Then show the step checklist with Steps 1–2 checked off:

```
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [ ] Step 3: Build computation skeleton
  [ ] Step 4: Confirm and write
```

---

### Step 3: Build computation skeleton

Build and display the skeleton using:

- **`guidance.yaml`** — `input_variables`, `intermediate_variables`, `output_variables` categories provide structure and group names
- **Step 2 signals (in-memory)** — topic tags, section headings, and file summaries enrich variable names; computation hints provide concrete variable names (prefer these over generic `examples` from the guidance template) and `expr_hint` values (show as `≈ <expr_hint>` when available, `= ?` when not inferable)

Display format:

```
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

---
[C]onfirm this computation skeleton, or describe what to add, remove, change, or rename.
```

Include an ASCII computation flow diagram only when the dependency graph is non-trivial (more than one path from inputs to output, 3+ steps). For simple linear chains, omit it.

Then show the step checklist with Steps 1–3 checked off:

```
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [✓] Step 3: Build computation skeleton
  [ ] Step 4: Confirm and write
```

---

### Step 4: Confirm and write

**On adjustment response** (user adds, removes, renames, or otherwise changes items): Update the skeleton in memory, re-display the full updated skeleton using the same format as Step 3, and re-ask:

```
[C]onfirm this computation skeleton, or describe what to add, remove, change, or rename.
```

Repeat until the user confirms.

**On unrecognized input:** Re-display the skeleton and re-prompt.

**On confirm** (user types "confirm", "yes", "looks good", "c", or presses Enter):

Write to `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:

1. **Write `skeleton:` key** — insert as a top-level key after `scope:` (before `constraints:`). Schema:
   ```yaml
   skeleton:
     confirmed_at: YYYY-MM-DD
     inputs: [<flat list of confirmed input variable names>]
     outputs: [<flat list of confirmed output variable names>]
     computations:
       - category: <category_name>
         variables: [<variable1>, <variable2>, ...]
         exprs:
           <variable>: "<expr_hint>"
           # (only variables with non-null expr_hints; = ? variables are omitted)
   ```

2. **Update `input_variables`** — for each `input_variables` category, rewrite `examples:` with the confirmed variable names from the skeleton display, in display order. Replace any generic placeholder names.

3. **Update `output_variables`** — rewrite `output_variables.primary` and `output_variables.secondary_decisions` with confirmed output variable names and types from the skeleton display.

4. **Update `intermediate_variables`** — for each intermediate variable category:
   - Rewrite `examples:` with confirmed variable names from the skeleton display, in display order. Replace any generic placeholder names.
   - Write a `computations:` list — one entry per variable that has a non-null expr hint (shown as `≈ <expr>` in the skeleton display). Each entry has `name:` (the variable name) and `expr:` (the expr hint string). Variables shown as `= ?` are omitted from `computations:`. Write entries in display order.

Print:
```
$DOMAINS_DIR/<domain>/specs/guidance.yaml [UPDATED]
```

Then show the final step checklist (all steps checked):

```
Steps:
  [✓] Step 1: Load current state
  [✓] Step 2: Extract doc signals and update guidance sections
  [✓] Step 3: Build computation skeleton
  [✓] Step 4: Confirm and write
```

Then suggest the next step:

```
Next: Run /create-ruleset-groups <domain> to propose workflow stages.
```

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
