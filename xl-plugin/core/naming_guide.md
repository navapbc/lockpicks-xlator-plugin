# Naming Guide

Plugin-wide style rules for variable names extracted from policy documents.
Consulted on every run by `/extract-computations` (per-file workers). Authority
on re-runs flows through a two-tier chain: `specs/naming-manifest.yaml`
(highest, **analyst-authoritative** — confirmed against a doc OR seeded
pre-extraction by `/declare-target-ruleset`; provenance fields are nullable on
seeded entries and gap-fill from observations via `/extract-ruleset` Step 7) →
this guide (lowest, style rules only).

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

`policy_phrase:` is the join key the authority chain uses to reconcile the
same concept across re-runs. It must be **stable across re-runs of the same
source** so the join doesn't drift.

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
- **Never paraphrase.** Paraphrase drift across runs silently breaks alignment
  with confirmed `specs/naming-manifest.yaml` entries — the worker's matching
  fails on a different paraphrase, and the analyst's rename is silently
  ignored.

## `expr_hint:` — assignment form, optional

`expr_hint:` is **optional** on a `sections[*].computations[*]` entry and, when
present, must be of the form `output_name = <expression>`:

- **LHS** — a snake_case identifier naming the computed output. Same naming
  rules as variable names above (lowercase letters/digits/underscores; first
  char a letter or underscore).
- **`=` separator** — single equals sign; no `:=` or other variants.
- **RHS** — the expression itself, referencing input variable names by their
  resolved snake_case names. Function calls, arithmetic, and conditional
  shorthand are permitted.

When a computation is descriptive-only (e.g., "households receiving SSI are
categorically eligible"), omit `expr_hint:` entirely. Downstream consumers
fall back to scanning `description:` prose for variable names.

The emitter (`xlator emit-per-file-yaml`) rejects bare-expression `expr_hint:`
payloads (no `=`), empty LHS, empty RHS, and non-snake_case LHS. Legacy
on-disk files containing bare-expression `expr_hint:` are not re-validated;
consumers tolerate them silently and fall through to `description:` prose
scanning.

## `type:` — controlled vocabulary, inference on signal

`type:` is **optional** on `specs/naming-manifest.yaml` entries and emitted
only when the source body carries a clear signal. Absent is the safe default.
The vocabulary is exactly:

`money | bool | int | float | string | enum | list | date`

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
field. Analyst overrides via `specs/naming-manifest.yaml` always win.

When present it must be a non-empty string. Pass `null` (or omit the key) to
mean "absent".

## `values:` — required for enum, omitted otherwise

`values:` is a list of allowed string values for an `enum`-typed concept.
**Required when `type: enum`, MUST be omitted otherwise.**

The source signal is a bulleted enumeration or comma-separated list of allowed
outcomes ("approve, deny, manual review"). Each list element is a string.

## Common mistakes

- **Don't paraphrase `policy_phrase:`.** Verbatim from the source body. If no
  noun phrase exists, fall back to a deterministic anchor (heading text);
  never invent.
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
- **Don't emit `expr_hint:` as a bare expression.** It must be the assignment
  form `output_name = <expression>`. For descriptive-only computations, omit
  `expr_hint:` entirely.
