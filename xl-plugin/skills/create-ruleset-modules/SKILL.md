---
name: create-ruleset-modules
description: Detect Ruleset Module as Modules for a Domain
---

# Detect Ruleset Module as Modules for a Domain

Reads `guidance/skeleton.yaml` and `guidance/ruleset-groups.yaml`, extracts doc signals from the per-file files under `policy_facets/computations/`, applies six heuristics to detect ruleset modules, and writes modules to `guidance/ruleset-modules.yaml`.

A "module" is a ruleset module — a subset of rules within a ruleset group (ruleset group). Ruleset modules must not cross ruleset group boundaries.

## Input

```
/create-ruleset-modules <domain> [<approximate_num_of_modules> | <module_names>]
```

`approximate_num_of_modules` — optional positive integer (default `3`) that sets the target final module count. Consumed by Step 2's §2b (count-mode) branch; heuristics fit candidates onto this target rather than emitting raw output for a separate consolidation pass.

`module_names` — optional quoted, comma-separated list of sub-module names (e.g., `"eligibility,income,assets"`) consumed by Step 2's §2a (names-mode) branch as the authoritative target taxonomy; heuristics serve as evidence for assigning detected candidates to each name. Names only sub-modules — the main module is still resolved from `guidance/output-variables.yaml` or the Step 3 fallback prompt. The analyst's exact strings are written to each entry's `name:` verbatim (no case or separator normalization).

The second positional disambiguates by type: a bare integer is `approximate_num_of_modules`; any value that fails integer parsing (including a comma-bearing string or a single non-numeric word) is `module_names`. When both are somehow supplied, `module_names` wins and the count is ignored.

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     :::
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **`guidance/metadata.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`
   - ABSENT → Print:
     :::error
     guidance/metadata.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml
     Run /declare-target-ruleset <domain> first.
     :::
     Then stop.

4. **Per-file computations present?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - ABSENT or empty → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first.
     :::
     Then stop.

5. **`guidance/skeleton.yaml` exists?**
   - ABSENT → Print:
     :::error
     Skeleton not found: $DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml
     Run /create-skeleton <domain> first.
     :::
     Then stop.

6. **`guidance/ruleset-groups.yaml` exists?**
   - ABSENT → Print:
     :::error
     Ruleset groups not found: $DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml
     Run /create-ruleset-groups <domain> first.
     Note: this command requires ruleset groups to be defined before ruleset module detection.
     This is intentional: ruleset modules must stay within a single stage, so groups must be defined first.
     :::
     Then stop.

## Mode Detection

After pre-flight, check whether `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` exists and is non-empty:

- **Present and non-empty** → **UPDATE mode**. Existing entries are pre-confirmed. Only newly detected modules (not already in `ruleset-modules.yaml`) are added.
- **Absent or empty** → **CREATE mode**.

---

## Process

### Step 1: Load state and extract signals

Read:
- `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` — `skeleton:` key
- `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml` — `ruleset_groups:` key
- `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` — `skeleton.computations[]` block carries intermediate variable names
- Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML map. Read `data["sections"]` as the list of section blocks. Re-run Step 2 signal extraction across all entries:
  - **`expr_hint:` parse rule** (uniform across consumer skills): when a computation carries `expr_hint:`, split on the first `=`; the LHS (whitespace-trimmed) is the snake_case **output name** for that computation, and the RHS is the expression. Tokenize the RHS for snake_case identifiers (skipping numeric literals, string literals, and built-in keywords like `if`, `else`, `and`, `or`, `not`, `min`, `max`, `sum`) — those identifiers are the **input names**. When `expr_hint:` is absent (descriptive-only computation), fall back to scanning `description:` prose for variable names mentioned in the source's terminology.
  - **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
  - **Section headings** — collect all `heading:` values; reveals statutory structure
  - **File summaries** — collect all `summary:` values; reveals program scope and terminology
  - **Computation hints** — collect all `computations:` entries from sections that have the field; for each entry apply the `expr_hint:` parse rule above to recover (output name, input names). Trace variable chains via LHS-of-one / RHS-of-another to identify intermediate computed variables. Collect bare-expression values (the `expr_hint:` RHS with the `<output> =` prefix stripped) keyed by output name; collect `preconditions:` expressions keyed by output name. Preconditions feed the `shared_gate` heuristic in Step 2 — outputs whose `preconditions:` share a common clause are candidates for a co-activation cluster even when their variable names lack a common prefix. If no entry has `computations:`, skip this signal.
  - **Stage membership** — collect each section's `stage:` value (when present); index every computation in that section under the section's `stage:`. Apply the same suffix-stripping normalization as `/create-ruleset-groups` (drop a trailing `_test` / `_check` / `_evaluation`) so stage identifiers match the canonical names in `ruleset-groups.yaml`. The stage index feeds the `sequential_chain` heuristic in Step 2 (which detects linear-backbone variable-dependency chains within a single stage) and feeds the R21 stage-boundary constraint (every variable in a candidate ruleset module must share the same `stage:` value when `stage:` is populated). If no section has `stage:`, skip this signal.

Source-path mapping: a section appearing in `policy_facets/computations/<rel>.md.yaml` describes the source at `input/policy_docs/<rel>.md`. Strip the trailing `.yaml` from the per-file file's relative path under `policy_facets/computations/` and prefix with `input/policy_docs/` to reconstruct `path:`.

Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of doc signals.

In UPDATE mode: display a summary of existing `ruleset-modules.yaml` entries as pre-confirmed before scanning for new modules. Include the `role: main` entry (if present) in the pre-confirmed block — it will not be re-prompted in Step 3:

:::progress
Existing ruleset modules (pre-confirmed):
  [confirmed] earned_income      (sub)   — Shared earned income computation (reuse_across_entities)
  [confirmed] deduction_chain    (sub)   — Sequential deduction chain (depth_threshold)
  [confirmed] eligibility        (main)  — AK DOH Earned Income Exclusions
Scanning for new modules...
:::

---

### Step 2: Apply heuristics and display modules

Apply the seven heuristics in priority order. Each heuristic uses the `skeleton:` section and the Step 1 signals:

| Priority | Heuristic | Rationale value | Test |
|----------|-----------|-----------------|------|
| 1 | `reuse_across_entities` | Entity reuse | Any of the following signals — fire on the first that matches. **(a) Mirrored input schemas:** 2+ entities in `specs/naming-manifest.yaml`'s `inputs:` block share one or more field names (e.g., `ClientStatement.gross_earned_income` AND `DOLRecord.gross_earned_income`). **(b) Parallel variable-name prefixes:** 2+ variables in `skeleton.computations[].variables` (or `skeleton.inputs`) share a common suffix differing only by entity-derived snake_case prefix — e.g., `client_adjusted_earned_income` AND `dol_adjusted_earned_income`, or `applicant_countable_resources` AND `spouse_countable_resources`. **(c) Cross-source comparison language:** computation `description:` or section `summary:` contains phrases like "apply X to both A and B", "compare A and B", "reasonably compatible", "reconcile A against B" — and the variables involved share a common suffix per (b). When fired, bind the proposed module to the parallel entities (CamelCase names) and name the module after the shared computation (the common suffix, snake_case). The module's `bound_entities:` carries the entity list; the module body is the shared computation chain. |
| 2 | `policy_structure` | Policy section grouping | Named sub-section heading aggregated from `policy_facets/computations/**/*.md.yaml` covers ≥3 intermediate variables in `skeleton.computations` |
| 3 | `sequential_chain` | Sequential stage chain | ≥3 computations in source order **within a single stage** (or within a single section, if no `stage:` is populated in that file) form a linear backbone: each computation's single output variable appears as an input variable of the next computation. **Aggregation is per-file only — do not chain across `*.md.yaml` files; "source order" is the per-file YAML's section list, then the section's `computations:` list within each section.** Each computation has exactly one output (the last item in `variables:`); "sibling outputs" refers to parallel computations whose outputs both depend on a shared input (a 1→many divergence), not multiple outputs from a single computation. **Y-junction tiebreaker:** when the variable-dependency graph has a convergence (multiple parallel computations whose outputs all feed a later computation) or branch-and-rejoin shape, pick the longest spine; on length tie, pick the spine whose head appears first in source order. If the chosen spine still has length ≥3, fire `sequential_chain`; otherwise fall through to the next heuristic |
| 4 | `depth_threshold` | Sequential depth | ≥5 variables in `skeleton` whose names suggest sequential dependence (e.g., `after_*` chain, `net_*` ← `gross_*` ← `total_*`) |
| 5 | `variable_coupling` | Coupling clique | ≥3 intermediate variables in `skeleton.computations` where each references ≥2 of the others' outputs — forming a mutual dependency clique that signals a self-contained computation cluster worth isolating |
| 6 | `shared_gate` | Co-activation | ≥3 intermediate variables share a common guard-variable prefix (e.g., `eligible_*`, `applies_if_*`, `qualified_*`) **OR** ≥3 intermediate variables' `preconditions:` (collected in Step 1) all reference the same clause (e.g., three outputs whose preconditions all contain `"household contains an elderly member"`), suggesting they all fire under the same condition and belong together |
| 7 | `user_hint` | Pre-existing entries | `ruleset-modules.yaml` already exists — load existing entries as pre-confirmed (UPDATE mode) |

When both `sequential_chain` and `depth_threshold` would match the same candidate, `sequential_chain` wins by priority — the candidate's `rationale:` value is `sequential_chain`, not `depth_threshold`. Explicit stage boundaries are a stronger stage-membership signal than variable-name-prefix patterns.

**R21 stage-boundary constraint:** Every variable in a candidate ruleset module must belong to a single ruleset group (no cross-stage ruleset modules). Infer stage membership by matching variable names and computation categories to stage descriptions and stage heading signals. **When `stage:` is populated on the source sections, every variable in a candidate ruleset module must additionally share the same (post-normalization) `stage:` value** — stage agreement is a hard constraint, not a heuristic. If a candidate's variables span two groups (or two stages), either split it into per-stage ruleset modules or reject it with an explanation to the user.

In UPDATE mode: pre-confirmed entries (existing sub-modules and any existing `role: main` entry) are shown above the table with `[confirmed]` labels as in Step 1. Only newly detected modules are shown in the table below.

**Constraint branch:** If `module_names` was provided, follow **§2a (names-mode)**. Otherwise follow **§2b (count-mode)** — the default for `approximate_num_of_modules` is `3` when neither positional argument was given, so count-mode runs by default.

Neither branch writes `specs/guidance/ruleset-modules.yaml` directly — both finalize an in-memory mapping that Step 4 writes.

#### §2a — Names-mode detection

1. The user-supplied names (from the `module_names` positional) are the authoritative target sub-module taxonomy. The seven heuristics above have already produced candidate clusters; treat them as evidence for assignment rather than as the final module set.

2. For each candidate cluster, choose the most semantically-fitting target from `module_names` based on the candidate's variable names, computation descriptions, and `stage:`. Candidates that fit no supplied name go to the `unfit` set.

3. **All-unfit early-exit.** If the `unfit` set equals the entire candidate set, surface this prompt BEFORE any per-candidate decisions:

   :::user_input
   None of your supplied module_names fit the detected candidates.
   [a] Revise names — return to skill input and try different names
   [b] Accept heuristic-only output — write the unconstrained candidate set
   [c] Decline — leave `ruleset-modules.yaml` unchanged in UPDATE mode (or write `ruleset_modules: []` in CREATE mode)
   (or type in different response)
   :::

   On `[a]`: return to skill input. On `[b]`: skip the per-candidate prompts and the mapping table; the in-memory mapping is the raw heuristic candidate set (no name-mapping applied). On `[c]` or free-form decline: same as step 8's decline path.

4. For each unfit candidate when the unfit set is non-empty but smaller than the candidate set, prompt — using one combined `:::user_input` block listing all unfit candidates with per-candidate rows:

   :::user_input
   Unfit candidates (none of your supplied names fits semantically):
     <candidate_name_1> (stage: <stage>) — [a] fold into <suggested_name>  [b] keep as own sub-module  [c] drop
     <candidate_name_2> (stage: <stage>) — [a] fold into <suggested_name>  [b] keep as own sub-module  [c] drop
     ...
   (or type in different response)
   :::

   Decline-default for each row is `[b]` (keep as own sub-module). The combined block scales to N rows; do not fire one prompt per candidate.

5. **R21 violation check.** For each supplied name, the `group:` values of the candidates folded under it must all agree (primary check, using `group:` data populated via `ruleset-groups.yaml`). Additionally, when `stage:` is populated on the source sections, every candidate folded under the supplied name must share the same `stage:` value (stricter overlay). On conflict at either tier, surface a `:::detail` table of conflicts and prompt:

   :::user_input
   R21 conflict: candidates folded under "<supplied_name>" span multiple groups/stages.
   [a] Revise names
   [b] Accept partial mapping — split "<supplied_name>" into one entry per group/stage
   [c] Decline mapping
   (or type in different response)
   :::

6. Show the proposed mapping in `:::detail` as a `name → absorbed-candidates (group)` table. In UPDATE mode, include `old_name → new_name (group)` rows for any existing pre-confirmed entries being renamed BEFORE the file is rewritten — the analyst sees the full delta before confirming.

7. Prompt:

   :::user_input
   Apply this mapping? [y/n]
   (or type in different response)
   :::

8. **On `y`** — finalize the in-memory mapping. Each named target absorbs `description:` / `sample_rules:` / `depends_on:` from the candidates folded onto it, preserving each absorbed entry's `group:` / `role:` per UPDATE-mode rules. §2a does NOT write the file directly; Step 4 writes using this finalized mapping.

   **On `n` or free-form decline** — the in-memory mapping is the raw heuristic-detected candidate set (no name-mapping applied). In CREATE mode, Step 4 writes these heuristic-only candidates per its existing contract. In UPDATE mode, Step 4 preserves the prior `ruleset-modules.yaml` verbatim. Proceed to Step 3 in either case.

After confirmation (or decline), display the final in-memory mapping using the **Results table format** below, then proceed to Step 3.

#### §2b — Count-mode detection

target = `approximate_num_of_modules` (default `3` when neither positional argument was given). The seven heuristics above have already produced candidate clusters; let `N` be the resulting count.

1. **If `N == target`**, the heuristic count already matches the target — emit the single grouping directly, no plan menu. Display the results table and proceed to Step 3.

2. **If `N != target`**, draft 2–3 consolidation plans (aggressive / balanced / conservative) targeted at `target`. Each plan must not propose merges that cross ruleset group boundaries (R21 carryover). Show each plan in a `:::detail` block, then prompt:

   :::user_input
   Choose a consolidation plan (target ≈ <approximate_num_of_modules>):
   [a] Plan A — aggressive (N modules)
   [b] Plan B — balanced (N modules)
   [c] Plan C — conservative (N modules)
   [n] None — keep current modules
   (or type in different response)
   :::

   Substitute the actual module counts; append additional letters if more than three plans are offered.

3. **On a plan selection** — the in-memory mapping is the selected plan's merged grouping. §2b does NOT write the file directly; Step 4 writes using this mapping.

   **On `n` or free-form decline** — the in-memory mapping is the unmerged heuristic candidate set. In CREATE mode, Step 4 writes these heuristic-only candidates. In UPDATE mode, Step 4 preserves the prior `ruleset-modules.yaml` verbatim. Proceed to Step 3 in either case.

After confirmation (or decline), display the final in-memory mapping using the **Results table format** below, then proceed to Step 3.

**Results table format**

Display the final in-memory mapping in exactly this format:

:::detail
Ruleset Modules
─────────────────────────────────────────────────────────────────────────
  # │ Name              │ Role │ Bound Entities          │ Heuristic
  1 │ earned_income     │ sub  │ ClientData, DOLRecord   │ reuse_across_entities
  2 │ deduction_chain   │ sub  │ Household               │ depth_threshold
─────────────────────────────────────────────────────────────────────────
:::

**If zero NEW modules are detected**, print:

```
No new ruleset modules identified.
```

- In UPDATE mode: print `Existing entries preserved unchanged.` and exit without writing.
- In CREATE mode: write `guidance/ruleset-modules.yaml` with `ruleset_modules: []` and suggest next step.

---

### Step 3: Derive main module name

**Skip this step if:**
- CREATE mode and zero sub-modules were detected (the single-file extraction path is unchanged)
- UPDATE mode and a `role: main` entry already exists (already shown as `[confirmed]` above)

Otherwise, derive the main module name automatically — no prompt:

1. Identify the primary output: read `guidance/output-variables.yaml` and find the entry with `primary: true`. If present, strip trailing `_check`, `_determination`, `_result`, `_outcome`, or `_eligibility` from the entry's name and use the result.
2. If no primary output variable is declared, prompt:
   :::user_input
   No primary output found in guidance/output-variables.yaml. What should the main module be named? (e.g., `eligibility`, `income_test`)
   :::

Example:
- `primary.name: eligibility_determination` → `eligibility`

Print the derived name so the user can see what was chosen:

:::important
Main module: eligibility  (edit guidance/ruleset-modules.yaml to rename)
:::

---

### Step 4: Write `guidance/ruleset-modules.yaml`

Write all detected new candidates to `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml`:
- In UPDATE mode: overwrite the file with the full final list (existing pre-confirmed + new confirmed); preserve `role:`, `depends_on:`, and `sample_rules:` from existing entries verbatim
- In CREATE mode with zero modules: write `ruleset_modules: []`

Each confirmed sub-module entry must use this exact YAML format:

```yaml
ruleset_modules:
  - name: <snake_case>
    description: "<what this ruleset module computes>"
    bound_entities: [<EntityName1>, <EntityName2>]
    rationale: <heuristic value, e.g. reuse_across_entities>
    depends_on: []
```

When a main module name was confirmed in Step 3, append the main module entry at the end of the list. Use `display_name` from `guidance/metadata.yaml` as the main module description:

```yaml
  - name: <program_name>
    description: "<display_name value from guidance/metadata.yaml>"
    bound_entities: []
    rationale: main_module
    role: main
    depends_on: [<all sub-module names from this run, comma-separated>]
```

`bound_entities` values use CamelCase entity names (e.g., `ClientData`, `DOLRecord`, `Household`) — not snake_case.
Do not write `generated_at`.

Print:

:::important
$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml [CREATED]
:::

---

### Step 5: Record guidance-tier manifest

Record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

```bash
xlator record-tier-manifest <domain> --tier guidance
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

---

### Step 6: Suggest next steps

:::next_step
Next: Run /extract-sample-rules <domain> to extract sample rules.
:::

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml    [CREATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of doc signals
- **Heuristic 1 (`reuse_across_entities`) fires on any of three independent signals — do not require all three.** A mirrored input schema in `naming-manifest.yaml` (e.g., `ClientStatement.gross_earned_income` AND `DOLRecord.gross_earned_income`) is sufficient on its own; a parallel variable-name prefix in the skeleton (`client_*` AND `dol_*` chains) is sufficient on its own; cross-source comparison language in computation `description:` or section `summary:` ("apply X to both A and B", "reasonably compatible", "compare A and B") is a corroborating signal. Missing this heuristic because only one signal was checked is the single most expensive miss — it silently downgrades the module structure to a single-flow shape and the analyst loses the explicit "reusable computation" capture that downstream consumers depend on.
- In UPDATE mode with zero new modules, preserve existing entries unchanged — do not clear `ruleset-modules.yaml`
- In UPDATE mode with new modules, overwrite the file with the full final list (existing pre-confirmed + new confirmed) — do not append only the new ones
- A ruleset module must not cross ruleset group boundaries — all variables in a candidate must belong to a single stage; if a candidate spans groups, split it or reject it with an explanation to the user
- **`sequential_chain` candidates must not cross stage boundaries** — when `stage:` is populated, every variable in the candidate must share the same (post-normalization) `stage:` value; split or reject per the existing R21 protocol
- **`sequential_chain` aggregation is per-file only** — do not chain computations across separate `*.md.yaml` files even when they share a `stage:` value; per-file workers in `/extract-computations` have no cross-file context, so cross-file chains have no source-order anchor and would produce non-deterministic candidates
- **Do not write `stage:` or modify it** — `stage:` is single-owner; only `/extract-computations` writes the field. This skill reads it
- Each sub-module entry must have `name`, `description`, `bound_entities`, `rationale`, and `depends_on` — never omit any field; `role:` defaults to `sub` when absent
- The main module entry additionally requires `role: main`, `bound_entities: []`, `rationale: main_module`, and `depends_on:` listing all sub-module names
- Do not write the `role: main` entry when zero sub-modules were detected — Step 3 only runs when at least one sub-module is present
- In CREATE mode with zero modules, write `ruleset_modules: []` — never omit the key entirely
- `bound_entities` values use CamelCase entity names (e.g., `ClientData`, `DOLRecord`, `Household`) — not snake_case; main module always uses `bound_entities: []`
- In UPDATE mode, preserve `role:`, `depends_on:`, and `sample_rules:` from existing entries — never strip fields added by a prior run
- Do not write `generated_at`
- Step 2 consumes `module_names` and `approximate_num_of_modules` as detection inputs — do not first emit heuristic-only candidates and then reshape them in a later step; §2a (names-mode) and §2b (count-mode) shape the detection result directly
- In §2a, surface unfit candidates inline via the `[a]/[b]/[c]` fold/keep/drop prompt — do not silently force-fit them under a supplied name, drop them, or hide them in an unmapped bucket without an explicit confirmation
- In §2b, skip the 3-plan menu when the heuristic candidate count already matches `approximate_num_of_modules` (`N == target`) — drafting a menu in this case is unnecessary friction; only draft aggressive / balanced / conservative plans when the gap is non-zero
- Neither §2a nor §2b writes `ruleset-modules.yaml` directly — both finalize an in-memory mapping that Step 4 writes
- This command has 6 steps — show a step checklist at the completion of each step (>3 steps rule)
- Note: requiring `ruleset_groups:` before ruleset module detection reverses the monolith's Step 4 → Step 5 order. This is intentional: ruleset modules must stay within a single stage.
