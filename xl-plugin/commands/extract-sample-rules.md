# Extract Sample Rules

Generate a comprehensive set of relevant CIVIL rules from `input-index.yaml` entries based on `guidance.yaml` and write them into `guidance.yaml` and `naming-manifest.yaml`. Runs non-interactively — no mid-run prompting. Suitable for automated UI invocation.

Unlike `/xl:refine-guidance` Step 8, which produces 2–3 illustrative rules gated behind user approval, this command generates as many rules as the index supports and writes them immediately for user review.

**Recommended run order:** After `/xl:create-ruleset-modules`. The quality of the output depends on how complete `guidance.yaml` is at invocation time:

| `guidance.yaml` state | Impact on output |
|---|---|
| `ruleset_modules:` populated (after `/xl:create-ruleset-modules`) | Rules routed to the correct ruleset module's `sample_rules:` — full structural grouping |
| `ruleset_groups:` present but no `ruleset_modules:` (after `/xl:create-ruleset-groups`) | Stage context available but all rules fall into the top-level `sample_rules:` |
| `skeleton:` present but no stages or ruleset modules (after `/xl:create-skeleton`) | Computation ordering and category context available; rules still fall into the top-level `sample_rules:` |
| Neither `skeleton:` nor `ruleset_modules:` (after `/xl:declare-ruleset-io` only) | Command runs but produces flat, unstructured output with no ordering context |

The command prints a warning when `skeleton:` or `ruleset_modules:` is absent (see Step 2). It does not stop — partial output is better than none.

## Input

```
/extract-sample-rules [<domain>] [<rule_topic>] [index-only]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance.yaml` files as a numbered menu and prompt:

```
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
```

`<rule_topic>` is an optional free-text filter (e.g., `"earned income"`, `"student exclusion"`). When provided, limit rule generation to index entries whose `heading:`, `summary:`, or `tags:` relate to the topic (case-insensitive keyword match). Report skipped entries at the end.

`index-only` is an optional literal keyword (third positional argument). When provided, only entries whose `computations[]` all have `expr_hint:` present are processed; entries that require reading source files are skipped entirely. Pass 4b does not run. Use this when you want fast, index-derived `computed:` rules without waiting for source reads.

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO → Print: `Domain not found: $DOMAINS_DIR/<domain>/` Then stop.

3. **Third positional argument provided but not `index-only`?**
   - YES → Print:
     ```
     Unrecognized argument: '<value>'. Did you mean: index-only?
     ```
     Stop.

4. **`guidance.yaml` exists?**
   - NO → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:suggest-ruleset-io <domain> first.
     ```
     Stop.

5. **`input-index.yaml` exists?**
   - NO → Print:
     ```
     input-index.yaml not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /xl:index-inputs <domain> first.
     ```
     Stop.

---

## Process

### Step 1: Load canonical names

Check for `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml`.

**If the manifest exists:** Read it. Build a lookup map from every `computed:` entry: `{variable_name → manifest_entry}`. These names are **canonical** — prefer them over freshly inferred names during rule generation in Step 4.

**If absent:** Proceed with an empty lookup map. The manifest will be created in Step 6.

Show step checklist:
```
Steps:
  [✓] 1. Load canonical names
  [ ] 2. Load and filter index
  [ ] 3. Read guidance context and classify entries
  [ ] 4. Generate rules (two-pass)
  [ ] 5. Merge into guidance.yaml
  [ ] 6. Write naming-manifest.yaml
```

### Step 2: Load and filter index

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`. Filter `sections[]` to entries that have a non-empty `computations:` field (at least one computation entry). Proceed regardless.

**If `<rule_topic>` was provided:** further filter to entries whose `heading:`, `summary:`, or `tags:` contain the topic keywords (case-insensitive). If no entries match the topic, print:

```
No index entries found related to '<rule_topic>'.
Available tags: [tag1, tag2, ...]
```

Then stop.

Print: `Found N qualifying index entries` (or `Found N qualifying entries matching '<rule_topic>'`).

Show updated step checklist.

### Step 3: Read guidance context and classify entries

Read `$DOMAINS_DIR/<domain>/specs/guidance.yaml` to produce a **prioritized working set** of entries for Step 4. The working set is an ordered list derived from the qualifying entries found in Step 2, with entries clearly unrelated to the ruleset's purpose removed and logged before further processing.

Check for missing context and print warnings if applicable:

```
⚠ skeleton: not found in guidance.yaml — computation ordering and category groupings unavailable.
  Run /xl:create-skeleton <domain> first for better-structured output.

⚠ ruleset_modules: not found in guidance.yaml — all rules will be written to top-level sample_rules: (no ruleset module grouping).
  Run /xl:create-ruleset-modules <domain> first for structured rule routing.
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
```
Working set: N entries (M high priority, K normal, L low priority)
  Classified: C computed-only, S needs-source
Skipped: P entries (unrelated to role)
```

Show updated step checklist.

### Step 4: Generate rules (two-pass)

Rules are generated in two passes. Pass 4a processes `computed-only` entries using index data alone and writes output immediately. Pass 4b processes `needs-source` entries (and any `computed-only` entries queued by heuristic signals) by reading source documents, then merges again. The merge schemas are defined in Steps 5 and 6 below.

---

**Pass 4a — Index pass**

For each `computed-only` entry in the working set, processed in priority order (high → low when `skeleton:` is present, or in index order when it is absent), then within each priority group in the order they appear in `input-index.yaml`:

**(a) Determine canonical variable names.** For each variable name in the entry's `computations[].variables[]` list, check the canonical names map from Step 1. If a match is found, use the manifest name. If no match, derive a snake_case name from the entry's `computations[].description` text:
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

**(d) Assign to ruleset module or main.** For each generated rule, determine the best matching `ruleset_modules:` entry in `guidance.yaml`:
- Match by variable name overlap (variables in the rule appear in the ruleset module's description) or section heading keyword overlap with the ruleset module's `description:`. Only match against sub-module entries (entries where `role:` is absent or `sub`) — do not route to the `role: main` entry during this matching step.
- If a clear match is found, assign to that sub-module's `sample_rules:` list.
- If no sub-module match is found: check whether `guidance.yaml` has a `ruleset_modules:` entry with `role: main`. If yes, assign the rule to that entry's `sample_rules:` list (locate the entry by its `name:` value). If no `role: main` entry exists, assign to the top-level `sample_rules:` list as a fallback.

After processing all `computed-only` entries:

**`index-only` + zero computed-only entries check:** If `index-only` is set and no `computed-only` entries were found, print:
```
⚠ index-only mode: no computed-only entries found — nothing to generate.
  Remove index-only or add expr_hint: fields to the index.
```
Stop.

**Write Pass 4a output:** Merge rules into `guidance.yaml` (Step 5 merge schema) and merge variable entries into `naming-manifest.yaml` (Step 6 merge schema), with index-derived field values:
- `path:` (from index entry) → `source_doc`
- `heading:` (from index entry) → `section`
- `computations[].description` → `policy_phrase` (if absent, omit the entry from naming-manifest for that variable and add to `missing_info`)

Write both files now.

Print Pass 4a Summary (see [Summary](#summary)).

Show updated step checklist.

If `index-only`: stop. Do not run Pass 4b.

---

**Pass 4b — Source pass**

Process all `needs-source` entries, then any `computed-only` entries added to the Pass 4b queue. Within each group, process in priority order (high → normal → low), then in `input-index.yaml` order.

**(a) Read source text.** Locate the source file at `path:` and navigate to the section identified by `heading:`. Read that section's text.

- If the file at `path:` does not exist: log `⚠ Source not found: <path> — skipping entry` and add to `missing_info`. Continue to the next entry.
- If the heading cannot be located in the file: log `⚠ Heading not found: "<heading>" in <path> — skipping entry` and add to `missing_info`. Continue.

**(b) Determine canonical variable names.** For each variable name in the entry's `computations[].variables[]` list, check the canonical names map from Step 1. If a match is found, use the manifest name. If no match, derive a snake_case name from the policy text using the Name Inventory algorithm:
- Extract the exact noun phrase from the policy text
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(c) Generate rules.** For each computation hint in the entry, produce one or more CIVIL rule snippets:

- **`computed:` rule** — for `needs-source` entries with an `expr_hint:`: produce a `computed:` snippet using the canonical output variable name and the expr_hint as the `expr:` value. For `computed-only` entries in the Pass 4b queue: **skip `computed:` rules** — already written in Pass 4a.
- **`computed:` rule (no expr_hint)** — for `needs-source` entries where no `expr_hint:` is given: produce the snippet with `expr: "?"` as a placeholder. Record the variable in `assumptions:` ("No expr_hint available for `<name>` — expr must be confirmed manually").
- **`categorical:` rules** — scan the source text for conditional policy statements (if/then, eligibility conditions, deny/approve triggers). For each, draft a `rules:` entry with `when:` and `then:` blocks using canonical variable names.
- **`table-lookup:` rule** — if the source text references a table or schedule of thresholds, draft a `computed:` entry using `table_lookup:` syntax with `table:` and `key:` fields.
- **`invoke:` rule** — if the source text's computation calls for running a ruleset module, and `ruleset_modules:` in `guidance.yaml` has a matching entry, draft a `computed:` entry with `invoke:` and `with:` fields using the ruleset module's `name:` and canonical variable bindings.

**(d) Assign to ruleset module or main.** Same logic as Pass 4a sub-step (d).

**(e) Record notes.** Track:
- Any referenced value not found in the index or source text → add descriptive string to `missing_info`
- Any inferential leap or assumption → add descriptive string to `assumptions`
- Any low-priority entry from Step 3 for which rules were generated → add to `assumptions`: `"<heading> not in skeleton — rule may be auxiliary or out of scope; confirm before use"`

After processing all Pass 4b entries: merge rules into `guidance.yaml` (Step 5 merge schema) and merge updated variable entries into `naming-manifest.yaml` (Step 6 merge schema), overwriting any index-derived `policy_phrase` values with source-text values where available. Write both files.

Print Full Summary (see [Summary](#summary)).

Show updated step checklist (all steps complete — both files written during Pass 4a and updated during Pass 4b).

### Step 5: Merge schema — guidance.yaml

> This schema is applied from within Step 4 after each pass. It is documented here as the canonical reference.

Apply all merges without clobbering existing content:

**`ruleset_modules[].sample_rules:` (merge by `id:`):**
For each ruleset module entry that has assigned rules, add a `sample_rules:` sub-key if absent, then append rules whose `id:` is not already present. Do not overwrite or remove existing entries.

Rule entry schema:
```yaml
sample_rules:
  - id: <snake_case_identifier>
    rule_type: computed | categorical | table-lookup
    source: "<quoted sentence from input-index.yaml section summary>"
    civil: |
      <full CIVIL YAML snippet>
```

**`sample_rules:` (merge by `id:`):**
Append unmatched rules to the top-level `sample_rules:` list. Place after `edge_cases:` if `sample_rules:` does not yet exist. Deduplicate by `id:`.

**`missing_info:` (merge — append unique strings):**
Add new unique strings to the top-level `missing_info:` list. Place after `edge_cases:` (or after `assumptions:` if that key already exists). Do not remove or overwrite existing entries.

**`assumptions:` (merge — append unique strings):**
Add new unique strings to the top-level `assumptions:` list. Place after `missing_info:`. Do not remove or overwrite existing entries.

### Step 6: Merge schema — naming-manifest.yaml

> This schema is applied from within Step 4 after each pass. It is documented here as the canonical reference.

**If `naming-manifest.yaml` already exists:**
Read it. For each variable name used in the generated rules that is not already present in the `computed:` block, append a new entry:
```yaml
computed:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
```
Do not modify or remove any existing entries.

**If `naming-manifest.yaml` does not exist:**
Create it with all variable names used in the generated rules:
```yaml
version: "1.0"
computed:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
```
Omit the `entities:` block — entity context is not available at this stage; it is populated by `/xl:extract-ruleset` Step 7b.

Do not add an auto-generated comment. The file is user-editable.

### Summary

The `→ <destination>` label uses the module's `name:` value (e.g., `→ eligibility` for the main module, `→ exclusion_chain` for a sub-module, `→ top-level` when no `role: main` entry exists and the rule falls back to the top-level `sample_rules:`).

#### Pass 4a Summary

Printed immediately after Pass 4a writes, before Pass 4b begins. Print one line per `computed:` rule written:

```
Index-pass rules written:
  earned_income_limit   (computed)   → exclusion_chain
  net_earned_income     (computed)   → eligibility

Missing info (index pass):
  - blind_work_expenses: description absent in index — policy_phrase not written to naming-manifest

Continuing with source reads...
```

If `index-only` mode, replace the last line with the Next: suggestion (see below) and stop. If any `needs-source` entries were skipped, append:

```
Skipped (index-only — source text required):
  - 441-1 EARNED INCOME
  - 523 A. SOCIAL SECURITY BENEFITS
```

#### Full Summary

Printed after Pass 4b completes (or after Pass 4a if `index-only`). Print one line per rule written across both passes, in the order they were generated:

```
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
```

If `<rule_topic>` was provided and entries were skipped, list them:
```
Skipped (not related to '<rule_topic>'):
  - 441-2 UNEARNED INCOME
  - 523 MEDICAID EXCEPTIONS
```

If `index-only` was provided and `needs-source` entries were skipped, list them (as a separate block when both filters are active):
```
Skipped (index-only — source text required):
  - 441-1 EARNED INCOME
  - 523 A. SOCIAL SECURITY BENEFITS
```

Then suggest next steps:

```
Next: Run /xl:tag-vars-to-include-with-output <domain> to auto-detect intermediate computed variables to be exposed along with the final output
```

---

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Updated — `ruleset_modules[].sample_rules`, `sample_rules`, `missing_info`, `assumptions` merged |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Created or updated — `computed:` entries merged |

---

## Common Mistakes to Avoid

- **Do not read files under `$DOMAINS_DIR/<domain>/input/` directly** — use `path:` and `heading:` from `input-index.yaml` to locate sections. Reading source policy files via those pointers is explicitly permitted for this command.
- **Do not overwrite existing `sample_rules:` entries** — merge by `id:` only; never remove manually edited rules
- **Do not overwrite existing `naming-manifest.yaml` entries** — append only; the manifest is user-editable and may contain frozen names from a prior `/xl:extract-ruleset` run
- **Do not clobber other guidance.yaml sections** — this command writes only to `ruleset_modules[].sample_rules`, `sample_rules`, `missing_info`, `assumptions`; all other sections must be preserved verbatim
- **Use canonical names from the manifest** — if a variable name exists in `naming-manifest.yaml`, use it; do not re-derive or rename it
- **`civil:` is a literal block scalar** — always use the `|` block indicator; never use a quoted string or folded scalar for CIVIL snippets
- **`source:` must be a quoted sentence from the index** — copy from `input-index.yaml` section `summary:` or `computations[].description:`; do not paraphrase
