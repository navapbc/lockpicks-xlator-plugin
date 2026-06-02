---
name: extract-sample-rules
description: Extract Sample Rules
---

# Extract Sample Rules

Generate a comprehensive set of relevant Catala rules from the per-file files under `policy_facets/computations/` based on the `guidance/` folder and write them into `guidance/ruleset-modules.yaml`, `guidance/sample-artifacts.yaml`, and `naming-manifest.yaml`. Runs non-interactively — no mid-run prompting. Suitable for automated UI invocation.

Emitted Catala snippets follow the idioms documented in `../../core/catala-authoring-quickref.md` — fenced `catala` (or `catala-metadata` for cross-module exports) blocks containing scope-shaped `definition`/`rule` constructs.

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

`index-only` is an optional literal keyword (third positional argument). When provided, only entries whose `computations[]` all have `expr_hint:` present are processed; entries that require reading the policy doc content are skipped entirely. Pass 4b does not run. Use this when you want fast, index-derived `computed:` rules without waiting for content reads.

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

Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML map. Read `data["sections"]` as the list of section blocks. Concatenate all entries into a single working list, deriving the `path:` field per entry from the file's relative location (a section in `policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md`). Filter the working list to entries that have a non-empty `computations:` field (at least one computation entry). Proceed regardless.

**`expr_hint:` parse rule** (uniform across consumer skills): when a computation carries `expr_hint:`, split on the first `=`; the LHS (whitespace-trimmed) is the snake_case **output name** for that computation, and the RHS is the bare expression. Tokenize the RHS for snake_case identifiers (skipping numeric literals, string literals, and built-in keywords like `if`, `else`, `and`, `or`, `not`, `min`, `max`, `sum`) — those identifiers are the **input names**. When `expr_hint:` is absent (descriptive-only computation), fall back to scanning `description:` prose for variable names mentioned in the source's terminology. The bare-expression value used downstream (in `expr:` substitution and `categorical:` rendering) is the RHS with the `<output> =` prefix stripped.

**If `<rule_topic>` was provided:** further filter to entries whose `heading:`, `summary:`, or `tags:` contain the topic keywords (case-insensitive). If no entries match the topic, print:

```
No index entries found related to '<rule_topic>'.
Available tags: [tag1, tag2, ...]
```

Then stop.

Print: `Found N qualifying index entries` (or `Found N qualifying entries matching '<rule_topic>'`).

Show updated step checklist (as `:::progress`).

### Step 3: Read guidance context and classify entries

Read `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` (`role:`), `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` (`outputs:`) plus `$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml` (for the `primary: true` flag), and optionally `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` and `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` to produce a **prioritized working set** of entries for Step 4. The working set is an ordered list derived from the qualifying entries found in Step 2, with entries clearly unrelated to the ruleset's purpose removed and logged before further processing.

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

**`skeleton:`** — The ordered list of computation categories and their members. For each entry remaining in the working set, derive the entry's variable inventory by applying the `expr_hint:` parse rule (Step 2) to every `computations[]` entry — the LHS is the output name and the RHS tokens are the input names; for descriptive-only computations, scan `description:` prose. Then:
- If any of the entry's derived variable names appear in `skeleton:`, mark it **high priority**.
- If none of the entry's variables appear in `skeleton:`, mark it **low priority** — it may represent auxiliary or supporting policy text.

Use the skeleton category labels (e.g., `income`, `deductions`, `benefit_amount`) in Step 4 to focus `categorical:` and `table-lookup:` rule drafting on the correct domain concepts.

If `skeleton:` is absent, mark all working set entries as normal priority.

**Pre-classify entries**

For each entry remaining in the working set, classify based solely on `expr_hint:` completeness in the index:

- **`computed-only`**: all `computations[]` entries have `expr_hint:` present in the assignment form (`output_name = <expression>`). Rules can be generated from index data alone in Pass 4a, without reading any policy doc content.
- **`needs-source`**: any `computations[]` entry is missing `expr_hint:` or `computations[]` is empty. These entries require reading the policy doc content in Pass 4b — the caveman-compressed mirror by default, with the original source as a fallback when the compressed text is ambiguous.

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

Rules are generated in two passes. **These passes are strictly sequential and must never be combined into a single write.** Pass 4a processes `computed-only` entries using index data alone and writes output immediately so the user can review rules while Pass 4b runs. Pass 4b processes `needs-source` entries (and any `computed-only` entries queued by heuristic signals) by reading the caveman-compressed policy mirror at `policy_facets/compressed/<rel>.md` (falling back to the source doc at `input/policy_docs/<rel>.md` only when the compressed text is ambiguous, unclear, or questionable), then merges again. The merge schemas are defined in Steps 5 and 6 below.

**Rendering `preconditions:` to Catala.** Multiple sub-steps below — Pass 4a (b), and Pass 4b (c) for both `computed:` and `categorical:` rules — consume the `preconditions:` field from index entries. Apply this rendering uniformly:

- **Boolean shape** — the top-level list joins terms with `and`; `{all_of: [...]}` joins terms with `and`; `{any_of: [...]}` joins terms with `or`; arbitrary nesting is permitted.
- **Leaf translation** — each leaf string clause is a natural-language predicate. Translate it into a Catala boolean expression using canonical variable names (resolved via SP-LoadNamingManifest as in sub-step (a)). When a clause cannot be confidently translated to Catala, keep it as a quoted string in a `# precondition: <clause>` Catala line comment immediately above the rule's `definition` or `rule` line, and add a `missing_info:` entry: `"Precondition <clause> for <variable_name> could not be translated to Catala — confirm manually"`.
- **`computed:` rules** — wrap the bare expression in a contextual `definition … under condition <rendered> consequence equals <expr_hint>` block instead of a bare `definition … equals`. When an `else` branch is implied by the policy, add a second `under condition not (<rendered>) consequence equals <fallback>` block (or use an `if … then … else …` body when the fallback is a constant). If no `else` branch is implied and no sensible default exists, keep the bare `definition … equals` form and prepend the `# precondition: <rendered>` comment.
- **`categorical:` rules** — render preconditions as the `rule … under condition <rendered>` clause; policy-text triggers from Pass 4b (c) contribute additional clauses joined with `and`.
- **Absent / empty `preconditions:`** — render rules unconditionally (existing behavior).

---

**Pass 4a — Index pass**

For each `computed-only` entry in the working set, processed in priority order (high → low when `skeleton:` is present, or in index order when it is absent), then within each priority group in the entry order produced by globbing `policy_facets/computations/**/*.md.yaml` alphabetically and concatenating each file's `sections:` list:

**(a) Determine canonical variable names.** For each variable name surfaced by applying the `expr_hint:` parse rule (Step 2) to the entry's `computations[]` (LHS output names plus RHS input tokens), apply the keyed lookup from SP-LoadNamingManifest: if the variable name matches a map key in `specs/naming-manifest.yaml`, use that manifest name. Only if no keyed match is found, derive a snake_case name from the entry's `computations[].description` text:
- Extract the noun phrase from the description
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(b) Generate `computed:` rules.** For each `computations[]` entry, produce a Catala `definition` snippet using:
- Canonical output variable name (the `expr_hint:` LHS, resolved through (a)) as the `definition <var> equals …` LHS
- The bare expression (the `expr_hint:` RHS with the `<output> =` prefix stripped) as the `definition` body, substituting canonical names for any input variable tokens and rewriting non-Catala operators into Catala forms (e.g., `* 0.5` → `* 50%`, `[a, b]` → `[ a; b ]`, money literals as `$1,234`). See `../../core/catala-authoring-quickref.md` Part 2 (money/date literals, list operators) and Part 4 (confabulation traps).
- `source:` from `computations[].description` (if `description` is absent, emit the definition body as `?` and add to `missing_info`: `"No description for <variable_name> — Catala body and source must be confirmed manually"`)
- If `preconditions:` is present on the entry, apply the **Rendering `preconditions:` to Catala** rule (above the Pass 4a header) to wrap the bare definition in `definition <var> under condition <rendered> consequence equals <expr>`, or fall back to a `# precondition: <rendered>` Catala line comment above the bare `definition … equals` when no `else` branch is inferable from the index.
- **`#[error.message]` annotation.** If the entry's source section carries a `stage:` value, attach `#[error.message = "<stage>"]` to the emitted `definition` block as the per-rule diagnostic tag — apply the same suffix-stripping normalization as `/create-ruleset-groups` (drop a trailing `_test` / `_check` / `_evaluation`) so the tag matches the canonical stage name `/create-ruleset-groups` writes to `ruleset-groups.yaml`. When `stage:` is absent on the source section, fall through to existing heading-text-derived tag logic unchanged. The stage-derived tag is the explicit doc signal; heading-text is a derived guess; explicit beats inferred.

**(c) Check heuristic signals.** Scan the entry's `tags:` and `summary:` for these keywords (case-insensitive):
- Table/schedule keywords: `table`, `schedule`, `threshold`, `limit`
- Conditional language: `if`, `unless`, `when`, `except`, `eligibility`

If any signals are present, add this entry to the Pass 4b queue for `categorical:` and `table-lookup:` rule generation. Do not read the policy doc content now.

**(d) Assign to ruleset module or main.** For each generated rule, determine the best matching entry in `guidance/ruleset-modules.yaml`:
- **Stage-aware matching when `stage:` is populated on the source section.** Filter sub-module candidates to those whose variables fall within the rule's (post-normalization) `stage:` — the R21 stage-boundary constraint is now extended to `stage:` agreement (see `/create-ruleset-modules` Step 2), so a sub-module is only a valid binding target if its variables share the rule's stage. Among stage-compatible sub-modules, then apply the existing variable name / heading keyword overlap heuristic. This keeps the rule's `#[error.message]` stage tag (set in (b)) consistent with the binding sub-module's stage membership.
- When `stage:` is absent on the source section, match by variable name overlap (variables in the rule appear in the ruleset module's description) or section heading keyword overlap with the ruleset module's `description:`. Only match against sub-module entries (entries where `role:` is absent or `sub`) — do not route to the `role: main` entry during this matching step.
- If a clear match is found, assign to that sub-module's `sample_rules:` list in `ruleset-modules.yaml`.
- If no sub-module match is found: check whether `ruleset-modules.yaml` has an entry with `role: main`. If yes, assign the rule to that entry's `sample_rules:` list (locate the entry by its `name:` value). If no `role: main` entry exists, assign to the top-level `sample_rules:` in `sample-artifacts.yaml` as a fallback.

After processing all `computed-only` entries:

**`index-only` + zero computed-only entries check:** If `index-only` is set and no `computed-only` entries were found, print:
:::error
⚠ index-only mode: no computed-only entries found — nothing to generate.
  Remove index-only or add expr_hint: fields to the index.
:::
Stop.

**Write Pass 4a output:** Merge rules into `guidance/ruleset-modules.yaml` and `guidance/sample-artifacts.yaml` (Step 5 merge schema) and merge variable entries into `naming-manifest.yaml` (Step 6 merge schema). Each manifest entry's `observations:` list receives one triple per index-derived source, with index-derived field values:
- `path:` (from index entry) → observation `source_doc`
- `heading:` (from index entry) → observation `section`
- `computations[].description` → observation `policy_phrase` (if absent, emit the observation triple with `policy_phrase` omitted — `source_doc` + `section` still anchor the observation — and add to `missing_info`)

Write both files now. **Do not begin Pass 4b until both files have been written to disk.**

Print Pass 4a Summary (see [Summary](#summary)). **Do not begin Pass 4b until the Pass 4a Summary has been printed.**

Show updated step checklist.

If `index-only`: stop. Do not run Pass 4b.

---

**Pass 4b — Source pass**

Process all `needs-source` entries, then any `computed-only` entries added to the Pass 4b queue. Within each group, process in priority order (high → normal → low), then in the entry order produced by globbing `policy_facets/computations/**/*.md.yaml` alphabetically and concatenating each file's `sections:` list.

**(a) Read policy text.** From the working-set entry's `path:` (reconstituted as `input/policy_docs/<rel>.md`), derive the caveman-compressed mirror path by swapping the `input/policy_docs/` prefix for `policy_facets/compressed/`. Open `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` and navigate to the section identified by `heading:`. Read that section's text from the compressed mirror — this is the default read.

**Fallback to source.** Only when the compressed text for that section is ambiguous, unclear, or questionable for the rule drafting at hand — for example, a precondition cannot be confidently translated to Catala, a table or schedule is referenced but its rows are not reproduced in the compressed mirror, or aggressive compression has elided detail needed to disambiguate variable names or thresholds — re-read the same section from the source doc at `$DOMAINS_DIR/<domain>/input/policy_docs/<rel>.md`. The compressed mirror is the default; the source read is the escape hatch.

- If neither the compressed mirror nor the source doc exists at the expected path: log `⚠ Source not found: <rel> — skipping entry` and add to `missing_info`. Continue to the next entry.
- If the heading cannot be located in the compressed mirror, attempt the same heading lookup in the source doc before declaring it missing. If neither file contains the heading: log `⚠ Heading not found: "<heading>" in <rel> — skipping entry` and add to `missing_info`. Continue.

**(b) Determine canonical variable names.** For each variable name surfaced by applying the `expr_hint:` parse rule (Step 2) to the entry's `computations[]` (LHS output names plus RHS input tokens, or `description:` prose for descriptive-only computations), apply the keyed lookup from SP-LoadNamingManifest: if the variable name matches a map key in `specs/naming-manifest.yaml`, use that manifest name. Only if no keyed match is found, derive a snake_case name from the policy text using the Name Inventory algorithm:
- Extract the exact noun phrase from the policy text
- Strip entity prefixes (ClientData, DOLRecord, etc.) if present
- Convert to snake_case
- Disambiguate if a name would collide with an existing name

**(c) Generate rules.** For each computation hint in the entry, produce one or more Catala rule snippets (each as a fenced `catala` block; use `catala-metadata` only when the snippet is a structure/enum/scope declaration intended for cross-module export — see `../../core/catala-authoring-quickref.md` Part 3 "Fence discipline"):

- **`computed:` rule** — for `needs-source` entries with a well-formed `expr_hint:` (assignment form): produce a `definition <var> equals <expr>` snippet using the canonical output variable name (LHS) and the bare expression (RHS with the `<output> =` prefix stripped) as the body, with non-Catala operators rewritten into Catala forms per the quickref. If the entry has `preconditions:`, apply the **Rendering `preconditions:` to Catala** rule (above the Pass 4a header) to wrap the bare definition in a `definition … under condition … consequence equals …` block; refine the rendered preconditions against the policy text from sub-step (a) — clauses that the index pass could not translate cleanly may translate now using policy-text variable names. For `computed-only` entries in the Pass 4b queue: **skip `computed:` rules** — already written in Pass 4a.
- **`computed:` rule (no expr_hint)** — for `needs-source` entries where `expr_hint:` is absent: emit `definition <var> equals ?` as a placeholder. Record the variable in `assumptions:` ("No expr_hint available for `<name>` — Catala body must be confirmed manually"). If the entry has `preconditions:`, still render them as a `# precondition: <rendered>` Catala line comment above the placeholder so the analyst can see the gating intent.
- **`categorical:` rules** — scan the policy text for conditional policy statements (if/then, eligibility conditions, deny/approve triggers). For each, draft a `rule <condition_var> under condition <bool_expr> consequence fulfilled` (or `… consequence not fulfilled` for deny shapes) using canonical variable names. For deny-rule shapes that override a base eligibility definition, prefer the `exception <label>` form documented in `../../core/catala-authoring-quickref.md` Part 2.6 (exception-default for deny rules). If the originating index entry has `preconditions:`, seed the `under condition` clause from the rendered preconditions (per the rendering rule above) and append policy-text-derived conditions joined with `and`.
- **`table-lookup:` rule** — if the policy text references a table or schedule of thresholds, draft a `declaration structure <TableRow>` plus `definition <table_name> equals [ <row>; … ]` (semicolon-separated list literal). Lookup uses a `content of x among <table_name> such that x.<key> = <value> is unique` form or a comprehension with filter — see the quickref's "Comprehensions" section.
- **`invoke:` rule** — if the policy text's computation calls for running a ruleset module, and `ruleset_modules:` in `guidance/ruleset-modules.yaml` has a matching entry, draft a sub-scope binding inside the containing scope declaration (`<sub_var> scope <SubModule>.<SubScopeName>`) plus the `definition <sub_var>.<input_field> equals <expr>` lines feeding inputs to the sub-scope. See `../../core/catala-authoring-quickref.md` "Modules" for the cross-module call pattern.

**(d) Assign to ruleset module or main.** Same logic as Pass 4a sub-step (d).

**(e) Record notes.** Track:
- Any referenced value not found in the index or policy text → add descriptive string to `missing_info`
- Any inferential leap or assumption → add descriptive string to `assumptions`
- Any low-priority entry from Step 3 for which rules were generated → add to `assumptions`: `"<heading> not in skeleton — rule may be auxiliary or out of scope; confirm before use"`

After processing all Pass 4b entries: merge rules into `guidance/ruleset-modules.yaml` and `guidance/sample-artifacts.yaml` (Step 5 merge schema) and merge updated variable entries into `naming-manifest.yaml` (Step 6 merge schema), updating each `observations:` triple to carry the policy-text `policy_phrase` value (when available) for any observation whose `source_doc` + `section` match the entry being re-processed. Per-observation union semantics in the merge tool ensure index-derived triples are preserved unless replaced in place by a policy-text refinement on the same `(source_doc, section)` pair. Write all files.

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
    catala: |
      ```catala
      <full Catala fenced-block snippet — scope-shaped definition/rule>
      ```
```

The `catala:` field value is a literal block scalar (always use `|`) whose contents begin with a `` ```catala `` (or `` ```catala-metadata ``) opening fence and end with a closing `` ``` `` fence, matching the literate-Markdown shape that `/extract-ruleset` consumes and that `clerk typecheck` compiles. See `../../core/catala-authoring-quickref.md` Part 3 "Fence discipline" for `catala` vs `catala-metadata` selection.

**`guidance/sample-artifacts.yaml` — `sample_rules:` (merge by `id:`):**
Append unmatched rules (those not assigned to any ruleset module) to the top-level `sample_rules:` list. If the file does not exist, create it with a `sample_rules:` key. Deduplicate by `id:`.

**`guidance/sample-artifacts.yaml` — `missing_info:` (merge — append unique strings):**
Add new unique strings to the `missing_info:` list. If the key does not exist, add it. Do not remove or overwrite existing entries.

**`guidance/sample-artifacts.yaml` — `assumptions:` (merge — append unique strings):**
Add new unique strings to the `assumptions:` list. Place after `missing_info:`. Do not remove or overwrite existing entries.

### Step 6: Merge schema — naming-manifest.yaml

> This schema is applied from within Step 4 after each pass. It is documented here as the canonical reference.

**If `naming-manifest.yaml` already exists:**
Read it. For each variable name used in the generated rules, route by whether the variable appears in the manifest's `outputs:` block:
- **Output variable** (name is a key in `naming-manifest.yaml`'s `outputs:`): if not already present, append a new entry to `outputs:`.
- **Computed variable** (name is not a key in `outputs:`): if not already present in the `computed:` block, append a new entry there.

```yaml
computed:
  <variable_name>:
    observations:
      - policy_phrase: "<noun phrase from source text>"
        source_doc: "<filename.md>"
        section: "<section heading>"
outputs:
  <variable_name>:
    observations:
      - policy_phrase: "<noun phrase from source text>"
        source_doc: "<filename.md>"
        section: "<section heading>"
```
Do not modify or remove any existing entries. Per-observation triples are added to the entry's `observations:` list (dedup on the `(policy_phrase, source_doc, section)` triple); existing observations survive.

**Rename anchor via `synonyms:` (best-effort).** This writer derives observation `policy_phrase` from `computations[].description` rather than from analyst confirmation. Rule:

- Append `{name: <prior-name>}` to the entry's `synonyms:` list **only** when this writer renames a name it just emitted (e.g., it derived a candidate name and chose to disambiguate it before writing). In every other case, do not touch `synonyms:`. Synonyms are `{name}`-only in the v3.0 schema — phrase-level provenance for any prior name lives in the entry's `observations:` list, not on the synonym itself.
- Readers resolve historical names by scanning `synonyms[].name` across entries — when no rename happened, omission is harmless; the canonical key is the only name in play.

**If `naming-manifest.yaml` does not exist:**
Create it with all variable names used in the generated rules, routing each to `computed:` or `outputs:` using the same rule above:
```yaml
version: "3.0"
inputs:
  <EntityName>:        # one entry per entity from bound_entities: (if available)
    # (fields populated by /extract-ruleset Step 7b)
computed:
  <variable_name>:
    observations:
      - policy_phrase: "<noun phrase from source text>"
        source_doc: "<filename.md>"
        section: "<section heading>"
outputs:
  <variable_name>:
    observations:
      - policy_phrase: "<noun phrase from source text>"
        source_doc: "<filename.md>"
        section: "<section heading>"
```

Populate the `inputs:` block using deduplicated CamelCase entity names from `ruleset_modules[].bound_entities` in `guidance/ruleset-modules.yaml`. If `ruleset-modules.yaml` is absent, empty, or all entries have empty `bound_entities:` lists (e.g., only a `role: main` entry exists), omit the `inputs:` block and add a comment: `# inputs: will be populated by /extract-ruleset Step 7b`.

Omit the `outputs:` block if no generated variables are recognized as outputs (per the prior `naming-manifest.yaml` `outputs:` keys, when the file existed; first-time generation defaults to no outputs unless the working set includes them).

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
  - blind_work_expenses: description absent in index — observation triple emitted with policy_phrase omitted (source_doc + section retained)

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

After both passes complete, record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

```bash
xlator record-tier-manifest <domain> --tier guidance
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

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

- **Default to the compressed mirror; read source only as a fallback** — Pass 4b sub-step (a) reads `policy_facets/compressed/<rel>.md` first. Only fall back to `input/policy_docs/<rel>.md` when the compressed text is ambiguous, unclear, or questionable for the rule at hand (e.g., a precondition cannot be confidently translated, a referenced table's rows are missing from the mirror, or aggressive compression has elided needed detail). Do not browse `input/` directly or read unrelated files: every read is scoped to the specific section identified by the per-file `path:` and `heading:` in `policy_facets/computations/<rel>.md.yaml`.
- **Do not overwrite existing `sample_rules:` entries** — merge by `id:` only; never remove manually edited rules
- **Do not overwrite existing `naming-manifest.yaml` entries** — append only; the manifest is user-editable and may contain frozen names from a prior `/extract-ruleset` run
- **Do not clobber other guidance file contents** — this command writes only to `ruleset_modules[].sample_rules` in `ruleset-modules.yaml`, and to `sample_rules`, `missing_info`, `assumptions` in `sample-artifacts.yaml`; all other fields must be preserved verbatim
- **Use canonical names from the manifest** — if a variable name exists in `naming-manifest.yaml`, use it; do not re-derive or rename it
- **`catala:` is a literal block scalar** — always use the `|` block indicator; never use a quoted string or folded scalar for Catala snippets. The block contents must include the `` ```catala `` (or `` ```catala-metadata ``) opening fence and closing `` ``` `` fence so the snippet is a paste-ready fragment of literate-Markdown Catala.
- **`source:` must be a quoted sentence from the per-file file** — copy from the section's `summary:` or `computations[].description:` in `policy_facets/computations/<rel>.md.yaml`; do not paraphrase
- **Do not write `generated_at`**
- **Do not combine Pass 4a and 4b into a single write** — Pass 4a must write files and print its summary before Pass 4b begins; the point is to let the user review index-derived rules while source reads are in progress
- **When a section has an explicit `stage:` value, bind the rule's `#[error.message]` tag to that stage** — apply the same suffix-stripping normalization as `/create-ruleset-groups` so the tag matches the canonical name in `ruleset_groups[*].name`. Do not derive the stage tag from heading text when `stage:` is present; explicit doc signal beats inference
- **Do not write `stage:` or modify it** — `stage:` is single-owner; only `/extract-computations` writes the field. This skill reads it
