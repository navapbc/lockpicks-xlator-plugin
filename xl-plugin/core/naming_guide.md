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

## Merge-time `role_hint:` resolution

When `xlator naming-defaults --build` groups synonyms into a single canonical
entry, the synonyms may carry different (or absent) `role_hint:` values. The
merge tool resolves them with these rules:

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
8. Emit `naming_manifest.variables.<name>` with `policy_phrase:`,
   `role_hint?`, `source_section:`.

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
