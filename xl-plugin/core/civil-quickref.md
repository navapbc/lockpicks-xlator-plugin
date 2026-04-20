# CIVIL DSL — Authoring Quick Reference

<!-- Last verified against tools/civil_schema.py: 2026-03-26 -->

This is a **Claude authoring cheat sheet** for writing valid CIVIL YAML modules.
For full specification and design rationale, see [CIVIL_DSL_spec.md](CIVIL_DSL_spec.md).

---

## Module-Level Structure (`CivilModule`)

| Field | Required | Notes |
|-------|----------|-------|
| `module` | ✅ | Unique identifier, e.g. `"snap_eligibility"` |
| `description` | ✅ | Human-readable description |
| `version` | ✅ | e.g. `"2026Q1"` |
| `jurisdiction` | ✅ | See `Jurisdiction` table |
| `effective` | ✅ | See `Effective` table |
| `facts` | ✅ | Dict of entity names → `FactEntity` |
| `decisions` | ✅ | Dict of decision names → `DecisionField` |
| `rule_set` | ✅ | See `RuleSet` table |
| `rules` | ✅ | List of `Rule` objects |
| `tables` | — | Optional lookup tables |
| `constants` | — | Optional named constants (UPPER_SNAKE_CASE) |
| `computed` | — | Optional derived intermediate values (CIVIL v2); supports `tags` (CIVIL v3); supports `invoke:` (CIVIL v4); `decisions:` supports `expr:`/`conditional:` (CIVIL v5); `rule_set.workflow_stages` + `rule.group`/`rule.mutex_group` (CIVIL v6); supports `table_lookup:` (CIVIL v7) |
| `types` | — | Optional custom type definitions |

---

## `FactEntity`

| Field | Required | Notes |
|-------|----------|-------|
| `description` | — | Human-readable entity description |
| `fields` | ✅ | Dict of field names → `FactField` |

Entity names use **PascalCase** (e.g. `Household`, `Applicant`).

## `FactField`

| Field | Required | Notes |
|-------|----------|-------|
| `type` | ✅ | See valid fact types below |
| `description` | — | Human-readable field description |
| `source` | — | Policy document location, e.g. `"7 CFR § 273.9(a) — Income and Deductions"` |
| `optional` | — | `true` if the field may be absent (default: `false`) |
| `currency` | — | Currency code for `money` type, e.g. `USD` |
| `values` | — | List of allowed strings for `enum` type |

> ⚠️ **`FactField` has no `default:` attribute.** Use `optional: true` for optional fields.

Valid `type` values for fact fields:
`int`, `float`, `bool`, `string`, `date`, `money`, `list`, `set`, `enum`

> ⚠️ **Use `string`, not `str`.**

---

## `ComputedField`

| Field | Required | Notes |
|-------|----------|-------|
| `type` | ✅ | `money`, `bool`, `float`, `int`, `string`, or `object` (invoke: only) |
| `module` | ✅ for `invoke:` | Sub-module name (no extension). E.g. `earned_income` |
| `currency` | — | Currency code for `money` type |
| `description` | — | Human-readable description |
| `source` | — | Policy document location, e.g. `"7 CFR § 273.9(d)(1) — Earned Income Deduction"` |
| `expr` | ✅ or `conditional`/`invoke` | CIVIL expression (mutually exclusive with the others) |
| `conditional` | ✅ or `expr`/`invoke` | If/then/else branch (mutually exclusive with the others) |
| `invoke` | ✅ or `expr`/`conditional` | (CIVIL v4) Sub-ruleset invocation. Requires `module:` and `type: object` |
| `review` | — | `ReviewBlock` with extraction quality scores |
| `tags` | — | (CIVIL v3) e.g. `[output]` to expose as Catala `output` declaration in the demo API |

> ⚠️ **`ComputedField.type` for `invoke:` fields must be `object`.** All other fields use `money`, `bool`, `float`, `int`, or `string`.

> ⚠️ **Exactly one of `expr`, `conditional`, or `invoke` must be present.**

> ⚠️ **Sub-module computed fields must have `tags: [output]`** to be accessible from a parent `invoke:` expression.
> Decision fields (e.g. `eligible`) are always accessible from the parent scope.

## `Conditional`

| Field | Required | Notes |
|-------|----------|-------|
| `if` | ✅ | Boolean CIVIL expression |
| `then` | ✅ | CIVIL expression for the true branch |
| `else` | ✅ | CIVIL expression for the false branch |

> ⚠️ **All three branches are required.** There is no optional `else`.

## `InvokeField` (CIVIL v4)

Invoke an external CIVIL module as a sub-computation. Access results as `field_name.output_field`.

| Field | Required | Notes |
|-------|----------|-------|
| `bind` | ✅ | Dict mapping sub-module entity → parent entity (at least one entry) |

**Example — invoke the same module against two data sources and compare results:**

```yaml
computed:
  client_result:
    type: object
    module: earned_income          # resolves to $DOMAINS_DIR/<domain>/specs/earned_income.civil.yaml
    description: "Income calc against client-reported data"
    invoke:
      bind:
        Household: ClientData      # sub-module entity ← parent entity

  dol_result:
    type: object
    module: earned_income
    description: "Income calc against DOL wage records"
    invoke:
      bind:
        Household: DOLRecord

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
```

**Constraints:**
- Sub-modules must be in the same domain (`$DOMAINS_DIR/<domain>/specs/`)
- `bind:` must have at least one entry; empty `bind: {}` is rejected
- Parent `invoke:` fields have `type: object` and `module:` set
- Sub-module computed fields need `tags: [output]` to be accessed from the parent
- Catala is the primary transpilation target; Rego is secondary (nested `input.<entity_var>` form)

> ⚠️ **`tags: [output]` required for sub-module field access**
>
> When a parent expression references `invoke_field.some_attr`, the sub-module's `some_attr` computed
> field **must** have `tags: [output]`. Without it the validator will warn and `catala typecheck` will
> fail with an opaque "variable not found" error.
>
> ```yaml
> # sub-module (e.g. income_calc.civil.yaml)
> computed:
>   adjusted_income:
>     type: money
>     tags: [output]        # ✅ required — parent can reference income_calc_result.adjusted_income
>     expr: "..."
>
>   internal_step:
>     type: money
>     expr: "..."           # ❌ no tags: [output] — NOT accessible from the parent module
> ```
>
> Decision fields (`decisions:` section) are always accessible from the parent; only `computed:` fields
> need the tag. The validator emits a `WARNING:` for each untagged reference it detects.

---

## `table_lookup` computed field (CIVIL v7)

Declarative shorthand for a computed field that is a direct table lookup. Desugars to the equivalent `expr:` at transpile time.

```yaml
computed:
  income_limit:
    type: money
    currency: USD
    description: "Income standard from Expanded Refused Cash Income Limits"
    source: "Addendum 1 (ADLTC)"
    table_lookup:
      table: expanded_refused_cash_income_limits  # must exist in tables:
      key: [household_type, benefit_year]         # column names resolved by name match
      # value: income_limit                       # omit when table has 1 value column
```

**Key resolution order:** computed field name (bare) → entity field (`Entity.field`). Ambiguous or missing names fail at validation.

**When to use:** Prefer `table_lookup` over `expr: "table(...).col"` for AI-extracted rulesets — it's structured, validator-checked, and more readable.

---

## `DecisionField` (CIVIL v5)

| Field | Required | Notes |
|-------|----------|-------|
| `type` | ✅ | e.g. `bool`, `enum`, `list`, `money`, `string` |
| `default` | — | Default value when no rules fire (e.g. `false`, `[]`, `"approve"`) |
| `description` | — | Human-readable description |
| `item` | — | Item type for `list`/`set` decisions (e.g. `Reason`) |
| `values` | — | List of allowed strings for `enum` decisions, e.g. `[approve, deny]`. Required when `type: enum`. |
| `expr` | ✅ for non-list/non-set | CIVIL expression. Mutually exclusive with `conditional`. |
| `conditional` | ✅ for non-list/non-set | If/then/else branch. Mutually exclusive with `expr`. |

> ⚠️ **`bool`, `money`, `string`, `int`, `enum`, and other scalar decision fields require `expr:` or `conditional:`.**
> `list`/`set` decision fields are rule-driven and do not accept `expr:` or `conditional:`.

**Canonical template (bool):**
```yaml
decisions:
  eligible:
    type: bool
    default: false
    description: "..."
    expr: "count(reasons) == 0"      # explicit; required for bool
  reasons:
    type: list
    item: Reason
    default: []
    description: "..."
  # Computation-output module — primary numeric result:
  adjusted_income:
    type: money
    currency: USD
    description: "..."
    expr: "step_n - exclusion_a - exclusion_b"
```

**Canonical template (enum — 3-way outcome):**

Use `type: string` with `values:` for enum decisions. For 3-way outcomes, nest the second condition in the `else:` branch as an inline expression.

```yaml
decisions:
  eligible:
    type: string
    values: [approve, deny, manual_verification]
    default: "approve"
    description: "..."
    conditional:
      if: "count(reasons) > 0"
      then: "\"deny\""
      else: "if manual_verification_required then \"manual_verification\" else \"approve\""
```

> **Note:** `type: enum` is also accepted by the schema and requires a non-empty `values:` list. The canonical form for extraction is `type: string` with `values:` — the extraction pipeline always emits `string`, not `enum`.

---

## `TableDef`

| Field | Required | Notes |
|-------|----------|-------|
| `description` | — | Human-readable description |
| `source` | — | Policy document location, e.g. `"7 CFR § 273.9(a)(1) — Gross Income Limits Table"` |
| `key` | ✅ | List of key column name(s), e.g. `[household_size]` |
| `value` | ✅ | List of value column name(s), e.g. `[max_gross_monthly]` |
| `rows` | ✅ | List of row dicts, e.g. `[{household_size: 1, max_gross_monthly: 1580}]` |

Table reference in expressions: `table('table_name', key_expr).value_column`

---

## `Rule`

| Field | Required | Notes |
|-------|----------|-------|
| `id` | ✅ | Unique. Recommended: `<JURISDICTION>-<TOPIC>-<KIND>-<SEQ>` |
| `kind` | ✅ | `deny` or `allow` |
| `priority` | ✅ | Int; lower = higher priority. Allow rules typically 100+ |
| `when` | ✅ | Boolean CIVIL expression |
| `then` | ✅ | List of `Action` objects — **must be non-empty** |
| `description` | — | Human-readable description |
| `source` | — | Policy document location, e.g. `"7 CFR § 273.9(a)(1) — Gross Income Test"` |
| `review` | — | `ReviewBlock` with extraction quality scores |
| `group` | — | (CIVIL v6) Workflow stage name, e.g. `"income_test"`. Must match a name in `rule_set.workflow_stages` when that list is non-empty. **Transpiler no-op.** |
| `mutex_group` | — | (CIVIL v6) Mutual-exclusion group name. Rules sharing a `mutex_group` are competing alternatives; all must have unique `priority` values. **Transpiler no-op.** |

> ⚠️ **`then:` must be non-empty for all rules**, including allow rules.

> ⚠️ **Transpiler ignores allow rules.** Only `deny` rules generate Rego. `then:` on allow rules is documentary only.

> ⚠️ **Rule `id` values must be unique** across the entire `rules:` list.

> ℹ️ **`group:` and `mutex_group:` are maintainability annotations only** — analogous to `review:`. They have no effect on transpilation or rule evaluation. The validator checks group name consistency (when `workflow_stages:` is defined) and priority uniqueness within a `mutex_group`.

**CIVIL v6 example:**
```yaml
rule_set:
  name: snap_eligibility
  precedence: deny_overrides_allow
  workflow_stages:
    - name: income_test
      description: Gross and net income eligibility tests
    - name: asset_test
      description: Countable asset limit check

rules:
  - id: SNAP-INCOME-DENY-001
    kind: deny
    priority: 10
    group: income_test          # must match a workflow_stages name
    mutex_group: gross_test     # only one rule in this group should fire
    when: gross_income > gross_limit
    then:
      - add_reason:
          code: GROSS_INCOME_EXCEEDS_LIMIT
          message: "Gross income exceeds the 130% FPL limit."
```

---

## `RuleSet`

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | Rule set identifier |
| `precedence` | — | `deny_overrides_allow`, `allow_overrides_deny`, `first_match`, or `priority_order` |
| `description` | — | Human-readable description |
| `workflow_stages` | — | (CIVIL v6) List of `WorkflowStage` objects defining named evaluation phases. When non-empty, `rule.group:` values are validated against these names. |

## `WorkflowStage` (CIVIL v6)

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | Stage identifier (snake_case), e.g. `income_test` |
| `description` | — | One-sentence description of this evaluation phase |

Populated by `/refine-guidance` Step 3 and auto-copied into the CIVIL file by `/extract-ruleset`.

---

## `Jurisdiction`

| Field | Required | Notes |
|-------|----------|-------|
| `level` | ✅ | `federal`, `state`, `county`, or `city` |
| `country` | ✅ | ISO country code, e.g. `US` |
| `state` | — | State/province code, e.g. `AK` |
| `county` | — | County name |
| `city` | — | City name |

> ⚠️ **`country:` is required**, even for state-level programs.

---

## `Effective`

| Field | Required | Notes |
|-------|----------|-------|
| `start` | ✅ | Effective start date, e.g. `2026-01-01` |
| `end` | — | Effective end date (optional for open-ended policies) |

---

## Valid Enum Values

| Field | Valid values |
|-------|-------------|
| `FactField.type` | `int` `float` `bool` `string` `date` `money` `list` `set` `enum` |
| `ComputedField.type` | `money` `bool` `float` `int` `object` (invoke: only) |
| `Rule.kind` | `deny` `allow` |
| `Jurisdiction.level` | `federal` `state` `county` `city` |
| `RuleSet.precedence` | `deny_overrides_allow` `allow_overrides_deny` `first_match` `priority_order` |

---

## Transpiler Behavior

The `transpile_to_catala.py` and `transpile_to_rego.py` transpilers generate Catala and Rego respectively from a CIVIL module. Key behaviors:

| CIVIL construct | Catala output | Rego output |
|----------------|---------------|-------------|
| `tables:` | One `definition field under condition key = row_key consequence equals row_value` block per row | Object literal lookup dict |
| `computed:` fields with `expr:` | `definition field equals expr` | Rego derived rule: `field := expr` |
| `computed:` fields with `conditional:` | `definition field under condition if_expr consequence equals then_expr` + default | Rego: `field := then if { if_expr } else := else_expr` |
| `rules:` with `kind: deny` | `definition decision under condition when_expr consequence equals Ineligible` | `reasons contains reason if { ... }` |
| `rules:` with `kind: allow` | **Nothing** — allow rules are not transpiled | **Nothing** — allow rules are not transpiled |
| `decisions:` with `type: bool` + `expr:` | `definition decision under condition expr consequence equals Eligible` + default `Ineligible` | `default eligible := false` + `eligible if { <expr> }` |
| `decisions:` with `type: list` | Reason codes emitted as enum variants | `reasons` set comprehension |
| `computed:` fields | `internal` scope variable (or `output` if tagged) | Included in `decision.computed` object |

The structured output object is always at `decision` (e.g., query `/v1/data/<pkg>/decision`).

---

## Common Gotchas

1. **`FactField` has no `default:`** — use `optional: true`; defaults are input-level concerns
2. **`string` not `str`** — fact field type for strings is `string`
3. **`ComputedField.type` cannot be `string`** — only `money`, `bool`, `float`, `int`
4. **`jurisdiction.country` is required** — don't omit it for state-level programs
5. **`then:` must be non-empty** — every rule needs at least one action
6. **`Conditional` needs all three branches** — `if`, `then`, and `else` are all required
7. **Allow rules aren't transpiled** — only `deny` rules produce Rego output; allow rules are documentary

---

## Authoring Tooling Schemas

The following YAML schemas are used by `/refine-guidance`, `/extract-ruleset`, and `/update-ruleset`. They are not part of the CIVIL DSL itself.

### `guidance.yaml` — `sub_rulesets:` key

Place after `edge_cases:`, before `example_rules:`. Populated by `/refine-guidance` Step 3 sub-ruleset-candidate detection. A sub-ruleset becomes a CIVIL sub-module.

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | Resolves to `$DOMAINS_DIR/<domain>/specs/<name>.civil.yaml` |
| `description` | ✅ | Human-readable purpose of the sub-module |
| `bound_entities` | ✅ | List of parent entity names (from the main module's `facts:`) that map to the sub-module's primary entity |
| `rationale` | ✅ | One of: `reuse_across_entities`, `depth_threshold`, `policy_structure`, `user_hint` |

```yaml
sub_rulesets:
  - name: earned_income               # resolves to $DOMAINS_DIR/<domain>/specs/earned_income.civil.yaml
    description: "Compute net earned income after deductions"
    bound_entities:                   # parent entity names that will bind to this sub-module
      - ClientData
      - DOLRecord
    rationale: "reuse_across_entities"  # one of: reuse_across_entities | depth_threshold | policy_structure | user_hint
```

**Heuristics that populate `rationale:`:**

| Rationale | Detection rule |
|-----------|---------------|
| `reuse_across_entities` | 2+ `bound_entities` candidates with same computed-variable naming prefix |
| `policy_structure` | Named sub-section in `input-index.yaml` headings or policy text covers ≥3 intermediate variables |
| `depth_threshold` | ≥5 variable names in skeleton whose names suggest sequential dependence (e.g., `after_*` chain, `net_*` ← `gross_*` ← `total_*`) |
| `user_hint` | `sub_rulesets:` already populated in `guidance.yaml` (UPDATE mode only) |

---

### `extraction-manifest.yaml` — multi-file format

When `sub_rulesets:` is non-empty, the manifest includes a `sub_modules:` list nested under the program block. The `programs:` key structure is unchanged — existing single-file consumers are unaffected.

```yaml
# Auto-generated by /extract-ruleset — do not edit manually
programs:
  eligibility:
    civil_file: $DOMAINS_DIR/snap/specs/eligibility.civil.yaml
    extracted_at: "2026-03-18T10:22:00Z"
    source_docs:
      - { path: "input/snap_policy.md", git_sha: "abc123" }
    sub_modules:
      - name: earned_income
        civil_file: $DOMAINS_DIR/snap/specs/earned_income.civil.yaml
        extracted_at: "2026-03-18T10:18:00Z"
        source_docs:
          - { path: "input/snap_policy.md", git_sha: "abc123" }
        referenced: false    # true if user chose "reference as-is" on existing file
```

> **`referenced: false`** means the file was generated by `/extract-ruleset`. **`referenced: true`** means the user chose "reference as-is" and the file was not regenerated during this extraction run.
