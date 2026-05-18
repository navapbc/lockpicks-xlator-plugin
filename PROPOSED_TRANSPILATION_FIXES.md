# Proposed Transpilation Fixes

**Date:** 2026-05-18  
**Authors:** Analysis by Claude Code, commissioned by Bradley Smock  
**Scope:** CIVIL ↔ Catala compatibility gaps, root-cause analysis of observed transpilation errors, and proposals for spec and process improvements  
**Status:** Analysis only — no code changed in this pass

---

## Background

Three investigation sources provided the raw material for this analysis:

- `doc/investigations/2026-05-14-catala-errors-collated.md` — 11 error classes inventoried from production session logs through 2026-05-14
- `doc/investigations/2026-05-18-app-support-transpiler-patches.md` — 28 patches (A1–A14, B1–B14) applied in-place to the running app on top of v2.5.1, now at risk of being dropped when the upstream v2.6.3b branch merges
- Direct review of all `tests-*.jsonl` and `tests:<module>-*.jsonl` logs across all six domains in `/Users/bradley/Documents/PolicyBridge/domains/` (ak-doh, snap, nj-payments), covering runs from 2026-05-07 through 2026-05-18

This document extends those analyses with a language-level comparison of CIVIL and Catala, groups the patch set and error classes by root cause, and proposes three layers of remediation: immediate patch re-application, CIVIL spec changes, and transpiler process improvements.

---

## 1. Pipeline Overview

```
  ┌────────── Step 3 (extract-ruleset) ─────────┐
  │  AI → *.civil.yaml + naming-manifest.yaml  │  ── (A) CIVIL extraction quality
  └─────────────────┬───────────────────────────┘
                    ▼
  ┌────────── transpile_to_catala.py ──────────┐
  │  CIVIL YAML → *.catala_en (ruleset)        │  ── (B) expression rewriting
  └─────────────────┬───────────────────────────┘  ── (C) type declaration
                    ▼
  ┌──── transpile_to_catala_tests.py ──────────┐
  │  tests YAML → *_tests.catala_en             │  ── (D) test-emission bugs
  └─────────────────┬───────────────────────────┘
                    ▼
  ┌────────── clerk build (catala ocaml) ──────┐
  │  *.catala_en → _build/ocaml/*.ml + tests   │  ── (E) typecheck failures
  └─────────────────┬───────────────────────────┘
                    ▼
  ┌────────── clerk test runtime ──────────────┐
  │  runs each #[test] scope                   │  ── (F) runtime evaluation errors
  └─────────────────┬───────────────────────────┘
                    ▼
  ┌────────── simulator (in-process) ──────────┐
  │  uvicorn loads demo/main.py + catala_runtime│  ── (G) packaging / preconditions
  └─────────────────────────────────────────────┘
```

Errors labeled (A)–(G) correspond to their pipeline stage. Most recurring production errors originate in (B), (C), and (D) — all transpiler-level — with the root cause in CIVIL→Catala language mismatches.

---

## 2. CIVIL vs. Catala Compatibility Matrix

### 2A. Features That Map Cleanly (low transpilation risk)

| CIVIL Feature | Catala Equivalent | Notes |
|---|---|---|
| `module:` scope declaration | `declaration scope ScopeName:` | CIVIL module name → PascalCase scope |
| Typed `inputs:` entity fields | Flat `input field content Type` or struct inputs | Entity becomes struct when `invoke:`-bound |
| `outputs:` fields | `output field content Type` | `type: bool` → condition or output depending on usage |
| `computed:` intermediate fields | `internal field content Type` | `tags: [expose]` promotes to `output` |
| `if/then/else` conditionals | `if X then Y else Z` | Catala is right-associative; same shape |
| `type: int` | `integer` | 1:1 |
| `type: float` | `decimal` | 1:1 |
| `type: money` | `money` | Catala money is cent-precise; no currency annotation |
| `type: bool` | `boolean` / `condition` | Condition variables use `rule/fulfilled` pattern |
| `type: date` | `date` | Format differs: `date("YYYY-MM-DD")` → `\|YYYY-MM-DD\|` |
| `type: list` | `list of Type` | Item type must be explicit; see §2D |
| `type: enum` | `declaration enumeration Name:` | PascalCase variants required |
| Arithmetic `+ - * /` | Same operators | Type strictness differs; see §2D |
| Boolean ops `&&` `\|\|` `!` | `and` `or` `not` | Pure syntax rewrite |
| Comparison `== != < <= > >=` | `= <> < <= > >=` | `==` → `=` is blocking; `!=` → `<>` |
| Constants (inlined) | Inlined literals | Transpiler inlines at code-gen; no Catala constant decl |
| Tables (row expansion) | Stacked `definition X under condition` blocks | Each table row → one scope definition block |
| `invoke:` subscope fields | `> Using SubModule` + subscope declaration + wiring | Entity bindings → struct type wiring |
| `add_reason: {code, ...}` | `ReasonEntry` struct pattern + `filter/map` pipeline | Transpiler encodes accumulation explicitly |
| Deny rules (`kind: deny`) | `exception base_eligible … definition eligible equals false` | Base case + exception per rule |

### 2B. CIVIL Features With No Direct Catala Equivalent (require transpiler workarounds)

| CIVIL Feature | Gap | Workaround Cost |
|---|---|---|
| `tags: [expose]` | Catala has no visibility pragma; fields are either `internal` or `output` | Low: transpiler promotes tagged fields to `output` at code-gen |
| `type: string, values: [...]` | Catala has no constrained-string type; needs explicit `enumeration` | Medium: transpiler must generate enum decl + rewrite all string literals to PascalCase (see §3, Category III) |
| `currency: USD` on money fields | Catala `money` type has no currency annotation | Negligible: metadata-only; no runtime cost if all currencies are homogeneous |
| `overlays: strategy:` | Catala has no composition semantics for merging rule sets across jurisdictions | Very High: unimplemented; would require a flatten-and-merge algorithm or a dispatcher scope pattern |
| `precedence: allow_overrides_deny` | Catala's exception model does not natively support allow-wins semantics | High: requires hand-wired exception order or explicit guard chains; not currently implemented |
| `precedence: first_match` | No short-circuit / first-match built-in in Catala | High: requires negated guard conditions on each subsequent rule; combinatorial |
| `add_to_set: {output: value}` | Catala has no set type; lists allow duplicates | Medium: transpiler must emit explicit membership checks before appending, or accept semantic difference |
| `add_instruction: {step, ...}` | No instruction/action list idiom in Catala | Low: reuse `ReasonEntry` pattern with an `Instruction` struct |
| `is_null(field)` | Catala uses `optional of Type` + pattern match; no null-check builtin | Medium: would require wrapping nullable inputs in `optional of Type` + desugaring `is_null` to pattern match |
| `between(date, start, end)` | No builtin in Catala | Very Low: desugar to `date >= start and date <= end` |
| `in(value, [a,b,c])` | Catala syntax is `[a; b; c] contains value` (inverted argument order) | Very Low: rewrite in transpiler |

### 2C. Catala Features Not Represented in CIVIL (expressiveness gaps)

| Catala Feature | Use Case | CIVIL Coverage |
|---|---|---|
| `duration` type | Time intervals, benefit periods, age arithmetic | No duration type; durations computed as integer differences, losing units |
| Pattern matching on structures | Decompose structs with guards | Not needed in current CIVIL policies; can approximate via computed fields |
| `context` variable kind | Caller-overridable defaults without input schema change | Not needed for batch policy evaluation |
| Quantifiers: `exists`, `for all` | Existential/universal over lists | Approximable via `count + filter` |
| Assertions | Executable invariant checking in scope | Test cases serve this role in CIVIL |
| Exception stacking independence | Multiple exceptions to same label fire independently | Aligns well with CIVIL's multi-deny model |

### 2D. Syntax and Semantic Mismatches That Cause Transpilation Errors

These are the directly actionable items. Each corresponds to a recurring error class.

#### Type system mismatches

| Mismatch | CIVIL | Catala | Severity | Fix |
|---|---|---|---|---|
| Equality operator | `==` | `=` (single equals; `==` is syntax error) | **Blocking** | Step 9 of `translate_expr_to_catala` rewrites `==` → `=` |
| List literal separator | `[a, b, c]` | `[a; b; c]` | **Blocking** | Patch B5 adds `;`-separated emission |
| String enum casing | `'mfj'` (as-written) | `Mfj` (PascalCase variant) | **Blocking** | Patches A10, B2, B7, B8 |
| Money literal format | Bare numeric (e.g. `35000`) | `$35,000` | **Blocking** | `money_literal()` in transpiler; Patch A3 fixes float support |
| Date literal format | `date("2024-01-01")` call | `\|2024-01-01\|` pipe notation | **Blocking** | Patches A7, A8, B4, B5 |
| List item type unspecified | `type: list` with no `item:` | `list of integer` (default, usually wrong) | **Blocking** | Patch A5 adds `civil_field_to_catala_type()` |
| Cross-module enum qualification | `integer` declared in consuming scope | `Module.EnumType` required | **Blocking** | Patch A9 adds `entity_to_sub_info` |
| Integer/money mixed arithmetic | `count * rate` (implicit coerce) | Requires explicit `decimal of` cast | **High** | Step 10 / Step 10.5 in transpiler; non-exhaustive |
| `sum(list)` missing type keyword | `sum(list)` | `sum money of list` (type required) | **High** | Patch A6 (Step 3.65) |
| Bare integer in money context | `12` where `$12` needed | Type error at Catala compile | **Medium** | Steps 11–13.9 in transpiler; fragile regex chain |
| `in(x, [a,b,c])` argument order | `in(x, [a,b,c])` | `[a; b; c] contains x` | **Medium** | Step 12.5 was added then removed (Patch A14); current status: expression not rewritten |
| Division by zero (non-money) | `A / B` where B can be 0 | Catala runtime `division by zero` | **Medium** | Step 10.5 guards money * int/int only; general case unguarded |

#### Rule evaluation mismatches

| Mismatch | CIVIL | Catala | Severity |
|---|---|---|---|
| `precedence: deny_overrides_allow` | Implicit; any deny rule firing denies | `exception base_eligible` per rule; multiple exceptions independent | Low — maps correctly if each deny rule is encoded as an exception |
| `precedence: allow_overrides_deny` | Allow wins over deny | Not expressible natively in Catala's exception model | **High — unimplemented** |
| `precedence: first_match` | First rule that fires wins; rest skipped | No short-circuit; all exceptions evaluated independently | **High — unimplemented** |
| Reasons list accumulation | `add_reason` fires and appends on each matching rule | Must manually encode as `ReasonEntry` struct + filter/map | Medium — transpiler handles this, but edge cases exist |

#### Scope / module composition mismatches

| Mismatch | CIVIL | Catala | Severity |
|---|---|---|---|
| Invoke sub-modules | `invoke: bind: Entity: ParentEntity` | Subscope declaration + field wiring in "Subscope Wiring" section | Low — transpiler handles this |
| Test file module imports | Not in CIVIL | `> Using ModuleName` required in test `.catala_en` | Low — Patch B11 adds `sub_module_names` scan |
| Jurisdiction layering (overlays) | `overlays: strategy:` declarative | No built-in composition; must flatten before transpile | **Very High — unimplemented** |

---

## 3. Patch Set Analysis by Root Cause

The 28 patches (A1–A14 on `transpile_to_catala.py`, B1–B14 on `transpile_to_catala_tests.py`) applied to the running app cluster into six root-cause categories.

### Category I — Deployment / runtime environment (A1, B1)

**Problem:** v2.6.3b switched the shebang to `uv run --script` with inline dependency metadata. The bundled macOS app has its own Python interpreter and cannot use `uv run`.

- **A1 / B1**: Restore `#!/usr/bin/env python3` shebang; remove four `uv` script header lines.

**Root cause:** Upstream tooling change not coordinated with app bundling constraints.

---

### Category II — Type system: money, list, date (A3, A5, A7, A8, B4, B5, B6)

**Problem:** The transpiler defaulted to integer math and integer list element types; dates were emitted without Catala pipe-notation.

- **A3**: `money_literal()` extended to handle floats (fractional cents like `$609.34`) and negatives. Upstream v2.6.3b truncates to `int(value)`, silently dropping cents.
- **A5**: New function `civil_field_to_catala_type()` resolves list/set element types from the `item:` key instead of defaulting to `list of integer`. Also used in `emit_declarations` call sites that had `civil_type_to_catala` (field-level function not aware of `item:`).
- **A7**: `_format_key_condition()` now wraps `datetime.date` values in `|...|` instead of falling through to integer branch.
- **A8**: `_substitute_row_into_expr()` now wraps `datetime.date` row values in `|...|` in both expression paths.
- **B4**: `default_value_for_type()` extended with `date → "|2024-01-01|"` and `list:* → "[]"` branches.
- **B5**: `value_to_catala()` extended with `date → "|{value}|"` and `list:* → "[ " + "; ".join(item_vals) + " ]"` branches.
- **B6**: `build_field_type_map()` tags list fields as `"list:{item_type}"` (e.g. `"list:money"`) so the emit functions know the element type.

**Root cause:** CIVIL spec allows `type: list` without `item:` (item defaults to `money` implicitly), and date values from YAML are parsed by PyYAML as `datetime.date` objects but were not converted to Catala's `|...|` notation.

**Spec action:** Mark `item:` required on all `type: list` fields (§6B-4 below).

---

### Category III — Enum / string type resolution (A4, A9, A10, A11, B2, B3, B7, B8)

**Problem:** The transpiler generated wrong or unqualified enum types for string-valued input fields and for cross-module enum references. Test files emitted bare variant names instead of `Module.FieldType.Variant` qualified form.

- **A4**: Add `_PER_PERSON` to the money-hint suffix list in `_format_constant_value`.
- **A9**: `entity_to_sub_info` dict built alongside `invoke_bound_entities`; struct field type selection uses three-way check: local values → unqualified enum, sub-module values → `SubModule.FieldName`, else → `integer` fallback.
- **A10**: New enum-declaration pass for input `string` fields with `values:` that are not invoke-bound entities. Fills the gap between the table-key pass (only table keys get enums) and the decisions pass (only output fields get enums).
- **A11**: In non-struct entity field declarations, splits the `if ftype in ("enum", "string")` branch: string fields with a declared enum get the PascalCase enum type; string fields with no enum source get `text` (not `integer` — signals intent rather than silently producing wrong code).
- **B2**: `value_to_catala()` signature extended with `field_name` and `module_name`; emits qualified `Module.FieldType.Variant` form when provided.
- **B3**: `enum_variants` changed from `{raw: emit}` dict to `[str]` list of raw values; PascalCase transformation moved into emit functions.
- **B7**: `_enum_default()` helper produces a properly-qualified fallback variant.
- **B8**: `module_name` threaded through `emit_field_value` and its callers.

**Root cause:** The CIVIL spec has two overlapping ways to express an enum-constrained string: `type: enum` (explicit) and `type: string, values: [...]` (implicit). The transpiler's enum-declaration logic was scattered across three separate passes, each covering only part of the surface area, and cross-module enum qualification was not implemented.

**Spec action:** Formalize `type: enum` as the canonical form; document `values:` as required on enum fields (§6B-1 below).

---

### Category IV — Expression rewriting (A6, A13, A14)

**Problem:** Several CIVIL expression idioms were not rewritten to valid Catala syntax.

- **A6**: Step 3.65 added: `sum(list)` → `sum {type} of list`. The element type is derived from the `field_type` parameter already present in `translate_expr_to_catala`.
- **A13**: Bracket subscript syntax `table_name[key]` removed from detection, extraction, and substitution code. The function-call syntax `table('name', key).col` is canonical.
- **A14**: Step 12.5 removed (`in(VAR, [V1, V2])` → `[V1; V2] contains VAR`). This rewrite conflicted with Catala's `contains` syntax in some contexts. The CIVIL spec still documents `in()` as a valid expression function.

**Root cause:** The CIVIL expression language (§"Expression language (minimal)" in the spec) includes functions (`in()`, `sum()`, `between()`) that have no direct syntactic equivalent in Catala and require different expansion strategies. The spec is under-constrained about which forms are transpiler-canonical vs. deprecated.

**Spec action:** Deprecate `in()` in the expression language, document `sum()` as requiring an element type annotation in context, remove bracket subscript syntax (§6B-6, §6B-7 below).

---

### Category V — Entity / struct mode detection (B9, B10, B11)

**Problem:** The test transpiler used entity count to determine whether struct or flat mode should be used, causing test files to fail when a single entity was invoke-bound.

- **B9**: `multi_entity` now set from `bool(invoke_bound_entities)` instead of `len(entity_fields) > 1`.
- **B10**: `invoke_bound_entities` built and passed from `transpile_file` to `emit_test_scope`.
- **B11**: `sub_module_names` scanned from `invoke:` computed fields; `> Using SubModule` directives emitted in test `.catala_en` file header.

**Root cause:** The `multi_entity` concept is CIVIL-internal (an entity is "multi" when it becomes an invoke-bound struct). The original test transpiler inferred this incorrectly from entity count; the correct signal is the presence of invoke bindings.

---

### Category VI — Diagnostic noise and fallbacks (A12, B12, B13, B14)

**Problem:** Several code paths printed `ERROR:` or `WARNING:` messages to stderr while still continuing with potentially incomplete output, producing noise without aiding recovery.

- **A12**: Eight stderr print calls converted to silent fallbacks in `transpile_to_catala.py`.
- **B12**: Empty test input (`no tests found`) now raises `fail()` instead of emitting an empty placeholder file.
- **B13**: `enum_variants` supplemented from test case input values for cross-module string fields with no table or `values:` declaration.
- **B14**: String decision assertion simplified; `snake_to_pascal(str(val))` always used.

**Root cause:** Defensive error handling was added incrementally without a consistent policy. Silent failures make debugging harder; raising errors early makes them easier.

---

## 3B. New Error Classes Discovered in 2026-05-18 Log Review

The direct log review of all domains uncovered four error classes not present in the prior investigation documents.

### Error 12 — `type: string` fields emit the field name as the Catala type (nj-payments)

**Symptom:** `weekly_eligibility.catala_en:159` — `Unknown type "ClaimWeekId", not a struct or enum previously declared` followed by 28 cascading `unknown identifier` errors for every field in the scope.

**Root cause:** The transpiler's `civil_type_to_catala("string")` path emits the field name converted to PascalCase (`claim_week_id` → `ClaimWeekId`) as the Catala type, rather than a primitive. This appears to be an unimplemented case in `civil_type_to_catala()`: when `ftype == "string"` and the field has no `values:` list and is not a table key, the function falls through to a name-based path intended for struct types.

**Impact:** Any module with a `type: string` input field that has no `values:` enum list will fail to compile. The entire scope becomes unresolvable — all 29 compilation errors are cascade failures from this one mistyped declaration.

**Fix needed:** In `civil_type_to_catala` (and `civil_field_to_catala_type`), add a branch: when `ftype == "string"` and no enum is available for the field, emit `text`. Then separately address Error 13.

---

### Error 13 — Catala 1.1.0 has no `text` or `string` primitive type (nj-payments)

**Symptom:** After patching Error 12 to emit `text`, the next error was `Unknown built-in type "text"` at the same line.

**Root cause:** The `civil_type_to_catala` mapping `"string": "text"` was speculative. Catala 1.1.0's type primitives are: `Integer`, `Decimal`, `Boolean`, `Money`, `Duration`, `Date`, `Position`. There is no string/text primitive.

**Implication:** CIVIL's `type: string` has no valid direct Catala equivalent. Every `type: string` field in CIVIL must be either:
1. An enumeration (if `values:` is declared) — map to `declaration enumeration` + PascalCase variants
2. An opaque identifier (like `claim_week_id`) — no good Catala representation; declare as `integer` with a comment, or omit from scope if unused in expressions
3. A free-text field (notes, messages) — no Catala equivalent; cannot be included in a typechecked Catala scope

**Impact:** This is a fundamental CIVIL↔Catala incompatibility for string fields without enumerated values. The `nj-payments/weekly_eligibility` fix worked because `claim_week_id` was unused in all rule expressions — it could be simply declared but its type was irrelevant to computation. Future modules with string fields used in expressions will hit a harder wall.

**Fix needed:** The transpiler should detect `type: string` fields that are:
- Used in rule expressions → error at CIVIL validation time ("string fields used in comparisons must have `values:` declared")
- Not used in any expression → emit as `integer` with a `# opaque identifier` comment, or suppress from scope declaration entirely
- Have `values:` → emit as `declaration enumeration` (existing path)

The CIVIL spec should document that `type: string` without `values:` is only valid for fields that are never referenced in `when:`, `expr:`, or `computed:` expressions.

---

### Error 14 — Enum case identifiers emitted lowercase in `declaration enumeration` blocks (snap)

**Symptom:** `eligibility.catala_en:154` — `Syntax error at "citizen": expected the name of an enum case`. The generated Catala contained `-- citizen` (lowercase).

**Root cause:** The transpiler has `snake_to_pascal()` for converting CIVIL string values to PascalCase enum variants, but at least one of the enum declaration emit paths does not apply this conversion. The Catala compiler requires enum case identifiers to start with a capital letter.

**Affected path:** Likely the decisions-output enum pass in `emit_declarations` or the table-key enum pass — the same paths not covered by Patch A10 (which added a new pass for non-invoke-bound input string fields). Alternatively, the `values:` list for the `citizen_status` field in `snap/eligibility` uses lowercase values that are emitted directly without PascalCase conversion.

**Impact:** Any module whose enum declaration emit path skips `snake_to_pascal()` will produce a Catala syntax error on the first enum case, blocking the entire module's compilation.

**Fix needed:** Audit every `emit_declarations` path that appends `-- <variant>` lines and confirm each applies `snake_to_pascal()` to the variant name. The three passes are: (1) table-key enum pass, (2) decisions/output enum pass, (3) Patch A10's input string-with-values pass. All three must apply the transformation.

---

### Error 15 — "No applicable rule" runtime error (snap/income_calculation)

**Symptom:** `TestDenyEdge001` and `TestSynDenyEdge001` fail with `income_calculation.catala_en:93`/`:97` — `During evaluation: no applicable rule to define this variable in this situation`.

**Root cause:** These test cases exercise an input combination where no `when:` clause in the ruleset fires for some internal variable. Catala requires every `internal` scope variable to have at least one applicable rule definition; when the condition space has a gap, this runtime error fires.

**Impact:** 2 of 27 tests fail. The test cases exist and compile but assert a code path the CIVIL rules don't cover.

**Fix needed:** This is most likely a CIVIL authoring gap (missing rule coverage) rather than a transpiler bug. The fix is to inspect `income_calculation.catala_en:93` and `:97` to identify which variable has no applicable rule, trace it back to the CIVIL computed/output definition, and add a base-case rule or default expression.

---

### Error 16 — Output fields `eligible`/`manual_review_required` silently skipped in test assertions (snap)

**Symptom:** `WARN  case 'allow_001': expected: field 'eligible' not found in decisions or computed; skipping` — repeated for all 6 test cases in both `income_calculation` and `resource_determination`.

**Root cause:** The test transpiler's `emit_test_scope` looks for `eligible` and `manual_review_required` in the `decisions` and `computed` dicts of the module metadata (from `*_meta.py`). If those fields are present in the CIVIL `outputs:` but are omitted from the metadata (e.g. because they are emitted as Catala `output` rather than being tracked in the decisions dict), the test assertion is silently skipped.

**Impact:** Non-fatal — tests pass, but the assertions for the most important output fields are never checked. Tests that should catch a wrong `eligible` value become vacuous.

**Fix needed:** Ensure the `*_meta.py` generation in `transpile_to_catala.py` includes all fields declared as `outputs:` in the CIVIL spec, including `eligible` and `manual_review_required`. Cross-check that the test transpiler's field-lookup covers both `output` and `internal` Catala declarations.

---

## 4. Error Class → Root Cause Map

The following table covers all error classes observed across the three prior investigation documents **plus** the direct log review of all domains conducted 2026-05-18. Errors 12–16 are newly discovered.

| Error # | Description | Domain(s) | Root cause category | Patch(es) addressing it |
|---|---|---|---|---|
| 1 | Cross-module type mismatch (`int` vs `Module.EnumType` / `money`) | ak-doh | Cat. III (enum resolution) | A5, A9 — transpiler-side; extraction-side (AI authoring `int`) unaddressed |
| 2a | Test syntax: list `,` vs `;` | ak-doh | §2D type mismatch | B5 |
| 2b | Test syntax: bare date literal | ak-doh | Cat. II (type system) | A7, A8, B4, B5 |
| 2c | Test syntax: `client_data` unknown identifier | ak-doh | Cat. V (struct mode) | B9, B10 |
| 3 | Division-by-zero at runtime | ak-doh | §2D semantic mismatch | A6 (Step 10.5, money×int/int only); general case unguarded |
| 4 | `sum(list)` no type keyword | ak-doh | Cat. IV (expression rewriting) | A6 (Step 3.65) |
| 5 | Unused variable warning | ak-doh, snap | CIVIL extraction quality | Not a transpiler bug |
| 6 | Naming-manifest divergence | snap | Process gap | Not a transpiler bug |
| 7 | Ambiguous prompts logged as success | — | UI session classification | Not a transpiler bug |
| 8 | Required field defaulted in test transpile | ak-doh, nj-payments | Cat. II + Cat. III | B4, B6 (partial) |
| 9 | `PackageNotFoundError: gmpy2` | snap | Deployment / packaging | Fixed in `xlator-ui.spec` |
| 10 | "No demo directory found" / "Demo missing main.py" | snap, nj-payments | Simulator precondition | Not a transpiler bug |
| 11 | Transient `tool_end: "Error"` events | — | Bash exit code noise | Not relevant |
| **12** | **`type: string` input fields emit field name as Catala type** (`content ClaimWeekId`) | **nj-payments** | **Cat. III (type mapping)** | **No existing patch — new bug** |
| **13** | **Catala 1.1.0 has no `text`/`string` primitive** — `civil_type_to_catala("string")` mapping invalid | **nj-payments** | **§2D type mismatch** | **No existing patch — new bug** |
| **14** | **Enum case identifiers emitted lowercase** (`-- citizen` instead of `-- Citizen`) | **snap** | **Cat. III (enum casing)** | **No existing patch — new bug** |
| **15** | **"No applicable rule" runtime error** — rule set has uncovered input space | **snap** | CIVIL rule logic gap | Not a transpiler bug; test input or rule gap |
| **16** | **Output field `eligible`/`manual_review_required` not found in decisions or computed** — test assertions silently skipped | **snap** | Cat. III / test emitter | No existing patch — likely CIVIL output field not exposed |

---

## 5. Proposed Fixes

### 5A. Immediate: Re-apply patches after v2.6.3b merge (P0)

After the `update-xlator-plugin` branch merges into `main`, the vendored files at `vendor/lockpicks-xlator-plugin/xl-plugin/tools/` will be the v2.6.3b versions. All patches below must be evaluated and re-applied.

**Highest priority (breakage without these):**

| Priority | Patch | File | Action |
|---|---|---|---|
| 1 | A1 / B1 | Both | Change shebang back to `#!/usr/bin/env python3`; remove `uv` script header lines |
| 2 | A3 | `transpile_to_catala.py` | Replace `money_literal` with float/negative-aware version |
| 3 | A5 | `transpile_to_catala.py` | Add `civil_field_to_catala_type()`; replace `civil_type_to_catala(ftype)` with it in `emit_declarations` where `field_def` is in scope |
| 4 | A10 | `transpile_to_catala.py` | Add enum-declaration pass for non-invoke-bound input `string` fields with `values:` |
| 5 | B2, B7, B8 | `transpile_to_catala_tests.py` | Extend `value_to_catala()` with qualified enum output; add `_enum_default()`; thread `module_name` |
| 6 | B9, B10 | `transpile_to_catala_tests.py` | Change `multi_entity` to use `invoke_bound_entities`; build and pass that set from `transpile_file` |

**Additional patches (apply all):**

| Patch | File | Summary |
|---|---|---|
| A2 | `transpile_to_catala.py` | Add `import datetime` |
| A4 | `transpile_to_catala.py` | Add `_PER_PERSON` to money-hint suffixes |
| A6 | `transpile_to_catala.py` | Add Step 3.65: `sum(list)` → `sum {type} of list` |
| A7 | `transpile_to_catala.py` | `_format_key_condition`: wrap `datetime.date` in `\|...\|` |
| A8 | `transpile_to_catala.py` | `_substitute_row_into_expr`: wrap `datetime.date` in `\|...\|` |
| A9 | `transpile_to_catala.py` | Add `entity_to_sub_info`; extend struct field type selection |
| A11 | `transpile_to_catala.py` | Split `enum`/`string` branch in scope input declarations |
| A12 | `transpile_to_catala.py` | Convert noisy `sys.stderr` prints to silent fallbacks |
| A13 | `transpile_to_catala.py` | Remove bracket subscript `table_name[key]` support (verify no CIVIL files use it first) |
| A14 | `transpile_to_catala.py` | Remove Step 12.5 `in(VAR, [...])` rewrite (verify no CIVIL files use it first) |
| B3 | `transpile_to_catala_tests.py` | Change `enum_variants` from `{raw: emit}` dict to `[str]` list |
| B4 | `transpile_to_catala_tests.py` | Add `date` and `list:*` branches to `default_value_for_type()` |
| B5 | `transpile_to_catala_tests.py` | Add `date` and `list:*` branches to `value_to_catala()` |
| B6 | `transpile_to_catala_tests.py` | Tag list fields as `"list:{item_type}"` in `build_field_type_map()` |
| B11 | `transpile_to_catala_tests.py` | Add `sub_module_names` scan; emit `> Using SubModule` in test file header |
| B12 | `transpile_to_catala_tests.py` | Replace empty-test placeholder with `fail()` |
| B13 | `transpile_to_catala_tests.py` | Supplement `enum_variants` from test case input values |
| B14 | `transpile_to_catala_tests.py` | Simplify string decision assertion to `snake_to_pascal(str(val))` |

**Verification after re-application:** Run `/xl:transpile-and-test ak-doh` on the merged branch and confirm errors 2 and 4 from the error inventory (§4) are absent, and that the `ak-doh eligibility` build no longer produces `integer vs Module.HouseholdType` errors for list-typed fields.

**Additional fixes required for Errors 12–14 (discovered 2026-05-18):**

| Priority | Bug | File | Action |
|---|---|---|---|
| P0 | Error 14 — enum case lowercase | `transpile_to_catala.py` | Audit every `-- <variant>` emit path in `emit_declarations`; apply `snake_to_pascal()` uniformly. The three passes (table-key, decisions/output, A10 input-string) must all convert values. Verify by compiling `snap/eligibility.catala_en` and checking that `-- citizen` becomes `-- Citizen`. |
| P0 | Error 12 — `type: string` emits field name as type | `transpile_to_catala.py` | In `civil_type_to_catala()` and `civil_field_to_catala_type()`, add an explicit branch for `ftype == "string"` with no enum: emit a stub (see Error 13 note) rather than falling through to the PascalCase-name path. |
| P1 | Error 13 — no Catala `text` primitive | `transpile_to_catala.py` + `validate_civil.py` | For string fields with no `values:` that are unreferenced in expressions: emit as `integer` with a comment, or omit from scope declaration. For string fields used in expressions: raise a CIVIL validation error. |
| P1 | Error 16 — `eligible`/`manual_review_required` skipped in test assertions | `transpile_to_catala.py` | Check `*_meta.py` generation: ensure all CIVIL `outputs:` fields are written to the decisions dict. Check `transpile_to_catala_tests.py`: ensure test assertion lookup covers Catala `output` declarations, not only the decisions/computed split. |

---

### 5B. CIVIL Spec Changes to Reduce Transpilation Friction (P1)

These changes to `xl-plugin/core/CIVIL_DSL_spec.md` eliminate categories of transpilation errors at the source by making CIVIL more explicit.

#### 5B-1. Formalize `type: enum` as the canonical constrained-string type

**Problem:** The spec documents two ways to express an enumerated value: `type: enum, values: [opt1, opt2]` (used in the tax filing example for `filing_status`) and `type: string, values: [opt1, opt2]` (implicitly equivalent but not documented as such). The transpiler must handle both, and the two-pass enum declaration logic in `emit_declarations` covers different surfaces for each.

**Proposal:** In the "Types + fact schema" section, replace the implicit `type: string, values:` pattern with an explicit `type: enum` canonical form. Update all examples to use `type: enum`. Add a deprecation note on `type: string, values:` with a note that the transpiler accepts it but `type: enum` is preferred.

**Effect:** Eliminates the ambiguity that caused Patches A10, A11. Makes the transpiler's enum declaration pass straightforward: `type: enum` → always emit `declaration enumeration`.

**Related:** Catala requires PascalCase variants. Document in the spec that all `values:` entries must be valid Python identifiers (no spaces, no hyphens), since the transpiler applies `snake_to_pascal()` to convert them.

---

#### 5B-2. Require `item:` on all `type: list` and `type: set` fields

**Problem:** The spec allows `item:` to be omitted on list fields (it appears as a comment in the module skeleton but is not marked required). The transpiler defaults to `list of integer` when `item:` is missing, which is almost never correct — most list fields hold `money` values.

**Proposal:** In the "Types + fact schema" section, mark `item:` as required when `type` is `list` or `set`. Add a validation rule to `validate_civil.py`: list/set fields without `item:` are a validation error, not a warning.

**Effect:** Eliminates the "list of integer vs list of money" type mismatch error class (Error 1 variant). Reduces silent wrong-type declarations that only surface as Catala typecheck failures.

---

#### 5B-3. Add `type: duration` as a first-class field type

**Problem:** The spec has no `duration` type. Policies that compute elapsed time (e.g., `residency_months`, benefit period length) use `type: int` and implicitly track units in the field name. Catala has a native `duration` type and date+duration arithmetic.

**Proposal:** Add `type: duration` to the "Types + fact schema" section. Document that durations are expressed as a number of days, months, or years depending on context. Map to Catala's `duration` type. This is a non-breaking addition; existing modules using `int` for duration-like fields are unaffected.

**Effect:** Enables future modules to express duration arithmetic correctly in both CIVIL and Catala, without relying on integer arithmetic with implicit units.

---

#### 5B-4. Restrict numeric type coercion: make cross-type arithmetic explicit

**Problem:** CIVIL's expression language implicitly allows mixed-type arithmetic (e.g., `count * RATE` where `count` is `int` and `RATE` is `decimal`). Catala strictly segregates `integer`, `decimal`, and `money`; mixed arithmetic requires explicit casts (`decimal of count`, `money of val`). The transpiler attempts to paper over this with regex-based coercions (Steps 10–13.9) but these are fragile for nested expressions.

**Proposal:** Add a note to the "Expression language" section: arithmetic across `int`, `float`, and `money` types requires that operands be the same type. The CIVIL spec recommends expressing cross-type arithmetic using computed intermediate fields of the target type (e.g., compute `rate_decimal: float = EARNED_INCOME_DEDUCTION_RATE` and use it in a money expression). This is a documentation change, not a breaking change.

**Effect:** Sets author expectations. New modules authored after this change will have fewer type-mismatch errors at Catala compile time.

---

#### 5B-5. Document `invoke:` module naming convention and circular dependency constraint

**Problem:** The spec's `invoke:` section says `module:` "resolves to `$DOMAINS_DIR/<domain>/specs/<name>.civil.yaml`" but does not state that the value must match the file name exactly (case-sensitive, no `.civil.yaml` extension). Circular invocation constraint is listed in the constraints table but not in the transpilation section.

**Proposal:** In §2f (Invoke fields), add: "The `module:` value must match the `.civil.yaml` filename exactly, without the `.civil.yaml` extension (e.g., `module: earned_income` resolves to `earned_income.civil.yaml`). Circular invocations (A→B→A) are detected at validation time and cause a hard error."

**Effect:** Reduces "module not found" errors that arise from naming mismatches. The constraint is already implemented; documenting it makes it author-visible.

---

#### 5B-6. Remove bracket subscript syntax from the expression language

**Problem:** The spec's "Expression language" section lists `table(name, key...).field` as the canonical table lookup syntax. The transpiler (until Patch A13) also accepted `table_name[key]` as a bracket subscript variant. The running app removed this variant because it introduced ambiguity. No known CIVIL files use bracket syntax.

**Proposal:** Remove the implicit bracket subscript form from the spec (it was never explicitly documented, but should be explicitly excluded). Add a note: "Only the `table(name, key...).field` function-call form is supported. The `table_name[key]` bracket form is not recognized."

**Effect:** Prevents future CIVIL authors from using bracket syntax and eliminates the dual-path detection code in the transpiler.

---

#### 5B-7. Deprecate `in(value, [a,b,c])` from the expression language

**Problem:** The spec documents `in(value, [a,b,c])` as a valid expression function. Patch A14 removed the transpiler rewrite that converted it to `[a; b; c] contains value` because it conflicted with Catala's `contains` syntax in some contexts. The running app no longer rewrites this expression, meaning any CIVIL file using `in()` will produce an untranslated expression that fails Catala parsing.

**Proposal:** In the "Expression language" section, mark `in(value, [a,b,c])` as deprecated. Document the recommended replacement: express set membership using a table with an enum key and a boolean value column, or restructure the rule to use multiple conditions joined by `||`.

**Effect:** Prevents new CIVIL authoring from using a function that produces a transpilation error. The underlying need (list membership test) should be covered by a recommended alternative.

---

### 5C. Transpiler Process Improvements (P1–P2)

These are improvements to the transpilation pipeline and tooling, not requiring spec changes.

#### 5C-1. Cross-module type contract validation at extraction time (P1)

**Problem:** The most frequent production error class (Error 1) is a scope-boundary type mismatch: the consuming module declares a field as `type: int` but the producing sub-module exports it as `enum(HouseholdType)` or `money`. This is caught only when `clerk build` runs — after the user has approved the ruleset and clicked "Run Tests."

**Proposal:** During `/xl:extract-ruleset` (or in `validate_civil.py`), build a contract table from each module's `outputs:` and `computed: [expose]` declarations. For every `inputs.<entity>.fields` declaration in a module that invokes another, verify the declared type matches the producer's exported type. Emit a structured error: `"module 'eligibility' declares 'household_type: int' but sub-module 'program_standards_lookup' exports 'household_type: enum(HouseholdType)'"`.

**Cost:** Low. The data already flows through `civil_helpers.py` for the sum-type rewrite. Add a separate validator pass.

**Effect:** Converts a late, opaque Catala typecheck error into an early, locatable CIVIL error. Eliminates the round-trip through `/xl:update-ruleset` to fix type mismatches.

---

#### 5C-2. Non-money division-by-zero lint rule (P1)

**Problem:** Step 10.5 in `translate_expr_to_catala` inserts a zero-guard for the `money * (int_a / int_b)` pattern. The general case (`int / int`, `decimal / decimal`, `decimal / int`) is not guarded, and Catala will produce a runtime `division by zero` error when the denominator field is 0 (e.g., uninitialized in a test case).

**Proposal:** Add a CIVIL lint rule in `validate_civil.py` (or as a new `civil_lint.py`) that flags any `expr:` or `conditional:` expression containing `A / B` where `B` is a non-required field or a computed field that can equal 0. Either emit a `civil-lint` warning before transpile, or auto-rewrite to the same zero-guard pattern used by Step 10.5.

**Cost:** Medium. Requires expression parsing to identify division subexpressions and trace field optionality. Can be done incrementally — start with a regex-based heuristic that flags `/ Field.name` where `Field.name` is not marked `required: true`.

---

#### 5C-3. Structured Catala error parser for debrief and UI (P2)

**Problem:** Catala's `[ERROR n/N] Error during typechecking, …` output format is not matched by any pattern in `app/services/tech_error.py`'s `_MEDIUM_PATTERNS`. No debrief files are written for Catala build failures, making support investigation harder.

**Proposal:** Add a `catala_error.py` module that parses each `[ERROR n/N]` block (file:line.col + category + provenance lines) into typed records. Use this to:
- Add `\[ERROR \d+/\d+\]` to `_MEDIUM_PATTERNS` so debrief files are written.
- Emit a structured `:::error` block per error category in the UI panel, with file:line references.
- Surface recovery hints: for "incompatible types: integer vs `Module.X`", link to the cross-module validation proposal above.

---

#### 5C-4. Aggregate defaulted-field warnings into a test-suite report (P2)

**Problem:** `transpile_to_catala_tests.py` emits one `WARN` line per defaulted field per test case — a test with 17 fields can produce 17 warning lines, making it hard to see which fields are systematically missing.

**Proposal:** Have `transpile_to_catala_tests.py` (or a wrapper) write a `test-defaults-report.yaml` alongside the transpile output, grouped by `case_id → [{field, civil_type, default_used}]`. The UI's `view_last_run_output` can surface a folded "X required fields defaulted across N cases" with drill-down.

---

## 6. Compatibility Gap Summary Table

| Gap | Severity | Fix location | Proposed fix |
|---|---|---|---|
| `==` → `=` operator | Blocking | Transpiler (existing Step 9) | Already fixed; verify in regression test |
| List separator `,` → `;` | Blocking | Transpiler | Patch B5 |
| String enum PascalCase | Blocking | Transpiler + spec | Patches A10, B2, B7, B8; §5B-1 (enum type formalization) |
| Money literal format (floats) | Blocking | Transpiler | Patch A3 |
| Date literal `\|...\|` format | Blocking | Transpiler | Patches A7, A8, B4, B5 |
| `list of integer` default for untyped lists | Blocking | Transpiler + spec | Patch A5; §5B-2 (require `item:`) |
| Cross-module enum type mismatch | Blocking | Transpiler + process | Patch A9; §5C-1 (contract validation) |
| `sum(list)` missing type keyword | Breaking | Transpiler | Patch A6 |
| `multi_entity` gate (invoke-bound structs) | Test failure | Transpiler | Patches B9, B10 |
| Sub-module `> Using` in test files | Test failure | Transpiler | Patch B11 |
| Money literal truncation (float cents) | Silent wrong answer | Transpiler | Patch A3 |
| Division by zero (non-money) | Runtime error | Process | §5C-2 (lint rule) |
| `in(x, list)` not rewritten | Parse error | Spec | §5B-7 (deprecate `in()`) |
| Bracket subscript `table[key]` | Parse error | Spec + transpiler | Patch A13; §5B-6 (remove from spec) |
| `type: list` without `item:` | Blocking | Spec | §5B-2 (require `item:`) |
| Cross-module type contract | Blocking | Process | §5C-1 |
| `duration` type missing | Semantic gap | Spec | §5B-3 (add `type: duration`) |
| `is_null(field)` not desugared | Semantic gap | Spec + transpiler | No current fix; requires optional type support |
| Overlay composition | Feature gap | Spec + transpiler | Out of scope for this pass; §CIVIL spec already notes as unimplemented |
| `allow_overrides_deny` / `first_match` | Semantic gap | Spec + transpiler | Out of scope for this pass; high complexity |
| `type: string` (no values) emits field name as type (`ClaimWeekId`) | **Blocking** | Transpiler | §5A additional fix (Error 12); add explicit `string` branch in `civil_type_to_catala` |
| Catala 1.1.0 has no `text` primitive — string fields without enum have no valid type | **Blocking** | Spec + transpiler | §5A additional fix (Error 13); spec must restrict bare `type: string` to non-expression contexts |
| Enum case identifiers emitted lowercase (`-- citizen` not `-- Citizen`) | **Blocking** | Transpiler | §5A additional fix (Error 14); apply `snake_to_pascal()` to all `-- <variant>` emit paths |
| "No applicable rule" runtime gap in rule coverage | Runtime error | CIVIL rules | Error 15; inspect `income_calculation.catala_en:93,97`; add base-case rule in CIVIL |
| Output fields `eligible`/`manual_review_required` silently skipped in test assertions | Silent test gap | Transpiler | §5A additional fix (Error 16); audit `*_meta.py` generation and test emitter field lookup |

---

## 7. What Is NOT Worth Addressing (Now)

- **`tool_end: "Error"` events** (Error 11) — noise from Bash exit codes during AI reconnaissance. Already correctly classified by `infer_pipeline_success`.
- **Unused-variable warnings** (Error 5) — surfaced correctly by Catala as warnings, not errors. Low frequency; treat as informational.
- **Simulator preconditions** (Errors 9, 10) — packaging and demo-directory issues; not transpiler bugs. Error 9 fixed in `xlator-ui.spec`; Error 10 needs a guard in the post-test completion hook.
- **Overlay composition** — unimplemented in both the transpiler and the validator; §§"Implementation status" in the CIVIL spec already flags this. Requires a separate design pass.
- **`allow_overrides_deny` / `first_match` precedence strategies** — valid CIVIL spec features but not used by any current domain module. Address when a domain requires them.

---

## Appendix: Key File Locations

| File | Role |
|---|---|
| `xl-plugin/core/CIVIL_DSL_spec.md` | Authoritative CIVIL spec; target for §5B changes |
| `xl-plugin/tools/transpile_to_catala.py` | Main transpiler (1,812 lines); target for §5A patches |
| `xl-plugin/tools/transpile_to_catala_tests.py` | Test transpiler (594 lines); target for §5A patches |
| `xl-plugin/tools/civil_expr.py` | Expression reference extractor; normalizes `table_lookup:` fields |
| `xl-plugin/tools/validate_civil.py` | CIVIL validator; target for §5C-1 and §5C-2 additions |
