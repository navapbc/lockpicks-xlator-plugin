# Ruleset Command — Shared Content

Shared by `/extract-ruleset` and `/update-ruleset`. Read via directive at the start of each command.
Do not invoke this file directly.

---

## Pre-flight Checks 3–6

3. **Input docs present?**
   - `$DOMAINS_DIR/<domain>/input/policy_docs/` missing or empty → Print: no input documents found, suggest adding `.md` files. Stop.

4. **`<filename>` valid (if given)?**
   - If `<filename>` has no `.md` extension, append it automatically (e.g., `APA` → `APA.md`)
   - Verify `$DOMAINS_DIR/<domain>/input/policy_docs/<filename>` exists on disk
   - If not found: print file not found, list available `.md` files, then stop.

5. **Load `guidance.yaml`**

   Check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:

   **If it exists:**
   - Read the file
   - Print: `Using goal: <display_name> (source: <source_template>)`
   - Store its content for injection in Step 1

   **If it does not exist:**
   - Print: no `guidance.yaml` found for this domain, suggest running `/refine-guidance <domain>` then re-running. Stop.

6. **Multiple input docs + no `<filename>`?**
   - If `$DOMAINS_DIR/<domain>/input/policy_docs/` contains 2+ `.md` files and `<filename>` was **not** given:

   **If `$DOMAINS_DIR/<domain>/specs/input-index.yaml` exists**, read it and display a context-rich selection prompt:
     ```
     Multiple policy documents found. Consulting specs/input-index.yaml for context...

       1. input/policy_docs/<file1>.md
          Tags: [tag1, tag2, tag3]
          <section heading> — <summary>
          <section heading> — <summary>

       2. input/policy_docs/<file2>.md
          Tags: [tag1, tag2]
          <section heading> — <summary>
          ...

       a. All files (unified corpus)

     Process which file(s)? Enter a number, comma-separated numbers, or 'a' for all:
     ```
   Show only the file's top-level H1 sections from the index (level `#` entries) to keep the prompt scannable. Omit H2/H3 entries.
   Selecting comma-separated numbers (e.g., `1,3`) reads those files as a unified corpus for the rest of the run.

   **If `input-index.yaml` does not exist**, ask the user whether to generate it first:
     ```
     specs/input-index.yaml not found. An index enables faster and richer file selection with summaries and tags.
     Run /index-inputs <domain> now? [y (recommended) / n — continue without index]:
     ```
   - **y (or Enter):** Run `/index-inputs <domain>` now (creating `specs/input-index.yaml`), then re-display the selection prompt using the rich indexed format (same as the "exists" path above).
   - **n:** Fall back to the plain filename list:
     ```
     Multiple policy documents found in $DOMAINS_DIR/<domain>/input/policy_docs/:
       1. <file1>.md
       2. <file2>.md
       ...
       a. All files (unified corpus)

     Process which file? [1/2/.../a]:
     ```
     - Selecting `a` proceeds with all files as a unified corpus (unchanged behavior).
     - Selecting a number sets `<filename>` to that file for the rest of the run.

---

## Scoring Rubric

When writing `review:` blocks, score each rule and computed field on four dimensions using this table. Apply scores independently — a rule can have high fidelity and high complexity simultaneously.

| Score | extraction_fidelity | source_clarity | logic_complexity | policy_complexity |
|-------|---------------------|----------------|------------------|-------------------|
| 1 | Guessed; source is silent on this | Contradictory or absent from source | Single boolean or comparison | Plain everyday English |
| 2 | Inferred with low confidence | Vague; multiple reasonable interpretations | 2–3 conditions, no table lookups | Some jargon or implicit cross-refs |
| 3 | Reasonable translation with minor gaps | Reasonably clear with minor ambiguity | 4–6 conditions or 1 table lookup | Moderate legalese or defined terms |
| 4 | Strong match to source text | Clear but uses statutory defined terms | 7–9 conditions or 2+ table lookups | Dense statutory language or CFR references |
| 5 | Direct quote or explicit formula | Exact thresholds/formulas stated verbatim | 10+ conditions, nested booleans, multiple tables | Exceptions-to-exceptions, multi-CFR cross-refs |

**Special cases:**
- Structural allow rules (`when: "true"`) always score `logic_complexity: 1`. Score `extraction_fidelity` and `source_clarity` based on whether the policy explicitly states the default-allow logic or leaves it implicit.
- `notes:` is required for any item where any score is ≤ 2 or ≥ 4. For all-3 items, `notes:` may be omitted.

---

## CIVIL Reference

> **Do NOT read `tools/civil_schema.py`, `tools/transpile_to_rego.py`, or any other file in `tools/`
> before authoring any CIVIL YAML.** All syntax needed for authoring is here.

<!-- Last verified: 2026-03-04 -->

Quick reference for expression syntax and field-traceability conventions.
For full schema attribute tables, see [`core/civil-quickref.md`](civil-quickref.md).

---

### Expression Language

For `when:` conditions and `computed:` expressions:

- **Field access:** `Household.household_size`, `Applicant.age`
- **Constants:** `MIN_AGE`, `INCOME_MULTIPLIER`
- **Table lookup:** `table('gross_income_limits', Household.household_size).max_gross_monthly`
- **Boolean:** `&&`, `||`, `!`
- **Comparison:** `==`, `!=`, `<`, `<=`, `>`, `>=`
- **Arithmetic:** `+`, `-`, `*`, `/`
- **Functions:** `exists(field)`, `is_null(field)`, `between(value, min, max)`, `in(value, [a, b, c])`
- **`computed:` only:** `max(a, b)`, `min(a, b)` — computed field names as bare identifiers

**Multi-step formulas (CIVIL v2):** Use a `computed:` section for chains where each step depends on
the prior (e.g., a deduction chain). The `when:` clause references the final computed field name directly.

---

### `source:` vs `citations:` — They Are Distinct

- **`source:`** on a field, table, rule, or computed field identifies *where in the policy document
  the element was defined* — developer traceability. Example: `"7 CFR § 273.9(a) — Income and Deductions"`

- **`citations:`** inside an `add_reason` action contains the *legal authority shown to applicants
  in a denial explanation* — the statutory basis displayed in user-facing output.

A deny rule may have the same CFR section in both `source:` and `citations:` — that is expected
and not redundant. They serve different audiences.

---

## Shared Procedures

The following subroutines are referenced from the steps above. When a step says "Run **SP-Name**", find the matching section below and execute it.

### SP-Validate

```bash
python tools/validate_civil.py $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml
```

**On failure — retry loop (max 3 attempts):**
- Read the specific error message
- Identify the offending CIVIL section
- For more schema details, see [`core/civil-quickref.md`](civil-quickref.md)
- Re-extract or fix that section
- Re-validate

After 3 failed attempts, stop and print:
```
Validation failed after 3 attempts. Errors:
  <error list>
Fix manually, then re-run: python tools/validate_civil.py $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml
```

### SP-ComputeGraph

```bash
python tools/computation_graph.py $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml
```

On success the tool prints both output file paths. On failure, print:
```
Warning: computation graph could not be refreshed. The draft graph at $DOMAINS_DIR/<domain>/specs/<program>.graph.md may reflect pre-approval state.
```
Continue — the CIVIL file and manifests are already written. Do NOT stop the extraction.

### SP-GuidanceCapture

After the Human Review Gate is approved, synthesize candidate guidance items from the review session to improve future extractions.

**Multi-file context:** When called from a multi-file review gate (i.e., after reviewing a sub-module or main module in a multi-file extraction), each candidate guidance item must be prefixed with `[module: <name>]` where `<name>` is the name of the CIVIL module being reviewed at that gate (e.g., `[module: earned_income]`). This prefix appears in the candidate display and is preserved in the written `guidance.yaml` entry. When called from a single-file review gate, no prefix is added.

**Step 1 — Collect signals.**

Gather everything that occurred during the Human Review Gate:
- Items that were rejected and re-extracted (original vs. accepted: what changed, and why?)
- Items in the Uncertain bucket (fidelity ≤2 or source_clarity ≤2) — even if ultimately accepted
- Items in the Complex bucket that had `notes:` fields
- Any corrections the user provided to CIVIL expressions, values, or notes

If none of these signals are present (all items verified, no corrections, no notes), proceed silently to SP-CompleteExtraction — no synthesis needed.

**Step 2 — Synthesize candidates.**

From the collected signals, draft up to 5 candidate guidance items total across all sections. For each item:
- Assign it to the most appropriate section (`constraints`, `standards`, `guidance`, or `edge_cases`)
- Write it as a concise, actionable statement (1–2 sentences)
- In multi-file context, prepend `[module: <name>] ` to the statement text
- Check the corresponding section in `$DOMAINS_DIR/<domain>/specs/guidance.yaml` — if a semantically equivalent item already exists, skip this candidate

If zero candidates remain after deduplication, proceed silently to SP-CompleteExtraction.

**Step 3 — Offer to user.**

Print:
```
Based on your review, I have X suggestion(s) to add as ruleset guidance to improve future extractions.
Review them? [y/n]
```

- **n** → proceed to SP-CompleteExtraction.
- **Unrecognized input** → re-display and re-prompt.
- **y** → for each candidate in sequence, print:
  ```
  [<section>] "<candidate text>"
  Add this item? [y / n / edit]
  ```
  - **y** → record item for appending.
  - **n** → skip.
  - **edit** → print `Enter revised text (current: "<candidate text>"):` — accept the user's replacement text, then record it for appending.
  - **Unrecognized input** → re-display the per-item prompt and re-prompt.

**Step 4 — Write to file.**

After all candidates have been reviewed:
- Append each accepted item to its assigned section in `$DOMAINS_DIR/<domain>/specs/guidance.yaml`
- Update `generated_at` to today's date (write once after all appends, not after each individual item)
- Preserve `source_template` and all other sections verbatim

If 1 or more items were added, print (use "item" for N=1, "items" for N>1):
```
Updated guidance.yaml (+1 item)
Updated guidance.yaml (+3 items)
```

Then proceed to SP-CompleteExtraction.

> **Note:** Items added by SP-GuidanceCapture are indistinguishable from items created by `/refine-guidance`. A subsequent run of `/refine-guidance <domain>` (UPDATE mode) will present them as existing content, allow refinement, and preserve them if not changed.

### SP-CompleteExtraction

**Extraction complete.**

If `<filename>` was given and other `.md` files exist in `$DOMAINS_DIR/<domain>/input/policy_docs/` that were not processed, print:
```
Note: this domain has other policy docs not included in this run:
  - <other_file>.md
  ...
To extract from all files as a unified corpus, run without specifying a filename.
```

```
Next step: `/create-tests <domain>`
```

---

### SP-TagOutputs

**When to run:** At the end of the Human Review Gate, after the user has approved the translation. Run SP-TagOutputs before SP-GuidanceCapture.

**Applies to Catala backends only.** Rego backends surface all computed fields automatically via `decision.computed` — SP-TagOutputs has no effect on Rego transpilation.

**Steps:**

1. Read the `computed:` section of `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`.
2. If there are no `computed:` fields, print: "No computed fields found — skipping output selection." and stop.
3. Identify fields **ineligible** for `output` tagging: `type: bool` fields that have `expr:` (Catala uses `condition` syntax for these, which cannot be declared `output`). Exclude them from the ranked list.
4. Rank remaining eligible fields by explanatory importance:
   - **Tier 1 (highest):** Fields that directly feed a deny rule condition
   - **Tier 2:** Final pipeline output values (e.g., `countable_earned_income`, `income_limit`)
   - **Tier 3:** Major pipeline stage milestones (e.g., `after_federal_exclusions`, `after_student`)
   - **Tier 4:** Sub-steps within a single exclusion stage
5. Display the ranked list with three pre-selection tiers:
   - **`[REQUIRED]`** — fields whose names appear in the main module's `invoke:` dot-access expressions (sub-module files only); locked, cannot be deselected.
   - **`[GUIDANCE]`** — fields whose names appear in the guidance output set (from `intermediate_variables.include_with_output` in `guidance.yaml`); pre-checked, user may uncheck.
   - *(unlabeled)* — the top 5–8 remaining eligible fields by rank; pre-checked, user may uncheck.
   - Fields already tagged `output` in the CIVIL YAML are always pre-selected regardless of rank.
   - `[REQUIRED]` fields appear first, then `[GUIDANCE]` fields, then unlabeled fields in rank order.
6. Ask: "These are the computed fields recommended to include to help explain the rule engine's output in the demo breakdown. Confirm, adjust, or add more?"
7. For each confirmed field, use a targeted Edit tool call to insert `  tags: [output]` immediately after the field's `  type:` line in the CIVIL YAML. Never remove or overwrite existing content.
8. **This procedure is append-only.** Tags are added, never removed. To remove a tag, edit the CIVIL YAML directly.

---

### SP-ResolveSubRulesets

**When to call:**
- `/extract-ruleset`: immediately after pre-flight Check 5 (load `guidance.yaml`)
- `/update-ruleset`: at the start of Step 0, after the naming-manifest divergence check

**Parameter contract:**
- Input: `guidance.yaml` (already loaded), `extraction-manifest.yaml` (loaded if exists), invocation context (`extract` or `update`)
- Output: an ordered **work-list** of `{file, name, action, bind_map, is_new}` entries, where `action` is one of `generate` or `reference`

**Logic:**

```
SP-ResolveSubRulesets

1. If sub_rulesets: absent or empty in guidance.yaml:
   → Output: [{file: $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml,
               name: <program>, action: generate, bind_map: {}, is_new: <bool>}]
   → Return immediately (single-file path; caller proceeds as today — no changes to existing behavior)

2. For each entry in sub_rulesets:
   a. Resolve expected civil_file path: $DOMAINS_DIR/<domain>/specs/<name>.civil.yaml
   b. Check if file exists on disk
   c. Check if file is listed in extraction-manifest.yaml sub_modules:

3. If context == 'update':
   For each sub_rulesets: entry whose name does NOT appear in extraction-manifest.yaml sub_modules::
     → Emit:
       "⚠️  New sub-ruleset candidates found in guidance.yaml that were not in the initial extraction:
             <names>.
        Run /extract-ruleset <domain> to generate them before running /update-ruleset."
     → Abort SP-ResolveSubRulesets (caller must stop — do not proceed with partial work-list)

4. Build binding confirmation table (extract context only):
   Skip entirely when context == 'update' — bindings are already resolved in the existing manifest.
   For each sub_rulesets: entry, derive bind: dict from bound_entities::
   - If sub-module has exactly one entity in its facts: section, derive {SubEntity: BoundEntity} for each entry in bound_entities:
   - If the mapping is ambiguous (sub-module entity count unknown): prompt "Map <SubModuleEntity> to which parent entity? <bound_entities>" for each ambiguous pair
   Show the full binding table before any prompts:

   Binding Confirmation:
   ─────────────────────────────────────────────────────────────────────────
     # │ Sub-Module       │ Sub Entity   → Parent Entity
     1 │ earned_income    │ Household    → ClientData
     2 │ earned_income    │ Household    → DOLRecord
   ─────────────────────────────────────────────────────────────────────────
   Confirm bindings? [y / e <N> to edit row N]:

   On [e N]: prompt "Row N — Sub entity: <current> → Parent entity: <current>. Enter new parent entity name:". Update and re-display the table. Loop until [y].

5. For each sub-module in sub_rulesets: order where file exists on disk:
   Show first 10 lines of existing file + "Last modified: <date>"
   Prompt:
     File exists: $DOMAINS_DIR/<domain>/specs/<name>.civil.yaml
     [r] Regenerate (overwrite)  [s] Skip — reference as-is

   If [s]: record action: reference for this entry; the file is not regenerated; manifest will record referenced: true.
   If [r]: record action: generate.

   For new files (not on disk): action: generate automatically.

6. Output work-list in dependency order:
   [sub-modules in sub_rulesets: order] → [main module last]
   For 'reference' entries: included in work-list with action: reference; caller skips generation and SP-Validate for this entry; caller still writes manifest entry with referenced: true.
```

**Work-list entry format:**
```
{
  file:      "$DOMAINS_DIR/<domain>/specs/<name>.civil.yaml",
  name:      "<name>",                   # module name (e.g., "earned_income")
  action:    "generate" | "reference",
  bind_map:  { "<SubEntity>": "<ParentEntity>", ... },   # empty {} for main module
  is_sub:    true | false,               # false for the main module entry
  is_new:    true | false                # false if file already exists
}
```

**Callers must treat a single-entry work-list (empty sub_rulesets:) identically to today's single-file path.** SP-ResolveSubRulesets's step 1 fast-return guarantees the output is compatible with existing single-file logic.

---

### SP-OrchestrationFilter

**When to call:**
- `/extract-ruleset`: after Step 2 (Identify CIVIL Components) has produced the candidate component map, before advancing to Step 3b (Name Inventory)
- `/update-ruleset`: after Step 5 (Re-extract Affected Sections) identifies new rule components, before Step 6 (Merge)

**In multi-file extractions:** SP-OrchestrationFilter runs independently per `generate` entry in the work-list.

**Input:** The candidate rule component list produced by component identification. For each candidate: the component description and the policy text excerpt it was derived from.

**Procedure:**

```
For each candidate rule component C in the component list:

  Evaluate: Does C contain ANY of the following?

  [O1] Conditional control flow that SEQUENCES other rules or sub-modules
       (e.g., "if Step A passes, then proceed to Step B";
        "after income verification, run the asset test")

  [O2] Data loading, source selection, or retry logic
       (e.g., "if DOL record not found, fall back to client-reported data";
        "use the most recently submitted form")

  [O3] Call routing between modules beyond invoke: sub-ruleset binding
       (e.g., "route to the AK DOH income calculator";
        "select which calculation module to invoke based on application state")

  If ANY of O1–O3 match:
    → Remove C from the generate list
    → Add to flagged_components list with: {name, o_code: "O1/O2/O3", reason, policy_source}

After processing all components:
  If flagged_components is empty:
    Continue silently to the next step.

  If flagged_components is non-empty:
    Print: "Orchestration concerns flagged (excluded from CIVIL):"
    | Component Name | Concern | Policy Source |
    | -------------- | ------- | ------------- |
    | <name>         | <O-code>: <reason> | <policy_source> |
    ...
    Ask: "Do any of these belong in CIVIL after all? Enter names to re-include, or press Enter to confirm exclusions:"

    If user re-includes component C:
      → Add C back to the generate list
      → Mark C with re_include_note: "[O-code]: <reason>. Included by author decision."
      → When C is emitted into the CIVIL file, prepend a YAML comment to the rule:
        # NOTE: Reviewed for orchestration leakage ([O-code]). Included by author decision.
        # Concern: <reason>. Ensure application code does not duplicate this logic.
```

**Examples of orchestration leakage to reject (O1–O3):**
- "If the claimant has not yet submitted Form B, defer eligibility determination" → O1: sequencing
- "Use the most recent of the applicant's reported income or the DOL lookup result" → O2: source selection
- "If the income module returns an error, re-run with default values" → O2: retry logic
- "Route to the AK income calculator for residents, or the federal calculator otherwise" → O3: call routing

**Examples of valid CIVIL rules to keep:**
- "Deny if gross income exceeds the gross limit for the household size" → pure policy decision
- "Deny if the applicant is not a US resident" → pure eligibility condition
- "Computed: adjusted income = gross income - standard deduction - earned income deduction" → pure calculation

---

### SP-MaintainabilityReview

**When to call:**
- `/extract-ruleset`: new Step 4b — after Step 4 (Draft CIVIL Module), before Step 5 (Write Extraction Manifest) / Step 6 (Validate)
- `/update-ruleset`: new Step 6b — after Step 6 (Merge into Existing CIVIL), before Step 7 (Update Manifest)

**In multi-file extractions:** SP-MaintainabilityReview runs independently per `generate` entry in the work-list, same as SP-TagOutputs and SP-GuidanceCapture.

**In `/update-ruleset` context:** SP-MaintainabilityReview checks only rules and computed fields that were added or modified in the current update (identified in Step 4: Identify Affected CIVIL Sections). It does not re-check unchanged rules.

**Input:** The drafted/merged CIVIL module file (path). Also available: the `workflow_stages:` defined in `guidance.yaml` for the domain (for context on expected stage names).

**Procedure:**

```
Run the following checklist against the CIVIL file:

CHECKLIST:

[M1] group: coverage (non-blocking)
     PASS: Every rule entry has group: set to a defined workflow stage name
           (or workflow_stages is empty, in which case this check is advisory only)
     FAIL: List rules with missing or invalid group:

[M2] computed: vs rules: separation (non-blocking)
     PASS: No computed: field contains type: deny/allow eligibility conditions
           — computed: fields calculate values only; deny/allow logic belongs in rules: only
     FAIL: List computed: fields that appear to encode eligibility decisions rather than values

[M3] Orchestration-free rules (non-blocking)
     PASS: No rule when: clause references external system concepts
           (API names, DB field names, service identifiers, process state variables)
     FAIL: List rules with likely external-system references in their when: clause

[M4] mutex_group: coverage (non-blocking)
     PASS: Rules that represent competing alternatives (same policy test, different threshold branches)
           have mutex_group: set
     FAIL: List pairs of rules that appear mutually exclusive but lack mutex_group:

[M5] Priority uniqueness within mutex_group (BLOCKING)
     PASS: All rules sharing a mutex_group: have unique priority: values
     FAIL: List mutex_groups with duplicate priorities

For each FAILING item:
  1. Describe the issue(s) found
  2. Show the corrected YAML snippet
  3. Apply the fix directly to the CIVIL file (in-place edit without asking for confirmation)
  4. Re-check the item
     — If re-check fails again: show the conflict and stop for manual resolution before continuing

After all items are checked and fixes applied:
  Print the summary table:
  "Maintainability Self-Review complete."
  | Check | Status         | Items Fixed           |
  | M1    | PASS           | 3 rules annotated     |
  | M2    | PASS           | —                     |
  | M3    | WARN (override)| user override applied |
  | M4    | PASS           | 1 pair annotated      |
  | M5    | PASS           | —                     |
```

**Non-blocking items (M1–M4):**
The user may override a flagged item by typing its check ID (e.g., `M3`). Overrides are logged as a YAML comment on the affected rule:
```yaml
# M3 override: external system reference retained by author decision.
```

**Blocking item (M5):**
If M5 fails and the auto-fix cannot resolve the priority conflict (e.g., two rules with identical priorities in the same mutex_group where the correct priority cannot be determined automatically), SP-MaintainabilityReview stops and displays:
```
Blocking check M5 failed. Please resolve priority conflicts before proceeding.
  mutex_group '<name>': rules <id1> and <id2> both have priority <N>.
  Assign unique priorities, then re-run SP-MaintainabilityReview.
```
Do not advance to SP-Validate until M5 passes.

---

## Common Mistakes to Avoid

- **Don't forget `default eligible := false`** — OPA boolean rules are undefined (not false) when conditions don't match; the transpiler handles this automatically for all `bool` fields in `decisions:` and `computed:`
- **Cite the actual CFR/USC section** for each rule, not just "Program Policy Manual"
- **Use `optional: true`** for fact fields that may not always be provided (e.g., `earned_income`, `shelter_costs`)
- **Distinguish earned vs. unearned income** if any deduction applies only to earned income
- **Use `computed:` for multi-step formulas** — don't reference undefined identifiers in `when:` clauses; if a value needs multiple steps to compute, define it in `computed:` and reference it by name
- **Don't use `git diff` alone for change detection** — also run `git status` to catch untracked new files not yet committed
- **Always update the manifest after extraction** — stale git SHAs in `extraction-manifest.yaml` will cause UPDATE mode to miss real changes on the next run
