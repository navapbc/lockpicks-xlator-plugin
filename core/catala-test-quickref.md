# Catala 1.1.0 — Test Authoring Quick Reference

<!-- Based on: https://book.catala-lang.org/en/3-3-test-ci.html, https://book.catala-lang.org/en/5-6-modules.htm -->
<!-- Catala 1.1.0 "bac d'Eloka" (released 2026-01-29) -->

Reference for authoring and generating Catala test files.
For general Catala syntax see [catala-quickref.md](catala-quickref.md).
For generating tests from YAML: `python tools/transpile_to_catala_tests.py`

To run tests: `clerk test`

---

## Test Scope Pattern (`#[test]`)

The canonical Catala test uses `#[test]` on a scope declaration, declares the
tested scope as a **sub-scope** (`result scope Module.Scope`), assigns each input
field individually, and asserts on outputs:

```catala
#[test]
declaration scope TestAllow001:
  result scope Eligibility.EligibilityDecision

scope TestAllow001:
  definition result.household_size equals 3
  definition result.has_elderly_member equals false
  definition result.gross_monthly_income equals $1,800
  definition result.earned_income equals $0
  definition result.unearned_income equals $1,800
  definition result.shelter_costs_monthly equals $500
  definition result.dependent_care_costs equals $0
  assertion (result.eligible = true)
  assertion (result.reasons = [])
```

Key points:
- `#[test]` goes **directly before** the `declaration scope` line
- `result scope ModuleName.ScopeName` — declares a sub-scope; inputs are set via `definition result.<field>` and outputs are accessed as `result.<field>`
- Inputs are assigned individually: `definition result.<field> equals <value>`
- `assertion (expr)` — parentheses are required around the assertion expression
- Equality in assertions uses `=` (single equals), **not** `==`

---

## Assertion Syntax

```catala
scope TestName:
  assertion (some_bool_field = true)
  assertion (some_money_field = $1,234)
  assertion (some_int_field = 42)
  assertion (result.eligible = false)
```

**List assertions** (e.g., `reasons`):

```catala
  assertion (result.reasons = [])                          # empty list
  assertion (result.reasons = [GrossIncomeExceedsLimit])   # one item
  assertion (result.reasons = [GrossIncomeExceedsLimit; NetIncomeExceedsLimit])  # multiple
```

List items are separated by `;` (semicolons), **not commas**.

---

## Sub-scope Input Syntax

All declared `input` fields of the sub-scope must be assigned in the test scope body:

```catala
scope TestAllow001:
  definition result.household_size equals 3         # integer: bare number
  definition result.has_elderly_member equals false # boolean: true / false
  definition result.gross_monthly_income equals $1,800  # money: $N,NNN
  definition result.household_type equals A1E       # enum: bare variant name (no quotes)
```

Type summary:

| CIVIL type | `definition result.<field> equals` literal |
|---|---|
| `int` | `3` |
| `bool` | `true` / `false` |
| `money` | `$1,800` |
| `enum` | `VariantName` (PascalCase or as declared) |

---

## Module Declarations: `catala-metadata` vs `catala`

In Catala 1.1.0, code fence type controls cross-module visibility at **runtime**:

| Fence | Visibility | Effect |
|---|---|---|
| `` ```catala-metadata `` | **Public** | Scope and types are exported in the compiled `.cmxs`; accessible via `> Using` from other modules |
| `` ```catala `` | **Private** | Compiled into `.cmxs` but NOT exported; invisible cross-module at runtime |

**Rule:** The `## Declarations` section (enums, structs, scope declaration) must use `` ```catala-metadata ``.
All other sections (table lookups, computed values, rules, decision) use `` ```catala `` — they are
implementation details.

**Symptom of missing `catala-metadata`:** `catala typecheck` passes (uses the source AST, which sees
all declarations), but `catala interpret` raises "Could not resolve reference to Module.ScopeName"
(uses the compiled `.cmxs`, which only contains public exports).

The transpiler (`tools/transpile_to_catala.py`) generates `catala-metadata` for declarations
automatically. If regenerating manually, ensure the `## Declarations` fence is `catala-metadata`.

---

## Organizing Test Files

Recommended structure (mirrors YAML test sources):

```
$DOMAINS_DIR/<domain>/output/tests/
  <module>_tests.catala_en           # generated from specs/tests/<module>_tests.yaml
  <module>_happy_tests.catala_en     # if multiple YAML files
```

Each generated test file begins with a module import directive:

```
> Using ModuleName
```

Where `ModuleName` = snake_case module name with first letter uppercased
(e.g. `eligibility` → `Eligibility`, `earned_income` → `Earned_income`).
This corresponds to the `> Module ModuleName` declaration in the main `.catala_en` file.

---

## Running Tests

```bash
# Run all tests in the project
clerk test

# Typecheck only (no execution)
clerk typecheck <file>

# Run a specific scope
clerk run <file> --scope=TestAllow001
```

`clerk test` scans the entire project for `#[test]` attributes and cram test blocks,
executes them, and reports pass/fail counts.

---

## Cram Tests (reference only)

Cram tests compare terminal output to expected output. **Use assertion tests instead** —
they are more readable and less brittle.

A cram block looks like:

````markdown
```catala-test-cli
$ catala interpret --scope=TestAllow001
[expected output here]
```
````

---

## Explaining ruleset results

```sh
cd $DOMAINS_DIR/snap/output
# Run single scope
catala interpret tests/eligibility_tests.catala_en -I . --trace --scope TestAllow001
# JSON output
catala interpret tests/eligibility_tests.catala_en -I . --trace --scope TestAllow001 --trace-format=json
# Run all scopes
clerk run tests/eligibility_tests.catala_en --catala-opts=--trace
```

## Dependency graph visualization

```sh
# Create PNG
python tools/catala_depgraph.py $DOMAINS_DIR/snap/output/eligibility.catala_en --format png
# Create Mermaid diagram
python tools/catala_depgraph.py $DOMAINS_DIR/snap/output/eligibility.catala_en --format mmd
```

---

## CI Setup (GitHub Actions)

Use the Catala Docker image and `clerk ci`:

```yaml
jobs:
  tests:
    container:
      image: registry.gitlab.inria.fr/catala/ci-images:latest-c
      options: --user root
    steps:
      - uses: actions/checkout@v4
      - run: opam exec -- clerk ci
```

`clerk ci` combines `clerk test`, building all targets, and backend-specific testing.

---

## Common Gotchas

1. **`#[test]` must be on its own line** directly before `declaration scope`
2. **All scope inputs required** — all `input` fields must be assigned via `definition result.<field>`; omitting any is a type error
3. **Assertion parentheses required** — `assertion (x = true)` works; `assertion x = true` may parse incorrectly
4. **List semicolons** — `[A; B]` not `[A, B]`
5. **Single `=` for equality** — `=` in assertions, `==` is a syntax error
6. **Sub-scope for cross-module tests** — use `result scope ModuleName.ScopeName`, NOT `output result content ModuleName.ScopeName`; scopes are not types in Catala 1.1.0
7. **`catala` vs `catala-metadata` fences** — module declarations must use `catala-metadata` for cross-module visibility; `catala` blocks are private at runtime even if `catala typecheck` passes
