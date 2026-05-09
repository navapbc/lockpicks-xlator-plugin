---
name: extract-computations
description: Extract Per-File Section/Computation Data Into policy_facets/computations/
---

# Extract Per-File Section/Computation Data

Read one policy doc under `<domain>/input/policy_docs/`, consult the naming authority chain, parse its H1–H3 sections, and write a YAML map `{naming_manifest, sections}` to the mirrored destination at `<domain>/policy_facets/computations/<rel>.md.yaml`.

This skill is invoked per file by `/index-inputs` (in a loop over REINDEX entries) and may also be invoked standalone by the analyst against a single source file. The non-AI half (file enumeration, manifest sync, mirror-deletes, plan/finalize handoff) lives in `xlator extract-computations <domain> --plan` and `--finalize`; this skill is the AI half that does the per-file content generation.

## Input

```
/extract-computations <path_to_policy_file>
```

`<path_to_policy_file>` must resolve to a path under `<DOMAINS_DIR>/<domain>/input/policy_docs/`. If not, pre-flight emits `:::error` and stops.

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Argument provided?**
   - NO →
     :::error
     Usage: /extract-computations <path_to_policy_file>
     :::
     Then stop.

2. **Path resolves under `<DOMAINS_DIR>/<domain>/input/policy_docs/`?**
   - Resolve `<path_to_policy_file>` to an absolute path. Accept absolute paths, paths relative to the project root, or paths relative to `$DOMAINS_DIR`.
   - Walk the resolved path's ancestors to find `input/policy_docs/`. The directory three levels up from `input/policy_docs/<rel>` (i.e., the ancestor whose child `input/policy_docs/` exists) is `<domain>`. The grandparent is `$DOMAINS_DIR`.
   - If the resolved path is not under `<DOMAINS_DIR>/<domain>/input/policy_docs/<rel>` for any valid `<domain>` →
     :::error
     Path must be under <domain>/input/policy_docs/. Got: <resolved-path>
     :::
     Then stop.

3. **Source file exists and is readable?**
   - File missing or unreadable →
     :::error
     Source file not found: <path>
     :::
     Then stop.

4. **Source has `.md` extension?**
   - Non-`.md` →
     :::error
     Only .md sources are supported in v1; got: <path>
     :::
     Then stop.

5. **md_quality gate.**
   - If `<DOMAINS_DIR>/<domain>/policy_facets/input-index.yaml` exists, look up `input/policy_docs/<rel>.md` in its `files:` block. If found and `md_quality.score < 40` (the project's REJECTED threshold) →
     :::error
     Source rejected by index (md_quality.score=<N>). Fix or remove from input/policy_docs/.
     :::
     Then stop.
   - If the file is not in the index, or no `input-index.yaml` exists yet (analyst running this skill before `/index-inputs`), proceed without the gate. The gate only fails on a known-low-quality source.

## Process Checklist

This skill has 5 steps:
- [ ] Step 1: Read source and parse H1–H3 sections
- [ ] Step 2: Consult the naming authority chain
- [ ] Step 3: Generate per-section data and `naming_manifest:` block
- [ ] Step 4: Emit `policy_facets/computations/<rel>.md.yaml` via `xlator emit-per-file-yaml`
- [ ] Step 5: Print summary

## Step 1: Read source and parse sections

Read the full file content at `<DOMAINS_DIR>/<domain>/input/policy_docs/<rel>.md`.

Extract all H1 (`#`), H2 (`##`), and H3 (`###`) headings in document order. For each heading, collect the section body — the text between this heading and the next heading of equal or higher level.

If the file has **no H1–H3 headings**, treat the whole file as a single section. The heading for that single section is the filename stem (without `.md`) prefixed with `#`.

## Step 2: Consult the naming authority chain

Read `../../core/naming_guide.md` now — the static plugin-wide style guide consulted on every run.

Then resolve `<domain_dir>` from the source path: walk ancestors of `<path_to_policy_file>` to find `input/policy_docs/`; the directory three levels up is `<domain>`; its parent is `$DOMAINS_DIR`. (The pre-flight already does this lookup; reuse the resolved `<domain_dir>`.)

Build two authority lookup maps from the chain (highest priority first):

1. **Specs (highest authority):** If `<domain_dir>/specs/naming-manifest.yaml` exists, read it and flatten per the plan's Specs-side flattening rule:
   - Walk `inputs.*.*`, `computed.*`, and `outputs.*`.
   - For each entry, key by `normalize(policy_phrase)` with value `{name: <leaf_key>, role_hint: <entry.role_hint?>, source_doc, section}`.
   - On collision (e.g., `inputs.Household.gross_income` and `inputs.Applicant.gross_income` both with phrase "gross monthly income"), prefer the entry whose `source_doc:` matches the file currently being processed; deterministic tiebreak: alphabetical by entity name.
   - Malformed file → log a warning to stderr and treat as empty map. Never block extraction.

2. **Defaults (mid authority):** If `<domain_dir>/policy_facets/naming-defaults.yaml` exists, read it; build `{normalize(policy_phrase) → {name, role_hint?, source_doc, section}}` from its flat `variables:` map — `name` is the variable key, `source_doc` and `section` come from the entry's top-level `source_doc:` / `section:` fields (no longer dug out of a `sources:` list). Specs entries take priority over defaults entries on conflicting normalized phrases. Malformed file → same warning-and-continue behavior.

The normalizer used here MUST match the merge tool's normalizer in `xlator naming-defaults --build`: lowercase, strip leading articles (`a`, `an`, `the`), strip ASCII punctuation, collapse whitespace.

If neither file exists (first run on a domain), both maps are empty — Step 3 falls back to deriving fresh names from the static guide. This is normal and expected on the first `/index-inputs` run.

## Step 3: Generate per-section data and `naming_manifest:` block

For each section produce:

- **`heading:`** — verbatim heading text including the `#` / `##` / `###` prefix. The prefix encodes the level; do NOT strip it.
- **`summary:`** — one sentence describing what this section covers, in the policy's own terminology.
- **`tags:`** — 3–5 short noun-phrase tags (lowercase, hyphenated or single-word). These are downstream filtering signals.
- **`phase:`** — *optional* snake_case identifier naming the phase or stage of analysis the section belongs to. Populate ONLY when the source doc surfaces an explicit phase or stage signal — examples that justify a `phase:`:
  - A heading like `# Phase 1 — Initial Screening` (the heading itself is the signal).
  - A parent heading several levels above the current section (e.g., the section's H3 sits under an H1 `Phase 2: Detailed Eligibility` — the phase label is attributable to the ancestor).
  - A body sentence like *"the computations below run as Phase 2 of the eligibility test"* — explicit phase wording in prose, anchored to the section.

  Convert the surfaced label to a snake_case identifier (`Phase 1 — Initial Screening` → `initial_screening`). Omit the field entirely when no such signal exists in or above the section. **Inventing a `phase:` when the source has no signal degrades downstream defaults — an absent field is stronger than a hallucinated one.**
- **`phase_source:`** — required when `phase:` is present, omitted when `phase:` is omitted. Value is the **verbatim source-text phrase** that justified the `phase:` identifier — copied character-for-character from the source `.md`, no paraphrasing, no truncation that breaks substring matching. Downstream consumers run `grep -F "<phase_source>" <input/policy_docs/<rel>.md>` to verify the AI honored the explicit-signal rule. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `phase:` entirely rather than invent or paraphrase.
- **`computations:`** — *optional* list. Include only if the section contains identifiable rule logic (formulas, arithmetic, table lookups, thresholds, conditional assignments). Each entry has:
  - `description:` — one sentence describing the computation in plain language.
  - `variables:` — all variable names involved, **inputs first, computed output last**, snake_case, decided per the authority-chain rule below. Every name in this list MUST also appear as a key in the top-level `naming_manifest.variables` map (the emitter validates this invariant at write time).
  - `preconditions:` — *optional* boolean expression describing when the `expr_hint:` applies, derived from the section's own heading, its parent headings, and the surrounding text. The value is a list of **terms** joined by implicit AND at the top level. Each term is one of:
    - a string clause — a self-contained predicate in plain language; reference variable names from `variables:` where possible (e.g., `"household contains a working adult"`, `"var2 > 0"`).
    - `{all_of: [<term>, ...]}` — an explicit AND group; useful for nesting inside `any_of`.
    - `{any_of: [<term>, ...]}` — an OR group; terms inside may themselves be string clauses or further `all_of` / `any_of` groups, so arbitrary nesting is allowed.

    Example: a parent H2 "Working Adults" plus a phrase "if the applicant is over 65, or is married to an employed spouse" yields
    ```yaml
    preconditions:
      - "household contains a working adult"
      - any_of:
          - "applicant is over 65"
          - all_of:
              - "applicant is married"
              - "spouse is employed"
    ```
    Omit the field when the computation applies unconditionally within the section.
  - `expr_hint:` — *optional* short formula or expression fragment. Include when a formula or condition is stated or clearly implied; omit when the logic is descriptive only.

  **Omit the `computations:` field entirely** when no rule logic is present. Do not emit `computations: []` — an empty list is never correct.

### Variable name decision (per concept)

For each variable a section's `computations:` references:

1. Compute the variable's `policy_phrase:` per the verbatim rule in `core/naming_guide.md` — a verbatim noun phrase from the source body (or the most specific deterministic anchor when no noun phrase exists). Never paraphrase.
2. Normalize the phrase (lowercase, strip leading articles, strip ASCII punctuation, collapse whitespace).
3. **Lookup priority:**
   - If the normalized phrase matches an entry in the **specs** map (Step 2's highest-priority lookup), use that entry's name verbatim. Done.
   - Else if it matches an entry in the **defaults** map (Step 2's mid-priority lookup), use that entry's name. Done.
   - Else derive a fresh name from the static guide's style rules (snake_case, noun phrase, prefer policy term over acronym, strip entity-name words, disambiguate when needed).
4. Decide `role_hint:` per the trigger table in `core/naming_guide.md`. Omit when no clear signal exists.

### Top-level `naming_manifest:` block

In addition to per-section data, emit a top-level `naming_manifest.variables:` map covering every variable name referenced in any section's `computations[*].variables`. Each entry has:

- **`policy_phrase:`** — the verbatim policy phrase (or deterministic anchor) for the concept.
- **`role_hint:`** — *optional*, snake_case identifier (`input` | `computed` | `output`). Emit only when the source body carries an unambiguous syntactic signal (see the trigger table in `core/naming_guide.md`). Omit when no clear signal exists — absent is the safe default.
- **`source_doc:`** — *required*, the full `input/policy_docs/<rel>.md` path of the policy doc this variable was extracted from. **Worker invariant:** `source_doc:` MUST equal `input/policy_docs/<rel>.md` for the per-file file at `policy_facets/computations/<rel>.md.yaml` (i.e., it must equal `source_rel` in the JSON payload). The merge tool sanity-checks this; mismatches surface as warnings in the `xlator naming-defaults --build` JSON summary.
- **`section:`** — *optional*, the heading or §-citation where the concept first appears, matching the per-section context. Replaces the legacy `source_section:` field name. Omit when no clear section signal exists.
- **`description:`** — *optional*, one concise sentence (analyst-readable prose) explaining what the concept represents in the source's own framing. Emit only when the source contains a definitional sentence about the concept; never paraphrase to fit a template. Absent is the safe default.
- **`type:`** — *optional*, one of `money | bool | int | float | string | enum | list | date`. Emit only when the source body carries a clear signal (see "Type inference triggers" below). Never infer from the variable name alone — `gross_income` does not become `money` just because the name contains "income"; the source must say so. Absent is safer than wrong.
- **`values:`** — *required when* `type: enum`, *omitted otherwise*. List of allowed string values; populate from a bulleted enumeration or comma-separated list of allowed outcomes in the source. The emitter rejects payloads with `type: enum` and no `values:` (or `values:` without `type: enum`).

#### Type inference triggers

Emit `type:` only when the source surfaces one of these signals:

| Type     | Signal in source text                                                              |
|----------|------------------------------------------------------------------------------------|
| `money`  | currency markers (`$`, `USD`, "dollars"), "per month", "annual income", monetary thresholds |
| `bool`   | "yes/no", "true/false", "is/is not eligible", binary flags                          |
| `int`    | counts ("number of household members"), age in years, integer thresholds            |
| `float`  | percentages (`20%`, `0.20`), ratios, multipliers                                    |
| `string` | free-form identifier (case number, applicant name)                                  |
| `enum`   | bulleted/comma-separated list of allowed outcomes ("approve, deny, manual review")  |
| `list`   | "list of …", repeating-collection phrasing ("each member …")                        |
| `date`   | dates, "as of", "effective date", calendar references                               |

When the signal is ambiguous or absent, omit `type:`. A hallucinated type pollutes the authority chain — downstream consumers act on it.

Cross-block invariant: every name in `sections[*].computations[*].variables` MUST appear as a key in `naming_manifest.variables`. The emitter (`xlator emit-per-file-yaml`) validates this at write time and refuses to produce output otherwise. The emitter also enforces: required `source_doc:` (non-empty string per variable); rejection of legacy `source_section:` field; the `type` vocabulary; the `type: enum` ↔ `values:` dependency; and the non-empty-string rule for `description:` and `section:` when present.

## Step 4: Emit the per-file YAML via `xlator emit-per-file-yaml`

Compute the destination: `<DOMAINS_DIR>/<domain>/policy_facets/computations/<rel>.md.yaml` (where `<rel>.md` mirrors the source filename verbatim, with `.yaml` appended so the file's content type is unambiguous to editors and tooling).

**Do NOT hand-format YAML.** Build a JSON payload and pipe it to `xlator emit-per-file-yaml` via stdin. The tool validates the cross-block name-set invariant, omits absent optional fields cleanly, handles quoting hazards in `policy_phrase:` values, and writes the destination atomically (`tmp + os.replace`) with the standard preamble.

JSON payload shape:

```json
{
  "destination": "<absolute path to .md.yaml file>",
  "source_rel":  "input/policy_docs/<rel>.md",
  "naming_manifest": {
    "variables": {
      "<name>": {
        "policy_phrase":   "...",
        "role_hint":       "input | computed | output",
        "source_doc":      "input/policy_docs/<rel>.md",
        "section":         "...",
        "description":     "...",
        "type":            "money | bool | int | float | string | enum | list | date",
        "values":          ["..."]
      }
    }
  },
  "sections": [
    {
      "heading":      "# Section Title",
      "summary":      "...",
      "tags":         ["tag1", "tag2"],
      "phase":        "initial_screening",
      "phase_source": "Phase 1 — Initial Screening",
      "computations": [
        {
          "description":   "...",
          "variables":     ["var1", "var2", "output_var"],
          "preconditions": [...],
          "expr_hint":     "var1 * 0.20"
        }
      ]
    }
  ]
}
```

Invocation pattern:

```bash
echo "$JSON_PAYLOAD" | xlator emit-per-file-yaml
```

The tool emits the YAML map as:

```yaml
# Auto-generated by /extract-computations — do not edit manually
# Source: input/policy_docs/<rel>.md

naming_manifest:
  variables:
    gross_income:
      policy_phrase: "gross monthly income"
      role_hint: input
      source_doc: "input/policy_docs/<rel>.md"
      section: "§1.2"
      description: "Total monthly household income before any deductions."
      type: money
    eligibility:
      policy_phrase: "eligibility status"
      role_hint: output
      source_doc: "input/policy_docs/<rel>.md"
      section: "§3.1"
      type: enum
      values: [approve, deny, manual_verification]
    # ...

sections:
  - heading: "# Section Title"
    summary: "..."
    tags: [tag1, tag2, tag3]
    phase: initial_screening
    phase_source: "Phase 1 — Initial Screening"
    computations:
      - description: "..."
        variables: [gross_income, deductions, net_income]
        # ...
```

Conventions enforced by the emitter:
- Top-level value is a YAML map with exactly two keys: `naming_manifest` and `sections`. Consumers read `data["sections"]` for section blocks and `data["naming_manifest"]["variables"]` for the per-file naming map.
- Optional fields (`role_hint:`, `phase:`, `phase_source:`, `preconditions:`, `expr_hint:`, `computations:` when no rule logic, `description:`, `type:`, `values:`) are omitted entirely when absent from the JSON payload — never written as `null` or `[]`.
- **Cross-block name-set invariant:** every name in `sections[*].computations[*].variables` MUST appear as a key in `naming_manifest.variables`. If the JSON violates this, the tool exits non-zero and refuses to write — diagnose the missing entry before retrying.
- **`type:` vocabulary:** must be one of `money | bool | int | float | string | enum | list | date` when present. The emitter rejects any other value (including `str`).
- **`type: enum` ↔ `values:` dependency:** the emitter rejects payloads that have `type: enum` without a non-empty `values:` list, or `values:` with any other `type:`.
- **`description:` non-empty:** when present, must be a non-empty string. Pass `null` or omit the key to mean "absent".
- **List order in `sections[*].computations:` reflects source order.** Downstream consumers (notably `/create-ruleset-modules`'s `sequential_chain` heuristic) rely on this — within a section, the first computation in the list is the first in document order, the second is next, and so on. Build the JSON `computations:` array in source order.

Always rewrite the destination file in full; this skill is idempotent at the file level. Per-file caching is the manifest's job (handled by `xlator extract-computations --finalize`), not the skill's.

## Step 5: Print summary

:::important
✓ Wrote policy_facets/computations/<rel>.md.yaml (<K> section(s), <V> variable(s) in naming_manifest).
:::

Do NOT emit `:::next_step` from this skill — it is per-file and is normally invoked from a parent loop. The parent (e.g., `/index-inputs`) emits the workflow's next-step suggestion.

## Common Mistakes to Avoid

- **Don't include a `path:` field** — the destination filename encodes the source path; `path:` is redundant and was removed in this version.
- **Don't omit the heading prefix** — `heading: "# Title"` not `heading: "Title"`; the `#` characters encode the level.
- **Don't merge all sections from a file into one entry** — each H1/H2/H3 heading is its own entry.
- **Don't emit `computations: []`** for sections with no rule logic — omit the field entirely.
- **Don't hand-format the YAML output.** Build a JSON payload and pipe to `xlator emit-per-file-yaml`. The tool handles quoting, optional-field omission, and the cross-block invariant. Hand-formatting silently breaks the invariant or quotes `policy_phrase:` values incorrectly.
- **Don't reference a variable in `sections[*].computations[*].variables` that isn't a key in `naming_manifest.variables`.** The emitter rejects payloads that violate this invariant. Add the entry to `naming_manifest.variables` before emitting.
- **Don't paraphrase `policy_phrase:`.** Verbatim from the source body. If no noun phrase exists, fall back to a deterministic anchor (the section heading text). Paraphrase drift across re-runs silently breaks the no-copy-back guarantee on subsequent `/index-inputs` runs.
- **Don't invent `role_hint:`.** Emit it only when policy text gives a clear syntactic signal (formula syntax → `computed`; "applicant provides" → `input`; "determined to be" → `output`). Absent is the safe default — never invented.
- **Don't infer `type:` from the variable name alone.** `gross_income` is not automatically `money`; the source must contain a currency marker, monetary threshold, or other explicit signal. Naming heuristics produce silent type pollution that flows through the authority chain into downstream skills.
- **Don't use `type: str` or `type: text`.** The vocabulary is exactly `money | bool | int | float | string | enum | list | date`. The emitter rejects anything else.
- **Don't emit `values:` without `type: enum`.** And don't emit `type: enum` without `values:`. The two ship together or both are absent. The emitter rejects either alone.
- **Don't paraphrase `description:` to fit a template.** One sentence anchored to a definitional sentence in the source. If the source has no definitional framing, omit the field.
- **Don't update the manifest from this skill** — the manifest is the single responsibility of `xlator extract-computations --finalize`. When invoked standalone (outside `/index-inputs`), the per-file file is written but the manifest is not updated; the next `--plan` will simply re-extract this file (matching destination + missing manifest entry → `to_extract`). This is acceptable best-effort behavior for the standalone path.
- **Don't write `policy_facets/input-sections.yaml`** — that artifact is removed in v3.0.0. All section data lives in per-file `policy_facets/computations/<rel>.md.yaml` files.
- **Don't read or mutate any pre-existing `input-sections.yaml`** — leave it on disk untouched. Maintainers delete it manually.
- **Don't run this skill on a low-md_quality source** — the pre-flight gate refuses files whose `md_quality.score < 40`. If the gate fires, fix the source or remove it from `input/policy_docs/`.
- **Don't invent a `phase:` value when the source has no explicit signal** — phases must be anchored to a heading, body sentence, or attributable ancestor heading. An absent `phase:` is the safe default; hallucinated phases flow through `/create-ruleset-groups` into `guidance/ruleset-groups.yaml` and ultimately produce `validate_civil.py` rejection at the `/extract-ruleset` stage.
- **Don't paraphrase `phase_source:`** — it must be a verbatim substring of the source `.md` so `grep -F "<phase_source>" <source>` matches. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `phase:` entirely rather than invent or paraphrase.
- **Don't emit `phase:` without `phase_source:`** (or vice versa) — the two fields ship together. The quote is the proof that the AI honored the explicit-signal rule.
