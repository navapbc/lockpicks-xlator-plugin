# Detect Sub-Ruleset as Modules for a Domain

Reads `skeleton:` and `workflow_stages:` from `guidance.yaml`, extracts doc signals from `input-index.yaml`, applies four heuristics to detect sub-ruleset modules, and writes modules to `guidance.yaml` as `sub_rulesets:` after `workflow_stages:`.

A "module" is a sub-ruleset — a subset of rules within a ruleset group (workflow stage). Sub-rulesets must not cross workflow stage boundaries.

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
     Run /declare-ruleset-io <domain> first.
     ```
     Then stop.

4. **`input-index.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     ```
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /index-inputs <domain> first.
     ```
     Then stop.

5. **`skeleton:` key present in `guidance.yaml`?**
   - ABSENT → Print:
     ```
     Skeleton not found in guidance.yaml.
     Run /create-skeleton <domain> first.
     ```
     Then stop.

6. **`workflow_stages:` key present in `guidance.yaml`?**
   - ABSENT → Print:
     ```
     Workflow stages not found in guidance.yaml.
     Run /create-ruleset-groups <domain> first.
     Note: this command requires workflow stages to be defined before sub-ruleset detection.
     This is intentional: sub-rulesets must stay within a single stage, so stages must be defined first.
     ```
     Then stop.

## Mode Detection

After pre-flight, check whether `sub_rulesets:` already exists and is non-empty in `guidance.yaml`:

- **Present and non-empty** → **UPDATE mode**. Existing entries are pre-confirmed. Only newly detected modules (not already in `sub_rulesets:`) are added.
- **Absent or empty** → **CREATE mode**.

---

## Process

### Step 1: Load state and extract signals

Read:
- `$DOMAINS_DIR/<domain>/specs/guidance.yaml` — load `skeleton:`, `workflow_stages:`, and `intermediate_variables.categories` (for variable names)
- `$DOMAINS_DIR/<domain>/specs/input-index.yaml` — re-run Step 2 signal extraction:
  - **Topic tags** — collect all `tags:` values across all sections; cluster to find prominent domain areas
  - **Section headings** — collect all `heading:` values; reveals statutory structure
  - **File summaries** — collect all `summary:` values; reveals program scope and terminology
  - **Computation hints** — collect all `computations:` entries from sections that have the field; trace variable chains (last item in `variables` list is the output); collect `expr_hint` values keyed by output variable. If the index has no `computations:` entries, skip this signal.

Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of doc signals.

In UPDATE mode: display a summary of existing `sub_rulesets:` as pre-confirmed before scanning for new modules:

```
Existing sub-rulesets (pre-confirmed):
  [confirmed] earned_income      — Shared earned income computation (reuse_across_entities)
  [confirmed] deduction_chain    — Sequential deduction chain (depth_threshold)
Scanning for new modules...
```

---

### Step 2: Apply heuristics and display modules

Apply the four heuristics in priority order. Each heuristic uses the `skeleton:` section and the Step 1 signals:

| Priority | Heuristic | Rationale value | Test |
|----------|-----------|-----------------|------|
| 1 | `reuse_across_entities` | Entity reuse | 2+ entity names in `input_variables.categories` (or `skeleton.inputs`) where a common computation prefix applies to each — e.g., `client_earned_income` and `dol_earned_income` suggest the same `earned_income` sub-ruleset bound to two entities (ClientData, DOLRecord) |
| 2 | `policy_structure` | Policy section grouping | Named sub-section heading from `input-index.yaml` covers ≥3 intermediate variables in `skeleton.computations` |
| 3 | `depth_threshold` | Sequential depth | ≥5 variables in `skeleton` whose names suggest sequential dependence (e.g., `after_*` chain, `net_*` ← `gross_*` ← `total_*`) |
| 4 | `user_hint` | Pre-existing entries | `sub_rulesets:` already populated in `guidance.yaml` — load existing entries as pre-confirmed (UPDATE mode) |

**R21 stage-boundary constraint:** Every variable in a candidate sub-ruleset must belong to a single workflow stage (no cross-stage sub-rulesets). Infer stage membership by matching variable names and computation categories to stage descriptions and phase heading signals. If a candidate's variables span two stages, either split it into per-stage sub-rulesets or reject it with an explanation to the user.

In UPDATE mode: pre-confirmed entries are shown above the table with `[confirmed]` labels (not in the table). Only newly detected modules are shown in the table.

**If one or more new modules are detected**, display the results table in exactly this format:

```
Sub-Ruleset Modules
─────────────────────────────────────────────────────────────────────────
  # │ Sub-Module Name   │ Bound Entities          │ Heuristic
  1 │ earned_income     │ ClientData, DOLRecord   │ reuse_across_entities
  2 │ deduction_chain   │ Household               │ depth_threshold
─────────────────────────────────────────────────────────────────────────
```

All detected modules are confirmed automatically. Proceed immediately to Step 3.

**If zero NEW modules are detected**, print:

```
No new sub-ruleset modules identified.
```

- In UPDATE mode: print `Existing entries preserved unchanged.` and exit without writing.
- In CREATE mode: write `sub_rulesets: []` to `guidance.yaml` and suggest next step.

---

### Step 3: Write `sub_rulesets:`

Write all detected new candidates to `sub_rulesets:`.

Write `sub_rulesets:` to `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:
- Insert after `workflow_stages:`, before `input_variables:` (if present), or at end of file if neither follows
- In UPDATE mode: overwrite `sub_rulesets:` with the full final list (existing pre-confirmed + new confirmed)
- In CREATE mode with zero modules: write `sub_rulesets: []`

Each confirmed entry must use this exact YAML format:

```yaml
sub_rulesets:
  - name: <snake_case>
    description: "<what this sub-ruleset computes>"
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
Next: Run /extract-ruleset <domain> to extract the CIVIL ruleset.
      Re-run /refine-guidance <domain> at any time to further refine guidance.
```

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [UPDATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of doc signals
- `sub_rulesets:` is inserted after `workflow_stages:` in `guidance.yaml`, not at the end of the file unless no later keys exist
- In UPDATE mode with zero new modules, preserve existing entries unchanged — do not clear `sub_rulesets:`
- In UPDATE mode with new modules, overwrite `sub_rulesets:` with the full final list (existing pre-confirmed + new confirmed) — do not append only the new ones
- A sub-ruleset must not cross workflow stage boundaries — all variables in a candidate must belong to a single stage; if a candidate spans stages, split it or reject it with an explanation to the user
- Each `sub_rulesets:` entry must have `name`, `description`, `bound_entities`, and `rationale` — never omit any field
- In CREATE mode with zero modules, write `sub_rulesets: []` — never omit the key entirely
- `bound_entities` values use CamelCase entity names (e.g., `ClientData`, `DOLRecord`, `Household`) — not snake_case
- This command has 3 steps — the step checklist rule (>3 steps) does NOT apply; do not show a step checklist
- Note: requiring `workflow_stages:` before sub-ruleset detection reverses the monolith's Step 4 → Step 5 order. This is intentional: sub-rulesets must stay within a single stage.
