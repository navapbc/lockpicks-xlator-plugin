---
name: suggest-target-ruleset
description: Suggest Target Rulesets given Policy Documents
---

# Suggest Target Rulesets given Policy Documents

Analyze a domain's `specs/input-index.yaml` and suggest 1–3 candidate target rulesets based on all its information, including section headings, summary, topic tags, and computation hints. Saves suggestion files to `specs/suggested_targets/`. The user will select one of the suggestion files as the input to `/declare-target-ruleset`.

The optional `<hint>` argument (e.g., "eligibility" or "benefit calculation") narrows what kinds of rulesets to suggest — it prioritizes candidates that match the hint phrase but still reads the whole index. When no hint is provided, all distinct policy scopes found in the index are candidates with a preference for rulesets that cover more topics.

## Input

```
/suggest-target-ruleset <domain> [<hint>]
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
     Then stop.

3. **Input index present?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     :::error
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /index-inputs <domain> first, then re-run /suggest-target-ruleset <domain>.
     :::
     Then stop.

---

## Process

### Step 1: Analyze index

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`.

Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of doc signals.

Cluster the index signals to identify 1–5 distinct policy scopes. For each scope, derive a candidate target ruleset:

**Signals to extract:**
- **Topic tags** across all sections → cluster to find prominent domain areas (e.g., "income", "eligibility", "household")
- **Section headings** → reveals statutory structure and sub-program scope
- **File summaries** → reveals program scope and terminology
- **Computation hints** → collect all `computations:` entries from sections that have the field; trace variable chains (a variable that is the last item in one entry's `variables` list and appears earlier in another entry's `variables` list is an intermediate computed variable); collect `expr_hint` values keyed by their output variable. If the index has no `computations:` entries, skip this signal.

**For each candidate, derive:**
- `ruleset_name` — snake_case base filename (e.g., `eligibility_check`, `income_calculation`)
- `display_name` — human-readable title (e.g., "Determine Program Eligibility")
- `description` — one sentence describing what the ruleset computes
- `role` — AI persona for extraction (e.g., "You are a policy-to-rules analyst for eligibility determination.")
- `scope` — extraction goal sentence (e.g., "Convert the provided policy text into explicit, testable eligibility rules that produce an eligibility decision.")
- `input_variables.categories` — inferred from the index's section groups and computation hint variable names; each category has a `category` (snake_case) and `description`
- `output_variables.primary` — `name` (snake_case), `type` (bool | money | enum | str | int | ...), `description`
- `output_variables.secondary_decisions` — list of `{name, type, description}` for secondary outputs (e.g., denial reasons, verification flags); may be empty

Refer to `$DOMAINS_DIR/ak_doh/specs/guidance.yaml` as an exemplar of the expected ruleset shape when inferring variable names, types, and I/O structure.

**When `<hint>` is provided:**
- Rank candidates by relevance to the hint phrase — prefer section headings, topic tags, and computation hints that match the hint
- Show a display header: `Suggestions focused on: <hint>`
- Still read all of `input-index.yaml` — the hint is a prioritization signal, not a filter that discards unrelated sections entirely
- If hint matches nothing strongly, show all candidates and note: `(No strong match found for "<hint>" — showing all candidates)`

**Display all candidates** in a numbered list:

:::detail
Suggestions focused on: <hint>            ← omit this line if no hint was provided

  1. <ruleset_name>
     <description>
     Inputs: <comma-separated category names>
     Output: <primary.name> (<primary.type>)

  2. <ruleset_name>
     <description>
     Inputs: <comma-separated category names>
     Output: <primary.name> (<primary.type>)

  3. <ruleset_name>   ← include only if a third distinct scope is identifiable
     ...
:::

### Step 2: Save

Save each candidate:

1. Ensure `$DOMAINS_DIR/<domain>/specs/suggested_targets/` directory exists. Create it if absent.

2. Write `$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml` using exactly this schema:

```yaml
# Auto-generated by /suggest-target-ruleset — do not edit manually
# Domain: <domain>
# Generated: YYYY-MM-DD
ruleset_name: <snake_case>
display_name: <string>
description: <string>
role: <string>
scope: <string>
input_variables:
  categories:
    - category: <snake_case>
      description: <string>
output_variables:
  primary:
    name: <snake_case>
    type: bool | money | enum | ...
    description: <string>
  secondary_decisions:
    - name: <snake_case>
      type: <string>
      description: <string>
```

   YAML conventions:
   - Two-space indentation throughout
   - All `description:` and `display_name:` values as quoted strings
   - `secondary_decisions: []` when no secondary outputs are identified — never omit the field
   - `# Generated:` date as YYYY-MM-DD (today's date)

3. Confirm each file written:
   :::important
   Saved: $DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml
   :::

After all saves, suggest the next step:

:::next_step
Next: Run /declare-target-ruleset <domain> <ruleset_name> to create guidance.yaml from a suggestion file.
:::

---

## Output

:::important
$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml    [CREATED]
:::

(one line per saved file)

---

## Common Mistakes to Avoid

- **Do not include `intermediate_variables`, `constraints`, `standards`, `guidance`, `edge_cases`, `skeleton:`, `ruleset_groups:`, or `ruleset_modules:` in suggestion files** — those are written by later skills (`/create-skeleton`, `/create-ruleset-groups`, `/create-ruleset-modules`)
- **Do not read files under `$DOMAINS_DIR/<domain>/input/`** — `input-index.yaml` is the sole source of doc signals
- **Do not suggest a single monolithic ruleset when the index shows multiple distinct policy scopes** — identify separate scopes as separate candidates (e.g., an income exclusion chain and an eligibility determination are two distinct scopes)
- **`secondary_decisions: []` is valid when no secondary outputs are identified** — do not omit the field; an absent `secondary_decisions` key is not the same as an empty list
- **Do not use block-style lists for `type:` values** — `type: enum` not `type:\n  - approve\n  - deny`
- **Do not guess domain names or paths** — always expand `$DOMAINS_DIR` from `.xlator.local.env` if the variable is unknown
- **When hint matches nothing strongly, show all candidates** — do not suppress candidates because they don't match the hint; the hint is a ranking signal only
