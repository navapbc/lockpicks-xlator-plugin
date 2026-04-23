# Extract Sample Rules

Exhaustively generate CIVIL rules from `input-index.yaml` entries and write them into `guidance.yaml` and `naming-manifest.yaml`. Runs non-interactively — no mid-run prompting. Suitable for automated UI invocation.

Unlike `/xl:refine-guidance` Step 8, which produces 2–3 illustrative rules gated behind user approval, this command generates as many rules as the index supports and writes them immediately.

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
/extract-sample-rules [<domain>] [<rule_topic>]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance.yaml` files as a numbered menu and prompt:

```
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
```

`<rule_topic>` is an optional free-text filter (e.g., `"earned income"`, `"student exclusion"`). When provided, limit rule generation to index entries whose `heading:`, `summary:`, or `tags:` relate to the topic (case-insensitive keyword match). Report skipped entries at the end.

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO → Print: `Domain not found: $DOMAINS_DIR/<domain>/` Then stop.

3. **`guidance.yaml` exists?**
   - NO → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:suggest-ruleset-io <domain> first.
     ```
     Stop.

4. **`input-index.yaml` exists?**
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

**If the manifest exists:** Read it. Build a lookup map from every `computed:` entry: `{variable_name → manifest_entry}`. These names are **canonical** — prefer them over freshly inferred names during rule generation in Step 3.

**If absent:** Proceed with an empty lookup map. The manifest will be created in Step 5.

Show step checklist:
```
Steps:
  [✓] 1. Load canonical names
  [ ] 2. Load and filter index
  [ ] 3. Generate rules from source text
  [ ] 4. Merge into guidance.yaml
  [ ] 5. Write naming-manifest.yaml
```

### Step 2: Load and filter index

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`. Filter `sections[]` to entries that have a non-empty `computations:` field (at least one computation entry).

Before filtering, check `guidance.yaml` for missing context and print warnings if applicable:

```
⚠ skeleton: not found in guidance.yaml — computation ordering and category groupings unavailable.
  Run /xl:create-skeleton <domain> first for better-structured output.

⚠ ruleset_modules: not found in guidance.yaml — all rules will be written to top-level sample_rules: (no ruleset module grouping).
  Run /xl:create-ruleset-modules <domain> first for structured rule routing.
```

Print only the warnings that apply. Proceed regardless.

**If `<rule_topic>` was provided:** further filter to entries whose `heading:`, `summary:`, or `tags:` contain the topic keywords (case-insensitive). If no entries match the topic, print:

```
No index entries found related to '<rule_topic>'.
Available tags: [tag1, tag2, ...]
```

Then stop.

Print: `Found N qualifying index entries` (or `Found N qualifying entries matching '<rule_topic>'`).

Show updated step checklist.

### Step 3: Generate rules from source text

For each qualifying index entry, in the order they appear in `input-index.yaml`:

**(a) Read source text.** Locate the source file at `path:` and navigate to the section identified by `heading:`. Read that section's text.

- If the file at `path:` does not exist: log `⚠ Source not found: <path> — skipping entry` and add to `missing_info`. Continue to the next entry.
- If the heading cannot be located in the file: log `⚠ Heading not found: "<heading>" in <path> — skipping entry` and add to `missing_info`. Continue.

**(b) Determine canonical variable names.** For each variable name in the entry's `computations[].variables[]` list, check the canonical names map from Step 1. If a match is found, use the manifest name. If no match, derive a snake_case name from the policy text using the Name Inventory algorithm:
- Extract the exact noun phrase from the policy text
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(c) Generate rules.** For each computation hint in the entry, produce one or more CIVIL rule snippets:

- **`computed:` rule** — for each `computations[]` entry with an `expr_hint:`: produce a `computed:` snippet using the canonical output variable name and the expr_hint as the `expr:` value. Substitute canonical names for any input variable names found in the expr_hint.
- **`computed:` rule (no expr_hint)** — if no `expr_hint:` is given, produce the snippet with `expr: "?"` as a placeholder. Record the variable in `assumptions:` ("No expr_hint available for `<name>` — expr must be confirmed manually").
- **`categorical:` rules** — scan the source text for conditional policy statements (if/then, eligibility conditions, deny/approve triggers). For each, draft a `rules:` entry with `when:` and `then:` blocks using canonical variable names.
- **`table-lookup:` rule** — if the source text references a table or schedule of thresholds, draft a `computed:` entry using `table_lookup:` syntax with `table:` and `key:` fields.
- **`invoke:` rule** — if the source text's computation calls for running a ruleset module, and `ruleset_modules:` in `guidance.yaml` has a matching entry, draft a `computed:` entry with `invoke:` and `with:` fields using the ruleset module's `name:` and canonical variable bindings.

**(d) Assign to ruleset module or main.** For each generated rule, determine the best matching `ruleset_modules:` entry in `guidance.yaml`:
- Match by variable name overlap (variables in the rule appear in the ruleset module's description) or section heading keyword overlap with the ruleset module's `description:`.
- If a clear match is found, assign to that ruleset module's `sample_rules:` list.
- If no ruleset module match is found, assign to `sample_rules:` (the top-level list).

**(e) Record notes.** Track:
- Any referenced value not found in the index or source text → add descriptive string to `missing_info`
- Any inferential leap or assumption → add descriptive string to `assumptions`

Show updated step checklist after processing all entries.

### Step 4: Merge into guidance.yaml

Read the current `guidance.yaml`. Apply all merges without clobbering existing content:

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

Write the updated `guidance.yaml`.

Show updated step checklist.

### Step 5: Write naming-manifest.yaml

**If `naming-manifest.yaml` already exists:**
Read it. For each variable name used in the generated rules that is not already present in the `computed:` block, append a new entry:
```yaml
computed:
  <variable_name>:
    policy_phrase: "<noun phrase from source text>"
    source_doc: "<filename.md>"
    section: "<section heading>"
```
Do not modify or remove any existing entries. Write the updated file.

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

Show updated step checklist (all steps complete).

### Summary

Print one line per rule written, in the order they were generated:

```
Rules written:
  after_federal     (computed)      → exclusion_chain
  after_eitc        (computed)      → exclusion_chain
  is_compatible     (computed)      → main
  approve_income    (categorical)   → main
  income_limit      (table-lookup)  → main

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
