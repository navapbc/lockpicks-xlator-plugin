# Declare Ruleset Input-Output from a Suggestion File

Bootstrap `guidance.yaml` for a domain from a ruleset file produced by `/xl:suggest-target-ruleset`. No template selection is required — the ruleset file already encodes the ruleset's name, input-output shape, role, and scope. Writes `source_template: suggestion--<ruleset_name>` as a sentinel recording which ruleset the file was created from.

## Input

```
/declare-target-ruleset <domain> [<ruleset_name>]
```

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
     No ruleset files found. Run /xl:suggest-target-ruleset <domain> first.
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

5. **`guidance.yaml` already exists?**
   - `$DOMAINS_DIR/<domain>/specs/guidance.yaml` present → Prompt:
     :::user_input
     guidance.yaml already exists at $DOMAINS_DIR/<domain>/specs/guidance.yaml. Overwrite? [y]es / [n]o
     :::
     - `n` → Stop without writing.
     - `y` → Continue.

## Process

### Step 1: Display ruleset

Read the resolved ruleset file and display a summary:

:::detail
Ruleset: <display_name>
Description: <description>
Inputs: <comma-separated category names>
Output: <primary.name> (<primary.type>)
Secondary: <secondary_decisions names, or "none">
:::

### Step 2: Create `guidance.yaml`

Use the following as the baseline constraint, standard, and guidance entries.
Write `$DOMAINS_DIR/<domain>/specs/guidance.yaml` with this exact field ordering:

```yaml
template_id: <ruleset_name from ruleset>
source_template: suggestion--<ruleset_name>
generated_at: <today YYYY-MM-DD>
display_name: "<display_name from ruleset>"
description: "<description from ruleset>"

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

input_variables:
  categories:
    - category: <category from ruleset>
      description: "<description from ruleset>"
      examples: []
    # (repeat for each category in the ruleset file's input_variables.categories)

output_variables:
  primary:
    name: <name from ruleset>
    type: "<type from ruleset>"
    description: "<description from ruleset>"
  secondary_decisions:
    - name: <name from ruleset>
      type: "<type from ruleset>"
      description: "<description from ruleset>"
    # (secondary_decisions: [] when the ruleset has no secondary decisions)

intermediate_variables:
  include_with_output: []
  categories: []
```

**Field population rules:**

- `template_id`: the `ruleset_name` field from the ruleset file (snake_case, e.g., `eligibility_check`)
- `source_template`: `suggestion--<ruleset_name>` (e.g., `suggestion--eligibility_check`) — never a template filename, never just `suggestion-`
- `generated_at`: today's date in `YYYY-MM-DD` format
- `display_name`, `description`, `role`, `scope`: copied verbatim from the ruleset file as quoted strings
- `constraints`, `standards`, `guidance`: copy all entries from `xl-plugin/core/guidance-templates/assess-eligibility.yaml` exactly as listed in that file — do not summarize or reword
- `edge_cases: []`: always empty at creation; `/xl:create-skeleton`'s Step 2 pass will populate
- `input_variables.categories`: copy each `category` and `description` from the ruleset file; add `examples: []` to every category (template placeholder — skeleton building fills these in during `/xl:create-skeleton`)
- `output_variables.primary`: copy `name`, `type`, and `description` from the ruleset file
- `output_variables.secondary_decisions`: copy from the ruleset file; write `secondary_decisions: []` when the ruleset had none — never omit the key
- `intermediate_variables.include_with_output: []`: always initialized empty — never omit this key
- `intermediate_variables.categories: []`: always initialized empty

**Fields to omit entirely:** `ruleset_groups:`, `ruleset_modules:`, `skeleton:`, `constants_and_tables:`, `sample_rules:` — those are written by later commands (`/xl:create-skeleton`, `/xl:create-ruleset-groups`, `/xl:create-ruleset-modules`).

After writing, print:

:::important
Created $DOMAINS_DIR/<domain>/specs/guidance.yaml
:::

Then suggest the next step:

:::next_step
Next: Run /xl:create-skeleton <domain> to extract document signals and build the computation skeleton.
:::

## Output

:::important
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [CREATED]
:::

## Common Mistakes to Avoid

- `source_template` must be `suggestion--<ruleset_name>` — never a template filename (e.g., not `assess-eligibility`), never just `suggestion-`
- `source_template` is never updated after creation — do not modify it on re-runs or updates
- Do not omit `intermediate_variables.include_with_output: []` — downstream commands (`/xl:create-skeleton`, `/xl:extract-ruleset`) expect this key to exist
- Do not include `ruleset_groups:`, `ruleset_modules:`, `skeleton:`, `constants_and_tables:`, or `sample_rules:` — those are written by later commands
- Do not add `edge_cases:` content here — `edge_cases: []` is always empty at creation; `/xl:create-skeleton` populates it
- `secondary_decisions: []` must be present even when the ruleset had no secondary decisions — never omit the key
- `examples: []` in each `input_variables` category is intentional — it is a placeholder that `/xl:create-skeleton` will fill in with domain-specific variable names
- Do not add `edge_cases:` to the guidance template files in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` — `edge_cases:` belongs only in per-domain `guidance.yaml`
- `template_id` is the `ruleset_name` from the ruleset file (snake_case) — not the `display_name`, not a path, not a template id from `guidance-templates/`
