---
name: catala-emit-tests
description: Emit Catala #[test] fixtures from YAML test cases by reading the Catala source for scope-input shape.
---

# Emit Catala test fixtures from YAML test cases

Read each YAML test file under `specs/tests/<program>*_tests.yaml`, infer the target scope's input shape directly from the Catala source (`specs/<program>.catala_en` plus any modules reached via `> Using`), and emit a `.catala_en` peer per non-null-input YAML file containing one `#[test]` scope per case. After emission, drive `clerk typecheck` via the U2 clerk-loop helper to self-correct any emission errors before handing back to the caller.

This sub-skill is the AI-driven test-fixture emitter introduced in v14.0.0, replacing the deterministic transpiler script that preceded it. The Catala source is the authority for scope-input layout; the naming-manifest is consulted only for leaf-field type / optional / enum-variant metadata, **never** to derive entity layout from `inputs.<Entity>` top-level keys.

## Input

```
/catala-emit-tests <domain> <program>
```

Both args are required. `<program>` matches `specs/<program>.catala_en`.

Read `../../core/output-fencing.md` now.
Read `../../core/catala-authoring-quickref.md` now (declaration scope, declaration structure, `> Using` directives).
Read `../../core/catala-test-quickref.md` now (`#[test]` annotation, `result scope`, struct literal form).

## Pre-flight

1. **Domain folder exists?** — `$DOMAINS_DIR/<domain>/` missing → Print:
   :::error
   Domain `<domain>` not found.
   :::
   Stop.
2. **Catala source exists?** — `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` missing → Print:
   :::error
   No Catala source at `specs/<program>.catala_en`. Run `/extract-ruleset <domain>` first.
   :::
   Stop.
3. **Naming manifest exists?** — `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` missing → Print:
   :::error
   `specs/naming-manifest.yaml` not found. Run `/extract-ruleset <domain>` first.
   :::
   Stop.
4. **YAML test files exist?** — `$DOMAINS_DIR/<domain>/specs/tests/<program>*_tests.yaml` glob returns nothing → Print:
   :::error
   No YAML test files under `specs/tests/`. Run `/create-tests <domain> <program>` first.
   :::
   Stop.

## Process

### Step 1: Load source context

Read `$DOMAINS_DIR/<domain>/specs/<program>.catala_en`. Parse it as literate Markdown containing fenced ` ```catala-metadata ` and ` ```catala ` blocks. From the `catala-metadata` declarations, extract:

- **The target scope.** Find the `declaration scope <ScopeName>:` block. Record the scope's `input <var> content <Type>` declarations as `(var_name, type_reference)` pairs. Types may be unqualified (defined in this module) or module-qualified (`<SubModule>.<Type>` — defined in an imported module).
- **Output declarations.** Record each `output <name> content <Type>` line. These drive the `assertion (result.<name> = <expected>)` block for each test case.

For each module-qualified input type, the source contains a `> Using <SubModule>` directive at the top. Read `$DOMAINS_DIR/<domain>/specs/<SubModule>.catala_en` and parse its `declaration structure <Type>:` blocks to recover the field shape. Repeat transitively if the sub-module references types from its own sub-modules.

For each `declaration structure <Type>:` block, record `(field_name, field_type)` pairs where `field_type` may be a primitive (`money`, `integer`, `boolean`, `decimal`, `date`, `duration`) or `list of <NestedType>` where `<NestedType>` is another struct in the same or qualified module.

Load `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml`. Extract per-field `type:`, `optional:`, and `enum_variants:` (or legacy `values:`). **Manifest's role is leaf metadata only** — never read `inputs.<Entity>` top-level keys to derive scope layout; the Catala source's `declaration scope` line is authoritative.

### Step 2: Enumerate YAML test files

Glob `$DOMAINS_DIR/<domain>/specs/tests/<program>*_tests.yaml`. For each match, capture the stem (e.g., `<program>_tests`, `<program>_boundary_expanded_tests`).

**Skip files matching `*_null_input_expanded_tests.yaml`** — null-input test cases cannot be encoded as Catala without specific patterns (preserved from the retired transpiler behavior). Collect the skipped filenames for the `:::detail` note at the end.

### Step 3: Emit one `.catala_en` peer per non-null YAML

For each non-skipped YAML test file `<stem>.yaml`, write `$DOMAINS_DIR/<domain>/specs/tests/<stem>.catala_en` containing:

1. The `> Using <ModuleName>` directive matching the source module's `> Module` line.
2. One `#[test] declaration scope Test<CaseId>:` block per YAML test case, with `result scope <Module>.<TargetScope>`.
3. Per-case `definition result.<input_var> equals <struct literal>` lines populated from the YAML's `inputs:` dict.
4. Per-case `assertion (result.<output_field> = <expected>)` lines for every entry in the YAML's `expected:` block, with values translated per the output's declared Catala type.

**Struct literal shape.** Use the actual scope-input declarations from Step 1, not the manifest's entity layout. If the scope declares `input household content Household_classification.Household` and the `Household` struct has `members content list of HouseholdMember`, emit:

```catala
definition result.household equals Household_classification.Household {
  -- household_size: <value>
  -- gross_monthly_income: <value>
  -- ...
  -- members: [
    Household_classification.HouseholdMember {
      -- age: <value>
      -- ...
    }
  ]
}
```

Map YAML keys into the struct literal based on field-name match. A flat YAML test case with both top-level and nested-struct fields collapsed (e.g., `household_size: 2` alongside `age: 35`) splits across the appropriate struct boundary by consulting the field name lists from Step 1.

**Value rendering.** Use the leaf field type (from Catala source first, fall back to manifest's `type:`):

- `money`: accept Catala-native strings (`"$1,800"`) and numeric forms (`1800`); both emit as `$1,800`. Negative as `-$500`. Cents form `$1,800.50` when fractional.
- `integer`: bare numeral.
- `decimal`: numeral with explicit fraction, or `decimal of <expr>`.
- `boolean`: `true` / `false`.
- `date`: `|YYYY-MM-DD|`.
- `enum`: bare variant name (PascalCase per the field's `enum_variants:` from the manifest).

**Assertion translation.** Match `expected:` keys to output declarations. For `bool` output: `assertion (result.<field> = true)`. For enum: `assertion (result.<field> = <Variant>)`. For numeric: `assertion (result.<field> = <literal>)`. For list-of-reasons: `assertion (result.<reasons_field> = [<Variant1>; <Variant2>; ...])` ordered as in the YAML.

### Step 4: Self-check via the U2 clerk loop

After each `.catala_en` file is written, drive `clerk_loop.run()` against it. The skill calls the deterministic Python library, not via shell-out:

```python
from clerk_loop import run, LoopResult
result = run(Path("$DOMAINS_DIR/<domain>/specs/tests/<stem>.catala_en"))
```

Handle the result:

- **`status="ok"`** — `clerk typecheck` (and `clerk test` when applicable) passed. Continue to the next file. Surface a one-line confirmation in `:::progress`: `<stem>.catala_en — typecheck passed (iter <result.iterations>)`.

- **`status="unresolved"`** — the loop hit the iteration cap (default `N=5`) without converging. Surface a `:::user_input` fence containing `result.summary`, the per-diagnostic `file:line:category:message` lines from `result.last_diagnostics`, and the `repair_history` `action_taken` recommendations. Ask the analyst whether to:
  - **Hand-edit** the emitted `.catala_en` to address the diagnostics, then re-run the loop on this file.
  - **Skip** this YAML file (proceed without its Catala companion — the file will be regenerated next time `/catala-emit-tests` runs).
  - **Restart** the emission for this file (re-emit from scratch, in case the first pass had a systemic problem).
  - **Abort** the whole sub-skill (no further YAML files processed, no `record-tier-manifest` write — caller handles).

  Do not proceed to the next YAML file until the analyst chooses.

Operational note: `clerk_loop.run()` calls `catala_runtime.reset_log()` between iterations by default (PR #45 prevention).

### Step 5: Hand back to the caller

After every non-skipped YAML file has produced a typechecking `.catala_en` peer, surface a `:::important` summary:

```
Emitted N .catala_en test fixtures under specs/tests/:
- <stem1>.catala_en (iter K1)
- <stem2>.catala_en (iter K2)
...
Skipped M null-input file(s): <list>
```

**Do not call `xlator record-tier-manifest`** — that is the caller's responsibility (`/create-tests`, `/expand-tests`, or the SME running this sub-skill standalone). Emitting the test fixtures and stopping keeps the sub-skill composable: caller-driven invocations consolidate the manifest write with their own bookkeeping, and standalone SME invocations can pair this skill with a follow-up `xlator record-tier-manifest <domain> --tier tests` call.

## Output

| File | Action |
|------|--------|
| `specs/tests/<stem>.catala_en` | Created or Overwritten — one per non-null YAML test file |
| `specs/<program>.catala_en` | Read — source of truth for scope-input shape |
| `specs/<SubModule>.catala_en` (each `> Using` target) | Read — source of nested struct definitions |
| `specs/naming-manifest.yaml` | Read — leaf field type / optional / enum-variant metadata |
| `specs/tests/<program>*_tests.yaml` | Read — test input + expected data |

## Common Mistakes to Avoid

- **Don't derive scope-input layout from the manifest's `inputs.<Entity>` keys.** The Catala source's `declaration scope` line is authoritative. The manifest's flat entity list does not, in general, map 1:1 to the scope's input declarations — nested struct fields, list-of-struct nesting, and cross-module type qualifiers all break the assumption. This is the bug class the pre-v14.0.0 deterministic transpiler suffered; do not reintroduce it.
- **Don't use `<CurrentModule>.<Entity>` as the type qualifier when the scope's input is cross-module qualified.** If `declaration scope X: input household content Household_classification.Household`, the struct literal must say `Household_classification.Household { ... }`, not `<CurrentModule>.Household { ... }`. Follow the `> Using` directive in the source to confirm the qualifier.
- **Don't emit two parallel scope-input definitions when the scope has one input with a nested struct.** A `Household` struct with `members content list of HouseholdMember` is one input (typed `Household_classification.Household`), not two parallel inputs. The `members` field is populated as a nested list literal inside the parent struct. (This is the symptom of the previous bullet's pitfall.)
- **Don't skip the clerk-loop check.** Step 4 is mandatory — emission without typecheck is unverified output. If a file repeatedly fails to converge, the right move is to surface the diagnostics to the analyst (Step 4's `unresolved` path), not to ship a broken `.catala_en`.
- **Don't call `xlator record-tier-manifest` inside this sub-skill.** The caller (or the SME) owns that bookkeeping. Calling it here when invoked from `/create-tests` or `/expand-tests` creates a double-write that records a stale manifest mid-flight.
- **Don't process `*_null_input_expanded_tests.yaml` files.** Null inputs are not Catala-encodable in v1; skip silently with a `:::detail` note.
