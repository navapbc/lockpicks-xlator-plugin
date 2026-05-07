# `guidance/` Folder Reference

Each domain's ruleset guidance lives in `$DOMAINS_DIR/<domain>/specs/guidance/`. This folder replaces the single `guidance.yaml` file. Files are split by skill lifecycle: fields written and read together in the same pipeline phase stay in the same file.

`CLAUDE.md` in each domain's `guidance/` folder is a copy of this file, placed by `/new-domain`. Reading `CLAUDE.md` here gives a complete picture of the folder without consulting any other reference.

**Existing domains** continue to use `guidance.yaml` until manually migrated. The split structure is for new domains only.

---

## Folder contents

```
guidance/
  CLAUDE.md                ← copy of this file (placed by /new-domain)
  metadata.yaml            ← template_id, source_template, display_name, description
  prompt-context.yaml      ← role, scope, constraints, standards, guidance, edge_cases
  variables.yaml           ← input_variables, output_variables, intermediate_variables, constants_and_tables
  skeleton.yaml            ← skeleton
  ruleset-groups.yaml      ← ruleset_groups
  ruleset-modules.yaml     ← ruleset_modules
  sample-artifacts.yaml    ← sample_rules, missing_info, assumptions
  sample-tests.yaml        ← sample_tests
  policy-briefing.yaml     ← [analyst] goal, what_matters, basic_flow, known_exceptions, uncertainties
  scenario-cards.yaml      ← [analyst] scenarios list
  known-pitfalls.yaml      ← [analyst] do_not_assume, watch_for
  source-annotations.yaml  ← [analyst] important_sections, ignore_or_low_priority
```

**Pipeline files** (written by AI skills): `metadata.yaml`, `prompt-context.yaml`, `variables.yaml`, `skeleton.yaml`, `ruleset-groups.yaml`, `ruleset-modules.yaml`, `sample-artifacts.yaml`, `sample-tests.yaml`

**Analyst-authored files** (written by analyst, AI skill, or both): `policy-briefing.yaml`, `scenario-cards.yaml`, `known-pitfalls.yaml`, `source-annotations.yaml`

**Missing file behavior:** A missing guidance file is treated identically to a missing field in the old `guidance.yaml` — skills that need that file proceed with empty/default values rather than failing.

---

## Pipeline files

### `metadata.yaml`

Identifies the ruleset type and display metadata. Written once by `/declare-target-ruleset` or `/refine-guidance`; never updated after initial creation.

**Written by:** `/declare-target-ruleset`, `/refine-guidance` (template-copy path)
**Read by:** `/create-ruleset-groups` (`display_name`), `/create-ruleset-modules` (`template_id`), `/extract-sample-rules` (`display_name`), pre-flight Check 5 (`display_name`)

```yaml
template_id: calculate-earned-income-after-exclusions
source_template: suggestion--eligibility_check
display_name: "Determine Medicaid Eligibility based on Reported Income after Exclusions (Alaska)"
description: "Assess eligibility for Medicaid based on the adjusted monthly earned income amount."
```

#### `template_id`

Identifies the ruleset type in snake_case. Set to the `ruleset_name` from the suggestion file (when bootstrapped via `/declare-target-ruleset`) or the template's own `template_id` (when copied from a guidance template via `/refine-guidance`).

#### `source_template`

Records which source produced the file. For `/declare-target-ruleset`, uses the sentinel `suggestion--<ruleset_name>`. For `/refine-guidance`, uses the guidance template filename (without extension). Never updated after initial creation.

#### `display_name`

Human-readable name for the ruleset, shown in skill output and UI labels.

#### `description`

One-sentence description of what the ruleset computes.

---

### `prompt-context.yaml`

The AI persona and extraction directives. Written initially by `/declare-target-ruleset` or `/refine-guidance`; extended by `/create-skeleton` (constraints/standards/guidance/edge_cases) and SP-GuidanceCapture (same sections).

**Written by:** `/declare-target-ruleset`, `/refine-guidance` (template-copy path), `/create-skeleton` (appends to constraints/standards/guidance/edge_cases), SP-GuidanceCapture (appends to same)
**Read by:** `/create-skeleton`, `/create-sample-tests` (`edge_cases`), `/extract-sample-rules` (`role`), SP-GuidanceCapture, `/extract-ruleset` (Step 1 context injection)

```yaml
role: "You are a policy-to-rules analyst for eligibility determination based on earned income."
scope: "Convert the provided policy text into explicit, testable earned income calculation rules."

constraints:
  - "Do not interpret beyond the text; do not add requirements that aren't stated."
  - "Ensure every monetary formula traces directly to a policy citation."

standards:
  - "Use monthly amounts unless policy specifies otherwise."
  - "Express monetary values in dollars."

guidance:
  - "Apply rounding rules only if stated in the policy."

edge_cases:
  - "Alaska Native and American Indian real property is always excluded as a resource."
  - "[module: exclusion_chain] The infrequent/irregular earned income exclusion cap is $10/month."
```

#### `role`

The AI persona used when extracting rules. Frames the LLM as a domain-specific analyst.

#### `scope`

The extraction task description: what the AI should do with the policy text.

#### `constraints`

List of things the AI must not do during extraction — inferences to avoid, rounding rules, ordering constraints, etc.

#### `standards`

Normalization and formatting rules for values in the generated ruleset — units, categories, numeric representations.

#### `guidance`

Miscellaneous guidance: non-obvious rule patterns, formula structures, and domain-specific heuristics for the AI to apply.

#### `edge_cases`

Special populations, exceptional conditions, or policy interactions that override the general rules. Initialized as `[]` at creation; populated by `/create-skeleton` or via `/refine-guidance`. Module-scoped edge cases use the prefix `[module: <ruleset_module_name>]`.

---

### `variables.yaml`

Input, output, and intermediate variable declarations. Written initially by `/declare-target-ruleset` or `/refine-guidance`; extended by `/create-skeleton` (fills in `examples`, `computations`); `include_with_output` updated by `/tag-vars-to-include-with-output`.

**Written by:** `/declare-target-ruleset`, `/refine-guidance` (template-copy path), `/create-skeleton` (updates `examples`, `computations`), `/tag-vars-to-include-with-output` (`include_with_output` only)
**Read by:** `/create-skeleton`, `/create-ruleset-modules`, `/extract-sample-rules`, `/tag-vars-to-include-with-output`, `/create-sample-tests`, SP-TagOutputs (`intermediate_variables.include_with_output`), `/extract-ruleset` (Step 1 context injection), `validate_civil.py` (`output_variables.primary`)

```yaml
input_variables:
  categories:
    - category: earned_income
      description: "Total earned income as reported by the client/applicant."
      examples: [client_gross_earned]
      source_file: "domains/ak_doh/input/policy_docs/apa_manual/441/441-1 EARNED INCOME.md"
      source_section: "441-1 EARNED INCOME"

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

constants_and_tables:
  - name: expanded_refused_cash_income_limits
    description: "Expanded Refused Cash Income Limits table keyed by household_type and benefit_year."
  - name: student_earned_income_exclusion
    description: "Monthly and annual maximum limits for the student earned income exclusion."
```

#### `input_variables`

Describes the categories of inputs the ruleset accepts. Initialized with `examples: []`; populated with domain-specific variable names by `/create-skeleton`.

Each category has:
- `category` — snake_case name for the input group
- `description` — what this group of inputs represents
- `examples` — list of concrete variable names in this category
- `source_file` _(optional)_ — path to the policy document defining this input
- `source_section` _(optional)_ — section heading within that document
- `exact_phrase` _(optional)_ — verbatim policy text the AI should treat as authoritative for this input

#### `output_variables`

Declares the ruleset's outputs.

**`primary`** — the main output, with:
- `name` — variable name
- `type` — `bool`, `money`, `int`, `str`, or `enum`
- `values` _(enum only)_ — list of allowed values
- `description`

**`secondary_decisions`** — additional outputs returned alongside the primary, each with `name`, `type`, and `description`. Write `secondary_decisions: []` when there are none.

#### `intermediate_variables`

Describes computed variables that sit between inputs and outputs.

**`include_with_output`** — list of intermediate variable names to expose in the API's `ComputedBreakdown` response alongside the final output. Initialized as `[]`; populated by `/tag-vars-to-include-with-output`.

**`categories`** — list of variable groups, each with:
- `category` — snake_case group name
- `description` — what this group computes
- `examples` — list of variable names in this group
- `computations` _(optional)_ — list of `{name, expr}` pairs for variables with known expression hints
- `source_file`, `source_section`, `exact_phrase` _(optional)_

#### `constants_and_tables`

Named constants and lookup tables the ruleset references, with descriptions.

---

### `skeleton.yaml`

Records the confirmed computation structure — inputs, outputs, and intermediate computations with expression hints. Written once by `/create-skeleton`; updated on re-runs.

**Written by:** `/create-skeleton`
**Read by:** `/create-ruleset-groups`, `/create-ruleset-modules`, `/extract-sample-rules`

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

Sub-fields: `inputs` (flat list of confirmed input variable names), `outputs` (flat list of confirmed output variable names), `computations` (list of `{category, variables[], exprs}` entries), `flow_diagram` (ASCII diagram of computation flow).

---

### `ruleset-groups.yaml`

Named evaluation phases that rule `group:` annotations reference. Written by `/create-ruleset-groups`.

**Written by:** `/create-ruleset-groups`
**Read by:** `/create-ruleset-modules`, `/extract-sample-rules`, SP-MaintainabilityReview

```yaml
ruleset_groups:
  - name: income_classification
    description: Classify income as earned or unearned (441)
  - name: earned_exclusion_chain
    description: Apply 10-step earned income exclusion sequence (442-2, 442-4)
  - name: eligibility_determination
    description: Compare adjusted income to income standard and set eligible
```

Each entry: `name` (snake_case stage identifier), `description` (what this stage computes or decides).

---

### `ruleset-modules.yaml`

All modules that will be generated as separate `.civil.yaml` files — both sub-modules and the main program file. Written by `/create-ruleset-modules`; `sample_rules` sub-entries appended by `/extract-sample-rules`.

**Written by:** `/create-ruleset-modules`, `/extract-sample-rules` (appends `sample_rules` sub-entries)
**Read by:** `/extract-sample-rules`, `/tag-vars-to-include-with-output`, `/create-sample-tests`, `/create-tests`, SP-ResolveRulesetModules

```yaml
ruleset_modules:
  - name: exclusion_chain
    description: "10-step sequential earned income exclusion chain (442-4)."
    bound_entities: [ClientData, DOLRecord]
    rationale: reuse_across_entities
    depends_on: []
    sample_rules:
      - id: after_half_step
        rule_type: computed
        source: "One-half of remaining earned income after steps 1–7 is excluded."
        civil: |
          computed:
            - name: after_half
              expr: after_irwe * 0.5
              group: earned_exclusion_chain
  - name: eligibility
    description: "AK DOH Earned Income Exclusions"
    bound_entities: []
    rationale: main_module
    role: main
    depends_on: [exclusion_chain]
```

Each entry: `name`, `description`, `bound_entities` (CamelCase entity names; `[]` for main module), `rationale`, `role` (`main` for main program module; omit for sub-modules), `depends_on` (list of module names this module invokes). Exactly one entry per ruleset may have `role: main`.

---

### `sample-artifacts.yaml`

Sample CIVIL rules, gaps, and assumptions produced by `/extract-sample-rules`. Written atomically in a single pass.

**Written by:** `/extract-sample-rules`; `/create-sample-tests` may append to `assumptions`
**Read by:** `/tag-vars-to-include-with-output`, `/create-sample-tests`

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

missing_info:
  - "monthly_limit for student exclusion not defined in index; see Addendum 1"

assumptions:
  - "No expr_hint for blind_work_expenses — expr marked as '?'"
```

#### `sample_rules`

Top-level list of CIVIL rule snippets that did not match any ruleset module. Each entry: `id` (snake_case rule identifier), `rule_type` (`computed`, `categorical`, or `table-lookup`), `source` (verbatim sentence from the input index that grounds the rule), `civil` (full CIVIL YAML snippet as a literal block scalar).

#### `missing_info`

Strings describing gaps encountered during rule generation — referenced values not found in the index.

#### `assumptions`

Strings recording inferential leaps made during rule generation.

---

### `sample-tests.yaml`

Pre-extraction test scaffolding written by `/create-sample-tests`. Not used by `/extract-ruleset`; a separate validated test suite is produced by `/create-tests` after extraction.

**Written by:** `/create-sample-tests`
**Read by:** `/create-tests`

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

Each entry: `case_id` (format `<primary_tag>_<NNN>`), `description`, `inputs` (flat key-value map), `expected` (map of output fields to expected values; `deny` cases include a `reasons` sub-map), `tags`.

---

## Analyst-authored files

These files may be created by an analyst, an AI skill, or both. They are never required for pipeline skills to run — a missing analyst file is silently treated as empty.

### `policy-briefing.yaml`

High-level policy context written before or during domain setup. Helps AI skills understand the policy domain without re-reading all source documents.

**Written by:** Analyst (or AI skill)
**Read by:** Future authoring skills; available to `/extract-ruleset` as optional context

```yaml
goal: "Determine whether an applicant's earned income after exclusions falls below the income standard for Medicaid eligibility."
what_matters:
  - "Sequential 10-step exclusion chain (442-4) must be applied in order — reordering is not permitted."
  - "Reasonable compatibility check (523 B) compares client income to DOL income within 10%."
  - "Income standard comes from the Expanded Refused Cash Income Limits table keyed by household_type and benefit_year."
basic_flow: "Applicant reports earned income → exclusions applied step-by-step → adjusted income compared to income standard → eligibility determined, subject to compatibility check against DOL data."
known_exceptions:
  - "Alaska Native and American Indian real property is always excluded as a resource, regardless of value."
  - "The infrequent/irregular exclusion cap is $10/month for this program, not $30."
uncertainties:
  - "Monthly vs. quarterly DOL reporting — unclear which figure to use when both are available."
```

Fields:
- `goal` — What the ruleset is supposed to accomplish (1–2 sentences)
- `what_matters` — Most important aspects for extraction, as a list
- `basic_flow` — High-level policy flow description (1–3 sentences)
- `known_exceptions` — Edge cases the analyst already knows about, as a list
- `uncertainties` — Areas where the analyst is unsure about interpretation, as a list

---

### `scenario-cards.yaml`

Concrete input/output scenarios written before extraction. Used to validate that the extracted ruleset handles known cases correctly.

**Written by:** Analyst (or AI skill)
**Read by:** Future authoring skills; may inform `/create-sample-tests`

```yaml
scenarios:
  - scenario: "Base case — household with steady earned income below standard"
    inputs:
      client_gross_earned: 800
      household_type: individual
      benefit_year: 2025
    expected_result: "approve"
    reasoning: "Adjusted income after all exclusions falls below the income standard for an individual in 2025."
    confidence: high

  - scenario: "High income — client clearly over standard, DOL confirms"
    inputs:
      client_gross_earned: 5000
      household_type: individual
      benefit_year: 2025
    expected_result: "deny"
    reasoning: "Even after maximum exclusions, adjusted income exceeds income standard."
    confidence: high

  - scenario: "Borderline — client income near standard, DOL within 10%"
    inputs:
      client_gross_earned: 1100
      dol_gross_earned: 1050
      household_type: individual
      benefit_year: 2025
    expected_result: "approve"
    reasoning: "Adjusted income just below standard; DOL income within 10% so reasonable compatibility passes."
    confidence: medium
```

Each entry: `scenario` (description), `inputs` (flat key-value map), `expected_result`, `reasoning`, `confidence` (`high`, `medium`, or `low`).

---

### `known-pitfalls.yaml`

Analyst notes on common extraction mistakes and edge cases that surprised the analyst. Helps AI skills avoid repeating known errors.

**Written by:** Analyst (or AI skill)
**Read by:** Future authoring skills; available to `/extract-ruleset` as optional context

```yaml
do_not_assume:
  - "Do not assume the exclusion chain steps can be reordered — the 10-step sequence in 442-4 is mandatory and sequential."
  - "Do not assume 'household' and 'benefit unit' are interchangeable — only benefit unit members count toward the income test."
  - "Do not use the federal $30 cap for infrequent/irregular income — this program uses $10/month."
watch_for:
  - "The Expanded Refused Cash Income Limits table has separate columns for each benefit year — always key by both household_type and benefit_year."
  - "The $20 general income exclusion and the $65 earned income exclusion are each allowed only once per couple, even when both members have earned income."
```

Fields:
- `do_not_assume` — List of false assumptions to avoid during extraction
- `watch_for` — List of edge cases or subtleties the analyst has encountered

---

### `source-annotations.yaml`

Analyst annotations about which source document sections are most and least important for extraction. Helps AI skills focus their attention.

**Written by:** Analyst (or AI skill)
**Read by:** Future authoring skills

```yaml
important_sections:
  - file: "input/policy_docs/apa_manual/442/442-2 EARNED INCOME EXCLUSIONS.md"
    sections: ["442-2 A", "442-2 B", "442-2 C", "442-2 D", "442-2 E", "442-2 F", "442-2 G", "442-2 I", "442-2 J"]
    notes: "Primary source for the 10-step exclusion chain — all subsections are required."
  - file: "input/policy_docs/adltc_manual/523 MEDICAID EXCEPTIONS TO APA INCOME POLICY.md"
    sections: ["Reasonable Compatibility (523 B)"]
    notes: "Defines the 10% compatibility check against DOL data."

ignore_or_low_priority:
  - file: "input/policy_docs/apa_manual/441/441-3 SELF-EMPLOYMENT INCOME.md"
    reason: "Self-employment income rules apply only to a separate ruleset — not part of this calculation."
```

Fields:
- `important_sections` — List of `{file, sections, notes}` entries marking high-priority source sections
- `ignore_or_low_priority` — List of `{file, reason}` entries marking source sections to deprioritize
