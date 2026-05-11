---
name: refine-guidance
description: Refine Ruleset Guidance for a Domain
---

# Refine Ruleset Guidance for a Domain

Create or update the `guidance/` folder for a domain by orchestrating the granular guidance-authoring skills in sequence. On first run (CREATE), bootstraps via AI-suggested target rulesets (`/suggest-target-ruleset` → `/declare-target-ruleset`), then runs each authoring step in order. On subsequent runs (UPDATE), loads the existing guidance files and runs each step to refine them.

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

3. **Per-file computations required?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - **ABSENT or empty** → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first, then re-run /refine-guidance <domain>.
     :::
     Stop.

4. **Detect mode** — check for `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`:
   - **Present** → **UPDATE mode**
   - **Absent** → **CREATE mode**

---

## Process

Steps in this command:
1. Bootstrap / Load
2. Computation skeleton
3. Ruleset groups
4. Ruleset modules
5. Sample rules
6. Tag output variables
7. Sample tests

### Step 1 [CREATE]: Bootstrap guidance files

Run `xlator ensure-guidance <domain>` first to create `$DOMAINS_DIR/<domain>/specs/guidance/` (if absent) and seed `CLAUDE.md` from `core/guidance_claude.md`. This is idempotent.

Then bootstrap the target ruleset via AI suggestion:

1. Prompt: "Enter a hint to narrow candidate rulesets (e.g. 'eligibility', 'benefit calculation'), or 'all' to suggest all:"
2. Run `/suggest-target-ruleset <domain> [<hint>]` (omit `<hint>` if the user responded with 'all'). Skip pre-flight — domain and index already verified above.
3. Present the list of generated candidate files from `specs/suggested_targets/`.
4. Ask the user which candidate to use.
5. Run `/declare-target-ruleset <domain> <chosen_ruleset>`. Skip pre-flight — domain already verified above.

After this step, `specs/naming-manifest.yaml`, `guidance/metadata.yaml`, and `guidance/prompt-context.yaml` exist and Step 2 may proceed. The remaining descriptive guidance files (`output-variables.yaml`, `input-variables.yaml`, `constants-and-tables.yaml`) are written by `/create-skeleton` in Step 2; `include-with-output.yaml` is written by `/tag-vars-to-include-with-output` in Step 6.

### Step 1 [UPDATE]: Load existing files

Read `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` and `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml`. Print a summary:
:::detail
Folder: $DOMAINS_DIR/<domain>/specs/guidance/
Current guidance: <display_name>
Sections: constraints (<N> items), standards (<N> items), guidance (<N> items), edge_cases (<N> items)
Skeleton: <N> computations across <N> intermediate categories, <N> example rules
:::
(Show `Skeleton: none` if `skeleton.yaml` does not exist and no `sample_rules:` section exists in `sample-artifacts.yaml`.)

---

### Step 2: Computation skeleton

Run `/create-skeleton <domain>`. Skip pre-flight — domain, guidance files, and `policy_facets/computations/` already verified above.

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
$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml               [CREATED in Step 1 (CREATE only) via /declare-target-ruleset]
$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml             [CREATED or UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml       [CREATED or UPDATED]
$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml     [CREATED or UPDATED in Step 2]
$DOMAINS_DIR/<domain>/specs/guidance/input-variables.yaml      [CREATED or UPDATED in Step 2]
$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml [CREATED or UPDATED in Step 2]
$DOMAINS_DIR/<domain>/specs/guidance/include-with-output.yaml  [CREATED or UPDATED in Step 6]
```

## Common Mistakes to Avoid

- Do not write `generated_at` in any guidance file — git tracks version history; this field is dropped
- Do not create or scaffold a domain folder here — if the domain doesn't exist, stop and refer to `/new-domain`
- Do not read files under `$DOMAINS_DIR/<domain>/input/` at any step — `policy_facets/computations/` is the sole source of doc signals
- `guidance/metadata.yaml` is created in Step 1 [CREATE] (via `/declare-target-ruleset`), not deferred to later steps — it always exists before Step 2 begins
