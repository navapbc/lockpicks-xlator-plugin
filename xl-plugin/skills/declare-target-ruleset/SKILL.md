---
name: declare-target-ruleset
description: Declare Ruleset Input-Output from a Suggestion File
---

# Declare Ruleset Input-Output from a Suggestion File

Bootstrap the `guidance/` folder for a domain from a ruleset file produced by `/suggest-target-ruleset`. No template selection is required — the ruleset file already encodes the ruleset's name, input-output shape, role, and scope. Writes `source_template: suggestion--<ruleset_name>` as a sentinel recording which ruleset the file was created from.

## Input

```
/declare-target-ruleset <domain> [<ruleset_name>]
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
     :::
     Then stop.

3. **`suggested_targets/` directory exists and has at least one `.yaml` file?**
   - Directory absent or empty → Print:
     :::error
     No ruleset files found. Run /suggest-target-ruleset <domain> first.
     :::
     Then stop.

4. **Ruleset file resolved:**
   - If `<ruleset_name>` was provided: check `$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml` exists.
     - NOT FOUND → Print:
       :::error
       Ruleset file not found: $DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml
       Available ruleset files:
         - <file1>.yaml
         - <file2>.yaml
       :::
       Then stop.
   - If `<ruleset_name>` was NOT provided: list all `.yaml` files in `$DOMAINS_DIR/<domain>/specs/suggested_targets/` as a numbered menu and prompt:
     :::user_input
     Available ruleset files:
       1. <file1>
       2. <file2>
     Which ruleset file? Enter a number or file name:
     :::
     Await the user's response and use the resolved file as the ruleset file. Then continue.

5. **`guidance/metadata.yaml` already exists?**
   - `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` present → Prompt:
     :::user_input
     guidance/metadata.yaml already exists at $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml. Overwrite? [y/n]
     :::
     - `n` → Stop without writing.
     - `y` → Continue (UPDATE mode).

## Process

### Step 1: Display ruleset

Read the resolved ruleset file and display a summary:

:::detail
Ruleset: <display_name>
Description: <description>
Inputs: <comma-separated entity names from inputs.<Entity>>
Computed: <comma-separated computed.<field> names, or "none">
Output: <primary output name> (<type>)
Secondary outputs: <comma-separated names of outputs with primary: false, or "none">
:::

### Step 2: Ensure guidance folder

Run `xlator ensure-guidance <domain>` to create `$DOMAINS_DIR/<domain>/specs/guidance/` (if absent) and seed `CLAUDE.md` from `core/guidance_claude.md`. This is idempotent — safe to run when the folder already exists.

### Step 3: Seed `specs/naming-manifest.yaml` and create guidance files

Write three files: the seeded manifest at `specs/naming-manifest.yaml` and two guidance files at `specs/guidance/`. **Do NOT write `guidance/variables.yaml`** — that file no longer exists; structural variable data lives in `specs/naming-manifest.yaml`, descriptive guidance lives in dedicated `guidance/<concern>.yaml` files written by `/create-skeleton` and `/tag-vars-to-include-with-output`.

**`specs/naming-manifest.yaml`** (seeded entries — provenance fields are nullable per the v7.0.0 schema; populate them later via `/extract-ruleset` Step 7):

```yaml
version: "1.0"
inputs:
  <EntityName>:
    <field_name>:
      type: <type>           # optional; copy from suggested_targets when present
      description: "<...>"   # optional; copy from suggested_targets when present
    # repeat per field
  # repeat per entity
computed:
  <field_name>:
    type: <type>
    description: "<...>"
outputs:
  <field_name>:
    type: <type>
    description: "<...>"
    # The `primary: true|false` flag from suggested_targets does NOT propagate
    # to naming-manifest.yaml. The primary distinction will be re-encoded in
    # guidance/output-variables.yaml when /create-skeleton runs.
```

Provenance fields (`policy_phrase:`, `source_doc:`, `section:`) and propagated metadata (`synonyms:`) are omitted at seed time (R5 nullable). They fill in via the merge tool's two-pass logic and `/extract-ruleset` Step 7.

**`guidance/metadata.yaml`:**

```yaml
template_id: <ruleset_name from ruleset>
source_template: suggestion--<ruleset_name>
display_name: "<display_name from ruleset>"
description: "<description from ruleset>"
```

**`guidance/prompt-context.yaml`:**

```yaml
role: "<role from ruleset>"
scope: "<scope from ruleset>"

constraints:
  - "Do not interpret beyond the text; do not add requirements that aren't stated."
  - "Ensure every rule has citations."
  - "Create a list of unknowns/gaps (things needed but not defined in the text)."
  - "List any assumptions made."
  - "Do not invent verification requirements."
  - "Ensure no rule introduces concepts not present in the policy."

standards:
  - "<standard from ruleset>"
  # (repeat for each standard in the ruleset file's standards list)

guidance:
  - "<guidance item from ruleset>"
  # (repeat for each guidance item in the ruleset file's guidance list)

edge_cases: []
```

**Field population rules:**

- `template_id`: the `ruleset_name` field from the ruleset file (snake_case, e.g., `eligibility_check`)
- `source_template`: `suggestion--<ruleset_name>` (e.g., `suggestion--eligibility_check`) — never a template filename, never just `suggestion-`
- `display_name`, `description`, `role`, `scope`: copied verbatim from the ruleset file as quoted strings
- `constraints`, `standards`, `guidance`: copy all entries from `xl-plugin/core/guidance-templates/assess-eligibility/prompt-context.yaml` exactly as listed — do not summarize or reword
- `edge_cases: []`: always empty at creation; `/create-skeleton`'s Step 2 pass will populate
- **`naming-manifest.yaml` `inputs:` block:** copy `inputs.<EntityName>.<field>` from the ruleset file directly; carry `type:` and `description:` when the ruleset supplied them; omit them otherwise (R5 nullable)
- **`naming-manifest.yaml` `computed:` block:** copy `computed.<field>` from the ruleset file; carry `type:` and `description:` when present
- **`naming-manifest.yaml` `outputs:` block:** copy `outputs.<field>` from the ruleset file; carry `type:` and `description:`. **Do NOT copy the `primary: true|false` flag** — primary distinction lives in `guidance/output-variables.yaml` (written by `/create-skeleton`)
- **Provenance fields** (`policy_phrase:`, `source_doc:`, `section:`): always omitted at seed time. Filled in via `/extract-ruleset` Step 7 when the analyst confirms a seeded name against an observed phrase

**Fields to omit entirely from all three files:** `ruleset_groups:`, `ruleset_modules:`, `skeleton:`, `constants_and_tables:`, `sample_rules:`, `synonyms:` — those are written by later AI skills or filled in by the merge tool.
**Never write `generated_at`** — git version history tracks file history.

After writing, print:

:::important
Created $DOMAINS_DIR/<domain>/specs/naming-manifest.yaml
Created $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml
Created $DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml
:::

Then suggest the next step:

:::next_step
Next: Run /index-inputs <domain> to extract per-file section data from policy docs. Then /create-skeleton to populate descriptive guidance files.
:::

## Output

:::important
$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml         [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml       [CREATED]
$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml [CREATED]
:::

## Common Mistakes to Avoid

- `source_template` must be `suggestion--<ruleset_name>` — never a template filename (e.g., not `assess-eligibility`), never just `suggestion-`
- `source_template` is never updated after creation — do not modify it on re-runs or updates
- Do not write `generated_at` — git tracks version history; this field is dropped
- **Do not write `guidance/variables.yaml`** — the file is gone in v7.0.0. Structural data lives in `specs/naming-manifest.yaml`; descriptive guidance is split across `output-variables.yaml`, `input-variables.yaml`, `include-with-output.yaml`, `constants-and-tables.yaml`.
- **Do not write `policy_phrase:`, `source_doc:`, or `section:` on seeded `naming-manifest.yaml` entries** — these provenance fields are nullable at seed time. Populating them now would falsify pre-extraction provenance; `/extract-ruleset` Step 7 fills them in once the analyst confirms a seeded name against an observed phrase.
- **Do not propagate the `primary: true|false` flag** from `suggested_targets/*.yaml` into `naming-manifest.yaml` — primary distinction lives in `guidance/output-variables.yaml` (written by `/create-skeleton`).
- Do not include `ruleset_groups:`, `ruleset_modules:`, `skeleton:`, `constants_and_tables:`, or `sample_rules:` in any guidance file — those are written by later AI skills
- Do not add `edge_cases:` content here — `edge_cases: []` is always empty at creation; `/create-skeleton` populates it
- `template_id` is the `ruleset_name` from the ruleset file (snake_case) — not the `display_name`, not a path, not a template id from `guidance-templates/`
