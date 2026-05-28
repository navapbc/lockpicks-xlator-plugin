# Plugin Output Improvements

Issues observed in live plugin output that affect how Xlator renders assistant messages.
Confirmed via `parseFenceBlocks` console logging on 2026-05-01.

## 1. Missing newline before fence markers

Fence markers (`:::type`) sometimes appear mid-line, directly attached to the end of a
preceding sentence with no newline separator.

**Observed examples:**
```
Now running Step 2: Computation skeleton.:::progress
Steps:
  [✓] Step 1: Load current state
  ...
```
```
`termination_date = ...`:::important
Updated guidance sections.
```

**Expected:**
```
Now running Step 2: Computation skeleton.
:::progress
Steps:
  ...
```

Xlator works around this with a normalization pass, but the plugin should emit a `\n`
before every `:::type` token.

## 2. Missing newline between sentences in plain text

Consecutive sentences in plain-text output are sometimes concatenated without a newline,
causing them to run together in the UI.

**Observed example:**
```
Now running Step 3: Ruleset groups.Phase headings from the index map to four functional areas: ...
```

**Expected:**
```
Now running Step 3: Ruleset groups.
Phase headings from the index map to four functional areas: ...
```

This is not worked around on the Xlator side — the text is displayed as-is.

## 3. Fence block content emitted as `undefined`

A fence block is sometimes emitted with the literal string `undefined` as its content,
indicating an uninitialized variable in the plugin's output logic.

**Observed example:**
```
Applying heuristics to skeleton computations and index signals:

:::progress
undefined
:::
```

**Expected:** either a valid progress string or no fence block at all.

Xlator suppresses fence blocks whose content is the sentinel string `"undefined"`, but
the plugin should not emit them in the first place.

## 4. Step headers emitted as plain text without markdown formatting

Step progress headers like "Step 5 (UPDATE): Sample rules" are output as plain text with
no markdown markers, so they render unstyled in the chat panel.

**Observed example:**
```
Step 4 (UPDATE): Ruleset modules.

Step 5 (UPDATE): Sample rules — scanning...
```

**Expected:**
```
**Step 4 (UPDATE): Ruleset modules.**

**Step 5 (UPDATE): Sample rules — scanning...**
```

or using heading markers (`##`). Without formatting, these headers are visually
indistinguishable from regular body text. This is a plugin output issue; Xlator does not
work around it.

## 6. `/xl:transpile-and-test` ignores explicit `<program>` argument when multiple modules exist

When running tests from a specific test module's detail page, the UI passes the module name
as the `<program>` argument: `/xl:transpile-and-test <domain> <module>`. With multiple
`.civil.yaml` files present, the pre-flight "Determine module" logic only had branches for
the case where `<program>` was _not_ specified, so the model fell through to the
multi-program disambiguation prompt and either asked the user to choose or ran all programs.

**Observed behavior:** Test results showed output for the entire ruleset instead of the
single requested module.

**Fix (applied 2026-05-08):** Added an explicit first bullet to the pre-flight step:

> `<program>` specified in the command → use it directly. Skip to step 2.

The execution block (`xlator catala-pipeline <domain> <program>`) already correctly scopes
to a single program; the bug was purely in the pre-flight determination logic.

**File:** `xl-plugin/commands/transpile-and-test.md`

## 5. Inconsistent option formatting across "Action required" prompts

"Action required" sections use two different formats for presenting user choices, with no
apparent logic distinguishing when each is used.

**Format A — labeled list (used for skeleton step):**
```
Please choose one of the available options:

[a] accept — keep skeleton as-is and proceed to Step 3
[b] replace — re-run full skeleton extraction, overwrite existing
[c] revise — display existing skeleton for manual editing
```

**Format B — inline slash-separated (used for ruleset_groups step):**
```
[a]ccept / [r]eplace / [m]erge? (default: accept)
```

Format A is preferable: it is more readable, explicitly labels each option, and includes
descriptions. Format B is terse and omits descriptions. All "Action required" prompts
should use Format A for consistency.

## 7. `sum()` not transpiled to Catala — causes compile failure

CIVIL expressions using `sum(list)` were passed through the transpiler unchanged.
Catala's `sum` keyword requires an explicit element type, so the raw `sum(list)` output
would cause a Catala compile error at the `catala-pipeline` step.

**Observed behavior:** `xlator catala-pipeline` fails with a Catala parse/type error on
any module whose expressions include `sum(...)`. Reproduced 2026-05-20 on
`ak-doh/monthly_income_estimation`:
```
Syntax error at "(":
» the 'sum' operator must be followed by the type to be summed..
sum(recent_payment_amounts) / number_of_payments
```

**Expected:** `sum(list)` → `sum <type> of list`, where `<type>` is derived from the
*list element type* (`item:` field on the CIVIL list declaration), not the target
field's type. Also, scope inputs declared as `type: list, item: money` were being
emitted as `content list of integer` (placeholder), which fails Catala's type checker
once `sum money of` is used in expressions against them.

**Fix (re-applied 2026-05-20):**
- Added `field_to_catala_type(field_def)` helper that maps `type: list, item: <T>`
  to `list of <civil_type_to_catala(T)>`.
- Updated input scope declaration emission to use it (was emitting placeholder
  `list of integer` for every list).
- `transpile()` now builds a `list_item_types: {field_name: catala_type}` map from
  `doc["inputs"]` and threads it through every `translate_expr_to_catala()` call site.
- Added step 3.65 to `translate_expr_to_catala()`: regex rewrite `sum(<ident>)` →
  `sum <type> of <ident>` using the threaded `list_item_types` map (default `money`
  when the field is unknown).

A prior fix dated 2026-05-11 was recorded here but absent from the codebase as of
2026-05-20 — either reverted or never committed. The current entry documents the
re-application with the corrected design (list element type, not field type).

**File:** `xl-plugin/tools/transpile_to_catala.py`

**Ticket 20 follow-up (2026-05-21) — symptom recurred due to stale symlink, not a transpiler regression:**

`xlator catala-pipeline ak-doh resource_reasonable_compatibility` reproduced the same
`sum(...)` Catala syntax error despite the fix above being present in the in-tree
transpiler. Investigation confirmed the in-tree v2.11.3 emits `sum money of` correctly.
The actual cause: `/Users/bradley/.local/bin/xlator` was a symlink to the marketplace
cache at `~/.claude/plugins/cache/lockpicks-marketplace/xl/2.6.3b/` (installed by a
prior `xlator_setup.sh` run) rather than to the vendored plugin. All `xlator` invocations
were silently running the stale v2.6.3b tools.

**Fix (2026-05-21):** Re-pointed the symlink to the vendored plugin:
```bash
ln -sf /path/to/vendor/lockpicks-xlator-plugin/xl-plugin/bin/xlator ~/.local/bin/xlator
```
The vendored plugin is the authoritative source; the symlink must track it. If
`xlator_setup.sh` re-runs and resets this symlink to the marketplace cache, it will
break again — the setup script should be updated to point at `vendor/` instead.

---

## 8. Test transpiler emits wrong Catala for `list`, `date`, and cross-module enum fields

**Root cause (four bugs in `transpile_to_catala_tests.py`):**

**Bug 8a — `multi_entity` mode fires incorrectly for sub-modules.**
Sub-modules with multiple input entities but no sub-invoke bindings (e.g.
`resource_reasonable_compatibility`, `income_reasonable_compatibility`) have their
entity fields flattened into scope inputs by the main transpiler. The test transpiler
incorrectly entered multi-entity mode for them (condition `len(entity_fields) > 1`),
emitting `definition result.client_data equals Module.ClientData { ... }` when the
scope expects flat `definition result.gross_earned_income equals ...` assignments.
Catala reports this as "unknown identifier `client_data`".

**Bug 8b — `list` fields default to `0` and are serialised as Python literals.**
`default_value_for_type` did not handle `list` → returned `"0"`. `value_to_catala`
did not handle list values → fell through to `str([5])` = `"[5]"`. Catala requires
`[]` for empty lists and `[ $5; $200 ]` for non-empty `list of money` values.
`build_field_type_map` also discarded the list item type (e.g. `money`) needed for
per-element formatting.

**Bug 8c — `date` fields default to `0`.**
`default_value_for_type` returned `"0"` for `date` type. Catala requires `|YYYY-MM-DD|`
fence syntax. `value_to_catala` likewise did not wrap date values in `|...|`.

**Bug 8d — Cross-module enum fields default to `0`.**
String fields that map to Catala enums defined in another module (e.g. `household_type`
→ `Program_standards_lookup.HouseholdType`) have no lookup table in the current CIVIL
spec. When not provided by a test case, `default_value_for_type("string")` returned
`"0"`. Catala reports a type mismatch (integer given where enum expected).

**Fix (applied 2026-05-11; bugs 8b/8c re-applied 2026-05-20):**
- `multi_entity` condition changed from `len(entity_fields) > 1 or ...` to
  `bool(invoke_bound_entities)` — only root modules that pass entity structs to
  sub-scopes use multi-entity mode.
- `build_field_type_map` now stores `"list:{item}"` (e.g. `"list:money"`) for list
  fields so item type is available downstream.
- `default_value_for_type` now returns `"|2020-01-01|"` for `date` and `"[]"` for
  `list:*`.
- `value_to_catala` now handles `date` (wraps in `|...|`) and `list:*` (formats
  each element, joins with `; `, wraps in `[ ... ]`).
- `transpile` now scans test case inputs to build fallback `enum_variants` for
  string fields without table definitions in the current spec (covers cross-module
  enum types).

Bugs 8b (list defaults/serialisation) were absent from the codebase on 2026-05-20
during a re-run of `ak-doh/monthly_income_estimation`: list fields still defaulted
to `"0"` and lists were serialised as Python literals `[456, 398, 430]` (commas)
instead of Catala literals `[ $456; $398; $430 ]` (semicolons + per-element money
formatting). Re-applied with the typed-list approach described above.

**File:** `xl-plugin/tools/transpile_to_catala_tests.py`

---

## 10. `money_literal` truncates fractional cent values

The `money_literal` helper in `transpile_to_catala.py` used `int(float_val)` which
silently dropped the decimal part of monetary values. Table rows with fractional values
(e.g. SSI B/1 2023: `$609.34`, SSI B/1 2026: `$628.67`) were emitted as `$609` and
`$628`, causing Clerk assertion failures for any test that asserted on those values.

**Observed behavior:** `TestAllowSsiBLivingArrangement2024` and `TestEdgeYear2023H1eBLiving`
failed with value-mismatch errors because the generated Catala table contained truncated
amounts.

**Fix (applied 2026-05-11):** Rewrote `money_literal` to detect a fractional part and
format it as `$NNN.DD` (e.g. `$609.34`) rather than `$NNN` (e.g. `$609`).

**File:** `xl-plugin/tools/transpile_to_catala.py`

---

## 11. `xlator` script fails in git worktrees — `.xlator.local.env` not found

When the app server runs inside a `git worktree add` branch (e.g. `.worktrees/convert-doc`),
`git rev-parse --show-toplevel` returns the worktree root, not the main repo root. The
`.xlator.local.env` file only exists in the main repo root, so the `source` call on line 31
fails with "No such file or directory" and every `xlator` invocation aborts.

**Observed behavior:** Uploading a document from a worktree-hosted app server produces:

```
WARNING xlator_ui.routes.documents — Upload errors for non-md: [
  'Dirty Cert Reasons.docx: /Users/bradley/.local/bin/xlator: line 31:
  /path/to/.worktrees/convert-doc/.xlator.local.env: No such file or directory'
]
```

**Fix (applied 2026-05-19):** After resolving `show-toplevel`, the script now checks whether
`.xlator.local.env` exists there. If not, it resolves the git common dir
(`git rev-parse --git-common-dir`) — which always points to the shared `.git` directory in the
main repo — and uses its parent as the fallback `PROJECT_ROOT`.

```bash
_TOPLEVEL="$(git rev-parse --show-toplevel)"
if [ -f "$_TOPLEVEL/.xlator.local.env" ]; then
    PROJECT_ROOT="$_TOPLEVEL"
else
    _GIT_COMMON_DIR="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
    _MAIN_ROOT="$(dirname "$_GIT_COMMON_DIR")"
    if [ -f "$_MAIN_ROOT/.xlator.local.env" ]; then
        PROJECT_ROOT="$_MAIN_ROOT"
    else
        PROJECT_ROOT="$_TOPLEVEL"
    fi
fi
```

**File:** `xl-plugin/bin/xlator`

---

## 9. Division-by-zero in `prorated_rental_expense` (unearned income classification)

When `total_non_bathroom_rooms` is 0 (the default for test cases that do not involve
room rental), the formula `(rented_rooms / total_non_bathroom_rooms) * allowable_household_expenses`
causes a Catala runtime division-by-zero in 12 of 16 test cases (those without room
rental inputs).

**Fix (applied 2026-05-11):** Added a zero guard in the CIVIL spec expression:
`if total_non_bathroom_rooms > 0 then ... else 0`.
Regenerated `unearned_income_classification.catala_en`.

**File:** `specs/unearned_income_classification.civil.yaml`

---

## 12. Sub-module entity binding emits subscope wiring without auto-promoting forwarded fields onto the parent struct

When a CIVIL `computed:` entry invokes a sub-module with an entity-to-entity
binding like `bind: { Household: Household }`, the transpiler emits one
`definition <subscope>.<field> equals household.<field>` line per input field
declared on the sub-module's Household entity. If the parent module's
`inputs.Household.fields` does not also declare those fields, Catala fails to
compile with `Field <X> does not belong to structure Household` at every
forwarded field — the broken `.catala_en` file is still written to disk.

**Observed (2026-05-21) in `ak-doh/eligibility.civil.yaml`:**

The eligibility module invokes `monthly_income_estimation`, `earned_income_exclusion_chain`,
and seven other sub-modules with `bind: { Household: Household }`. The transpiled
`eligibility.catala_en` emits forwarding statements for every sub-module input field,
but the parent Household struct declares only the field subset used by the parent's own
rules. Compile fails with 11 errors of this shape:

```
┌─[ERROR]─ 1/11 ─
│  Field "pay_frequency" does not belong to structure "Household".
├─➤ eligibility.catala_en:505.57-505.80:
│ 505 │   definition monthly_income_result.pay_frequency equals household.pay_frequency
```

Because `output/clerk.toml` lists all 9 modules under a single target, this
broken `Eligibility.ml` failure blocks the OCaml build for every other module
in the project — `xlator catala-pipeline` cannot complete for any program in
ak-doh until eligibility's Household struct is fixed.

**Fix (applied 2026-05-21, ticket 14 — Phase A fail-fast validation):**

At transpile time, before emitting any subscope wiring or writing any file,
`transpile_to_catala.py` now calls `check_bind_forwarding()` — a pure function
that compares each sub-module's input entity fields against the parent's declared
fields for that entity. If any fields are missing it prints one structured error
per bind pair (naming the sub-module, entity names, and exact missing field list)
and exits 1 without writing a `.catala_en` file. This replaces 11 cryptic Catala
compile errors at OCaml build time with one actionable error at transpile time.

Error format:
> `ERROR: Sub-module 'monthly_income_estimation' (bound as 'Household' → 'Household') requires fields not declared on parent's inputs.Household.fields: ['pay_frequency', ...]. Either add the fields (with optional: true if appropriate) to the parent CIVIL spec, or remove the entity-to-entity bind and forward fields individually.`

Phase B (auto-promotion of sub-module fields onto the parent struct) is deferred
until at least one real user asks for it; Phase A's error message already provides
the exact field list to copy.

**File:** `xl-plugin/tools/transpile_to_catala.py`

---

## 13. `type: string` field transpiled inconsistently across scopes (integer vs. enum)

When a CIVIL field is declared `type: string`, the Catala emit depends on whether
the field also appears as a column in a `constants` lookup table within the same
module:

* **Constant-table column** → transpiler infers an enum (e.g. `HouseholdType` with
  variants `A1E`/`B1E`/…) and emits `content HouseholdType`.
* **Not a column** → transpiler falls back to `content integer` (not `content string`).

When two modules declare the same logical field — one with a backing lookup
table, the other without — and one invokes the other with `bind: { … }`, the
forwarded value's type (`integer`) does not match the sub-module's input
(`<EnumType>`) and Catala fails type-check.

**Observed (2026-05-21) in `ak-doh/eligibility.civil.yaml`:**

After [[12]] was worked around by adding the missing Household fields, building
`eligibility.catala_en` produced two errors:

```
[ERROR] incompatible types: integer ─/─ Program_standards_lookup.HouseholdType
  definition standards_result.household_type equals household.household_type
  data household_type content integer
  input household_type content HouseholdType
```

Same for `living_arrangement` (sub-module enum `LivingArrangement`).

In both CIVIL files the field is declared `type: string`. The sub-module's
declaration produces an enum because the standards table column infers one; the
parent module produces `integer` because there is no table.

**Fix (applied 2026-05-21, ticket 15 — cross-module type resolution):**

At transpile time, after loading all sub-module CIVIL docs (already done for bind
validation), the transpiler now runs `build_cross_module_enums(sub_module_docs)` to
scan each sub-module's constants tables for string fields used as table keys. This
builds a `{field_name: (qualified_catala_type, variants)}` map.

When emitting a `type: string` field on an invoke-bound entity struct:
1. Local table or `values:` declaration → use local enum (unchanged behavior).
2. In sub-module map → emit the qualified type (e.g. `Program_standards_lookup.HouseholdType`). No local `declaration enumeration` is emitted — the enum lives in the sub-module and is already accessible via the `> Using` directive.
3. Neither → fall back to `integer` (unchanged).

Layer 2 divergence check: if two sub-modules infer different variant sets for the same
field name, `build_cross_module_enums()` fails fast with a structured error listing both
modules and both variant sets.

Result in `ak-doh/eligibility.catala_en`:
```
# Before:  data household_type content integer
# After:   data household_type content Program_standards_lookup.HouseholdType
```

Same for `living_arrangement` → `Program_standards_lookup.LivingArrangement`.

**File:** `xl-plugin/tools/transpile_to_catala.py`

---

## 16. String fields with no enum variants downgraded to integer instead of omitted

**Problem:** When a CIVIL field has `type: string` and no enum variants (no `values:` list and
no occurrence as a table key), both transpilers previously downgraded the field to Catala
`integer`. This was semantically dishonest — a free-form string became a number that
downstream Catala could do arithmetic on — and hid AI extraction bugs where categorical
fields were left without variants.

**Root cause:** `emit_declarations()` fell through to `catala_type = "integer"` for string
fields with no local or cross-module enum. The test transpiler matched this with
`effective_type = "int" if civil_type == "string" and not valid_variants else civil_type`.

**Fix (applied 2026-05-21, ticket 11 — string-no-variants as optional, not integer):**

Layer 0 finding: `grep "content text|content string"` across the entire repo returns zero
hits in emitted Catala. The deliberate integer downgrade in the original code strongly
implies `content text` does not work as a Catala input declaration in this dialect.
Confirmed by absence of any test or example using `content text` as an input type.
Proceeding with the omission approach.

Main transpiler (`transpile_to_catala.py`): In both the struct-field declaration path
(invoke-bound entities) and the scope-input declaration path (non-invoke-bound entities),
the `catala_type = "integer"` fallback is replaced with a two-way branch:

- **Optional** (`optional: true`) → `continue`: the field is omitted from the Catala
  struct/scope-input declarations entirely. Policy logic that references it will fail to
  compile, surfacing the modeling issue. Tests omit the field on the input side.
- **Required** (no `optional: true`) → `raise ValueError` with a message naming the field,
  its entity, and listing the four remedies: declare `values:`, use as a table key,
  mark `optional: true`, or change the field type.

The scope-input declaration path also gains a `cross_module_enums` check (previously
missing from that path) before the optional/required branch, so cross-module enum fields
are correctly resolved when the entity is not invoke-bound.

Test transpiler (`transpile_to_catala_tests.py`): `emit_field_value()` now returns
`(None, None)` for string fields with no enum variants instead of downgrading to `int`.
Both callers in `emit_test_scope()` (`continue` on `catala_val is None`) skip emission
for these fields, keeping the emitted test case in sync with the Catala scope inputs.

**Migration:** If a domain has a required `type: string` field with no `values:` and no
table key, the transpiler now raises a `ValueError`. Remedies in order of preference:
1. Declare `values: [val1, val2, ...]` if the field is categorical.
2. Add a table keyed on the field if the values come from a lookup table.
3. Mark `optional: true` if the field is metadata the policy doesn't need.
4. Change the field type (e.g. to `int`) if the original `string` was an extraction error.

**Files:** `xl-plugin/tools/transpile_to_catala.py`,
`xl-plugin/tools/transpile_to_catala_tests.py`

---

## 18. Subscope wiring emitted forwarding lines for fields omitted by ticket 11 (regression)

**Problem:** Ticket 11 (string-no-variants → omit from scope inputs) updated only the scope-input
declaration emit. The subscope wiring emit — which generates
`definition <subscope>.<field> equals <entity>.<field>` lines when a parent invokes a sub-module
via `bind:` — was not updated. It continued iterating all CIVIL input fields of the sub-module,
including the ones ticket 11 omits from the sub-module's Catala scope. Catala correctly rejected
any `definition <subscope>.<field>` assignment targeting a nonexistent scope input.

**Observed (2026-05-21) in `ak-doh/eligibility`:**
```
"pay_frequency": unknown identifier for a variable of scope Monthly_income_estimation.MonthlyIncomeEstimationDecision
eligibility.catala_en:581.36-581.49
```

`pay_frequency` is declared in `monthly_income_estimation.civil.yaml` as `type: string, optional: true`
with no variants. Ticket 11 omitted it from the sub-module's Catala scope, but eligibility's
subscope wiring still emitted `definition monthly_income_result.pay_frequency equals household.pay_frequency`.

Because `output/clerk.toml` lists all 9 ak-doh modules under a single build target, this single
broken file blocked `xlator catala-pipeline` for every program in the domain.

**Fix (applied 2026-05-21, ticket 16):**

Extracted the omit predicate into `_scope_input_omits_field(field_def, *, tables, field_name)`
— a pure function that returns True when `type: string + optional: true + no enum variants`.

Applied it in three places:
1. **`emit_subscope_wiring()`** — skips the `definition` line for any sub-module field the predicate
   would omit, reading from the sub-module's own CIVIL tables.
2. **`check_bind_forwarding()`** — excludes omitted fields from the "required" set so the parent
   is no longer flagged for missing a field it doesn't need to forward.
3. **Scope-input emit in `emit_declarations()`** — replaced the inline `elif field_def.get("optional"): continue`
   with a call to the shared predicate.

Both emit paths now read from the same predicate; future changes to the omit rule (e.g. ticket 15's
additional cases) will automatically apply to subscope wiring, bind validation, and scope-input
declaration.

**File:** `xl-plugin/tools/transpile_to_catala.py`

---

## 17. `/update-ruleset` aborts when guidance.yaml declares new modules not yet extracted

**Problem:** When the user adds a new sub-module to `ruleset_modules:` in `guidance.yaml`
and runs `/update-ruleset`, `SP-ResolveRulesetModules` step 3 emitted a warning and aborted
because the new module was not present in `extraction-manifest.yaml`. The user had no path
forward other than running `/extract-ruleset` manually from the CLI.

**Root cause (two layers):**

Layer 1 (app-side): `_resolve_ruleset_command()` in `app/routes/sessions/_handlers.py`
picked `/update-ruleset` whenever _any_ `*.civil.yaml` existed, without checking whether
all guidance modules had been extracted to disk.

Layer 2 (plugin-side): `SP-ResolveRulesetModules` step 3 aborted on new modules rather than
continuing with mixed extract/update semantics.

**Fix (applied 2026-05-21, ticket 08):**

Layer 1: `_resolve_ruleset_command()` now compares the set of sub-module names declared in
`guidance.yaml` against the set of `*.civil.yaml` stems on disk. If any declared module is
absent from disk, it routes to `/extract-ruleset` instead of `/update-ruleset`. Two new pure
helpers were added to `app/services/projects.py`:
- `guidance_module_names(domains_dir, slug) -> frozenset[str]` — reads sub-module names from
  `guidance.yaml` (excludes `role: main`).
- `civil_module_names_on_disk(domains_dir, slug) -> frozenset[str]` — returns module names
  with an existing `*.civil.yaml` in specs/.

Layer 2: `SP-ResolveRulesetModules` step 3 now partitions entries into `existing` (in manifest)
and `new` (not in manifest). When new entries exist it emits an `:::important` notice describing
mixed-context behavior and assigns per-entry context (`'extract'` for new, `'update'` for
existing) rather than aborting. Step 4 is updated to run binding confirmation for any entry
with per-entry context `'extract'`, even when the top-level context is `'update'`.

**Files:** `app/routes/sessions/_handlers.py`, `app/services/projects.py`,
`xl-plugin/core/ruleset-shared.md`

---

## 18. `AskUserQuestion` called in non-interactive subprocess — user sees "declined" without ever seeing a prompt

**Ticket:** 12

**Symptom:** When a skill needed to ask the user a question (e.g., `/xl:create-demo ak-doh`
choosing which module to build), the chat panel showed "I declined to answer" with no preceding
prompt. The user never saw the question.

**Root cause:** `CLAUDE.md` had an `## AskUserQuestion` section that instructed Claude to use
the built-in `AskUserQuestion` tool with `[a]`/`[b]`/`[c]` option formatting. However,
PolicyBridge invokes Claude Code as a headless subprocess (`stdin=DEVNULL`), so `AskUserQuestion`
cannot display a prompt. Its response in that environment resolves to "user declined", and Claude
incorporates that into its reply.

**Fix (applied 2026-05-21, ticket 12):**

Layer 1: Replaced `## AskUserQuestion` in `xl-plugin/CLAUDE.md` with `## Asking the user a
question`, which explicitly forbids `AskUserQuestion` and requires `:::user_input` fence blocks
instead. Includes a worked example with `[a]`/`[b]`/`[c]` option format and a reference to
`core/output-fencing.md`.

Layer 2: Added `--disallowedTools AskUserQuestion` to the `claude` subprocess argv in
`app/clients/claude_code.py`. Defense-in-depth: even if a skill author drifts back to
`AskUserQuestion`, the tool is unavailable and Claude must fall back to fence blocks.

Layer 3: Audited all `xl-plugin/skills/*/SKILL.md` files for loose "prompt the user to choose"
language without explicit fence block instructions. Updated all 11 affected skills
(`create-demo`, `rego-create-demo`, `expand-tests`, `create-tests`, `extract-test-cases`,
`review-ruleset`, `extract-ruleset`, `update-ruleset`, `transpile-and-test`,
`rego-transpile-and-test`, `convert-doc`) to say "emit a `:::user_input` fence block" and
"Do NOT use AskUserQuestion."

**Files:** `xl-plugin/CLAUDE.md`, `app/clients/claude_code.py`, and all 11 skill SKILL.md files
listed above.

---

## 19. `catala-pipeline <program>` doesn't detect stale sibling Catala files in shared `_build/`

**Problem:** `xlator catala-pipeline <domain> <program>` re-transpiles only the named program,
but `output/clerk.toml` groups every program in the domain into one ninja build target. The
OCaml build phase therefore reads every `*.catala_en` in the output directory — including
sibling files that may be older than the CIVIL specs or sub-modules they reference. When a
sibling is stale, the build fails with an error pointing at the stale Catala, even though the
program the user just transpiled is fine. The user has no signal that the failure is staleness
rather than a defect in the program they ran.

**Observed (2026-05-21) in `ak-doh`:**

Sequence of events from `domains/ak-doh/logs/session.jsonl`:

| Time | Event |
|------|-------|
| 10:51 | Ticket 11 commit lands (omit string-no-variants from scope inputs) |
| 11:06 | `eligibility.catala_en` regenerated — still emits stale subscope wiring line for `pay_frequency` (ticket 16 not yet in) |
| 11:13 | `monthly_income_estimation.catala_en` regenerated — correctly omits `pay_frequency` input |
| 11:55 | Ticket 16 commit lands (subscope wiring fix) |
| 13:47–15:21 | User runs `catala-pipeline ak-doh <other_program>` 4× — each succeeds at transpile but fails at OCaml build with `"pay_frequency": unknown identifier ... eligibility.catala_en:581.36-581.49` |

The user spent multiple iterations debugging `resource_reasonable_compatibility`,
`self_employment_income`, `unearned_income_classification`, and `unearned_income_exclusions`
before recognizing that the actual fault was a stale `eligibility.catala_en` produced 49
minutes before the ticket-16 wiring fix landed. None of the four named programs reference
`pay_frequency` — the failure mode was misleading.

**Why the existing flow misses this:** `catala-pipeline` calls `catala-transpile` for one
program, then invokes clerk (which runs the OCaml build) against the shared `_build/`. The
OCaml build's dependency graph is based on its own intermediate artifacts, not on `.civil.yaml`
mtimes — so it happily rebuilds from a stale `.catala_en` whose source CIVIL was last touched
before the transpiler was patched.

**Fix (applied 2026-05-21, ticket 17):**

After `catala-transpile` runs (so the named program's `.catala_en` is fresh), `catala-pipeline`
now calls `stale_catala_files()` — a pure function in `catala_pipeline_checks.py` that scans
every `*.catala_en` in `output/` and checks two mtime vectors per file:

1. **civil-newer:** the `.civil.yaml` source is newer than the `.catala_en`
2. **transpiler-newer:** `transpile_to_catala.py` is newer than the `.catala_en`

If any stale files are found, the pipeline fails immediately (before invoking clerk) and emits
one error line per stale file naming the file, the staleness reason, and the exact
copy-paste-runnable command to fix it:

```
ERR Stale eligibility.catala_en (transpiler-newer). Run: xlator catala-transpile ak-doh eligibility
ERR Pre-build staleness check failed. Re-transpile the listed programs.
```

The check is fail-fast rather than warn-and-continue because the resulting OCaml error is
misleading (it points at a line in the stale file, not the program the user ran).

**Files:** `xl-plugin/tools/catala_pipeline_checks.py` (new),
`xl-plugin/tools/xlator.py` (`cmd_catala_pipeline`),
`xl-plugin/tools/test_catala_pipeline_staleness.py` (10 new unit tests)

---

## Fix #20 — Test transpiler's hardcoded date/int defaults fall outside table key value sets

**Problem:** When a CIVIL field is `optional: true` AND used as a table key, the test transpiler
defaulted the field using `default_value_for_type` (e.g. `|2020-01-01|` for `date`, `0` for
`int`). If that value does not appear in any row of the table, Catala evaluates the rule with no
matching entry and fails with "no applicable rule to define this variable in this situation" — even
for tests that are not asserting on that table's output, because Catala evaluates all scope
outputs whenever any output is asserted.

**Observed in `ak-doh/program_standards_lookup`:** 20 of 24 tests failed at
`expanded_refused_cash_income_limit` because `effective_date` (optional, table key) defaulted to
`|2020-01-01|`, which does not appear in the `expanded_refused_cash_income_limits` table (rows
cover only `2024-01-01`, `2025-01-01`, `2026-01-01`).

**Fix (applied 2026-05-21, ticket 19):**

`build_field_type_map()` now collects a `table_key_defaults: {field_name: first_row_raw_value}`
map by scanning every table's `key:` columns and recording the first row's value. This is
independent of `enum_variants` (which covers string fields) and applies to any type (date, int,
float).

`emit_field_value()` now prefers `table_key_defaults[field_name]` over `default_value_for_type`
in both the optional-field and required-but-missing-field branches. The raw first-row value is
converted through `value_to_catala` (so dates get `|YYYY-MM-DD|` framing, ints get string-cast).
When the field is provided by the test case, behavior is unchanged.

Symmetric with the existing enum-variant default selection for string fields: table-key membership
is another form of valid-value enumeration, generalized to non-string types.

**File:** `xl-plugin/tools/transpile_to_catala_tests.py`

---

## Fix #19 — Test transpiler omits cross-module enum fields (regression from ticket 15)

**Symptom (2026-05-21, ticket 18):**

`xlator catala-pipeline ak-doh eligibility` failed the OCaml build with:

```
Missing field(s) for structure Eligibility.Household: "household_type"
eligibility_tests.catala_en:<each test scope>
```

All 17 emitted eligibility test scopes failed with the same shape. The field `household_type`
(and `living_arrangement`) was present in every YAML test case but absent from every emitted
test scope. The OCaml compiler rejected every test scope at typecheck time.

**Root cause:**

Ticket 15 (cross-module type resolution, 2026-05-21) taught the **main** transpiler to detect
string fields that are enum keys in a sub-module's `tables:` and emit them with a qualified
Catala type (`Program_standards_lookup.HouseholdType`). The **test** transpiler was not updated.

In `transpile_to_catala_tests.py`, `build_field_type_map()` only scanned the current module's
local `tables:` and `values:` to populate `enum_variants`. A required `type: string` field
with no local enum variants hit the early-out at `emit_field_value()`:

```python
if civil_type == "string" and not valid_variants:
    return None, None  # silently skipped
```

This silently dropped the field from the struct literal, producing an incomplete Catala struct
that the compiler rejected.

**Fix (applied 2026-05-21, ticket 18):**

1. `build_field_type_map(civil_doc, sub_module_docs=None)` now accepts sibling sub-module docs
   and calls `build_cross_module_enums(sub_module_docs)` (imported from `transpile_to_catala`).
   For cross-module string enum fields, `enum_variants[field_name]` is populated with
   `{raw_value: "ModulePrefix.RawValue"}` (e.g. `{"A1E": "Program_standards_lookup.A1E"}`).

2. The `transpile()` entrypoint now loads sub-module docs from sibling CIVIL files (scanning
   `computed:` invoke fields, mirroring the main transpiler's `sub_module_docs` loading).

3. The `emit_field_value()` early-out is no longer unconditional. It now skips only when the
   field is **optional** (matching the main transpiler's ticket-11 omit rule). Required
   string fields with no variants raise `ValueError` — fail-fast rather than silent omit.

**Emit form:** cross-module table-derived variants emit as `ModulePrefix.RawVariant`
(e.g. `Program_standards_lookup.A1E`), matching the Catala constructor form declared in
the sub-module.

**Files:** `xl-plugin/tools/transpile_to_catala_tests.py` (`build_field_type_map`,
`emit_field_value`, `transpile`),
`xl-plugin/tools/test_transpile_catala_tests.py` (6 new tests, 1 updated)

---

## Fix #21b — `_format_key_condition` silently emits bare dates; fail fast on unsupported types

**Symptom (2026-05-21, ticket 21):**

`xlator catala-pipeline ak-doh program_standards_lookup` failed the OCaml build:

```
Syntax error at "-01": unexpected token.
428 │     under condition household_type with pattern A1E and effective_date = 2024-01-01
```

Catala requires date literals in `|YYYY-MM-DD|` fence form. Bare `2024-01-01` is a syntax error.

**Root cause:**

The in-tree `_format_key_condition` already had the date branch (`isinstance(datetime.date)`
→ `|YYYY-MM-DD|` form), but the `import datetime` was function-scoped. The immediate symptom
was caused by cached-plugin staleness (the marketplace cache was at an older version without
the date branch); the version bump to 2.12.0 forced a cache refresh. The remaining gap:
the silent fallthrough `return f"{key_var} = {key_val}"` would still produce broken Catala
for any value type not explicitly handled, giving a confusing OCaml error rather than a clear
Python message at transpile time.

**Fix (applied 2026-05-21, ticket 21):**

- Moved `import datetime` from function-local to module-level (avoids any namespace
  ambiguity if `datetime` is patched or shadowed).
- Added an explicit `isinstance(key_val, (int, float))` branch for numeric types.
- Replaced the silent fallthrough with `raise ValueError(...)` that names the offending
  type, variable, and value — so authors get a clear message at transpile time instead
  of a cryptic OCaml syntax error.

**Files:** `xl-plugin/tools/transpile_to_catala.py` (`_format_key_condition`),
`xl-plugin/tools/test_format_key_condition.py` (8 new unit tests: str, date, datetime,
int, float, unsupported-type ValueError, yaml-loaded date row, compound condition)

---

## Fix #21 — `catala-pipeline <program>` misattributes OCaml build failures to the requested module

**Symptom (2026-05-21, ticket 22):**

`xlator catala-pipeline ak-doh earned_income_exclusion_chain` exited non-zero with:

```
✓ earned_income_exclusion_chain.civil.yaml is valid CIVIL
✓ Transpiled to ak-doh/output/earned_income_exclusion_chain.catala_en
✓ Wrote ...clerk.toml
✓ Wrote ...earned_income_exclusion_chain_meta.py
OK  Wrote 14 test scope(s) → ...earned_income_exclusion_chain_tests.catala_en
FAILED: [code=123] _build/ocaml/Resource_reasonable_compatibility.ml ...
┌─[ERROR]─ 1/4 ─
│  Syntax error at "(": the 'sum' operator must be followed by the type to be summed
├─➤ resource_reasonable_compatibility.catala_en:55.8-55.9:
│ 55 │     sum(client_stated_resources_per_account)
└─ Resource_reasonable_compatibility
ninja: build stopped: cannot make progress due to previous errors.
```

The requested module (`earned_income_exclusion_chain`) transpiled and tested cleanly. The
failure was in a sibling module (`resource_reasonable_compatibility`) that shares the same
ninja build target. Without an explicit attribution message, the user spent significant time
debugging `earned_income_exclusion_chain` before reading the OCaml error path closely enough
to notice it pointed at a different file.

**Root cause:**

`output/clerk.toml` groups every program in the domain into one ninja build target (required
for cross-module OCaml type resolution). When ninja invokes `catala ocaml` for the shared
build, every `.catala_en` in the directory is compiled. Any one failing breaks the whole build.
`cmd_catala_test` previously called `clerk test` without capturing output and passed the raw
non-zero exit code up — no analysis of which module caused it.

**Fix (applied 2026-05-21, ticket 22):**

`cmd_catala_test` now accepts an optional `requested_module` keyword argument. When called
from `cmd_catala_pipeline` (which passes `module` as `requested_module`), the function:

1. Runs `clerk test` with `capture_output=True` and re-prints the combined output immediately
   so the existing OCaml error blocks remain visible.
2. On non-zero exit, calls `attribute_errors()` — a new pure function in
   `catala_pipeline_checks.py` — which parses `├─➤ <file>.catala_en:<line>` pointers from
   each error block and groups blocks by module name.
3. Calls `format_attribution_summary()` (also in `catala_pipeline_checks.py`) and prints the
   resulting `:::important` block before exiting.

**Example output when requested module is clean:**

```
:::important
Build failed — but your requested module (earned_income_exclusion_chain) compiled cleanly.

Failure is in OTHER modules sharing the same Catala build target:

  resource_reasonable_compatibility — 2 error(s):
    Syntax error at "(": the 'sum' operator must be followed by the type to be summed
  program_standards_lookup — 2 error(s):
    Syntax error at "-01": unexpected token

Your module's transpile artifacts are valid:
  ✓ earned_income_exclusion_chain.catala_en
  ✓ earned_income_exclusion_chain_meta.py
  ✓ tests/earned_income_exclusion_chain_tests.catala_en
:::
```

Exit code semantics are preserved (still non-zero when any build target fails).

**Files:** `xl-plugin/tools/catala_pipeline_checks.py` (`attribute_errors`,
`format_attribution_summary`), `xl-plugin/tools/xlator.py` (`cmd_catala_test`,
`cmd_catala_pipeline`),
`xl-plugin/tools/test_catala_pipeline_attribution.py` (18 new unit tests)

## Fix #24 — Bare integer literals in money-typed conditional branches transpile to invalid Catala

**Symptom (2026-05-26, ah-doh2 / medicaid_income_exceptions.civil.yaml line 133):**

A `computed:` field of `type: money` had a nested conditional whose inner `else` branch was
the integer literal `0`:

```yaml
section_1619_ssi_disregarded:
  type: money
  ...
  conditional:
    if: "Household.is_section_1619_eligible"
    then: "if Household.months_since_institutionalization <= 2 then Household.ssi_amount else 0"
    else: "0"
```

The outer `else: "0"` was already auto-coerced by Step 11 in `translate_expr_to_catala`
(`result == "0"` → `"$0"` when `field_type == "money"`). The inner `else 0` survived
verbatim because Step 11 only matches when the entire result is `"0"`, and Steps 13a/13b
only coerce integers that are operands of `+`/`-`. Catala then rejected the file at compile
time with a branch-type mismatch (`money` vs `integer`).

**Root cause:**

CIVIL conditional expressions are stored as opaque strings (`"if X then Y else Z"`) and emitted
to Catala verbatim by `translate_expr_to_catala`. The existing money-context coercion steps
(11, 13a, 13b) handle three narrow shapes — entire-result-is-`"0"`, integer as left operand
of `+`/`-`, integer as right operand of `+`/`-` — but they don't cover integers in `then`/`else`
position inside an embedded conditional. Catala's strict type checker rejects branch-type
mismatches at the emitted `.catala_en`, not at the CIVIL source.

**Symptom patch (applied 2026-05-26):**

`ah-doh2/specs/medicaid_income_exceptions.civil.yaml:133` — explicit `$0` in the inner branch:

```yaml
# Before
then: "if Household.months_since_institutionalization <= 2 then Household.ssi_amount else 0"
# After
then: "if Household.months_since_institutionalization <= 2 then Household.ssi_amount else $0"
```

**Fuller fix — `translate_expr_to_catala` Step 13c (auto-coerce bare-int in `then`/`else`):**

Extends the existing money-context coercion pattern (Steps 11, 13a, 13b) with a new step that
catches the conditional-branch case. The regex matches `\b(then|else)\s+(\d+)\b` with the
same negative guards as Step 13b (not preceded by `$`, not followed by `, . digit %`) and
rewrites to `$N` form. This is auto-coercion rather than fail-fast because the conversion is
unambiguous and safe in money context — identical to how Steps 11/13a/13b already handle
bare integers in arithmetic positions.

The new step lives between Step 13b and Step 13.9 in the translation pipeline. Word boundary
ensures it won't match inside identifiers like `else_value`.

**Coverage (eight new unit tests in `test_transpile_money_branch_coercion.py`):**

- `then ssi_amount else 0` → `then ssi_amount else $0`
- `then 100 else exclusion_amount` → `then $100 else exclusion_amount`
- `then count else 0` with `field_type="int"` → unchanged
- `then rate else 0.5` → unchanged (decimal literal preserved)
- `then ssi_amount else $0` → unchanged (idempotent)
- `else_value + 10` → identifier with `else` prefix not touched
- Nested `if a then (if b then 100 else 0) else 0` → both inner and outer integer branches coerced
- Standalone `"0"` → still coerced by Step 11 (no regression)

**Relationship to Fix #23:**

This is a partial implementation of Fix #23's proposed check #2 (conditional-branch
type-check). It covers the most common mismatch — bare integer in money context — by
auto-coercion rather than fail-fast validation. The broader proposal in #23 (parse-and-typecheck
of branches, with fail-fast for non-zero/non-money mismatches like `string` vs `money` or
`date` vs `money`) is still deferred. When that lands, this step can either remain (as a
lenient pre-pass that absorbs the trivial case) or be removed in favor of the general check.

**Files:**

- `xl-plugin/tools/transpile_to_catala.py` — added Step 13c after Step 13b in
  `translate_expr_to_catala`
- `xl-plugin/tools/test_transpile_money_branch_coercion.py` (new) — 8 unit tests
- `<ah-doh2-domain>/specs/medicaid_income_exceptions.civil.yaml` — explicit `$0` in inner
  conditional branch (symptom patch)

---

## Fix #25 — Broader conditional branch type-check (#23.2)

**Symptom (2026-05-27):**

Fix #24's Step 13c only auto-coerces bare integer literals in `then`/`else` branch positions
when `field_type == "money"`. Non-literal mismatches — `string` vs `money`, `date` vs `money`,
integer *field reference* vs `money` — were emitted verbatim and rejected by Catala's type
checker at compile time rather than at transpile time.

**Root cause:**

The transpiler had no mechanism to infer the CIVIL type of branch expressions from field
references, so it could not detect a mismatch between `then: "Client.payment"` (money) and
`else: "Client.status_code"` (string) before emitting the `.catala_en`.

**Fix:**

Three new pure helpers added in `transpile_to_catala.py` (UTILITIES section):

- `_build_all_field_type_map(civil_doc)` — builds `{bare_field_name: civil_type}` from all
  `inputs.*` fields and `computed.*` fields in the CIVIL document.
- `_infer_civil_type(expr, field_type_map)` — classifies a simple expression (money/int/date/bool
  literals, or a single field reference) returning the CIVIL type string or `None` for complex
  expressions (arithmetic, function calls, nested if/then/else). `None` → skip check (no false
  positives).
- `_check_cond_branch_type_compat(then_raw, else_raw, field_name, field_type_map, field_type)` —
  calls `_infer_civil_type` on both raw CIVIL branch strings; raises `ValueError` with a clear,
  structured message when both are known and differ. Takes the field's declared `field_type` so
  it can apply the coercion-aware exemption described below.

Two integration points:

1. **Outer conditional (Level 1):** `emit_computed_section_catala` and
   `emit_decision_section_catala` each gained a `field_type_map` parameter. Before calling
   `translate_expr_to_catala` on a `conditional:` field, `_check_cond_branch_type_compat` is
   called on the raw CIVIL `then:` and `else:` strings. This catches top-level mismatches before
   any translation happens.

2. **Inner conditional (Level 2 — Step 14):** `translate_expr_to_catala` gained a `field_type_map`
   parameter and a new Step 14 that runs after all coercions (so Step 13c's bare-int coercions are
   complete). Step 14 scans the translated expression for `\b(then|else)\s+IDENT\b` patterns
   (guarded against arithmetic with a negative lookahead), looks up each identifier's type, and
   raises `ValueError` if two or more distinct known types appear. This catches mismatches embedded
   inside expression strings like `then: "if X then payment else status_code"`.

`transpile()` builds `field_type_map` once from the loaded CIVIL document and passes it to both
emit functions. All new parameters are optional (`None` default) — callers that omit them get the
old behavior unchanged (backward compatible).

**Coercion-aware exemption in the outer check (post-deploy fix, 2026-05-27):**

After deploying the initial fix, `medicaid_income_exceptions` failed with a false positive on
`ssa_cola_disregarded`:

```
ERROR: Field 'ssa_cola_disregarded': conditional branches have incompatible types.
  then: "Household.ssa_cola_increase_amount" → money
  else: "0" → int
```

The Level 1 outer check runs on the raw CIVIL strings **before** `translate_expr_to_catala` is
called, so it saw `else: "0"` as `int`. But `"0"` in a money-typed field is exactly the case
Step 11 auto-coerces to `"$0"` — it is not a real mismatch.

Root cause: `_check_cond_branch_type_compat` had no knowledge of the surrounding field's type
and could not distinguish a bare integer **literal** (safe: Step 11 will coerce it) from an
integer **field reference** like `"household_size"` (unsafe: cannot be auto-coerced).

Fix: `_check_cond_branch_type_compat` gained a `field_type` parameter. When
`field_type == "money"` and a branch expression is a bare integer literal (`^\d+$`), that
branch is reclassified as `"money"` before the comparison — mirroring exactly what
`translate_expr_to_catala` Step 11 / Step 13c will do downstream. Integer field references
do not match `^\d+$` and are still flagged as genuine mismatches.

Step 14 (Level 2, inner check) is unaffected: it runs on the *translated* string, after Step
13c has already rewritten `else 0` → `else $0`, so `$0` is already seen as money by the time
Step 14 scans.

**Relationship to Fix #24:**

Step 14 runs *after* Step 13c, so `else 0` → `else $0` (coercion) before the type scan. `$0`
classifies as money, matching `ssi_amount` (money) in the then-branch — no false mismatch.
Existing Step 13c tests remain green (8/8 pass). Fix #24 is fully preserved as a complementary
auto-correction pass; Fix #25 adds fail-fast validation for the cases that cannot be safely
auto-corrected.

**Coverage (48 unit tests in `test_transpile_conditional_type_check.py`):**

- `_infer_civil_type`: money/int/date/bool literals, prefixed/bare field refs, unknown fields →
  None, complex expressions → None, whitespace stripping
- `_check_cond_branch_type_compat`: compatible pairs pass; string vs money, date vs money, int
  field vs money raise `ValueError`; bare int literal in money context passes; bare int literal
  in non-money context still fails; complex branches skip
- Step 14: inner IDENT mismatch detected; arithmetic-guarded IDENT skipped; no map → no check
- Step 13c regression: `else 0` still coerced to `$0`, no false mismatch triggered
- `_build_all_field_type_map`: inputs, computed, missing type defaults to money, merged
- Emit-layer integration: `emit_computed_section_catala` raises `ValueError` on genuine mismatch;
  passes for `else: "0"` in money field (the `ssa_cola_disregarded` regression case)

**Remaining gap (still deferred — Fix #23 checks #1, #3, #4):**

Reserved-word scanner, enum case-name validator, and bind-coverage audit are still not
implemented. See Fix #23 below.

**Files:**

- `xl-plugin/tools/transpile_to_catala.py` — added `_build_all_field_type_map`,
  `_infer_civil_type`, `_check_cond_branch_type_compat`; extended `translate_expr_to_catala`
  with Step 14 + `field_type_map` param; extended `emit_computed_section_catala`,
  `emit_decision_section_catala`, `emit_rules_section_catala`, and
  `translate_condition_to_catala` with `field_type_map` param + pre-check call; wired in
  `transpile()`; `main()` catches `ValueError` and routes through `fail()`
- `xl-plugin/tools/test_transpile_conditional_type_check.py` (new) — 41 unit tests

---

## Fix #23 — Catala compile-step errors are not surfaced upstream during CIVIL validation/transpile

**Symptom (2026-05-26, ah-doh2 domain):**

`xlator catala-pipeline` on ah-doh2 surfaced four failures, none of which were caught by
`xlator validate` or by the transpiler's pre-emit checks. In each case the user only learned
about the defect after the Catala/OCaml compiler rejected the emitted `.catala_en`, with an
error message that points at the generated file rather than the CIVIL source — making it
much harder to locate and fix.

| # | File | Failure mode | Compiler error |
|---|------|-------------|----------------|
| 1 | `earned_income_exclusions.civil.yaml` | Input field named `year` collides with a Catala reserved keyword. CIVIL validation passes. | `Syntax error at "year": reserved keyword` (or similar) at the emitted scope-input declaration. |
| 2 | `medicaid_income_exceptions.civil.yaml:146` | Conditional branches have mismatched types — `then ssi_amount` is `money` but `else 0` is `integer`. CIVIL doesn't type-check expression branches. | Catala type-check error: branches of `if … then … else` have incompatible types `money` and `integer`. Fix: `else $0`. |
| 3 | `program_standards.civil.yaml:45` | Enum case literal `individual` rejected by Catala — collides with reserved word, or fails constructor-case rules (Catala constructors must begin uppercase; CIVIL author writes them as-is). | Catala parser rejects the enum case literal at the `declaration enumeration` site. |
| 4 | `medicaid_eligibility.civil.yaml` | Sub-module entity binds (`Household → DOLRecord`, `Household → ClientData`, `Household → AVSData`) require fields on the parent's entity declaration that aren't there. Same shape as Fix #12, broader cases. | `Field "<X>" does not belong to structure "Household"` — emitted at the forwarded `definition <subscope>.<field>` line. |

**Why the existing pipeline misses these:**

- **Reserved-word collisions (#1, #3):** `xlator validate` accepts any syntactically-valid CIVIL
  identifier. The transpiler then emits the identifier verbatim into Catala, where the parser
  rejects it. The memory note [Avoid Catala reserved words as CIVIL field names](../.claude/projects/-Users-bradley-workspaces-lockpicks-xlator-ruleset-builder/memory/feedback_civil_catala_reserved_words.md)
  captures this for `year` and lists known collisions (`sum`, `output`, `content`, `condition`,
  `for`, `let`, `match`, `if`, etc.), but the constraint is policy-author knowledge, not
  enforced by the toolchain.
- **Type mismatch in conditionals (#2):** CIVIL expressions are stored as opaque strings
  (`"then ssi_amount else 0"`). The transpiler emits them verbatim into Catala without parsing
  or type-checking the expression itself. Catala then catches the type error at compile time.
- **Bind fields missing on parent (#4):** Fix #12 added `check_bind_forwarding()` which detects
  this for the common case of entity-to-entity binds where the parent's entity declares a
  subset of the sub-module's fields. The recurrence here suggests either: (a) the check isn't
  running for all bind shapes (e.g. when the parent's entity is named differently from the
  sub-module's, like `Household → ClientData`), or (b) the check is running but emitting only
  a warning instead of fail-fast. Worth verifying before adding a new fix.

**Fuller fix (deferred — proposed):**

Add a CIVIL → Catala pre-flight validator that runs before any `.catala_en` is written. It
should catch all four classes upstream and emit one structured error per finding, naming the
exact CIVIL file, line, identifier, and remediation. Proposed checks:

1. **Reserved-word scanner.** Walk every CIVIL identifier (input fields, computed names,
   enum cases, constant table column names) and compare against the known Catala keyword
   list. On match, emit:
   `ERROR: <file>:<line> — identifier '<name>' collides with Catala reserved keyword.
   Rename (e.g. '<name>' → '<suggested>'). Update: this CIVIL spec, all rule expressions
   referencing it, tests, naming-manifest.`
   **Status (2026-05-27):** Harness-level scanner implemented —
   `.claude/hooks/analyze/civil_reserved_words.py` + `civil-reserved-words.sh`, wired into
   `run-all.sh` (blocking gate between Syntax and Style) and `post-write.sh` (fires on any
   `*.civil.yaml` write). Scans five key spaces: `inputs.*.fields`, `computed`, `outputs`,
   `tables.*.key[]`, `tables.*.value[]`. The `year` collision still exists in
   `program_standards.civil.yaml` and `medicaid_eligibility.civil.yaml`; the scanner will
   surface both when next run over `$DOMAINS_DIR`. xl-plugin `validate_civil.py` integration
   remains deferred.
2. **Conditional-branch type-check.** Parse `if … then X else Y` expressions and check
   whether `X` and `Y` have compatible types (using the field-type map already built by the
   transpiler). For literal-vs-typed-identifier branches like `then ssi_amount else 0`,
   detect the type mismatch and suggest the literal form (`else $0`, `else 0.0`, etc.).
   **Status: implemented (Fix #25, 2026-05-27).** Fix #24 auto-coerces bare-int branches
   to money literals in money context. Fix #25 adds fail-fast validation via
   `_build_all_field_type_map` + `_infer_civil_type` + `_check_cond_branch_type_compat`
   (outer check) and Step 14 in `translate_expr_to_catala` (inner check), covering all
   simple literal and field-reference mismatch shapes. Complex expressions (arithmetic,
   nested if/then/else) are silently skipped to avoid false positives.
3. **Enum case-name validator.** Constructor names must be Catala-valid (uppercase initial,
   not a reserved word). If CIVIL specs write lowercase enum case names, either lift the
   transpiler convention so it auto-capitalizes, or fail fast with a clear renaming suggestion.
   **Known active instance:** `program_standards.civil.yaml` — table values `"individual"`
   and `"couple"` are emitted verbatim as lowercase Catala constructors, which Catala rejects.
   No auto-capitalization or fail-fast check exists in the transpiler. See Fix #26 for the
   full bug description and proposed fix. Do not work around in CIVIL — fix in the transpiler.
4. **Bind-coverage check (audit Fix #12 scope).** Verify `check_bind_forwarding()` runs for
   all bind shapes including cross-entity binds (`Household: ClientData`) and is fail-fast
   rather than warn-and-continue.
   **Known active instance:** `medicaid_eligibility.civil.yaml` — cross-entity binds
   `Household → DOLRecord`, `Household → ClientData`, `Household → AVSData`. These have the
   same shape as the Fix #12 eligibility case but use different source/target entity names.
   Audit needed: confirm whether `check_bind_forwarding()` fires for cross-entity bind shapes
   (not just same-entity `Household → Household`), and whether it exits 1 on mismatch or only
   warns. Do this audit before adding a new fix — the existing check may already cover it.
   Not yet audited.

**Symptom patch (applied 2026-05-26 for #1 only):**

Renamed `year` → `effective_year` on `earned_income_exclusions.civil.yaml` (input field
declaration + expression usage) and matching test inputs in
`tests/earned_income_exclusions_tests.yaml`. This unblocks transpile of that one module.
Failures #2, #3, #4 remain. The same `year` collision exists in `program_standards.civil.yaml`
and `medicaid_eligibility.civil.yaml`; those are intentionally not patched here pending the
fuller fix above, which will give the user a single pass that catches every collision and
prescribes the rename.

**Files (symptom patch):** `<ah-doh2-domain>/specs/earned_income_exclusions.civil.yaml`,
`<ah-doh2-domain>/specs/tests/earned_income_exclusions_tests.yaml`

**Files (proposed fuller fix — not yet implemented):** `xl-plugin/tools/civil_validate.py`
(or a new `xl-plugin/tools/catala_preflight.py`), called from `cmd_catala_transpile` and
`cmd_validate` so the check runs in both validation-only and pipeline flows.

---

## Fix #22 — Plugin loaded via `claude --plugin-dir` instead of marketplace install

**Symptom (2026-05-22, ticket 24):**

App startup logs showed:

```
Plugin version mismatch (installed=2.5.1, bundled=2.12.0) — reinstalling.
Registered xl@xlator-ui-vendored plugin with Claude Code (project scope, startup-prewarm).
```

The "reinstalling" claim was wrong. `claude plugin install xl@xlator-ui-vendored --scope project`
is idempotent on plugin-id — when the id is already installed at any version, the CLI exits 0
with "already installed" without making any change. The running session continued loading v2.5.1
even though the vendored source was v2.12.0. Every `plugin.json` bump between those two versions
was invisible to users until they manually ran `claude plugin update xl@xlator-ui-vendored`.

**Root cause:**

`plugin_install.py:ensure_plugin_installed` checked whether `xl@xlator-ui-vendored` was already
registered and, on version mismatch, called `claude plugin install` again — which is a no-op
when the plugin id exists at any version. `claude plugin update` would have worked, but the
install machinery was managing a version-keyed cache that couldn't distinguish "needs update"
from "already current."

**Fix:**

Replaced the entire marketplace-install dance with `claude --plugin-dir <path>`, which loads
the plugin from the directory for that session only. The flag is documented in `claude --help`
and is repeatable. With `--plugin-dir` the registry is never consulted: no install, no version
check, no cached state. Bumping `plugin.json` version is reflected in the very next app restart.

**Files changed:**

- `app/clients/claude_code.py` — added `_validate_plugin_root`, `_build_session_argv` helpers;
  `_run()` now calls `_build_session_argv` which includes `--plugin-dir <claude_plugin_root>`.
- `app/services/plugin_install.py` — deleted (370 lines of install machinery, now dead code).
- `app/routes/sessions/_handlers.py` — removed `_run_global_preflight_sync`,
  `prewarm_session_preflight`; simplified `ensure_session_preflight` to only write the shim env.
- `app/main.py` — removed `prewarm_session_preflight` call from lifespan startup.
- `app/services/plugin_assets.py` — removed `_copy_marketplace_json`; stable dir now only
  contains the xl-plugin source, not a marketplace descriptor.
- `.claude-plugin/marketplace.json` — deleted (no longer consumed by anything).
- Tests updated: `test_plugin_install.py` deleted; prewarm test replaced with
  `test_ensure_session_preflight_calls_write_shim_env_file`; new
  `TestBuildSessionArgv` and `TestValidatePluginRoot` classes in `test_claude_adapter.py`.

---

## Fix #26 — Transpiler emits lowercase Catala enum cases for CIVIL table values (enum case naming bug, #23.3)

**Symptom:**

`xlator catala-pipeline` fails with a Catala constructor error when a CIVIL module's
`constants:` table (or `values:` list on a field) uses lowercase string values as enum
cases. Catala constructors must begin with an uppercase letter; the transpiler emits CIVIL
table values verbatim into `declaration enumeration` blocks without capitalizing them.

**Observed in `program_standards.civil.yaml`:**

CIVIL table uses `"individual"` and `"couple"` as household type values. The transpiler
emits:

```
declaration enumeration HouseholdType:
  -- individual
  -- couple
```

Catala rejects these: `individual` and `couple` are lowercase, violating the constructor
naming rule. If `individual` also overlaps with a Catala reserved word, the error surface
changes (parse error vs. type error), but the root cause is the same — verbatim emit of
lowercase CIVIL strings.

**Root cause:**

The enum-case emit path in `transpile_to_catala.py` iterates the unique values in a
table's key or value column and emits each as `-- <value>` in a `declaration enumeration`
block. No capitalization or identifier-validity check is applied before emit. The CIVIL
schema places no constraint on case; the transpiler assumes CIVIL values are already
Catala-valid constructors.

**Proposed fix (not yet implemented):**

In `emit_declarations()`, before emitting each enum case string, apply a normalization:

1. **Capitalize the first letter** of each enum case string (e.g. `individual` → `Individual`,
   `couple` → `Couple`). This is the minimal Catala-validity requirement.
2. **Check against `CATALA_RESERVED`** after capitalizing. If the capitalized form still
   collides (unlikely — Catala keywords are lowercase), emit a fail-fast `ValueError` naming
   the CIVIL file, field, and value, and suggesting a rename.
3. Apply the same normalization anywhere a CIVIL enum case value is referenced in an
   expression: pattern-match arms in `match … with`, literal comparisons. These appear in
   `translate_expr_to_catala` and the rule/condition emit paths.

The matching change in the test transpiler (`transpile_to_catala_tests.py`):
`value_to_catala` for string enum fields must emit the capitalized form (`Individual`)
rather than the raw CIVIL value (`individual`), so test struct literals match the
`declaration enumeration` forms.

**Scope:**

Do not work around this in CIVIL files (by writing `Individual` / `Couple` as the CIVIL
values) — CIVIL table values should use whatever case is natural for the domain. The
transpiler's job is to produce valid Catala. Patch in the transpiler, not in the specs.

**Files (proposed):** `xl-plugin/tools/transpile_to_catala.py` (emit_declarations enum
case emit + translate_expr_to_catala pattern arms), `xl-plugin/tools/transpile_to_catala_tests.py`
(value_to_catala string enum path)

---

## Fix #27 — Sub-module bind forwarding fails when parent entity lacks optional sub-module fields (#23.4)

**Symptom (2026-05-27, ah-doh2 / medicaid_eligibility.civil.yaml):**

`xlator catala-transpile ah-doh2 medicaid_eligibility` exited 1 with errors of this shape:

```
ERROR: Sub-module 'medicaid_resources' (bound as 'Household' → 'AVSData') requires fields
not declared on parent's inputs.AVSData.fields: ['alaska_native_real_property_value',
'client_stated_resources', 'dingell_act_land_value', 'excess_home_equity_threshold',
'home_equity', 'iim_account_balance', 'is_institutional'].
```

The same error fired for the `Household → ClientData` bind (missing the AVS-side fields
`avs_matched_amounts`, `client_matched_amounts`, `new_avs_accounts`). Both bind errors
blocked transpile entirely; no `.catala_en` was written.

**Root cause:**

`medicaid_resources` is a reusable module whose `Household` entity covers both the
client-stated resource view and the AVS electronic verification view. Every field on that
entity is declared `optional: true` in CIVIL, because no single caller ever provides all of
them — `ClientData` has the client-side exclusion fields, `AVSData` has the matched-account
fields, and neither has the other's fields. The module's own expressions handle zero
values gracefully.

`check_bind_forwarding()` treated ALL non-string-no-variants sub-module fields as
required of the parent entity, regardless of their `optional: true` annotation. This meant
that any reusable module designed to be invoked with partial field coverage would always
fail validation, even though the sub-module's policy logic explicitly accommodates the
missing fields.

`emit_subscope_wiring()` had a symmetric problem: it would emit forwarding lines like
`definition avs_resources_result.iim_account_balance equals avs_data.iim_account_balance`
for ALL declared fields, even when `avs_data` (the `AVSData` struct) had no
`iim_account_balance` member. This would produce a Catala type error — referencing a
nonexistent struct field — even if bind validation had somehow passed.

A third bug (latent, not yet observable): `emit_subscope_wiring()` shadowed the outer
loop variable `field_def` with the inner loop variable, causing the `description` and
`source` metadata carried in the wiring chunk tuples to come from the last sub-entity
field rather than from the invoke field itself. The prose heading in the generated
`.catala_en` was therefore wrong for any module with more than one sub-entity field.

**Fix (applied 2026-05-27):**

**Part 1 — `check_bind_forwarding()` (validation):**

Added a second exclusion condition to the `required_sub_fields` set comprehension:

```python
# Before:
required_sub_fields = {
    fname
    for fname, fdef in sub_entity_fields.items()
    if not _scope_input_omits_field(fdef, tables=sub_tables, field_name=fname)
}

# After:
required_sub_fields = {
    fname
    for fname, fdef in sub_entity_fields.items()
    if not _scope_input_omits_field(fdef, tables=sub_tables, field_name=fname)
    if not (isinstance(fdef, dict) and fdef.get("optional"))
}
```

`optional: true` fields in the sub-module are no longer required of the parent entity.
Callers that only provide a subset of the sub-module's inputs are now valid, as long as
all `required` (non-optional) sub-module fields are covered.

**Part 2 — `emit_subscope_wiring()` (code generation):**

Added `parent_inputs: dict = None` parameter (backward-compatible). When provided, the
function consults the parent entity's field set for each bind pair. For each sub-module
field that is `optional: true` AND absent from the parent entity, it emits a
zero/empty-default wiring line instead of a forwarding reference:

| Sub-module field type | Catala default emitted |
|-----------------------|------------------------|
| `money`               | `$0`                   |
| `int`                 | `0`                    |
| `float`               | `0.0`                  |
| `bool`                | `false`                |
| `list` / `set`        | `[]`                   |

This keeps the Catala struct fully initialized without referencing a field the parent
entity does not have. The new `_default_catala_literal(civil_type)` helper encapsulates
the type→literal mapping.

When the parent entity DOES have the optional field, the existing forwarding path is used
unchanged. When `parent_inputs` is `None` (tests that call `emit_subscope_wiring`
directly without a parent doc), the function falls back to the old behavior.

The `transpile()` call site now passes `parent_inputs=doc.get("inputs", {})` to
`emit_subscope_wiring`, activating the zero-default path for all end-to-end transpile runs.

**Part 3 — variable-shadowing fix:**

The inner loop variable in `emit_subscope_wiring` was renamed from `field_def` to
`sub_field_def`, and the outer loop variable from `field_def` to `invoke_field_def`. The
chunk tuple now correctly captures `invoke_field_def.get("description")` and
`invoke_field_def.get("source")` rather than the last sub-entity field's metadata.

**New tests (8 in `test_transpile_bind_forwarding.py`):**

- `test_check_bind_forwarding_ignores_optional_money_field` — optional money field absent from parent → no error
- `test_check_bind_forwarding_ignores_optional_bool_and_list_fields` — optional bool/list absent → no error
- `test_check_bind_forwarding_still_requires_non_optional_fields` — required (non-optional) field still enforced; optional field in same sub-module not mentioned in error
- `test_subscope_wiring_emits_zero_for_absent_optional_money_field` — `$0` default for money
- `test_subscope_wiring_emits_empty_list_for_absent_optional_list_field` — `[]` default for list
- `test_subscope_wiring_emits_false_for_absent_optional_bool_field` — `false` default for bool
- `test_subscope_wiring_forwards_optional_field_when_parent_has_it` — forwarding still used when parent entity has the field
- `test_transpile_optional_absent_fields_get_zero_defaults` — integration: transpile() with mixed present/absent optional fields produces correct wiring and no exit(1)

All 26 bind-forwarding tests pass. 18 pre-existing tests are unchanged.

**Scope of this fix vs. remaining `medicaid_eligibility` blockers:**

Fix #27 resolves all cases where the missing sub-module fields are `optional: true`. For
`medicaid_eligibility`, this means:

| Bind | Missing field | Optional? | Status after Fix #27 |
|------|--------------|-----------|----------------------|
| `Household → AVSData` (medicaid_resources) | `alaska_native_real_property_value`, `client_stated_resources`, `dingell_act_land_value`, `excess_home_equity_threshold`, `home_equity`, `iim_account_balance`, `is_institutional` | All `optional: true` | ✓ Fixed — zero defaults emitted |
| `Household → ClientData` (medicaid_resources) | `avs_matched_amounts`, `client_matched_amounts`, `new_avs_accounts` | All `optional: true` | ✓ Fixed — zero defaults emitted |
| `Household → ClientData` (medicaid_income_exceptions) | `pickle_reduction_factor` | `optional: true` | ✓ Fixed — `0.0` default emitted |
| `Household → ClientData` (medicaid_income_exceptions) | `gross_earned_income` | **required** | ✗ Still blocks — CIVIL spec issue |
| `Household → ClientData` (unearned_income_exclusions) | `gross_unearned_income` | **required** | ✗ Still blocks — CIVIL spec issue |
| `Household → ClientData` (reasonable_compatibility_check) | `dol_quarter_total` | **required** | ✗ Still blocks — CIVIL spec issue |

The three remaining failures are genuine CIVIL spec modeling errors, not transpiler bugs.
In each case the sub-module requires a field as a non-optional input, but the parent's
bound entity does not declare it because:

- `gross_earned_income` and `gross_unearned_income` are **computed in the parent** (via
  `earned_class_result.gross_earned_income` and `unearned_class_result.gross_unearned_income`).
  The entity-bind model only forwards parent *input* fields; computed values are not available
  to forward.
- `dol_quarter_total` lives on the parent's `DOLRecord` entity, not on `ClientData`, so
  binding `Household: ClientData` cannot reach it.

**Fix (CIVIL spec — preferred):** Switch from entity-bind shorthand to field-by-field
`invoke:` for the three affected sub-modules. Explicitly map each required sub-module input
to its source (computed field or cross-entity field) in the parent's invoke block. This is
the principled fix — `check_bind_forwarding` is working correctly and the CIVIL spec
should be regenerated with field-by-field forwarding here. See Fix #28 for the DSL
extension that makes this expressible.

**Files:**

- `xl-plugin/tools/transpile_to_catala.py` — `check_bind_forwarding` (optional exclusion),
  `_default_catala_literal` (new helper), `emit_subscope_wiring` (zero-default path,
  variable-shadow fix, `parent_inputs` param), `transpile` (pass `parent_inputs`)
- `xl-plugin/tools/test_transpile_bind_forwarding.py` — 8 new tests

---

## Fix #28 — CIVIL v10: `field_bind:` — forward computed or cross-entity values to sub-module inputs

**Motivation:**

The entity-bind shorthand (`bind: {SubEntity: ParentEntity}`) covers the common case where
all required sub-module input fields exist verbatim on a parent input entity. Three blockers
remain in `medicaid_eligibility` (see Fix #27) where this is not true:

| Sub-module | Field needed | Why entity-bind can't reach it |
|------------|-------------|-------------------------------|
| `medicaid_income_exceptions` | `gross_earned_income` | Computed in parent via `earned_class_result.gross_earned_income` (output of `earned_income_classification` subscope) — not an input field on `ClientData` |
| `unearned_income_exclusions` | `gross_unearned_income` | Computed in parent via `unearned_class_result.gross_unearned_income` (output of `unearned_income_classification` subscope) |
| `reasonable_compatibility_check` | `dol_quarter_total` | On parent's `DOLRecord` entity, not on the `ClientData` entity being bound |

The CIVIL DSL has no syntax to express "forward this computed subscope output (or
cross-entity input) as input to another sub-module." The fix is a DSL extension, not a
workaround in the CIVIL spec.

**New syntax — `field_bind:` under `invoke:`:**

```yaml
computed:
  medicaid_exceptions_result:
    type: object
    module: medicaid_income_exceptions
    invoke:
      bind:
        Household: ClientData           # entity-bind: forwards matching input fields
      field_bind:
        Household:
          gross_earned_income: "earned_class_result.gross_earned_income"

  unearned_exclusions_result:
    type: object
    module: unearned_income_exclusions
    invoke:
      bind:
        Household: ClientData
      field_bind:
        Household:
          gross_unearned_income: "unearned_class_result.gross_unearned_income"

  reasonable_compat_result:
    type: object
    module: reasonable_compatibility_check
    invoke:
      bind:
        Household: ClientData
      field_bind:
        Household:
          dol_quarter_total: "DOLRecord.dol_quarter_total"
```

`field_bind:` is a sibling key to `bind:` under `invoke:`. Its structure mirrors `bind:`
at the top level (entity name as key) but maps individual field names to CIVIL expression
strings at the second level.

**Semantics:**

- `field_bind:` and `bind:` are additive — `bind:` forwards matching input fields in bulk;
  `field_bind:` adds explicit per-field overrides for fields that entity-bind cannot reach.
- The value of each `field_bind:` entry is a CIVIL expression evaluated in the **parent**
  module's scope: field references, subscope output references, cross-entity inputs, and
  constants are all valid. The expression goes through `translate_expr_to_catala` with the
  parent's context (fact_entities, invoke_bound_entities, constants, tables).
- Fields named in `field_bind:` are excluded from `check_bind_forwarding`'s "required but
  missing" check — they're explicitly provided, so no entity-bind coverage is needed.
- If a field appears in both `bind:` and `field_bind:`, `field_bind:` takes precedence
  (its wiring line is emitted last and Catala's priority-based evaluation resolves it).

**Catala emit:**

The expression value is translated and emitted as a subscope definition alongside the
entity-bind wiring:

```catala
scope MedicaidEligibilityDecision:
  # from bind: {Household: ClientData} — entity-forwarded fields
  definition medicaid_exceptions_result.current_year_ss_amount equals household.current_year_ss_amount
  # ...
  # from field_bind: {Household: {gross_earned_income: ...}}
  definition medicaid_exceptions_result.gross_earned_income equals earned_class_result.gross_earned_income
```

For `"DOLRecord.dol_quarter_total"`: `DOLRecord` is not invoke-bound in the parent, so
its prefix is stripped by `translate_expr_to_catala` → `dol_quarter_total` (the flat scope
input variable).

**Implementation plan:**

1. **`check_bind_forwarding()`** — collect the set of fields covered by `field_bind:` for
   each bind pair and subtract them from `required_sub_fields` before the missing-field
   comparison. A field explicitly provided via `field_bind:` is never flagged as missing.

2. **`emit_subscope_wiring()`** — after emitting `bind:`-derived definition lines, iterate
   `field_bind:` entries. For each `(sub_entity, {sub_field: parent_expr})`, call
   `translate_expr_to_catala(parent_expr, ...)` with the parent's context and emit
   `definition <subscope_var>.<sub_field> equals <catala_expr>`.

   Requires a new `parent_context: dict = None` parameter carrying
   `{constants, tables, fact_entities, invoke_bound_entities}` from the parent. Called
   with the full parent context from `transpile()`.

3. **CIVIL schema (`civil_schema.py`)** — add `field_bind: dict[str, dict[str, str]] | None`
   to the `InvokeField` model. No required fields are added; old CIVIL files that omit
   `field_bind:` parse identically.

4. **CIVIL spec (`medicaid_eligibility.civil.yaml`)** — add `field_bind:` entries under the
   three affected `computed:` invoke fields as shown above.

5. **`README-dev.md` Architecture Notes** — document CIVIL v10:
   `v10: Add field_bind: under invoke: to forward computed values or cross-entity input
   fields to sub-module inputs, complementing the entity-level bind: shorthand.`

**Tests to write:**

Unit tests in `test_transpile_bind_forwarding.py`:
- `test_check_bind_forwarding_field_bind_satisfies_required_field` — a required non-optional
  sub-module field covered by `field_bind:` does not appear in errors
- `test_check_bind_forwarding_field_bind_does_not_suppress_other_missing` — only the
  field_bind-covered field is satisfied; other missing required fields are still flagged
- `test_subscope_wiring_emits_field_bind_expression` — `field_bind:` value is translated
  and emitted as a definition line
- `test_subscope_wiring_field_bind_strips_entity_prefix` — cross-entity reference like
  `DOLRecord.dol_quarter_total` strips `DOLRecord.` and emits `dol_quarter_total`
- `test_subscope_wiring_field_bind_and_bind_combined` — both `bind:` and `field_bind:`
  present; entity-forwarded fields and field_bind-explicit fields both appear in output
- Integration: `test_transpile_field_bind_resolves_computed_value` — end-to-end transpile
  with a `field_bind:` entry succeeds and emits the correct definition line

---

## Fix #29 — UPPER_SNAKE_CASE enum values mangled to PascalCase at reference sites

**Problem:** CIVIL specs that declare enum variants in UPPER_SNAKE_CASE (e.g. `QMB`, `SLMB`,
`SLMB_PLUS`, `QDWI`, `SPECIAL_LTC`) are valid and pass schema validation. At declaration sites
for `type: string` fields with `values:`, the transpiler already used `_to_catala_constructor`
which preserves uppercase-initial values — so `-- QMB` and `-- SLMB_PLUS` appeared correctly
in the Catala output. But three other sites still used `snake_to_pascal` directly, which
capitalizes only the first letter of each `_`-separated segment:

- `snake_to_pascal("SLMB_PLUS")` → `"SlmbPlus"` ✗
- `snake_to_pascal("QMB")` → `"Qmb"` ✗

Affected sites:

| File | Location | Symptom |
|------|----------|---------|
| `transpile_to_catala.py` | `emit_declarations`, `type: enum` path (line ~934) | Declaration emitted `-- Qmb` instead of `-- QMB` |
| `transpile_to_catala.py` | `translate_expr_to_catala` Step 12 (line ~726) | `when: field == "SLMB_PLUS"` became `field = SlmbPlus` |
| `transpile_to_catala_tests.py` | `build_field_type_map`, `type: enum` path (line ~150) | Emit-form map returned `"Qmb"` for key `"QMB"` |

**Fix (applied 2026-05-27):** Replaced `snake_to_pascal(v)` with `_to_catala_constructor(v)` at
all three sites. `_to_catala_constructor` returns the value unchanged when `value[0].isupper()`
(covers `A1E`, `QMB`, `SLMB_PLUS` etc.) and calls `snake_to_pascal` only for lowercase-initial
values (`individual`, `low_income` etc.).

**Tests added** in `test_transpile_enum_case_names.py`:
- `TestEnumCaseCapitalization::test_upper_snake_case_enum_values_unchanged_in_declaration`
- `TestBuildFieldTypeMapEnumCapitalization::test_enum_type_upper_snake_values_unchanged`
- `TestUpperSnakeCaseEnumWhenClause::test_upper_snake_when_clause_preserved`
- `TestUpperSnakeCaseEnumWhenClause::test_all_caps_when_clause_preserved`

---

## Fix #30 — Single CIVIL field used as the key for two tables with disjoint key domains

**Symptom (2026-05-27, ah-doh2 / `program_standards` module):**

`xlator catala-pipeline ah-doh2 program_standards` produced 22 runtime rule errors
attributed to `program_standards.catala_en:83-84`, the output declarations for
`ltc_resource_limit` and `refused_cash_limit`. Both outputs were keyed off a single CIVIL
input field, `Household.household_type`, but the two lookup tables had **disjoint** key
value sets:

| Output | Table | Key domain |
|---|---|---|
| `ltc_resource_limit` | `table_resource_limits` | `{individual, couple}` |
| `refused_cash_limit` | `table_refused_cash`    | `{A1E, B1E, H1E, A2S, B2S, H2S, A2C, B2C, H2C, NHR}` |

Every Catala scope invocation must define every declared output. When a test sets
`household_type = "individual"`, the `ltc_resource_limit` rules match but no
`refused_cash_limit` row's `under condition` is satisfied — Catala reports
"no applicable rule to define this variable in this situation." The symmetric case happens
for any APA-coded value. CIVIL validation and the transpiler both accept the spec; the
defect only surfaces at Catala compile/runtime, with an error attributed to the generated
file (the output declarations) rather than the offending input field.

**Why the existing pipeline misses this:**

- `_extract_table_info()` in `transpile_to_catala.py` (~lines 874–905) returns a single
  `(table_name, key_exprs)` tuple per computed field. The lookup-table emitter
  (`emit_table_section`, ~lines 1724–1790) processes each computed field in isolation;
  there is no cross-field analysis that detects two fields sharing the same input key.
- `civil_schema.py` (`TableDef`, ~lines 229–248) defines `key: list[str]` as column names
  only — it does not enumerate or constrain the discrete value domain on those columns,
  and there is no rule that two tables keyed on the same field must share a key domain.
- The Catala error message points at the output declaration line, not at the CIVIL field
  driving the lookup, which makes the root cause non-obvious to the policy author.

**Fuller fix (proposed):**

Add a CIVIL preflight check — naturally slotted as a 5th item in the Fix #23 preflight
validator — that detects multi-table key reuse with disjoint domains:

1. **Disjoint-multi-table-key check.** Build an index `{field_name → [(table_name, key_value_set), …]}`
   by scanning every `tables.<name>` entry: for each column listed in `key:`, collect the
   set of distinct values that appear in the `rows:` for that column. For any field that
   appears as a key in ≥2 tables, compare its value sets. If **any pair is disjoint**, emit:

   ```
   ERROR: <file> — input field '<field>' is used as a lookup key for tables with
   disjoint key domains:
     - <table_A> keys: {<A>, <B>, …}
     - <table_B> keys: {<X>, <Y>, …}
   A single scope invocation can only set '<field>' to one value, so one output's
   table will always have no applicable rule. Resolve by one of:
     1. Split into two fields (recommended): rename '<field>' to '<table_A>_<field>'
        and '<table_B>_<field>' in the spec, tests, and any sub-module bind sites.
     2. Mark both outputs `optional: true` so each is only required when its matching
        input is supplied.
     3. Add fallback expressions to each output (e.g. `$0` default).
   ```

   Run before any `.catala_en` is written, in both `cmd_catala_transpile` and
   `cmd_validate` flows. **Trade-off:** must specifically test for *disjoint* domains —
   tables that legitimately share a key (e.g. multiple tables keyed on `benefit_year`
   with overlapping year sets) are valid and must not be flagged.

**Symptom patch (applied 2026-05-27):**

Split `Household.household_type` in `program_standards.civil.yaml` into two optional,
values-constrained inputs (`resource_household_type ∈ {individual, couple}` and
`apa_household_type ∈ {A1E…NHR}`), marked both affected outputs `optional: true`, rewired
each output's `expr:` to its matching new field, and renamed `key:` plus row keys in
`table_resource_limits` and `table_refused_cash`. Propagated the rename through
`medicaid_eligibility.civil.yaml` (renamed `ClientData.household_type` to
`resource_household_type` — module only consumes `ltc_resource_limit` downstream) and
both test files (`tests/program_standards_tests.yaml`, `tests/medicaid_eligibility_tests.yaml`).
Tests that exercised FPG/Pickle/PNA/excess-home-equity lookups — and never needed the
household-type field — had it removed entirely.

**Files (symptom patch):**
- `<ah-doh2-domain>/specs/program_standards.civil.yaml`
- `<ah-doh2-domain>/specs/medicaid_eligibility.civil.yaml`
- `<ah-doh2-domain>/specs/tests/program_standards_tests.yaml`
- `<ah-doh2-domain>/specs/tests/medicaid_eligibility_tests.yaml`

**Files (proposed fuller fix — not yet implemented):** new check inside the Fix #23
preflight validator (likely `xl-plugin/tools/civil_validate.py` or
`xl-plugin/tools/catala_preflight.py`), called from `cmd_catala_transpile` and
`cmd_validate`.

---

## Fix #32 — Test YAML assertions reference reason codes that no rule emits

**Symptom (2026-05-27, ah-doh2 / `eligibility_gates` module):**

Three `allow_*` cases in `tests/eligibility_gates_tests.yaml` —
`allow_qmb_001`, `allow_slmb_above_qmb_001`, `allow_boundary_at_qmb_fpg_001` —
asserted

```yaml
expected:
  eligible: true
  reasons:
    - code: "ELIGIBLE"
```

The transpiler faithfully emitted

```catala
assertion (result.reasons = [ Eligible ])
```

at lines 27, 102, and 177 of the generated `.catala_en`. The spec defines `reasons`
as a denial-only list (`default: []`, populated only by `add_reason` actions inside
`kind: deny` rules), and no rule emits an `ELIGIBLE` code. The actual scope returned
`reasons = []`, so all three tests failed at `xlator catala-test` time with the
ambiguous mismatch `eligibility_gates [] = [Eligible]`.

The defect is purely in the test artifact — likely introduced by an AI test-scaffolding
pass that paired every `eligible: true` assertion with a sentinel `code: "ELIGIBLE"`
reason. The CIVIL spec, the transpiler, and the rule logic are all correct.

**Why the existing pipeline misses this:**

- `validate_civil.py` validates the spec YAML only; it never opens sibling
  `_tests.yaml` files. There is no test-file validator at all.
- `transpile_to_catala_tests.py` at `emit_test_scope` (line 521) calls
  `reason_code_to_pascal(r["code"])` on whatever string the test author supplied —
  no membership check against the spec's declared reason codes.
- `transpile_to_catala.py` builds the `ReasonCode` enum (lines 1103–1118) from
  `rules[*].then[*].add_reason.code` in the spec; the test transpiler does not
  consult that same set when emitting test assertions.
- The failure manifests as a Catala value-equality mismatch at the *last* pipeline
  step. The reported message names the generated Catala line and an enum
  constructor (`Eligible`) that does not appear anywhere in the spec, so the test
  author can spend significant time hunting for a phantom rule before realising the
  test itself is the source of the orphan code.

**Proposed fix:**

Add an orphan reason-code preflight check that runs in two places:

1. `cmd_validate` — when `xlator validate <domain> <module>` runs against a spec,
   also walk the sibling `tests/<module>_tests.yaml` (if present) and report orphan
   codes there.
2. `transpile_to_catala_tests.transpile` — fail-fast at the top of the function
   (before any Catala lines are emitted), so test files that were added or edited
   after spec validation still get caught.

The check itself, as a pure function:

```python
def find_orphan_reason_codes(
    spec_doc: dict,
    tests_doc: dict,
    sub_module_docs: dict,
) -> list[OrphanFinding]:
    """Return one finding per (case_id, orphan_code) pair. Empty list = clean."""
    valid_codes = _collect_emitted_reason_codes(spec_doc, sub_module_docs)
    reasons_field = _denial_field_name(spec_doc)              # usually "reasons"
    is_denial_only = _is_denial_only_list(spec_doc, reasons_field)
    findings = []
    for case in (tests_doc.get("tests") or []):
        expected = case.get("expected") or {}
        asserted = expected.get(reasons_field) or []
        if not isinstance(asserted, list):
            continue
        asserted_codes = [entry["code"] for entry in asserted if "code" in entry]
        for code in asserted_codes:
            if code not in valid_codes:
                findings.append(OrphanFinding(
                    case_id=case.get("case_id", "<unknown>"),
                    orphan_code=code,
                    valid_codes=sorted(valid_codes),
                    nearest_match=_levenshtein_closest(code, valid_codes),
                    denial_only_violation=False,
                ))
        if is_denial_only and asserted_codes and expected.get("eligible") is True:
            findings.append(OrphanFinding(
                case_id=case.get("case_id", "<unknown>"),
                orphan_code=None,
                valid_codes=sorted(valid_codes),
                nearest_match=None,
                denial_only_violation=True,
            ))
    return findings
```

**Cross-module plumbing.** `_collect_emitted_reason_codes` unions:

- The parent spec's own `rules[*].then[*].add_reason.code`.
- For every `computed.*.invoke.module = <sub>`: the sub-module's
  `rules[*].then[*].add_reason.code`, loaded via the same sibling-spec loader the
  test transpiler already uses (`transpile_to_catala_tests.py:572–582`). Reuse the
  loader; do not duplicate it. This handles aggregation patterns where a parent
  module appends sub-module reasons into its own `reasons` output.

**Error message format** (must name the case_id, the orphan code, the valid set, and
the nearest match — so the author can paste the fix in without re-reading the spec):

```
ERROR: tests/<module>_tests.yaml — case 'allow_qmb_001':
  expected.reasons asserts code 'ELIGIBLE', but no rule in <module>.civil.yaml
  emits this code.
    Valid reason codes: [EXCESS_RESOURCES, EXCESS_INCOME_QMB, EXCESS_INCOME_QDWI,
                         NOT_CITIZEN, NOT_ALASKA_RESIDENT]
    Did you mean: (no close match — likely a sentinel that should be removed)
    Note: outputs.reasons.default is [] (denial-only). For allow tests, assert
          'reasons: []' or omit the reasons block entirely.
```

When `denial_only_violation` fires (denial-only list + `eligible: true` + non-empty
asserted reasons), emit the stronger second paragraph regardless of whether the
codes happen to be valid. Reason: even valid codes are wrong on an allow path,
because no rule writes to `reasons` outside the deny branch.

**Generator-side guardrail (secondary fix):**

The three failing tests had identical bug shape, which points to a single template
in whatever scaffolded them — likely `/create-sample-tests` (or
`/extract-sample-tests`). Update that skill's prompt with a short rule:

> When `outputs.reasons.default == []` (denial-only list, populated only by
> `kind: deny` rules), an `allow_*` test case must assert `reasons: []` or omit the
> `reasons:` key entirely. Never invent a sentinel code like `"ELIGIBLE"` to pair
> with `eligible: true`.

Without this, the validator catches the same generator output on every domain and
the user is stuck hand-fixing each spec.

**Edge cases the validator must handle correctly:**

| Case | Expected behavior |
|---|---|
| Test omits `reasons:` entirely | No finding — current passing pattern (e.g. `allow_reasonable_compat_001`). |
| Test asserts `reasons: []` on a deny test | No finding — semantically odd but not wrong; rule precedence may have suppressed the reason. |
| Spec has no `outputs.reasons` field at all | No finding — `valid_codes` is empty, but `asserted` is also empty in a well-formed test (Pydantic should catch the malformed case before reaching here). |
| Cross-module: parent has zero `add_reason` rules but its `reasons` output concatenates a sub-module's reasons | Sub-module's codes are valid; parent-only check would false-positive — hence the sub-module union. |
| Same code declared in two sub-modules | Treated as a single valid code (union, not multiset). |
| Test asserts `reasons: [{}]` (missing `code:` key) | Skipped silently here; Pydantic schema for the test file (separate work item) should reject it upstream. |

**Test plan:**

| Test | Type | Asserts |
|---|---|---|
| `test_orphan_code_in_simple_module` | unit | Spec emits `{A, B}`; test asserts `code: C` → one finding with `orphan_code = 'C'`, `valid_codes = ['A', 'B']`. |
| `test_orphan_code_with_levenshtein_match` | unit | Spec emits `EXCESS_RESOURCES`; test asserts `EXCESS_RESOURCE` → finding includes `nearest_match = 'EXCESS_RESOURCES'`. |
| `test_no_finding_when_reasons_omitted` | unit | Test has no `reasons:` key → empty findings (mirrors `allow_reasonable_compat_001`). |
| `test_no_finding_when_reasons_empty` | unit | Test asserts `reasons: []` → empty findings. |
| `test_denial_only_violation_with_eligible_true` | unit | Spec `default: []` + test `eligible: true` + non-empty `reasons:` → `denial_only_violation = True` finding even when codes are nominally valid. |
| `test_cross_module_reason_codes_union` | unit | Parent invokes sub-module that emits `code: SUB_REASON`; parent test asserts that code → no finding. |
| `test_validate_civil_walks_sibling_tests` | integration | `xlator validate ah-doh2 eligibility_gates` after re-introducing the `ELIGIBLE` sentinel exits non-zero with the case_id in the message. |
| `test_test_transpile_fails_fast_on_orphan` | integration | `xlator catala-test-transpile` aborts before writing any `.catala_en` when orphan codes are present. |
| `test_clean_ah_doh2_tests_pass_validator` | regression | After Fix #31, the full `ah-doh2` test suite (all `_tests.yaml` files) passes the new check. |

**Files to add/modify:**

- `xl-plugin/tools/validate_civil.py` — add `validate_orphan_reason_codes(spec_path, spec_doc)` following the signature of the existing `validate_invoke_references` / `validate_table_lookup_references` helpers (returns `(errors, warnings)`); wire it into `validate()` between the table-lookup and v6 checks. Locate the sibling `tests/<module>_tests.yaml` from the spec path.
- `xl-plugin/tools/transpile_to_catala_tests.py` — call the same function at the top of `transpile()`, after `sub_module_docs` is built (~line 582), and `fail(...)` with the formatted message if findings are non-empty.
- `xl-plugin/tools/test_validate_orphan_reason_codes.py` — new test file covering the matrix above.
- `xl-plugin/.claude-plugin/plugin.json` — bump MINOR version (new feature, backwards-compatible).
- `xl-plugin/skills/create-sample-tests/SKILL.md` (or the equivalent generator skill) — add the denial-only-reasons rule from the "Generator-side guardrail" section above.

**Out of scope:**

- A Pydantic schema for `_tests.yaml` (would catch malformed-reason-entry shape but
  is independent of the orphan-code semantics; track separately).
- Validating that the spec actually emits every code it declares (the inverse
  problem — declared but never produced); transpiler already handles this benignly
  by emitting unused `ReasonCode` variants, no user-visible issue today.
- Cross-domain reason codes (sub-modules from other `$DOMAINS_DIR` domains) —
  current bind model is single-domain only.

**Related:**

Symptom-only fix already applied to `tests/eligibility_gates_tests.yaml`
(2026-05-27): three `reasons: [{code: "ELIGIBLE"}]` blocks replaced with
`reasons: []`. The validator in this entry is the upstream guard that would have
caught the original bug at `xlator validate` time instead of at `catala-test` time.

---

## Fix #31 — Unguarded division by a CIVIL field causes Catala runtime "Rule error" (#23.6)

**Symptom (2026-05-27, ah-doh2 / `unearned_income_classification.civil.yaml:253`):**

The `countable_room_rental_income` formula divided by `Household.total_rooms`:

```yaml
expr: "Household.rental_income - (Household.total_household_expenses * Household.rented_rooms / Household.total_rooms)"
```

Only one of 11 test fixtures (`ext_009`, the rental scenario) sets `total_rooms` to a
non-zero value. The remaining 10 leave it at the default 0. CIVIL validation passed and
the transpiler emitted the expression verbatim; Catala then raised a runtime "Rule error"
at `unearned_income_classification.catala_en:285` for every test that exercised the
field — 10 failures attributed to the generated file, with no signal pointing back at
the CIVIL source.

This is the **second occurrence** of the same defect class in the same module: Fix #9
(2026-05-11) recorded the identical bug shape for `prorated_rental_expense`, with the
same `if total_non_bathroom_rooms > 0 then … else 0` symptom patch. Two hits in one
module, no toolchain enforcement.

**Why the existing pipeline misses this:**

- CIVIL's expression mini-language (`civil_schema.py:13-21`) lists `/` as a supported
  arithmetic operator with no domain constraints. The validator accepts any
  syntactically-valid expression.
- `translate_expr_to_catala` emits arithmetic verbatim — no static check that the
  divisor is a `FieldRef` whose value can legitimately be zero (which, in CIVIL,
  is the default for any unset numeric field of `type: int` or `type: money`).
- Catala's "Rule error" surfaces only at evaluation time. Its location points at the
  emitted line in `.catala_en`, not at the CIVIL `expr:` string, and the attribution
  message gives no hint that the cause is a zero divisor — making the bug
  disproportionately hard to diagnose relative to the size of the fix.

**Fuller fix (proposed — sibling of Fix #23, check #6):**

Add a CIVIL preflight check that flags unguarded division by a field reference. Slot it
into the same preflight validator as Fix #30's disjoint-table-key check, before any
`.catala_en` is written.

1. **Unguarded-division scanner.** For every `expr:` string (in `computed.*.expr`,
   `conditional.*.then`, `conditional.*.else`, and `when:` clauses), tokenize and locate
   every `/` operator. Classify the divisor:
   - **Numeric literal** (`/ 12`, `/ 0.5`) → safe, skip.
   - **Field reference** (`/ Household.total_rooms`, `/ effective_year`) → flagged
     unless the surrounding context guards against zero. "Guarded" means the formula
     appears inside a `conditional:` whose `if:` constrains the same field positively
     (e.g. `if: "Household.total_rooms > 0"`), or inside a `then:` / `else:` branch
     whose enclosing `if:` does so.
   - **Sub-expression** (`/ (a + b)`) → conservative: skip (avoid false positives on
     algebraically-impossible-zero cases until a real example shows up).

   On match, emit:
   ```
   ERROR: <file>:<line> — expression in computed.<field>.expr divides by
   '<divisor_field>' without a zero guard. CIVIL numeric fields default to 0 when
   unset, so this will raise a Catala "Rule error" at runtime for any test fixture
   that omits the divisor.
   Resolve by one of:
     1. Wrap the formula in a structured `conditional:` block (recommended):
          conditional:
            if: "<divisor_field> > 0"
            then: "<existing expression>"
            else: "0"
     2. Mark the divisor field non-optional with a positive `min:` constraint
        (requires extending the CIVIL schema; not currently supported).
     3. Annotate the expression with `# safe-divide-ok: <reason>` to suppress this
        check (use only when the divisor is genuinely guaranteed non-zero by an
        external invariant the validator cannot see).
   ```

   **Trade-off:** false positives on divisors that are genuinely never zero
   (e.g. a constant from a lookup table, or a count derived from an earlier
   `conditional:` that already guards it). The `# safe-divide-ok` escape hatch
   covers the long tail without forcing every author to restructure benign
   formulas; absence of the annotation defaults to the strict check.

**Alternative — `safe_div` expression builtin (deferred):**

Add `safe_div(numerator, divisor, default)` to the CIVIL expression mini-language. The
transpiler rewrites it to `if divisor != 0 then numerator / divisor else default` at emit
time. Call-site form:

```yaml
expr: "Household.rental_income - safe_div(Household.total_household_expenses * Household.rented_rooms, Household.total_rooms, 0)"
```

Pros: no structural rewrite of the field — `expr:` stays a single line; clear author
intent; no escape-hatch sprawl. Cons: expands DSL surface and adds a parser rule;
authors can still write `/` and bypass the safety entirely, so the validator check
above is still needed as the enforcement layer.

**Recommendation:** ship the validator scanner first (check #6 in the Fix #23
preflight). Reassess `safe_div` after the scanner has run across `$DOMAINS_DIR` and
the false-positive rate is known — if the escape-hatch annotation appears more than
a handful of times, `safe_div` becomes worth the DSL surface.

**Symptom patch (applied 2026-05-27):**

Replaced the unguarded `expr:` on `countable_room_rental_income` with a structured
`conditional:` block, mirroring the Fix #9 pattern and the existing
`student_exclusion_amount` shape in
`earned_income_exclusions.civil.yaml:162-165`:

```yaml
countable_room_rental_income:
  type: money
  ...
  conditional:
    if: "Household.total_rooms > 0"
    then: "Household.rental_income - (Household.total_household_expenses * Household.rented_rooms / Household.total_rooms)"
    else: "0"
```

`conditional:` and `expr:` are mutually exclusive on a single computed field; the
`expr:` line was removed entirely. The bare `"0"` else-branch is auto-coerced to
`$0` for money-typed fields by `translate_expr_to_catala` Step 11
(see `transpile_to_catala.py:736-801`). After re-transpile, `unearned_income_classification.catala_en:285`
reads `if total_rooms > 0 then rental_income - … else $0` and all 11 unearned-income
tests pass.

**Files (symptom patch):** `<ah-doh2-domain>/specs/unearned_income_classification.civil.yaml`

**Files (proposed fuller fix — not yet implemented):** new check inside the Fix #23
preflight validator (`xl-plugin/tools/civil_validate.py` or
`xl-plugin/tools/catala_preflight.py`), with new unit tests in
`xl-plugin/tools/test_civil_unguarded_division.py`. The check needs read access to the
full CIVIL document (to walk enclosing `conditional.if` guards) and to the field-type
map already built by `_build_all_field_type_map` (Fix #25) — both available where the
preflight runs.

---

## Fix #33 — Test-transpiler default for shared input keys picks first-table first-row, breaking every test scope

**Symptom (2026-05-27, ah-doh2 / `program_standards` module):**

All 13 test scopes in `output/tests/program_standards_tests.catala_en` failed at the
`program_standards.catala_en:94` output declaration (`excess_home_equity_threshold`) with
"no applicable rule to define this variable in this situation." Tests asserting on
`fpg_threshold_qmb`, `pickle_reduction_factor`, etc. — outputs entirely unrelated to
excess-home-equity — still failed because Catala evaluates every declared output in a
scope.

**Root cause:**

`build_field_type_map` in `transpile_to_catala_tests.py` (lines ~194–212) built
`table_key_defaults` as `{key → first_seen_table.first_row[key]}`. For
`benefit_year`, three tables shared the key with different coverage:

| Table | `benefit_year` rows |
|---|---|
| `table_program_standards` (seen first) | 2023–2026 |
| `table_excess_home_equity` | **2024–2026** |
| `table_student_exclusion` | 2023–2026 |

Default picked: **2023**. Result: `excess_home_equity_threshold` had no rule matching
`benefit_year = 2023`, so every test scope failed at the unsatisfiable output declaration
— regardless of what the test actually asserted on.

A contributing factor masked the typo path: the test YAML used `year:` instead of
`benefit_year:`. The transpiler silently dropped the unknown key, fell back to the
buggy default, and surfaced the failure as a Catala rule error rather than a field-name
typo.

**Fix (applied 2026-05-27):**

Two changes in `transpile_to_catala_tests.py`:

1. **Intersection-based default selection** — replaced the `table_key_defaults` block in
   `build_field_type_map` with logic that computes the **intersection** of every value
   set seen for each key across all tables (and sub-module tables) that use it. The new
   pure helper `pick_representative(values)` returns `max(values)` for numeric sets
   (deterministic; picks newest year / largest size) and `min(values, key=str)` for
   non-numeric sets. When tables have no common value (disjoint coverage — the case
   Fix #30 proposed catching at the CIVIL layer), the transpiler now emits a WARN and
   picks from the largest value set so the failure mode is visible rather than silent.

2. **Unknown-input-name validation** — `emit_test_scope` now builds the set of declared
   input field names from `all_fields` and `entity_fields` (both bare and
   `Entity.field` forms), and emits a WARN listing the known names for any input key in
   the test YAML that is not declared in CIVIL. Catches `year:` vs `benefit_year:` typos
   at transpile time.

**Tests added** in `xl-plugin/tools/`:

- `test_transpile_table_key_defaults_intersection.py` (12 tests): `pick_representative`
  determinism (numeric → max, string → lex-min, mixed → lex-min); intersection across
  3 overlapping tables; single-table case; disjoint-coverage WARN + larger-set fallback;
  string-keyed intersection; empty-table handling; key-in-only-one-table.
- `test_transpile_unknown_test_input_warns.py` (4 tests): unknown bare input WARNs and
  suggests valid names; known bare input does not WARN; multi-entity `Entity.field`
  known and unknown forms.

Updated `test_transpile_catala_tests.py`:
`test_build_field_type_map_collects_int_table_key_default` asserts the new
representative (max) instead of the old first-row value; the prior
`test_build_field_type_map_table_key_defaults_first_row_wins` was renamed to
`test_build_field_type_map_disjoint_table_key_warns_and_picks_from_larger_set`
and now also asserts the WARN.

**Relationship to Fix #30:** Fix #30 proposed a CIVIL preflight check that flags
disjoint-domain shared keys before transpilation. Fix #33 is complementary — it operates
at the test-transpiler layer so existing domains still produce working tests even when
the disjoint condition is present, and it surfaces the issue via WARN whether or not the
preflight check is added later.

**Files:**

- `xl-plugin/tools/transpile_to_catala_tests.py` — Changes 1 and 2
- `xl-plugin/tools/test_transpile_table_key_defaults_intersection.py` — new
- `xl-plugin/tools/test_transpile_unknown_test_input_warns.py` — new
- `xl-plugin/tools/test_transpile_catala_tests.py` — updated assertions
- `xl-plugin/.claude-plugin/plugin.json` — version bumped to 2.13.1
- `doc/design/transpiler-default-fix.md` — design rationale
