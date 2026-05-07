---
name: refine-guidance
description: Refine Ruleset Guidance for a Domain
---

# Refine Ruleset Guidance for a Domain

Create or update the `guidance/` folder for a domain by orchestrating the granular guidance-authoring skills in sequence. On first run (CREATE), guides the user through guidance template selection (or AI-suggested rulesets) to bootstrap the split guidance files, then runs each authoring step in sequence. On subsequent runs (UPDATE), loads the existing guidance files and runs each step to refine them.

The **guidance template** (in `../../core/guidance-templates/` and `$DOMAINS_DIR/guidance-templates/`) provides an initial ruleset guidance that is then customized per domain in `$DOMAINS_DIR/<domain>/specs/guidance/`.

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
   - Check for `$DOMAINS_DIR/<domain>/specs/input-sections.yaml`
   - **ABSENT** → Print:
     :::error
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-sections.yaml
     Run /index-inputs <domain> first, then re-run /refine-guidance <domain>.
     :::
     Stop.

4. **Detect mode** — check for `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`:
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

### Step 1 [CREATE]: Bootstrap guidance files

Two paths are available. Present as options:

**a. Template selection** — Choose a guidance template:

Scan `../../core/guidance-templates/*/metadata.yaml` and `$DOMAINS_DIR/guidance-templates/*/metadata.yaml` for all available guidance template folders, reading `metadata.yaml` in each to get the `template_id`, `display_name`, and `description`.

- Present a list for the user to choose one where each option shows: "`<template_id>`: <display_name> (<full_folder_path>)".
- Instead of "(Type in another answer)", present "(or paste path of folder to use as the ruleset guidance template)".

Print a summary of the selected template's `metadata.yaml` content for the user to review.

After the user confirms the selected guidance template, write three files into `$DOMAINS_DIR/<domain>/specs/guidance/`:

1. Copy `<template_folder>/metadata.yaml` to `guidance/metadata.yaml` and insert `source_template: <template_folder_name>` immediately after `template_id:`.
2. Copy `<template_folder>/prompt-context.yaml` to `guidance/prompt-context.yaml` verbatim.
3. Copy `<template_folder>/variables.yaml` to `guidance/variables.yaml` verbatim.

**Never write `generated_at`** in any of these files.

:::important
Created $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml
Created $DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml
Created $DOMAINS_DIR/<domain>/specs/guidance/variables.yaml
:::

**b. AI-suggest** — Let the AI propose candidate rulesets based on the index:

1. Prompt: "Enter a hint to narrow candidate rulesets (e.g. 'eligibility', 'benefit calculation'), or 'all' to suggest all:"
2. Run `/suggest-target-ruleset <domain> [<hint>]` (omit `<hint>` if the user responded with 'all'). Skip pre-flight — domain and index already verified above.
3. Present the list of generated candidate files from `specs/suggested_rulesets/`.
4. Ask the user which candidate to use.
5. Run `/declare-target-ruleset <domain> <chosen_ruleset>`. Skip pre-flight — domain already verified above.

After either path, `guidance/metadata.yaml` exists and Step 2 may proceed.

### Step 1 [UPDATE]: Load existing files

Read `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` and `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml`. Print a summary:
:::detail
Folder: $DOMAINS_DIR/<domain>/specs/guidance/
Current guidance: <display_name> (source: <source_template>)
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: <N> computations across <N> intermediate categories, <N> example rules
:::
(Show `Skeleton: none` if `skeleton.yaml` does not exist and no `sample_rules:` section exists in `sample-artifacts.yaml`.)

---

### Step 2: Computation skeleton

Run `/create-skeleton <domain>`. Skip pre-flight — domain, guidance files, and `input-sections.yaml` already verified above.

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
$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml      [CREATED or UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml [CREATED or UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/variables.yaml      [CREATED or UPDATED]
```

## Common Mistakes to Avoid

- Do not add `edge_cases:` content to guidance template files in `../../core/guidance-templates/` — they are domain-agnostic; `edge_cases:` is initialized as `[]` in the per-domain `prompt-context.yaml`
- `source_template` is never updated after initial creation — it records which guidance template the folder was originally created from
- Do not write `generated_at` in any guidance file — git tracks version history; this field is dropped
- Do not create or scaffold a domain folder here — if the domain doesn't exist, stop and refer to `/new-domain`
- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `input-sections.yaml` is the sole source of doc signals
- `guidance/metadata.yaml` is created in Step 1 [CREATE], not deferred to later steps — it always exists before Step 2 begins
