# Catala 1.1.0 — Transpiler Output Quick Reference

<!-- Verified against: https://github.com/CatalaLang/catala/blob/master/doc/syntax/syntax_en.catala_en -->
<!-- Catala 1.1.0 "bac d'Eloka" (released 2026-01-29) -->

Reference for reading and generating `.catala_en` files from `tools/transpile_to_catala.py`.
For CIVIL YAML authoring see [civil-quickref.md](civil-quickref.md).

To verify Catala file: `clerk typecheck <catala_file>`
To run file: `clerk run <catala_file> --scope=<scope>`

---

## File Format

Catala files are **literate programs** — Markdown with fenced `catala` code blocks.

**The very first line must be a module directive** with the module name capitalized (first letter uppercase, underscores preserved):

```
> Module Earned_income
```

Module name rules: last segment of the dotted CIVIL `module` field, first letter uppercased. Example: `earned_income` → `Earned_income`. This must match the `modules` entry in `clerk.toml`.

Full file structure:

```
> Module Earned_income

# Earned_income

Prose text (legal citations, explanations, etc.)

```catala
scope EligibilityDecision:
  definition eligible equals true
```

More prose.
```

Extension: `.catala_en` (English keyword set).

**Test files and files that import a module** use `> Using` instead of `> Module`:

```
> Using Earned_income
```

This makes all public declarations of `Earned_income` accessible. Scope references must be
module-qualified: `Earned_income.ScopeName`. See [catala-test-quickref.md](catala-test-quickref.md)
for test file patterns.

---

## Primitive Types and Literals

| Type | Literal syntax | Notes |
|---|---|---|
| `boolean` | `true` / `false` | |
| `integer` | `65536` | Unbounded exact integer |
| `decimal` | `65536.262144` or `37%` | Arbitrary-precision rational; `%` = `/100` |
| `money` | `$1,234,567.89` | Cent-precise (rounds on `*`) |
| `date` | `\|2024-04-01\|` | ISO 8601 inside pipes |
| `duration` | `254 day + -4 month + 1 year` | |
| `optional of T` | `Present content $34` / `Absent` | Optional wrapper |
| `list of T` | `[ 12; 24; 36 ]` | Semicolon-separated |
| `(T1, T2, T3)` | `(\|2024-04-01\|, $30, 1%)` | Tuple |

Type conversions: `decimal of 44`, `money of 23.15`, `round of $9.99`

---

## Top-Level Declarations

### Structure (maps to CIVIL `inputs:` entity)

```catala
declaration structure Household:
  data household_size content integer
  data gross_monthly_income content money
  data has_elderly_member content boolean
```

- Structure names: **PascalCase**; field names: **snake_case**

### Enumeration (maps to CIVIL `string` fields and `reasons` codes)

```catala
declaration enumeration ReasonCode:
  -- GrossIncomeExceedsLimit
  -- NetIncomeExceedsLimit

declaration enumeration StatusType:
  -- Active
  -- Inactive content integer   # variant with payload
```

### Constant (maps to CIVIL `constants:`)

```catala
declaration EARNED_INCOME_DEDUCTION_RATE content decimal equals 20%
```

### Function (top-level pure function)

```catala
declaration square content decimal
  depends on x content decimal
  equals x * x

declaration f content decimal
  depends on x content money, y content decimal
  equals y * x / $12.0
```

Call with: `f of $44.50, 1/3`

---

## Scope Declaration (maps to CIVIL module)

```catala
declaration scope EligibilityDecision:
  input Household content Household         # provided by caller, cannot redefine
  input Applicant content Applicant
  internal gross_limit content money        # computed inside, not returned
  internal rule_1_triggered condition       # boolean condition variable
  output eligible content boolean           # returned to caller
  output reasons content list of ReasonCode
  context extra content integer             # caller-overridable with a default
  input output combined content boolean     # both input and output
  sub1 scope SubScopeName                   # sub-scope call (not output)
  output sub2 scope SubScopeName            # sub-scope call (output)
```

Variable kinds:
- **`input`**: caller-supplied; cannot be redefined in body
- **`internal`**: computed locally; not returned
- **`output`**: computed locally; returned to caller
- **`context`**: has a default definition but caller can override
- **`condition`**: boolean variable (use `rule ... consequence fulfilled/not fulfilled`)

---

## Scope Definitions

### Simple definition

```catala
scope EligibilityDecision:
  definition eligible equals true
```

### Scope-level condition (applies to all definitions below)

```catala
scope EligibilityDecision
  under condition Household.household_size >= 1:

  definition gross_limit equals $1,696
```

### Conditional definition (`under condition` + `consequence`)

```catala
scope EligibilityDecision:
  definition gross_limit
    under condition Household.household_size = 1
    consequence equals $1,696

  definition gross_limit
    under condition Household.household_size = 2
    consequence equals $2,292
```

> ⚠️ Multiple definitions for the same variable need exhaustive guards or a base case.

### Conditional definition (if/then/else — maps to CIVIL `conditional:`)

```catala
scope EligibilityDecision:
  definition shelter_deduction equals
    if is_exempt_household then shelter_excess
    else (if shelter_excess < SHELTER_DEDUCTION_CAP
          then shelter_excess
          else SHELTER_DEDUCTION_CAP)
```

### Boolean rule (`condition` variable type)

```catala
scope EligibilityDecision:
  rule rule_1_triggered
    under condition
      not Household.has_elderly_member and
      Household.gross_monthly_income > gross_limit
    consequence fulfilled

  rule rule_1_triggered under condition false
    consequence not fulfilled
```

Use `rule`/`fulfilled`/`not fulfilled` for `condition`-typed variables (not `definition`/`equals`).

### Labeled definition + exception (maps to CIVIL deny rules on `eligible`)

```catala
scope EligibilityDecision:
  label base_eligible
  definition eligible equals true

scope EligibilityDecision:
  exception base_eligible
  definition eligible
    under condition rule_1_triggered
    consequence equals false

scope EligibilityDecision:
  exception base_eligible
  definition eligible
    under condition rule_2_triggered
    consequence equals false
```

- Each deny rule is an independent `exception base_eligible`
- Exception without label: `exception definition var1 equals 0` (when only one base case)
- Multiple exceptions to the same label are **independent** — each fires on its own condition

### Function definition in scope

```catala
scope EligibilityDecision:
  definition f of x, y equals x + y
```

### Assertion

```catala
scope EligibilityDecision:
  assertion Household.household_size > 0
```

### State transitions

```catala
scope EligibilityDecision:
  definition income state before equals Household.gross_monthly_income
  definition income state after equals income state before - deductions
```

---

## Expressions

### let...in binding

```catala
let x equals 36 - 5 in
let y equals x * 2 in
y + 1
```

### if/then/else

```catala
if condition then value_a else value_b
```

### Pattern matching (enumeration)

```catala
match date_of_death with pattern
  -- StillAlive: false
  -- Deceased content d: d < processing_date
```

Wildcard arm: `-- anything: default_value`

### Pattern test (boolean check)

```catala
expr with pattern Case1                          # true if Case1
expr with pattern Case1 content x and x >= 2   # true if Case1 and condition holds
```

### Structure construction and update

```catala
Household { -- household_size: 4 -- gross_monthly_income: $2,000 }

entry but replace { -- gross_monthly_income: $1,500 }
```

### Field access

```catala
Household.household_size    # struct field
tuple1.2                    # tuple element (1-indexed)
sub1.var0                   # sub-scope output
```

### Scope call

```catala
output of SubScope with {
  -- input_field: value
  -- another_field: 42
}
```

---

## List Operations

### Filter

```catala
list of x among lst such that x > 2
```

### Map

```catala
map each x among lst to x + 2
```

### Map with filter (combined)

```catala
map each x among lst such that x > 2 to x - 2
```

### Zip two lists (map over pairs)

```catala
map each (x, y) among (lst1, lst2) to x + y
```

### Sum

```catala
sum integer of lst
sum money of lst
sum decimal of lst
```

### Count

```catala
number of lst
```

### Maximum / Minimum

```catala
maximum of lst or if list empty then -1

content of x among lst such that x * x is minimum or if list empty then -1
```

### Membership

```catala
lst contains 3
```

### Quantifiers

```catala
exists x among lst such that x > 2
for all x among lst we have x > 2
```

### Concatenate

```catala
lst1 ++ lst2
```

### Fold / Combine

```catala
combine all x among lst in acc initially 0 with acc + x
```

### reasons pattern (CIVIL deny rules → list of codes)

```catala
declaration structure ReasonEntry:
  data triggered content boolean
  data code content ReasonCode

scope EligibilityDecision:
  internal all_reason_entries content list of ReasonEntry

  definition all_reason_entries equals
    [ ReasonEntry { -- triggered: rule_1_triggered -- code: GrossIncomeExceedsLimit } ;
      ReasonEntry { -- triggered: rule_2_triggered -- code: NetIncomeExceedsLimit } ]

  definition reasons equals
    map each entry among
      (list of e among all_reason_entries such that e.triggered)
    to entry.code
```

---

## CIVIL → Catala Type Mapping

| CIVIL type | Catala type | Notes |
|---|---|---|
| `int` | `integer` | |
| `float` | `decimal` | |
| `bool` | `boolean` | Use `condition` variable kind for rule flags |
| `money` | `money` | |
| `date` | `date` | |
| `string` | `enumeration` | Emit enum with variants from known string values |
| `list` | `list of <type>` | |

---

## CIVIL → Catala Operator Mapping

| CIVIL expr | Catala expr | Notes |
|---|---|---|
| `a && b` | `a and b` | Stays in one expression (no splitting unlike Rego) |
| `a \|\| b` | `a or b` | Stays inline (no OR-splitting needed) |
| `!a` | `not a` | |
| `a == b` | `a = b` | Single `=` for equality — `==` is a syntax error |
| `a != b` | `a != b` | |
| `Entity.field` | `Entity.field` | Same dot notation |
| `max(a, b)` | `if a >= b then a else b` | No built-in max; use conditional |
| `min(a, b)` | `if a <= b then a else b` | No built-in min; use conditional |
| `CONSTANT` | Inlined literal | Constants substituted at transpile time |
| `table('name', key).col` | Stacked `under condition` definitions (default) or single `if/else if/else` chain (`--table-style else-if`) | One block per row, or one chained definition |

Typed arithmetic (when mixed types need explicit precision):
- `+!` integer, `+.` decimal, `+$` money, `+^` duration

---

## Common Gotchas

1. **`=` not `==`** — equality is single `=`; `==` is a syntax error
2. **Filter syntax** — `list of x among lst such that x > 2` (not `filter`)
3. **Sum syntax** — `sum integer of lst` (not `integer sum of`)
4. **`condition` variables use `rule`/`fulfilled`** — not `definition`/`equals`
5. **Stacked `under condition` defs need exhaustive coverage** — runtime error if no case matches and no base case
6. **Exception must reference an existing label** — label must appear on another definition in the same scope
7. **Money × decimal only** — `money * integer` is a type error; cast with `decimal of integer`
8. **Structure literal** — `StructName { -- field: value -- field2: value2 }` (double-dash, no commas)
9. **Scope call** — `output of ScopeName with { -- input: value }` not a function call syntax
10. **Multiple exceptions to same label are independent** — all fire independently; each can set `eligible` to `false`
11. **`under condition` + `consequence` both required** — for conditional definition form; cannot omit either keyword
12. **List semicolons** — list elements separated by `;` not `,`: `[ a; b; c ]`
13. **`> Module` must be the first line** — before any Markdown heading; module name must be capitalized (first letter uppercase, underscores preserved, e.g. `Earned_income` not `earned_income`)
