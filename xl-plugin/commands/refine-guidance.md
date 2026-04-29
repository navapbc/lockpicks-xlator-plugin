# Refine Ruleset Guidance for a Domain

Create or update `guidance.yaml` for a domain by orchestrating the granular guidance-authoring commands in sequence. On first run (CREATE), guides the user through guidance template selection (or AI-suggested rulesets) to bootstrap `guidance.yaml`, then runs each authoring step in sequence. On subsequent runs (UPDATE), loads the existing `guidance.yaml` and runs each step to refine it.

The **guidance template** (in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` and `$DOMAINS_DIR/guidance-templates/`) provides an initial ruleset guidance that is then customized per domain in `$DOMAINS_DIR/<domain>/specs/guidance.yaml`.

## Input

```
/refine-guidance <domain>
```

Read `$CLAUDE_PLUGIN_ROOT/core/output-fencing.md` now.

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
     Run: /xl:new-domain <domain>
     :::
     Then stop. Do not scaffold a new domain here — that's `/xl:new-domain`'s job.

3. **Input index required?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - **ABSENT** → Print:
     :::error
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /xl:index-inputs <domain> first, then re-run /xl:refine-guidance <domain>.
     :::
     Stop.

4. **Detect mode** — check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:
   - **Present** → **UPDATE mode**
   - **Absent** → **CREATE mode**

---

## Process

Steps in this command:
1. Template / Load
2. Computation skeleton
3. Ruleset groups
4. Ruleset modules
5. Sample rules
6. Tag output variables
7. Sample tests

### Step 1 [CREATE]: Bootstrap guidance.yaml

Two paths are available. Present as options:

**a. Template selection** — Choose a guidance template:

Scan `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/*.yaml` and `$DOMAINS_DIR/guidance-templates/*.yaml` for all available guidance template files, reading only the top 5 lines to get the `template_id`, `display_name`, and `description` for each file.

- Present a list for the user to choose one where each option shows: "`<template_id>`: <display_name> (<full_file_path>)".
- Instead of "(Type in another answer)", present "(or paste path of file to use as the ruleset guidance template)".

Print a summary of the selected file's content for the user to review.

After the user confirms the selected guidance template, copy the guidance template to `$DOMAINS_DIR/<domain>/specs/guidance.yaml` and add the following fields as metadata at the top of the file:
```yaml
source_template: <guidance template file (without extension)>
generated_at: <today YYYY-MM-DD>
```

The `source_template` and `generated_at` fields are inserted immediately after `template_id:` so the file reads top-to-bottom: metadata → scope → guidance → variables.

:::important
Created $DOMAINS_DIR/<domain>/specs/guidance.yaml
:::

**b. AI-suggest** — Let the AI propose candidate rulesets based on the index:

1. Prompt: "Enter a hint to narrow candidate rulesets (e.g. 'eligibility', 'benefit calculation'), or 'all' to suggest all:"
2. Run `/xl:suggest-target-ruleset <domain> [<hint>]` (omit `<hint>` if the user responded with 'all'), following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/suggest-target-ruleset.md`. Skip pre-flight — domain and index already verified above.
3. Present the list of generated candidate files from `specs/suggested_rulesets/`.
4. Ask the user which candidate to use.
5. Run `/xl:declare-target-ruleset <domain> <chosen_ruleset>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/declare-target-ruleset.md`. Skip pre-flight — domain already verified above.

After either path, `guidance.yaml` exists and Step 2 may proceed.

### Step 1 [UPDATE]: Load existing file

Read `$DOMAINS_DIR/<domain>/specs/guidance.yaml`. Print a summary:
:::detail
File: $DOMAINS_DIR/<domain>/specs/guidance.yaml
Current guidance: <display_name> (source: <source_template>, updated: <generated_at>)
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: <N> computations across <N> intermediate categories, <N> example rules
:::
(Show `Skeleton: none` if no `computations:` fields are present yet in `intermediate_variables` and no `sample_rules:` section exists.)

---

### Step 2: Computation skeleton

Run `/xl:create-skeleton <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/create-skeleton.md`. Skip pre-flight — domain, `guidance.yaml`, and `input-index.yaml` already verified above.

The sub-command handles:
- Doc signal extraction from `input-index.yaml` (topic tags, section headings, file summaries, computation hints)
- Merging doc-derived proposals into the four guidance sections (`constraints`, `standards`, `guidance`, `edge_cases`)
- Building and confirming the computation skeleton with the user
- Writing the confirmed skeleton and variable categories to `guidance.yaml` under `skeleton:`

In UPDATE mode, `create-skeleton` detects the existing skeleton and offers accept / replace / revise.

### Step 3: Ruleset groups

Run `/xl:create-ruleset-groups <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/create-ruleset-groups.md`. Skip pre-flight — already verified above.

The sub-command scans `input-index.yaml` for phase headings, proposes `ruleset_groups`, and writes the confirmed list to `guidance.yaml`. In UPDATE mode, it detects existing `ruleset_groups:` and offers accept / replace / merge.

### Step 4: Ruleset modules

Run `/xl:create-ruleset-modules <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/create-ruleset-modules.md`. Skip pre-flight — already verified above.

The sub-command applies heuristics to detect ruleset module candidates from the confirmed skeleton and `ruleset_groups:`, presents them for confirmation, and writes the confirmed list to `guidance.yaml` under `ruleset_modules:`. In UPDATE mode, existing entries are pre-confirmed and preserved.

### Step 5: Sample rules

Run `/xl:extract-sample-rules <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/extract-sample-rules.md`. Skip pre-flight — already verified above.

The sub-command generates a comprehensive set of CIVIL rules grounded in `input-index.yaml` entries and writes them to `guidance.yaml` under `sample_rules:`. Runs non-interactively — no mid-run prompting.

### Step 6: Tag output variables

Run `/xl:tag-vars-to-include-with-output <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/tag-vars-to-include-with-output.md`. Skip pre-flight — already verified above.

The sub-command auto-detects invoke-derived variables (dot-access expressions in `computations:`) and writes the selection to `guidance.yaml` under `intermediate_variables.include_with_output`. Running after Step 5 ensures variables visible only in CIVIL snippets are captured. Runs non-interactively.

### Step 7: Sample tests

Run `/xl:create-sample-tests <domain>`, following the instructions in `$CLAUDE_PLUGIN_ROOT/commands/create-sample-tests.md`. Skip pre-flight — already verified above.

The sub-command generates pre-extraction test scaffolding from `guidance.yaml` and writes test cases under `sample_tests:`. Requires `sample_rules:` to be present (written by Step 5). Runs non-interactively.

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [CREATED or UPDATED]
```

## Common Mistakes to Avoid

- Do not add `edge_cases:` to ruleset guidance template files in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` — they are domain-agnostic; `edge_cases:` belongs only in per-domain `guidance.yaml`
- `source_template` is never updated after initial creation — it records which guidance template the file was originally created from
- Do not create or scaffold a domain folder here — if the domain doesn't exist, stop and refer to `/xl:new-domain`
- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-index.yaml` is the sole source of doc signals
- `guidance.yaml` is created in Step 1 [CREATE], not deferred to later steps — it always exists before Step 2 begins
