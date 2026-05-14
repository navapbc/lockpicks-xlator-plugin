---
name: create-skeleton
description: Build Computation Skeleton for a Domain
---

# Build Computation Skeleton for a Domain

Extract structured signals from `policy_facets/computations/` and the naming manifest via `xlator skeleton-signals`, produce an AI enrichment JSON (descriptions, ASCII flow diagram, primary selection, prompt-context proposals), then have `xlator emit-skeleton` validate and merge into five guidance files. The deterministic signal extraction, schema enforcement, and re-run preservation all live in the two tools.

## Input

```
/create-skeleton <domain>
```

Read `../../core/output-fencing.md` now.

Read `../../core/examples/skeleton/canonical.yaml` now.

## Pre-flight

1. **Domain argument provided?**
   - NO → List directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt for selection inside `:::user_input`. Then continue.

Both tools re-validate every other pre-flight condition (domain folder, `metadata.yaml`, `prompt-context.yaml`, `naming-manifest.yaml`, non-empty `policy_facets/computations/`) and exit 2 with a clear stderr message on failure. Relay stderr verbatim inside `:::error` and stop.

## Mode Detection

Check whether `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` exists:

- **Absent** → CREATE mode. Tool mode: `create`. Proceed to Process.
- **Present** → UPDATE mode. Display the existing skeleton summary and prompt:
  :::user_input
  Skeleton already exists: <N> computations across <M> stages

  [a]ccept — keep as-is and exit
  [b]replace — overwrite the four Step-4 files unconditionally
  [c]revise — preserve analyst-edited fields, fill in the rest
  :::
  - `a` → Exit without invoking the tools. Emit `:::next_step` pointing at `/create-ruleset-groups`.
  - `b` → Tool mode: `replace`. Proceed.
  - `c` → Tool mode: `revise`. Proceed.

## Process

### Step 1: Extract signals

```bash
xlator skeleton-signals <domain>
```

The stdout is a single JSON object. Hold it in memory for Step 2. On non-zero exit, relay stderr inside `:::error` and stop.

### Step 2: Produce enrichment JSON

Read the signals JSON and produce an `enrichment.json` object. The emit tool validates the schema and exits 1 on any violation with `ERROR: enrichment.<dotted-path>: <reason>` — fix the enrichment and re-run, do not bypass.

**Required top-level fields:**

- `prompt_context_additions: {constraints, standards, guidance, edge_cases}` — each is a list of strings. Additions only; do not re-list items already in `signals.prompt_context_existing`. Ground proposals in `signals.tags`, `signals.headings`, `signals.summaries`, `signals.expr_hints`, and `signals.mirrored_fields`.
- `skeleton_flow_diagram` — ASCII string. Compose from `signals.expr_hints`, `signals.intermediate_variables`, and `signals.stage_index`.
- `skeleton_inputs`, `skeleton_outputs` — flat ordered lists of variable names. Inputs drawn from `signals.entities` + `signals.intermediate_variables` (preserve stage order when present); outputs from `signals.outputs_in_manifest` in declaration order.
- `output_variables: {<name>: {description, primary, examples?}}` — one entry per `signals.outputs_in_manifest` name. Set `primary: true` on the name in `signals.output_primary_hint` when non-null; otherwise pick the most-eligibility-like output. **Exactly one entry must have `primary: true`.** `examples:` carries sample values (NOT synonyms — those live in `naming-manifest.yaml`).
- `input_variables: {categories: [{category, description, fields: [{name_ref}], examples?, source_file?, source_section?, exact_phrase?}]}` — cluster `signals.entities` fields into semantic categories. Every `name_ref:` must appear in `signals.entities[<entity>]` OR `signals.intermediate_variables` (the emit tool warns on unrecognized refs).
- `constants_and_tables: {<Name>: {description}}` — keyed by names in `signals.candidate_constants_and_tables`. Drop irrelevant candidates by omitting them. **Do not invent names** — the emit tool drops invented entries and warns. Provenance is filled mechanically from the matching candidate row.

**Parallel-entity reuse signal.** When `signals.mirrored_fields` is non-empty, expand parallel runs with entity-prefixed names (e.g., `client_adjusted_earned_income`, `dol_adjusted_earned_income`) in `skeleton_inputs` and intermediates. Flattening hides the reuse signal `/create-ruleset-modules`'s `reuse_across_entities` heuristic needs.

Write the enrichment JSON to a tempfile (e.g., `tempfile.NamedTemporaryFile(suffix='.json')`).

### Step 3: Validate and write guidance files

```bash
xlator emit-skeleton <domain> --mode <create|replace|revise> --enrichment <tmpfile>
```

Parse the JSON header on stdout; the line `--- EMIT-SKELETON-HEADER-END ---` divides header from human summary.

- Exit 1 (schema violation): relay stderr inside `:::error` and stop.
- Exit 2 (`create` collision): relay stderr inside `:::error` and stop.
- Exit 0: relay the human summary inside `:::important`.

Then record the manifest:

```bash
xlator record-tier-manifest <domain> --tier guidance
```

Non-zero exit → `:::error` with stderr; stop. Otherwise emit:

:::next_step
Next: Run /create-ruleset-groups <domain> to propose ruleset groups.
:::

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` | Merged (additions only) |
| `$DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml` | Created or revised |
| `$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml` | Created or revised |
| `$DOMAINS_DIR/<domain>/specs/guidance/input-variables.yaml` | Created or revised |
| `$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml` | Created or revised |

## Common Mistakes to Avoid

- The emit tool enforces the enrichment schema (including the exactly-one-`primary: true` invariant) with a specific stderr error per violation — fix the enrichment, do not bypass.
- `output-variables.yaml`'s `examples:` carries sample values, NOT synonym names.
- Constants without provenance in `signals.candidate_constants_and_tables` are dropped — do not invent provenance in the enrichment. Hand-add post-emit if a missed constant matters.
- `revise` mode preserves analyst-edited fields. Do not regenerate them.
- In UPDATE-mode `[a]ccept`, exit without invoking either tool.
- When `signals.mirrored_fields` is non-empty, expand parallel runs with per-entity-prefixed names — do not flatten.
