---
name: extract-computations
description: Extract Per-File Section/Computation Data Into policy_facets/computations/
---

# Extract Per-File Section/Computation Data

Read one policy doc under `<domain>/input/policy_docs/`, load the naming manifest, parse its H1–H3 sections, and write a YAML map `{sections}` to the mirrored destination at `<domain>/policy_facets/computations/<rel>.md.yaml`.

This skill is invoked per file by `/index-inputs` (in a loop over REINDEX entries) and may also be invoked standalone by the analyst against a single source file. The non-AI half (file enumeration, manifest sync, mirror-deletes, plan/finalize handoff) lives in `xlator extract-computations <domain> --plan` and `--finalize`; this skill is the AI half that does the per-file content generation.

## Input

```
/extract-computations <path_to_policy_file>
```

`<path_to_policy_file>` must resolve to a path under `<DOMAINS_DIR>/<domain>/input/policy_docs/`. If not, pre-flight emits `:::error` and stops.

Read `../../core/output-fencing.md` now.

Read `../../core/examples/computations/source.md` and `../../core/examples/computations/canonical.md.yaml` now — both the compressed-source input and the extracted-computations output, so the input→output transformation is visible.

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
- [ ] Step 2: Load the naming manifest
- [ ] Step 3: Generate per-section data
- [ ] Step 4: Emit `policy_facets/computations/<rel>.md.yaml` via `xlator emit-per-file-yaml`
- [ ] Step 5: Print summary

## Step 1: Read source and parse sections

Read the full file content at `<DOMAINS_DIR>/<domain>/input/policy_docs/<rel>.md`.

Extract all H1 (`#`), H2 (`##`), and H3 (`###`) headings in document order. For each heading, collect the section body — the text between this heading and the next heading of equal or higher level.

If the file has **no H1–H3 headings**, treat the whole file as a single section. The heading for that single section is the filename stem (without `.md`) prefixed with `#`.

## Step 2: Load the naming manifest

Read `../../core/naming_guide.md` now — the static plugin-wide style guide consulted on every run.

Then resolve `<domain_dir>` from the source path: walk ancestors of `<path_to_policy_file>` to find `input/policy_docs/`; the directory three levels up is `<domain>`; its parent is `$DOMAINS_DIR`. (The pre-flight already does this lookup; reuse the resolved `<domain_dir>`.)

If `<domain_dir>/specs/naming-manifest.yaml` exists, build a `{normalize(policy_phrase) → {name, source_doc, section}}` lookup map by walking `inputs.*.*`, `computed.*`, and `outputs.*`:

- Each entry is keyed by `normalize(policy_phrase)` with value `{name: <leaf_key>, source_doc, section}`.
- On collision (e.g., `inputs.Household.gross_income` and `inputs.Applicant.gross_income` both with phrase "gross monthly income"), prefer the entry whose `source_doc:` matches the file currently being processed; deterministic tiebreak: alphabetical by entity name.
- Entries with no `policy_phrase` (seeded but not yet confirmed) are skipped — they have no key to match observations against.
- Malformed file → log a warning to stderr and treat as empty map. Never block extraction.

The normalizer used here: lowercase, strip leading articles (`a`, `an`, `the`), strip ASCII punctuation, collapse whitespace.

If the manifest does not exist (first run on a domain), the lookup map is empty — Step 3 derives fresh names from the static guide. This is normal and expected on the first `/index-inputs` run.

## Step 3: Generate per-section data

**Section filter:** Only sections that contain identifiable rule logic (i.e., would carry a non-empty `computations:` field) are emitted. Sections with no rule logic — narrative, definitions-only prose, intro/overview text, table of contents, etc. — are dropped from the output entirely. Parse all H1–H3 sections internally so you can attribute `stage:` ancestors and surface variables, but the output `sections:` list contains only the surviving sections.

For each surviving section produce:

- **`heading:`** — verbatim heading text including the `#` / `##` / `###` prefix. The prefix encodes the level; do NOT strip it.
- **`summary:`** — one sentence describing what this section covers, in the policy's own terminology.
- **`tags:`** — 3–5 short noun-phrase tags (lowercase, hyphenated or single-word). These are downstream filtering signals.
- **`stage:`** — *optional* snake_case identifier naming the stage of analysis the section belongs to. Populate ONLY when the source doc surfaces an explicit phase or stage signal — examples that justify a `stage:`:
  - A heading like `# Phase 1 — Initial Screening` (the heading itself is the signal).
  - A parent heading several levels above the current section (e.g., the section's H3 sits under an H1 `Phase 2: Detailed Eligibility` — the stage label is attributable to the ancestor).
  - A body sentence like *"the computations below run as Phase 2 of the eligibility test"* — explicit stage wording in prose, anchored to the section.

  Convert the surfaced label to a snake_case identifier (`Phase 1 — Initial Screening` → `initial_screening`). Omit the field entirely when no such signal exists in or above the section. **Inventing a `stage:` when the source has no signal degrades downstream defaults — an absent field is stronger than a hallucinated one.**
- **`stage_source:`** — required when `stage:` is present, omitted when `stage:` is omitted. Value is the **verbatim source-text phrase** that justified the `stage:` identifier — copied character-for-character from the source `.md`, no paraphrasing, no truncation that breaks substring matching. Downstream consumers run `grep -F "<stage_source>" <input/policy_docs/<rel>.md>` to verify the AI honored the explicit-signal rule. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `stage:` entirely rather than invent or paraphrase.
- **`computations:`** — required, non-empty list. Every emitted section has at least one entry; sections that would carry an empty list are filtered out per the section-filter rule above. Each entry has:
  - `description:` — one sentence describing the computation in plain language.
  - `preconditions:` — *optional* boolean expression describing when the `expr_hint:` applies, derived from the section's own heading, its parent headings, and the surrounding text. The value is a list of **terms** joined by implicit AND at the top level. Each term is one of:
    - a string clause — a self-contained predicate in plain language (e.g., `"household contains a working adult"`, `"gross_income > 0"`).
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
  - `expr_hint:` — *optional* assignment of the form `output_name = <expression>` capturing the formula stated or clearly implied by the source. Include when the section states a formula or condition; omit when the logic is descriptive only.

    **Shape:** single `=` separator. The LHS is a snake_case identifier naming the computed output. The RHS is the prior bare-expression form — short formula or expression fragment referencing input variables by name. The emitter rejects bare-expression `expr_hint:` payloads (no `=`); both sides must be present and non-empty when the field is present.

    Examples:
    - `gross_income = earned_income + unearned_income`
    - `eligibility = gross_income < poverty_line`
    - `monthly_payment = base_rate * months`

    For descriptive-only computations (e.g., "households receiving SSI are categorically eligible"), omit `expr_hint:` entirely. Downstream consumers fall back to scanning `description:` prose for variable names.

  **Drop the section entirely** when no rule logic is present — do not emit it in `sections:`. Never emit `computations: []` and never emit a section without a `computations:` field.

### Variable name decision (per concept)

For each variable a section's `computations:` references (whether on the LHS or RHS of `expr_hint:`, or named in `description:` for descriptive-only computations):

1. Compute the variable's `policy_phrase:` per the verbatim rule in `core/naming_guide.md` — a verbatim noun phrase from the source body (or the most specific deterministic anchor when no noun phrase exists). Never paraphrase.
2. Normalize the phrase (lowercase, strip leading articles, strip ASCII punctuation, collapse whitespace).
3. If the normalized phrase matches an entry in the manifest lookup map (Step 2), use that entry's name verbatim. Otherwise, derive a fresh name from the static guide's style rules (snake_case, noun phrase, prefer policy term over acronym, strip entity-name words, disambiguate when needed).

Use the resolved snake_case name on both sides of `expr_hint:` (LHS for the computed output, RHS for inputs) and in any `description:` mentions.

## Step 4: Emit the per-file YAML via `xlator emit-per-file-yaml`

Compute the destination: `<DOMAINS_DIR>/<domain>/policy_facets/computations/<rel>.md.yaml` (where `<rel>.md` mirrors the source filename verbatim, with `.yaml` appended so the file's content type is unambiguous to editors and tooling).

**Do NOT hand-format YAML.** Build a JSON payload and pipe it to `xlator emit-per-file-yaml` via stdin. The tool validates per-computation `expr_hint:` shape, omits absent optional fields cleanly, and writes the destination atomically (`tmp + os.replace`) with the standard preamble.

JSON payload shape:

```json
{
  "destination": "<absolute path to .md.yaml file>",
  "source_rel":  "input/policy_docs/<rel>.md",
  "sections": [
    {
      "heading":      "# Section Title",
      "summary":      "...",
      "tags":         ["tag1", "tag2"],
      "stage":        "initial_screening",
      "stage_source": "Phase 1 — Initial Screening",
      "computations": [
        {
          "description":   "...",
          "preconditions": [...],
          "expr_hint":     "output_var = var1 * 0.20"
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

sections:
  - heading: "# Section Title"
    summary: "..."
    tags: [tag1, tag2, tag3]
    stage: initial_screening
    stage_source: "Phase 1 — Initial Screening"
    computations:
      - description: "..."
        expr_hint: "net_income = gross_income - deductions"
        # ...
```

Conventions enforced by the emitter:
- Top-level value is a YAML map with exactly one key: `sections`. Consumers read `data["sections"]` for section blocks.
- Optional fields (`stage:`, `stage_source:`, `preconditions:`, `expr_hint:`) are omitted entirely when absent from the JSON payload — never written as `null` or `[]`.
- `computations:` is required on every emitted section. Sections lacking rule logic are filtered out upstream (see Step 3) — they never appear in the JSON payload at all.
- **`expr_hint:` shape:** when present, must match `<snake_case_identifier> = <non-empty expression>`. The emitter rejects payloads with no `=`, empty LHS, empty RHS, or non-snake_case LHS.
- **List order in `sections[*].computations:` reflects source order.** Downstream consumers (notably `/create-ruleset-modules`'s `sequential_chain` heuristic) rely on this — within a section, the first computation in the list is the first in document order, the second is next, and so on. Build the JSON `computations:` array in source order.

Always rewrite the destination file in full; this skill is idempotent at the file level. Per-file caching is the manifest's job (handled by `xlator extract-computations --finalize`), not the skill's.

## Step 5: Print summary

:::important
✓ Wrote policy_facets/computations/<rel>.md.yaml (<K> section(s), <C> computation(s)).
:::

Do NOT emit `:::next_step` from this skill — it is per-file and is normally invoked from a parent loop. The parent (e.g., `/index-inputs`) emits the workflow's next-step suggestion.

## Common Mistakes to Avoid

- **Don't include a `path:` field** — the destination filename encodes the source path; `path:` is redundant and was removed.
- **Don't omit the heading prefix** — `heading: "# Title"` not `heading: "Title"`; the `#` characters encode the level.
- **Don't merge all sections from a file into one entry** — each H1/H2/H3 heading is its own entry.
- **Don't emit a section with no rule logic.** Sections without a `computations:` block are dropped from the output — narrative, definitions-only prose, intros, and TOC sections are excluded from `sections:`. Never emit `computations: []`, and never emit a section without a `computations:` field.
- **Don't hand-format the YAML output.** Build a JSON payload and pipe to `xlator emit-per-file-yaml`. The tool handles quoting, optional-field omission, and the `expr_hint:` shape check.
- **Don't emit `expr_hint:` as a bare expression** — it must be `output_name = <expression>`. The emitter rejects payloads with no `=`. For descriptive-only computations, omit `expr_hint:` entirely; downstream consumers fall back to `description:` prose.
- **Don't paraphrase `policy_phrase:`** when consulting the specs lookup. Verbatim from the source body. If no noun phrase exists, fall back to a deterministic anchor (the section heading text). Paraphrase drift across re-runs silently breaks alignment with confirmed specs entries.
- **Don't update the manifest from this skill** — the manifest is the single responsibility of `xlator extract-computations --finalize`. When invoked standalone (outside `/index-inputs`), the per-file file is written but the manifest is not updated; the next `--plan` will simply re-extract this file (matching destination + missing manifest entry → `to_extract`). This is acceptable best-effort behavior for the standalone path.
- **Don't run this skill on a low-md_quality source** — the pre-flight gate refuses files whose `md_quality.score < 40`. If the gate fires, fix the source or remove it from `input/policy_docs/`.
- **Don't invent a `stage:` value when the source has no explicit signal** — stages must be anchored to a heading, body sentence, or attributable ancestor heading. An absent `stage:` is the safe default; hallucinated stages flow through `/create-ruleset-groups` into `guidance/ruleset-groups.yaml` and ultimately produce `validate_civil.py` rejection at the `/extract-ruleset` stage.
- **Don't paraphrase `stage_source:`** — it must be a verbatim substring of the source `.md` so `grep -F "<stage_source>" <source>` matches. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `stage:` entirely rather than invent or paraphrase.
- **Don't emit `stage:` without `stage_source:`** (or vice versa) — the two fields ship together. The quote is the proof that the AI honored the explicit-signal rule.
