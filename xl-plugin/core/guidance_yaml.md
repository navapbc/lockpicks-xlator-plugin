# `guidance.yaml` Structure Reference

`guidance.yaml` is the per-domain ruleset guidance file. It lives at `$DOMAINS_DIR/<domain>/specs/guidance.yaml` and controls how `/extract-ruleset` reads policy documents and generates a CIVIL ruleset.

The file is built incrementally by a sequence of slash commands.

---

## Top-level key ordering

Slash commands insert keys in this order:

```
template_id
source_template
generated_at
display_name
description
role
scope
skeleton          ← /create-skeleton
ruleset_groups    ← /create-ruleset-groups
ruleset_modules   ← /create-ruleset-modules
constraints
standards
guidance
edge_cases
input_variables
output_variables
intermediate_variables
constants_and_tables
sample_rules      ← /extract-sample-rules
missing_info      ← /extract-sample-rules
assumptions       ← /extract-sample-rules
sample_tests      ← /create-sample-tests
```

---

## Field usage by command

Which commands read each field as input (beyond `/extract-ruleset` and the command that writes it):

| Field | Read by |
|-------|---------|
| `template_id` | `/create-ruleset-modules` |
| `display_name` | `/create-skeleton`, `/create-ruleset-groups`, `/extract-sample-rules` |
| `role` | `/extract-sample-rules` |
| `scope` | |
| `constraints` | |
| `standards` | |
| `guidance` | |
| `edge_cases` | `/create-sample-tests` |
| `input_variables` | `/create-skeleton` (update mode) |
| `output_variables` | `/create-skeleton` (update mode), `/create-ruleset-modules`, `/extract-sample-rules`, `/tag-vars-to-include-with-output`, `/create-sample-tests` |
| `intermediate_variables` | `/create-skeleton` (update mode), `/create-ruleset-modules`, `/extract-sample-rules`, `/tag-vars-to-include-with-output`, `/create-sample-tests` |
| `constants_and_tables` | `/create-sample-tests` |
| `skeleton` | `/create-ruleset-groups`, `/create-ruleset-modules`, `/extract-sample-rules` |
| `ruleset_groups` | `/create-ruleset-modules`, `/extract-sample-rules` |
| `ruleset_modules` | `/extract-sample-rules`, `/tag-vars-to-include-with-output`, `/create-sample-tests` |
| `ruleset_modules[].sample_rules` | `/tag-vars-to-include-with-output`, `/create-sample-tests` |
| `sample_rules` | `/tag-vars-to-include-with-output`, `/create-sample-tests` |

Fields not listed (`source_template`, `generated_at`, `description`, `missing_info`, `assumptions`, `sample_tests`) are not consumed as inputs by any downstream command.

---

## Created by `/declare-ruleset-io` or `/refine-guidance`

These fields form the initial file and are never changed after creation.

### `template_id`

Identifies the ruleset type in snake_case. Set to the `ruleset_name` from the suggestion file (when bootstrapped via `/declare-ruleset-io`) or the template's own `template_id` (when copied from a guidance template via `/refine-guidance`).

```yaml
template_id: calculate-earned-income-after-exclusions
```

### `source_template`

Records which source produced the file. For `/declare-ruleset-io`, uses the sentinel `suggestion--<ruleset_name>`. For `/refine-guidance`, uses the guidance template filename (without extension). Never updated after initial creation.

```yaml
source_template: suggestion--eligibility_check
# or, when created from a template:
source_template: assess-eligibility
```

### `generated_at`

Date the file was created or last structurally updated, in `YYYY-MM-DD` format. Updated by commands that write new top-level keys (e.g., `/create-ruleset-groups`).

```yaml
generated_at: 2026-03-27
```

### `display_name`

Human-readable name for the ruleset, shown in command output and UI labels.

```yaml
display_name: "Determine Medicaid Eligibility based on Reported Income after Exclusions (Alaska)"
```

### `description`

One-sentence description of what the ruleset computes.

```yaml
description: "Assess eligibility for Medicaid based on the adjusted monthly earned income amount for a household after applying all relevant exclusions."
```

### `role`

The AI persona used when extracting rules. Frames the LLM as a domain-specific analyst.

```yaml
role: "You are a policy-to-rules analyst for eligibility determination based on earned income."
```

### `scope`

The extraction task description: what the AI should do with the policy text.

```yaml
scope: "Convert the provided policy text into explicit, testable earned income calculation rules that produce a dollar amount per month."
```

### `constraints`

List of things the AI must not do during extraction — inferences to avoid, rounding rules, ordering constraints, etc.

```yaml
constraints:
  - "Do not interpret beyond the text; do not add requirements that aren't stated."
  - "Ensure every monetary formula traces directly to a policy citation."
  - "Apply earned income exclusions in the exact 10-step sequential order specified in 442-4; re-ordering is not permitted."
```

### `standards`

Normalization and formatting rules for values in the generated ruleset — units, categories, numeric representations.

```yaml
standards:
  - "Use monthly amounts unless policy specifies otherwise."
  - "Express monetary values in dollars."
  - "Express percentages as decimals (0.3, not 30%)."
```

### `guidance`

Miscellaneous guidance: non-obvious rule patterns, formula structures, and domain-specific heuristics for the AI to apply. Can include concrete variable names and expression patterns derived from the document index.

```yaml
guidance:
  - "The CIVIL ruleset should define dol_avg_monthly_income as a computed: field equal to dol_quarterly_earnings / 3."
  - "The income_limit is a table lookup: income_limit = table[household_type][benefit_year]."
```

### `edge_cases`

Special populations, exceptional conditions, or policy interactions that override the general rules. Initialized as `[]` at creation; populated by `/create-skeleton` or via `/refine-guidance`.

```yaml
edge_cases:
  - "Alaska Native and American Indian real property is always excluded as a resource, regardless of value or transfer (524L)."
  - "[module: exclusion_chain] The infrequent/irregular earned income exclusion cap is $10/month (not $30)."
```

Module-scoped edge cases use the prefix `[module: <ruleset_module_name>]` to indicate which ruleset module they apply to.

### `input_variables`

Describes the categories of inputs the ruleset accepts. Initialized with `examples: []`; populated with domain-specific variable names by `/create-skeleton`.

Each category has:
- `category` — snake_case name for the input group
- `description` — what this group of inputs represents
- `examples` — list of concrete variable names in this category (e.g., `[gross_income, earned_income]`)
- `source_file` _(optional)_ — path to the policy document defining this input
- `source_section` _(optional)_ — section heading within that document
- `exact_phrase` _(optional)_ — verbatim policy text the AI should treat as authoritative for this input

```yaml
input_variables:
  categories:
    - category: earned_income
      description: "Total earned income as reported by the client/applicant."
      examples: [client_gross_earned]
      source_file: "domains/ak_doh/input/policy_docs/apa_manual/441/441-1 EARNED INCOME.md"
      source_section: "441-1 EARNED INCOME"

    - category: blind_disabled_student_earnings
      description: "Earned income of a blind or disabled student subject to exclusion."
      examples: [student_earned_income]
      source_file: "domains/ak_doh/input/policy_docs/apa_manual/442/442-2 EARNED INCOME EXCLUSIONS.md"
      source_section: "442-2 D. STUDENT EARNED INCOME EXCLUSION"
      exact_phrase: "The earned income of a blind or disabled student is excluded subject to monthly and yearly maximums."
```

### `output_variables`

Declares the ruleset's outputs.

**`primary`** — the main output, with:
- `name` — variable name
- `type` — `bool`, `money`, `int`, `str`, or `enum`
- `values` _(enum only)_ — list of allowed values
- `description`

**`secondary_decisions`** — additional outputs returned alongside the primary, each with `name`, `type`, and `description`. Write `secondary_decisions: []` when there are none.

```yaml
output_variables:
  primary:
    name: eligible
    type: "enum"
    values: ["approve", "deny", "manual_verification"]
    description: "Eligibility result based on adjusted income and compatibility checks."
  secondary_decisions:
    - name: denial_reason
      type: "str"
      description: "Income-based reason for a 'deny' decision."
```

### `intermediate_variables`

Describes computed variables that sit between inputs and outputs.

**`include_with_output`** — list of intermediate variable names to expose in the API's `ComputedBreakdown` response alongside the final output. Initialized as `[]`; populated by `/tag-vars-to-include-with-output`. Good candidates are ruleset module result objects and pivotal values referenced in decision conditions.

**`categories`** — list of variable groups, each with:
- `category` — snake_case group name
- `description` — what this group computes
- `examples` — list of variable names in this group
- `computations` _(optional)_ — list of `{name, expr}` pairs for variables with known expression hints
- `source_file`, `source_section`, `exact_phrase` _(optional)_ — same as `input_variables` categories

```yaml
intermediate_variables:
  include_with_output: [client_result, dol_result, after_half, is_compatible, income_limit]
  categories:
    - category: exclusion_chain_steps
      description: "10-step sequential earned income exclusion chain (442-4)."
      examples: [after_federal, after_eitc, after_irregular, after_student, after_general_20,
                 after_65, after_irwe, after_half, after_blind, adjusted_earned_income]
      computations:
        - name: after_federal
          expr: "gross_earned_income - federal_exclusions"
        - name: after_half
          expr: "after_irwe * 0.5"

    - category: reasonable_compatibility
      description: "Whether client income is within 10% of DOL income (523B)."
      examples: [is_compatible]
      computations:
        - name: is_compatible
          expr: "abs(dol_result.adjusted_earned_income - client_result.adjusted_earned_income) <= client_result.adjusted_earned_income * 0.10"
      source_file: "domains/ak_doh/input/policy_docs/adltc_manual/523 MEDICAID EXCEPTIONS TO APA INCOME POLICY.md"
      source_section: "Reasonable Compatibility (523 B)"
```

### `constants_and_tables`

Named constants and lookup tables the ruleset references, with descriptions. Used by the AI to identify where to look for threshold values.

```yaml
constants_and_tables:
  - name: expanded_refused_cash_income_limits
    description: "Expanded Refused Cash Income Limits table keyed by household_type and benefit_year."
  - name: student_earned_income_exclusion
    description: "Monthly and annual maximum limits for the student earned income exclusion."
```

---

## Added by `/create-skeleton`

Inserted as a top-level key after `scope:` and before `constraints:`.

### `skeleton`

Records the confirmed computation structure — inputs, outputs, and intermediate computations with expression hints. Populated once and updated on re-runs.

Sub-fields:
- `confirmed_at` — date the skeleton was confirmed
- `inputs` — flat list of confirmed input variable names
- `outputs` — flat list of confirmed output variable names
- `computations` — list of `{category, variables[], exprs}` entries; `exprs` is a map of `variable → expr_hint` for variables with known expressions (variables shown as `= ?` are omitted)
- `flow_diagram` — ASCII diagram of the computation flow

```yaml
skeleton:
  inputs: [client_gross_earned, dol_quarterly_earnings, household_type, benefit_year]
  outputs: [eligible, denial_reason]
  computations:
    - category: dol_preprocessing
      variables: [dol_avg_monthly_income]
      exprs:
        dol_avg_monthly_income: "dol_quarterly_earnings / 3"
    - category: exclusion_chain_steps
      variables: [after_federal, after_eitc, after_half, adjusted_earned_income]
      exprs:
        after_half: "after_irwe * 0.5"
  flow_diagram: |
    client_gross_earned ──► exclusion_chain ──► client_result
    dol_quarterly_earnings ──► /3 ──► exclusion_chain ──► dol_result
    client_result, dol_result ──► is_compatible ──► eligible
```

---

## Added by `/create-ruleset-groups`

Inserted after `skeleton:`.

### `ruleset_groups`

Named evaluation phases that rule `group:` annotations reference. Each stage represents a logical policy section (e.g., income classification, deduction chain, eligibility determination).

Each entry has:
- `name` — snake_case stage identifier
- `description` — what this stage computes or decides

```yaml
ruleset_groups:
  - name: income_classification
    description: Classify income as earned or unearned (441)
  - name: earned_exclusion_chain
    description: Apply 10-step earned income exclusion sequence (442-2, 442-4)
  - name: eligibility_determination
    description: Compare adjusted income to income standard and set eligible
```

---

## Added by `/create-ruleset-modules`

Inserted after `ruleset_groups:`.

### `ruleset_modules`

All modules that will be generated as separate `.civil.yaml` files — both sub-modules and the main program file. Each entry is either a sub-module (invoked via `invoke:`) or the main module (the top-level program file).

Each entry has:
- `name` — snake_case ruleset module identifier (also the `.civil.yaml` file stem)
- `description` — what it computes
- `bound_entities` — CamelCase entity names it operates on; `[]` (empty) for the main module
- `rationale` — the heuristic that detected it: `reuse_across_entities`, `policy_structure`, `depth_threshold`, `variable_coupling`, `shared_gate`, `user_hint`, or `main_module` (for the main program entry)
- `role` _(optional)_ — `main` for the main program module; omit or set to `sub` for all sub-modules. Exactly one entry per ruleset may have `role: main`.
- `depends_on` _(optional)_ — list of module `name:` values that this module invokes. Defaults to `[]` when absent. Sub-modules with no inter-dependencies use `depends_on: []`. The main module's `depends_on:` lists all sub-modules it invokes.

**Main module entry conventions:** `bound_entities: []` (empty) and `rationale: main_module` are required. The `description:` is set to the `display_name` value from `guidance.yaml`.

Later populated with `sample_rules` by `/extract-sample-rules`.

```yaml
ruleset_modules:
  - name: exclusion_chain
    description: "10-step sequential earned income exclusion chain (442-4). Takes gross earned income and returns adjusted_earned_income."
    bound_entities: [ClientData, DOLRecord]
    rationale: reuse_across_entities
    depends_on: []
  - name: eligibility
    description: "AK DOH Earned Income Exclusions"
    bound_entities: []
    rationale: main_module
    role: main
    depends_on: [exclusion_chain]
```

---

## Added by `/extract-sample-rules`

### `ruleset_modules[].sample_rules`

CIVIL rule snippets generated for a specific ruleset module, keyed by `id`. Each entry has:
- `id` — snake_case rule identifier
- `rule_type` — `computed`, `categorical`, or `table-lookup`
- `source` — verbatim sentence from the input index that grounds the rule
- `civil` — full CIVIL YAML snippet as a literal block scalar

```yaml
ruleset_modules:
  - name: exclusion_chain
    sample_rules:
      - id: after_half_step
        rule_type: computed
        source: "One-half of remaining earned income after steps 1–7 is excluded."
        civil: |
          computed:
            - name: after_half
              expr: after_irwe * 0.5
              group: earned_exclusion_chain
```

### `sample_rules`

Top-level list of CIVIL rule snippets that did not match any ruleset module. Same schema as `ruleset_modules[].sample_rules`. Also used by `/refine-guidance` Step 8 for its rule preview.

```yaml
sample_rules:
  - id: eligibility_determination
    rule_type: categorical
    source: "Provides annual income limit thresholds by household type."
    civil: |
      rules:
        - id: approve_income_within_limit
          group: eligibility_determination
          when:
            - client_result.adjusted_earned_income <= income_limit
          then:
            eligible: approve
```

### `missing_info`

Strings describing gaps encountered during rule generation — referenced values not found in the index or source text.

```yaml
missing_info:
  - "monthly_limit for student exclusion not defined in index; see Addendum 1"
```

### `assumptions`

Strings recording inferential leaps made during rule generation — places where the AI could not derive an expression from the text and used a placeholder or estimate.

```yaml
assumptions:
  - "No expr_hint for blind_work_expenses — expr marked as '?'"
```

---

## Added by `/create-sample-tests`

### `sample_tests`

Pre-extraction test scaffolding — planned test cases written before the CIVIL ruleset exists, intended to validate coverage intent. Not used by `/extract-ruleset`; a separate validated test suite is produced by `/create-tests` after extraction.

Each entry has:
- `case_id` — format `<primary_tag>_<NNN>` (e.g., `allow_001`, `deny_gross_001`)
- `description` — one sentence describing what the case tests
- `inputs` — flat key-value map of input field names to values (never nested)
- `expected` — map of output field names to expected values; may include fields from `include_with_output`; `deny` cases include a `reasons` sub-map
- `tags` — list of coverage tags (e.g., `["allow"]`, `["deny", "gross_test"]`)

```yaml
sample_tests:
  - case_id: "allow_001"
    description: "All income thresholds comfortably met — standard approve path."
    inputs:
      client_gross_earned: 800
      household_type: individual
      benefit_year: 2025
    expected:
      eligible: approve
    tags: ["allow"]

  - case_id: "deny_gross_001"
    description: "Client income exceeds income standard; DOL confirms — deny."
    inputs:
      client_gross_earned: 3000
      household_type: individual
      benefit_year: 2025
    expected:
      eligible: deny
      reasons:
        - code: "INCOME_EXCEEDS_STANDARD"
    tags: ["deny", "gross_test"]
```
