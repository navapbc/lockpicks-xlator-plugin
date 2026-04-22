# Declare Ruleset I/O from a Suggestion File

Bootstrap `guidance.yaml` for a domain from a suggestion file produced by `/suggest-ruleset-io`. No template selection is required — the suggestion file already encodes the ruleset's name, I/O shape, role, and scope. Writes `source_template: suggestion-derived-<ruleset_name>` as a sentinel recording which suggestion the file was created from.

## Input

```
/declare-ruleset-io <domain> [<suggestion_name>]
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

3. **`suggested_rulesets/` directory exists and has at least one `.yaml` file?**
   - Directory absent or empty → Print:
     ```
     No suggestion files found. Run /suggest-ruleset-io <domain> first.
     ```
     Then stop.

4. **Suggestion file resolved:**
   - If `<suggestion_name>` was provided: check `$DOMAINS_DIR/<domain>/specs/suggested_rulesets/<suggestion_name>.yaml` exists.
     - NOT FOUND → Print:
       ```
       Suggestion file not found: $DOMAINS_DIR/<domain>/specs/suggested_rulesets/<suggestion_name>.yaml
       Available suggestion files:
         - <file1>.yaml
         - <file2>.yaml
       ```
       Then stop.
   - If `<suggestion_name>` was NOT provided: list all `.yaml` files in `$DOMAINS_DIR/<domain>/specs/suggested_rulesets/` as a numbered menu and prompt:
     ```
     Available suggestion files:
       1. <file1>
       2. <file2>
     Which suggestion file? Enter a number or file name:
     ```
     Await the user's response and use the resolved file as the suggestion file. Then continue.

5. **`guidance.yaml` already exists?**
   - `$DOMAINS_DIR/<domain>/specs/guidance.yaml` present → Prompt:
     ```
     guidance.yaml already exists at $DOMAINS_DIR/<domain>/specs/guidance.yaml. Overwrite? (y/n)
     ```
     - `n` → Stop without writing.
     - `y` → Continue.

## Process

### Step 1: Confirm suggestion

Read the resolved suggestion file. Display a summary for user confirmation:

```
Ruleset: <display_name>
Description: <description>
Inputs: <comma-separated category names>
Output: <primary.name> (<primary.type>)
Secondary: <secondary_decisions names, or "none">

Create guidance.yaml from this suggestion? (y/n)
```

- `n` → Stop.
- `y` → Proceed to Step 2.

### Step 2: Create `guidance.yaml`

Read `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/assess-eligibility.yaml` to obtain the baseline constraint, standard, and guidance entries.

Write `$DOMAINS_DIR/<domain>/specs/guidance.yaml` with this exact field ordering:

```yaml
template_id: <ruleset_name from suggestion>
source_template: suggestion-derived-<ruleset_name>
generated_at: <today YYYY-MM-DD>
display_name: "<display_name from suggestion>"
description: "<description from suggestion>"

role: "<role from suggestion>"
scope: "<scope from suggestion>"

constraints:
  - "Do not interpret beyond the text; do not add requirements that aren't stated."
  - "Ensure every rule has citations."
  - "Create a list of unknowns/gaps (things needed but not defined in the text)."
  - "List any assumptions made."
  - "Do not invent verification requirements."
  - "Ensure no rule introduces concepts not present in the policy."

standards:
  - "Use monthly income amounts unless policy specifies annual."
  - "Express monetary values in dollars."
  - "Normalize citizenship status as boolean or enum (not free text)."

guidance:
  - "Look for chained deductions: gross → earned deduction → net → shelter deduction → adjusted net income."
  - "Check for both gross and net income tests as separate computed fields."
  - "When federal and state policies conflict, prefer the more restrictive rule."
  - "Deduction amounts typically appear in constants or household-size tables."

edge_cases: []

input_variables:
  categories:
    - category: <category from suggestion>
      description: "<description from suggestion>"
      examples: []
    # (repeat for each category in the suggestion file's input_variables.categories)

output_variables:
  primary:
    name: <name from suggestion>
    type: "<type from suggestion>"
    description: "<description from suggestion>"
  secondary_decisions:
    - name: <name from suggestion>
      type: "<type from suggestion>"
      description: "<description from suggestion>"
    # (secondary_decisions: [] when the suggestion has no secondary decisions)

intermediate_variables:
  include_with_output: []
  categories: []
```

**Field population rules:**

- `template_id`: the `ruleset_name` field from the suggestion file (snake_case, e.g., `eligibility_check`)
- `source_template`: `suggestion-derived-<ruleset_name>` (e.g., `suggestion-derived-eligibility_check`) — never a template filename, never just `suggestion-derived`
- `generated_at`: today's date in `YYYY-MM-DD` format
- `display_name`, `description`, `role`, `scope`: copied verbatim from the suggestion file as quoted strings
- `constraints`, `standards`, `guidance`: copy all entries from `xl-plugin/core/guidance-templates/assess-eligibility.yaml` exactly as listed in that file — do not summarize or reword
- `edge_cases: []`: always empty at creation; `/create-skeleton`'s Step 2 pass will populate
- `input_variables.categories`: copy each `category` and `description` from the suggestion file; add `examples: []` to every category (template placeholder — skeleton building fills these in during `/create-skeleton`)
- `output_variables.primary`: copy `name`, `type`, and `description` from the suggestion file
- `output_variables.secondary_decisions`: copy from the suggestion file; write `secondary_decisions: []` when the suggestion had none — never omit the key
- `intermediate_variables.include_with_output: []`: always initialized empty — never omit this key
- `intermediate_variables.categories: []`: always initialized empty

**Fields to omit entirely:** `workflow_stages:`, `sub_rulesets:`, `skeleton:`, `constants_and_tables:`, `examples:`, `example_rules:` — those are written by later commands (`/create-skeleton`, `/create-ruleset-groups`, `/create-ruleset-modules`).

After writing, print:

```
Created $DOMAINS_DIR/<domain>/specs/guidance.yaml
```

Then suggest the next step:

```
Next: Run /create-skeleton <domain> to extract doc signals and build the computation skeleton.
```

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [CREATED]
```

## Common Mistakes to Avoid

- `source_template` must be `suggestion-derived-<ruleset_name>` — never a template filename (e.g., not `assess-eligibility`), never just `suggestion-derived`
- `source_template` is never updated after creation — do not modify it on re-runs or updates
- Do not omit `intermediate_variables.include_with_output: []` — downstream commands (`/create-skeleton`, `/extract-ruleset`) expect this key to exist
- Do not include `workflow_stages:`, `sub_rulesets:`, `skeleton:`, `constants_and_tables:`, or `example_rules:` — those are written by later commands
- Do not add `edge_cases:` content here — `edge_cases: []` is always empty at creation; only `/create-skeleton` populates it
- `secondary_decisions: []` must be present even when the suggestion had no secondary decisions — never omit the key
- `examples: []` in each `input_variables` category is intentional — it is a placeholder that `/create-skeleton` will fill in with domain-specific variable names
- Do not add `edge_cases:` to the guidance template files in `$CLAUDE_PLUGIN_ROOT/core/guidance-templates/` — `edge_cases:` belongs only in per-domain `guidance.yaml`
- `template_id` is the `ruleset_name` from the suggestion file (snake_case) — not the `display_name`, not a path, not a template id from `guidance-templates/`
