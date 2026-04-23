# CIVIL DSL Specification

**Civic Instructions & Validations Intermediate Language**

This specification is the authoritative what and why of the DSL. It includes design rationale, goals, full syntax narrative, examples of all constructs, compilation targets, jurisdiction layering. It is written in prose with embedded YAML examples.

* Read CIVIL_DSL_spec.md to understand the DSL
* Read civil-quickref.md to write a CIVIL module (used by AI)

---

## Introduction

Below is a **basic, practical DSL** aimed at government policy/regulation logic (eligibility + tax instructions) that's easy to **transpile** later to Catala, OPA/Rego, DMN, Drools, etc. It's designed around: **jurisdiction + effective dates + citations + explainability**.

This specification defines **CIVIL** (Civic Instructions & Validations Intermediate Language), the target architecture for Xlator's policy rule engine.

---

Design goals
------------

-   **Policy-native metadata**: jurisdiction, authority, citations, effective dates, version.
-   **Deterministic evaluation**: no hidden state; input facts → outputs.
-   **Layering**: federal baseline + state + city overrides/stricter rules.
-   **Explainability**: rules emit _reasons_ with citations.
-   **Transpiler-friendly**: simple AST, minimal syntax sugar.

---

Core concepts
-------------

### 1) Inputs are typed “facts”

Facts are the only input; the engine is a pure function:

`evaluate(policy_bundle, facts, as_of_date, jurisdiction) -> decisions + reasons`

### 2) Outputs are “decisions”

Decisions can be boolean, enum, numeric, or structured objects.

### 3) Rules are “when/then” with a reason

A rule can:

-   set a decision (`eligible = true`)
-   compute derived values (`agi = ...`)
-   emit instructions (`instructions += [...]`)
-   add violations/reasons (`reasons += reason(...)`)

### 4) Policy bundle supports versions + effective dates

Quarterly updates become a new policy bundle version, not in-place edits.

---

DSL structure (high level)
--------------------------

-   `policy_bundle`: a versioned collection of modules
-   `module`: scoped to domain (eligibility, tax, etc.)
-   `definitions`: types, constants, tables
-   `rule_set`: rules + precedence strategy

---

Syntax (YAML-like, transpiler-friendly)
---------------------------------------

### Policy bundle skeleton

```yaml
bundle: "civics:benefits"
version: "2026Q1"
effective:
  start: 2026-01-01
  end: 2026-03-31
jurisdiction:
  level: federal
  country: US

modules:
  - file: eligibility/housing_assistance.civil.yaml
  - file: tax/filing_basics.civil.yaml
```

---

Module format
-------------

### 1) Types + fact schema

```yaml
module: "housing_assistance"
description: "Housing Assistance eligibility rules"
jurisdiction: { level: federal, country: US }

facts:
  Applicant:
    fields:
      age: { type: int }
      resident: { type: bool }
      household_size: { type: int }
      annual_income: { type: money, currency: USD }
      city: { type: string }
      state: { type: string }

decisions:
  eligible: { type: bool, default: false }
  reasons:  { type: list, item: Reason, default: [] }

types:
  # Optional 'description:' is supported on fact fields, decision fields,
  # table definitions, rule_set, and individual rules.
  Reason:
    fields:
      code: { type: string }
      message: { type: string }
      citations: { type: list, item: Citation }
  Citation:
    fields:
      label: { type: string }      # e.g., "42 USC § 1437a"
      url: { type: string, optional: true }
      excerpt: { type: string, optional: true }  # keep short
```

### 2) Constants + tables

```yaml
constants:
  MIN_AGE: 18

tables:
  income_limit_by_household:
    key: [household_size]
    value: [max_income]
    rows:
      - { household_size: 1, max_income: 35000 }
      - { household_size: 2, max_income: 42000 }
      - { household_size: 3, max_income: 48000 }
```

### 2b) Computed values (CIVIL v2)

The `computed:` section defines intermediate derived values using CIVIL expressions. Unlike `decisions:`, computed values are not primary outputs — they are results available to `rules:` (in `when:` expressions) and to other `computed:` fields.

```yaml
computed:
  earned_income_deduction:
    type: money
    currency: USD
    description: "20% of earned income (7 CFR § 273.9(b))"
    expr: "Household.earned_income * EARNED_INCOME_DEDUCTION_RATE"

  is_exempt_household:
    type: bool
    description: "Elderly or disabled — exempt from gross test and shelter cap"
    expr: "Household.has_elderly_member || Household.has_disabled_member"

  shelter_deduction:
    type: money
    description: "Shelter deduction, capped unless exempt"
    conditional:
      if: "is_exempt_household"
      then: "shelter_excess"
      else: "min(shelter_excess, SHELTER_DEDUCTION_CAP)"
```

Each field uses either `expr:` (a single expression) or `conditional:` (if/then/else). Define fields in dependency order — no forward references.

**Expression language additions in `computed:` fields:**
- `max(a, b)` — maps to Catala `max integer of [a, b]` (or `max money of [a, b]` for money types); maps to Rego `max([a, b])`
- `min(a, b)` — maps to Catala `min integer of [a, b]` (or `min money of [a, b]` for money types)
- Other computed field names referenced by bare identifier (e.g., `shelter_excess`); maps to Rego `min([a, b])`; 

**`tags:` (CIVIL v3, optional)** — A list of string labels on a computed field. Currently only `"output"` has defined behavior:

- Marks the field for exposure as an `output` declaration (instead of `internal`) when the Catala transpiler is run.
- The tagged field then appears in the demo API's `ComputedBreakdown` response model, enabling policy transparency.
- **Ignored by the Rego transpiler** — all computed fields are automatically included in `decision.computed` for Rego backends.
- **Restriction**: fields with `type: bool` and `expr:` are ineligible (Catala uses `condition` syntax for these, which cannot be declared `output`). Fields with `type: bool` and `conditional:` are eligible.

```yaml
computed:
  countable_earned_income:
    type: money
    expr: "after_blind_work"
    tags: [output]   # CIVIL v3: expose in Catala output + ComputedBreakdown
```

---

### 2c) Review blocks (optional, on rules and computed fields)

`review:` blocks attach extraction quality scores to individual rules and computed fields. All four integer fields are required when the block is present. Scores have no effect on transpilation or OPA evaluation — they exist only for human review.

```yaml
review:
  extraction_fidelity: int  # 1–5: how accurately the AI captured the policy intent
  source_clarity: int       # 1–5: how clear and unambiguous the source policy text was
  logic_complexity: int     # 1–5: number of conditions, boolean depth, table lookups
  policy_complexity: int    # 1–5: density of legalese, cross-references, exceptions
  notes: string             # optional; required for any score ≤ 2 or ≥ 4
```

Score scale: 1 = very low · 3 = moderate · 5 = very high.

Example on a rule:

```yaml
rules:
  - id: "FED-SNAP-DENY-001"
    kind: deny
    priority: 1
    when: "Household.gross_monthly_income > gross_limit"
    then:
      - add_reason:
          code: GROSS_INCOME_EXCEEDED
          message: "Gross income exceeds the 130% FPL limit"
    review:
      extraction_fidelity: 5
      source_clarity: 5
      logic_complexity: 2
      policy_complexity: 1
      notes: "Direct threshold test; formula explicit in 7 CFR § 273.9(a)(1)"
```

---

### 2d) Source provenance (optional, on fact fields, computed fields, tables, and rules)

The `source:` field records where in the source policy document an element was defined. It is a plain free-text string — typically a CFR section plus heading. Its value is purely for human traceability; it has no effect on transpilation or OPA evaluation.

```yaml
source: "7 CFR § 273.9(a)(1) — Gross Income Test"
```

**Where it applies:** `FactField`, `ComputedField`, `TableDef`, `Rule`.

**Where it does not apply:** `FactEntity` (container, not a leaf definition), `DecisionField` (output), `constants` (untyped dict).

**`source:` vs `citations:` distinction:**

| Field | Purpose | Audience |
|-------|---------|---------|
| `source:` (on field/table/rule) | Where in the policy doc this element was *extracted from* — developer traceability | CIVIL authors, auditors |
| `citations:` (in add_reason actions) | Legal authority shown in *denial explanations to applicants* | End users, caseworkers |

A deny rule will often have the same CFR section in both. That is expected — they serve different audiences.

Example on a fact field, computed field, table, and rule:

```yaml
facts:
  Household:
    fields:
      gross_monthly_income:
        type: money
        currency: USD
        description: "Total gross monthly income before deductions"
        source: "7 CFR § 273.9(a) — Income and Deductions"

computed:
  gross_income_exceeds_limit:
    type: bool
    expr: "Household.gross_monthly_income > gross_limit"
    source: "7 CFR § 273.9(a)(1) — Gross Income Test"

tables:
  gross_income_limits:
    key: [household_size]
    value: [monthly_limit]
    source: "7 CFR § 273.9(a)(1) — Gross Income Limits Table"
    rows:
      - { household_size: 1, monthly_limit: 1580 }

rules:
  - id: "FED-SNAP-DENY-GROSS"
    kind: deny
    priority: 1
    source: "7 CFR § 273.9(a)(1) — Gross Income Test"
    when: "gross_income_exceeds_limit"
    then:
      - add_reason:
          code: GROSS_INCOME_EXCEEDED
          message: "Gross income exceeds the 130% FPL limit."
          citations:
            - { label: "7 CFR § 273.9(a)(1)" }
```

---

### 2e) decisions: with expr: or conditional: (CIVIL v5)

**CIVIL v5** extends `decisions:` fields to support any CIVIL type (`money`, `int`, `string`, `enum`, etc.) with an optional `expr:` or `conditional:` for direct expression-driven assignment.

- `bool` decision fields now require an explicit `expr:` (the implicit `count(reasons)==0` is gone).
- `list`/`set` decision fields remain rule-driven (no `expr:` needed).
- Non-list/non-set decision fields without `expr:` or `conditional:` are a validation error.

**Use case:** Computation-only sub-modules (e.g., income calculators) have their primary numeric output as a `decisions:` field with `expr:` rather than a `computed:` field with `tags: [output]`. This makes the value top-level in the `decision` object instead of nested under `decision.computed`.

```yaml
decisions:
  eligible:
    type: bool
    default: false
    description: "Whether the applicant meets all eligibility criteria"
    expr: "count(reasons) == 0"           # explicit; overrides no default

  reasons:
    type: list
    item: Reason
    default: []
    description: "Denial reasons (empty if eligible)"

  # For computation-output modules — primary numeric output as a decision:
  adjusted_income:
    type: money
    currency: USD
    description: "Final adjusted income after all exclusion steps"
    expr: "step_n_result - exclusion_a - exclusion_b"

  # Conditional (if/then/else) also valid:
  determination:
    type: string
    default: "approve"
    description: "Outcome: 'approve', 'deny', or 'manual_verification'"
    conditional:
      if: "has_income_denial"
      then: "deny"
      else: "determination_non_denial"
```

**Transpilation (Rego):** Expression-driven decisions emit as top-level rules:
```rego
adjusted_income := half_remaining - blind_work_expenses - plan_earnings
decision := {
    "reasons": [r | r := reasons[_]],
    "adjusted_income": adjusted_income,
    ...
}
```

**Transpilation (Catala):** Decision fields emit as `definition field equals <expr>` — no label/exception chaining needed since `eligible` is now a pure expression.

---

### 2f) Invoke: ruleset module computed fields (CIVIL v4)

An `invoke:` computed field runs an external CIVIL module as a sub-computation and makes its result accessible in the parent module's `computed:` chain and rules.

**Primary use case:** Cross-referencing the same income-calculation module against two different data sources (client-reported vs. DOL wage records) and comparing the results.

**Syntax:**

```yaml
computed:
  client_result:
    type: object                   # required for invoke: fields
    module: earned_income          # resolves to $DOMAINS_DIR/<domain>/specs/earned_income.civil.yaml
    description: "Income calc against client-reported data"
    invoke:
      bind:
        Household: ClientData      # sub-module entity name ← parent entity name

  dol_result:
    type: object
    module: earned_income
    invoke:
      bind:
        Household: DOLRecord       # same sub-module, different parent entity

  income_gap:
    type: money
    expr: "client_result.countable_earned_income - dol_result.countable_earned_income"

rules:
  - id: DISCREP-001
    kind: deny
    priority: 1
    when: "income_gap > DISCREPANCY_LIMIT"
    then:
      - add_reason:
          code: INCOME_MISMATCH
          message: "Income differs from DOL records."
          citations:
            - label: "AK Admin Code § 7 AAC 100"
```

**Result field access** is flat: `client_result.net_income` resolves a computed field;
`client_result.eligible` resolves a decision field. Decision fields win if names collide.

**Constraints:**

| Constraint | Description |
|-----------|-------------|
| Same-domain only | Sub-modules must be in the same domain (`$DOMAINS_DIR/<domain>/specs/`) |
| `type: object` | `invoke:` fields must use `type: object` |
| `module:` required | Must name the sub-module file (without `.civil.yaml` extension) |
| Non-empty `bind:` | At least one entity binding is required |
| `tags: [output]` required | Sub-module computed fields must have `tags: [output]` to be accessible from a parent `invoke:` expression. Decision fields (e.g. `eligible`) are always accessible. |
| No circular invocation | A→B→A cycles are detected and rejected at validation time |

**Transpilation:**

- **Catala (primary):** Emits `> Using SubModuleName`, module-qualified subscope declaration (`output field_name scope ModuleName.ScopeDecision`), and a "Subscope Wiring" section that wires parent entity struct fields to subscope inputs. Invoke-bound entities are declared as Catala structure types rather than flattened inputs.
- **Rego (secondary):** Emits `import data.<domain>.<sub_module>` and a `with input as input.<entity_var>` assignment. The parent input JSON must provide invoke-bound entities as nested objects keyed by snake_case entity name (e.g. `input.client_data = {...}`).

---

### 2g) Declarative table lookup computed fields (CIVIL v7)

A `table_lookup` computed field provides a declarative shorthand for a computed field whose value is a direct lookup in a named table. It replaces the verbose `expr: "table('name', Entity.field1, Entity.field2).col"` form with structured, machine-readable YAML.

**Syntax:**

```yaml
computed:
  income_limit:
    type: money
    currency: USD
    table_lookup:
      table: expanded_refused_cash_income_limits  # must exist in tables:
      key: [household_type, benefit_year]         # key column names (positional)
      # value: income_limit                       # optional; required only if table has >1 value column
```

**Key value resolution** — each name in `key:` is resolved to a CIVIL expression in order:
1. Computed field names in the same module → bare name (e.g. `household_type`)
2. Entity fields across all entities → `Entity.field` (e.g. `ClientData.household_type`)

If a name is found in multiple entities, validation raises an ambiguity error. If not found anywhere, validation raises a missing-ref error.

**Value column resolution:**
- `value:` omitted → the table's `value:` list must have exactly one column; that column is used automatically
- `value:` specified → must name a column in the table's `value:` list; required when the table has multiple value columns

**Transpilation:** `table_lookup` desugars to the equivalent `expr:` string at transpile time via `civil_expr.normalize_computed_doc()`. Transpiler output is identical to writing the `expr:` form manually. Both Catala and Rego transpilers are supported.

**Constraints:**

| Constraint | Description |
|-----------|-------------|
| Table must exist | `table:` must name a key in the module's `tables:` dict |
| Key columns must exist | Each name in `key:` must be a key column of the named table |
| Value column valid | `value:` (if specified) must name a value column of the named table |
| Single-value auto-infer | `value:` may be omitted only when the table has exactly one value column |
| Mutually exclusive | `table_lookup:` cannot be combined with `expr:`, `conditional:`, or `invoke:` |

---

### 2h) Rule maintainability annotations (CIVIL v6)

**CIVIL v6** adds three opt-in annotation fields to improve maintainability at scale. All three are transpiler no-ops — they have no effect on rule evaluation or code generation.

#### `rule_set.ruleset_groups:` — named evaluation phases

Defines the controlled vocabulary for rule grouping. When non-empty, `validate_civil.py` checks that every rule's `group:` value matches a stage name listed here.

```yaml
rule_set:
  name: snap_eligibility
  precedence: deny_overrides_allow
  ruleset_groups:
    - name: income_test
      description: Gross and net income eligibility tests
    - name: asset_test
      description: Countable asset limit check
    - name: household_composition
      description: Rules determining household unit size
```

Populated by `/refine-guidance` Step 3 (ruleset group elicitation) and auto-copied into the CIVIL file by `/extract-ruleset` Step 4.

#### `rule.group:` — ruleset group assignment

Tags a rule with the evaluation phase it belongs to. Enables readers and reviewers to quickly find, compare, and retire all rules for a given policy test.

```yaml
rules:
  - id: SNAP-INCOME-DENY-001
    kind: deny
    priority: 10
    group: income_test          # must match a ruleset_groups name (when defined)
    when: gross_income > gross_limit
    then: ...
```

**Validation:** If `ruleset_groups` is non-empty and a rule has `group:` set to an unknown stage name, `validate_civil.py` errors. If `ruleset_groups` is empty but a rule has `group:` set, the validator emits a warning (not an error) — backward compatible.

#### `rule.mutex_group:` — mutual-exclusion grouping

Marks rules that are competing alternatives for the same policy decision. Rules sharing a `mutex_group` must have unique `priority` values (enforced by the validator). Only one should fire per evaluation.

```yaml
rules:
  - id: SNAP-INCOME-DENY-001
    kind: deny
    priority: 10
    group: income_test
    mutex_group: gross_test     # this rule and DENY-002 are competing alternatives
    when: gross_income > gross_limit
    then: ...

  - id: SNAP-INCOME-DENY-002
    kind: deny
    priority: 20
    group: income_test
    mutex_group: gross_test     # same mutex_group — unique priority required
    when: gross_income > adjusted_gross_limit
    then: ...
```

**Validation:**
- Singleton `mutex_group` (only one member): warning
- Duplicate `priority` values within a `mutex_group`: error

---

### 3) Rule set

```yaml
rule_set:
  name: "federal_eligibility"
  description: "Federal housing assistance eligibility"  # optional
  precedence: "deny_overrides_allow"   # other options: allow_overrides_deny, first_match, priority_order

rules:
  - id: "FED-RESIDENCY"
    kind: "deny"
    description: "Applicant must be a US resident"  # optional
    when: "Applicant.resident == false"
    then:
      - add_reason:
          code: "NOT_RESIDENT"
          message: "Applicant must be a resident."
          citations:
            - { label: "42 USC § 1437a" }

  - id: "FED-MIN-AGE"
    kind: "deny"
    when: "Applicant.age < MIN_AGE"
    then:
      - add_reason:
          code: "UNDERAGE"
          message: "Applicant must be at least 18 years old."
          citations:
            - { label: "24 CFR § 5.403" }

  - id: "FED-INCOME"
    kind: "deny"
    when: "Applicant.annual_income > table('income_limit_by_household', Applicant.household_size).max_income"
    then:
      - add_reason:
          code: "INCOME_TOO_HIGH"
          message: "Income exceeds the limit for household size."
          citations:
            - { label: "24 CFR § 5.609" }

  - id: "FED-ALLOW"
    kind: "allow"
    when: "true"
    then:
      - set: { eligible: true }
```

**Available `then:` action types:**

| Action | Effect |
|--------|--------|
| `set: { decision: value }` | Set a decision output to a value |
| `add_reason: { code, message, citations }` | Append a Reason to a list-typed decision |
| `add_instruction: { step, message, citations }` | Append an Instruction to a list-typed decision |
| `add_to_set: { decision: value }` | Add a value to a set-typed decision |
| `append_to_list: { decision: value }` | Append a value to a list-typed decision |

Each `then:` entry must have exactly one action type.

**Notes for transpilers**

-   `kind: deny/allow` makes compilation easy to “deny wins” logic (OPA style) or DMN hit policies.
-   `add_reason` gives explainability without needing an engine-specific tracer.
-   `table()` is declarative and maps to DMN tables, Rego data, or Drools facts. In Catala, it expands into one stacked `definition field under condition key = row_key consequence equals row_value` block per table row.

---

Jurisdiction layering (federal → state → city)
----------------------------------------------

> **Implementation status:** Overlay bundles are defined in this spec but are not yet validated by `tools/validate_civil.py` or transpiled by `tools/transpile_to_rego.py`. The validator and transpiler operate on individual CIVIL modules only.

You’ll want a _composition DSL_ that defines how modules merge.

### Overlay bundle example (Illinois overrides)

```yaml
bundle: "civics:benefits"
version: "2026Q1"
effective: { start: 2026-01-01, end: 2026-03-31 }
jurisdiction: { level: state, country: US, state: IL }

overlays:
  - target_module: "housing_assistance"
    strategy: "add_stricter_denials"     # other: replace_rule_set, override_by_id
    rules:
      - id: "IL-INCOME-ADJUST"
        kind: "deny"
        when: "Applicant.annual_income > 45000 && Applicant.household_size == 3"
        then:
          - add_reason:
              code: "IL_INCOME_TOO_HIGH"
              message: "Illinois sets a lower income cap for household size 3."
              citations:
                - { label: "305 ILCS 5/xx-x" }
```

### City overlay example (Chicago adds requirement)

```yaml
bundle: "civics:benefits"
version: "2026Q1"
jurisdiction: { level: city, country: US, state: IL, city: Chicago }
effective: { start: 2026-01-01, end: 2026-03-31 }

overlays:
  - target_module: "housing_assistance"
    strategy: "add_stricter_denials"
    rules:
      - id: "CHI-RESIDENCY-DURATION"
        kind: "deny"
        when: "Applicant.resident == true && Applicant.residency_months < 6"
        then:
          - add_reason:
              code: "CHI_RESIDENCY_TOO_SHORT"
              message: "Chicago requires 6+ months of residency."
              citations:
                - { label: "Chicago Municipal Code § x-x-x" }
```

To support this, the fact schema can include `Applicant.residency_months`.

---

Tax filing instructions example
-------------------------------

Taxes often need:
-   computed values
-   conditional instructions
-   outputs beyond booleans

### Module

```yaml
module: "tax.filing_basics"
jurisdiction: { level: federal, country: US }

facts:
  Taxpayer:
    fields:
      filing_status: { type: enum, values: [single, mfj, mfs, hoh] }
      age: { type: int }
      has_w2: { type: bool }
      has_1099: { type: bool }
      self_employed: { type: bool }
      gross_income: { type: money, currency: USD }
      withholding: { type: money, currency: USD }

decisions:
  must_file: { type: bool, default: false }
  forms:     { type: set, item: string, default: [] }
  instructions: { type: list, item: Instruction, default: [] }
  reasons:   { type: list, item: Reason, default: [] }

types:
  Instruction:
    fields:
      step: { type: string }
      message: { type: string }
      citations: { type: list, item: Citation }
```

### Rules

```yaml
constants:
  SINGLE_THRESHOLD: 14600   # example placeholder

rule_set:
  name: "filing_requirement"
  precedence: "priority_order"

rules:
  - id: "ADD-FORM-1040"
    kind: "allow"
    priority: 10
    when: "true"
    then:
      - add_to_set: { forms: "Form 1040" }

  - id: "SELF_EMPLOYED_SCHEDULE_C"
    kind: "allow"
    priority: 20
    when: "Taxpayer.self_employed == true"
    then:
      - add_to_set: { forms: "Schedule C" }
      - add_instruction:
          step: "Report self-employment income"
          message: "Complete Schedule C for business income/expenses."
          citations:
            - { label: "IRS Instructions for Schedule C" }

  - id: "MUST_FILE_SINGLE"
    kind: "allow"
    priority: 30
    when: "Taxpayer.filing_status == 'single' && Taxpayer.gross_income >= SINGLE_THRESHOLD"
    then:
      - set: { must_file: true }
      - add_reason:
          code: "INCOME_OVER_THRESHOLD"
          message: "Gross income meets or exceeds the filing threshold."
          citations:
            - { label: "26 USC § 6012" }
```

This is intentionally “instructional”—it outputs forms/instructions, not just allow/deny.

---

Expression language (minimal)
-----------------------------

Keep it small so you can transpile to almost anything:

-   literals: numbers, strings, booleans, dates
-   field access: `Applicant.age`
-   boolean ops: `&& || !`
-   comparisons: `== != < <= > >=`
-   arithmetic: `+ - * /`
-   functions:
    -   `table(name, key...).field`
    -   `in(value, [a,b,c])`
    -   `exists(field)` / `is_null(field)`
    -   `date("YYYY-MM-DD")`, `between(date, start, end)`
    -   `max(a, b)`, `min(a, b)` — numeric comparison (CIVIL v2; for `computed:` fields)

---

Compilation targets (how this maps later)
-----------------------------------------

This DSL is basically an **intermediate representation**:

-   **Catala**: inputs/computed fields become scope variables; rules become stacked `under condition` definitions; tables expand to one `under condition` block per row; constants are inlined as literals.
-   **OPA/Rego**: rules become derived booleans/sets; tables become data; reasons become structured outputs.
-   **DMN**: tables map directly; precedence maps to hit policies; reason codes become outputs.
-   **Drools**: facts are working memory; rules map to LHS/RHS; reasons are inserted.

Because you’ve separated:
-   metadata
-   tables/constants
-   pure expressions
-   deterministic actions (`set`, `add_reason`, `add_instruction`)

…you’re not locked in.

---

What I’d build first (minimum viable)
-------------------------------------

1.  **Parser** (YAML → AST) with schema validation
2.  **Reference interpreter** (slow but correct) to run tests
3.  **Golden test harness** (fixtures per jurisdiction + quarter)
4.  **Transpiler** to your first engine once semantics stabilize

The reference interpreter is critical: it becomes your “semantic source of truth” while you swap engines.

---

Possible next steps:
-   a formal EBNF-style grammar (if you prefer a text syntax over YAML),
-   a precise precedence/merge algorithm for overlays,
-   and a canonical JSON AST shape to make the transpiler straightforward.
