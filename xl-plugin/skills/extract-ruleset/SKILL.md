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
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–5),
the scoring rubric, CIVIL reference, shared procedures (SP-Validate, SP-ComputeGraph, SP-GuidanceCapture, and others), and common mistakes.

---

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain folder exists?**
   - NO → Print:
     :::error
     domain not found at `$DOMAINS_DIR/<domain>/`, suggest running `/new-domain <domain>`.
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

Run shared pre-flight checks 3–5 from `../../core/ruleset-shared.md`.

**After Check 4 (guidance files loaded):** Run **SP-ResolveRulesetModules** (from `../../core/ruleset-shared.md`) with context `extract`. Store the returned work-list for use in Steps 3b, 4, SP-Validate, Step 7, SP-TagOutputs, and SP-CompleteExtraction.
- If SP-ResolveRulesetModules emits an abort signal → stop with the message SP-ResolveRulesetModules printed.
- If the work-list has exactly one entry (ruleset_modules: empty) → proceed as today (single-file path; all steps below behave identically to prior behavior).

**After Check 5 (in-scope source set resolved):** Run **SP-LoadInputIndex** (from `../../core/ruleset-shared.md`) with `domain=<domain>`, `mode=batch`, and `paths` set to the in-scope source set:
- If Check 5 fired (2+ files): `paths` is the list of `input/policy_docs/<rel>.md` keys the user selected (a single number, comma-separated numbers, or every file when `a` was chosen).
- Else (1 file): `paths = ["input/policy_docs/<the-file>.md"]`.

Store the returned `{path → sha}` map for use in Step 5 (Write Extraction Manifest).
- If SP-LoadInputIndex emits an abort signal → stop with the message it printed. Do not advance to Step 1.

**Immediately after SP-LoadInputIndex succeeds:** Run **SP-LoadGuidanceShas** (from `../../core/ruleset-shared.md`) with `domain=<domain>`. Store the returned `{guidance-path → sha}` map for use in Step 5 to fill the `consumed_guidance[].sha:` block in `extraction-manifest.yaml`. The SP returns an empty map when no `specs/guidance/*.yaml` files exist — that case is fine; the resulting `consumed_guidance:` block is an empty list, not absent.

---

## Process

### Step 1: Read Policy Documents

The guidance files were loaded in pre-flight. Internalize the following before reading any policy documents:

```
---
[content of guidance/metadata.yaml, guidance/prompt-context.yaml,
 guidance/output-variables.yaml, guidance/input-variables.yaml,
 guidance/include-with-output.yaml, guidance/constants-and-tables.yaml,
 guidance/skeleton.yaml — paste verbatim as loaded.
 Plus specs/naming-manifest.yaml for structural variable data.]
---

Use this goal to scope your reading:
- Prioritize policy sections relevant to the input categories listed in `guidance/input-variables.yaml`.
- Watch for intermediate values referenced in `guidance/skeleton.yaml`'s `computations:` block.
- Target the primary output (the entry with `primary: true` in `guidance/output-variables.yaml`); its type comes from `specs/naming-manifest.yaml`'s `outputs:` block (mapped to CIVIL decisions[0]).
- Apply all constraints and standards listed above throughout Steps 1–7.
```

Additionally, build five in-memory structures from the loaded guidance files:

1. **Confirmed exprs map** `{variable_name → expr}`: Read `guidance/skeleton.yaml`'s `computations:` block. For each stage, iterate its `exprs:` map and add `name → expr` to the map. This map is used in Step 4.

2. **Example rules list**: Read the top-level `sample_rules:` section from `guidance/sample-artifacts.yaml` (if present) as a list of seed CIVIL snippets. Each entry has `id:`, `rule_type:`, `source:`, and `civil:`. This list is used in Step 4 (main module / single-file path).

3. **Guidance output set** `{variable_name}`: Read `guidance/include-with-output.yaml` (if present). It is a flat list of variable name strings; treat it as the include set. If the file is absent or empty, use an empty set. This set is used in Step 4 and SP-TagOutputs.

4. **Constants/tables seed list** `[{name, description, source_file, source_section}]`: Read the top-level `constants_and_tables:` key from `guidance/constants-and-tables.yaml` (if present). For each entry, collect its `name:`, `description:`, `source_file:`, and `source_section:` — all four are required on every guidance entry. If the file is absent or empty, the list is empty. This list is used in Step 4.

5. **Per-module sample rules map** `{module_name → [{id, rule_type, source, civil}]}`: Iterate `ruleset_modules:` from `guidance/ruleset-modules.yaml` (if present). For each entry, collect the module's `name:` and its `sample_rules:` list (empty list if the key is absent on that entry). If `ruleset_modules:` is absent or empty, the map is empty. This map is used in Step 4 (multi-file path only).

Read the caveman-compressed copies for the files selected via the pre-flight prompt (all files if `a` was chosen, or the specific file(s) selected by number). Translate each index key's `input/policy_docs/` prefix to `policy_facets/compressed/` — see the "Index path keys vs content reads" section in `xl-plugin/CLAUDE.md`.

**If `policy_facets/computations/` is populated**, use the per-file files as a reading guide: glob `policy_facets/computations/**/*.md.yaml`, then for each selected source file open the matching per-file file at `policy_facets/computations/<rel>.md.yaml` (a YAML map with one top-level key `sections`) and skim `data["sections"]` (heading/summary/tags/computations on each section block) to understand structure before reading the full compressed content. The source path of each per-file file is encoded in its relative location — strip the trailing `.yaml` from the per-file path: `policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md`; read the matching compressed file at `policy_facets/compressed/<rel>.md`.

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

If SP-ResolveRulesetModules resolved a main module name from a `role: main` entry in `guidance/ruleset-modules.yaml` (Step 1b of SP-ResolveRulesetModules), use that name directly — no inference or prompt needed.

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

Present the result as a Markdown table with a **Source** column distinguishing seeded / extracted / algorithm-derived entries:

:::detail
| Policy Phrase | Entity / Section | Field Name | Source Section | Source |
|--------------|-----------------|-----------|----------------|--------|
| gross monthly income | Household | `gross_monthly_income` | §1.2 | extracted |
| number of people in the household | Household | `household_size` | §1.1 | extracted |
| net monthly income after all deductions | computed | `net_income` | §2.4 | extracted |
| eligibility status | outputs | `eligibility_status` |  | seeded |
:::

The **Source** column distinguishes three values:
- **`seeded`**: from `specs/naming-manifest.yaml` with no `policy_phrase` (analyst declared via `/declare-target-ruleset`; provenance is null pre-extraction). Source Section column is blank. Policy Phrase column shows `<seeded>` placeholder.
- **`confirmed`**: from `specs/naming-manifest.yaml` with a populated `policy_phrase` (was confirmed against a doc in a prior run). Source Section comes from the entry's `section`. The variable name on the row equals the existing specs key.
- **`extracted`**: surfaced from per-file `*.md.yaml` files via the aggregation algorithm below — names from `expr_hint:` LHSes plus AI-scanned `description:` prose for descriptive-only computations. Source Section is the per-file section's `heading:` value; the per-file file's source_doc (reconstituted from its relative path) provides per-row provenance.
- **`algorithm-derived`**: no prior entry and no per-file extraction surfaced the concept; derived directly from policy text via the algorithm above.

When the analyst-confirmed Field Name in Step 3b differs from a previously confirmed specs key (rename), the Source column shows `confirmed` and the analyst-edited cell carries the new name; the rename is recorded in Step 7 by appending the prior specs key to the entry's `synonyms:` list.

**Pre-populate the table from three sources:**

1. **Manifest entries:** If `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` exists, run **SP-LoadNamingManifest** (from `../../core/ruleset-shared.md`). For each entry:
   - **Confirmed entries** (have `policy_phrase`): pre-populate Field Name from the variable name key, Policy Phrase from `policy_phrase`, Entity / Section from the entity key (e.g., `Household`) for `inputs:` entries or `computed`/`outputs` otherwise, Source Section from `section`, **Source = `confirmed`**.
   - **Seeded entries** (no `policy_phrase`): pre-populate Field Name from the variable name key, Entity / Section from the entity key, **Source = `seeded`**. Source Section is blank (provenance not yet filled). Policy Phrase column shows `<seeded>` placeholder.

2. **Per-file aggregation (`extracted`):** For policy concepts not already covered by the manifest, walk every `*.md.yaml` under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and extract candidate names per the aggregation algorithm:
   - For each `sections[*].computations[*]` entry: if `expr_hint:` is present and well-formed (`output_name = <expression>`), the LHS is the computation's output name and the RHS is tokenized for snake_case identifier inputs (skip numeric/string literals and built-in keywords). For descriptive-only computations (no `expr_hint:`), AI-scan the entry's `description:` prose for variable names that mirror the source's verbatim noun phrases.
   - Each surfaced name is recorded with its provenance: the per-file file's `source_doc` (reconstituted as `input/policy_docs/<rel>.md` from the per-file file's relative path under `policy_facets/computations/`) and the enclosing section's `heading:` value (used as Source Section).
   - **Determinism rules** (apply uniformly across the aggregation, so re-runs produce stable inventories):
     - Dedup case-insensitively on the candidate Field Name.
     - When the same name appears across multiple `source_doc` paths, surface **one row per `source_doc`** rather than collapsing — the analyst sees each file the name was observed in.
     - Within each `source_doc`, order rows alphabetically by canonical Field Name.
   - For each surfaced name not already covered by a specs entry, populate the row with **Source = `extracted`**, Field Name = the snake_case name, Entity / Section = inferred from the per-file section's heading/summary plus the variable name itself (use the same heuristics as `/suggest-target-ruleset`'s entity-inference rule; fall back to `Case` when ambiguous).

3. **Algorithm-derived (fallback):** For policy concepts not covered by the manifest and not surfaced by the per-file aggregation, derive the name from policy text using the algorithm above. **Source = `algorithm-derived`**.

When the manifest and the per-file aggregation both surface the same concept (matched case-insensitively by name), the manifest wins — it is the analyst-confirmed authority. The per-file row is suppressed.

:::user_input
Do the field names in this table match your intent? You may edit any name.
:::
If the user changes any name, update the table and re-present. Loop until the user explicitly approves. Use the approved names in Step 4 onward.

When a confirmed specs entry's Field Name is edited (rename), retain the prior specs key as the rename anchor for Step 7 (it is appended to the rewritten entry's `synonyms:` list). The per-file aggregation does not contribute to rename anchoring — anchors flow only through the existing specs entries themselves.

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

**CIVIL v6 — ruleset_groups auto-copy:** When emitting the `rule_set:` block, check whether `guidance/ruleset-groups.yaml` exists and has a top-level `ruleset_groups:` list:
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
- **Table entry:** emit a `tables:` skeleton using the seed `name:` (snake_case), the seed `description:`, and placeholder `key:`, `value:`, and `rows:` derived from policy text. Add the YAML comment `# pre-seeded from guidance/constants-and-tables.yaml` on the entry's name line. If no matching policy text is found, include the skeleton as a stub and add `# not found in policy — verify manually`.
- **Constant entry:** emit a `constants:` entry using the seed `name:` (UPPER_SNAKE_CASE) with its value filled from policy text. Add the YAML comment `# pre-seeded from guidance/constants-and-tables.yaml`. If no value is found in policy text, use `null  # not found in policy — verify manually`.
- **`source:` population from seed provenance:** populate the table or constant's `source:` field as `"<source_section> — <source_file basename without .md>"` (matching the `"<§ citation> — <heading>"` format used elsewhere in this step) directly from the seed entry's `source_file:` and `source_section:` — both are guaranteed present by `/create-skeleton`. Do not re-derive `source:` from policy text for seeded entries.
- After all seeded entries, append any additional tables or constants found in policy text that were not in the seed list (existing behavior).

Create `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`:

**Before drafting `outputs:`,** identify the primary output (the entry with `primary: true` in `guidance/output-variables.yaml`) and read its `type:` from `specs/naming-manifest.yaml`'s `outputs.<primary_name>.type`:
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
  # CIVIL v6: ruleset_groups (auto-copied from guidance/ruleset-groups.yaml if defined)
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
      - { path: "input/policy_docs/<rel>.md", git_sha: "<sha>" }
    consumed_guidance:
      - { path: "specs/guidance/<file>.yaml", sha: "<sha>" }
      - { path: "specs/naming-manifest.yaml", sha: "<sha>" }
```

**Multi-file (ruleset_modules: non-empty):** write using the multi-file format (see `../../core/civil-quickref.md` — Authoring Tooling Schemas section). For each `reference` entry in the work-list, set `referenced: true` in its `sub_modules:` entry; for `generate` entries, set `referenced: false`. Each sub-module entry also carries its own `consumed_guidance:` block using the same `{path, sha}` shape — populate it identically to the parent program's block (sub-modules consume the same guidance set as the parent in v1).

For each `source_docs:` entry being written, read the SHA from the `{path → sha}` map produced by **SP-LoadInputIndex** in pre-flight, keyed on the entry's `path:` (`input/policy_docs/<rel>.md`). Write that value verbatim into the entry's `git_sha:` field. Do not run `git hash-object` here — the SP already computed the working-tree drift check, so the indexed SHA is guaranteed to match the bytes being extracted. Field-name translation: the index field is `sha:`, the manifest field is `git_sha:`; the value is identical (see `../../core/ruleset-shared.md` SP-LoadInputIndex "Field-name translation contract").

For each `consumed_guidance:` entry, read the SHA from the `{guidance-path → sha}` map produced by **SP-LoadGuidanceShas** in pre-flight, keyed on the entry's `path:` (`specs/guidance/<file>.yaml` or `specs/naming-manifest.yaml`). Write that value verbatim into the entry's `sha:` field. Enumerate every path that appears in the map — the resulting `consumed_guidance:` list reflects the full state of the guidance tier at extract time. When the SP returns an empty map (no `specs/guidance/*.yaml` files), write `consumed_guidance: []` (an empty list, not an absent field). This block enables the `/check-freshness` tier-3 drift check (`civil_stale`); without it, the freshness check reports `civil_manifest_missing`.

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

**Seeded-entry handling.** When the Name Inventory's Source column for an entry is `seeded`, the entry already exists in `specs/naming-manifest.yaml` with nullable provenance (no `policy_phrase`, no `source_doc`, no `section`). For each such entry the analyst confirmed against an observed phrase in Step 3b:
- Fill `policy_phrase:` from the observed entry's policy phrase (the Source Section column's policy_phrase value, joined to the seeded entry by name-equality).
- Fill `source_doc:` and `section:` from the observed entry's provenance.
- Apply the preserve-non-null rule below: existing analyst-supplied fields on the seeded entry (description, type, values) are preserved; null/absent fields gap-fill from the matched defaults entry.

For seeded entries the analyst did NOT match against an observation in Step 3b (still standalone after confirmation), leave provenance fields null. They remain seeded-but-unobserved; the next `/index-inputs` run may surface a matching observation and a future Step 7 will fill provenance retroactively.

**`policy_phrase:` derivation.** For each entry being written, derive `policy_phrase:` from the source policy doc's verbatim text scoped to the section the name was observed in:
- For rows with Source = `confirmed` or `seeded` (existing specs entries): when the analyst confirmed an unfilled (seeded) phrase against an observation in Step 3b, fill `policy_phrase:` from the analyst-confirmed observation. When the entry was already confirmed, preserve the existing `policy_phrase:` per the preserve-non-null rule below.
- For rows with Source = `extracted` (surfaced by per-file aggregation): read the caveman-compressed source doc at `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` (where `<rel>.md` is reconstituted from the per-file file's relative path under `policy_facets/computations/`). Scope the scan to the section the name was observed in by using the per-file section's `heading:` value as a boundary marker — locate the heading in the compressed source and read text up to the next heading of equal or higher level. Within that scoped text, extract the verbatim noun phrase per the rule in `core/naming_guide.md` lines 34–54. If the section heading cannot be located in the compressed source (boundary edge case at start or end of doc), fall back to scanning the whole file but log a `policy_phrase:` derivation warning.
- For rows with Source = `algorithm-derived`: derive `policy_phrase:` from the policy text directly per the same verbatim rule, using the heading and surrounding paragraph the analyst pointed to during Step 3b confirmation.

**Rename tracking via `synonyms:`.** Rename anchoring re-uses the existing `synonyms:` list rather than a dedicated `original_name:` field:

- If the analyst's confirmed Field Name equals the existing specs entry's key (analyst kept the same name), **do not append anything to `synonyms:`** for this entry.
- If the analyst's confirmed Field Name differs from the existing specs entry's key (analyst renamed it in Step 3b), append `{name: <prior-specs-key>}` to the new entry's `synonyms:` list — the prior key joins the rename chain as a rename-anchor synonym (no `source_doc:` or `section:`, since the prior name was the analyst's prior choice, not an observed phrasing).
- If the entry is new (Source = `extracted` or `algorithm-derived` with no matching prior specs entry), do not append anything — there is no prior canonical to record.

**Optional fields.** `description:`, `type:`, `values:`, and `synonyms:` are no longer auto-propagated from any external file. Sources for these fields:
- Analyst-supplied during Step 3b confirmation, or already present on a prior `confirmed` specs entry (preserved per the preserve-non-null rule below).
- AI-inferred from policy text when the source carries an unambiguous signal — `type:` from a currency marker / yes-no phrasing / bulleted enum / etc.; `description:` from a definitional sentence in the source; `values:` from an enumerated list when `type: enum` is inferred.
- Otherwise omitted (specs entries' optional fields are nullable per the existing schema).

`role_hint:` is intentionally excluded because specs encodes role via the section placement (`inputs.<Entity>` vs `computed:` vs `outputs:`). `synonyms:` carries two kinds of entries: **observed-phrasing synonyms** (curated by the analyst, with `source_doc:` and `section:` populated to anchor the alternative name in policy text) and **rename-anchor synonyms** (appended by Step 7 on each rename round, with `source_doc:` and `section:` omitted).

**Multi-file:** Write one consolidated `naming-manifest.yaml` covering all `generate` entries in the work-list (sub-modules first, main module last). Merge entries from each module into the appropriate `inputs:`, `computed:`, and `outputs:` sections.

```yaml
version: "1.0"
inputs:
  <EntityName>:
    <field_name>:
      policy_phrase: "<exact policy phrase derived per Step 7 rule>"
      description: "<analyst- or AI-inferred>"    # optional; omitted when absent
      type: "<money|bool|int|float|string|enum|list|date>"  # optional; omitted when absent
      values: ["<a>", "<b>"]                      # optional; only when type: enum
      source_doc: "<source filename>"
      section: "<source title, heading, and paragraph>"
      synonyms:                                   # optional; observed-phrasing entries plus rename-anchor entries
        - name: <alt-name>                        # observed in policy text
          source_doc: <input/policy_docs/...>
          section: "<...>"
        - name: <prior-specs-key>                 # rename-anchor (Step 7 append; no source_doc/section)
  # repeat for each entity
computed:
  <field_name>:
    policy_phrase: "<exact policy phrase>"
    description: "<analyst- or AI-inferred>"      # optional
    type: "<...>"                                 # optional
    values: ["<...>"]                             # optional; only when type: enum
    source_doc: "<source filename>"
    section: "<source title, heading, and paragraph>"
    synonyms:                                     # optional; observed-phrasing + rename-anchor
      - name: <alt-name>
        source_doc: <input/policy_docs/...>
        section: "<...>"
      - name: <prior-specs-key>                   # rename-anchor (no source_doc/section)
outputs:
  <field_name>:
    policy_phrase: "<exact policy phrase from Name Inventory>"
    description: "<analyst- or AI-inferred>"      # optional
    type: "<...>"                                 # optional
    values: ["<...>"]                             # optional; only when type: enum
    source_doc: "<source filename>"
    section: "<source title, heading, and paragraph>"
    synonyms:                                     # optional; observed-phrasing + rename-anchor
      - name: <alt-name>
        source_doc: <input/policy_docs/...>
        section: "<...>"
      - name: <prior-specs-key>                   # rename-anchor (no source_doc/section)
  # repeat for each outputs: field
```

**Re-run merge — replace-on-rename, keyed by entry identity** (CREATE re-run when the file already exists):

- For each entry being written, identify its prior specs counterpart by **field-name match** against the existing specs entries. (When a confirmed entry's name is unchanged, the new and prior keys match. When the analyst renamed in Step 3b, the rename anchor is the prior specs key carried alongside the row.)
- **Match found, name matches existing key (analyst kept the same name):** preserve the existing entry's populated fields, including any `synonyms:` already on it. **Preserve-non-null rule:** on a name-match re-run, the writer preserves a field's value when present (non-null), and fills it only when null or absent. This composes cleanly with seed-time analyst values (preserved when supplied) and AI-inferred or analyst-confirmed values (filled when blank). Provenance fields (`policy_phrase`, `source_doc`, `section`) are gap-fillable when null, which is exactly what seeded entries arriving at Step 7 with provenance still null require. To force re-derivation of an analyst-supplied value, delete the field from specs and re-run `/extract-ruleset`.
- **Match found, name differs (analyst renamed in this run):** **replace** the existing entry — write the new entry under the new field-name key. Carry the existing entry's `synonyms:` list forward, then append `{name: <existing-key>}` to it as a new rename-anchor synonym (no `source_doc:`/`section:`). Skip the append when `<existing-key>` is already present in the carried list (idempotent on re-runs that don't actually rename). The full rename chain accumulates in `synonyms:` across rounds. Drop the existing entry from the file (no duplicate). Optional fields (`description:` / `type:` / `values:`) are carried forward from the existing entry under the preserve-non-null rule above.
- **No match (new entry):** append a new entry. New entries surfaced via the per-file aggregation or algorithm-derived path do not carry rename-anchor synonyms — there is no prior specs canonical to anchor against.

This preserves rename-chain integrity across multiple rename rounds — every prior canonical name accumulates as a synonym, so downstream consumers can resolve any historical name to the current canonical by scanning `synonyms[].name`.

This file is user-editable. Do **not** add an "auto-generated" comment.

---

:::important
Extraction complete.
:::

:::next_step
Run the review gate to validate and finalize:

```
/review-ruleset <domain> <program>
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
| `$DOMAINS_DIR/<domain>/policy_facets/computations/<rel>.md.yaml` | Read-only (per-file section data; if present) |
| `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` | Read-only (canonical content for AI consumption) |
| `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` | Read (required — run `/declare-target-ruleset <domain>` first) |
| `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` | Read (required) |
| `$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml` | Read (required) |
| `$DOMAINS_DIR/<domain>/specs/guidance/input-variables.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/include-with-output.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/sample-artifacts.yaml` | Read (if present) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/review-ruleset`. Tests and transpilation are handled by `/create-tests` and `/transpile-and-test`.
