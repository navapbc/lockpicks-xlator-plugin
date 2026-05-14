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

3. **Load extraction context (deterministic).**

   Run:
   ```bash
   xlator load-extraction-context <domain> [<program>] --mode extract
   ```

   This tool subsumes pre-flight checks 3–5 from `../../core/ruleset-shared.md`, plus `SP-LoadInputIndex` and `SP-LoadGuidanceShas`. It reads every guidance file + `naming-manifest.yaml` + `policy_facets/input-index.yaml` + `extraction-manifest.yaml` (if present), runs the working-tree drift check on `input-index.yaml`'s recorded SHAs, computes `git hash-object` for every `specs/guidance/*.yaml` + `specs/naming-manifest.yaml`, resolves the multi-file work-list from `ruleset-modules.yaml`, and emits a single JSON payload to stdout.

   On non-zero exit: relay the tool's stderr in `:::error` and stop. The tool exits 1 on working-tree drift (with `Re-run /index-inputs <domain>`), 2 on missing required files (with the specific file path).

   Parse the JSON payload. Bind the following structures to the AI's working context (used in later steps):
   - `confirmed_exprs` — `{<variable>: <expression>}`. Used in Step 4 when emitting `computed:` fields.
   - `example_rules` — anchor block at the top of the main module's CIVIL draft.
   - `guidance_output_set` — list of variable names to be tagged `expose` in Step 4.
   - `constants_tables_seed` — pre-seeds `tables:` and `constants:` in Step 4.
   - `per_module_sample_rules` — sub-module anchor blocks in Step 4 (multi-file).
   - `input_index_shas`, `guidance_shas` — used in Step 5 to populate `extraction-manifest.yaml`.
   - `work_list` — drives multi-file extraction iteration (sub-modules first, main module last; `action: generate | reference`).
   - `metadata`, `prompt_context`, `output_variables`, `input_variables`, `naming_manifest` — the full guidance docs for Step 1 internalization and Step 3b table pre-population.
   - `program` — resolved program name (from `ruleset-modules.yaml`'s `role: main` entry, the CLI arg, or single-`*.civil.yaml` auto-detection). When `null` and `candidate_programs` is non-empty, prompt the user to choose one.

   **If the work-list has exactly one entry** (ruleset_modules: empty) → proceed as today (single-file path). **If multiple entries** → proceed with multi-file branches throughout.

4. **Multi-doc selection (Check 5 from `core/ruleset-shared.md`).** If `input/policy_docs/` contains 2+ `.md` files, follow Check 5's display logic (using the `input_index_shas` map to drive the rich indexed prompt). The selected set scopes the source docs read in Step 1.

---

## Process

### Step 1: Read Policy Documents

The context payload from pre-flight already contains every guidance file + the naming manifest as parsed JSON. Internalize them now:

```
---
[content of metadata, prompt_context, output_variables, input_variables,
 guidance_output_set, constants_tables_seed, and naming_manifest from the
 pre-flight JSON payload]
---

Use this goal to scope your reading:
- Prioritize policy sections relevant to the input categories listed in input_variables.
- Watch for intermediate values whose expressions are in confirmed_exprs.
- Target the primary output (the entry with primary: true in output_variables); its type comes from naming_manifest's outputs block.
- Apply all constraints and standards listed above throughout Steps 1–7.
```

Read the caveman-compressed copies for the files selected via the pre-flight prompt. Translate each index key's `input/policy_docs/` prefix to `policy_facets/compressed/` — see the "Index path keys vs content reads" section in `xl-plugin/CLAUDE.md`.

**If `policy_facets/computations/` is populated**, use the per-file files as a reading guide: glob `policy_facets/computations/**/*.md.yaml`, then for each selected source file open the matching per-file file at `policy_facets/computations/<rel>.md.yaml` (a YAML map with one top-level key `sections`) and skim `data["sections"]` (heading/summary/tags/computations on each section block) to understand structure before reading the full compressed content. Strip the trailing `.yaml` from the per-file path: `policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md`; read the matching compressed file at `policy_facets/compressed/<rel>.md`.

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

If the pre-flight JSON payload's `program` field is populated (resolved from a `role: main` entry in `ruleset-modules.yaml` or auto-detected from a single `*.civil.yaml`), use that name directly — no inference or prompt needed.

Otherwise (`program` is null and no CLI arg):
1. Infer from the `module:` name found in the policy text (e.g., "SNAP income eligibility" → `eligibility`).
2. If ambiguous, prompt: "What should the program file be named? (e.g., `eligibility`, `income_test`)"

### Step 3b: Name Inventory

**Multi-file:** Build one Name Inventory table per `generate` entry in the work-list (sub-modules first, main module last). Label each table `Name Inventory: <module_name>`. Display all tables together in a single presentation so the user can review cross-file naming at once, then confirm or adjust as a batch. For `reference` entries: skip (names are already set in the existing file).

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
- **`seeded`**: from the JSON payload's `naming_manifest` with no `policy_phrase` (analyst declared via `/declare-target-ruleset`; provenance is null pre-extraction). Source Section column is blank. Policy Phrase column shows `<seeded>` placeholder.
- **`confirmed`**: from the JSON payload's `naming_manifest` with a populated `policy_phrase` (was confirmed against a doc in a prior run). Source Section comes from the entry's `section`. The variable name on the row equals the existing specs key.
- **`extracted`**: surfaced from per-file `*.md.yaml` files via the aggregation algorithm below — names from `expr_hint:` LHSes plus AI-scanned `description:` prose for descriptive-only computations. Source Section is the per-file section's `heading:` value; the per-file file's source_doc (reconstituted from its relative path) provides per-row provenance.
- **`algorithm-derived`**: no prior entry and no per-file extraction surfaced the concept; derived directly from policy text via the algorithm above.

When the analyst-confirmed Field Name in Step 3b differs from a previously confirmed specs key (rename), the Source column shows `confirmed` and the analyst-edited cell carries the new name; the rename is recorded in Step 7 by passing the prior specs key as `prior_name` in the inventory JSON (Step 7's merge tool appends it to the entry's `synonyms:` list).

**Pre-populate the table from three sources:**

1. **Manifest entries:** Use the `naming_manifest` already loaded in the pre-flight JSON payload. For each entry:
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

When a confirmed specs entry's Field Name is edited (rename), retain the prior specs key as the rename anchor for Step 7 (it is passed as `prior_name` in the inventory JSON). The per-file aggregation does not contribute to rename anchoring — anchors flow only through the existing specs entries themselves.

**`source:` population:** In Step 4, populate `source:` on every `FactField`, `ComputedField`, `TableDef`, and `Rule` as an object with two subfields:

- `file:` — the source-doc path relative to the domain root, always written as `input/policy_docs/<rel>.md`. Use the `source_doc:` provenance from the manifest entry or per-file aggregation row; do not substitute the compressed-mirror path.
- `section:` — the citation plus heading, formatted as `"<§ citation> — <heading>"`, e.g. `"7 CFR § 273.9(a) — Income and Deductions"`. If the "Source Section" column contains only a bare citation (`"§ 273.9(a)"`), prepend the full CFR title reference and append the heading from the enclosing document section.
- For `Rule` entries (not in the Name Inventory table), derive `file:` and `section:` from the doc path and the heading/paragraph of the policy text where the rule's condition is stated.
- `source:` itself, and either subfield within it, is optional — if the policy document has no clear section for a given element, omit `section:` (or the whole `source:` object) rather than guessing. Never emit `source:` as a plain string.

### Step 4: Draft the CIVIL Module

**Name binding:** Before writing any CIVIL YAML, re-read the approved Name Inventory table(s) from Step 3b. Use **only** those approved field names for every `inputs:`, `computed:`, `outputs:`, `tables:`, and `constants:` entry — do not re-derive names from policy text.

**Multi-file:** Iterate the work-list in generation order (sub-modules first, main module last). For each `generate` entry, apply the full drafting logic below. For each `reference` entry, skip drafting entirely (the file is already on disk).

**Sub-module files:** Draft as a standard CIVIL module (no `invoke:` fields). Sub-module computed fields that will be accessed by the parent module via dot-access **must** have `tags: [expose]`. Remind yourself of the parent's planned `invoke:` fields when choosing which computed fields to mark as expose.

**Main module with sub-modules:** Draft with `invoke:` computed fields using the confirmed `bind:` maps from the work-list. Use confirmed field names from the sub-module Name Inventory tables (or actual field names from `reference` files) in dot-access expressions (e.g., `client_result.net_income`). Each `invoke:` field has `type: object` and a `module:` matching the sub-module name.

**Single-file (ruleset_modules: empty):** existing behavior unchanged.

**CIVIL v6 — ruleset_groups auto-copy:** When emitting the `rule_set:` block, check whether `guidance/ruleset-groups.yaml` exists and has a top-level `ruleset_groups:` list:
- **If present:** copy the list directly into `rule_set.ruleset_groups` in the emitted CIVIL file. This enables `rule.group:` annotations to be validated immediately.
- **If absent:** omit the `ruleset_groups:` key from `rule_set:` entirely (the CIVIL schema treats it as optional, defaulting to `[]`).

**If `example_rules` (from the JSON payload) is non-empty**, display those rules at the top of the CIVIL draft output for the **main module** (single-file path or main module in multi-file path) before emitting any new content:

```
# === User-approved example rules ===
# These rules were confirmed by the user. Use them as anchors for CIVIL
# structure, citation format, and naming style throughout this draft.
<civil: content of each example_rules entry>
# =========================================================
```

**Multi-file — sub-module anchor injection:** For each **sub-module** `generate` entry, look up the module's `name:` in the `per_module_sample_rules` map (from the JSON payload). If the list is non-empty, display it before emitting any new content for that sub-module:

```
# === User-approved example rules (module: <name>) ===
# These rules were confirmed by the user. Use them as anchors for CIVIL
# structure, citation format, and naming style throughout this sub-module draft.
<civil: content of each per_module_sample_rules[<name>] entry>
# =========================================================
```

If a sub-module's per-module list is empty, skip the anchor block for that module.

**When emitting `computed:` fields**, check `confirmed_exprs` (from the JSON payload) first:
- If the variable name appears in the map, use its value directly and add the YAML comment `# expr confirmed in /refine-guidance` on the same line or immediately above the `expr:` field.
- For variables not in the map, infer `expr:` from policy text as normal.

Additionally, check `guidance_output_set` (from the JSON payload): if the variable name is in the set, add `tags: [expose]` immediately after the `type:` line in the emitted CIVIL YAML for that field.

**When emitting `tables:` and `constants:` sections**, if `constants_tables_seed` (from the JSON payload) is non-empty, begin with the seeded entries before drafting from policy text:
- For each entry in the seed list, infer whether it is a `tables:` entry or a `constants:` entry from its `name:` and `description:` (keywords like "thresholds", "limits", "by household size", "lookup" → table; "fixed", "rate", "percentage", "flat amount" → constant).
- **Table entry:** emit a `tables:` skeleton using the seed `name:` (snake_case), the seed `description:`, and placeholder `key:`, `value:`, and `rows:` derived from policy text. Add the YAML comment `# pre-seeded from guidance/constants-and-tables.yaml` on the entry's name line. If no matching policy text is found, include the skeleton as a stub and add `# not found in policy — verify manually`.
- **Constant entry:** emit a `constants:` entry using the seed `name:` (UPPER_SNAKE_CASE) with its value filled from policy text. Add the YAML comment `# pre-seeded from guidance/constants-and-tables.yaml`. If no value is found in policy text, use `null  # not found in policy — verify manually`.
- **`source:` population from seed provenance:** populate the table or constant's `source:` field as a `{file:, section:}` object directly from the seed entry's `source_file:` and `source_section:` — both are guaranteed present by `/create-skeleton`. Use the seed `source_file:` value verbatim for `file:` (it is already the `input/policy_docs/<rel>.md` form) and the seed `source_section:` verbatim for `section:`. Do not re-derive `source:` from policy text for seeded entries.
- After all seeded entries, append any additional tables or constants found in policy text that were not in the seed list (existing behavior).

Create `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`:

**Before drafting `outputs:`,** identify the primary output (the entry with `primary: true` in `output_variables` from the JSON payload) and read its `type:` from the `naming_manifest`'s `outputs.<primary_name>.type`:
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
        source:
          file: "input/policy_docs/<rel>.md"            # e.g., "input/policy_docs/snap_eligibility.md"
          section: "<§ citation> — <heading>"           # e.g., "7 CFR § 273.9(a) — Income and Deductions"
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
    source:
      file: "input/policy_docs/<rel>.md"
      section: "<§ citation> — <heading>"   # e.g., "7 CFR § 273.9(a)(1) — Gross Income Limits Table"
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
    source:
      file: "input/policy_docs/<rel>.md"
      section: "<§ citation> — <heading>"   # e.g., "7 CFR § 273.9(d)(1) — Earned Income Deduction"
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
    source:
      file: "input/policy_docs/<rel>.md"
      section: "<§ citation> — <heading>"
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
    source:
      file: "input/policy_docs/<rel>.md"
      section: "<§ citation> — <heading>"   # e.g., "7 CFR § 273.9(a)(1) — Gross Income Test"
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

For each `source_docs:` entry, read the SHA from the `input_index_shas` map in the pre-flight JSON payload, keyed on the entry's `path:` (`input/policy_docs/<rel>.md`). Write that value verbatim into `git_sha:`. Do not run `git hash-object` here — the pre-flight tool already validated drift, so the indexed SHA matches the bytes being extracted.

For each `consumed_guidance:` entry, read the SHA from the `guidance_shas` map in the pre-flight JSON payload, keyed on the entry's `path:` (`specs/guidance/<file>.yaml` or `specs/naming-manifest.yaml`). Enumerate every path that appears in the map — the resulting list reflects the full state of the guidance tier at extract time. When the map is empty (no `specs/guidance/*.yaml` files), write `consumed_guidance: []`.

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

Build the analyst-approved Name Inventory from Step 3b as an inventory JSON file, then call `xlator merge-naming-manifest` to apply the deterministic merge rules (preserve-non-null, rename-via-synonyms-append, drop-on-rename, seeded-entry gap-fill, carry-forward synonyms, entity-grouped `inputs:`).

**1. Build the inventory JSON.** For each row in the approved Name Inventory table(s), construct one inventory entry:

```json
{
  "name": "<approved snake_case Field Name>",
  "section": "inputs.<Entity>" | "computed" | "outputs",
  "policy_phrase": "<exact verbatim phrase from policy doc>" | null,
  "source_doc": "input/policy_docs/<rel>.md" | null,
  "section_text": "<§ citation> — <heading>" | null,
  "prior_name": "<previous specs key>" | null,
  "description": "<analyst- or AI-supplied>" | null,
  "type": "<money|bool|int|float|string|enum|list|set|date|object>" | null,
  "values": ["<a>", "<b>"] | null,
  "observed_synonyms": [
    {"name": "<alt-name>",
     "source_doc": "input/policy_docs/<rel>.md",
     "section": "<§ citation> — <heading>"}
  ] | null
}
```

Rules for building each entry:
- **`name`**: the analyst-approved Field Name from Step 3b (snake_case).
- **`section`**: `inputs.<EntityName>` for input fields (3-level structure); `computed` or `outputs` (flat).
- **`policy_phrase`**: the verbatim noun phrase from the source policy doc, scoped to the section the name was observed in. For `confirmed`/`seeded` rows where the analyst confirmed the name against an observation, fill from the observation. For `extracted`/`algorithm-derived` rows, derive per the verbatim rule in `core/naming_guide.md` lines 34–54 using the caveman-compressed source at `policy_facets/compressed/<rel>.md`. If no observation exists (seeded entry not confirmed this round), set to `null` — the merge tool preserves null provenance.
- **`source_doc`**: `input/policy_docs/<rel>.md` for the file the policy_phrase was observed in. `null` when policy_phrase is null.
- **`section_text`**: `"<§ citation> — <heading>"` from the section the policy_phrase was observed in. `null` when policy_phrase is null.
- **`prior_name`**: the prior specs key when the analyst renamed an entry in Step 3b (Source = `confirmed` with edited Field Name). `null` for non-renames and for new entries.
- **`description`, `type`, `values`**: optional analyst- or AI-supplied values. AI-infer `type:` from currency markers / yes-no phrasing / enumerated lists when the source carries an unambiguous signal. AI-infer `description:` from definitional sentences. Set to `null` to defer to whatever the existing entry has (preserve-non-null).
- **`observed_synonyms`**: optional. For curated alternative phrasings observed in policy text. Each entry has `name` (required), `source_doc` and `section` (recommended for traceability). Omit or set `null` when there are no curated synonyms this round.

Write the inventory list to a tempfile (e.g., `tempfile.NamedTemporaryFile(suffix='.json', mode='w')`), then close before passing the path.

**2. Run the merge tool:**

```bash
xlator merge-naming-manifest <domain> <program> --inventory <tmpfile>
```

The tool reads the existing `specs/naming-manifest.yaml`, applies the merge rules, and writes the merged manifest atomically. It emits a JSON header line on stdout followed by `--- MERGE-NAMING-MANIFEST-HEADER-END ---` and a human summary. Parse the JSON header for counters; relay the summary in `:::important`.

On non-zero exit: relay the tool's stderr in `:::error` and stop. The tool exits 1 on inventory schema violation (`ERROR: inventory[<N>].<field>: <reason>`) or pathological conflict (both `name` and `prior_name` exist as separate entries); exit 2 on missing domain or missing inventory file.

The tool enforces the load-bearing invariants from the prior prose version of Step 7:
- **Preserve-non-null:** for every entry being written, existing non-null fields win; inventory fills null fields. Seeded-entry provenance gap-fill is the same rule applied to `policy_phrase`/`source_doc`/`section`.
- **Rename via `synonyms:`-append:** when `prior_name` matches an existing key in the same section, the old entry is dropped and a `{name: <prior_name>}` rename-anchor synonym is appended (no `source_doc:`/`section:`). Idempotent on re-runs (skips append when the prior key is already in the carried synonyms list).
- **Carry-forward synonyms:** the new entry inherits the existing entry's full `synonyms:` list before the rename-anchor is appended; rename chains accumulate across multiple rename rounds.
- **`role_hint:` is never written** — section placement encodes role.
- **`inputs.<Entity>` is 3-level; `computed:` and `outputs:` are flat.**

The merged manifest is user-editable. Do **not** add an "auto-generated" comment.

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
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Written by `xlator merge-naming-manifest` (Step 7, after validation) |
| `$DOMAINS_DIR/<domain>/policy_facets/computations/<rel>.md.yaml` | Read-only (per-file section data; if present) |
| `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` | Read-only (canonical content for AI consumption) |
| `$DOMAINS_DIR/<domain>/specs/guidance/*.yaml` | Read (via `xlator load-extraction-context`) |
| `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml` | Read (via `xlator load-extraction-context`) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/review-ruleset`. Tests and transpilation are handled by `/create-tests` and `/transpile-and-test`.
