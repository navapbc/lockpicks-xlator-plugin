# Naming Guide

Plugin-wide style rules for variable names extracted from policy documents.
Consulted on every run by `/extract-computations` (per-file workers) and by
`xlator naming-defaults --build` (the cross-file merge tool). Authority on
re-runs flows through a chain: `specs/naming-manifest.yaml` (highest, analyst
renames) → `policy_facets/naming-defaults.yaml` (mid, auto-picked canonicals)
→ this guide (lowest, style rules only).

## Variable name style

- **`snake_case`.** Lowercase ASCII letters, digits, and underscores. No
  hyphens, no camelCase, no dots.
- **Noun phrase, not verb phrase.** `gross_income` not `compute_gross_income`;
  `net_eligible_amount` not `is_eligible`.
- **Prefer policy terminology over acronyms.** `gross_income` not `gi`;
  `temporary_assistance_for_needy_families` not `tanf` when the source spells
  out the term. Use the acronym only when the source does and the expansion
  would be unwieldy.
- **Strip entity-name words when redundant.** When the entity context is
  `Household`, prefer `gross_income` over `household_gross_income`. Strip
  duplicated words from the head of the phrase.
- **Disambiguate with a qualifier when needed.** Two distinct concepts in the
  same entity that would otherwise collide on the same name take a
  disambiguating qualifier from the source text (e.g., `gross_monthly_income`
  vs `gross_annual_income`).
- **Singular unless the variable is a list.** A list-valued variable may use
  the plural; scalars stay singular.

## `policy_phrase:` — verbatim rule

`policy_phrase:` is the join key the authority chain (R4, R5) and the merge
tool (U4) use to reconcile the same concept across files. It must be **stable
across re-runs of the same source** so the join doesn't drift.

- **Copy a verbatim noun phrase from the source body.** Not paraphrased, not
  summarized, not re-cased. Whitespace and punctuation may be normalized at
  comparison time, but the emitted value is byte-for-byte from the source.
- **Verifiable.** `grep -F "<policy_phrase>" <input/policy_docs/<rel>.md>`
  must match. If it doesn't, the value is wrong.
- **Fallback when no verbatim noun phrase exists.** Use the most specific
  deterministic anchor available: the section heading text, the parent
  heading, or — as a last resort — the first sentence of the section. The
  fallback string is also stable across re-runs (it is derived from source
  structure, not from generation). Document the fallback choice nowhere else;
  the source itself is the record.
- **Never paraphrase.** Paraphrase drift across runs silently breaks the
  no-copy-back guarantee (R12) — the worker's matching against
  `specs/naming-manifest.yaml` fails on a different paraphrase, the analyst's
  rename is silently ignored, and the merge tool produces a fresh canonical.

## `type:` — controlled vocabulary, inference on signal

`type:` is **optional** and emitted only when the source body carries a clear
signal. Absent is the safe default. The vocabulary is exactly:

`money | bool | int | float | string | enum | list | date`

The emitter (`xlator emit-per-file-yaml`) rejects any other value, including
`str` and `text`.

| Type     | Trigger phrases / patterns                                                          |
|----------|--------------------------------------------------------------------------------------|
| `money`  | currency markers (`$`, `USD`, "dollars"), "per month", "annual income", monetary thresholds |
| `bool`   | "yes/no", "true/false", "is/is not eligible", binary flags                           |
| `int`    | counts ("number of household members"), age in years, integer thresholds             |
| `float`  | percentages (`20%`, `0.20`), ratios, multipliers                                     |
| `string` | free-form identifier (case number, applicant name)                                   |
| `enum`   | bulleted/comma-separated list of allowed outcomes ("approve, deny, manual review")   |
| `list`   | "list of …", repeating-collection phrasing ("each member …")                         |
| `date`   | dates, "as of", "effective date", calendar references                                |

Never infer `type:` from the variable name alone — `gross_income` does not
become `money` just because the name contains "income"; the source body must
say so. A hallucinated type pollutes the authority chain and downstream skills
act on it.

## `description:` — concise prose, optional

`description:` is **optional** and emitted only when the source contains a
definitional sentence about the concept. One sentence, anchored to the source's
own framing — never paraphrased to fit a template, never invented to fill the
field. The merge tool prefers the canonical-name contributor's description and
falls back to a synonym contributor's only when the canonical lacks one;
analyst overrides via `specs/naming-manifest.yaml` beat both.

When present it must be a non-empty string; the emitter rejects empty or
whitespace-only descriptions. Pass `null` (or omit the key) to mean "absent".

## `values:` — required for enum, omitted otherwise

`values:` is a list of allowed string values for an `enum`-typed concept.
**Required when `type: enum`, MUST be omitted otherwise.** The emitter rejects
both directions of the violation: `type: enum` without `values:`, and `values:`
with any other `type:`.

The source signal is a bulleted enumeration or comma-separated list of allowed
outcomes ("approve, deny, manual review"). Each list element is a string;
the emitter rejects non-string entries.

When two synonyms each carry their own `values:`, the merge tool emits the
**sorted union** as the canonical's values (deterministic across re-runs).
Analyst override via `specs/naming-manifest.yaml` replaces the union entirely.

## `role_hint:` — optional signal hint, only on clear evidence

`role_hint:` is **optional** and emitted only when the source body carries an
unambiguous syntactic signal. Absent is the safe default.

| Hint        | Trigger phrases / patterns                                                       |
|-------------|-----------------------------------------------------------------------------------|
| `input`     | "applicant provides", "self-reported", "from the application", "stated by"        |
| `computed`  | Formula syntax (`= a + b`, `* 0.20`), "calculated as", "computed by", "the result of" |
| `output`    | "determined to be eligible/ineligible", "the program decision is", "approved/denied" |

`role_hint:` is **not** AI categorization. The full categorization (every
variable assigned to inputs / computed / outputs in CIVIL) remains
`/extract-ruleset`'s responsibility; the analyst confirms each entry there.
`role_hint:` accelerates Step 3b only for the unambiguous cases — it never
preempts analyst judgment.

When in doubt, **omit the field**. An absent `role_hint:` is stronger than a
hallucinated one.

## Merge-time resolution

When `xlator naming-defaults --build` groups synonyms into a single canonical
entry, the synonyms may carry different (or absent) values for `role_hint:`,
`description:`, `type:`, and `values:`. The merge tool resolves each field
with the rules below. **In every case, `specs/naming-manifest.yaml` overrides
all observed values when the specs entry supplies the field.**

### `role_hint:`

- **Prefer specific over absent.** If the canonical entry omits `role_hint:`
  but a synonym carries one, copy the synonym's hint onto the canonical.
- **On disagreement, `computed > output > input`.** `computed` is the
  strongest signal because it requires explicit formula evidence in the
  source. `output` ranks above `input` because policy texts more often state
  outcomes explicitly than they declare inputs (inputs are the default
  category for un-signaled variables).
- **Tie within rank → omit.** If multiple synonyms tie on the highest rank
  with conflicting evidence (rare), omit `role_hint:` from the canonical and
  let the analyst supply it in `/extract-ruleset` Step 3b.

### `description:`

- **Canonical contributor preferred.** If the per-file file whose name became
  the canonical has a `description:`, it wins.
- **Fall back to any synonym.** If the canonical contributor has no
  `description:` but a synonym does, copy the synonym's onto the canonical.
- **Otherwise omit.** No descriptions anywhere → absent.

### `type:`

- **Agreement wins.** If every synonym that supplies `type:` agrees on a
  single value, use it. Synonyms that omit `type:` do not count as
  disagreement.
- **Disagreement → omit and warn.** If two synonyms supply different `type:`
  values, omit `type:` from the canonical and surface a warning in the merge
  tool's `errors:` array (and on stderr). The analyst resolves via specs
  override on the next run; silent guess-on-disagreement would propagate
  hallucinated types.
- **No type signal anywhere → omit.** Absent is the safe default.

### `values:`

- **Only meaningful when canonical `type: enum`.** When the resolved type is
  not `enum`, the canonical entry has no `values:` field.
- **Sorted union of observed values.** When multiple synonyms supply
  `values:`, the canonical's `values:` is the alphabetically-sorted union of
  every observation's values. This is deterministic across re-runs.
- **Specs override replaces, not unions.** When `specs/naming-manifest.yaml`
  supplies `values:`, the canonical uses the specs list verbatim — observed
  values are dropped.

## Worker decision flow

For each variable a per-file worker extracts:

1. Read the static guide (this file).
2. Compute the variable's `policy_phrase:` per the verbatim rule above.
3. Normalize the phrase (lowercase, strip leading articles `a/an/the`, strip
   ASCII punctuation, collapse whitespace) for the authority lookup.
4. If the normalized phrase matches an entry in `specs/naming-manifest.yaml`,
   use that entry's name verbatim. Done.
5. Else if the normalized phrase matches an entry in
   `policy_facets/naming-defaults.yaml`, use that entry's name. Done.
6. Else derive a fresh name from the style rules above.
7. Decide `role_hint:` per the trigger table above. Omit if no clear signal.
8. Decide `type:` per the trigger table above; emit only on clear signal.
   When `type: enum`, also emit `values:` from the source's enumeration of
   allowed outcomes.
9. Emit `description:` only when the source contains a definitional sentence
   about the concept; one sentence, anchored to the source's framing.
10. Emit `naming_manifest.variables.<name>` with `policy_phrase:`,
   `role_hint?`, `source_section:`, `description?`, `type?`, `values?`.

## Common mistakes

- **Don't paraphrase `policy_phrase:`.** Verbatim from the source body. If no
  noun phrase exists, fall back to a deterministic anchor (heading text);
  never invent.
- **Don't invent `role_hint:`.** No clear signal in the source → omit the
  field. Absent is safer than wrong.
- **Don't strip too aggressively.** `gross_income` is fine when the entity
  context is `Household`; over-stripping to `income` collides with adjacent
  income variables.
- **Don't pluralize scalars.** `dependent_count` not `dependents_count`.
- **Don't use abbreviations the source doesn't.** If the source spells it
  out, the variable name spells it out too.
- **Don't infer `type:` from variable-name shape.** `gross_income` does not
  imply `money` and `is_eligible` does not imply `bool` from the name alone —
  the source body must carry an explicit signal from the trigger table.
- **Don't write `values:` without `type: enum`.** And don't write `type: enum`
  without `values:`. The two ship together.
- **Don't paraphrase `description:` to fit a template.** One sentence anchored
  to a definitional sentence in the source. If the source has no definitional
  framing, omit the field.
