---
name: extract-sample-rules
description: Extract Sample Rules
---

# Extract Sample Rules

Generate a comprehensive set of relevant CIVIL rules from the per-file files under `policy_facets/computations/` based on the `guidance/` folder and write them into `guidance/ruleset-modules.yaml`, `guidance/sample-artifacts.yaml`, and `naming-manifest.yaml`. Runs non-interactively — no mid-run prompting. Suitable for automated UI invocation.

Unlike `/refine-guidance` Step 8, which produces 2–3 illustrative rules gated behind user approval, this command generates as many rules as the index supports and writes them immediately for user review.

**Recommended run order:** After `/create-ruleset-modules`. The quality of the output depends on how complete the guidance files are at invocation time:

| Guidance state | Impact on output |
|---|---|
| `guidance/ruleset-modules.yaml` populated (after `/create-ruleset-modules`) | Rules routed to the correct ruleset module's `sample_rules:` — full structural grouping |
| `guidance/ruleset-groups.yaml` present but no `ruleset-modules.yaml` (after `/create-ruleset-groups`) | Stage context available but all rules fall into the top-level `sample_rules:` in `sample-artifacts.yaml` |
| `guidance/skeleton.yaml` present but no groups or ruleset modules (after `/create-skeleton`) | Computation ordering and category context available; rules still fall into the top-level `sample_rules:` |
| No `skeleton.yaml` or `ruleset-modules.yaml` (after `/declare-target-ruleset` only) | Command runs but produces flat, unstructured output with no ordering context |

The command prints a warning when `skeleton:` or `ruleset_modules:` is absent (see Step 2). It does not stop — partial output is better than none.

## Input

```
/extract-sample-rules [<domain>] [<rule_topic>] [index-only]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance/metadata.yaml` files as a numbered menu and prompt:

:::user_input
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
:::

`<rule_topic>` is an optional free-text filter (e.g., `"earned income"`, `"student exclusion"`). When provided, limit rule generation to index entries whose `heading:`, `summary:`, or `tags:` relate to the topic (case-insensitive keyword match). Report skipped entries at the end.

`index-only` is an optional literal keyword (third positional argument). When provided, only entries whose `computations[]` all have `expr_hint:` present are processed; entries that require reading source files are skipped entirely. Pass 4b does not run. Use this when you want fast, index-derived `computed:` rules without waiting for source reads.

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **Third positional argument provided but not `index-only`?**
   - YES → Print:
     :::error
     Unrecognized argument: '<value>'. Did you mean: index-only?
     :::
     Stop.

4. **`guidance/metadata.yaml` exists?**
   - NO → Print:
     :::error
     guidance/metadata.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml
     Run /suggest-target-ruleset <domain> first.
     :::
     Stop.

5. **Per-file computations present?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - ABSENT or empty → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first.
     :::
     Stop.

---

## Process

### Step 1: Load canonical names

Run **SP-LoadNamingManifest** (from `../../core/ruleset-shared.md`). The resulting lookup map is used in Step 4 to prefer canonical names over freshly inferred ones. If absent, the manifest will be created in Step 6.

Show step checklist:
:::progress
Steps:
  [✓] 1. Load canonical names
  [ ] 2. Load and filter index
  [ ] 3. Read guidance context and classify entries
  [ ] 4. Generate rules (two-pass)
  [ ] 5. Merge into guidance files
  [ ] 6. Write naming-manifest.yaml
:::

### Step 2: Load and filter per-file computations

Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML list of section blocks. Concatenate all entries into a single working list, deriving the `path:` field per entry from the file's relative location (a section in `policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md`). Filter the working list to entries that have a non-empty `computations:` field (at least one computation entry). Proceed regardless.

**If `<rule_topic>` was provided:** further filter to entries whose `heading:`, `summary:`, or `tags:` contain the topic keywords (case-insensitive). If no entries match the topic, print:

```
No index entries found related to '<rule_topic>'.
Available tags: [tag1, tag2, ...]
```

Then stop.

Print: `Found N qualifying index entries` (or `Found N qualifying entries matching '<rule_topic>'`).

Show updated step checklist (as `:::progress`).

### Step 3: Read guidance context and classify entries

Read `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` (`role:`), `$DOMAINS_DIR/<domain>/specs/guidance/variables.yaml` (`output_variables`), and optionally `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` and `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` to produce a **prioritized working set** of entries for Step 4. The working set is an ordered list derived from the qualifying entries found in Step 2, with entries clearly unrelated to the ruleset's purpose removed and logged before further processing.

Check for missing context and print warnings if applicable:

```
⚠ guidance/skeleton.yaml not found — computation ordering and category groupings unavailable.
  Run /create-skeleton <domain> first for better-structured output.

⚠ guidance/ruleset-modules.yaml not found — all rules will be written to sample-artifacts.yaml (no ruleset module grouping).
  Run /create-ruleset-modules <domain> first for structured rule routing.
```

Print only the warnings that apply. Proceed regardless.

**`role:`** — The stated purpose of the ruleset (e.g., `"Determine SNAP eligibility and benefit amount"`). For each qualifying entry, judge whether its `heading:` or `summary:` is plausibly related to that purpose. If an entry is clearly unrelated (e.g., it covers a separate program or administrative procedure with no variable overlap), remove it from the working set and log: `⚠ Skipped (unrelated to role): "<heading>"`. When in doubt, keep the entry — err toward inclusion.

If `role:` is absent, keep all qualifying entries in the working set.

**`skeleton:`** — The ordered list of computation categories and their members. For each entry remaining in the working set:
- If the entry's `computations[].variables[]` include one or more variables mentioned in `skeleton:`, mark it **high priority**.
- If none of the entry's variables appear in `skeleton:`, mark it **low priority** — it may represent auxiliary or supporting policy text.

Use the skeleton category labels (e.g., `income`, `deductions`, `benefit_amount`) in Step 4 to focus `categorical:` and `table-lookup:` rule drafting on the correct domain concepts.

If `skeleton:` is absent, mark all working set entries as normal priority.

**Pre-classify entries**

For each entry remaining in the working set, classify based solely on `expr_hint:` completeness in the index:

- **`computed-only`**: all `computations[]` entries have `expr_hint:` present. Rules can be generated from index data alone in Pass 4a, without reading the source policy document.
- **`needs-source`**: any `computations[]` entry is missing `expr_hint:`, or `computations[]` is empty. These entries require reading the source policy document in Pass 4b.

Tag each entry in the working set with its class. Classification runs regardless of whether `role:` or `skeleton:` is present — it depends only on index data.

Note: heuristic signals (table/schedule keywords, conditional language in `tags:` or `summary:`) are **not** used for classification here. They are checked inside Pass 4a to decide whether a `computed-only` entry should also be queued for Pass 4b processing.

Print a summary:
:::detail
Working set: N entries (M high priority, K normal, L low priority)
  Classified: C computed-only, S needs-source
Skipped: P entries (unrelated to role)
:::

Show updated step checklist (as `:::progress`).

### Step 4: Generate rules (two-pass)

Rules are generated in two passes. **These passes are strictly sequential and must never be combined into a single write.** Pass 4a processes `computed-only` entries using index data alone and writes output immediately so the user can review rules while Pass 4b runs. Pass 4b processes `needs-source` entries (and any `computed-only` entries queued by heuristic signals) by reading source documents, then merges again. The merge schemas are defined in Steps 5 and 6 below.

---

**Pass 4a — Index pass**

For each `computed-only` entry in the working set, processed in priority order (high → low when `skeleton:` is present, or in index order when it is absent), then within each priority group in the entry order produced by globbing `policy_facets/computations/**/*.md.yaml` alphabetically and concatenating each file's section list:

**(a) Determine canonical variable names.** For each variable name in the entry's `computations[].variables[]` list, apply the two operations from SP-LoadNamingManifest in order: (1) keyed lookup — if the variable name matches a map key, use that manifest name; (2) concept matching — if no keyed match, scan map values for a `policy_phrase` that closely matches the entry's `computations[].description` text, and use that entry's variable name if found. Only if neither operation finds a match, derive a snake_case name from the entry's `computations[].description` text:
- Extract the noun phrase from the description
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(b) Generate `computed:` rules.** For each `computations[]` entry, produce a `computed:` rule snippet using:
- Canonical output variable name
- `expr_hint:` as the `expr:` value, substituting canonical names for any input variable names
- `source:` from `computations[].description` (if `description` is absent, use `expr: "?"` and add to `missing_info`: `"No description for <variable_name> — expr and source must be confirmed manually"`)

**(c) Check heuristic signals.** Scan the entry's `tags:` and `summary:` for these keywords (case-insensitive):
- Table/schedule keywords: `table`, `schedule`, `threshold`, `limit`
- Conditional language: `if`, `unless`, `when`, `except`, `eligibility`

If any signals are present, add this entry to the Pass 4b queue for `categorical:` and `table-lookup:` rule generation. Do not read the source file now.

**(d) Assign to ruleset module or main.** For each generated rule, determine the best matching entry in `guidance/ruleset-modules.yaml`:
- Match by variable name overlap (variables in the rule appear in the ruleset module's description) or section heading keyword overlap with the ruleset module's `description:`. Only match against sub-module entries (entries where `role:` is absent or `sub`) — do not route to the `role: main` entry during this matching step.
- If a clear match is found, assign to that sub-module's `sample_rules:` list in `ruleset-modules.yaml`.
- If no sub-module match is found: check whether `ruleset-modules.yaml` has an entry with `role: main`. If yes, assign the rule to that entry's `sample_rules:` list (locate the entry by its `name:` value). If no `role: main` entry exists, assign to the top-level `sample_rules:` in `sample-artifacts.yaml` as a fallback.

After processing all `computed-only` entries:

**`index-only` + zero computed-only entries check:** If `index-only` is set and no `computed-only` entries were found, print:
:::error
⚠ index-only mode: no computed-only entries found — nothing to generate.
  Remove index-only or add expr_hint: fields to the index.
:::
Stop.

**Write Pass 4a output:** Merge rules into `guidance/ruleset-modules.yaml` and `guidance/sample-artifacts.yaml` (Step 5 merge schema) and merge variable entries into `naming-manifest.yaml` (Step 6 merge schema), with index-derived field values:
- `path:` (from index entry) → `source_doc`
- `heading:` (from index entry) → `section`
- `computations[].description` → `policy_phrase` (if absent, omit the entry from naming-manifest for that variable and add to `missing_info`)

Write both files now. **Do not begin Pass 4b until both files have been written to disk.**

Print Pass 4a Summary (see [Summary](#summary)). **Do not begin Pass 4b until the Pass 4a Summary has been printed.**

Show updated step checklist.

If `index-only`: stop. Do not run Pass 4b.

---

**Pass 4b — Source pass**

Process all `needs-source` entries, then any `computed-only` entries added to the Pass 4b queue. Within each group, process in priority order (high → normal → low), then in the entry order produced by globbing `policy_facets/computations/**/*.md.yaml` alphabetically and concatenating each file's section list.

**(a) Read source text.** Locate the source file at `path:` and navigate to the section identified by `heading:`. Read that section's text.

- If the file at `path:` does not exist: log `⚠ Source not found: <path> — skipping entry` and add to `missing_info`. Continue to the next entry.
- If the heading cannot be located in the file: log `⚠ Heading not found: "<heading>" in <path> — skipping entry` and add to `missing_info`. Continue.

**(b) Determine canonical variable names.** For each variable name in the entry's `computations[].variables[]` list, apply the two operations from SP-LoadNamingManifest in order: (1) keyed lookup — if the variable name matches a map key, use that manifest name; (2) concept matching — if no keyed match, scan map values for a `policy_phrase` that closely matches the policy text, preferring entries whose `source_doc` and `section` match the current document, and use that entry's variable name if found. Only if neither operation finds a match, derive a snake_case name from the policy text using the Name Inventory algorithm:
- Extract the exact noun phrase from the policy text
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(c) Generate rules.** For each computation hint in the entry, produce one or more CIVIL rule snippets:

- **`computed:` rule** — for `needs-source` entries with an `expr_hint:`: produce a `computed:` snippet using the canonical output variable name and the expr_hint as the `expr:` value. For `computed-only` entries in the Pass 4b queue: **skip `computed:` rules** — already written in Pass 4a.
- **`computed:` rule (no expr_hint)** — for `needs-source` entries where no `expr_hint:` is given: produce the snippet with `expr: "?"` as a placeholder. Record the variable in `assumptions:` ("No expr_hint available for `<name>` — expr must be confirmed manually").
- **`categorical:` rules** — scan the source text for conditional policy statements (if/then, eligibility conditions, deny/approve triggers). For each, draft a `rules:` entry with `when:` and `then:` blocks using canonical variable names.
- **`table-lookup:` rule** — if the source text references a table or schedule of thresholds, draft a `computed:` entry using `table_lookup:` syntax with `table:` and `key:` fields.
- **`invoke:` rule** — if the source text's computation calls for running a ruleset module, and `ruleset_modules:` in `guidance/ruleset-modules.yaml` has a matching entry, draft a `computed:` entry with `invoke:` and `with:` fields using the ruleset module's `name:` and canonical variable bindings.

**(d) Assign to ruleset module or main.** Same logic as Pass 4a sub-step (d).

**(e) Record notes.** Track:
- Any referenced value not found in the index or source text → add descriptive string to `missing_info`
- Any inferential leap or assumption → add descriptive string to `assumptions`
- Any low-priority entry from Step 3 for which rules were generated → add to `assumptions`: `"<heading> not in skeleton — rule may be auxiliary or out of scope; confirm before use"`

After processing all Pass 4b entries: merge rules into `guidance/ruleset-modules.yaml` and `guidance/sample-artifacts.yaml` (Step 5 merge schema) and merge updated variable entries into `naming-manifest.yaml` (Step 6 merge schema), overwriting any index-derived `policy_phrase` values with source-text values where available. Write all files.

Print Full Summary (see [Summary](#summary)).

Show updated step checklist (all steps complete — both files written during Pass 4a and updated during Pass 4b).

### Step 5: Merge schema — `guidance/ruleset-modules.yaml` and `guidance/sample-artifacts.yaml`

> This schema is applied from within Step 4 after each pass. It is documented here as the canonical reference.

Apply all merges without clobbering existing content:

**`guidance/ruleset-modules.yaml` — `ruleset_modules[].sample_rules:` (merge by `id:`):**
For each ruleset module entry that has assigned rules, add a `sample_rules:` sub-key if absent, then append rules whose `id:` is not already present. Do not overwrite or remove existing entries.

Rule entry schema:
```yaml
sample_rules:
  - id: <snake_case_identifier>
    rule_type: computed | categorical | table-lookup
    source: "<quoted sentence from the section's summary in policy_facets/computations/<rel>.md.yaml>"
    civil: |
      <full CIVIL YAML snippet>
```

**`guidance/sample-artifacts.yaml` — `sample_rules:` (merge by `id:`):**
Append unmatched rules (those not assigned to any ruleset module) to the top-level `sample_rules:` list. If the file does not exist, create it with a `sample_rules:` key. Deduplicate by `id:`.

**`guidance/sample-artifacts.yaml` — `missing_info:` (merge — append unique strings):**
Add new unique strings to the `missing_info:` list. If the key does not exist, add it. Do not remove or overwrite existing entries.

**`guidance/sample-artifacts.yaml` — `assumptions:` (merge — append unique strings):**
Add new unique strings to the `assumptions:` list. Place after `missing_info:`. Do not remove or overwrite existing entries.

### Step 6: Merge schema — naming-manifest.yaml

> This schema is applied from within Step 4 after each pass. It is documented here as the canonical reference.

**If `naming-manifest.yaml` already exists:**
Read it. For each variable name used in the generated rules, route by whether the variable appears in `guidance/variables.yaml`'s `output_variables` list:
- **Output variable** (name is in `guidance/variables.yaml` `output_variables`): if not already present in the `outputs:` block, append a new entry there.
- **Computed variable** (name is not in `guidance/variables.yaml` `output_variables`): if not already present in the `computed:` block, append a new entry there.

```yaml
computed:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
outputs:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
```
Do not modify or remove any existing entries.

**If `naming-manifest.yaml` does not exist:**
Create it with all variable names used in the generated rules, routing each to `computed:` or `outputs:` using the same rule above:
```yaml
version: "1.0"
inputs:
  <EntityName>:        # one entry per entity from bound_entities: (if available)
    # (fields populated by /extract-ruleset Step 7b)
computed:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
outputs:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
```

Populate the `inputs:` block using deduplicated CamelCase entity names from `ruleset_modules[].bound_entities` in `guidance/ruleset-modules.yaml`. If `ruleset-modules.yaml` is absent, empty, or all entries have empty `bound_entities:` lists (e.g., only a `role: main` entry exists), omit the `inputs:` block and add a comment: `# inputs: will be populated by /extract-ruleset Step 7b`.

Omit the `outputs:` block if no generated variables are in `guidance/variables.yaml`'s `output_variables` list.

Do not add an auto-generated comment. The file is user-editable.

### Summary

The `→ <destination>` label uses the module's `name:` value (e.g., `→ eligibility` for the main module, `→ exclusion_chain` for a sub-module, `→ sample-artifacts` when no `role: main` entry exists and the rule falls back to the top-level `sample_rules:`).

#### Pass 4a Summary

Printed immediately after Pass 4a writes, before Pass 4b begins. Print one line per `computed:` rule written:

:::progress
Sample quick ("index-only") rules were written to the guidance/ folder and ready for review while the remaining ("needs-source") rules are being created.

Index-pass rules written:
  earned_income_limit   (computed)   → exclusion_chain
  net_earned_income     (computed)   → eligibility

Missing info (index pass):
  - blind_work_expenses: description absent in index — policy_phrase not written to naming-manifest

Continuing with source reads...
:::

If `index-only` mode, replace the last line with the Next: suggestion (see below) and stop. If any `needs-source` entries were skipped, append:

```
Skipped (index-only — source text required):
  - 441-1 EARNED INCOME
  - 523 A. SOCIAL SECURITY BENEFITS
```

#### Full Summary

Printed after Pass 4b completes (or after Pass 4a if `index-only`). Print one line per rule written across both passes, in the order they were generated:

:::important
Rules written:
  after_federal     (computed)      → exclusion_chain
  after_eitc        (computed)      → exclusion_chain
  is_compatible     (computed)      → eligibility
  approve_income    (categorical)   → eligibility
  income_limit      (table-lookup)  → eligibility

Missing info:
  - monthly_limit for student exclusion not defined in index; see Addendum 1

Assumptions:
  - No expr_hint for blind_work_expenses — expr marked as "?"
:::

If `<rule_topic>` was provided and entries were skipped, list them:
:::detail
Skipped (not related to '<rule_topic>'):
  - 441-2 UNEARNED INCOME
  - 523 MEDICAID EXCEPTIONS
:::

If `index-only` was provided and `needs-source` entries were skipped, list them (as a separate block when both filters are active):
:::detail
Skipped (index-only — source text required):
  - 441-1 EARNED INCOME
  - 523 A. SOCIAL SECURITY BENEFITS
:::

Then suggest next steps:

:::next_step
Next: Run /tag-vars-to-include-with-output <domain> to auto-detect intermediate computed variables to be exposed along with the final output
:::

---

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` | Updated — `ruleset_modules[].sample_rules` merged |
| `$DOMAINS_DIR/<domain>/specs/guidance/sample-artifacts.yaml` | Created or updated — `sample_rules`, `missing_info`, `assumptions` merged |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Created or updated — `computed:` and `outputs:` entries merged |

---

## Common Mistakes to Avoid

- **Do not read files under `$DOMAINS_DIR/<domain>/input/` directly** — use the per-file files under `policy_facets/computations/`: each section's source path is encoded in the file's relative location (`policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md`); navigate to the section identified by `heading:` within that source. Reading source policy files via those pointers is explicitly permitted for this command.
- **Do not overwrite existing `sample_rules:` entries** — merge by `id:` only; never remove manually edited rules
- **Do not overwrite existing `naming-manifest.yaml` entries** — append only; the manifest is user-editable and may contain frozen names from a prior `/extract-ruleset` run
- **Do not clobber other guidance file contents** — this command writes only to `ruleset_modules[].sample_rules` in `ruleset-modules.yaml`, and to `sample_rules`, `missing_info`, `assumptions` in `sample-artifacts.yaml`; all other fields must be preserved verbatim
- **Use canonical names from the manifest** — if a variable name exists in `naming-manifest.yaml`, use it; do not re-derive or rename it
- **`civil:` is a literal block scalar** — always use the `|` block indicator; never use a quoted string or folded scalar for CIVIL snippets
- **`source:` must be a quoted sentence from the per-file file** — copy from the section's `summary:` or `computations[].description:` in `policy_facets/computations/<rel>.md.yaml`; do not paraphrase
- **Do not write `generated_at`**
- **Do not combine Pass 4a and 4b into a single write** — Pass 4a must write files and print its summary before Pass 4b begins; the point is to let the user review index-derived rules while source reads are in progress
