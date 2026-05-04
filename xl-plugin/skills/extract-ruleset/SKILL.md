---
name: extract-ruleset
description: Extract Ruleset from Policy Documents
---

# Extract Ruleset from Policy Documents

Create a CIVIL DSL ruleset for a domain from documents in its `input/policy_docs/` subfolder.

## Input

```
/extract-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/extract-ruleset <domain> <program>                # target a specific <program>.civil.yaml
/extract-ruleset <domain> <program> <filename>     # scope extraction to one input file
```

`<filename>` is the basename of a `.md` file in `$DOMAINS_DIR/<domain>/input/policy_docs/` (e.g., `APA.md`). The `.md` extension is appended automatically if omitted. When given, `<filename>` scopes the full pipeline: only that file is read as the policy corpus, and only its manifest entry is updated.

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–6),
the scoring rubric, CIVIL reference, shared procedures (SP-Validate, SP-ComputeGraph, SP-GuidanceCapture, and others), and common mistakes.

---

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain folder exists?**
   - NO → Print:
     :::error
     domain not found at `$DOMAINS_DIR/<domain>/`, suggest running `/xl:new-domain <domain>`.
     :::
     Stop.

2. **CIVIL file already exists?**
   - **If `<program>` was given:** check if `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` exists → if yes, redirect:
     :::error
     A ruleset already exists for <program>. To update it, run:
       /update-ruleset <domain> <program>
     :::
     Then stop. Continue if not found.
   - **If `<program>` was not given:** check `$DOMAINS_DIR/<domain>/specs/*.civil.yaml`:
     - 0 files → continue (no existing ruleset)
     - 1 file → redirect:
       :::error
       A ruleset already exists for this domain. To update it, run:
         /update-ruleset <domain>
       :::
       Then stop.
     - 2+ files → list them and prompt:
       :::error
       Existing rulesets found:
         - <program1>
         - <program2>
         ...
       To update one of these, use /update-ruleset <domain> <program>.
       To create a new program, provide a name: /extract-ruleset <domain> <new_program>
       :::
       Then stop.

Run shared pre-flight checks 3–6 from `../../core/ruleset-shared.md`.

**After Check 5 (guidance.yaml loaded):** Run **SP-ResolveRulesetModules** (from `../../core/ruleset-shared.md`) with context `extract`. Store the returned work-list for use in Steps 3b, 4, SP-Validate, Step 7, SP-TagOutputs, and SP-CompleteExtraction.
- If SP-ResolveRulesetModules emits an abort signal → stop with the message SP-ResolveRulesetModules printed.
- If the work-list has exactly one entry (ruleset_modules: empty) → proceed as today (single-file path; all steps below behave identically to prior behavior).

---

## Process

### Step 1: Read Policy Documents

The `guidance.yaml` file was loaded in pre-flight. Internalize the following before reading any policy documents:

```
---
[guidance.yaml content — paste verbatim as loaded]
---

Use this goal to scope your reading:
- Prioritize policy sections relevant to the input_variables categories listed above.
- Watch for intermediate values matching the intermediate_variables categories.
- Target a <output_variables.primary.type> primary output (mapped to CIVIL decisions[0]).
- Apply all constraints and standards listed above throughout Steps 1–7.
```

Additionally, build five in-memory structures from the loaded `guidance.yaml`:

1. **Confirmed exprs map** `{variable_name → expr}`: For each category in `intermediate_variables`, read its `computations:` list (if present). For each entry, add `name → expr` to the map. If a category has no `computations:`, no entries are added. This map is used in Step 4.

2. **Example rules list**: Read the top-level `sample_rules:` section (if present) as a list of seed CIVIL snippets. Each entry has `id:`, `rule_type:`, `source:`, and `civil:`. This list is used in Step 4 (main module / single-file path).

3. **Guidance output set** `{variable_name}`: Read `intermediate_variables.include_with_output` (if present). If the key is absent or `intermediate_variables` does not exist, use an empty set. This set is used in Step 4 and SP-TagOutputs.

4. **Constants/tables seed list** `[{name, description}]`: Read the top-level `constants_and_tables:` key (if present). For each entry, collect its `name:` and `description:`. If the key is absent or empty, the list is empty. This list is used in Step 4.

5. **Per-module sample rules map** `{module_name → [{id, rule_type, source, civil}]}`: Iterate `ruleset_modules:` (if present). For each entry, collect the module's `name:` and its `sample_rules:` list (empty list if the key is absent on that entry). If `ruleset_modules:` is absent or empty, the map is empty. This map is used in Step 4 (multi-file path only).

If `<filename>` is given, read only `$DOMAINS_DIR/<domain>/input/policy_docs/<filename>`.
Otherwise, read the files selected via the pre-flight prompt (all files if `a` was chosen, or the specific file(s) selected by number).

**If `specs/input-index.yaml` exists**, use the index as a reading guide: skim the index entries for the selected files to understand their structure before reading the full content. This helps prioritize which sections to extract from when the docs are long.

Identify:

1. **Program name and jurisdiction** — what benefit/program, which level of government
2. **Effective dates** — when do these rules apply?
3. **Input facts** — what information does the system require? (numeric values, categorical fields, boolean flags, etc.)
4. **Decisions** — what yes/no determinations does the policy make?
5. **Thresholds and lookup tables** — tables keyed by categorical variables (size, band, category, etc.)
6. **Named constants** — fixed rates, percentages, dollar amounts used in rules
7. **The rules themselves** — conditions for allow vs. deny, and the reasons given
8. **Legal citations** — CFR sections, USC provisions, or other citable authority

### Step 2: Identify CIVIL Components

Map policy elements to CIVIL DSL constructs:

| Policy Element | CIVIL Construct |
|---|---|
| Entity inputs | `inputs:` entity with typed fields |
| Eligibility outcome | `outputs:` (usually `eligible: bool` with `expr:`) |
| Denial/approval explanations | `outputs: reasons: list[Reason]` |
| Computed output value (e.g., adjusted_income) | `outputs:` field with `type: money` and `expr:` |
| Dollar thresholds by size | `tables:` with key/value rows |
| Fixed rates/amounts | `constants:` |
| **Intermediate derived values** | **`computed:` fields (CIVIL v2)** |
| Income/asset test | `rules:` with `kind: deny` |
| Pass all tests → eligible | `rules:` with `kind: allow`, `when: "true"` |

After building the component map, run **SP-OrchestrationFilter** (from `../../core/ruleset-shared.md`) on the candidate rule components:
- Remove flagged components from the generate list.
- If any components were removed, display the SP-OrchestrationFilter summary table.
- Continue to Step 3 with the filtered list. Re-included components will have a YAML comment added in the draft step.

### Step 3: Derive Program Name

If SP-ResolveRulesetModules resolved a main module name from a `role: main` entry in `guidance.yaml` (Step 1b of SP-ResolveRulesetModules), use that name directly — no inference or prompt needed.

Otherwise (no `role: main` entry exists — backward compat path):
1. Use `<program>` argument if given.
2. Infer from the `module:` name found in the policy text (e.g., "SNAP income eligibility" → `eligibility`).
3. If ambiguous, prompt: "What should the program file be named? (e.g., `eligibility`, `income_test`)"

### Step 3b: Name Inventory

**Multi-file:** Build one Name Inventory table per `generate` entry in the SP-ResolveRulesetModules work-list (sub-modules first, main module last). Label each table `Name Inventory: <module_name>`. Display all tables together in a single presentation so the user can review cross-file naming at once, then confirm or adjust as a batch. For `reference` entries: skip (names are already set in the existing file).

**Single-file (ruleset_modules: empty):** produce one Name Inventory table as described below (existing behavior).

Before drafting any CIVIL YAML, produce the canonical field name for every fact and computed concept in the policy. For each measurable quantity, flag, or derived value found in the policy documents, apply this algorithm:

1. Find the **exact noun phrase** in the policy text describing the concept
2. Use specific field names to evoke the meaning without having to look up the corresponding policy text and minimize risk of name collisions in future extractions
3. **Strip** any words that duplicate the entity name (e.g., entity is `Household` → strip "household" from "household gross income" → `gross income`)
4. Convert to **`snake_case`**
5. If the result would be **ambiguous** with another field in the same entity, append a disambiguating qualifier from the policy text

Present the result as a Markdown table:

:::detail
| Policy Phrase | Entity / Section | Field Name | Source Section |
|--------------|-----------------|-----------|----------------|
| gross monthly income | Household | `gross_monthly_income` | §1.2 |
| number of people in the household | Household | `household_size` | §1.1 |
| net monthly income after all deductions | computed | `net_income` | §2.4 |
:::

**If `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` already exists** (CREATE re-run after a previous successful extraction):
- Run **SP-LoadNamingManifest** (from `../../core/ruleset-shared.md`). Pre-populate all table columns from the resulting map: Field Name from the variable name key, Policy Phrase from `policy_phrase`, Entity / Section from the entity key (e.g., `Household`) for `inputs:` entries or `computed`/`outputs` otherwise, and Source Section from `section`.
- Only derive new names for policy concepts not already listed

:::user_input
Do the field names in this table match your intent? You may edit any name.
:::
If the user changes any name, update the table and re-present. Loop until the user explicitly approves. Use the approved names in Step 4 onward.

**`source:` population:** In Step 4, populate `source:` on every `FactField`, `ComputedField`, `TableDef`, and `Rule` using the "Source Section" value from the Name Inventory table above, *combined* with the surrounding document heading:

- Format: `"<§ citation> — <heading>"`, e.g. `"7 CFR § 273.9(a) — Income and Deductions"`
- If the "Source Section" column contains only a bare citation (`"§ 273.9(a)"`), prepend the full CFR title reference and append the heading from the enclosing document section
- For `Rule` entries (not in the Name Inventory table), derive `source:` from the heading and paragraph of the policy text where the rule's condition is stated
- `source:` is optional — if the policy document has no clear section for a given element, omit it rather than guessing

### Step 4: Draft the CIVIL Module

**Name binding:** Before writing any CIVIL YAML, re-read the approved Name Inventory table(s) from Step 3b. Use **only** those approved field names for every `inputs:`, `computed:`, `outputs:`, `tables:`, and `constants:` entry — do not re-derive names from policy text.

**Multi-file:** Iterate the SP-ResolveRulesetModules work-list in generation order (sub-modules first, main module last). For each `generate` entry, apply the full drafting logic below. For each `reference` entry, skip drafting entirely (the file is already on disk).

**Sub-module files:** Draft as a standard CIVIL module (no `invoke:` fields). Sub-module computed fields that will be accessed by the parent module via dot-access **must** have `tags: [expose]`. Remind yourself of the parent's planned `invoke:` fields when choosing which computed fields to mark as expose.

**Main module with sub-modules:** Draft with `invoke:` computed fields using the confirmed `bind:` maps from SP-ResolveRulesetModules's work-list. Use confirmed field names from the sub-module Name Inventory tables (or actual field names from `reference` files) in dot-access expressions (e.g., `client_result.net_income`). Each `invoke:` field has `type: object` and a `module:` matching the sub-module name.

**Single-file (ruleset_modules: empty):** existing behavior unchanged.

**CIVIL v6 — ruleset_groups auto-copy:** When emitting the `rule_set:` block, check whether `guidance.yaml` has a top-level `ruleset_groups:` list (written by `/xl:refine-guidance` Sub-step 3b.5):
- **If present:** copy the list directly into `rule_set.ruleset_groups` in the emitted CIVIL file. This enables `rule.group:` annotations to be validated immediately.
- **If absent:** omit the `ruleset_groups:` key from `rule_set:` entirely (the CIVIL schema treats it as optional, defaulting to `[]`).

**If the example rules list (from Step 1) is non-empty**, display those rules at the top of the CIVIL draft output for the **main module** (single-file path or main module in multi-file path) before emitting any new content:

```
# === User-approved example rules ===
# These rules were confirmed by the user. Use them as anchors for CIVIL
# structure, citation format, and naming style throughout this draft.
<civil: content of each sample_rules entry>
# =========================================================
```

**Multi-file — sub-module anchor injection:** For each **sub-module** `generate` entry, look up the module's `name:` in the per-module sample rules map (from Step 1). If the list is non-empty, display it before emitting any new content for that sub-module:

```
# === User-approved example rules (module: <name>) ===
# These rules were confirmed by the user. Use them as anchors for CIVIL
# structure, citation format, and naming style throughout this sub-module draft.
<civil: content of each ruleset_modules[<name>].sample_rules entry>
# =========================================================
```

If a sub-module's per-module list is empty, skip the anchor block for that module.

**When emitting `computed:` fields**, check the confirmed exprs map (from Step 1) first:
- If the variable name appears in the map, use its `expr:` value directly and add the YAML comment `# expr confirmed in /refine-guidance` on the same line or immediately above the `expr:` field.
- For variables not in the map, infer `expr:` from policy text as normal.

Additionally, check the guidance output set (from Step 1): if the variable name is in the set, add `tags: [expose]` immediately after the `type:` line in the emitted CIVIL YAML for that field.

**When emitting `tables:` and `constants:` sections**, if the constants/tables seed list (from Step 1) is non-empty, begin with the seeded entries before drafting from policy text:
- For each entry in the seed list, infer whether it is a `tables:` entry or a `constants:` entry from its `name:` and `description:` (keywords like "thresholds", "limits", "by household size", "lookup" → table; "fixed", "rate", "percentage", "flat amount" → constant).
- **Table entry:** emit a `tables:` skeleton using the seed `name:` (snake_case), the seed `description:`, and placeholder `key:`, `value:`, and `rows:` derived from policy text. Add the YAML comment `# pre-seeded from guidance.yaml constants_and_tables` on the entry's name line. If no matching policy text is found, include the skeleton as a stub and add `# not found in policy — verify manually`.
- **Constant entry:** emit a `constants:` entry using the seed `name:` (UPPER_SNAKE_CASE) with its value filled from policy text. Add the YAML comment `# pre-seeded from guidance.yaml constants_and_tables`. If no value is found in policy text, use `null  # not found in policy — verify manually`.
- After all seeded entries, append any additional tables or constants found in policy text that were not in the seed list (existing behavior).

Create `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`:

**Before drafting `outputs:`,** check `output_variables.primary.type` in `guidance.yaml`:
- **`bool`** (default) — use `type: bool` with `expr: "count(reasons) == 0"`
- **`enum`** — use `type: string` + `values:` + `conditional:` (see template below); `enum` maps to `string` in CIVIL
- **other scalar** (`money`, `int`, `float`) — use a typed output decision with `expr:` instead of `computed:` + `tags: [expose]`

```yaml
module: "<program_name>"
description: "..."
version: "<year>Q<quarter>"
jurisdiction:
  level: federal  # or: state, county, city
  country: US
  # state: <code>  # if state-level
effective:
  start: YYYY-MM-DD
  end: YYYY-MM-DD  # optional

inputs:
  <EntityName>:
    description: "..."
    fields:
      <field_name>:
        type: <int|float|bool|string|money|date|list|set|enum>
        description: "..."
        source: "<§ citation> — <heading>"  # e.g., "7 CFR § 273.9(a) — Income and Deductions"
        currency: USD  # for money type
        optional: true  # if not required

# outputs: CASE A — output_variables.primary.type is bool (default)
outputs:
  eligible:
    type: bool
    default: false
    description: "..."
    expr: "count(reasons) == 0"
  reasons:
    type: list
    item: Reason
    default: []
    description: "..."

# outputs: CASE B — output_variables.primary.type is enum
# outputs:
#   eligible:
#     type: string
#     values: [approve, deny, manual_verification]  # from output_variables.primary.values
#     default: "approve"  # "neutral" value
#     description: "..."
#     conditional:
#       if: "count(reasons) > 0"
#       then: "\"deny\""
#       else: "if manual_verification_required then \"manual_verification\" else \"approve\""
#             ^ for 3-way outcomes: binary conditional where else: is an inline if expression
#   reasons:
#     type: list
#     item: Reason
#     default: []
#     description: "..."

# outputs: CASE C — computation-output module (money, int, float)
# outputs:
#   adjusted_income:
#     type: money
#     currency: USD
#     description: "Final adjusted income after all exclusion steps"
#     expr: "step_n - exclusion_a - exclusion_b"

tables:
  <table_name>:
    description: "..."
    source: "<§ citation> — <heading>"  # e.g., "7 CFR § 273.9(a)(1) — Gross Income Limits Table"
    key: [<key_field>]
    value: [<value_field>]
    rows:
      - { <key_field>: <val>, <value_field>: <val> }

constants:
  UPPER_SNAKE_CASE_NAME: value

computed:  # optional (CIVIL v2) — intermediate derived values for multi-step formulas
  <field_name>:
    type: <money|bool|float|int>
    description: "..."
    source: "<§ citation> — <heading>"  # e.g., "7 CFR § 273.9(d)(1) — Earned Income Deduction"
    expr: "<CIVIL expression>"     # single expression
    review:
      extraction_fidelity: <1-5>
      source_clarity: <1-5>
      logic_complexity: <1-5>
      policy_complexity: <1-5>
      notes: "<explain any score ≤2 or ≥4>"  # omit if all scores are 3
  <field_name_2>:
    type: money
    description: "..."
    source: "<§ citation> — <heading>"
    conditional:
      if: "<bool expression>"
      then: "<value expression>"
      else: "<value expression>"
    review:
      extraction_fidelity: <1-5>
      source_clarity: <1-5>
      logic_complexity: <1-5>
      policy_complexity: <1-5>
      notes: "<explain any score ≤2 or ≥4>"  # omit if all scores are 3

rule_set:
  name: "<identifier>"
  precedence: "deny_overrides_allow"
  description: "..."
  # CIVIL v6: ruleset_groups (auto-copied from guidance.yaml if defined)
  # ruleset_groups:
  #   - name: income_test
  #     description: Income eligibility tests

rules:
  - id: "<JURISDICTION>-<TOPIC>-<KIND>-<SEQ>"  # e.g., FED-SNAP-DENY-001
    kind: deny  # or: allow
    priority: 1  # lower = higher priority; allow rules typically 100+
    description: "..."
    source: "<§ citation> — <heading>"  # e.g., "7 CFR § 273.9(a)(1) — Gross Income Test"
    when: "<CIVIL expression>"
    then:
      - add_reason:
          code: "MACHINE_CODE"
          message: "Human-readable explanation"
          citations:
            - label: "7 CFR § 273.9(a)(1)"
              url: "https://..."
              excerpt: "Brief excerpt"
    review:                          # assign scores while policy text is in context
      extraction_fidelity: <1-5>
      source_clarity: <1-5>
      logic_complexity: <1-5>
      policy_complexity: <1-5>
      notes: "<explain any score ≤2 or ≥4>"  # omit if all scores are 3
```

**Scoring:** Assign `review:` blocks to every entry in `rules:` and `computed:` as you draft them. Use the Scoring Rubric from `../../core/ruleset-shared.md`. Write scores while the source policy text is in context — do not defer to a separate pass.

**Reference:** See the **CIVIL Reference** section in `../../core/ruleset-shared.md` for expression language syntax and multi-step formula guidance.

### Step 4b: Maintainability Self-Review (CIVIL v6)

*Runs after Step 4 (Draft), before Step 5 (Write Extraction Manifest).*

**Multi-file:** Run SP-MaintainabilityReview once per `generate` entry, immediately after that file is drafted (not after all files). Label the SP-MaintainabilityReview output:
```
Maintainability Self-Review: <module_name>
```

**Single-file:** Run SP-MaintainabilityReview on the single drafted file.

Run **SP-MaintainabilityReview** (from `../../core/ruleset-shared.md`) on the drafted CIVIL file:
- SP-MaintainabilityReview applies in-place fixes for non-blocking items (M1–M4) where the fix is mechanical.
- If blocking item **M5** (duplicate priority within a `mutex_group`) fails and cannot be auto-fixed:
  1. Display the conflicting rules and their priorities.
  2. Ask:
     :::user_input
     Two or more rules in `mutex_group '<name>'` share the same priority. Please assign unique priorities, then type 'continue'.
     :::
  3. Apply the user's corrections to the draft in-memory.
  4. Re-run SP-MaintainabilityReview to confirm M5 is resolved before advancing.
- On SP-MaintainabilityReview completion: display the summary table.

Proceed to Step 5 only after SP-MaintainabilityReview passes (no blocking failures).

### Step 5: Write Extraction Manifest

**Single-file (ruleset_modules: empty):** create `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` in single-file format:

```yaml
# Auto-generated by /extract-ruleset — do not edit manually
programs:
  <program>:
    civil_file: $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml
    extracted_at: "YYYY-MM-DD"
    source_docs:
      - { path: "input/policy_docs/<filename>.md", git_sha: "<sha>" }
```

**Multi-file (ruleset_modules: non-empty):** write using the multi-file format (see `../../core/civil-quickref.md` — Authoring Tooling Schemas section). For each `reference` entry in the work-list, set `referenced: true` in its `sub_modules:` entry; for `generate` entries, set `referenced: false`.

Get each doc's git SHA:
```bash
git log -1 --format="%H" -- $DOMAINS_DIR/<domain>/input/policy_docs/<filename>.md
```
If a file is untracked (not yet committed), use `"untracked"` as the SHA.

### Step 6: Validate CIVIL files

**Multi-file:** Run **SP-Validate** once per `generate` entry in the work-list, in work-list order. On SP-Validate 3-retry failure for any file, stop and print:
```
Validation failed for: $DOMAINS_DIR/<domain>/specs/<name>.civil.yaml
The following files were written and may be inconsistent: <list of previously written files>.
Delete them and retry /extract-ruleset <domain>, or fix manually.
```
Do not proceed to the next file after a validation failure.

**Single-file:** Run **SP-Validate** as today (single call).

### Step 7: Write Naming Manifest

Now that the CIVIL file is validated, write `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` using every entry from the approved Name Inventory table (Step 3b). Field names were approved in Step 3b; validation confirms the YAML is structurally correct. Populate the `inputs:` section with entity-grouped field entries (entity names as CamelCase keys). Populate the `outputs:` section with one entry per `outputs:` field, deriving `policy_phrase:`, `source_doc:`, and `section:` from the Name Inventory or policy text provenance for that field.

**Multi-file:** Write one consolidated `naming-manifest.yaml` covering all `generate` entries in the work-list (sub-modules first, main module last). Merge entries from each module into the appropriate `inputs:`, `computed:`, and `outputs:` sections.

```yaml
version: "1.0"
inputs:
  <EntityName>:
    <field_name>:
      policy_phrase: "<exact policy phrase from Name Inventory>"
      source_doc: "<source filename>"
      section: "<source title, heading, and paragraph>"
  # repeat for each entity
computed:
  <field_name>:
    policy_phrase: "<exact policy phrase>"
    source_doc: "<source filename>"
    section: "<source title, heading, and paragraph>"
outputs:
  <field_name>:
    policy_phrase: "<exact policy phrase from Name Inventory>"
    source_doc: "<source filename>"
    section: "<source title, heading, and paragraph>"
  # repeat for each outputs: field
```

**If `naming-manifest.yaml` already exists** (CREATE re-run): merge — preserve all existing entries unchanged and append only new entries.

This file is user-editable. Do **not** add an "auto-generated" comment.

---

:::important
Extraction complete.
:::

:::next_step
Run the review gate to validate and finalize:

```
/xl:review-ruleset <domain> <program>
```
:::

---

## Output

Files created or modified by this command:

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/<sub_module>.civil.yaml` | Created (for each generated sub-module, if ruleset_modules: non-empty) |
| `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` | Created |
| `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` | Created (multi-file format if ruleset_modules: non-empty) |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Created (Step 7, after validation) |
| `$DOMAINS_DIR/<domain>/specs/input-index.yaml` | Read-only (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Read (required — run `/xl:refine-guidance <domain>` first) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/xl:review-ruleset`. Tests and transpilation are handled by `/xl:create-tests` and `/xl:transpile-and-test`.
