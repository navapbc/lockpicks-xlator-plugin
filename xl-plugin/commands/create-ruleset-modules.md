# Detect Ruleset Module as Modules for a Domain

Reads `skeleton:` and `ruleset_groups:` from `guidance.yaml`, extracts doc signals from `input-index.yaml`, applies six heuristics to detect ruleset modules, and writes modules to `guidance.yaml` as `ruleset_modules:` after `ruleset_groups:`.

A "module" is a ruleset module — a subset of rules within a ruleset group (ruleset group). Ruleset modules must not cross ruleset group boundaries.

## Input

```
/create-ruleset-modules <domain>
```

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     ```
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     ```
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     ```
     Domain not found: $DOMAINS_DIR/<domain>/
     ```
     Then stop.

3. **`guidance.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`
   - ABSENT → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:declare-ruleset-io <domain> first.
     ```
     Then stop.

4. **`input-index.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     ```
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /xl:index-inputs <domain> first.
     ```
     Then stop.

5. **`skeleton:` key present in `guidance.yaml`?**
   - ABSENT → Print:
     ```
     Skeleton not found in guidance.yaml.
     Run /xl:create-skeleton <domain> first.
     ```
     Then stop.

6. **`ruleset_groups:` key present in `guidance.yaml`?**
   - ABSENT → Print:
     ```
     Ruleset groups not found in guidance.yaml.
     Run /xl:create-ruleset-groups <domain> first.
     Note: this command requires ruleset groups to be defined before ruleset module detection.
     This is intentional: ruleset modules must stay within a single stage, so stages must be defined first.
     ```
     Then stop.

## Mode Detection

After pre-flight, check whether `ruleset_modules:` already exists and is non-empty in `guidance.yaml`:

- **Present and non-empty** → **UPDATE mode**. Existing entries are pre-confirmed. Only newly detected modules (not already in `ruleset_modules:`) are added.
- **Absent or empty** → **CREATE mode**.

---

## Process

### Step 1: Load state and extract signals

Read:
- `$DOMAINS_DIR/<domain>/specs/guidance.yaml` — load `skeleton:`, `ruleset_groups:`, and `intermediate_variables.categories` (for variable names)
- `$DOMAINS_DIR/<domain>/specs/input-index.yaml` — re-run Step 2 signal extraction:
  - **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
  - **Section headings** — collect all `heading:` values; reveals statutory structure
  - **File summaries** — collect all `summary:` values; reveals program scope and terminology
  - **Computation hints** — collect all `computations:` entries from sections that have the field; trace variable chains (last item in `variables` list is the output); collect `expr_hint` values keyed by output variable. If the index has no `computations:` entries, skip this signal.

Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of doc signals.

In UPDATE mode: display a summary of existing `ruleset_modules:` as pre-confirmed before scanning for new modules:

```
Existing ruleset modules (pre-confirmed):
  [confirmed] earned_income      — Shared earned income computation (reuse_across_entities)
  [confirmed] deduction_chain    — Sequential deduction chain (depth_threshold)
Scanning for new modules...
```

---

### Step 2: Apply heuristics and display modules

Apply the four heuristics in priority order. Each heuristic uses the `skeleton:` section and the Step 1 signals:

| Priority | Heuristic | Rationale value | Test |
|----------|-----------|-----------------|------|
| 1 | `reuse_across_entities` | Entity reuse | 2+ entity names in `input_variables.categories` (or `skeleton.inputs`) where a common computation prefix applies to each — e.g., `client_earned_income` and `dol_earned_income` suggest the same `earned_income` ruleset module bound to two entities (ClientData, DOLRecord) |
| 2 | `policy_structure` | Policy section grouping | Named sub-section heading from `input-index.yaml` covers ≥3 intermediate variables in `skeleton.computations` |
| 3 | `depth_threshold` | Sequential depth | ≥5 variables in `skeleton` whose names suggest sequential dependence (e.g., `after_*` chain, `net_*` ← `gross_*` ← `total_*`) |
| 4 | `variable_coupling` | Coupling clique | ≥3 intermediate variables in `skeleton.computations` where each references ≥2 of the others' outputs — forming a mutual dependency clique that signals a self-contained computation cluster worth isolating |
| 5 | `shared_gate` | Co-activation | ≥3 intermediate variables share a common guard-variable prefix (e.g., `eligible_*`, `applies_if_*`, `qualified_*`), suggesting they all fire under the same condition and belong together |
| 6 | `user_hint` | Pre-existing entries | `ruleset_modules:` already populated in `guidance.yaml` — load existing entries as pre-confirmed (UPDATE mode) |

**R21 stage-boundary constraint:** Every variable in a candidate ruleset module must belong to a single ruleset group (no cross-stage ruleset modules). Infer stage membership by matching variable names and computation categories to stage descriptions and phase heading signals. If a candidate's variables span two stages, either split it into per-stage ruleset modules or reject it with an explanation to the user.

In UPDATE mode: pre-confirmed entries are shown above the table with `[confirmed]` labels (not in the table). Only newly detected modules are shown in the table.

**If one or more new modules are detected**, display the results table in exactly this format:

```
Ruleset Modules
─────────────────────────────────────────────────────────────────────────
  # │ Sub-Module Name   │ Bound Entities          │ Heuristic
  1 │ earned_income     │ ClientData, DOLRecord   │ reuse_across_entities
  2 │ deduction_chain   │ Household               │ depth_threshold
─────────────────────────────────────────────────────────────────────────
```

All detected modules are confirmed automatically. Proceed immediately to Step 3.

**If zero NEW modules are detected**, print:

```
No new ruleset modules identified.
```

- In UPDATE mode: print `Existing entries preserved unchanged.` and exit without writing.
- In CREATE mode: write `ruleset_modules: []` to `guidance.yaml` and suggest next step.

---

### Step 3: Write `ruleset_modules:`

Write all detected new candidates to `ruleset_modules:`.

Write `ruleset_modules:` to `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:
- Insert after `ruleset_groups:` and before `constraints:` (if present), or at end of file if neither follows
- In UPDATE mode: overwrite `ruleset_modules:` with the full final list (existing pre-confirmed + new confirmed)
- In CREATE mode with zero modules: write `ruleset_modules: []`

Each confirmed entry must use this exact YAML format:

```yaml
ruleset_modules:
  - name: <snake_case>
    description: "<what this ruleset module computes>"
    bound_entities: [<EntityName1>, <EntityName2>]
    rationale: <heuristic value, e.g. reuse_across_entities>
```

`bound_entities` values use CamelCase entity names (e.g., `ClientData`, `DOLRecord`, `Household`) — not snake_case.

Print:

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml [UPDATED]
```

Then suggest next steps:

```
Next: Run /xl:extract-sample-rules <domain> to extract sample rules.
```

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [UPDATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of doc signals
- `ruleset_modules:` is inserted after `ruleset_groups:` and before `constraints:` in `guidance.yaml`, not at the end of the file unless no later keys exist
- In UPDATE mode with zero new modules, preserve existing entries unchanged — do not clear `ruleset_modules:`
- In UPDATE mode with new modules, overwrite `ruleset_modules:` with the full final list (existing pre-confirmed + new confirmed) — do not append only the new ones
- A ruleset module must not cross ruleset group boundaries — all variables in a candidate must belong to a single stage; if a candidate spans stages, split it or reject it with an explanation to the user
- Each `ruleset_modules:` entry must have `name`, `description`, `bound_entities`, and `rationale` — never omit any field
- In CREATE mode with zero modules, write `ruleset_modules: []` — never omit the key entirely
- `bound_entities` values use CamelCase entity names (e.g., `ClientData`, `DOLRecord`, `Household`) — not snake_case
- This command has 3 steps — the step checklist rule (>3 steps) does NOT apply; do not show a step checklist
- Note: requiring `ruleset_groups:` before ruleset module detection reverses the monolith's Step 4 → Step 5 order. This is intentional: ruleset modules must stay within a single stage.
