---
name: refine-guidance
description: Refine Ruleset Guidance for a Domain
---

# Refine Ruleset Guidance for a Domain

Create or update `guidance.yaml` for a domain by orchestrating the granular guidance-authoring skills in sequence. On first run (CREATE), guides the user through guidance template selection (or AI-suggested rulesets) to bootstrap `guidance.yaml`, then runs each authoring step in sequence. On subsequent runs (UPDATE), loads the existing `guidance.yaml` and runs each step to refine it.

The **guidance template** (in `../../core/guidance-templates/` and `$DOMAINS_DIR/guidance-templates/`) provides an initial ruleset guidance that is then customized per domain in `$DOMAINS_DIR/<domain>/specs/guidance.yaml`.

## Input

```
/refine-guidance <domain>
```

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
     Run: /new-domain <domain>
     :::
     Then stop. Do not scaffold a new domain here — that's `/new-domain`'s job.

3. **Input index required?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - **ABSENT** → Print:
     :::error
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /index-inputs <domain> first, then re-run /refine-guidance <domain>.
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

Scan `../../core/guidance-templates/*.yaml` and `$DOMAINS_DIR/guidance-templates/*.yaml` for all available guidance template files, reading only the top 5 lines to get the `template_id`, `display_name`, and `description` for each file.

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
2. Run `/suggest-target-ruleset <domain> [<hint>]` (omit `<hint>` if the user responded with 'all'). Skip pre-flight — domain and index already verified above.
3. Present the list of generated candidate files from `specs/suggested_rulesets/`.
4. Ask the user which candidate to use.
5. Run `/declare-target-ruleset <domain> <chosen_ruleset>`. Skip pre-flight — domain already verified above.

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

Run `/create-skeleton <domain>`. Skip pre-flight — domain, `guidance.yaml`, and `input-index.yaml` already verified above.

### Step 3: Ruleset groups

Run `/create-ruleset-groups <domain>`. Skip pre-flight — already verified above.

### Step 4: Ruleset modules

Run `/create-ruleset-modules <domain>`. Skip pre-flight — already verified above.

### Step 5: Sample rules

Run `/extract-sample-rules <domain>`. Skip pre-flight — already verified above.

### Step 6: Tag output variables

Run `/tag-vars-to-include-with-output <domain>`. Skip pre-flight — already verified above.

### Step 7: Sample tests

Run `/create-sample-tests <domain>`. Skip pre-flight — already verified above.

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [CREATED or UPDATED]
```

## Common Mistakes to Avoid

- Do not add `edge_cases:` to ruleset guidance template files in `../../core/guidance-templates/` — they are domain-agnostic; `edge_cases:` belongs only in per-domain `guidance.yaml`
- `source_template` is never updated after initial creation — it records which guidance template the file was originally created from
- Do not create or scaffold a domain folder here — if the domain doesn't exist, stop and refer to `/new-domain`
- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-index.yaml` is the sole source of doc signals
- `guidance.yaml` is created in Step 1 [CREATE], not deferred to later steps — it always exists before Step 2 begins
