# Ruleset Command — Shared Content

Shared by `/extract-ruleset`, `/update-ruleset`, and `/extract-sample-rules`. Read via directive at the start of each command.
Do not invoke this file directly.

---

## Pre-flight Checks 3–5

3. **Input docs present?**
   - `$DOMAINS_DIR/<domain>/input/policy_docs/` missing or empty → Print: no input documents found, suggest adding `.md` files. Stop.

4. **Load guidance files**

   Check for `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`:

   **If it exists:**
   - Read `guidance/metadata.yaml` — load `display_name`
   - Print: `Using goal: <display_name>`
   - Each calling skill loads only the additional guidance files it needs (see per-skill file lists). Missing optional files are silently treated as empty.

   **If `guidance/metadata.yaml` does not exist:**
   - Print: no guidance found for this domain, suggest running `/refine-guidance <domain>` then re-running. Stop.

5. **Multiple input docs?**
   - If `$DOMAINS_DIR/<domain>/input/policy_docs/` contains 2+ `.md` files:

   **If `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml` exists**, read it and display a context-rich selection prompt:
     ```
     Multiple policy documents found. Consulting policy_facets/input-index.yaml for context...

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
     policy_facets/input-index.yaml not found. An index enables faster and richer file selection with summaries and tags.
     Run /index-inputs <domain> now? [y (recommended) / n — continue without index]:
     ```
   - **y:** Run `/index-inputs <domain>` now (creating `policy_facets/input-index.yaml`), then re-display the selection prompt using the rich indexed format (same as the "exists" path above).
   - **n:** Proceed with all files as a unified corpus (unchanged behavior).

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

## Expression Reference

> **Do NOT read files under `tools/` before authoring Catala source.** All syntax needed for authoring is in [`core/catala-authoring-quickref.md`](catala-authoring-quickref.md).

<!-- Last verified: 2026-03-04 -->

Quick reference for expression syntax and field-traceability conventions.
For full Catala syntax, see [`core/catala-authoring-quickref.md`](catala-authoring-quickref.md) and [`core/catala-quickref.md`](catala-quickref.md).

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

**Multi-step formulas:** Use a `computed:` section for chains where each step depends on
the prior (e.g., a deduction chain). The `when:` clause references the final computed field name directly.

---

### `source:` vs `citations:` — They Are Distinct

- **`source:`** on a field, table, rule, or computed field identifies *where in the policy document
  the element was defined* — developer traceability. It is an object with optional `file:` (source-doc
  path relative to the domain root, e.g. `input/policy_docs/snap_eligibility.md`) and `section:`
  (citation + heading, e.g. `"7 CFR § 273.9(a) — Income and Deductions"`) subfields.

- **`citations:`** inside an `add_reason` action contains the *legal authority shown to applicants
  in a denial explanation* — the statutory basis displayed in user-facing output.

A deny rule may have the same CFR section in both `source:` and `citations:` — that is expected
and not redundant. They serve different audiences.

---

## Shared Procedures

The following subroutines are referenced from the steps above. When a step says "Run **SP-Name**", find the matching section below and execute it.

### SP-ComputeGraph

```bash
xlator graph <domain> <module>
```

On success the tool prints both output file paths. On failure, print:
```
Warning: computation graph could not be refreshed. The draft graph at $DOMAINS_DIR/<domain>/specs/<program>.graph.md may reflect pre-approval state.
```
Continue — the Catala module file and manifests are already written. Do NOT stop the extraction.

### SP-GuidanceCapture

After the Human Review Gate is approved, synthesize candidate guidance items from the review session to improve future extractions.

**Multi-file context:** When called from a multi-file review gate (i.e., after reviewing a sub-module or main module in a multi-file extraction), each candidate guidance item must be prefixed with `[module: <name>]` where `<name>` is the name of the module being reviewed at that gate (e.g., `[module: earned_income]`). This prefix appears in the candidate display and is preserved in the written `prompt-context.yaml` entry. When called from a single-file review gate, no prefix is added.

**Step 1 — Collect signals.**

Gather everything that occurred during the Human Review Gate:
- Items that were rejected and re-extracted (original vs. accepted: what changed, and why?)
- Items in the Uncertain bucket (fidelity ≤2 or source_clarity ≤2) — even if ultimately accepted
- Items in the Complex bucket that had `notes:` fields
- Any corrections the user provided to expressions, values, or notes

If none of these signals are present (all items verified, no corrections, no notes), proceed silently to SP-CompleteExtraction — no synthesis needed.

**Step 2 — Synthesize candidates.**

From the collected signals, draft up to 5 candidate guidance items total across all sections. For each item:
- Assign it to the most appropriate section (`constraints`, `standards`, `guidance`, or `edge_cases`)
- Write it as a concise, actionable statement (1–2 sentences)
- In multi-file context, prepend `[module: <name>] ` to the statement text
- Check the corresponding section in `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` — if a semantically equivalent item already exists, skip this candidate

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
- Append each accepted item to its assigned section in `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml`
- Preserve all other fields in `prompt-context.yaml` verbatim (do not modify `role:`, `scope:`, or other sections)
- Do not write `generated_at`

If 1 or more items were added, print (use "item" for N=1, "items" for N>1):
```
Updated guidance/prompt-context.yaml (+1 item)
Updated guidance/prompt-context.yaml (+3 items)
```

Then proceed to SP-CompleteExtraction.

> **Note:** Items added by SP-GuidanceCapture are indistinguishable from items created by `/refine-guidance`. A subsequent run of `/refine-guidance <domain>` (UPDATE mode) will present them as existing content, allow refinement, and preserve them if not changed.

### SP-CompleteExtraction

**Extraction complete.**

If the caller's in-scope source set selected only a subset of the available `.md` files in `$DOMAINS_DIR/<domain>/input/policy_docs/`, print:
```
Note: this domain has other policy docs not included in this run:
  - <other_file>.md
  ...
To extract from all files as a unified corpus, re-run and select all files at the multi-doc prompt.
```

---

### SP-TagOutputs

**When to run:** At the end of the Human Review Gate, after the user has approved the translation. Run SP-TagOutputs before SP-GuidanceCapture.

**Mechanism:** Promote selected `internal` variables in the Catala scope declaration to `output`. Catala's scope variable kinds (`input`, `internal`, `output`, `context`) determine what is returned to callers; promoting `internal` → `output` exposes the value to the demo breakdown, downstream `> Using` consumers, and `catala_eval` JSON output.

**Steps:**

1. Read `$DOMAINS_DIR/<domain>/specs/<program>.catala_en`. Parse the `declaration scope <ScopeName>:` block inside the `catala-metadata` fence under `## Declarations`.
2. Collect every `internal <name> content <type>` declaration (these are the candidates for output promotion). If the scope declares no `internal` variables, print: "No internal variables found — skipping output selection." and stop.
3. Identify variables **ineligible** for promotion: `internal <name> condition` declarations. Catala has no `output ... condition` form; condition variables are inherently internal. Exclude them from the ranked list.
4. Rank remaining eligible variables by explanatory importance:
   - **Tier 1 (highest):** Variables that directly feed a deny rule's `under condition` clause
   - **Tier 2:** Final pipeline output values (e.g., `countable_earned_income`, `income_limit`)
   - **Tier 3:** Major pipeline stage milestones (e.g., `after_federal_exclusions`, `after_student`)
   - **Tier 4:** Sub-steps within a single exclusion stage
5. Display the ranked list with three pre-selection tiers:
   - **`[REQUIRED]`** — variable names that appear in the main module's scope-call dot-access expressions (sub-module files only — e.g., if the main module reads `client_result.net_income` from a sub-module call, then `net_income` is locked); cannot be deselected.
   - **`[GUIDANCE]`** — variable names that appear in `guidance/include-with-output.yaml` (the flat list written earlier by `/tag-vars-to-include-with-output`); pre-checked, user may uncheck.
   - *(unlabeled)* — the top 5–8 remaining eligible variables by rank; pre-checked, user may uncheck.
   - Variables already declared `output` (e.g., from a prior SP-TagOutputs pass) are always pre-selected regardless of rank.
   - `[REQUIRED]` fields appear first, then `[GUIDANCE]` fields, then unlabeled fields in rank order.
6. Ask: "These are the computed fields recommended to include to help explain the rule engine's output in the demo breakdown. Confirm, adjust, or add more?"
7. For each confirmed variable, use a targeted Edit tool call to change the keyword `internal` to `output` on that variable's declaration line inside the `catala-metadata` fence. Match the exact line shape `  internal <name> content <type>` to avoid editing other declarations.
8. **This procedure is append-only.** Promotions are added (`internal` → `output`), never reverted. To revert, edit the `.catala_en` directly.
9. After all promotions land, re-run the clerk-loop (`xlator clerk-loop <domain> <module>`) to confirm typecheck + tests still pass — promoting a variable affects cross-module exports.

---

### SP-ResolveRulesetModules

**When to call:**
- `/extract-ruleset`: immediately after pre-flight Check 5 (load guidance files)
- `/update-ruleset`: at the start of Step 0, after the naming-manifest divergence check

**Parameter contract:**
- Input: `guidance/ruleset-modules.yaml` (loaded if exists; treated as empty if absent), `extraction-manifest.yaml` (loaded if exists), invocation context (`extract` or `update`)
- Output: an ordered **work-list** of `{file, name, action, bind_map, is_new}` entries, where `action` is one of `generate` or `reference`

**Logic:**

```
SP-ResolveRulesetModules

1. If guidance/ruleset-modules.yaml is absent or ruleset_modules: is empty:
   → Output: [{file: $DOMAINS_DIR/<domain>/specs/<program>.catala_en,
               name: <program>, action: generate, bind_map: {}, is_new: <bool>}]
   → Return immediately (single-file path; caller proceeds as today — no changes to existing behavior)

1b. Resolve main module name:
    Find the entry in ruleset_modules: where role: == 'main' (if any).
    If a role: main entry exists:
      main_name = that entry's name
      If <program> CLI arg was given AND differs from main_name:
        Print: "Note: Using declared main module name '<main_name>' from
                guidance/ruleset-modules.yaml (ignoring '<program>' argument)."
    Else:
      main_name = <program> CLI arg if given (backward compat path — R11).
      If no <program> arg, main_name is resolved later by the caller's Step 3
      (Derive Program Name) from policy text.

2. For each entry in ruleset_modules:
   a. Resolve expected catala_file path: $DOMAINS_DIR/<domain>/specs/<name>.catala_en
   b. Check if file exists on disk
   c. Check if file is listed in extraction-manifest.yaml sub_modules: (sub-module entries only;
      the role: main entry is not expected in sub_modules: — skip this check for it)

3. If context == 'update':
   For each ruleset_modules: entry WHERE role != 'main' (or role absent)
     whose name does NOT appear in extraction-manifest.yaml sub_modules::
     → Emit:
       "⚠️  New ruleset module candidates found in guidance/ruleset-modules.yaml that were not in the initial extraction:
             <names>.
        Run /extract-ruleset <domain> to generate them before running /update-ruleset."
     → Abort SP-ResolveRulesetModules (caller must stop — do not proceed with partial work-list)

4. Build binding confirmation table (extract context only):
   Skip entirely when context == 'update' — bindings are already resolved in the existing manifest.
   For each sub-module entry in ruleset_modules: (skip the role: main entry — it has no binding),
   derive bind: dict from bound_entities::
   - If sub-module has exactly one entity in its inputs: section, derive {SubEntity: BoundEntity} for each entry in bound_entities:
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

5. For each module in ruleset_modules: (including role: main) where file exists on disk:
   Show first 10 lines of existing file + "Last modified: <date>"
   Prompt:
     File exists: $DOMAINS_DIR/<domain>/specs/<name>.catala_en
     [r] Regenerate (overwrite)  [s] Skip — reference as-is

   If [s]: record action: reference for this entry; the file is not regenerated; manifest will record referenced: true.
   If [r]: record action: generate.

   For new files (not on disk): action: generate automatically.

6. Output work-list in topological order derived from depends_on: declarations:
   a. Build an in-degree map: for each module M, in_degree[M] = count of modules that list M in their depends_on:.
      Equivalently: for each entry E and each name D in E.depends_on:, increment in_degree[E] by the number
      of modules that D depends on indirectly — simpler: in_degree[M] = number of M's appears in other entries' depends_on:.
   b. Initialize queue with all modules whose in_degree == 0 (no modules depend on them).
   c. While queue is non-empty: emit the front module, remove it from the queue; for each module that listed
      the emitted module in its depends_on:, decrement their in_degree; enqueue any that reach 0.
   d. If any modules remain after the queue drains (cycle detected):
      → Abort with: "Cycle detected in depends_on: references among ruleset modules: <list of cycle node names>.
        Fix guidance/ruleset-modules.yaml depends_on: entries before running /extract-ruleset."

   Fallback (no depends_on: declared on any entry, or all depends_on: are empty lists):
   Output work-list in current convention order — sub-module entries in ruleset_modules: declaration order,
   then the role: main entry last.

   For 'reference' entries: included in work-list with action: reference; caller skips generation and
   the post-emission clerk-loop for this entry; caller still writes manifest entry with referenced: true.
```

**Work-list entry format:**
```
{
  file:      "$DOMAINS_DIR/<domain>/specs/<name>.catala_en",
  name:      "<name>",                   # module name (e.g., "earned_income")
  action:    "generate" | "reference",
  bind_map:  { "<SubEntity>": "<ParentEntity>", ... },   # empty {} for main module
  is_sub:    true | false,               # true for sub-module entries; false for the role: main entry
  is_new:    true | false                # false if file already exists
}
```

`is_sub` is consumed by SP-TagOutputs to determine `[REQUIRED]` field locking — it must be `false` for the main module entry and `true` for all sub-module entries.

**Callers must treat a single-entry work-list (empty ruleset_modules:) identically to today's single-file path.** SP-ResolveRulesetModules's step 1 fast-return guarantees the output is compatible with existing single-file logic.

---

### SP-OrchestrationFilter

**When to call:**
- `/extract-ruleset`: after Step 2 (Identify Components) has produced the candidate component map, before advancing to Step 3b (Name Inventory)
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

  [O3] Call routing between modules beyond invoke: ruleset module binding
       (e.g., "route to the AK DOH income calculator";
        "select which calculation module to invoke based on application state")

  If ANY of O1–O3 match:
    → Remove C from the generate list
    → Add to flagged_components list with: {name, o_code: "O1/O2/O3", reason, policy_source}

After processing all components:
  If flagged_components is empty:
    Continue silently to the next step.

  If flagged_components is non-empty:
    Print: "Orchestration concerns flagged (excluded from ruleset):"
    | Component Name | Concern | Policy Source |
    | -------------- | ------- | ------------- |
    | <name>         | <O-code>: <reason> | <policy_source> |
    ...
    Ask: "Do any of these belong in the ruleset after all? Enter names to re-include, or 'confirm' to confirm exclusions:"

    If user re-includes component C:
      → Add C back to the generate list
      → Mark C with re_include_note: "[O-code]: <reason>. Included by author decision."
      → When C is emitted into the Catala module file, prepend a YAML comment to the rule:
        # NOTE: Reviewed for orchestration leakage ([O-code]). Included by author decision.
        # Concern: <reason>. Ensure application code does not duplicate this logic.
```

**Examples of orchestration leakage to reject (O1–O3):**
- "If the claimant has not yet submitted Form B, defer eligibility determination" → O1: sequencing
- "Use the most recent of the applicant's reported income or the DOL lookup result" → O2: source selection
- "If the income module returns an error, re-run with default values" → O2: retry logic
- "Route to the AK income calculator for residents, or the federal calculator otherwise" → O3: call routing

**Examples of valid ruleset rules to keep:**
- "Deny if gross income exceeds the gross limit for the household size" → pure policy decision
- "Deny if the applicant is not a US resident" → pure eligibility condition
- "Computed: adjusted income = gross income - standard deduction - earned income deduction" → pure calculation

---

### SP-MaintainabilityReview

**When to call:**
- `/extract-ruleset`: new Step 4b — after Step 4 (Draft the Catala Module), before Step 5 (Write Extraction Manifest) / Step 6 (Validate)
- `/update-ruleset`: new Step 6b — after Step 6 (Merge into Existing Catala Module), before Step 7 (Update Manifest)

**In multi-file extractions:** SP-MaintainabilityReview runs independently per `generate` entry in the work-list, same as SP-TagOutputs and SP-GuidanceCapture.

**In `/update-ruleset` context:** SP-MaintainabilityReview checks only rules and computed fields that were added or modified in the current update (identified in Step 4: Identify Affected Sections). It does not re-check unchanged rules.

**Input:** The drafted/merged Catala module file (path). Also available: the `ruleset_groups:` from `guidance/ruleset-groups.yaml` for the domain (for context on expected stage names).

**Procedure:**

```
Run the following checklist against the Catala module file:

CHECKLIST:

[M1] group: coverage (non-blocking)
     PASS: Every rule entry has group: set to a defined ruleset group name
           (or ruleset_groups is empty, in which case this check is advisory only)
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
  3. Apply the fix directly to the Catala module file (in-place edit without asking for confirmation)
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
Do not advance to the post-emission clerk-loop until M5 passes.

---

## Common Mistakes to Avoid

- **Don't forget `default eligible := false`** — OPA boolean rules are undefined (not false) when conditions don't match; the transpiler handles this automatically for all `bool` fields in `outputs:` and `computed:`
- **Cite the actual CFR/USC section** for each rule, not just "Program Policy Manual"
- **Use `optional: true`** for fact fields that may not always be provided (e.g., `earned_income`, `shelter_costs`)
- **Distinguish earned vs. unearned income** if any deduction applies only to earned income
- **Use `computed:` for multi-step formulas** — don't reference undefined identifiers in `when:` clauses; if a value needs multiple steps to compute, define it in `computed:` and reference it by name
- **Don't use `git diff` alone for change detection** — also run `git status` to catch untracked new files not yet committed
- **Always update the manifest after extraction** — stale git SHAs in `extraction-manifest.yaml` will cause UPDATE mode to miss real changes on the next run
- **Don't add a synonym entry when the analyst kept the same name** — only append `{name: <prior-key>}` to `synonyms:` when the analyst's confirmed Field Name in Step 3b differs from the existing `specs/naming-manifest.yaml` entry's key for the same concept.
- **Don't append a duplicate entry on re-run rename** — when a rename lands in `specs/naming-manifest.yaml`, replace the existing field-name-matched entry rather than appending. Carry the existing entry's `synonyms:` list forward, then append the existing entry's key as a new synonym (so the full rename chain accumulates as synonyms across rounds). Skip the append when the prior key is already present in the carried-forward list.

---

## SP-LoadNamingManifest

**Signature:** `SP-LoadNamingManifest(path)`

- `path` — absolute path to `specs/naming-manifest.yaml` (the analyst-authoritative canonical-name file). Confirmed against a doc OR seeded pre-extraction by `/declare-target-ruleset`. Per-entry provenance lives inside the entry's `observations:` list (manifest schema v3.0); the list may be empty/absent on synthesized outputs or when seeded from a suggestion file that supplied none. `/extract-ruleset` Step 7 fills observations in once the analyst maps a seeded name to an observed phrase.

**If the file exists:** Read it. Build a lookup map `{variable_name → manifest_entry}` collecting entries from `inputs.<EntityName>.<field>`, `computed.<field>`, and `outputs.<field>`. The `manifest_entry` carries an `observations:` list (may be empty or absent) — each observation is a triple `{policy_phrase, source_doc, section}` where `source_doc` and `section` appear together when present and `policy_phrase` is independently optional. The entry also carries (when present) `description`, `type`, `optional`, `values`, `enum_variants`, and `synonyms` (the row list `[{name}, ...]` — every synonym is `{name}`-only in v3.0; phrase-level provenance for any prior-name observation lives in the entry's `observations:` list, not on per-synonym fields). **`observations:` may be empty/absent:** seeded entries written by `/declare-target-ruleset` carry whatever `observations:` list the suggestion file supplied, or omit the field entirely for synthesized outputs; `/extract-ruleset` Step 7 unions in additional observations via the preserve-non-null rule once the analyst confirms a seeded name against an observed phrase. Optional fields (`description:`, `type:`, `optional:`, `values:`, `enum_variants:`, `synonyms:`) are analyst-supplied or AI-inferred from policy text per `/extract-ruleset` Step 7; older specs files lacking these keys are tolerated. For `inputs:` entries, the entity name is also recorded on the entry so callers that surface the table know which entity each field belongs to.

**Type metadata.** The manifest is the authority for per-field Catala-native type, optionality, and enum-variant metadata as well as identifier names. Three optional fields per entry carry the metadata:

- `type:` — Catala-native type name, exactly one of: `integer`, `decimal`, `money`, `boolean`, `date`, `duration`, `string`, `enum`, `list`, `structure`. The merge tool rejects any other value. Struct/enum type references (e.g. `Household`, `EligibilityResult`) are also permitted as bare names; nested struct schemas are NOT recursively encoded — only the leaf type.
- `optional:` — boolean. When `true`, the field is `Optional<T>` in the Catala emission. Default when absent: `false`.
- `enum_variants:` — list of variant constructor names for enum-typed fields (e.g. `["Eligible", "Denied", "ManualVerification"]`). Distinct from `values:` (the list of allowed string values); `enum_variants:` carries the Catala-side constructor names.

Consumers that need type info (`/catala-emit-tests`, `/create-tests`, `/expand-tests`, `/create-sample-tests`, `/extract-test-cases`, the CSV import/export tools) read these fields from the manifest. **Default behavior when `type:` is absent on a referenced field:** consumers default to `string` and emit a `WARN` to stderr identifying the field — never abort.

**Rename lookup via `synonyms:`.** When a caller has the analyst's confirmed Field Name for an entry and wants to find the entry's prior key (e.g., to anchor a rename in Step 7's merge), scan every entry's `synonyms[].name` list — a match means the entry's current key is the canonical and the matching `synonyms[].name` is a prior name from an earlier rename. This replaces the v10.0.x `original_name:` lookup which carried the same information as a top-level field.

**Using the map during rule generation:**

Two operations apply:

1. **Name confirmation (keyed lookup):** When you have already inferred a candidate variable name, look it up directly by key. If found, use that name as-is — do not re-derive it.

2. **Concept matching (value scan):** When you encounter a policy concept in source text and have not yet inferred a variable name, scan map values and compare the concept against each `observations[*].policy_phrase` on each entry. If a close match is found, use that entry's variable name rather than deriving a new one. When multiple entries match, prefer entries with an observation whose `source_doc` and `section` match the current policy document being processed; use non-matching entries as fallback only.

In both cases these names are **canonical** — never re-derive or rename a variable that already exists in the manifest.

**If absent or malformed:** Proceed with an empty lookup map. Defensive parsing: an unreadable or invalid YAML file is treated as absent (do not abort the caller).

---

## SP-LoadInputIndex

**Signature:** `SP-LoadInputIndex(domain, paths, mode=batch)`

- `domain` — the domain name; resolves to `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml`.
- `paths` — list of `input/policy_docs/<rel>.md` keys to load. Pass a single-element list when scoping to one file, the full in-scope set when scoping to a multi-doc selection, or `[]` to request the entire (filtered) `files:` map (used by `/update-ruleset` Step 2 to enumerate added files).
- `mode` — `batch` (default; filter rejected entries with `md_quality.score < 40`) or `strict` (no filtering — caller has already constrained `paths` to known-eligible entries).

**Output:** map `{path → sha}` for the requested paths (or for every eligible entry when `paths == []`), or an abort signal with the `:::error` text the caller should print verbatim.

**When to call:**
- `/extract-ruleset`: in pre-flight, immediately after Pre-flight Check 5 returns. Pass the in-scope source set (the single available `.md` file or the multi-doc selection chosen in Check 5).
- `/update-ruleset`: in pre-flight, immediately after Step 0 (naming-manifest divergence + multi-file validation). Pass `paths == []` so the full filtered `files:` map is loaded for use in Steps 1, 2, and 7.

**Procedure:**

```
1. Resolve and load.
   Read $DOMAINS_DIR/<domain>/policy_facets/input-index.yaml.
   If the file is absent or unreadable:
     → Emit abort signal:
       :::error
       policy_facets/input-index.yaml is missing or unreadable for <domain>.
       Run `/index-inputs <domain>` first to build the input index.
       :::
     → Caller stops.

2. Parse files: block.
   Extract the files: map. Path keys are input/policy_docs/<rel>.md strings.
   Each entry has shape: { sha: "<40-hex>" | "untracked", md_quality: { score: <int>, flags?: [...] } }.

3. Filter rejected entries (batch mode only).
   When mode == batch, drop entries where md_quality.score < 40.
   /index-inputs moved these source files from input/policy_docs/<rel>.md to input/rejected/<rel>.md;
   their input-index.yaml entry persists but the source is no longer at the index key's path.

4. Materialize requested paths.
   If paths == []: requested_paths = the full set of keys remaining after step 3.
   Else: requested_paths = paths.

   For each <path> in requested_paths:
     If the entry is missing from the (filtered) files: map:
       → Emit abort signal:
         :::error
         no input-index.yaml entry for <path> in <domain> (or the entry is rejected).
         Run `/index-inputs <domain>` to refresh the index.
         :::
       → Caller stops.

5. Drift check.
   For each <path> in requested_paths, compute:
     git hash-object $DOMAINS_DIR/<domain>/<path>
   Compare the result against the index entry's sha: value.
   If the values differ:
     → Emit abort signal:
       :::error
       policy_facets/input-index.yaml is stale for <path> in <domain> (working tree differs from indexed SHA).
       Run `/index-inputs <domain>` first to refresh the index, then re-run this skill.
       :::
     → Caller stops.

   If git hash-object itself fails (e.g., git unavailable) or the index entry's sha: equals "untracked",
   skip the drift comparison for that path — the index's sha: value is still returned to the caller as-is.

6. Return.
   Return the map { <path>: <sha> } covering every path in requested_paths.
   Each <sha> is the verbatim value from the index's files.<path>.sha field.
```

**Field-name translation contract.** The index's per-file SHA lives at `files.<path>.sha`. The `extraction-manifest.yaml` field is named `git_sha:`. Callers write the SHA value returned by this SP directly into the manifest's `git_sha:` field — no recomputation, no rename of the field. Only the wire-format field name differs; the value is identical.

**Why the drift check.** Before this SP, callers ran `git hash-object` against the source at extract/update time, so the recorded `git_sha` was guaranteed to match the bytes that were actually extracted — even when source files had uncommitted edits between `/index-inputs` and the calling skill. After this SP, callers consume the index value rather than recomputing; the drift check preserves that guarantee by hard-failing when the index lags the working tree, and directs the analyst to re-run `/index-inputs` rather than silently recording a stale SHA.

**Relationship to Pre-flight Check 5.** Check 5 has its own "input-index.yaml does not exist" branch that allows the caller to proceed without an index (the `n` option). For callers that subsequently invoke `SP-LoadInputIndex` (`/extract-ruleset`, `/update-ruleset`), the SP's hard-fail in step 1 will abort the run regardless. For callers that do not invoke this SP (`/extract-sample-rules`), Check 5's soft fallback continues to apply.

---

## SP-LoadGuidanceShas

**Signature:** `SP-LoadGuidanceShas(domain)`

- `domain` — the domain name; resolves to `$DOMAINS_DIR/<domain>/specs/guidance/` plus `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml`.

**Output:** map `{path → sha}` covering every `specs/guidance/*.yaml` file and `specs/naming-manifest.yaml` that currently exists in the domain. Path keys are repo-relative strings (e.g., `specs/guidance/skeleton.yaml`, `specs/naming-manifest.yaml`). When the guidance tier is empty (no `specs/guidance/` directory or no `*.yaml` files in it), returns an empty map.

**When to call:**
- `/extract-ruleset`: in pre-flight, immediately after `SP-LoadInputIndex` returns. The resulting map is consumed by Step 5 to fill the `consumed_guidance[].sha:` block in `specs/extraction-manifest.yaml`.
- `/update-ruleset`: in pre-flight, immediately after `SP-LoadInputIndex` returns. The resulting map is consumed by Step 7 to refresh `consumed_guidance[].sha:` for the program being updated.

**Procedure:**

```
1. Enumerate upstream guidance files.
   Build the list of candidate paths:
     - $DOMAINS_DIR/<domain>/specs/guidance/*.yaml  (non-recursive, direct children only)
     - $DOMAINS_DIR/<domain>/specs/naming-manifest.yaml
   Include only paths that currently exist as regular files.
   Skip dot-prefixed files (e.g. .facets-manifest.yaml is generated by /check-freshness's
   write-side, not a tier-3 input).

2. Compute working-tree SHAs.
   For each <path>, compute:
     git hash-object $DOMAINS_DIR/<domain>/<path>
   On success, record the SHA. On failure (git unavailable, hash-object exits non-zero),
   record the literal string "untracked" — same fallback contract as SP-LoadInputIndex
   step 5.

3. Return.
   Return the map { <path>: <sha> } covering every enumerated path.
   When no guidance files exist, return an empty map; callers treat that as
   "tier-3 has no inputs to record this run."
```

**Field-name convention.** The new `consumed_guidance[]` block in `extraction-manifest.yaml` uses `path:` + `sha:` (not `git_sha:`). This is intentional: it aligns with `input-index.yaml`'s `files.<path>.sha` wording. The existing `source_docs[].git_sha` field name in the same manifest is preserved unchanged for backward compatibility — only the new block uses the cleaner form.

**Why no drift check.** Unlike `SP-LoadInputIndex`, this SP does not compare against a pre-recorded SHA. It is a fresh capture: the manifest written by the caller this run *is* the new recorded state. Drift between current upstream and the previously-written manifest is the freshness-check tool's job (`xlator check-freshness`), not this SP's. The SP exists to give the caller deterministic, atomic access to a working-tree snapshot at write time, not to enforce historical agreement.
