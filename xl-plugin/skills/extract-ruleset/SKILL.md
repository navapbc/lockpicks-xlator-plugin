---
name: extract-ruleset
description: Extract Ruleset from Policy Documents
---

# Extract Ruleset from Policy Documents

Create a Catala ruleset for a domain from documents in its `input/policy_docs/` subfolder.

## Input

```
/extract-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/extract-ruleset <domain> <program>                # target a specific <program>.catala_en
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–5),
the scoring rubric, shared procedures (SP-ComputeGraph, SP-GuidanceCapture, and others), and common mistakes.

Read `../../core/catala-authoring-quickref.md` now. It is the authoritative AI-targeted Catala reference — grammar excerpts, project idioms (literate Markdown citations, `catala-metadata` vs `catala` fence discipline, denial-reasons accumulation), and the six "AI failure modes to preempt" categories. Apply its idioms whenever emitting `.catala_en` content in Step 4.

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

2. **Catala file already exists?**
   - **If `<program>` was given:** check if `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` exists → if yes, redirect:
     :::error
     A ruleset already exists for <program>. To update it, run:
       /update-ruleset <domain> <program>
     :::
     Then stop. Continue if not found.
   - **If `<program>` was not given:** check `$DOMAINS_DIR/<domain>/specs/*.catala_en`:
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
   - `confirmed_exprs` — `{<variable>: <expression>}`. Used in Step 4 when emitting computed definitions.
   - `example_rules` — anchor block at the top of the main module's Catala draft.
   - `guidance_output_set` — list of variable names to be marked `output` (rather than `internal`) in Step 4.
   - `constants_tables_seed` — pre-seeds tables and constants in Step 4.
   - `per_module_sample_rules` — sub-module anchor blocks in Step 4 (multi-file).
   - `input_index_shas`, `guidance_shas` — used in Step 5 to populate `extraction-manifest.yaml`.
   - `work_list` — drives multi-file extraction iteration (sub-modules first, main module last; `action: generate | reference`; each entry carries `catala_file: specs/<name>.catala_en`).
   - `metadata`, `prompt_context`, `output_variables`, `input_variables`, `naming_manifest` — the full guidance docs for Step 1 internalization and Step 3b table pre-population.
   - `program` — resolved program name (from `ruleset-modules.yaml`'s `role: main` entry, the CLI arg, or single-`*.catala_en` auto-detection). When `null` and `candidate_programs` is non-empty, prompt the user to choose one.

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

### Step 2: Identify Catala Components

Map policy elements to Catala constructs (see `../../core/catala-authoring-quickref.md` for full grammar and idioms):

| Policy Element | Catala Construct |
|---|---|
| Entity inputs | `declaration structure <Entity>` with `data <field> content <Type>` |
| Eligibility outcome | `output <var> condition` (boolean) or `output <var> content <Enum>` (multi-valued) |
| Denial/approval explanations | `output reasons content list of <Reason>` accumulated via the denial-reasons idiom |
| Computed output value (e.g., adjusted_income) | `output <var> content money` with a `definition <var> equals ...` |
| Dollar thresholds by size | `declaration structure <TableRow>` + a lookup scope or list-of-records constant |
| Fixed rates/amounts | Scope-level `definition <name> equals <literal>` (acts as a named constant) |
| **Intermediate derived values** | `internal <var> content <Type>` |
| Income/asset test | `rule <condition_var> under condition <expr> consequence fulfilled` (deny rules use exception-default pattern) |
| Pass all tests → eligible | `rule <eligible_condition> under condition <combined expr> consequence fulfilled` |

The quickref's "AI failure modes to preempt" section documents the six categories that recur in AI emissions: cross-module type contracts, enum qualification, money/date literals, list operators, struct mode detection, and exception-default for deny rules. Read those before drafting.

After building the component map, run **SP-OrchestrationFilter** (from `../../core/ruleset-shared.md`) on the candidate rule components:
- Remove flagged components from the generate list.
- If any components were removed, display the SP-OrchestrationFilter summary table.
- Continue to Step 3 with the filtered list. Re-included components will have a Markdown comment added in the draft step.

### Step 3: Derive Program Name

If the pre-flight JSON payload's `program` field is populated (resolved from a `role: main` entry in `ruleset-modules.yaml` or auto-detected from a single `*.catala_en`), use that name directly — no inference or prompt needed.

Otherwise (`program` is null and no CLI arg):
1. Infer from the policy text (e.g., "SNAP income eligibility" → `eligibility`).
2. If ambiguous, prompt: "What should the program file be named? (e.g., `eligibility`, `income_test`)"

The program name becomes the filename basename. The Catala `> Module <ModuleName>` directive is derived **mechanically** from the basename (CamelCase, underscores preserved) — never confabulated. See `../../core/catala-authoring-quickref.md` Part 1 "File preamble and module name" for the binding rule.

### Step 3b: Name Inventory

**Multi-file:** Build one Name Inventory table per `generate` entry in the work-list (sub-modules first, main module last). Label each table `Name Inventory: <module_name>`. Display all tables together in a single presentation so the user can review cross-file naming at once, then confirm or adjust as a batch. For `reference` entries: skip (names are already set in the existing file).

**Single-file (ruleset_modules: empty):** produce one Name Inventory table as described below (existing behavior).

Before drafting any Catala source, produce the canonical field name for every fact and computed concept in the policy. For each measurable quantity, flag, or derived value found in the policy documents, apply this algorithm:

1. Find the **exact noun phrase** in the policy text describing the concept
2. Use specific field names to evoke the meaning without having to look up the corresponding policy text and minimize risk of name collisions in future extractions
3. **Strip** any words that duplicate the entity name (e.g., entity is `Household` → strip "household" from "household gross income" → `gross income`)
4. Convert to **`snake_case`**
5. If the result would be **ambiguous** with another field in the same entity, append a disambiguating qualifier from the policy text

Present the result as a Markdown table with these columns: **Policy Phrase**, **Entity / Section**, **Field Name**, **Source Doc**, **Source Section**, **Origin**. The **Origin** column is the origin-marker (seeded / confirmed / extracted / algorithm-derived); the **Source Doc** column is the source-doc path (always written as `input/policy_docs/<rel>.md`); the **Source Section** column is the verbatim section heading (`#` / `##` / `###` prefix preserved). Together the (Source Doc, Source Section) pair is the per-observation provenance the merge tool consumes in Step 7. **Row granularity is one row per observation** — multi-observation manifest entries produce multiple rows (one per `(source_doc, section)` pair), all sharing the same Field Name. This gives the analyst the full multi-source view at confirmation time.

:::detail
| Policy Phrase | Entity / Section | Field Name | Source Doc | Source Section | Origin |
|--------------|-----------------|-----------|-----------|----------------|--------|
| gross monthly income | Household | `gross_monthly_income` | input/policy_docs/income.md | # Income Calculation | extracted |
| gross household income | Household | `gross_monthly_income` | input/policy_docs/income.md | ## Income Sources | extracted |
| number of people in the household | Household | `household_size` | input/policy_docs/composition.md | # Composition | confirmed |
| net monthly income after all deductions | computed | `net_income` | input/policy_docs/income.md | ## Net Income | confirmed |
| `<seeded>` | outputs | `eligibility_status` |  |  | seeded |
:::

The **Origin** column distinguishes four values; row granularity is one-per-observation across all four:
- **`seeded`**: from the JSON payload's `naming_manifest` (analyst declared via `/declare-target-ruleset`). Each row corresponds to one entry in the manifest entry's `observations:` list. When an observation has a `policy_phrase`, render it in the Policy Phrase column and the observation's `section` in the Source Section column. When the observation has no `policy_phrase` (variable observed in a section but no verbatim phrase recorded), render `<seeded>` in Policy Phrase and the `section` in Source Section. **When a manifest entry has no `observations:` list at all** (synthesized output that was never observed in source), render one row with `<seeded>` in Policy Phrase and blank Source Doc + Source Section.
- **`confirmed`**: same shape as `seeded` but the manifest entry's observations were confirmed against a doc in a prior `/extract-ruleset` round. The distinction between `seeded` and `confirmed` is informational (origin marker); both read from the same `observations:` list.
- **`extracted`**: surfaced from per-file `*.md.yaml` files via the aggregation algorithm below — names from `expr_hint:` LHSes plus AI-scanned `description:` prose for descriptive-only computations, with per-observation provenance read from each section's `variables:` block. One row per `(source_doc, section)` pair.
- **`algorithm-derived`**: no prior entry and no per-file extraction surfaced the concept; derived directly from policy text via the algorithm above. Always single-row.

When the analyst-confirmed Field Name in Step 3b differs from a previously confirmed specs key (rename), the Origin column shows `confirmed` and the analyst-edited cell carries the new name; the rename is recorded in Step 7 by passing the prior specs key as `prior_name` in the inventory JSON (Step 7's merge tool appends it to the entry's `synonyms:` list as a `{name: <prior>}`-only entry).

**Pre-populate the table from three sources:**

1. **Manifest entries (`seeded` and `confirmed`):** Use the `naming_manifest` already loaded in the pre-flight JSON payload. For each entry:
   - Iterate the entry's `observations:` list. For each observation, emit one row: Field Name = the variable name key, Entity / Section = the entity key (e.g., `Household`) for `inputs:` entries or `computed` / `outputs` otherwise, Source Doc = the observation's `source_doc` field, Source Section = the observation's `section` field, Origin = `confirmed` (when the entry was confirmed in a prior round) or `seeded` (when the entry came from `/declare-target-ruleset` and has not yet been analyst-reviewed).
   - When the observation has a `policy_phrase` field, render it in the Policy Phrase column.
   - When the observation has no `policy_phrase` (phrase-absent observation), render `<seeded>` in the Policy Phrase column.
   - When the manifest entry has no `observations:` list at all (synthesized output), emit one row with Field Name = the variable name key, Entity / Section = the entity key, Policy Phrase = `<seeded>`, Source Doc = blank, Source Section = blank, Origin = `seeded`.

2. **Per-file aggregation (`extracted`):** For policy concepts not already covered by the manifest, walk every `*.md.yaml` under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and extract candidate names per the aggregation algorithm:
   - For each `sections[*].computations[*]` entry: if `expr_hint:` is present and well-formed (`output_name = <expression>`), the LHS is the computation's output name and the RHS is tokenized for snake_case identifier inputs (skip numeric/string literals and built-in keywords). For descriptive-only computations (no `expr_hint:`), AI-scan the entry's `description:` prose for variable names that mirror the source's verbatim noun phrases.
   - Each surfaced name's provenance comes from the section's `variables:` block (post-3.0): `policy_phrase` (when present in `variables:[<name>].policy_phrase`), `source_doc` (reconstituted as `input/policy_docs/<rel>.md` from the per-file file's relative path), and `section` (the section's `heading:` field). When the variable doesn't appear in the section's `variables:` block, the row has no `policy_phrase` — render `<seeded>` in the Policy Phrase column.
   - **Determinism rules** (apply uniformly across the aggregation, so re-runs produce stable inventories):
     - Dedup on the `(Field Name, source_doc, section)` triple — one row per observation triple. When the same variable appears in two sections of the same file, emit two rows.
     - Order rows lexicographically: by `source_doc`, then by section order within file, then alphabetically by Field Name within a section.
   - For each surfaced name not already covered by a specs entry, populate the row with **Origin = `extracted`**, Field Name = the snake_case name, Entity / Section = inferred from the per-file section's heading/summary plus the variable name itself (use the same heuristics as `/suggest-target-ruleset`'s entity-inference rule; fall back to `Case` when ambiguous).

3. **Algorithm-derived (fallback):** For policy concepts not covered by the manifest and not surfaced by the per-file aggregation, derive the name from policy text using the algorithm above. **Origin = `algorithm-derived`**. Always single-row.

When the manifest and the per-file aggregation both surface the same concept (matched case-insensitively by Field Name), the manifest wins — it is the analyst-confirmed authority. The per-file rows are suppressed for that Field Name (the manifest's own observation rows fully cover that variable).

:::user_input
Do the field names in this table match your intent? You may edit any name.
:::
If the user changes any name, update the table and re-present. Loop until the user explicitly approves. Use the approved names in Step 4 onward.

When a confirmed specs entry's Field Name is edited (rename), retain the prior specs key as the rename anchor for Step 7 (it is passed as `prior_name` in the inventory JSON). The per-file aggregation does not contribute to rename anchoring — anchors flow only through the existing specs entries themselves.

**Source-doc citation form for Step 4:** Catala captures source-doc provenance through **literate Markdown structure** — `## <Heading>` lines bracket fenced Catala blocks, and `*Source: <citation>*` italic-prose lines provide per-rule citations. Provenance survives compilation as `SourcePosition.law_headings` runtime metadata. See `../../core/catala-authoring-quickref.md` Part 3 "Source-doc citation form" for the exact pattern.

- Use the per-file section's `heading:` value (or, when missing, the citation plus a brief title like `"7 CFR § 273.9(a) — Income and Deductions"`) as the `## <Heading>` text immediately above each fenced block.
- Use an italic-prose `*Source: <file> — <citation>*` line between the heading and the fenced block. The `<file>` is the source-doc path relative to the domain root (always written as `input/policy_docs/<rel>.md`). The `<citation>` is the citation plus heading, e.g. `"7 CFR § 273.9(a) — Income and Deductions"`.
- When the policy document has no clear section for a given rule, omit the `*Source: ...*` line rather than guessing.

### Step 4: Draft the Catala Module

**Name binding:** Before writing any Catala source, re-read the approved Name Inventory table(s) from Step 3b. Use **only** those approved field names for every `data` declaration, scope variable (`input`/`internal`/`output`/`context`), `definition`, and `rule` consequence-binding — do not re-derive names from policy text.

**Multi-file:** Iterate the work-list in generation order (sub-modules first, main module last). For each `generate` entry, apply the full drafting logic below. For each `reference` entry, skip drafting entirely (the file is already on disk).

**Sub-module files:** Draft as a standard Catala module. Sub-module outputs that will be consumed by the parent module **must** be declared `output` (not `internal`) so they cross the scope boundary. Cross-module exports also require the declaring block to use the `catala-metadata` fence — see `../../core/catala-authoring-quickref.md` Part 3 "Fence visibility" for the rule.

**Main module with sub-modules:** Begin the file with `> Using <SubModuleName>` directives (one per imported sub-module). Reference sub-module scopes via `<sub_var> scope <SubModule.SubScopeName>` and consume their outputs via the standard scope-call pattern from the quickref. The sub-module names must match the CamelCase of the sub-module filename basenames.

**Single-file (ruleset_modules: empty):** existing behavior unchanged.

**If `example_rules` (from the JSON payload) is non-empty**, display those rules at the top of the Catala draft output for the **main module** (single-file path or main module in multi-file path) before emitting any new content:

```
# === User-approved example rules ===
# These rules were confirmed by the user. Use them as anchors for Catala
# structure, citation form, and naming style throughout this draft.
<catala: content of each example_rules entry>
# =========================================================
```

**Multi-file — sub-module anchor injection:** For each **sub-module** `generate` entry, look up the module's `name:` in the `per_module_sample_rules` map (from the JSON payload). If the list is non-empty, display it before emitting any new content for that sub-module:

```
# === User-approved example rules (module: <name>) ===
# These rules were confirmed by the user. Use them as anchors for Catala
# structure, citation form, and naming style throughout this sub-module draft.
<catala: content of each per_module_sample_rules[<name>] entry>
# =========================================================
```

If a sub-module's per-module list is empty, skip the anchor block for that module.

**When emitting `definition <var> equals <expr>`**, check `confirmed_exprs` (from the JSON payload) first:
- If the variable name appears in the map, use its value (translating from the guidance expression form to Catala syntax) and add the Markdown comment `<!-- expr confirmed in /refine-guidance -->` immediately above the fenced block.
- For variables not in the map, infer the expression from policy text as normal.

Additionally, check `guidance_output_set` (from the JSON payload): if the variable name is in the set, declare it as `output <var>` (rather than `internal <var>`) in the scope-declaration block. `output` variables cross the scope boundary and are visible to callers and explanations.

**When emitting tables and constants**, if `constants_tables_seed` (from the JSON payload) is non-empty, begin with the seeded entries before drafting from policy text:
- For each entry in the seed list, infer whether it is a table-lookup (multiple key→value rows) or a single named value from its `name:` and `description:` (keywords like "thresholds", "limits", "by household size", "lookup" → lookup pattern; "fixed", "rate", "percentage", "flat amount" → single value).
- **Lookup pattern:** declare a `structure <TableRow>` for the row schema in a `catala-metadata` fence, then emit a `definition <table_name> equals [ <row>, ... ]` in a `catala` fence with rows derived from policy text. Add a Markdown comment line `<!-- pre-seeded from guidance/constants-and-tables.yaml -->` immediately above the fence. If no matching policy text is found, include the structure but leave the list empty and add `<!-- not found in policy — verify manually -->`.
- **Single named value:** emit a scope-level `definition <NAME> equals <value>` with its value filled from policy text. Add the Markdown comment `<!-- pre-seeded from guidance/constants-and-tables.yaml -->` immediately above the fence. If no value is found in policy text, leave the definition placeholder-only and add `<!-- not found in policy — verify manually -->`.
- **Source-doc citation from seed provenance:** populate the `## <Heading>` and `*Source: ...*` lines directly from the seed entry's `source_file:` and `source_section:` — both are guaranteed present by `/create-skeleton`. Use the seed `source_file:` value verbatim for the file path (already in the `input/policy_docs/<rel>.md` form) and the seed `source_section:` verbatim for the section citation. Do not re-derive citations from policy text for seeded entries.
- After all seeded entries, append any additional tables or constants found in policy text that were not in the seed list.

Create `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` (or the corresponding `catala_file` from the `work_list`). Structure the file as literate Markdown with Catala fenced blocks:

````markdown
> Module <ModuleName>   # CamelCase of the filename basename, mechanically derived

# <Program display name>

<!-- File-level provenance is captured through the per-section `## <Heading>` lines
     and `*Source: ...*` italic-prose lines below, not through a separate metadata block. -->

## Declarations

```catala-metadata
declaration structure <Entity>:
  data <field> content <Type>
  data <other_field> content <OtherType>

declaration scope <ScopeName>:
  input <entity_var> content <Entity>
  internal <intermediate_var> content <Type>
  output <result_var> content <Type>
  output <result_condition> condition
```

## <Section heading from policy doc>

*Source: input/policy_docs/<rel>.md — <§ citation> — <heading>*

```catala
scope <ScopeName>:
  definition <var> equals <expression>
```

## <Another section>

*Source: input/policy_docs/<rel>.md — <§ citation> — <heading>*

```catala
scope <ScopeName>:
  rule <condition_var>
    under condition <bool expression>
    consequence fulfilled
```
````

**Output-typing cases:**
- **`bool` (default)** — declare `output <name> condition` and emit a rule `under condition count of reasons = 0 consequence fulfilled`. The denial-reasons accumulation idiom (see quickref Part 3) accumulates a list of `Reason` records via filter/map over each test's outcome.
- **`enum`** — declare an enumeration in a `catala-metadata` fence, then `output <name> content <Enum>` with a `definition <name> equals ...` over the test outcomes. Mention each enum constructor with its full qualifier (`Enum.Constructor`) per quickref Part 2 "Enum qualification".
- **money/int/float** — declare `output <name> content money` (or `integer`/`decimal`) and provide a `definition <name> equals <expression>`.

**Money and date literals** follow Catala's `$N,NNN.NN` and `|YYYY-MM-DD|` forms — see quickref Part 2. AI emissions that use OCaml-style `Money.of_int` or Python-style `date(...)` calls will fail typecheck.

**Exception-default for deny rules:** the canonical "default true, override to false on any deny condition" pattern uses exception-priority. See quickref Part 2 "Exception-default for deny rules" for the boilerplate.

**Scoring:** Assign `review:` blocks to every rule and computed-value definition as you draft them. Use the Scoring Rubric from `../../core/ruleset-shared.md`. Catala has no native annotation field, so emit `review:` data as a Markdown HTML comment block immediately above the fenced block:

```markdown
<!-- review:
       extraction_fidelity: <1-5>
       source_clarity: <1-5>
       logic_complexity: <1-5>
       policy_complexity: <1-5>
       notes: "<explain any score ≤2 or ≥4>"
-->
```

Write scores while the source policy text is in context — do not defer to a separate pass.

**Reference:** See `../../core/catala-authoring-quickref.md` for the full grammar, project idioms, and AI-failure-modes-to-preempt checklist.

### Step 4b: Maintainability Self-Review

*Runs after Step 4 (Draft), before Step 5 (Write Extraction Manifest).*

**Multi-file:** Run SP-MaintainabilityReview once per `generate` entry, immediately after that file is drafted (not after all files). Label the SP-MaintainabilityReview output:
```
Maintainability Self-Review: <module_name>
```

**Single-file:** Run SP-MaintainabilityReview on the single drafted file.

Run **SP-MaintainabilityReview** (from `../../core/ruleset-shared.md`) on the drafted Catala file. SP-MaintainabilityReview applies in-place fixes where the fix is mechanical and reports the findings. On any blocking item that cannot be auto-fixed, surface it as a `:::user_input` fence with the conflicting rules and ask for resolution before advancing.

Proceed to Step 5 only after SP-MaintainabilityReview passes (no blocking failures).

### Step 5: Write Extraction Manifest

**Single-file (ruleset_modules: empty):** create `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` in single-file format:

```yaml
# Auto-generated by /extract-ruleset — do not edit manually
programs:
  <program>:
    catala_file: $DOMAINS_DIR/<domain>/specs/<program>.catala_en
    extracted_at: "YYYY-MM-DD"
    source_docs:
      - { path: "input/policy_docs/<rel>.md", git_sha: "<sha>" }
    consumed_guidance:
      - { path: "specs/guidance/<file>.yaml", sha: "<sha>" }
      - { path: "specs/naming-manifest.yaml", sha: "<sha>" }
```

**Multi-file (ruleset_modules: non-empty):** write using the multi-file format. For each `reference` entry in the work-list, set `referenced: true` in its `sub_modules:` entry; for `generate` entries, set `referenced: false`. Each sub-module entry also carries its own `consumed_guidance:` block using the same `{path, sha}` shape — populate it identically to the parent program's block (sub-modules consume the same guidance set as the parent in v1).

For each `source_docs:` entry, read the SHA from the `input_index_shas` map in the pre-flight JSON payload, keyed on the entry's `path:` (`input/policy_docs/<rel>.md`). Write that value verbatim into `git_sha:`. Do not run `git hash-object` here — the pre-flight tool already validated drift, so the indexed SHA matches the bytes being extracted.

For each `consumed_guidance:` entry, read the SHA from the `guidance_shas` map in the pre-flight JSON payload, keyed on the entry's `path:` (`specs/guidance/<file>.yaml` or `specs/naming-manifest.yaml`). Enumerate every path that appears in the map — the resulting list reflects the full state of the guidance tier at extract time. When the map is empty (no `specs/guidance/*.yaml` files), write `consumed_guidance: []`.

### Step 6: Verify Catala source with the clerk loop

Run the multi-module clerk-loop orchestrator over every module in the work-list. The orchestrator drives `clerk typecheck` + `clerk test` per module (with the per-iteration in-loop naming-manifest check bypassed, since the per-file check cannot converge against a manifest shared across siblings), then runs a single **aggregated** naming-manifest divergence check across the union of identifiers from every module. Single-module and multi-file invocations both go through this script.

**Invocation:**

```bash
xlator clerk-loop-multi <domain> [<program>]
```

The script emits a JSON header line on stdout followed by `--- CLERK-LOOP-MULTI-HEADER-END ---` and a human-readable summary. Parse the JSON header (first stdout line); verify the sentinel on the second line. Header shape:

```json
{
  "status": "ok" | "unresolved" | "error",
  "mode": "full",
  "modules_checked": <int>,
  "modules_generated": <int>,
  "iterations_per_module": [{"module": "<name>", "iterations": <int>}, ...],
  "failed_module": null | "<name>",
  "verified_modules": ["<name>", ...],
  "diagnostic_count": <int>,
  "warnings": ["<msg>", ...]
}
```

Branch on `header.status`:

- **`status="ok"`** — every per-module clerk loop passed and the aggregated divergence check reported no mismatch. Surface a `:::important` summary that names the modules verified and the per-module iteration counts (from `iterations_per_module`), then continue to Step 7.

- **`status="unresolved"`** — either a per-module loop halted (`header.failed_module` is set; surface the diagnostics listed under the sentinel) or the aggregated divergence check flagged one or more mismatches. Emit a `:::user_input` fence containing the human summary and the diagnostic block. Ask the analyst how to proceed:
  - **Naming divergence**: each diagnostic carries both resolution options in its message body — (a) hand-edit the affected Catala source(s) to add or rename the identifier, or (b) edit `specs/naming-manifest.yaml` to update or remove the entry. Apply the chosen resolution, then re-run Step 6. (Per-fenced-block regeneration is no longer applicable — divergence is detected as a post-pass after every per-module loop has completed.)
  - **Per-module clerk failure** (`header.failed_module` is set): the listed diagnostics come from the failing module's last `clerk typecheck` / `clerk test` iteration. Hand-edit the source, then re-run Step 6. `header.verified_modules` lists the modules whose per-module loops completed before the halt.

  Do not proceed to Step 7 until the script returns `status="ok"`.

- **`status="error"`** (exit 2) — pre-flight failure (missing domain, missing `specs/naming-manifest.yaml`, missing `clerk` on PATH, or a `load-extraction-context` failure). Surface the human summary in `:::error` and stop.

Operational note: each per-module loop calls `catala_runtime.reset_log()` between iterations by default (PR #45 prevention); the orchestrator inherits this behavior unchanged.

### Step 7: Write Naming Manifest

Build the analyst-approved Name Inventory from Step 3b as an inventory JSON file, then call `xlator merge-naming-manifest` to apply the deterministic merge rules (preserve-non-null, rename-via-synonyms-append, drop-on-rename, seeded-entry gap-fill, carry-forward synonyms, entity-grouped `inputs:`).

**1. Build the inventory JSON.** Emit **one inventory entry per canonical variable name** — the entry carries the full `observations:` list inline (single-element for single-source variables, multi-element for multi-source). Multi-observation rows in the Name Inventory table from Step 3b collapse back into one inventory entry per Field Name at JSON-build time.

```json
{
  "name": "<approved snake_case Field Name>",
  "section": "inputs.<Entity>" | "computed" | "outputs",
  "observations": [
    {
      "policy_phrase": "<exact verbatim phrase from policy doc>",
      "source_doc": "input/policy_docs/<rel>.md",
      "section": "<verbatim heading including # / ## / ### prefix>"
    }
  ] | null,
  "prior_name": "<previous specs key>" | null,
  "description": "<analyst- or AI-supplied>" | null,
  "type": "<integer|decimal|money|boolean|date|duration|string|enum|list|structure>" | null,
  "optional": true | false | null,
  "values": ["<a>", "<b>"] | null,
  "enum_variants": ["<Variant1>", "<Variant2>"] | null,
  "observed_synonyms": [
    {"name": "<alt-snake_case-name>"}
  ] | null
}
```

Rules for building each entry:
- **`name`**: the analyst-approved Field Name from Step 3b (snake_case).
- **`section`**: `inputs.<EntityName>` for input fields (3-level structure); `computed` or `outputs` (flat).
- **`observations`**: list of observation triples gathered from all Name Inventory rows that share this Field Name. Each observation is:
  - `policy_phrase:` — *optional* verbatim noun phrase from the source body for this observation. Omit when the row shows `<seeded>` in the Policy Phrase column (phrase-absent observation — variable was seen in a section but no verbatim phrase exists). The merge tool's per-observation invariant requires `source_doc` + `section` paired-or-absent and `policy_phrase` independently optional.
  - `source_doc:` — `input/policy_docs/<rel>.md` for the file the observation came from.
  - `section:` — the verbatim section heading (including `#` / `##` / `###` prefix).
  - Set the whole list to `null` to defer to whatever observations the existing entry already has (preserve-non-null is union semantics at the list level — see Step 7's merge invariants below).
  - Set to `[]` to commit "no observations this round" but propose nothing — the merge tool will preserve any existing observations.
- **`prior_name`**: the prior specs key when the analyst renamed an entry in Step 3b (Origin = `confirmed` with edited Field Name). `null` for non-renames and for new entries.
- **`description`, `type`, `optional`, `values`, `enum_variants`**: optional analyst- or AI-supplied values. AI-infer them from policy-doc context plus the inferred Catala scope-declaration shape:
  - `type:` — Catala-native (`integer`, `decimal`, `money`, `boolean`, `date`, `duration`, `string`, `enum`, `list`, `structure`) from currency markers ("$", "dollars" → `money`), yes/no phrasing → `boolean`, enumerated lists → `enum`, calendar dates → `date`, repeated values → `list`, compound records → `structure`, and so on. The merge tool rejects any other value.
  - `optional:` (U7, post-pivot) — `true` when the field is `Optional<T>` in the Catala emission (the policy text treats the field as optional, or the AI declared the scope variable as `Optional`). `false` for required fields. `null` to defer to the existing entry.
  - `values:` — allowed-values list for `enum`-typed entries (see `core/naming_guide.md`).
  - `enum_variants:` (U7, post-pivot) — list of Catala enum constructor names (PascalCase, e.g. `["Eligible", "Denied", "ManualVerification"]`) when the field is an enum type. Distinct from `values:`; analysts/AIs supply this field for any enum-typed entry in the post-pivot manifest.
  - `description:` — short prose definition from the source policy text.
  - Set any field to `null` to defer to whatever the existing entry has (preserve-non-null).
- **`observed_synonyms`**: optional. For curated alternative *variable-name identifiers* observed in policy text (e.g., the source uses "wages" alongside "gross_income" for the same concept). Each entry is `{name: "<alt-snake_case-name>"}` only — per-synonym `source_doc` / `section` were retired in v3.0; phrase-level provenance lives in `observations:`. Omit or set `null` when there are no curated synonyms this round.

**Type-metadata confirmation (U7).** Before constructing the inventory, the analyst confirms not only field names but also each field's Catala type, optionality, and enum variants (when applicable). The Name Inventory table from Step 3b is augmented with three additional columns — **Type**, **Optional**, **Variants** — populated as follows:

- **`seeded` / `confirmed`** rows: pre-populate from the existing manifest entry's `type:` / `optional:` / `enum_variants:`. Display `<unset>` for any field absent on the manifest entry.
- **`extracted` / `algorithm-derived`** rows: AI-infer from policy-doc context. Show inferred values; mark uncertain inferences with a `?` suffix so the analyst can flag them.

Re-display the augmented table in a second `:::user_input` fence and ask "Do the inferred types, optionality, and enum variants match your intent?" — loop until approval. The approved Type / Optional / Variants values flow into the inventory JSON's `type:` / `optional:` / `enum_variants:` fields. Any field the analyst marks as "leave unset" maps to `null` so the merge tool's preserve-non-null rule applies.

Write the inventory list to a tempfile (e.g., `tempfile.NamedTemporaryFile(suffix='.json', mode='w')`), then close before passing the path.

**2. Run the merge tool:**

```bash
xlator merge-naming-manifest <domain> <program> --inventory <tmpfile>
```

The tool reads the existing `specs/naming-manifest.yaml`, applies the merge rules, and writes the merged manifest atomically. It emits a JSON header line on stdout followed by `--- MERGE-NAMING-MANIFEST-HEADER-END ---` and a human summary. Parse the JSON header for counters; relay the summary in `:::important`.

On non-zero exit: relay the tool's stderr in `:::error` and stop. The tool exits 1 on inventory schema violation (`ERROR: inventory[<N>].<field>: <reason>`) or pathological conflict (both `name` and `prior_name` exist as separate entries); exit 2 on missing domain or missing inventory file.

The tool enforces the load-bearing invariants (post-3.0):
- **Preserve-non-null (scalars):** for every entry being written, existing non-null fields win; inventory fills null fields. Applies to `description`, `type`, `optional`, `values`, `enum_variants`.
- **Observations union semantics:** when both existing and inventory carry `observations:` lists, the merged list is the union deduplicated on the `(policy_phrase, source_doc, section)` triple, existing-first order. Adding a new policy doc section between rounds adds new observations without discarding the old — analysts gain observations across rounds rather than overwriting them.
- **Per-observation invariant:** each observation requires `source_doc` and `section` together when present (or both absent); `policy_phrase` is independently optional. Relaxes the prior all-or-nothing rule to support phrase-absent observations.
- **Rename via `synonyms:`-append:** when `prior_name` matches an existing key in the same section, the old entry is dropped and a `{name: <prior_name>}` synonym is appended. Idempotent on re-runs (skips append when the prior key is already in the carried synonyms list). In v3.0, all synonym entries are `{name}`-only; the rename-anchor / observed-phrasing-synonym distinction collapsed.
- **Carry-forward synonyms:** the new entry inherits the existing entry's full `synonyms:` list before the rename-anchor is appended; rename chains accumulate across multiple rename rounds.
- **`role_hint:` is never written** — section placement encodes role.
- **`inputs.<Entity>` is 3-level; `computed:` and `outputs:` are flat.**
- **Manifest version gate:** the tool rejects manifests whose `version:` is not exactly `"3.0"` (including missing) with exit code 1 and a clear regenerate-instructions message. See the merge tool's error path; analysts hit this when running against a v2.0 (or pre-version) manifest and must follow the Migration Path in the plan's Scope Boundaries.

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
| `$DOMAINS_DIR/<domain>/specs/<sub_module>.catala_en` | Created (for each generated sub-module, if ruleset_modules: non-empty) |
| `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` | Created |
| `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` | Created (multi-file format if ruleset_modules: non-empty) |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Written by `xlator merge-naming-manifest` (Step 7, after Step 6 verification) |
| `$DOMAINS_DIR/<domain>/policy_facets/computations/<rel>.md.yaml` | Read-only (per-file section data; if present) |
| `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` | Read-only (canonical content for AI consumption) |
| `$DOMAINS_DIR/<domain>/specs/guidance/*.yaml` | Read (via `xlator load-extraction-context`) |
| `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml` | Read (via `xlator load-extraction-context`) |

Graph artifacts (`.graph.json`, `-depgraph.dot`, `-depgraph.mmd`) and guidance updates are written by `/review-ruleset`. Tests are handled by `/create-tests`.
