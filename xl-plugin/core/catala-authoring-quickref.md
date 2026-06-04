# Catala 1.1.0 — AI Authoring Quick Reference

<!-- Aimed at AI consumers (Claude). Not a tutorial. -->
<!-- Verified against: https://github.com/CatalaLang/catala/blob/master/doc/syntax/syntax_en.catala_en -->
<!-- Catala 1.1.0 "bac d'Eloka" (released 2026-01-29) -->

This reference is for AI agents emitting `.catala_en` source for the lockpicks
xlator pipeline. It is **structured as grammar excerpts + minimal examples +
failure modes to preempt**, not a tutorial. For general Catala feature lookup
see [catala-quickref.md](catala-quickref.md). For test files see
[catala-test-quickref.md](catala-test-quickref.md).

The body is divided into three parts:

1. **R9 feature reference** — every Catala feature the authoring skills emit
2. **AI failure modes to preempt** — six categories distilled from
   [proposed_transpilation_fixes_snapshot.md](proposed_transpilation_fixes_snapshot.md)
3. **Project idioms** — denial_reasons, citation form, fence discipline,
   `#[error.message]`, clerk-bootstrap hygiene

---

## Part 1 — R9 Feature Reference

### File preamble and module name

**Grammar:**

```
> Module <ModuleName>
```

- The `> Module` directive MUST be the first non-blank line of the file.
- `<ModuleName>` MUST equal the CamelCase of the filename basename, underscores preserved.
- File extension is `.catala_en`.

**Example.** A file named `eligibility.catala_en` begins:

```
> Module Eligibility

# Eligibility
```

A file named `earned_income.catala_en` begins:

```
> Module Earned_income
```

Importing a module from a test file or another module uses `> Using`:

```
> Using Eligibility
```

> **AI trap:** Confabulating the module name (e.g. `> Module Eligibility_program` in
> `eligibility.catala_en`) typechecks-fails on the first run — `clerk` resolves
> module references by filename. **Always derive `<ModuleName>` mechanically
> from the filename.** (PA3 lesson #1.)

---

### Scopes (declaration + definitions)

**Grammar (declaration):**

```
declaration scope <ScopeName>:
  input <var> content <Type>
  internal <var> content <Type>
  internal <var> condition
  output <var> content <Type>
  context <var> content <Type>
  input output <var> content <Type>
  <subvar> scope <SubScopeName>
  output <subvar> scope <SubScopeName>
```

Variable kinds:

| Kind | Role |
|---|---|
| `input` | Provided by caller; cannot be redefined in body |
| `internal` | Computed locally; not returned |
| `output` | Computed locally; returned to caller |
| `context` | Has a default definition; caller can override |
| `condition` | Boolean variable; use `rule … fulfilled` (not `definition … equals`) |

**Example.**

```catala
declaration scope EligibilityDecision:
  input household content Household
  internal income_test_passes condition
  output is_eligible condition
  output reasons content list of ReasonCode
```

Definitions live in a `scope <Name>:` block, which may appear multiple times in
the file:

```catala
scope EligibilityDecision:
  definition reasons equals []
```

---

### Contextual definitions (`under condition` + `consequence`)

**Grammar:**

```
definition <var>
  under condition <bool_expr>
  consequence equals <expr>
```

Multiple `under condition` definitions for the same variable are stacked. They
must be **exhaustive** — at runtime, exactly one must fire, or the program errors.

**Example.**

```catala
scope EligibilityDecision:
  definition gross_limit
    under condition household.size = 1
    consequence equals $1,696

  definition gross_limit
    under condition household.size = 2
    consequence equals $2,292

  definition gross_limit
    under condition household.size > 2
    consequence equals $2,292 + (decimal of (household.size - 2)) * $448
```

Scope-level conditions hoist a guard onto every `definition` in the block:

```catala
scope EligibilityDecision
  under condition household.size >= 1:

  definition gross_limit equals $1,696
```

---

### Conditional definition (if/then/else expression)

**Grammar:**

```
if <bool_expr> then <expr> else <expr>
```

**Example.**

```catala
scope EligibilityDecision:
  definition shelter_deduction equals
    if is_exempt_household then shelter_excess
    else (if shelter_excess < SHELTER_DEDUCTION_CAP
          then shelter_excess
          else SHELTER_DEDUCTION_CAP)
```

Prefer `under condition` for rule-shaped logic (multiple cases, exhaustive,
each case tied to a policy paragraph). Prefer `if/then/else` for inline
expression-level branching.

---

### Boolean rules on `condition` variables

**Grammar:**

```
rule <condition_var>
  under condition <bool_expr>
  consequence fulfilled
rule <condition_var> under condition <bool_expr>
  consequence not fulfilled
```

**Example.**

```catala
scope EligibilityDecision:
  rule income_test_passes
    under condition household.monthly_gross_income < federal_poverty_line * 200%
    consequence fulfilled
```

A `condition` variable defaults to `not fulfilled` if no rule fires; you do not
need a fallback rule unless you want to document the false case.

---

### Exceptions with priorities (labeled definitions)

**Grammar:**

```
label <label_name>
definition <var> equals <expr>

exception <label_name>
definition <var>
  under condition <bool_expr>
  consequence equals <expr>
```

Multiple `exception <same_label>` blocks are **independent** — each fires on
its own condition, and any firing exception wins over the base definition.
This is the foundation for deny-rule accumulation (see Part 3, denial_reasons).

**Example.**

```catala
scope EligibilityDecision:
  label base_eligible
  definition is_eligible equals true

scope EligibilityDecision:
  exception base_eligible
  definition is_eligible
    under condition gross_income_exceeds_limit
    consequence equals false

scope EligibilityDecision:
  exception base_eligible
  definition is_eligible
    under condition resource_disqualification
    consequence equals false
```

When there is exactly one base definition, the label can be elided on the
exception side: `exception definition is_eligible …` is valid shorthand.

---

### Modules (declaration + import)

**Grammar (in the importing file):**

```
> Using <SubModuleName>
```

**Grammar (in a scope that calls a sub-scope):**

```
declaration scope <Importer>:
  <subvar> scope <SubModuleName>.<SubScopeName>
```

Or with the sub-scope's outputs exposed:

```
  output <subvar> scope <SubModuleName>.<SubScopeName>
```

Sub-scope outputs are accessed by field: `<subvar>.<output_field>`.

**Example.**

```
> Using Earned_income

declaration scope EligibilityDecision:
  input household content Household
  earned scope Earned_income.EarnedIncome
  output is_eligible condition
```

Cross-module type references in declarations must be **qualified**:
`<SubModule>.<TypeName>` (e.g. `input household_type content Earned_income.HouseholdType`).
See Part 2, failure mode "enum qualification".

---

### Comprehensions (list operations)

**Grammar:**

```
list of <x> among <list_expr> such that <bool_expr>         # filter
map each <x> among <list_expr> to <expr>                    # map
map each <x> among <list_expr> such that <bool_expr> to <expr>   # map + filter
map each (<x>, <y>) among (<lst1>, <lst2>) to <expr>        # zip
sum integer of <list_expr>                                  # sum (type required)
sum money of <list_expr>
sum decimal of <list_expr>
number of <list_expr>                                       # count
maximum of <list_expr> or if list empty then <default>
content of <x> among <list_expr> such that <expr> is minimum or if list empty then <default>
<list_expr> contains <value>                                # membership
exists <x> among <list_expr> such that <bool_expr>          # quantifier
for all <x> among <list_expr> we have <bool_expr>           # quantifier
<lst1> ++ <lst2>                                            # concat
combine all <x> among <list_expr> in <acc> initially <init> with <expr>   # fold
```

List literals use **semicolons**, not commas: `[ a; b; c ]`.

**Example.**

```catala
definition deduction_total equals
  sum money of (map each entry among deduction_entries to entry.amount)

definition any_entry_triggered equals
  exists e among reason_entries such that e.triggered
```

---

### Conditional definitions (full form)

Already covered above under "Contextual definitions" and "Conditional definition".
The two patterns:

- **`under condition` + `consequence equals`** — for rule-shaped logic where each
  branch is policy-anchored
- **`if … then … else …`** — for inline expression branching

They compose: an `under condition` definition's RHS may itself be an `if/then/else`.

---

### Pattern matching on enumerations

**Grammar (declaration):**

```
declaration enumeration <EnumName>:
  -- <Variant1>
  -- <Variant2> content <Type>
```

**Grammar (matching):**

```
match <expr> with pattern
  -- <Variant1>: <expr>
  -- <Variant2> content <bound_name>: <expr>
  -- anything: <default_expr>
```

**Grammar (predicate check):**

```
<expr> with pattern <Variant>
<expr> with pattern <Variant> content <bound_name> and <bound_name> >= 2
```

**Example.**

```catala
declaration enumeration HouseholdType:
  -- Standard
  -- Elderly
  -- Disabled

scope EligibilityDecision:
  definition resource_limit equals
    match household.type with pattern
      -- Standard: $2,750
      -- Elderly: $4,250
      -- Disabled: $4,250
```

Variant names are **PascalCase**. See Part 2, "enum qualification".

---

### State transitions (`state before` / `state after`)

**Grammar:**

```
definition <var> state <state_name> equals <expr>
```

State names are author-chosen. The runtime threads each state forward in
declaration order; `<var> state <name>` refers to the value at that state.

**Example.**

```catala
scope EligibilityDecision:
  definition income state before equals household.gross_monthly_income
  definition income state after equals income state before - deductions
```

Used sparingly; most policy rules do not need explicit state. Reach for it when
the same variable participates in a "before deduction / after deduction" pair.

---

### Literate-program structure

A `.catala_en` file is **literate Markdown**. Catala compiles only the contents
of fenced code blocks; everything else is prose that survives compilation as
`SourcePosition.law_headings` runtime metadata (see
`xl-plugin/core/catala/python/catala_runtime.py` lines 31-45).

**Required shape:**

```
> Module <ModuleName>

# <Module title>

## <Section heading>

Prose paragraph.

```catala-metadata
declaration scope … :
  …
```

## <Another section>

*Source: <policy_doc_path> — <section_path>*

```catala
scope …:
  definition … equals …
```

More prose.
```

- Module-level title is `#`, sections are `##`, sub-sections `###`.
- Section headings **mirror** the layout in
  `policy_facets/computations/<rel>.md.yaml` so policy ↔ rule traceability
  survives compilation as `law_headings`.
- See Part 3, "Source-doc citation form" for the `*Source: ...*` line convention.

---

## Part 2 — AI Failure Modes to Preempt

These six categories are distilled from
[proposed_transpilation_fixes_snapshot.md](proposed_transpilation_fixes_snapshot.md)
sections 3.I–3.VI and section 2D. Each subsection gives one wrong example and
one corrective example. Memorize the corrective patterns.

### 2.1 — Cross-module type contracts

**Symptom:** Importer declares an input field with a primitive type, but the
sub-module exports it as a qualified enum or compound type. `clerk typecheck`
fails with `incompatible types: integer vs Module.X`.

**Wrong:**

```catala
> Using Program_standards_lookup

declaration scope EligibilityDecision:
  internal household_type content integer  # WRONG — sub-module exports an enum
```

**Right:**

```catala
> Using Program_standards_lookup

declaration scope EligibilityDecision:
  internal household_type content Program_standards_lookup.HouseholdType
```

**Rule.** When a field flows from a sub-scope into the importer, **read the
sub-module's exported type from its `catala-metadata` block** and use the
fully-qualified form `<SubModule>.<TypeName>` in the importer's declaration.
The naming-manifest carries types per-field (post-U7); use it as the authority.

---

### 2.2 — Enum qualification

**Symptom:** A variant name appears in an expression or test without its
module/enum-name prefix. Catala fails to resolve the bare name when the enum
lives in another module, or when the same variant name exists in multiple enums.

**Wrong:**

```catala
definition reasons equals [ GrossIncomeExceedsLimit ]   # AMBIGUOUS if used cross-module
```

**Right (same-module):**

```catala
definition reasons equals [ GrossIncomeExceedsLimit ]   # OK when ReasonCode is local
```

**Right (cross-module):**

```catala
definition household_type equals Program_standards_lookup.HouseholdType.Standard
```

**Rule.** When referencing a variant **declared in another module**, qualify
fully: `<Module>.<EnumName>.<Variant>`. When the enum is declared in the same
file, the bare variant is sufficient. Variant names are **PascalCase**; the
declaration uses `-- VariantName` (capital first letter), and references match
that exact casing.

---

### 2.3 — Money and date literals

**Symptom:** Bare numbers used in money context, or date strings used instead
of pipe-delimited dates.

**Wrong:**

```catala
definition threshold equals 1696             # WRONG — bare integer in money context
definition cutoff equals "2024-04-01"        # WRONG — string, not date
definition rate equals 0.20                  # WRONG-ish — emits decimal but prefer % form
```

**Right:**

```catala
definition threshold equals $1,696            # money literal: `$` + comma-grouped digits
definition cutoff equals |2024-04-01|         # date literal: ISO 8601 inside pipes
definition rate equals 20%                    # decimal-as-percent: `%` = `/100`
```

**Rules.**

- Money: `$` followed by integer or fractional cents; commas allowed as
  thousands separators. `$1,234.56` is valid; `$1234` is valid; `1234` is not.
- Date: pipe-delimited ISO 8601 — `|YYYY-MM-DD|`. Quoted strings are not dates.
- Decimal: bare numeric (`0.20`) or percent form (`20%`). The percent form is
  preferred for rates because it survives round-tripping cleanly.
- A bare integer where money is expected is a Catala type error, not a coerced
  literal — there is no implicit `integer → money` conversion.

---

### 2.4 — List operators

**Symptom:** Use of comma list separators, missing `of <type>` on `sum`, or
attempting `in(x, [...])` instead of `contains`.

**Wrong:**

```catala
definition my_list equals [ 1, 2, 3 ]                # WRONG — list uses `;`
definition total equals sum of incomes               # WRONG — must say `sum money of …`
definition has_value equals in(x, [1; 2; 3])         # WRONG — Catala has no `in(...)`
```

**Right:**

```catala
definition my_list equals [ 1; 2; 3 ]
definition total equals sum money of incomes
definition has_value equals [ 1; 2; 3 ] contains x
```

**Rules.**

- List literals separate elements with `;`, not `,`. Tuples use `,`.
- `sum` REQUIRES an element-type keyword: `sum integer of`, `sum money of`,
  `sum decimal of`. The type must match the element type of the list.
- Membership test is `<list> contains <value>` — argument order is
  list-first. There is no `in(...)` builtin.
- Filter is `list of x among lst such that <bool>`, not `filter(...)`.

---

### 2.5 — Struct mode detection (input shape)

**Symptom:** Inputs are declared individually when they should belong to a
struct, or vice versa. Test files fail to assign inputs correctly.

**When to use flat individual inputs:**

```catala
declaration scope EligibilityDecision:
  input household_size content integer
  input gross_monthly_income content money
  input has_elderly_member content boolean
```

**When to use a struct input:**

```catala
declaration structure Household:
  data household_size content integer
  data gross_monthly_income content money
  data has_elderly_member content boolean

declaration scope EligibilityDecision:
  input household content Household
```

**Rule.** Use a **struct input** when the fields belong to the same entity
(e.g. a household, a person) and that entity is also supplied by a sub-module
via a sub-scope binding. Use **flat individual inputs** when fields are
unrelated or when the scope is leaf-level (no sub-scopes producing the
entity). The naming-manifest indicates entity grouping; follow it.

The corresponding test pattern assigns each input separately
(see [catala-test-quickref.md](catala-test-quickref.md)).

---

### 2.6 — Exception-default for deny rules

**Symptom:** Deny rules expressed as `if/else` chains in a single definition,
which collapse policy structure into procedural code and lose the
policy-paragraph ↔ rule mapping.

**Wrong:**

```catala
scope EligibilityDecision:
  definition is_eligible equals
    if gross_income_exceeds_limit then false
    else if net_income_exceeds_limit then false
    else if resource_disqualification then false
    else true
```

**Right:**

```catala
scope EligibilityDecision:
  label base_eligible
  definition is_eligible equals true

scope EligibilityDecision:
  exception base_eligible
  definition is_eligible
    under condition gross_income_exceeds_limit
    consequence equals false

scope EligibilityDecision:
  exception base_eligible
  definition is_eligible
    under condition net_income_exceeds_limit
    consequence equals false

scope EligibilityDecision:
  exception base_eligible
  definition is_eligible
    under condition resource_disqualification
    consequence equals false
```

**Rule.** Each deny rule is its own `exception base_eligible` block. Multiple
exceptions to the same label fire independently — any one firing denies. This
preserves the 1-to-1 mapping between policy paragraphs and Catala blocks, which
is the load-bearing property for source-doc traceability. Compose with the
denial_reasons idiom in Part 3 to surface which rules fired.

---

## Part 3 — Project Idioms

### 3.1 — denial_reasons accumulation

Catala has no built-in "collect reason codes for failing rules" operator.
Encode it as a boolean flag per rule + a `ReasonEntry` struct + a filter/map
pipeline producing the final `reasons` list.

```catala
declaration enumeration ReasonCode:
  -- GrossIncomeExceedsLimit
  -- NetIncomeExceedsLimit
  -- ResourceDisqualification

declaration structure ReasonEntry:
  data triggered content boolean
  data code content ReasonCode

declaration scope EligibilityDecision:
  internal gross_income_exceeds_limit condition
  internal net_income_exceeds_limit condition
  internal resource_disqualification condition
  internal all_reason_entries content list of ReasonEntry
  output reasons content list of ReasonCode

scope EligibilityDecision:
  definition all_reason_entries equals
    [ ReasonEntry { -- triggered: gross_income_exceeds_limit -- code: GrossIncomeExceedsLimit } ;
      ReasonEntry { -- triggered: net_income_exceeds_limit   -- code: NetIncomeExceedsLimit } ;
      ReasonEntry { -- triggered: resource_disqualification  -- code: ResourceDisqualification } ]

  definition reasons equals
    map each entry among
      (list of e among all_reason_entries such that e.triggered)
    to entry.code
```

**Why this shape:**

- One row per policy rule keeps the table aligned with the policy paragraphs.
- The `triggered` flag is the same boolean a deny-rule `exception` would test
  (Part 2.6), so deny rules and reasons share their predicates.
- Filter-then-map yields a list of just the fired codes, in declaration order.

---

### 3.2 — Source-doc citations (literate Markdown form)

Citations are **structural**, not just prose. The `## Heading` mirrors the
section path in `policy_facets/computations/<rel>.md.yaml`; the
`*Source: ...*` italic line names the source document and section.

```markdown
## Resource disqualification

*Source: input/policy_docs/snap/02_eligibility.md — Section 4. Disqualification*

```catala
scope EligibilityDecision:
  rule resource_disqualification
    under condition household.countable_resources > $5,000
    consequence fulfilled
```
```

**Rules.**

- The `## Heading` text MUST match the section heading from the
  `computations/<rel>.md.yaml` `sections[*].path` value verbatim
  (preserves grep-ability).
- The `*Source: ...*` line uses `*…*` italic Markdown (not bold, not a link).
- Format: `*Source: <input/policy_docs/<rel>.md> — <section_path>*`
  with an em-dash separator.
- Source line is emitted **immediately above** the fenced `catala` block it
  attributes, so `law_headings` carries both into runtime.

---

### 3.3 — Fence discipline: `catala-metadata` vs `catala`

Two fence types compile to different visibility:

| Fence | Visibility | Use for |
|---|---|---|
| `` ```catala-metadata `` | **Public** (exported in `.cmxs`; visible cross-module) | enum decls, struct decls, scope declarations |
| `` ```catala `` | **Private** (compiled but not exported) | rule definitions, sub-scope wiring, internal computations |

**This is a typecheck-passes-but-runtime-fails class of bug** — `clerk typecheck`
sees all declarations through the source AST, but `clerk test` (which links the
compiled `.cmxs`) cannot resolve cross-module references to declarations buried
in a private `catala` fence.

**Rule.** The `## Declarations` section uses `catala-metadata`. Every other
section uses `catala`. If an importing module fails at runtime with
"Could not resolve reference to `<Module>.<Name>`", look for the declaration
inside a `catala` fence and move it into `catala-metadata`.

**Why both fences:** keeping internal computations in `catala` reduces the
exported surface — sub-modules can change implementation without breaking
downstream typecheck.

---

### 3.4 — Catala 1.1.0 `#[error.message]` attribute

Catala 1.1.0 introduced attribute annotations that surface in compiler
diagnostics. `#[error.message]` is accepted **only directly before an
`assertion` or `impossible` block.** Attaching it to a `definition`,
`exception`, or any other construct produces the compiler warning:

```
Attribute #[error.message] is not allowed in this context.
It must be put before an assertion or impossible.
```

The misuse is reported as a warning rather than an error, but the tag itself
is silently dropped — the named label never surfaces in any diagnostic. Do
not author tags on `definition` / `exception` blocks under the assumption
that they will appear later; they will not.

Use the attribute to tag assertion failures with AI-readable labels:

```catala
scope EligibilityDecision:
  #[error.message = "gross_income_check"]
  assertion gross_income <= gross_income_limit
```

When the assertion fails at runtime, `gross_income_check` appears in the
diagnostic. The U2 clerk loop parses these tags and maps them to
repair-history categories for the AI to self-correct.

**Rule.** Add `#[error.message = "<tag>"]` only to `assertion` and
`impossible` blocks. Do not attach to `definition` or `exception`. Tag
values are **snake_case** and **short** (verb_noun or noun_qualifier). They
are not enforced as enums; use a stable set per module.

---

### 3.5 — Clerk bootstrap and dry-run warnings

(Operational notes from the PA3 spike; not Catala-syntax but load-bearing for
the AI authoring loop.)

1. **`clerk start` is a per-project bootstrap.** The Catala stdlib (`_build/libcatala`)
   must exist before `clerk typecheck`. The U2 clerk loop runs `clerk start`
   when it sees a missing `_build` directory; an AI emission that goes
   straight to `clerk typecheck` on a fresh project will fail spuriously.

2. **`clerk test` evaluates scopes during dry-run** and emits benign runtime
   warnings on scopes that lack `#[test]` annotations. Do not chase these as
   errors — the U2 loop classifies them as `runtime_warning`, not
   `runtime_error`. They are informational.

---

## Part 4 — Syntax Confabulation Traps

AI agents trained on functional-language corpora tend to import OCaml, F#, or
Haskell syntax that **looks** correct but is not Catala. Memorize the
distinctions:

| Confabulation | Catala-correct form |
|---|---|
| `let x = e in body` (= sign) | `let x equals e in body` (`equals`, not `=`) |
| `match e with \| C1 -> a \| C2 -> b` | `match e with pattern -- C1: a -- C2: b` |
| `fun x -> e` | (no first-class lambdas; use `declaration … depends on … equals …`) |
| `(a, b)` as a struct | `<StructName> { -- a: … -- b: … }` (structs are nominal, not tuples) |
| `if e then a else b` (newline-sensitive) | `if e then a else b` (whitespace-insensitive, same form) |
| `x == y` for equality | `x = y` (single `=`); `x != y` for inequality |
| `[a, b, c]` for lists | `[a; b; c]` (semicolons) |
| `not x` or `!x` | `not x` only |
| `List.map f lst` | `map each x among lst to f of x` |
| `List.filter p lst` | `list of x among lst such that p of x` |
| `fold` / `List.fold_left` | `combine all x among lst in acc initially <init> with <expr>` |
| `Some x` / `None` | `Present content x` / `Absent` |
| `:` for type annotation in expression | (no inline type annotations; declare at scope level) |
| `let rec` / recursive functions | (recursion not supported in scope definitions) |
| Module `open` | `> Using <Module>` |
| `type t = A \| B` | `declaration enumeration T: -- A -- B` |
| Comments `(* ... *)` | `#` for line comments (no block comment syntax) |

**Rule of thumb:** if a syntactic form would feel natural in OCaml but you have
not seen it in this quickref or in [catala-quickref.md](catala-quickref.md), it
is probably wrong. Search the quickrefs before emitting.

---

## Appendix — Verification commands

When the U2 clerk loop is unavailable (e.g., authoring in isolation):

```bash
# Per-project bootstrap (once per checkout; required before typecheck on fresh projects)
clerk start

# Typecheck (static; misses runtime/fence-visibility bugs)
clerk typecheck <module>.catala_en

# Full pass (typecheck + dry-run; catches fence-visibility bugs)
clerk test

# Multi-error parseable diagnostics (used by U2)
catala typecheck --message-format=gnu --stop-on-error=false <module>.catala_en
```

The U2 clerk loop wraps these. Skill code SHOULD call the U2 library, not
shell out to `clerk` directly.
